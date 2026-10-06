"""Open Targets Platform connector (GraphQL, https://api.platform.opentargets.org/api/v4/graphql).

For every subject target x subject disease:
1. association: overall + per-datatype + per-datasource scores (indirect, as on the platform's
   association pages; the direct score is kept alongside);
2. genetic evidence: top gwas_credible_sets (locus-to-gene) and gene_burden records, capped;
3. per target: safety liabilities (one Evidence) and tractability (one Evidence);
4. known drugs: the disease's drug/clinical candidates whose drug acts on a subject target, with
   the disease-specific max clinical stage and the clinical reports (NCT ids, phase, status).

Open Targets records are not dated, so published_at is None and evidence_cutoff does not filter them.

Schema checked by introspection and live queries against platform release 26.09 (API 26.9.0).
Fields used: disease.{name, associatedTargets(Bs, enableIndirect){rows{score target{id}
datatypeScores{id score} datasourceScores{id score}}}, evidences(ensemblIds, enableIndirect,
datasourceIds, size){rows{id datasourceId score literature pValueMantissa pValueExponent beta oddsRatio
statisticalMethod statisticalMethodOverview cohortId ancestry studySampleSize diseaseFromSource credibleSet{studyLocusId studyId
pValueMantissa pValueExponent beta finemappingMethod confidence variant{id rsIds} study{traitFromSource
publicationFirstAuthor publicationDate pubmedId nSamples}}}}, drugAndClinicalCandidates{rows{maxClinicalStage
drug{id name drugType} clinicalReports{id source clinicalStage trialPhase trialOverallStatus trialWhyStopped
url}}}}; targets(ensemblIds).{id approvedSymbol tractability{label modality value} safetyLiabilities{event
eventId datasource literature url effects{direction dosing} biosamples{tissueLabel cellLabel}
studies{name type description}} drugAndClinicalCandidates{rows{drug{id}}}}.
The old knownDrugs / known_drug fields no longer exist (replaced by drugAndClinicalCandidates / "clinical").
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

from researcher.schema import Evidence, SourceStatus, Subject, evidence_id
from researcher.http import HttpClient

SOURCE = "opentargets"
API_URL = "https://api.platform.opentargets.org/api/v4/graphql"
PLATFORM = "https://platform.opentargets.org"

GENETIC_DATASOURCES = ["gwas_credible_sets", "gene_burden"]
_GENETIC_CAP = 10
_SNIPPET_DATATYPES = 4
_SNIPPET_NCT_IDS = 5

# Datatype ids as the API reports them -> short snippet labels. Unknown ids fall back to the id.
_DATATYPE_LABELS = {
    "genetic_association": "genetic",
    "genetic_literature": "genetic literature",
    "somatic_mutation": "somatic",
    "clinical": "clinical",
    "known_drug": "known drug",
    "affected_pathway": "pathways",
    "rna_expression": "RNA expression",
    "literature": "literature",
    "animal_model": "animal model",
}
_MODALITY_LABELS = {"SM": "small molecule", "AB": "antibody", "PR": "PROTAC", "OC": "other clinical"}

_Q_TARGETS = """
query Targets($ids: [String!]!) {
  targets(ensemblIds: $ids) {
    id approvedSymbol
    tractability { label modality value }
    safetyLiabilities {
      event eventId datasource literature url
      effects { direction dosing }
      biosamples { tissueLabel cellLabel }
      studies { name type description }
    }
    drugAndClinicalCandidates { rows { drug { id } } }
  }
}"""

_Q_DISEASE = """
query Disease($d: String!, $ids: [String!]!, $size: Int!) {
  disease(efoId: $d) {
    id name
    indirect: associatedTargets(Bs: $ids, enableIndirect: true, page: {index: 0, size: $size}) {
      rows { score target { id } datatypeScores { id score } datasourceScores { id score } }
    }
    direct: associatedTargets(Bs: $ids, page: {index: 0, size: $size}) { rows { score target { id } } }
    drugAndClinicalCandidates {
      rows {
        maxClinicalStage
        drug { id name drugType }
        clinicalReports { id source clinicalStage trialPhase trialOverallStatus trialWhyStopped url }
      }
    }
  }
}"""

_Q_GENETIC = """
query Genetic($d: String!, $t: String!, $sources: [String!]!, $size: Int!) {
  disease(efoId: $d) {
    evidences(ensemblIds: [$t], enableIndirect: true, datasourceIds: $sources, size: $size) {
      rows {
        id datasourceId score literature
        pValueMantissa pValueExponent beta oddsRatio statisticalMethod statisticalMethodOverview cohortId
        ancestry studySampleSize diseaseFromSource
        disease { id name }
        credibleSet {
          studyLocusId studyId pValueMantissa pValueExponent beta finemappingMethod confidence
          variant { id rsIds }
          study { traitFromSource publicationFirstAuthor publicationDate pubmedId nSamples }
        }
      }
    }
  }
}"""


class ApiError(Exception):
    pass


class OpenTargetsConnector:
    source = SOURCE

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http
        self.last_status: SourceStatus | None = None

    def fetch(self, subject: Subject) -> list[Evidence]:
        """Return Open Targets evidence for the subject. Never raises; see `last_status`."""
        t0 = time.monotonic()
        run = _Run(subject, self.http or HttpClient())
        try:
            evidence = run.collect()
        except Exception as e:  # never raise into the pipeline
            run.errors.append(f"{type(e).__name__}: {e}"[:300])
            evidence = list(run.evidence.values())
        finally:
            if self.http is None:
                run.http.close()
        self.last_status = SourceStatus(
            source=SOURCE,
            ok=not run.errors,
            records=len(evidence),
            error="; ".join(run.errors) or None,
            duration_s=round(time.monotonic() - t0, 4),
            cached=run.requests > 0 and not run.network,
        )
        return evidence


def fetch(subject: Subject, http: HttpClient | None = None) -> list[Evidence]:
    return OpenTargetsConnector(http).fetch(subject)


class _Run:
    def __init__(self, subject: Subject, http: HttpClient) -> None:
        self.subject, self.http = subject, http
        self.errors: list[str] = []
        self.requests = 0
        self.network = False
        self.now = datetime.now(UTC)
        self.evidence: dict[str, Evidence] = {}
        self.symbols = dict(zip(subject.target_ids, subject.target_symbols))
        self.drug_targets: dict[str, list[str]] = {}  # ChEMBL id -> subject target ids it acts on

    def collect(self) -> list[Evidence]:
        targets = self.subject.target_ids
        if not targets:
            self.errors.append("no subject targets")
        if not self.subject.disease_ids:
            self.errors.append("no subject disease")
        if targets:
            self._step("targets", self._targets)
        for disease_id in self.subject.disease_ids:
            self._step(f"disease {disease_id}", lambda d=disease_id: self._disease(d))
            for target_id in targets:
                self._step(f"genetic {target_id}/{disease_id}", lambda d=disease_id, t=target_id: self._genetic(t, d))
        return list(self.evidence.values())

    def _step(self, label: str, fn: Any) -> None:
        try:
            fn()
        except Exception as e:
            self.errors.append(f"{label}: {type(e).__name__}: {e}"[:300])

    def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        resp = self.http.post(SOURCE, API_URL, {"query": query, "variables": variables})
        self.requests += 1
        self.network |= not resp.from_cache
        body = resp.json()
        if body.get("errors"):
            raise ApiError("; ".join(e.get("message", "") for e in body["errors"])[:300])
        return body["data"]

    def _add(self, native_key: str, **fields: Any) -> None:
        eid = evidence_id(SOURCE, native_key)
        if eid not in self.evidence:
            self.evidence[eid] = Evidence(
                id=eid, source=SOURCE, retrieved_at=self.now, kind="record", published_at=None, **fields
            )

    # --- targets: safety, tractability, drugs per target ---------------------

    def _targets(self) -> None:
        data = self._query(_Q_TARGETS, {"ids": self.subject.target_ids})
        for t in data.get("targets") or []:
            tid, symbol = t["id"], t.get("approvedSymbol") or self.symbols.get(t["id"], t["id"])
            self.symbols.setdefault(tid, symbol)
            for row in _rows(t.get("drugAndClinicalCandidates")):
                if (row.get("drug") or {}).get("id"):
                    self.drug_targets.setdefault(row["drug"]["id"], [])
                    if tid not in self.drug_targets[row["drug"]["id"]]:
                        self.drug_targets[row["drug"]["id"]].append(tid)
            self._safety(tid, symbol, t.get("safetyLiabilities") or [])
            self._tractability(tid, symbol, t.get("tractability") or [])
        # Subject drugs were verified against these targets in resolve.py; keep them even if the
        # target's candidate list lacks them.
        for d in self.subject.drugs:
            if d.chembl_id:
                ids = self.drug_targets.setdefault(d.chembl_id, [])
                ids.extend(t for t in d.target_ids if t not in ids)

    def _safety(self, tid: str, symbol: str, liabilities: list[dict[str, Any]]) -> None:
        if not liabilities:
            return
        events = _dedupe([(s.get("event") or "unspecified event") for s in liabilities])
        shown = ", ".join(events[:6]) + (f" (+{len(events) - 6} more)" if len(events) > 6 else "")
        self._add(
            f"safety:{tid}",
            url=f"{PLATFORM}/target/{tid}",
            modules=["red-flags", "scientific-evidence"],
            entity_refs={"target": tid},
            title=f"{symbol} target safety liabilities",
            snippet=f"{symbol} safety liabilities ({len(liabilities)}): {shown}",
            data={"target_id": tid, "target_symbol": symbol, "safetyLiabilities": liabilities},
        )

    def _tractability(self, tid: str, symbol: str, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        by_modality: dict[str, list[str]] = {}
        for it in items:
            if it.get("value"):
                by_modality.setdefault(it["modality"], []).append(it["label"])
        parts = [f"{_MODALITY_LABELS.get(m, m)}: {', '.join(labels)}" for m, labels in by_modality.items()]
        self._add(
            f"tractability:{tid}",
            url=f"{PLATFORM}/target/{tid}",
            modules=["scientific-evidence"],
            entity_refs={"target": tid},
            title=f"{symbol} tractability",
            snippet=f"{symbol} tractability — " + ("; ".join(parts) if parts else "no positive assessments"),
            data={"target_id": tid, "target_symbol": symbol, "tractability": items, "positive": by_modality},
        )

    # --- disease: associations and known drugs --------------------------------

    def _disease(self, disease_id: str) -> None:
        targets = self.subject.target_ids
        data = self._query(_Q_DISEASE, {"d": disease_id, "ids": targets, "size": max(len(targets), 1)})
        disease = data.get("disease")
        if disease is None:
            raise ApiError(f"disease {disease_id} not found")
        name = disease.get("name") or disease_id
        if targets:
            direct = {r["target"]["id"]: r["score"] for r in _rows(disease.get("direct"))}
            for row in _rows(disease.get("indirect")):
                self._association(row, disease_id, name, direct.get(row["target"]["id"]))
        self._known_drugs(disease, disease_id, name)

    def _association(self, row: dict[str, Any], disease_id: str, disease_name: str, direct: float | None) -> None:
        tid = row["target"]["id"]
        symbol = self.symbols.get(tid, tid)
        datatypes = {s["id"]: s["score"] for s in sorted(row.get("datatypeScores") or [], key=lambda s: -s["score"])}
        datasources = {s["id"]: s["score"] for s in sorted(row.get("datasourceScores") or [], key=lambda s: -s["score"])}
        parts = [f"{_DATATYPE_LABELS.get(k, k.replace('_', ' '))} {v:.2f}" for k, v in list(datatypes.items())[:_SNIPPET_DATATYPES]]
        snippet = f"{symbol}–{disease_name} association {row['score']:.2f}"
        if parts:
            snippet += "; " + ", ".join(parts)
        self._add(
            f"assoc:{tid}:{disease_id}",
            url=f"{PLATFORM}/evidence/{tid}/{disease_id}",
            modules=["scientific-evidence"],
            entity_refs={"target": tid, "disease": disease_id},
            title=f"{symbol} – {disease_name} association (Open Targets)",
            snippet=snippet,
            data={
                "target_id": tid,
                "target_symbol": symbol,
                "disease_id": disease_id,
                "disease_name": disease_name,
                "score": row["score"],
                "direct_score": direct,
                "datatypeScores": datatypes,
                "datasourceScores": datasources,
            },
        )

    def _known_drugs(self, disease: dict[str, Any], disease_id: str, disease_name: str) -> None:
        for row in _rows(disease.get("drugAndClinicalCandidates")):
            drug = row.get("drug") or {}
            chembl = drug.get("id")
            target_ids = self.drug_targets.get(chembl or "")
            if not chembl or not target_ids:
                continue
            reports = [
                {k: r.get(k) for k in ("id", "source", "clinicalStage", "trialPhase", "trialOverallStatus", "trialWhyStopped", "url")}
                for r in row.get("clinicalReports") or []
            ]
            nct_ids = sorted({r["id"].upper() for r in reports if _is_nct(r.get("id"))})
            trials = {r["id"].upper(): r for r in reports if _is_nct(r.get("id"))}
            symbols = [self.symbols.get(t, t) for t in target_ids]
            name = drug.get("name") or chembl
            stage = row.get("maxClinicalStage")
            snippet = f"{name} ({'/'.join(symbols)}) in {disease_name}: max stage {stage or 'unknown'}"
            if nct_ids:
                statuses: dict[str, int] = {}
                for r in trials.values():
                    st = r.get("trialOverallStatus") or "UNKNOWN"
                    statuses[st] = statuses.get(st, 0) + 1
                counts = ", ".join(f"{n} {st}" for st, n in sorted(statuses.items(), key=lambda kv: (-kv[1], kv[0])))
                shown = ", ".join(nct_ids[:_SNIPPET_NCT_IDS]) + (" …" if len(nct_ids) > _SNIPPET_NCT_IDS else "")
                snippet += f"; {_plural(len(nct_ids), 'trial')} ({counts}): {shown}"
            others = len(reports) - len(trials)
            if others:
                snippet += f"; {_plural(others, 'other report')}"
            self._add(
                f"drug:{chembl}:{disease_id}",
                url=f"{PLATFORM}/drug/{chembl}",
                modules=["pipeline", "competitive-landscape"],
                entity_refs={"target": target_ids[0], "disease": disease_id, "drug": chembl},
                title=f"{name} – {disease_name} (Open Targets clinical candidates)",
                snippet=snippet,
                data={
                    "chembl_id": chembl,
                    "drug_name": name,
                    "drugType": drug.get("drugType"),
                    "target_ids": target_ids,
                    "target_symbols": symbols,
                    "disease_id": disease_id,
                    "disease_name": disease_name,
                    "maxClinicalStage": stage,
                    "nct_ids": nct_ids,
                    "trials": [
                        {"nct_id": n, "phase": trials[n].get("trialPhase") or trials[n].get("clinicalStage"),
                         "status": trials[n].get("trialOverallStatus"), "why_stopped": trials[n].get("trialWhyStopped")}
                        for n in nct_ids
                    ],
                    "clinicalReports": reports,
                },
            )

    # --- genetic evidence -----------------------------------------------------

    def _genetic(self, tid: str, disease_id: str) -> None:
        data = self._query(_Q_GENETIC, {"d": disease_id, "t": tid, "sources": GENETIC_DATASOURCES, "size": _GENETIC_CAP})
        rows = _rows(((data.get("disease") or {}).get("evidences")))
        symbol = self.symbols.get(tid, tid)
        for row in sorted(rows, key=lambda r: -(r.get("score") or 0))[:_GENETIC_CAP]:
            self._genetic_record(row, tid, symbol, disease_id)

    def _genetic_record(self, row: dict[str, Any], tid: str, symbol: str, disease_id: str) -> None:
        cs = row.get("credibleSet") or {}
        study = cs.get("study") or {}
        variant = cs.get("variant") or {}
        source = row["datasourceId"]
        ev_disease = row.get("disease") or {}
        mantissa = cs.get("pValueMantissa", row.get("pValueMantissa"))
        exponent = cs.get("pValueExponent", row.get("pValueExponent"))
        data = {
            "target_id": tid,
            "target_symbol": symbol,
            "disease_id": disease_id,
            "evidence_disease_id": ev_disease.get("id"),
            "evidence_disease_name": ev_disease.get("name"),
            "datasourceId": source,
            "score": row.get("score"),
            "literature": row.get("literature") or [],
            "pValueMantissa": mantissa,
            "pValueExponent": exponent,
            "beta": cs.get("beta", row.get("beta")),
            "oddsRatio": row.get("oddsRatio"),
        }
        pval = f"p={mantissa:.3g}e{exponent}" if mantissa is not None and exponent is not None else None
        if source == "gwas_credible_sets" and cs:
            data.update({
                "studyLocusId": cs.get("studyLocusId"),
                "studyId": cs.get("studyId"),
                "variant_id": variant.get("id"),
                "rsIds": variant.get("rsIds") or [],
                "finemappingMethod": cs.get("finemappingMethod"),
                "confidence": cs.get("confidence"),
                "traitFromSource": study.get("traitFromSource"),
                "publicationFirstAuthor": study.get("publicationFirstAuthor"),
                "publicationDate": study.get("publicationDate"),
                "pubmedId": study.get("pubmedId"),
                "nSamples": study.get("nSamples"),
            })
            variant_label = (variant.get("rsIds") or [None])[0] or variant.get("id") or "variant"
            bits = [f"L2G {row['score']:.2f}", pval, study.get("traitFromSource"), cs.get("studyId")]
            snippet = f"{symbol} GWAS locus {variant_label}: " + ", ".join(b for b in bits if b)
            url = f"{PLATFORM}/credible-set/{cs['studyLocusId']}"
            key = f"gwas:{cs['studyLocusId']}:{tid}"
            title = f"{symbol} GWAS credible set ({study.get('traitFromSource') or ev_disease.get('name')})"
        else:
            data.update({
                "statisticalMethod": row.get("statisticalMethod"),
                "statisticalMethodOverview": row.get("statisticalMethodOverview"),
                "cohortId": row.get("cohortId"),
                "ancestry": row.get("ancestry"),
                "studySampleSize": row.get("studySampleSize"),
                "diseaseFromSource": row.get("diseaseFromSource"),
                "evidence_id": row["id"],
            })
            effect = f"OR {row['oddsRatio']:g}" if row.get("oddsRatio") is not None else (
                f"beta {row['beta']:g}" if row.get("beta") is not None else None)
            bits = [pval, effect, row.get("statisticalMethod"), row.get("cohortId"), row.get("diseaseFromSource")]
            snippet = f"{symbol} {source.replace('_', ' ')}: " + ", ".join(b for b in bits if b)
            url = f"{PLATFORM}/evidence/{tid}/{disease_id}"
            key = f"genetic:{source}:{row['id']}"
            title = f"{symbol} {source.replace('_', ' ')} ({row.get('diseaseFromSource') or ev_disease.get('name')})"
        self._add(
            key,
            url=url,
            modules=["scientific-evidence"],
            entity_refs={"target": tid, "disease": disease_id},
            title=title,
            snippet=snippet,
            data=data,
        )


def _rows(obj: dict[str, Any] | None) -> list[dict[str, Any]]:
    return (obj or {}).get("rows") or []


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _is_nct(report_id: str | None) -> bool:
    return bool(report_id and re.fullmatch(r"nct\d{8}", report_id, re.IGNORECASE))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out
