"""Coverage: mandatory underwriting questions, evaluated deterministically against the bundle (no LLM).

The checklist is plain data (CHECKLIST below), so it can be read and edited without touching the logic.
To add a question: append a ChecklistItem. Public questions need a `check`; non-public ones need `requires`
(values as in the frontend's DiligenceRequirement) and get status "not_public".

Status semantics (see schema.CoverageStatus):
- covered:  the bundle can answer the question. The answer goes in `note`, and it can be a "no"
            (e.g. no genetic signal, R5 did not fire). Coverage is about being answerable, not favourable.
- missing:  publicly answerable, but this bundle cannot answer it (source not implemented, no data,
            or the data is unusable under evidence_cutoff).
- not_public: needs data no public source has.

`answer` (schema.CoverageAnswer) is the answer itself, separate from status: "yes" / "no" / "unknown", null
for not_public. A check sets it explicitly; otherwise covered -> "yes" and missing -> "unknown".

Trial counts are reported separately per drug relation (Drug.relation, copied to data.drug_relation):
subject_mechanism vs same_target_other_modality vs trials linked to no subject drug.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from researcher.schema import Bundle, CoverageAnswer, CoverageItem, DiligenceRequirement, Evidence, Module

# Competitive landscape: minimum active phase 2-3 trials in the indication.
MIN_ACTIVE_TRIALS = 5
STOPPED = ("TERMINATED", "WITHDRAWN", "SUSPENDED")
ACTIVE = ("RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION")


@dataclass
class CheckResult:
    covered: bool
    evidence_ids: list[str] = field(default_factory=list)
    note: str | None = None
    answer: CoverageAnswer | None = None


Check = Callable[[Bundle], CheckResult]


@dataclass
class ChecklistItem:
    id: str
    question: str
    module: Module
    public_answerable: bool
    check: Check | None = None
    requires: list[DiligenceRequirement] = field(default_factory=list)


# --- checks ------------------------------------------------------------------------------


def _by_source(bundle: Bundle, source: str) -> list[Evidence]:
    return [e for e in bundle.evidence if e.source == source]


def _drug_ids(bundle: Bundle) -> set[str]:
    return {d.chembl_id for d in bundle.subject.drugs if d.chembl_id}


SUBJECT = "subject_mechanism"
OTHER_MODALITY = "same_target_other_modality"
_RELATION_LABELS = {SUBJECT: "subject mechanism", OTHER_MODALITY: "same target, other modality", None: "no subject drug"}


def _relation(e: Evidence) -> str | None:
    return e.data.get("drug_relation") if e.entity_refs.get("drug") else None


def _by_relation(trials: list[Evidence]) -> str:
    """'3 subject mechanism, 5 same target, other modality, 2 no subject drug' (zero counts kept for the first two)."""
    counts = {k: sum(_relation(e) == k for e in trials) for k in _RELATION_LABELS}
    return ", ".join(f"{n} {_RELATION_LABELS[k]}" for k, n in counts.items() if n or k is not None)


def check_standard_of_care(bundle: Bundle) -> CheckResult:
    ev = _by_source(bundle, "openfda")
    if not ev:
        return CheckResult(False, note="no openFDA evidence (openFDA connector not implemented yet)")
    return CheckResult(True, [e.id for e in ev], f"{len(ev)} openFDA records")


def check_genetic_support(bundle: Bundle) -> CheckResult:
    """Answerable when Open Targets association scores exist for a subject target; the answer is whether
    any genetic datatype (genetic_*, somatic_mutation) scores > 0, or genetic evidence records exist."""
    assoc = [e for e in _by_source(bundle, "opentargets") if "datatypeScores" in e.data]
    if not assoc:
        return CheckResult(False, note="no Open Targets association for the subject targets and disease")
    genetic = {
        e.id: {k: v for k, v in e.data["datatypeScores"].items() if (k.startswith("genetic") or k == "somatic_mutation") and v > 0}
        for e in assoc
    }
    records = [e for e in _by_source(bundle, "opentargets") if e.data.get("datasourceId") in ("gwas_credible_sets", "gene_burden")]
    positive = [e for e in assoc if genetic[e.id]]
    if positive or records:
        parts = [f"{e.data['target_symbol']} " + ", ".join(f"{k} {v:.2f}" for k, v in genetic[e.id].items()) for e in positive]
        if records:
            parts.append(f"{len(records)} genetic evidence records")
        return CheckResult(True, [e.id for e in positive + records], "yes: " + "; ".join(parts))
    symbols = "/".join(e.data["target_symbol"] for e in assoc)
    return CheckResult(True, [e.id for e in assoc], f"no: no genetic datatype score > 0 for {symbols} (association scores present)",
                       answer="no")


def check_target_safety(bundle: Bundle) -> CheckResult:
    ev = [e for e in _by_source(bundle, "opentargets") if e.data.get("safetyLiabilities")]
    if not ev:
        return CheckResult(False, note="Open Targets lists no safety liabilities for the subject targets")
    return CheckResult(True, [e.id for e in ev], "; ".join(e.snippet for e in ev))


def check_mechanism_validated(bundle: Bundle) -> CheckResult:
    if bundle.input.evidence_cutoff is not None:
        return CheckResult(False, note="max_phase is current and undated; not usable under evidence_cutoff")
    approved = [d for d in bundle.subject.drugs if (d.max_phase or 0) >= 4]
    if not bundle.subject.drugs:
        return CheckResult(False, note="no subject drugs resolved")
    own = [d for d in approved if d.relation == SUBJECT]
    other = [d for d in approved if d.relation != SUBJECT]
    other_note = f"; approved with the same target but another modality: {', '.join(d.name for d in other)}" if other else ""
    if not own:
        return CheckResult(True, note=f"no: no subject-mechanism drug is approved (max_phase 4){other_note}", answer="no")
    ids = {d.chembl_id for d in own}
    linked = [e.id for e in bundle.evidence if e.entity_refs.get("drug") in ids and e.source == "opentargets"]
    return CheckResult(True, linked, "yes: subject-mechanism drugs approved (max_phase 4): "
                       + ", ".join(d.name for d in own) + other_note, answer="yes")


def check_trials_found(bundle: Bundle) -> CheckResult:
    drugs = _drug_ids(bundle)
    ev = [e for e in _by_source(bundle, "clinicaltrials") if e.entity_refs.get("drug") in drugs]
    if not ev:
        return CheckResult(False, note="no ClinicalTrials.gov trial linked to a subject drug in this indication")
    names = {d.chembl_id: d.name for d in bundle.subject.drugs}
    parts = []
    for relation in (SUBJECT, OTHER_MODALITY):
        per_drug: dict[str, int] = {}
        for e in ev:
            if _relation(e) == relation:
                per_drug[names[e.entity_refs["drug"]]] = per_drug.get(names[e.entity_refs["drug"]], 0) + 1
        listed = f" ({', '.join(f'{k} {v}' for k, v in per_drug.items())})" if per_drug else ""
        parts.append(f"{sum(per_drug.values())} trials {_RELATION_LABELS[relation]}{listed}")
    own = sum(_relation(e) == SUBJECT for e in ev)
    prefix = "yes: " if own else "no subject-mechanism trials: "
    return CheckResult(True, [e.id for e in ev], prefix + "; ".join(parts), answer="yes" if own else "no")


def check_stopped_explained(bundle: Bundle) -> CheckResult:
    stopped = [e for e in _by_source(bundle, "clinicaltrials") if e.data.get("overall_status") in STOPPED]
    if not stopped:
        trials = _by_source(bundle, "clinicaltrials")
        if not trials:
            return CheckResult(False, note="no ClinicalTrials.gov evidence to check")
        return CheckResult(True, note="no stopped trials among the trials found")
    unexplained = [e for e in stopped if not e.data.get("why_stopped") or not e.data.get("stop_category")]
    ids = [e.id for e in stopped]
    cats: dict[str, int] = {}
    for e in stopped:
        key = e.data.get("stop_category") or "unclassified"
        cats[key] = cats.get(key, 0) + 1
    summary = (f"{len(stopped)} stopped trials ({_by_relation(stopped)}): "
               + ", ".join(f"{k} {v}" for k, v in cats.items()))
    if unexplained:
        with_results = [e.data["nct_id"] for e in unexplained if e.data.get("has_results")]
        results = f"; results posted — check outcome: {', '.join(with_results)}" if with_results else ""
        return CheckResult(False, ids, f"no: {len(unexplained)} of {len(stopped)} stopped trials have no stated reason: "
                           + ", ".join(e.data["nct_id"] for e in unexplained) + results + f". {summary}", answer="no")
    return CheckResult(True, ids, summary, answer="yes")


def check_target_failure(bundle: Bundle) -> CheckResult:
    stats = bundle.reconcile_stats
    if stats is None:
        return CheckResult(False, note="reconcile has not run")
    if "target_failure" in stats.rules_skipped:
        return CheckResult(False, note=f"R5 skipped: {stats.rules_skipped['target_failure']}")
    fired = [e for e in bundle.evidence if e.source == "reconcile" and e.data.get("rule") == "target_failure"]
    if fired:
        return CheckResult(True, [e.id for e in fired], "R5 fired: " + "; ".join(e.title for e in fired), answer="yes")
    trials = [e for e in bundle.evidence if e.source == "clinicaltrials" or e.data.get("trials")]
    if not trials:
        return CheckResult(False, note="R5 ran on no trial data (none in the bundle); a 'no failure' answer would be empty")
    return CheckResult(True, note="R5 evaluated: fewer than 2 subject drugs with efficacy/safety stops", answer="no")


def check_competitive_landscape(bundle: Bundle) -> CheckResult:
    ev = [
        e for e in _by_source(bundle, "clinicaltrials")
        if e.data.get("overall_status") in ACTIVE and {"PHASE2", "PHASE3"} & set(e.data.get("phases") or [])
    ]
    note = f"{len(ev)} active phase 2-3 trials (need {MIN_ACTIVE_TRIALS}): {_by_relation(ev)}"
    covered = len(ev) >= MIN_ACTIVE_TRIALS
    answer = "yes" if covered else "no" if _by_source(bundle, "clinicaltrials") else "unknown"
    return CheckResult(covered, [e.id for e in ev], note, answer=answer)


def check_disease_burden(bundle: Bundle) -> CheckResult:
    ev = [e for e in bundle.evidence if e.kind in ("literature", "web")]
    if not ev:
        return CheckResult(False, note="no literature/web evidence (PubMed and web connectors not implemented yet)")
    return CheckResult(True, [e.id for e in ev], f"{len(ev)} literature/web records")


def check_pricing_analogues(bundle: Bundle) -> CheckResult:
    return CheckResult(False, note="no pricing source implemented yet")


# --- the checklist -----------------------------------------------------------------------

CHECKLIST: list[ChecklistItem] = [
    ChecklistItem("standard_of_care", "What is the current standard of care (approved drugs for the indication)?",
                  "unmet-need", True, check_standard_of_care),
    ChecklistItem("genetic_support", "Is there human genetic support linking a subject target to the disease?",
                  "scientific-evidence", True, check_genetic_support),
    ChecklistItem("target_safety", "Are target-level safety liabilities known?",
                  "red-flags", True, check_target_safety),
    ChecklistItem("mechanism_validated_in_humans", "Is the mechanism validated in humans elsewhere (an approved drug)?",
                  "scientific-evidence", True, check_mechanism_validated),
    ChecklistItem("trials_found", "Have subject drugs been tested in this indication?",
                  "pipeline", True, check_trials_found),
    ChecklistItem("stopped_trials_explained", "Is every stopped trial explained (stated and categorised reason)?",
                  "red-flags", True, check_stopped_explained),
    ChecklistItem("target_failure_checked", "Has the target/mechanism failed with multiple drugs in this indication?",
                  "red-flags", True, check_target_failure),
    ChecklistItem("competitive_landscape", "What is the competitive landscape (active phase 2-3 trials in the indication)?",
                  "competitive-landscape", True, check_competitive_landscape),
    ChecklistItem("disease_burden", "What is the disease burden / epidemiology?",
                  "patient-population", True, check_disease_burden),
    ChecklistItem("pricing_analogues", "What are the pricing analogues?",
                  "market-sentiment", True, check_pricing_analogues),
    ChecklistItem("human_exposure", "Can efficacious human exposure be achieved safely?",
                  "trial-design", False, requires=["experiment", "proprietary_data"]),
    ChecklistItem("target_engagement_tissue", "Is target engagement demonstrated in diseased tissue?",
                  "scientific-evidence", False, requires=["experiment"]),
    ChecklistItem("cmc_manufacturability", "Is the asset manufacturable at scale (CMC)?",
                  "manufacturing", False, requires=["cmc_ip"]),
    ChecklistItem("ip_freedom_to_operate", "Is the IP position defensible, with freedom to operate?",
                  "patent-ip", False, requires=["cmc_ip"]),
    ChecklistItem("response_predictors", "Are there patient-level predictors of response?",
                  "patient-population", False, requires=["patient_data"]),
]


def evaluate_coverage(bundle: Bundle, checklist: list[ChecklistItem] | None = None) -> list[CoverageItem]:
    """Evaluate every checklist item. A failing check is reported as missing, never raised."""
    out: list[CoverageItem] = []
    for item in checklist or CHECKLIST:
        base = dict(id=item.id, question=item.question, module=item.module,
                    public_answerable=item.public_answerable, requires=list(item.requires))
        if not item.public_answerable or item.check is None:
            out.append(CoverageItem(**base, status="not_public", answer=None, note="requires " + ", ".join(item.requires)))
            continue
        try:
            r = item.check(bundle)
        except Exception as e:  # a broken check must not crash the run
            r = CheckResult(False, note=f"check failed: {type(e).__name__}: {e}"[:300])
        answer = r.answer or ("yes" if r.covered else "unknown")
        out.append(CoverageItem(**base, status="covered" if r.covered else "missing", answer=answer,
                                evidence_ids=list(dict.fromkeys(r.evidence_ids)), note=r.note))
    return out
