"""resolve(): verify the planner's suggestions against Open Targets / ChEMBL and build the Subject.

The SearchPlan is an unverified suggestion. This module keeps only what the databases confirm,
adds what the databases know but the plan missed, and explains every change in
Subject.resolution_notes. It never raises: API failures are recorded as SourceStatus entries
and the subject is built from whatever could be verified.

Target consistency: every drug's mechanism targets end up either in the subject (when they are in
the same gene family as a subject target) or in Drug.other_target_symbols (multi-specific drugs).
Same family = same approved-symbol stem (IL17A / IL17F / IL17RA -> "IL17") or the same Open Targets
targetClass at level l5 (e.g. JAK1 / TYK2 -> "Tyrosine protein kinase JakA family"). Coarser
targetClass levels are not used: IL17A and TNF are both just l1 "Secreted protein".

Modality: every drug gets drug_type (Open Targets drugType, else ChEMBL molecule_type) and a relation to the
subject mechanism: "subject_mechanism" when its modality is compatible with plan.modality (or plan.modality is
"unspecified"), else "same_target_other_modality". See drug_modality() / MODALITY_COMPATIBLE and CLAUDE.md.

Open Targets GraphQL fields used here were checked by introspection (platform release 2026):
mapIds, search, disease.synonyms, targets.{approvedSymbol,targetClass{label level}},
targets.drugAndClinicalCandidates, drugs.{drugType,synonyms,tradeNames,maximumClinicalStage,mechanismsOfAction}.
ChEMBL molecule fields: molecule_chembl_id, pref_name, max_phase, molecule_type, molecule_synonyms.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from researcher.schema import Drug, DrugRelation, Modality, ResearchInput, SearchPlan, SourceStatus, Subject
from researcher.http import CachedResponse, HttpClient
from researcher.planner import strip_action_words

OT_URL = "https://api.platform.opentargets.org/api/v4/graphql"
CHEMBL_URL = "https://www.ebi.ac.uk/chembl/api/data"

OT_STATUS = "resolve:opentargets"
CHEMBL_STATUS = "resolve:chembl"

_MAX_DISEASE_TERMS = 6
# A disease hit whose aggregated score is at least this fraction of the best one is "close".
_CLOSE_DISEASE_RATIO = 0.5
_CHEMBL_BATCH = 50
# Rounds of "add same-family co-targets, then fetch drugs for the new targets".
_MAX_DRUG_ROUNDS = 3
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+(?:[-.][A-Z0-9]+)*$")
_FAMILY_CLASS_LEVEL = "l5"

# ChEMBL action types grouped by direction. A database drug is dropped only when its action on
# the subject's targets is clearly opposite to the planned action (e.g. an agonist for "inhibition").
_INHIBITING = {
    "INHIBITOR", "ANTAGONIST", "BLOCKER", "INVERSE AGONIST", "NEGATIVE ALLOSTERIC MODULATOR",
    "NEGATIVE MODULATOR", "ALLOSTERIC ANTAGONIST", "ANTISENSE INHIBITOR", "RNAI INHIBITOR",
    "DEGRADER", "DISRUPTING AGENT",
}
_ACTIVATING = {
    "AGONIST", "PARTIAL AGONIST", "ACTIVATOR", "OPENER", "POSITIVE ALLOSTERIC MODULATOR",
    "POSITIVE MODULATOR", "STABILISER",
}
_OPPOSITE = {"inhibition": _ACTIVATING, "degradation": _ACTIVATING, "activation": _INHIBITING}

# Open Targets clinical stage -> ChEMBL-style max_phase.
_STAGE_PHASE = {"APPROVAL": 4.0, "EARLY_PHASE_1": 0.5}

# ChEMBL synonym types that are nonproprietary names, best first.
_INN_TYPES = ("INN", "USAN", "BAN", "JAN")

# Drug type (Open Targets drugType / ChEMBL molecule_type, lowercased) -> modality. "protein" (fusion proteins,
# non-antibody binders) and "unknown" are drug-side only; they are not SearchPlan modalities.
_TYPE_MODALITY = {
    "small molecule": "small_molecule", "antibody": "antibody", "antibody drug conjugate": "adc",
    "cell": "cell_therapy", "gene": "gene_therapy", "oligonucleotide": "oligonucleotide",
    "protein": "protein", "enzyme": "protein", "oligosaccharide": "other",
}
# WHO INN stems, used when the database type is Unknown, and to tell gene-modified cell products (ChEMBL /
# Open Targets type them "Gene") from gene therapies.
_INN_STEMS = [("mab", "antibody"), ("cel", "cell_therapy"), ("gene", "gene_therapy"), ("vec", "gene_therapy"),
              ("rsen", "oligonucleotide"), ("siran", "oligonucleotide")]
# Plan modality -> drug modalities that count as the subject mechanism. A bispecific or a non-antibody
# protein binder is still an antibody-like biologic; an ADC or a cell therapy is not.
MODALITY_COMPATIBLE: dict[str, set[str]] = {
    "small_molecule": {"small_molecule"},
    "antibody": {"antibody", "bispecific", "protein"},
    "bispecific": {"bispecific"},
    "adc": {"adc"},
    "cell_therapy": {"cell_therapy"},
    "gene_therapy": {"gene_therapy"},
    "oligonucleotide": {"oligonucleotide"},
    "other": {"other", "protein"},
}

_Q_MAP_IDS = """
query MapIds($terms: [String!]!, $entities: [String!]!) {
  mapIds(queryTerms: $terms, entityNames: $entities) {
    mappings { term hits { id name score } }
  }
}"""

_Q_SEARCH_DISEASE = """
query SearchDisease($q: String!) {
  search(queryString: $q, entityNames: ["disease"], page: {index: 0, size: 5}) {
    hits { id name score }
  }
}"""

_Q_DISEASE = """
query Disease($id: String!) {
  disease(efoId: $id) { id name synonyms { relation terms } }
}"""

_Q_TARGET_FAMILY = """
query TargetFamily($ids: [String!]!) {
  targets(ensemblIds: $ids) { id approvedSymbol targetClass { label level } }
}"""

_DRUG_FIELDS = """
  id name drugType maximumClinicalStage
  synonyms { label } tradeNames { label }
  mechanismsOfAction { rows { actionType targets { id approvedSymbol } } }
"""

_Q_TARGET_DRUGS = (
    "query TargetDrugs($ids: [String!]!) { targets(ensemblIds: $ids) { id approvedSymbol "
    "drugAndClinicalCandidates { rows { drug {" + _DRUG_FIELDS + "} } } } }"
)

_Q_DRUGS = "query Drugs($ids: [String!]!) { drugs(chemblIds: $ids) {" + _DRUG_FIELDS + "} }"


class ApiError(Exception):
    pass


@dataclass
class _Log:
    source: str
    records: int = 0
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)
    used: bool = False
    network: bool = False  # at least one response did not come from the cache

    def status(self) -> SourceStatus:
        return SourceStatus(
            source=self.source,
            ok=not self.errors,
            records=self.records,
            error="; ".join(self.errors) or None,
            duration_s=round(self.duration_s, 4),
            cached=self.used and not self.network,
        )


@dataclass
class _DrugInfo:
    chembl_id: str
    name: str
    synonyms: list[str] = field(default_factory=list)
    max_phase: float | None = None
    # Ensembl target id -> ChEMBL action types of this drug on it.
    actions: dict[str, set[str]] = field(default_factory=dict)
    symbols: dict[str, str] = field(default_factory=dict)
    other_symbols: list[str] = field(default_factory=list)
    drug_type: str | None = None


def resolve(
    inp: ResearchInput,
    plan: SearchPlan,
    http: HttpClient | None = None,
    statuses: list[SourceStatus] | None = None,
) -> Subject:
    """Verify `plan` against Open Targets / ChEMBL. Appends one SourceStatus per source to `statuses`."""
    own_http = http is None
    http = http or HttpClient()
    try:
        r = _Resolver(inp, plan, http)
        subject = r.run()
    finally:
        if own_http:
            http.close()
    if statuses is not None:
        statuses.extend(log.status() for log in (r.ot, r.chembl) if log.used)
    return subject


class _Resolver:
    def __init__(self, inp: ResearchInput, plan: SearchPlan, http: HttpClient) -> None:
        self.inp, self.plan, self.http = inp, plan, http
        self.ot = _Log(OT_STATUS)
        self.chembl = _Log(CHEMBL_STATUS)
        self.chembl_down = False
        self.notes: list[str] = []
        self.targets: dict[str, str] = {}  # Ensembl id -> approved symbol, in insertion order
        self.families: dict[str, set[str]] = {}  # Ensembl id -> family keys (symbol stem, l5 class)
        self.disease_ids: list[str] = []
        self.disease_synonyms: list[str] = []
        self.drugs: dict[str, _DrugInfo] = {}
        self.origin: dict[str, str] = {}

    def run(self) -> Subject:
        self._step("targets", self._resolve_targets)
        self._step("disease", self._resolve_disease)
        self._step("drugs", self._resolve_drugs)
        if not self.targets:
            self.notes.append(
                f"no target could be resolved for mechanism {self.inp.mechanism!r}; "
                "targets and target-derived drugs are empty"
            )
        drugs = [self._to_drug(d) for d in self.drugs.values()]
        other = [f"{d.name} ({d.drug_type or 'unknown type'})" for d in drugs if d.relation != "subject_mechanism"]
        if other:
            self.notes.append(
                f"same target, other modality than {self.plan.modality} (relation same_target_other_modality): "
                + ", ".join(other)
            )
        return Subject(
            indication=self.inp.indication,
            mechanism=self.inp.mechanism,
            disease_ids=self.disease_ids,
            disease_synonyms=self.disease_synonyms,
            target_ids=list(self.targets),
            target_symbols=list(self.targets.values()),
            drugs=drugs,
            resolution_notes=self.notes,
        )

    def _step(self, name: str, fn: Any) -> None:
        try:
            fn()
        except Exception as e:  # never raise into the pipeline
            self.notes.append(f"{name}: failed ({type(e).__name__}: {e})")

    # --- 1. targets ---------------------------------------------------------

    def _resolve_targets(self) -> None:
        symbols = _dedupe([s.upper() for s in self.plan.target_symbols])
        if symbols:
            found = self._map_targets(symbols)
            for sym in symbols:
                hit = found.get(sym)
                if hit is None:
                    self.notes.append(f"dropped target {sym!r}: not found in Open Targets")
                    continue
                ensembl_id, approved = hit
                if approved != sym:
                    self.notes.append(f"target {sym!r} resolved to approved symbol {approved} ({ensembl_id})")
                self.targets.setdefault(ensembl_id, approved)
        if self.targets:
            return

        # The plan had nothing usable: try the mechanism text itself as a gene symbol ("anti-CD19 CAR-T").
        term, _ = strip_action_words(self.inp.mechanism)
        candidate = re.sub(r"[\s\-]", "", term).upper()
        if candidate and _SYMBOL_RE.match(candidate) and len(candidate) <= 15:
            hit = self._map_targets([candidate]).get(candidate)
            if hit:
                self.targets[hit[0]] = hit[1]
                self.notes.append(
                    f"added target {hit[1]} ({hit[0]}): mapped from mechanism text {term!r} "
                    "because the plan had no verifiable target"
                )

    def _map_targets(self, symbols: list[str]) -> dict[str, tuple[str, str]]:
        """symbol -> (Ensembl id, approved symbol). Exact symbol match wins; else a single unambiguous hit."""
        data = self._ot_query(_Q_MAP_IDS, {"terms": symbols, "entities": ["target"]})
        out: dict[str, tuple[str, str]] = {}
        for m in data["mapIds"]["mappings"]:
            hits = m.get("hits") or []
            exact = [h for h in hits if (h.get("name") or "").upper() == m["term"].upper()]
            pick = exact[0] if exact else hits[0] if len(hits) == 1 else None
            if pick:
                out[m["term"].upper()] = (pick["id"], pick["name"])
        self.ot.records += len(out)
        return out

    # --- 2. disease ---------------------------------------------------------

    def _resolve_disease(self) -> None:
        terms = _dedupe([self.inp.indication, *self.plan.disease_terms])[:_MAX_DISEASE_TERMS]
        score: dict[str, float] = {}
        names: dict[str, str] = {}
        for term in terms:
            hits = self._ot_query(_Q_SEARCH_DISEASE, {"q": term})["search"]["hits"]
            if not hits:
                continue
            top = hits[0]["score"] or 1.0
            for h in hits:
                # Scores are only comparable within one query: normalise to that query's top hit.
                score[h["id"]] = score.get(h["id"], 0.0) + (h["score"] or 0.0) / top
                names[h["id"]] = h["name"]
        for i, name in names.items():
            # A disease named exactly like the user's indication beats broader/narrower neighbours.
            if name.lower() == self.inp.indication.lower():
                score[i] += 1.0
        if not score:
            self.notes.append(f"no Open Targets disease matched {terms}")
            return

        ranked = sorted(score.items(), key=lambda kv: -kv[1])
        best_id, best_score = ranked[0]
        close = [i for i, s in ranked[1:] if s >= _CLOSE_DISEASE_RATIO * best_score]
        if close:
            alts = ", ".join(f"{names[i]} ({i})" for i in close)
            self.notes.append(f"disease: chose {names[best_id]} ({best_id}); close alternatives: {alts}")

        disease = self._ot_query(_Q_DISEASE, {"id": best_id})["disease"]
        exact = [t for s in disease.get("synonyms") or [] if s["relation"] == "hasExactSynonym" for t in s["terms"]]
        self.disease_ids = [disease["id"]]
        self.disease_synonyms = _dedupe([disease["name"], *exact])
        self.ot.records += 1

    # --- 3. drugs + 4. target consistency -----------------------------------

    def _resolve_drugs(self) -> None:
        plan_drugs: list[_DrugInfo] = []
        self._step("planned drugs", lambda: plan_drugs.extend(self._verify_plan_drugs()))
        self._reconcile(plan_drugs, planned=True)

        queried: set[str] = set()
        for _ in range(_MAX_DRUG_ROUNDS):
            todo = [t for t in self.targets if t not in queried]
            if not todo:
                break
            queried.update(todo)
            self._reconcile(self._add_database_drugs(todo), planned=False)

    def _reconcile(self, drugs: list[_DrugInfo], planned: bool) -> None:
        """Split each drug's targets into subject targets (same family) and other_symbols; keep or drop."""
        if not drugs:
            return
        if planned and not self.targets:
            for d in drugs:
                for tid in d.actions:
                    if tid not in self.targets:
                        self.targets[tid] = d.symbols.get(tid, tid)
                        self.notes.append(
                            f"added target {self.targets[tid]} ({tid}): mechanism target of planned drug "
                            f"{_norm(d.name)}; the plan had no verifiable target"
                        )
                self._keep(d, "plan")
            return

        self._load_families([*self.targets, *(t for d in drugs for t in d.actions)])
        for d in drugs:
            name = _norm(d.name)
            if not d.actions:
                if planned:
                    self.notes.append(f"planned drug {name}: no mechanism of action in ChEMBL / Open Targets; kept")
                self._keep(d, "plan" if planned else "database")
                continue
            on_subject = any(t in self.targets for t in d.actions)
            added, other = [], []
            for t in d.actions:
                if t in self.targets:
                    continue
                relative = self._family_member(t)
                (added if relative else other).append((t, relative))
            if planned and not on_subject and not added:
                syms = ", ".join(d.symbols.get(t, t) for t, _ in other)
                self.notes.append(f"dropped planned drug {name}: acts on {syms}, outside the subject mechanism")
                continue
            for t, relative in added:
                self.targets[t] = d.symbols.get(t, t)
                self.notes.append(
                    f"added target {self.targets[t]} ({t}): target of {name}, "
                    f"same gene family as {self.targets[relative]} ({self._shared_family(t, relative)})"
                )
            if other:
                d.other_symbols = _dedupe([*d.other_symbols, *(d.symbols.get(t, t) for t, _ in other)])
                self.notes.append(
                    f"{name} also targets {', '.join(d.other_symbols)} (multi-specific; outside the subject mechanism)"
                )
            self._keep(d, "plan" if planned else "database")

    def _keep(self, d: _DrugInfo, origin: str) -> None:
        self.drugs[d.chembl_id] = d
        self.origin[d.chembl_id] = origin

    def _load_families(self, target_ids: list[str]) -> None:
        todo = _dedupe([t for t in target_ids if t not in self.families])
        if not todo:
            return
        for t in self._ot_query(_Q_TARGET_FAMILY, {"ids": todo})["targets"] or []:
            keys = {f"symbol stem {_symbol_stem(t['approvedSymbol'])}"}
            keys |= {
                f"Open Targets class '{c['label']}'"
                for c in t.get("targetClass") or []
                if c.get("level") == _FAMILY_CLASS_LEVEL and c.get("label")
            }
            self.families[t["id"]] = keys

    def _family_member(self, target_id: str) -> str | None:
        """A subject target in the same gene family as `target_id`, if any."""
        mine = self.families.get(target_id, set())
        return next((s for s in self.targets if mine & self.families.get(s, set())), None)

    def _shared_family(self, a: str, b: str) -> str:
        return ", ".join(sorted(self.families.get(a, set()) & self.families.get(b, set())))

    # --- 3a. planned drugs --------------------------------------------------

    def _verify_plan_drugs(self) -> list[_DrugInfo]:
        names = _dedupe(self.plan.drug_names)
        if not names:
            return []
        ids: dict[str, str] = {}  # chembl id -> first plan name that matched it
        unresolved = list(names)
        if not self.chembl_down:
            try:
                unresolved = []
                for name in names:
                    cid = self._chembl_find(name)
                    if cid:
                        ids.setdefault(cid, name)
                    else:
                        unresolved.append(name)
            except ApiError:
                matched = {_norm(n) for n in ids.values()}
                unresolved = [n for n in names if _norm(n) not in matched]
        if self.chembl_down and unresolved:
            # ChEMBL failed: verify the remaining names against Open Targets' drug index instead.
            data = self._ot_query(_Q_MAP_IDS, {"terms": unresolved, "entities": ["drug"]})
            for m in data["mapIds"]["mappings"]:
                if m.get("hits"):
                    ids.setdefault(m["hits"][0]["id"], m["term"])
                    unresolved = [n for n in unresolved if _norm(n) != _norm(m["term"])]
        for name in unresolved:
            self.notes.append(f"dropped planned drug {_norm(name)!r}: not found in ChEMBL / Open Targets")

        infos = self._drug_infos(list(ids))
        return [infos[c] for c in ids if c in infos]

    def _chembl_find(self, name: str) -> str | None:
        """Parent ChEMBL id for a drug name (INN, trade name or research code); ChEMBL matches case-insensitively."""
        only = "molecule_chembl_id,max_phase,molecule_hierarchy"
        for key in ("molecule_synonyms__molecule_synonym__iexact", "pref_name__iexact"):
            mols = self._chembl_get("molecule", {key: name, "only": only, "limit": 20})["molecules"]
            if mols:
                mols.sort(key=lambda m: -float(m.get("max_phase") or -1))
                hier = mols[0].get("molecule_hierarchy") or {}
                return hier.get("parent_chembl_id") or mols[0]["molecule_chembl_id"]
        return None

    # --- 3b. database drugs -------------------------------------------------

    def _add_database_drugs(self, target_ids: list[str]) -> list[_DrugInfo]:
        """Drugs Open Targets links to `target_ids`. Returns the newly added ones (to reconcile)."""
        data = self._ot_query(_Q_TARGET_DRUGS, {"ids": target_ids})
        opposite = _OPPOSITE.get(self.plan.action or "", set())
        found: dict[str, dict[str, Any]] = {}
        skipped: list[str] = []
        for t in data["targets"] or []:
            for row in (t.get("drugAndClinicalCandidates") or {}).get("rows") or []:
                drug = row.get("drug")
                if not drug or drug["id"] in found:
                    continue
                actions = {
                    a for m in (drug.get("mechanismsOfAction") or {}).get("rows") or []
                    if any(x["id"] in self.targets for x in m.get("targets") or [])
                    for a in [m.get("actionType")] if a
                }
                if actions and actions <= opposite:
                    skipped.append(f"{_norm(drug['name'])} ({'/'.join(sorted(actions)).lower()})")
                    continue
                found[drug["id"]] = drug
        if skipped:
            self.notes.append(
                f"skipped database drugs with the opposite action to {self.plan.action!r}: {', '.join(skipped)}"
            )
        self.ot.records += len(found)

        for cid in found:
            if self.origin.get(cid) == "plan":
                self.origin[cid] = "both"
        new_ids = [c for c in found if c not in self.drugs]
        infos = self._drug_infos(new_ids, ot_rows=found)
        return [infos[c] for c in new_ids if c in infos]

    # --- drug details: ChEMBL first, Open Targets as fallback ---------------

    def _drug_infos(self, ids: list[str], ot_rows: dict[str, dict[str, Any]] | None = None) -> dict[str, _DrugInfo]:
        if not ids:
            return {}
        ot_rows = dict(ot_rows or {})
        missing = [c for c in ids if c not in ot_rows]
        if missing:
            for d in self._ot_query(_Q_DRUGS, {"ids": missing})["drugs"] or []:
                if d:
                    ot_rows[d["id"]] = d
        infos = {c: _from_ot(ot_rows[c]) for c in ids if c in ot_rows}
        if not self.chembl_down:
            try:
                self._enrich_from_chembl(ids, infos)
            except ApiError:
                pass  # already noted; keep Open Targets data
        return infos

    def _enrich_from_chembl(self, ids: list[str], infos: dict[str, _DrugInfo]) -> None:
        mols = self._chembl_batch(
            "molecule", "molecule_chembl_id", ids,
            "molecule_chembl_id,pref_name,max_phase,molecule_type,molecule_synonyms",
        )
        for m in mols:
            cid = m["molecule_chembl_id"]
            info = infos.setdefault(cid, _DrugInfo(cid, m.get("pref_name") or cid))
            if m.get("max_phase") is not None:
                info.max_phase = float(m["max_phase"])
            if _known_type(m.get("molecule_type")) and not _known_type(info.drug_type):
                info.drug_type = m["molecule_type"]
            rows = m.get("molecule_synonyms") or []
            info.name = m.get("pref_name") or info.name
            info.synonyms = _chembl_synonyms(rows) or info.synonyms
            inn = next(
                (r["molecule_synonym"] for t in _INN_TYPES for r in rows
                 if r.get("syn_type") == t and r.get("molecule_synonym")),
                None,
            )
            _prefer_inn(info, inn)
        self.chembl.records += len(mols)

        # Mechanisms only for drugs that had none from Open Targets (planned drugs verified via ChEMBL).
        need = [c for c in ids if c in infos and not infos[c].actions]
        if not need:
            return
        mechs = self._chembl_batch(
            "mechanism", "molecule_chembl_id", need, "molecule_chembl_id,target_chembl_id,action_type"
        )
        target_ids = _dedupe([m["target_chembl_id"] for m in mechs if m.get("target_chembl_id")])
        gene_of: dict[str, list[str]] = {}
        for t in self._chembl_batch("target", "target_chembl_id", target_ids, "target_chembl_id,organism,target_components"):
            if t.get("organism") != "Homo sapiens":
                continue
            gene_of[t["target_chembl_id"]] = [
                s["component_synonym"].upper()
                for c in t.get("target_components") or []
                for s in c.get("target_component_synonyms") or []
                if s.get("syn_type") == "GENE_SYMBOL"
            ]
        symbols = _dedupe([g for gs in gene_of.values() for g in gs])
        ensembl = self._map_targets(symbols) if symbols else {}
        for m in mechs:
            info = infos.get(m["molecule_chembl_id"])
            for g in gene_of.get(m.get("target_chembl_id") or "", []):
                if info and g in ensembl:
                    tid, sym = ensembl[g]
                    info.actions.setdefault(tid, set()).add(m.get("action_type") or "UNKNOWN")
                    info.symbols[tid] = sym

    # --- transport ----------------------------------------------------------

    @contextmanager
    def _timed(self, log: _Log) -> Iterator[None]:
        log.used = True
        t0 = time.monotonic()
        try:
            yield
        finally:
            log.duration_s += time.monotonic() - t0

    def _record(self, log: _Log, resp: CachedResponse) -> Any:
        log.network |= not resp.from_cache
        return resp.json()

    def _ot_query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._timed(self.ot):
                resp = self.http.post("opentargets", OT_URL, {"query": query, "variables": variables})
            body = self._record(self.ot, resp)
            if body.get("errors"):
                raise ApiError("; ".join(e.get("message", "") for e in body["errors"])[:300])
            return body["data"]
        except Exception as e:
            self.ot.errors.append(f"{type(e).__name__}: {e}"[:300])
            raise

    def _chembl_get(self, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET a ChEMBL list resource. Any failure marks ChEMBL as down for the rest of the run."""
        if self.chembl_down:
            raise ApiError("ChEMBL unavailable")
        try:
            with self._timed(self.chembl):
                resp = self.http.get("chembl", f"{CHEMBL_URL}/{resource}.json", params)
            return self._record(self.chembl, resp)
        except Exception as e:  # HttpError after http.py retries, or a non-JSON error page
            self.chembl_down = True
            msg = f"{type(e).__name__}: {e}"[:300]
            self.chembl.errors.append(msg)
            self.notes.append(f"ChEMBL unavailable ({msg}); continuing with Open Targets data only")
            raise ApiError(msg) from e

    def _chembl_batch(self, resource: str, id_field: str, ids: list[str], only: str) -> list[dict[str, Any]]:
        key = {"molecule": "molecules", "mechanism": "mechanisms", "target": "targets"}[resource]
        out: list[dict[str, Any]] = []
        for i in range(0, len(ids), _CHEMBL_BATCH):
            chunk = ids[i : i + _CHEMBL_BATCH]
            params = {f"{id_field}__in": ",".join(chunk), "only": only, "limit": 1000}
            out.extend(self._chembl_get(resource, params)[key])
        return out

    def _to_drug(self, d: _DrugInfo) -> Drug:
        name = _norm(d.name)
        drug = Drug(
            chembl_id=d.chembl_id,
            name=name,
            synonyms=[s for s in _dedupe(d.synonyms) if _norm(s) != name],
            max_phase=d.max_phase,
            origin=self.origin.get(d.chembl_id, "database"),
            target_ids=[t for t in d.actions if t in self.targets],
            other_target_symbols=d.other_symbols,
            drug_type=d.drug_type,
        )
        return drug.model_copy(update={"relation": drug_relation(drug, self.plan.modality)})


def drug_modality(drug: Drug) -> str:
    """Modality of a drug: its database type, refined by INN stem; 'unknown' when neither says.
    An antibody that also binds a target outside the subject mechanism is a bispecific."""
    words = [w for n in [drug.name, *drug.synonyms] for w in re.findall(r"[a-z]{5,}", n.lower())]
    stem = next((m for s, m in _INN_STEMS for w in words if w.endswith(s)), None)
    modality = _TYPE_MODALITY.get((drug.drug_type or "").lower()) or stem or "unknown"
    if modality == "gene_therapy" and stem == "cell_therapy":
        modality = "cell_therapy"  # gene-modified cells (CAR-T) are typed "Gene" by ChEMBL / Open Targets
    if modality == "antibody" and drug.other_target_symbols:
        modality = "bispecific"
    return modality


def drug_relation(drug: Drug, plan_modality: Modality) -> DrugRelation:
    """subject_mechanism unless the drug's modality is known and incompatible with the planned modality.
    Drugs of unknown modality are not demoted: there is no evidence they differ."""
    if plan_modality == "unspecified":
        return "subject_mechanism"
    modality = drug_modality(drug)
    if modality == "unknown" or modality in MODALITY_COMPATIBLE.get(plan_modality, {plan_modality}):
        return "subject_mechanism"
    return "same_target_other_modality"


def _known_type(t: str | None) -> bool:
    return bool(t) and t.lower() != "unknown"


def _from_ot(d: dict[str, Any]) -> _DrugInfo:
    info = _DrugInfo(
        chembl_id=d["id"],
        name=d.get("name") or d["id"],
        max_phase=_stage_to_phase(d.get("maximumClinicalStage")),
        drug_type=d.get("drugType"),
    )
    trade = [x["label"] for x in d.get("tradeNames") or [] if x.get("label")]
    other = [x["label"] for x in d.get("synonyms") or [] if x.get("label")]
    info.synonyms = _dedupe([*trade, *other])
    # Open Targets synonyms carry no type: an all-letter non-trade name is taken as the INN.
    trade_norm = {_norm(t) for t in trade}
    inn = next((s for s in other if re.fullmatch(r"[A-Za-z]{6,}", s) and _norm(s) not in trade_norm), None)
    _prefer_inn(info, inn)
    for m in (d.get("mechanismsOfAction") or {}).get("rows") or []:
        for t in m.get("targets") or []:
            info.actions.setdefault(t["id"], set()).add(m.get("actionType") or "UNKNOWN")
            info.symbols[t["id"]] = t.get("approvedSymbol") or t["id"]
    return info


def _prefer_inn(info: _DrugInfo, inn: str | None) -> None:
    """Use the INN as the name when the preferred name is a code (contains a digit); keep the code as a synonym."""
    if inn and re.search(r"\d", info.name) and _norm(inn) != _norm(info.name):
        info.synonyms = _dedupe([info.name, *info.synonyms])
        info.name = inn


def _chembl_synonyms(rows: list[dict[str, Any]]) -> list[str]:
    """Trade names first, then research codes, then other names."""
    order = {"TRADE_NAME": 0, "RESEARCH_CODE": 1}
    rows = sorted(rows, key=lambda r: order.get(r.get("syn_type") or "", 2))
    return _dedupe([r.get("molecule_synonym") or "" for r in rows])


def _symbol_stem(symbol: str) -> str:
    """Gene-family stem of an approved symbol: leading letters plus the first number.
    IL17A / IL17F / IL17RA -> IL17; TNF -> TNF; TNFSF13B -> TNFSF13; CD3E -> CD3; JAK1 -> JAK1."""
    m = re.match(r"[A-Z]+\d*", symbol.upper())
    return m.group(0) if m else symbol.upper()


def _stage_to_phase(stage: str | None) -> float | None:
    if not stage:
        return None
    if stage in _STAGE_PHASE:
        return _STAGE_PHASE[stage]
    m = re.fullmatch(r"PHASE_(\d)(?:_\d)?", stage)
    return float(m.group(1)) if m else None


def _norm(name: str) -> str:
    """Lowercase, whitespace-collapsed form used for drug names and all name matching."""
    return " ".join(name.lower().split())


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        item = item.strip()
        if item and _norm(item) not in seen:
            seen.add(_norm(item))
            out.append(item)
    return out
