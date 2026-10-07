"""researcher Bundle (researcher/schema.py) -> analytics EvidenceBundle (evidence_bundle/models.py).

Thin mapping; nothing is invented. What analytics relies on and where it comes from:

subject.disease / targets / drugs  <- Subject.disease_ids/disease_synonyms, target_ids/target_symbols, drugs
trial data.status / stop_class     <- overall_status / stop_category ("unknown" -> None)
trial data.role                    <- "same_target" when the linked drug is subject_mechanism, else "landscape"
conflicts (rule/severity/kill)     <- reconcile rules, see _RULES. A kill signal ("a same-target program already
                                      failed here", forces Do Not Invest) is raised only by R4 does_not_transfer
                                      and R5 target_failure, and only for subject-mechanism drugs: one stopped
                                      trial, or a failure of another modality on the same target, is a high /
                                      medium severity conflict, not a kill. No kill either when a subject-mechanism
                                      drug is already approved in THIS indication (Open Targets disease-specific
                                      maxClinicalStage APPROVAL): the mechanism is validated here, so one drug's
                                      failure is a drug-level risk (high severity, data.kill_suppressed says why).
trial_benchmarks                   <- medians over the bundle's own phase 2 / 3 trials (also one evidence item)
gaps                               <- coverage items with status not_public
source_status                      <- sources_status
"""
from __future__ import annotations

import statistics
from datetime import date
from typing import Any

from evidence_bundle import EvidenceBundle
from researcher.schema import Bundle, Drug, Evidence, evidence_id

STOP_CLASSES = {"safety", "efficacy", "business", "enrollment", "other"}
_ACTION = {"inhibition": "inhibitor", "activation": "agonist", "degradation": "degrader", "modulation": "modulator"}
# reconcile rule -> (analytics rule name, kind)
_RULES = {
    "stop_reason": "stopped_for_cause",
    "does_not_transfer": "mechanism_not_transferring",
    "target_failure": "target_failure",
    "status_mismatch": "status_mismatch",
}
BENCHMARK_PHASES = ("PHASE2", "PHASE3")
_BENCHMARK_STATUSES = ("COMPLETED", "TERMINATED")


def to_evidence_bundle(b: Bundle) -> EvidenceBundle:
    s = b.subject
    symbols = dict(zip(s.target_ids, s.target_symbols))
    drugs = {d.chembl_id: d for d in s.drugs if d.chembl_id}
    approved_here = [drugs[e.entity_refs["drug"]].name for e in b.evidence
                     if e.source == "opentargets" and e.data.get("maxClinicalStage") == "APPROVAL"
                     and drugs.get(e.entity_refs.get("drug", "")) and drugs[e.entity_refs["drug"]].relation == "subject_mechanism"]
    evidence = [_evidence(e, drugs, approved_here) for e in b.evidence]
    benchmarks, bench_ev = _benchmarks(b)
    if bench_ev:
        evidence.append(bench_ev)

    cutoff_policy = []
    if b.input.evidence_cutoff:
        st = b.reconcile_stats
        cutoff_policy.append(f"evidence published after {b.input.evidence_cutoff} was dropped"
                             + (f" ({st.dropped_by_cutoff} items; {st.dropped_by_leak_guard} undated items stating "
                                "clinical status dropped by the leak guard)" if st else ""))
        cutoff_policy.append("drug max_stage is current and undated, so it is hidden")

    return EvidenceBundle.model_validate({
        "run_id": b.run_id,
        "created_at": b.created_at,
        "is_fixture": b.is_example,
        "input": b.input.model_dump(mode="json"),
        "subject": {
            "disease": {"id": (s.disease_ids or [""])[0], "name": (s.disease_synonyms or [s.indication])[0],
                        "synonyms": s.disease_synonyms},
            "targets": [{"ensembl_id": t, "symbol": symbols[t], "role": "primary"} for t in s.target_ids],
            "action": _ACTION.get(b.plan.action or "") if b.plan else None,
            "drugs": [_drug(d, symbols, hide_stage=b.input.evidence_cutoff is not None) for d in s.drugs],
            "notes": s.resolution_notes,
        },
        "evidence": [e.model_dump(mode="json") for e in evidence],
        "trial_benchmarks": benchmarks,
        "gaps": [{"question": c.question, "requires": c.requires[0], "reason": c.note or ""}
                 for c in b.coverage if c.status == "not_public" and c.requires],
        "cutoff_policy": cutoff_policy,
        "source_status": [{"source": x.source, "ok": x.ok, "evidence_count": x.records,
                           "errors": [x.error] if x.error else []} for x in b.sources_status],
    })


def _drug(d: Drug, symbols: dict[str, str], hide_stage: bool) -> dict[str, Any]:
    return {
        "chembl_id": d.chembl_id, "name": d.name, "synonyms": d.synonyms,
        "target_symbols": [symbols[t] for t in d.target_ids if t in symbols] + d.other_target_symbols,
        "match": "target",
        "max_stage": None if hide_stage or d.max_phase is None else
        ("APPROVAL" if d.max_phase >= 4 else f"PHASE_{d.max_phase:g}"),
        "relation": d.relation, "drug_type": d.drug_type,
    }


def _evidence(e: Evidence, drugs: dict[str, Drug], approved_here: list[str]) -> Evidence:
    data, kind, refs = dict(e.data), e.kind, dict(e.entity_refs)
    if e.source == "clinicaltrials":
        data["status"] = data.get("overall_status")
        data["stop_class"] = data.get("stop_category") if data.get("stop_category") in STOP_CLASSES else None
        data["role"] = "same_target" if data.get("drug_relation") == "subject_mechanism" else "landscape"
        if data.get("nct_id"):
            refs["nct_id"] = data["nct_id"]
    elif e.source == "reconcile" and data.get("rule") in _RULES:
        kind, data = "conflict", {**data, **_conflict(data, drugs, approved_here)}
    elif e.source == "reconcile":
        data.setdefault("severity", "info")
    return e.model_copy(update={"kind": kind, "data": data, "entity_refs": refs})


def _conflict(data: dict[str, Any], drugs: dict[str, Drug], approved_here: list[str]) -> dict[str, Any]:
    rule = data["rule"]
    if rule == "target_failure":
        ids = [d["chembl_id"] for d in data.get("drugs") or []]
    elif rule == "does_not_transfer":
        ids = [data.get("chembl_id")]
    else:
        ids = data.get("drug_chembl_ids") or []
    known = [drugs[i] for i in ids if i in drugs]
    own = [d for d in known if d.relation == "subject_mechanism"]
    if rule == "status_mismatch":
        severity, kill = "low", False
    elif rule == "target_failure":
        kill = len(own) >= 2
        severity = "high" if kill else "medium"
    elif rule == "does_not_transfer":
        kill = bool(own)
        severity = "high" if kill else "medium"
    else:  # stopped_for_cause: one stopped trial is never a kill on its own
        severity, kill = ("high" if own else "medium"), False
    out = {"rule": _RULES[rule], "reconcile_rule": rule, "severity": severity, "kill_signal": kill,
           "drug": ", ".join(d.name for d in (own or known)) or None}
    if kill and approved_here:
        out["kill_signal"] = False
        out["kill_suppressed"] = f"subject-mechanism drug(s) approved in this indication: {', '.join(approved_here)}"
    return out


def _benchmarks(b: Bundle) -> tuple[dict[str, Any], Evidence | None]:
    """Enrollment / duration medians and termination rate of the bundle's single-phase 2 and 3 trials."""
    by_phase: dict[str, dict[str, Any]] = {}
    used: list[Evidence] = []
    for phase in BENCHMARK_PHASES:
        trials = [e for e in b.evidence if e.source == "clinicaltrials" and e.data.get("phases") == [phase]
                  and e.data.get("overall_status") in _BENCHMARK_STATUSES]
        sized = [e for e in trials if e.data.get("enrollment_type") == "ACTUAL" and (e.data.get("enrollment") or 0) > 0]
        months = [m for e in trials if (m := _months(e.data.get("start_date"), e.data.get("completion_date")))]
        if not sized and not months:
            continue
        used += trials
        by_phase[phase] = {
            "n": len(trials),
            "enrollment_median": int(statistics.median(e.data["enrollment"] for e in sized)) if sized else None,
            "duration_months_median": int(statistics.median(months)) if months else None,
            "termination_rate": round(sum(e.data["overall_status"] == "TERMINATED" for e in trials) / len(trials), 2),
        }
    if not by_phase:
        return {}, None
    benchmarks = {"benchmark": True, "synthetic": False, "source": "clinicaltrials (this bundle)", "by_phase": by_phase}
    snippet = "; ".join(
        f"{p.replace('PHASE', 'Phase ')}: n={v['n']}, median enrollment {v['enrollment_median']}, "
        f"median duration {v['duration_months_median']} months, terminated {v['termination_rate']:.0%}"
        for p, v in by_phase.items())
    dates = [e.published_at for e in used if e.published_at]
    ev = Evidence(
        id=evidence_id("reconcile", "trial_benchmarks"), source="reconcile",
        url="https://clinicaltrials.gov/search?cond=" + b.subject.indication.replace(" ", "+"),
        retrieved_at=b.created_at, kind="record", modules=["trial-design"],
        title=f"Phase 2/3 trial benchmarks in {b.subject.indication}", snippet=snippet,
        data=benchmarks, related_evidence_ids=sorted(e.id for e in used), published_at=max(dates) if dates else None,
    )
    return benchmarks, ev


def _months(start: str | None, end: str | None) -> int | None:
    a, z = _date(start), _date(end)
    if not a or not z or z <= a:
        return None
    return (z.year - a.year) * 12 + z.month - a.month


def _date(v: str | None) -> date | None:
    try:
        parts = [int(x) for x in (v or "").split("-")]
        return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except (ValueError, IndexError):
        return None
