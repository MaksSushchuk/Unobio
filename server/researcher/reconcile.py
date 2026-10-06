"""reconcile(bundle): post-process connector output into a consistent bundle.

Runs after all connectors, in this order:
1. dedupe by evidence id (the first occurrence wins; modules and missing entity_refs are merged in);
2. drug linking: ClinicalTrials.gov trials are re-linked with clinicaltrials.match_subject_drugs (interventions,
   title only when no intervention is a product); links that fail it are removed, and
   data.subject_drug_chembl_ids / entity_refs.drug / data.drug_relation are made to agree. Other evidence
   without entity_refs.drug is linked to the subject drug it names (interventions, then title, then snippet;
   whole-word, case-insensitive; terms < 4 chars ignored);
3. evidence_cutoff: drop evidence published after the cutoff; keep undated evidence, except
4. leak guard: undated evidence that carries clinical status (Open Targets clinical candidates:
   max stage, trial phases/statuses) is dropped unless every trial it cites has a ClinicalTrials.gov
   record in the bundle dated on or before the cutoff, and it cites no undated non-trial reports
   (approvals, labels). Otherwise the undated record would reveal post-cutoff status.

Every drop is recorded in Bundle.reconcile_stats.dropped with its reason.

5. derived evidence: deterministic cross-source rules (no LLM, nothing subject-specific) run on what
survived the cutoff. Each creates derived evidence: source="reconcile", kind="conflict" when two sources disagree else "record",
id = evidence_id("reconcile", f"{rule}:{','.join(sorted(related_ids))}"), modules include red-flags.
published_at of derived evidence = the latest published_at among its related evidence (None if all undated).

R1 stop_reason        classify why_stopped of every stopped trial (ClinicalTrials.gov, plus Open Targets
                      trials not in ClinicalTrials.gov) into efficacy / safety / business / enrollment /
                      positive_early_stop / other with ONE batched LLM call (keyword classifier as fallback);
                      no why_stopped -> "unknown". Writes data.stop_category + stop_category_source
                      ("llm" / "keyword" / "none") on the trial; a record per efficacy/safety stop.
                      Modules of stopped ClinicalTrials.gov trials follow the category: unknown -> no red-flags
                      (has_results -> data.stop_note "results posted — check outcome"); positive_early_stop ->
                      green-flags instead of red-flags; otherwise red-flags.
R2 status_mismatch    an NCT id in Open Targets clinical candidates and in ClinicalTrials.gov with a
                      different phase or status -> conflict.
R3 stale_active       recruiting / active / not yet recruiting / enrolling by invitation, but last update
                      posted more than 24 months before the as-of date (cutoff, else bundle creation) -> record.
R4 does_not_transfer  a subject drug with max_phase >= 4 has efficacy/safety stops in this indication -> conflict.
                      Skipped under evidence_cutoff: max_phase is current and undated, so it could leak.
R5 target_failure     2+ distinct subject drugs acting on the subject target family have efficacy/safety stops
                      in this indication -> record, data.severity="high". Subject targets form one family
                      (resolve.py only admits same-family targets), so the family is the subject's target set;
                      the record names the targets actually hit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from researcher.schema import Bundle, DroppedEvidence, Drug, Evidence, ReconcileStats, evidence_id
from researcher.connectors.clinicaltrials import match_subject_drugs, subject_drug_matchers
from researcher.llm import LLM, LlmError

_MIN_TERM_LEN = 4
_NCT_RE = re.compile(r"^NCT\d{8}$", re.IGNORECASE)
# Evidence.data keys that mean "this record states clinical status/stage".
_STATUS_KEYS = ("maxClinicalStage", "trials", "clinicalReports", "nct_ids", "overall_status")


def reconcile(bundle: Bundle, llm: LLM | None = None) -> Bundle:
    """`llm` classifies stop reasons (one call); without it, or if it fails, the keyword classifier is used."""
    total_before = len(bundle.evidence)
    evidence = _dedupe(bundle.evidence)
    duplicates_merged = total_before - len(evidence)

    drugs_linked, drugs_unlinked = _link_drugs(evidence, bundle.subject.drugs)

    dropped: list[DroppedEvidence] = []
    cutoff = bundle.input.evidence_cutoff
    if cutoff is not None:
        evidence = _apply_cutoff(evidence, cutoff, dropped)

    evidence, derived, derived_counts, skipped = apply_rules(bundle, evidence, llm)
    evidence = evidence + derived

    stats = ReconcileStats(
        total_before=total_before,
        duplicates_merged=duplicates_merged,
        drugs_linked=drugs_linked,
        drugs_unlinked=drugs_unlinked,
        dropped_by_cutoff=sum(d.reason == "cutoff" for d in dropped),
        dropped_by_leak_guard=sum(d.reason == "leak_guard" for d in dropped),
        dropped=dropped,
        derived_by_rule=derived_counts,
        rules_skipped=skipped,
    )
    return bundle.model_copy(update={"evidence": evidence, "reconcile_stats": stats})


# --- 1. dedupe -----------------------------------------------------------------


def _dedupe(items: list[Evidence]) -> list[Evidence]:
    out: dict[str, Evidence] = {}
    for ev in items:
        first = out.get(ev.id)
        if first is None:
            out[ev.id] = ev
            continue
        modules = list(first.modules) + [m for m in ev.modules if m not in first.modules]
        refs = {**ev.entity_refs, **first.entity_refs}  # never overwrite the first occurrence's refs
        out[ev.id] = first.model_copy(update={"modules": modules, "entity_refs": refs})
    return list(out.values())


# --- 2. drug linking -----------------------------------------------------------


def _link_drugs(evidence: list[Evidence], drugs: list[Drug]) -> tuple[int, int]:
    """(links added, links removed)."""
    matchers = [
        (d.chembl_id, [t for t in _dedupe_terms([d.name, *d.synonyms]) if len(t) >= _MIN_TERM_LEN])
        for d in drugs
        if d.chembl_id
    ]
    trial_matchers = subject_drug_matchers(drugs)
    linked = unlinked = 0
    for i, ev in enumerate(evidence):
        if ev.source == "clinicaltrials":
            new = _relink_trial(ev, trial_matchers)
            old_drug, new_drug = ev.entity_refs.get("drug"), new.entity_refs.get("drug")
            linked += bool(new_drug and new_drug != old_drug)
            unlinked += bool(old_drug and new_drug != old_drug)
            evidence[i] = new
            continue
        if ev.entity_refs.get("drug"):
            continue
        chembl = _find_drug(_texts(ev), matchers)
        if chembl:
            evidence[i] = ev.model_copy(update={"entity_refs": {**ev.entity_refs, "drug": chembl}})
            linked += 1
    return linked, unlinked


def _relink_trial(ev: Evidence, matchers: list[tuple[Drug, list[str]]]) -> Evidence:
    """Recompute a trial's subject drugs from its interventions/title; refs and data always agree."""
    interventions = [iv for iv in ev.data.get("interventions") or [] if isinstance(iv, dict)]
    matched = match_subject_drugs(interventions, ev.data.get("title") or ev.title, matchers)
    refs = {k: v for k, v in ev.entity_refs.items() if k != "drug"}
    if matched:
        refs["drug"] = matched[0].chembl_id
    data = {**ev.data, "subject_drug_chembl_ids": [d.chembl_id for d in matched],
            "drug_relation": matched[0].relation if matched else None}
    if refs == ev.entity_refs and data == ev.data:
        return ev
    return ev.model_copy(update={"entity_refs": refs, "data": data})


def _texts(ev: Evidence) -> list[str]:
    """Texts to search, most specific first: intervention names, then title, then snippet."""
    out: list[str] = []
    for iv in ev.data.get("interventions") or []:
        if isinstance(iv, dict):
            out.append(" ".join([iv.get("name") or "", *(iv.get("otherNames") or [])]))
    return out + [ev.title, ev.snippet]


def _find_drug(texts: list[str], matchers: list[tuple[str, list[str]]]) -> str | None:
    """The drug named earliest in the first text that names any subject drug."""
    for text in texts:
        padded = f" {_norm(text)} "
        best: tuple[int, str] | None = None
        for chembl, terms in matchers:
            for term in terms:
                pos = padded.find(f" {term} ")
                if pos >= 0 and (best is None or pos < best[0]):
                    best = (pos, chembl)
        if best:
            return best[1]
    return None


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def _dedupe_terms(terms: list[str]) -> list[str]:
    out: list[str] = []
    for t in terms:
        n = _norm(t)
        if n and n not in out:
            out.append(n)
    return out


# --- 3/4. cutoff and leak guard ------------------------------------------------


def _apply_cutoff(evidence: list[Evidence], cutoff: date, dropped: list[DroppedEvidence]) -> list[Evidence]:
    # Dates of ClinicalTrials.gov records (last update posted), before anything is dropped.
    trial_dates: dict[str, date | None] = {}
    for ev in evidence:
        nct = ev.data.get("nct_id") if ev.source == "clinicaltrials" else None
        if nct:
            trial_dates[nct.upper()] = ev.published_at

    kept: list[Evidence] = []
    for ev in evidence:
        if ev.published_at is not None and ev.published_at > cutoff:
            dropped.append(_drop(ev, "cutoff", f"published {ev.published_at} > cutoff {cutoff}"))
        elif ev.published_at is None and (reason := _leak(ev, trial_dates, cutoff)):
            dropped.append(_drop(ev, "leak_guard", reason))
        else:
            kept.append(ev)
    return kept


def _leak(ev: Evidence, trial_dates: dict[str, date | None], cutoff: date) -> str | None:
    """Why this undated record could reveal post-cutoff status, or None if it is safe to keep."""
    data = ev.data
    if not any(data.get(k) for k in _STATUS_KEYS):
        return None
    ncts, other_reports = _trial_refs(data)
    reasons: list[str] = []
    after = [n for n in ncts if trial_dates.get(n) is not None and trial_dates[n] > cutoff]
    undated = [n for n in ncts if trial_dates.get(n) is None]
    if after:
        reasons.append("cites trials dated after the cutoff: " + ", ".join(f"{n} ({trial_dates[n]})" for n in after))
    if undated:
        reasons.append("cites trials with no dated ClinicalTrials.gov record in the bundle: " + ", ".join(undated))
    if other_reports:
        reasons.append(f"cites {other_reports} undated non-trial clinical report(s) (approvals/labels)")
    if not ncts and not other_reports:
        reasons.append("states clinical status with no datable source")
    return "; ".join(reasons) or None


def _trial_refs(data: dict[str, Any]) -> tuple[list[str], int]:
    """(NCT ids cited, number of cited clinical reports that are not trials)."""
    ncts: list[str] = []
    for n in [*(data.get("nct_ids") or []), *(t.get("nct_id") for t in data.get("trials") or [] if isinstance(t, dict))]:
        if n and _NCT_RE.match(n) and n.upper() not in ncts:
            ncts.append(n.upper())
    reports = [r for r in data.get("clinicalReports") or [] if isinstance(r, dict)]
    other = sum(1 for r in reports if not _NCT_RE.match(r.get("id") or ""))
    return ncts, other


def _drop(ev: Evidence, reason: str, detail: str) -> DroppedEvidence:
    return DroppedEvidence(id=ev.id, source=ev.source, title=ev.title, reason=reason, detail=detail)


# --- 5. derived evidence: cross-source rules -------------------------------------



RULES_SOURCE = "reconcile"
STOPPED = ("TERMINATED", "WITHDRAWN", "SUSPENDED")
ACTIVE = ("RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION")
FAILURE_CATEGORIES = ("efficacy", "safety")
STALE_MONTHS = 24
_WHY_LEN = 160

StopCategory = Literal["efficacy", "safety", "business", "enrollment", "positive_early_stop", "other"]
NO_REASON = "unknown"  # stop_category of a stopped trial with no why_stopped
RESULTS_NOTE = "results posted — check outcome"

# Keyword fallback. Categories in priority order (the first matching category is the trial's stop_category). Patterns are
# phrases, not bare words: "safety" alone also appears in "safety was adequately evaluated elsewhere",
# "failed to meet" also in "recruitment failed to meet the target".
_STOP_PATTERNS: list[tuple[str, list[str]]] = [
    ("efficacy", [
        r"futil\w*", r"(?:lack|absence) of (?:\w+ ){0,2}(?:efficacy|effect|benefit|response)",
        r"(?:insufficient|inadequate|limited|poor) (?:\w+ ){0,2}(?:efficacy|benefit|response)",
        r"no (?:\w+ ){0,2}(?:benefit|efficacy|treatment effect)", r"not effective", r"ineffective\w*", r"inefficacy",
        r"(?:did not|does not|failed to|unlikely to|not) (?:meet|achieve|reach|demonstrate|show)\b[^.;]{0,40}?"
        r"\b(?:endpoint|end point|efficacy|benefit|superiority|objective)",
        r"worsen\w*", r"disease progression",
    ]),
    ("safety", [
        r"adverse (?:event|reaction|effect|finding)s?", r"safety (?:concern|issue|signal|reason|finding|risk|profile)s?",
        r"(?:for|due to|because of) safety", r"toxicit\w*", r"\btoxic\b", r"side effects?", r"deaths?",
        r"serious (?:infection|event)s?", r"unacceptable risk",
    ]),
    ("positive_early_stop", [
        r"overwhelming (?:efficacy|benefit)", r"early (?:evidence of )?(?:efficacy|benefit)",
        r"(?:met|achieved|reached) (?:its |the )?(?:primary )?(?:end ?points?|objectives?) early",
        r"positive (?:interim )?(?:results?|outcomes?|data)", r"efficacy (?:was |has been )?(?:demonstrated|established)",
    ]),
    ("business", [
        r"sponsor\w*", r"business", r"funding", r"funds?\b", r"financial", r"strategic", r"portfolio",
        r"commercial", r"company decision", r"priorit\w*", r"development program",
    ]),
    ("enrollment", [r"recruit\w*", r"enrol\w*", r"accrual", r"enough (?:patients|participants|subjects)"]),
]
_NEGATION = re.compile(r"\b(?:no|not|without|unrelated to|non)\b[\w\s-]{0,20}$")


def classify_stop(why: str | None) -> tuple[str, list[str]]:
    """Keyword classifier: (category, matched phrases). Matches preceded by a negation ("no safety concerns",
    "not based on safety or efficacy") are ignored. Used when the LLM is unavailable or fails."""
    text = " ".join((why or "").lower().split())
    matches: dict[str, list[str]] = {}
    for category, patterns in _STOP_PATTERNS:
        for pat in patterns:
            for m in re.finditer(pat, text):
                if not _NEGATION.search(text[: m.start()]):
                    matches.setdefault(category, []).append(m.group(0))
    for category, _ in _STOP_PATTERNS:
        if category in matches:
            return category, _unique([s for c, _ in _STOP_PATTERNS for s in matches.get(c, [])])
    return "other", []


class _StopLabel(BaseModel):
    id: int
    category: StopCategory


class _StopLabels(BaseModel):
    labels: list[_StopLabel]


_STOP_PROMPT = """Classify why each clinical trial below was stopped (terminated, withdrawn or suspended).

Categories:
- efficacy: lack of efficacy, futility, failed or unlikely to meet endpoints, no benefit over existing therapy
- safety: adverse events, toxicity, deaths, safety concerns or signals
- business: sponsor, company, strategic, portfolio, funding or commercial decisions
- enrollment: slow or poor recruitment / accrual, too few eligible participants
- positive_early_stop: stopped early because the treatment already showed efficacy or met its endpoint
- other: anything else (logistics, regulatory, investigator left, COVID-19, competing studies, standard of care
  changed, unclear)

Rules:
- Read negations carefully: "not based on safety or efficacy but due to slow enrollment" is enrollment;
  "no safety concerns" is not safety.
- If several reasons are given, use the first that applies in this order: efficacy, safety, positive_early_stop,
  business, enrollment, other.
- Return one label per id, using exactly the category names above.

Reasons:
{items}
"""


def classify_stops(texts: list[str], llm: LLM | None) -> dict[str, tuple[str, str, list[str]]]:
    """why_stopped text -> (category, source "llm"/"keyword", keyword matches). One LLM call for all texts;
    texts the LLM fails on or leaves out fall back to the keyword classifier."""
    texts = _unique([t for t in texts if t and t.strip()])
    out: dict[str, tuple[str, str, list[str]]] = {}
    if llm is not None and texts:
        items = "\n".join(f"{i}. {' '.join(t.split())}" for i, t in enumerate(texts))
        try:
            labels = llm.generate_json(_STOP_PROMPT.format(items=items), _StopLabels, purpose="stop_reason")
            for label in labels.labels:
                if 0 <= label.id < len(texts):
                    out.setdefault(texts[label.id], (label.category, "llm", []))
        except LlmError:
            pass
    for t in texts:
        if t not in out:
            category, matched = classify_stop(t)
            out[t] = (category, "keyword", matched)
    return out


@dataclass
class _Stop:
    nct: str
    status: str
    why: str | None
    category: str
    evidence_ids: list[str]
    drugs: list[str] = field(default_factory=list)  # subject ChEMBL ids
    published_at: date | None = None


class _Rules:
    def __init__(self, bundle: Bundle, evidence: list[Evidence], llm: LLM | None = None) -> None:
        self.bundle, self.evidence, self.llm = bundle, evidence, llm
        self.subject = bundle.subject
        self.cutoff = bundle.input.evidence_cutoff
        self.as_of = self.cutoff or bundle.created_at.date()
        self.drug_names = {d.chembl_id: d.name for d in self.subject.drugs if d.chembl_id}
        self.symbols = dict(zip(self.subject.target_ids, self.subject.target_symbols))
        self.by_id = {e.id: e for e in evidence}
        self.derived: dict[str, Evidence] = {}
        self.counts: dict[str, int] = {}
        self.skipped: dict[str, str] = {}
        self.ct = {e.data["nct_id"]: e for e in evidence if e.source == "clinicaltrials" and e.data.get("nct_id")}
        # NCT id -> Open Targets clinical-candidate records citing it.
        self.ot_citing: dict[str, list[Evidence]] = {}
        for e in evidence:
            for t in _trials(e):
                self.ot_citing.setdefault(t["nct_id"], []).append(e)
        self.stops: list[_Stop] = []

    def run(self) -> list[Evidence]:
        self.r1_stop_reason()
        self.r2_status_mismatch()
        self.r3_stale_active()
        self.r4_does_not_transfer()
        self.r5_target_failure()
        return list(self.derived.values())

    # --- helpers ---------------------------------------------------------------

    def _emit(self, rule: str, kind: str, related: list[str], modules: list[str], title: str, snippet: str,
              data: dict[str, Any], entity_refs: dict[str, str] | None = None, url: str | None = None) -> None:
        related = sorted(set(related))
        eid = evidence_id(RULES_SOURCE, f"{rule}:{','.join(related)}")
        if eid in self.derived:
            return
        dates = [self.by_id[r].published_at for r in related if r in self.by_id and self.by_id[r].published_at]
        refs = dict(entity_refs or {})
        if self.subject.disease_ids:
            refs.setdefault("disease", self.subject.disease_ids[0])
        self.derived[eid] = Evidence(
            id=eid, source=RULES_SOURCE, url=url or (self.by_id[related[0]].url if related and related[0] in self.by_id else ""),
            retrieved_at=self.bundle.created_at, kind=kind, modules=["red-flags", *[m for m in modules if m != "red-flags"]],
            entity_refs=refs, title=title, snippet=snippet, data={"rule": rule, **data},
            related_evidence_ids=related, published_at=max(dates) if dates else None,
        )
        self.counts[rule] = self.counts.get(rule, 0) + 1

    def _set_stop_modules(self, ev: Evidence, category: str) -> Evidence:
        """red-flags only for a stated, non-positive stop reason; green-flags for a positive early stop."""
        modules = [m for m in ev.modules if m not in ("red-flags", "green-flags")]
        if category == "positive_early_stop":
            modules.append("green-flags")
        elif category != NO_REASON:
            modules.append("red-flags")
        modules = [m for m in ev.modules if m in modules] + [m for m in modules if m not in ev.modules]
        if modules == ev.modules:
            return ev
        new = ev.model_copy(update={"modules": modules})
        self.by_id[ev.id] = new
        self.evidence[self.evidence.index(ev)] = new
        return new

    def _drug(self, chembl: str) -> str:
        return self.drug_names.get(chembl, chembl)

    def _replace(self, ev: Evidence, data: dict[str, Any]) -> Evidence:
        new = ev.model_copy(update={"data": data})
        self.by_id[ev.id] = new
        self.evidence[self.evidence.index(ev)] = new
        return new

    # --- R1 -------------------------------------------------------------------

    def _classify(self, why: str | None) -> tuple[str, str, list[str]]:
        if not (why or "").strip():
            return NO_REASON, "none", []
        return self.stop_labels[why]

    def r1_stop_reason(self) -> None:
        texts = [ev.data.get("why_stopped") for ev in self.ct.values() if ev.data.get("overall_status") in STOPPED]
        texts += [t.get("why_stopped") for ev in self.evidence for t in _trials(ev) if t.get("status") in STOPPED]
        self.stop_labels = classify_stops(texts, self.llm)

        for nct, ev in list(self.ct.items()):
            status = ev.data.get("overall_status")
            if status not in STOPPED:
                continue
            category, source, matched = self._classify(ev.data.get("why_stopped"))
            data = {**ev.data, "stop_category": category, "stop_category_source": source,
                    "stop_category_matches": matched}
            if category == NO_REASON and ev.data.get("has_results"):
                data["stop_note"] = RESULTS_NOTE
            ev = self._replace(ev, data)
            ev = self._set_stop_modules(ev, category)
            self.ct[nct] = ev
            drugs = _unique([ev.entity_refs.get("drug"), *(ev.data.get("subject_drug_chembl_ids") or []),
                             *(o.entity_refs.get("drug") for o in self.ot_citing.get(nct, []))])
            ids = [ev.id, *(o.id for o in self.ot_citing.get(nct, []))]
            self.stops.append(_Stop(nct, status, ev.data.get("why_stopped"), category, ids, drugs, ev.published_at))
        # Open Targets trials with no ClinicalTrials.gov record in the bundle.
        for ev in list(self.evidence):
            trials = _trials(ev)
            if not trials:
                continue
            new_trials = []
            for t in trials:
                if t.get("status") in STOPPED:
                    category, source, _ = self._classify(t.get("why_stopped"))
                    t = {**t, "stop_category": category, "stop_category_source": source}
                    if t["nct_id"] not in self.ct:
                        drug = ev.entity_refs.get("drug")
                        self.stops.append(_Stop(t["nct_id"], t["status"], t.get("why_stopped"), category, [ev.id],
                                                [drug] if drug else []))
                new_trials.append(t)
            if new_trials != trials:
                self._replace(ev, {**ev.data, "trials": new_trials})

        for s in self.stops:
            if s.category not in FAILURE_CATEGORIES:
                continue
            drugs = ", ".join(self._drug(d) for d in s.drugs) or "no subject drug"
            why = _short(s.why)
            self._emit(
                "stop_reason", "record", s.evidence_ids, ["pipeline", "trial-design"],
                title=f"{s.nct} stopped for {s.category} ({drugs})",
                snippet=f"{s.nct} ({drugs}) {s.status} — {s.category} stop in {self.subject.indication}: {why}",
                data={"nct_id": s.nct, "stop_category": s.category, "overall_status": s.status, "why_stopped": s.why,
                      "drug_chembl_ids": s.drugs},
                entity_refs={"drug": s.drugs[0]} if s.drugs else None,
                url=f"https://clinicaltrials.gov/study/{s.nct}",
            )

    # --- R2 -------------------------------------------------------------------

    def r2_status_mismatch(self) -> None:
        for nct, ct in self.ct.items():
            for ot in self.ot_citing.get(nct, []):
                t = next(t for t in _trials(ot) if t["nct_id"] == nct)
                diffs = {}
                ct_phase, ot_phase = _phase_set(ct.data.get("phases")), _phase_set([t.get("phase")])
                if ct_phase and ot_phase and ct_phase != ot_phase:
                    diffs["phase"] = {"clinicaltrials": "/".join(ct.data.get("phases") or []), "opentargets": t.get("phase")}
                ct_status, ot_status = ct.data.get("overall_status"), t.get("status")
                if ct_status and ot_status and ot_status != "UNKNOWN" and ct_status != ot_status:
                    diffs["status"] = {"clinicaltrials": ct_status, "opentargets": ot_status}
                if not diffs:
                    continue
                parts = "; ".join(f"{k}: Open Targets {v['opentargets']} vs ClinicalTrials.gov {v['clinicaltrials']}"
                                  for k, v in diffs.items())
                drug = ot.entity_refs.get("drug")
                self._emit(
                    "status_mismatch", "conflict", [ct.id, ot.id], ["pipeline"],
                    title=f"{nct}: Open Targets and ClinicalTrials.gov disagree",
                    snippet=f"{nct} ({self._drug(drug) if drug else 'unknown drug'}) {parts}",
                    data={"nct_id": nct, "differences": diffs, "supporting": [ct.id], "contradicting": [ot.id],
                          "note": "supporting = the registry record (authoritative); contradicting = Open Targets copy"},
                    entity_refs={"drug": drug} if drug else None,
                    url=ct.url,
                )

    # --- R3 -------------------------------------------------------------------

    def r3_stale_active(self) -> None:
        for nct, ct in self.ct.items():
            status = ct.data.get("overall_status")
            updated = ct.published_at
            if status not in ACTIVE or updated is None:
                continue
            months = _months_between(updated, self.as_of)
            if months <= STALE_MONTHS:
                continue
            drug = ct.entity_refs.get("drug")
            self._emit(
                "stale_active", "record", [ct.id], ["pipeline", "competitive-landscape"],
                title=f"{nct} likely stale ({status})",
                snippet=f"{nct} is {status} but last updated {updated} ({months} months before {self.as_of}) — likely stale",
                data={"nct_id": nct, "overall_status": status, "last_update_posted": str(updated),
                      "months_since_update": months, "as_of": str(self.as_of), "threshold_months": STALE_MONTHS},
                entity_refs={"drug": drug} if drug else None,
            )

    # --- R4 / R5 --------------------------------------------------------------

    def _failures_by_drug(self) -> dict[str, list[_Stop]]:
        out: dict[str, list[_Stop]] = {}
        for s in self.stops:
            if s.category in FAILURE_CATEGORIES:
                for d in s.drugs:
                    out.setdefault(d, []).append(s)
        return out

    def r4_does_not_transfer(self) -> None:
        if self.cutoff is not None:
            self.skipped["does_not_transfer"] = "evidence_cutoff set: max_phase is current and undated, so it could leak"
            return
        failures = self._failures_by_drug()
        for d in self.subject.drugs:
            stops = failures.get(d.chembl_id or "")
            if not stops or (d.max_phase or 0) < 4:
                continue
            ids = [i for s in stops for i in s.evidence_ids]
            listed = "; ".join(f"{s.nct} {s.category}: {_short(s.why, 80)}" for s in stops)
            self._emit(
                "does_not_transfer", "conflict", ids, ["scientific-evidence", "competitive-landscape"],
                title=f"{d.name}: approved elsewhere, failed in {self.subject.indication}",
                snippet=f"{d.name} is approved (max phase {d.max_phase:g}) but was stopped for "
                        f"{'/'.join(_unique([s.category for s in stops]))} in {self.subject.indication}: {listed}",
                data={"chembl_id": d.chembl_id, "drug_name": d.name, "max_phase": d.max_phase,
                      "max_phase_source": "subject.drugs (ChEMBL / Open Targets, not dated)",
                      "failed_trials": [{"nct_id": s.nct, "stop_category": s.category, "why_stopped": s.why} for s in stops],
                      "supporting": [], "contradicting": _unique(ids)},
                entity_refs={"drug": d.chembl_id, **({"target": d.target_ids[0]} if d.target_ids else {})},
            )

    def r5_target_failure(self) -> None:
        failures = self._failures_by_drug()
        drugs = [d for d in self.subject.drugs if d.chembl_id in failures and set(d.target_ids) & set(self.subject.target_ids)]
        if len(drugs) < 2:
            return
        targets = _unique([t for d in drugs for t in d.target_ids if t in self.symbols])
        symbols = [self.symbols[t] for t in targets]
        ids = [i for d in drugs for s in failures[d.chembl_id] for i in s.evidence_ids]
        listed = "; ".join(
            f"{d.name} ({'/'.join(self.symbols[t] for t in d.target_ids if t in self.symbols)}): "
            + ", ".join(f"{s.nct} {s.category}" for s in failures[d.chembl_id])
            for d in drugs
        )
        self._emit(
            "target_failure", "record", ids, ["scientific-evidence", "pipeline"],
            title=f"{'/'.join(symbols)}: {len(drugs)} drugs failed in {self.subject.indication}",
            snippet=f"Target-level failure — {len(drugs)} distinct drugs on {'/'.join(symbols)} stopped for "
                    f"efficacy/safety in {self.subject.indication}: {listed}",
            data={"severity": "high", "target_ids": targets, "target_symbols": symbols,
                  "drugs": [{"chembl_id": d.chembl_id, "name": d.name, "target_ids": d.target_ids,
                             "failed_trials": [{"nct_id": s.nct, "stop_category": s.category} for s in failures[d.chembl_id]]}
                            for d in drugs]},
            entity_refs={"target": targets[0]},
        )


def apply_rules(bundle: Bundle, evidence: list[Evidence], llm: LLM | None = None,
                ) -> tuple[list[Evidence], list[Evidence], dict[str, int], dict[str, str]]:
    """Returns (evidence with stop categories written in, derived evidence, counts per rule, skipped rules)."""
    rules = _Rules(bundle, list(evidence), llm)
    derived = rules.run()
    return rules.evidence, derived, rules.counts, rules.skipped


def _trials(ev: Evidence) -> list[dict[str, Any]]:
    if ev.source == "clinicaltrials":
        return []
    return [t for t in ev.data.get("trials") or [] if isinstance(t, dict) and t.get("nct_id")]


def _phase_set(values: list[str | None] | None) -> frozenset[str]:
    """{'2','3'} from ['PHASE2','PHASE3'], 'PHASE_2_3' or 'PHASE2/PHASE3'; 'E1' for early phase 1; empty if unknown."""
    out: set[str] = set()
    for v in values or []:
        v = (v or "").upper()
        if "EARLY" in v:
            out.add("E1")
            continue
        out |= set(re.findall(r"[1-4]", v.replace("PHASE", "")))
    return frozenset(out)


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def _short(text: str | None, n: int = _WHY_LEN) -> str:
    text = " ".join((text or "no reason given").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _unique(items: list[Any]) -> list[Any]:
    out: list[Any] = []
    for x in items:
        if x and x not in out:
            out.append(x)
    return out
