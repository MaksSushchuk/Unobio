from datetime import date
from typing import get_args

import pytest

from researcher.schema import DiligenceRequirement, Module, ResearchInput, SearchPlan
from researcher.coverage_checklist import CHECKLIST, CheckResult, ChecklistItem, evaluate_coverage
from researcher.pipeline import research_with_plan
from tests.test_reconcile import _bundle

IL17_PLAN = SearchPlan(
    target_symbols=["IL17A", "IL17F"], action="inhibition", modality="antibody",
    disease_terms=["Crohn's disease", "Crohn disease", "regional enteritis"],
    drug_names=["secukinumab", "brodalumab"],
)
NON_PUBLIC = {"human_exposure", "target_engagement_tissue", "cmc_manufacturability", "ip_freedom_to_operate",
              "response_predictors"}


def test_checklist_is_well_formed():
    ids = [i.id for i in CHECKLIST]
    assert len(ids) == len(set(ids))
    for item in CHECKLIST:
        assert item.module in get_args(Module)
        if item.public_answerable:
            assert item.check is not None and not item.requires, item.id
        else:
            assert item.check is None and item.requires, item.id
            assert set(item.requires) <= set(get_args(DiligenceRequirement))
    assert {i.id for i in CHECKLIST if not i.public_answerable} == NON_PUBLIC


def test_empty_bundle_and_broken_check():
    broken = ChecklistItem("broken", "?", "red-flags", True, check=lambda b: 1 / 0)
    ok = ChecklistItem("ok", "?", "red-flags", True, check=lambda b: CheckResult(True, ["a", "a"], "fine"))
    out = {c.id: c for c in evaluate_coverage(_bundle([]), [*CHECKLIST, broken, ok])}
    assert out["broken"].status == "missing" and "ZeroDivisionError" in out["broken"].note
    assert out["ok"].evidence_ids == ["a"]
    assert out["trials_found"].status == "missing"
    assert out["target_failure_checked"].status == "missing"  # reconcile has not run
    assert all(out[i].status == "not_public" and out[i].requires and out[i].answer is None for i in NON_PUBLIC)
    assert out["trials_found"].answer == "unknown" and out["ok"].answer == "yes"


def _run(cutoff=None):
    b = research_with_plan(ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition",
                                         evidence_cutoff=cutoff), IL17_PLAN)
    return b, {c.id: c for c in b.coverage}


@pytest.mark.live
def test_coverage_il17_crohns():
    b, cov = _run()
    assert [c.id for c in b.coverage] == [i.id for i in CHECKLIST]
    ids = {e.id for e in b.evidence}
    assert all(set(c.evidence_ids) <= ids for c in b.coverage)

    for item in ("genetic_support", "trials_found", "target_failure_checked", "mechanism_validated_in_humans",
                 "competitive_landscape"):
        assert cov[item].status == "covered", (item, cov[item].note)
    assert cov["genetic_support"].note.startswith("no:")  # answered: no genetic datatype signal for IL-17
    assert "R5 fired" in cov["target_failure_checked"].note and cov["target_failure_checked"].evidence_ids
    assert len(cov["trials_found"].evidence_ids) >= 5

    # NCT01009281 was terminated without a stated reason in ClinicalTrials.gov or Open Targets, so the
    # question cannot be fully answered from public data.
    assert cov["stopped_trials_explained"].status == "missing"
    assert cov["stopped_trials_explained"].answer == "no"  # missing, but the answer is known: not all explained
    assert "NCT01009281" in cov["stopped_trials_explained"].note
    assert cov["genetic_support"].answer == "no" and cov["target_failure_checked"].answer == "yes"
    assert "0 trials same target, other modality" in cov["trials_found"].note

    for item in ("standard_of_care", "disease_burden", "pricing_analogues"):
        assert cov[item].status == "missing"
    for item in NON_PUBLIC:
        assert cov[item].status == "not_public" and cov[item].requires and not cov[item].public_answerable


@pytest.mark.live
def test_coverage_il17_crohns_with_cutoff():
    _, full = _run()
    _, cut = _run(date(2009, 1, 1))
    assert len(cut["trials_found"].evidence_ids) < len(full["trials_found"].evidence_ids)
    assert cut["trials_found"].status == "missing"
    assert cut["mechanism_validated_in_humans"].status == "missing"  # max_phase is undated
    assert cut["competitive_landscape"].status == "missing"
    assert cut["target_failure_checked"].status == "missing"  # R5 had no trial data to evaluate
    assert cut["genetic_support"].status == "covered"  # Open Targets associations are undated and kept
