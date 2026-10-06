"""Live end-to-end checks on B-cell ALL + anti-CD19 CAR-T: modality-aware drug relations, trial drug links,
and stop-reason categories."""

import pytest

from researcher.schema import ResearchInput, SearchPlan
from researcher.llm import GeminiLLM
from researcher.pipeline import research_with_plan

INPUT = ResearchInput(indication="B-cell acute lymphoblastic leukemia", mechanism="anti-CD19 CAR-T")
CART_PLAN = SearchPlan(
    target_symbols=["CD19"], action="other", modality="cell_therapy",
    disease_terms=["B-cell acute lymphoblastic leukemia", "B-ALL"],
    drug_names=["tisagenlecleucel", "brexucabtagene autoleucel"],
)
SUBJECT = {"tisagenlecleucel", "brexucabtagene autoleucel", "obecabtagene autoleucel"}
OTHER_MODALITY = {"blinatumomab", "tafasitamab"}
STOP_CATEGORIES = {"NCT03743246": "efficacy", "NCT04506086": "enrollment"}


@pytest.fixture(scope="module")
def bundle():
    return research_with_plan(INPUT, CART_PLAN)


def _ct(bundle):
    return {e.data["nct_id"]: e for e in bundle.evidence if e.source == "clinicaltrials"}


@pytest.mark.live
def test_drug_relations(bundle):
    rel = {d.name: d.relation for d in bundle.subject.drugs}
    assert {n: rel.get(n) for n in SUBJECT} == dict.fromkeys(SUBJECT, "subject_mechanism"), rel
    assert {n: rel.get(n) for n in OTHER_MODALITY} == dict.fromkeys(OTHER_MODALITY, "same_target_other_modality")
    assert all(d.drug_type for d in bundle.subject.drugs if d.name in SUBJECT | OTHER_MODALITY)


@pytest.mark.live
def test_trial_drug_links(bundle):
    ct = _ct(bundle)
    # Tests nivolumab; tisagenlecleucel is only named in the title as background.
    assert "drug" not in ct["NCT05310591"].entity_refs and ct["NCT05310591"].data["subject_drug_chembl_ids"] == []
    relation = {d.chembl_id: d.relation for d in bundle.subject.drugs}
    for e in ct.values():
        ids = e.data["subject_drug_chembl_ids"]
        assert e.entity_refs.get("drug") == (ids[0] if ids else None), e.data["nct_id"]
        assert e.data["drug_relation"] == (relation[ids[0]] if ids else None)
        assert e.data["found_via"]
    assert bundle.reconcile_stats.drugs_unlinked == 0  # the connector already links by the same rule


@pytest.mark.live
def test_stop_categories_keyword(bundle):
    ct = _ct(bundle)
    for nct, category in STOP_CATEGORIES.items():
        assert (ct[nct].data["stop_category"], ct[nct].data["stop_category_source"]) == (category, "keyword")
    for e in ct.values():
        if e.data["overall_status"] in ("TERMINATED", "WITHDRAWN", "SUSPENDED") and not e.data["why_stopped"]:
            assert e.data["stop_category"] == "unknown" and "red-flags" not in e.modules


@pytest.mark.live
def test_coverage_splits_relations(bundle):
    cov = {c.id: c for c in bundle.coverage}
    assert "same target, other modality" in cov["trials_found"].note
    assert "subject mechanism" in cov["competitive_landscape"].note
    assert "blinatumomab" in cov["mechanism_validated_in_humans"].note.split("another modality")[1]


@pytest.mark.live
@pytest.mark.llm
def test_stop_categories_llm():
    llm = GeminiLLM.from_env()
    b = research_with_plan(INPUT, CART_PLAN, llm=llm)
    ct = _ct(b)
    for nct, category in STOP_CATEGORIES.items():
        assert (ct[nct].data["stop_category"], ct[nct].data["stop_category_source"]) == (category, "llm")
    stop_calls = [c for c in b.llm_calls if c.purpose == "stop_reason"]
    assert len(stop_calls) == 1 and stop_calls[0].ok
