"""Adapter: researcher output (`researcher.schema.Bundle`) -> analytics contract (`EvidenceBundle`).

The researcher module defines its own bundle schema (researcher/schema.py). Analytics reads
`EvidenceBundle` (models.py). This adapter is the only place where the two meet, so neither
side has to change when the other evolves: remap fields here.

What is mapped
* subject: disease_ids/target_ids/target_symbols/drugs -> DiseaseRef / TargetRef / DrugRef.
* evidence: copied as is; `data` gets the analytics conventions added next to the researcher keys:
    ClinicalTrials.gov  status, stop_class, role (same_target | landscape)
    Open Targets        has_genetic_evidence (genetics / association rows)
    reconcile rules     severity, kill_signal, drug (see KILL RULES below)
* coverage: `not_public` items -> gaps (what public data cannot answer).
* trial_benchmarks: medians of the ClinicalTrials.gov trials in the bundle, per phase.

KILL RULES (a kill signal forces Do Not Invest in analytics, so it must be rare and well-founded).
Aligned with the teammate's v1.0.0 orchestrator/bundle_adapter.py. "Own" drug = a subject drug whose
relation is "subject_mechanism" (same target AND compatible modality).
    stop_reason       one efficacy/safety stop              -> conflict, high if own drug else medium, NO kill
    target_failure    2+ drugs on the target failed here    -> conflict; kill only if 2+ are own drugs
    does_not_transfer approved elsewhere, failed here        -> conflict; kill only for an own drug
    status_mismatch   registries disagree                    -> conflict, low
    stale_active      active trial not updated 24+ months    -> record, info
No kill at all when an own drug is already APPROVED in this indication (Open Targets disease-specific
maxClinicalStage): the mechanism is validated here, one drug's failure is a drug-level risk
(data.kill_suppressed says why).
"""
from __future__ import annotations

import statistics
from datetime import date
from typing import Any

from .models import EvidenceBundle

FAILURE = {"efficacy", "safety"}
STOPPED = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
MIN_BENCHMARK_TRIALS = 5


def is_researcher_bundle(raw: dict[str, Any]) -> bool:
    """researcher Bundle has subject.disease_ids; the analytics contract has subject.disease."""
    subject = raw.get("subject") or {}
    return "disease_ids" in subject or "coverage" in raw or "sources_status" in raw


def from_researcher(raw: dict[str, Any]) -> EvidenceBundle:
    subject = raw.get("subject") or {}
    inp = dict(raw.get("input") or {})
    drugs_raw: list[dict[str, Any]] = subject.get("drugs") or []
    symbols = dict(zip(subject.get("target_ids") or [], subject.get("target_symbols") or []))
    drug_names = {str(d["chembl_id"]): _drug_name(d.get("name", "")) for d in drugs_raw if d.get("chembl_id")}
    own = {str(d["chembl_id"]) for d in drugs_raw
           if d.get("chembl_id") and d.get("relation", "subject_mechanism") == "subject_mechanism"}
    approved_here = sorted({drug_names[ref] for e in raw.get("evidence") or []
                            if e.get("source") == "opentargets"
                            and (e.get("data") or {}).get("maxClinicalStage") == "APPROVAL"
                            and (ref := (e.get("entity_refs") or {}).get("drug")) in own})
    cutoff = inp.get("evidence_cutoff")

    evidence = [_evidence(e, drug_names, own, approved_here) for e in raw.get("evidence") or []]
    out = {
        "run_id": raw["run_id"],
        "created_at": raw["created_at"],
        "is_fixture": bool(raw.get("is_example", False)),
        "input": inp,
        "subject": {
            "disease": {
                "id": (subject.get("disease_ids") or ["unknown"])[0],
                "name": (subject.get("disease_synonyms") or [subject.get("indication") or inp.get("indication", "")])[0],
                "synonyms": subject.get("disease_synonyms") or [],
            },
            "targets": [
                {"ensembl_id": tid, "symbol": sym, "role": "primary" if i == 0 else "related"}
                for i, (tid, sym) in enumerate(symbols.items())
            ],
            "action": (raw.get("plan") or {}).get("action"),
            "drugs": [_drug(d, symbols, hide_stage=cutoff is not None) for d in drugs_raw],
            "notes": list(subject.get("resolution_notes") or []),
        },
        "evidence": evidence,
        "trial_benchmarks": _benchmarks(evidence),
        "gaps": [_gap(c) for c in raw.get("coverage") or [] if c.get("status") == "not_public"],
        "cutoff_policy": _cutoff_policy(raw),
        "source_status": [
            {"source": s.get("source", "?"), "ok": bool(s.get("ok")), "evidence_count": s.get("records", 0),
             "errors": [s["error"]] if s.get("error") else []}
            for s in raw.get("sources_status") or []
        ],
        # kept for the report / traces (extra fields are allowed by the contract)
        "researcher": {
            "llm_calls": raw.get("llm_calls") or [],
            "sources_status": raw.get("sources_status") or [],
            "reconcile_stats": raw.get("reconcile_stats"),
            "coverage": raw.get("coverage") or [],
        },
    }
    return EvidenceBundle.model_validate(out)


# ----------------------------------------------------------------------------- pieces


def _drug_name(name: str) -> str:
    """Researcher stores names lowercase/uppercase INN; analytics shows them capitalised."""
    return name[:1].upper() + name[1:].lower() if name else name


def _drug(d: dict[str, Any], symbols: dict[str, str], hide_stage: bool) -> dict[str, Any]:
    target_symbols = [symbols[t] for t in d.get("target_ids") or [] if t in symbols]
    phase = d.get("max_phase")
    return {
        "chembl_id": d.get("chembl_id"),
        "name": _drug_name(d.get("name", "")),
        "synonyms": d.get("synonyms") or [],
        "target_symbols": target_symbols + list(d.get("other_target_symbols") or []),
        "match": "target" if d.get("relation", "subject_mechanism") == "subject_mechanism" else "family",
        # max_phase is current and undated: hide it under an evidence cutoff (it would leak the future)
        "max_stage": None if hide_stage or phase is None else ("APPROVAL" if phase >= 4 else f"PHASE{phase:g}"),
        "drug_type": d.get("drug_type"),
        "relation": d.get("relation"),
    }


def _get(data: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if data.get(k) not in (None, ""):
            return data[k]
    return None


def _evidence(e: dict[str, Any], drug_names: dict[str, str], own: set[str] | None = None,
              approved_here: list[str] | None = None) -> dict[str, Any]:
    e = dict(e)
    data = dict(e.get("data") or {})
    refs = e.get("entity_refs") or {}
    source = e.get("source")

    if source == "clinicaltrials":
        status = _get(data, "overall_status", "overallStatus")
        if status == "RECRUITING" and data.get("as_of_cutoff"):
            status = "ONGOING_AT_CUTOFF"  # researcher rebuilt the record as of the cutoff: the outcome is hidden
        if status:
            data.setdefault("status", status)
        why = _get(data, "why_stopped", "whyStopped")
        if why:
            data.setdefault("why_stopped", why)
        category = data.get("stop_category")
        if category and category != "unknown":
            data.setdefault("stop_class", "other" if category == "positive_early_stop" else category)
        drug = refs.get("drug") or refs.get("chembl_id") or (data.get("subject_drug_chembl_ids") or [None])[0]
        data.setdefault("role", "same_target" if drug in drug_names else "landscape")
        if drug in drug_names:
            data.setdefault("drug", drug_names[drug])
        if isinstance(data.get("enrollment"), dict):  # hand-made example uses {"count": n}
            data["enrollment"] = data["enrollment"].get("count")

    elif source == "opentargets":
        if data.get("datasourceId") in ("gwas_credible_sets", "gene_burden") or "datatypeScores" in data:
            data.setdefault("has_genetic_evidence", data.get("datasourceId") is not None)

    elif source == "reconcile":
        data.update(_reconcile_rule(data, drug_names, own if own is not None else set(drug_names), approved_here or []))
        if data.get("rule") in ("stop_reason", "target_failure", "does_not_transfer", "status_mismatch"):
            e["kind"] = "conflict"

    elif e.get("kind") == "conflict":
        data.setdefault("severity", "medium")

    e["data"] = data
    return e


def _reconcile_rule(data: dict[str, Any], drug_names: dict[str, str], own: set[str],
                    approved_here: list[str]) -> dict[str, Any]:
    """severity / kill_signal / drug for a researcher reconcile rule (see KILL RULES in the module docstring)."""
    rule = data.get("rule")
    if rule == "target_failure":
        ids = [d.get("chembl_id") for d in data.get("drugs") or []]
    elif rule == "does_not_transfer":
        ids = [data.get("chembl_id")]
    else:
        ids = list(data.get("drug_chembl_ids") or [])
    known = [i for i in ids if i in drug_names]
    mine = [i for i in known if i in own]
    names = ", ".join(drug_names[i] for i in (mine or known)) or None

    if rule == "stop_reason":
        if data.get("stop_category") not in FAILURE:
            return {"severity": "low", "kill_signal": False, "drug": names}
        return {"severity": "high" if mine else "medium", "kill_signal": False, "drug": names}
    if rule == "target_failure":
        out = {"severity": "high" if len(mine) >= 2 else "medium", "kill_signal": len(mine) >= 2, "drug": names}
    elif rule == "does_not_transfer":
        out = {"severity": "high" if mine else "medium", "kill_signal": bool(mine), "drug": names}
    elif rule == "status_mismatch":
        return {"severity": "low", "kill_signal": False, "drug": names}
    elif rule == "stale_active":
        return {"severity": "info", "kill_signal": False, "drug": names}
    else:
        return {"severity": data.get("severity") or "medium", "kill_signal": False, "drug": names}
    if out["kill_signal"] and approved_here:
        out["kill_signal"] = False
        out["kill_suppressed"] = f"subject-mechanism drug(s) approved in this indication: {', '.join(approved_here)}"
    return out


def _months(a: str | None, b: str | None) -> int | None:
    try:
        d1, d2 = date.fromisoformat(_full_date(a)), date.fromisoformat(_full_date(b))
    except (TypeError, ValueError):
        return None
    m = (d2.year - d1.year) * 12 + d2.month - d1.month
    return m if m > 0 else None


def _full_date(s: str | None) -> str:
    s = s or ""
    return s if len(s) == 10 else (s + "-01" if len(s) == 7 else s)


def _benchmarks(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Median enrollment / duration / termination rate per phase from the trials in the bundle."""
    by_phase: dict[str, dict[str, list[Any]]] = {}
    for e in evidence:
        if e.get("source") != "clinicaltrials":
            continue
        d = e["data"]
        for phase in d.get("phases") or []:
            if phase not in ("PHASE2", "PHASE3"):
                continue
            b = by_phase.setdefault(phase, {"enrollment": [], "months": [], "stopped": []})
            if isinstance(d.get("enrollment"), (int, float)) and d["enrollment"] > 0:
                b["enrollment"].append(d["enrollment"])
            m = _months(_get(d, "start_date", "startDate"), _get(d, "completion_date", "completionDate"))
            if m:
                b["months"].append(m)
            if d.get("status"):
                b["stopped"].append(d["status"] in STOPPED)
    out: dict[str, Any] = {}
    for phase, b in by_phase.items():
        if max(len(b["enrollment"]), len(b["months"])) < MIN_BENCHMARK_TRIALS:
            continue  # too few trials for a base rate; finance falls back to its defaults
        out[phase] = {
            "n": max(len(b["enrollment"]), len(b["months"])),
            "enrollment_median": int(statistics.median(b["enrollment"])) if b["enrollment"] else None,
            "duration_months_median": int(statistics.median(b["months"])) if b["months"] else None,
            "termination_rate": round(sum(b["stopped"]) / len(b["stopped"]), 2) if b["stopped"] else None,
        }
    return {"benchmark": True, "source": "trials in this bundle (ClinicalTrials.gov)", "by_phase": out} if out else {}


def _gap(c: dict[str, Any]) -> dict[str, Any]:
    requires = (c.get("requires") or ["experiment"])[0]
    return {"question": c.get("question", ""), "requires": requires,
            "reason": c.get("note") or "Not answerable from public data."}


def _cutoff_policy(raw: dict[str, Any]) -> list[str]:
    cutoff = (raw.get("input") or {}).get("evidence_cutoff")
    stats = raw.get("reconcile_stats") or {}
    if not cutoff:
        return []
    return [
        f"evidence cutoff {cutoff}: {stats.get('dropped_by_cutoff', 0)} item(s) published later were dropped",
        f"leak guard: {stats.get('dropped_by_leak_guard', 0)} undated item(s) revealing later status were dropped",
        *[f"rule skipped: {k} — {v}" for k, v in (stats.get("rules_skipped") or {}).items()],
    ]
