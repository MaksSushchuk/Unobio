"""Step 8 — risks, unknowns, diligence questions (pure code)."""
from __future__ import annotations

import pytest

from analytics.findings import MAX_QUESTIONS, MIN_QUESTIONS, build_diligence, build_risks, merge_unknowns
from analytics.schemas import Claim, EvidenceRef, LensResult, Unknown
from evidence_bundle import FIXTURES_DIR, load_bundle


@pytest.fixture(scope="module")
def now():
    return load_bundle(FIXTURES_DIR / "il17_crohn_now.json")


@pytest.fixture(scope="module")
def then():
    return load_bundle(FIXTURES_DIR / "il17_crohn_2011.json")


def lens(name, score, cites=(), unknowns=(), chain=None) -> LensResult:
    claims = [Claim(id=f"C{name}{i}", lens=name, text=f"claim {i} of {name}", kind="inference", confidence=0.6,
                    evidence=[EvidenceRef(evidence_id=c, stance="contradicts")]) for i, c in enumerate(cites)]
    params = {"translation_chain": chain} if chain else {}
    return LensResult(lens=name, score=score, score_rationale=f"{name} rationale", claims=claims,
                      unknowns=list(unknowns), params=params)


def test_conflicts_become_risks_kill_first_and_linked_to_claims(now):
    kill_ids = [e.id for e in now.conflicts() if e.data.get("kill_signal")]
    risks = build_risks(now, {"science": lens("science", 3, cites=[kill_ids[0]])})
    assert len(risks) == len([e for e in now.conflicts() if e.data.get("severity") != "info"])
    assert "Same-target failure" in risks[0].description  # kill signals ranked first
    assert any("Cscience0" in r.claim_ids for r in risks)
    ids = set(now.by_id())
    assert all(set(r.evidence_ids) <= ids for r in risks)


def test_weak_lenses_form_one_combined_risk(then):
    risks = build_risks(then, {"science": lens("science", 1), "market": lens("market", 2), "clinical": lens("clinical", 4)})
    weak = [r for r in risks if r.title.startswith("Low analyst scores")]
    assert len(weak) == 1 and weak[0].severity == "high"
    assert "science 1/5" in weak[0].title and "market 2/5" in weak[0].title and "clinical" not in weak[0].title


def test_translation_gaps_become_risks(then):
    chain = [{"step": "target_engagement", "status": "missing", "evidence_ids": []},
             {"step": "patient_benefit", "status": "contradicted", "evidence_ids": []},
             {"step": "human_exposure", "status": "supported", "evidence_ids": []}]
    titles = {r.title: r.severity for r in build_risks(then, {"science": lens("science", 3, chain=chain)})}
    assert titles == {"Translation gap: target engagement not demonstrated": "medium",
                      "Translation gap: patient benefit contradicted": "high"}


def test_unknowns_are_merged_and_deduplicated(then):
    u1 = Unknown(question="Is target engagement demonstrated in the relevant diseased tissue?", requires="proprietary_data")
    u2 = Unknown(question="Is target engagement demonstrated in diseased tissue?", requires="experiment")
    merged = merge_unknowns(then, {"science": lens("science", 3, unknowns=[u1]), "clinical": lens("clinical", 3, unknowns=[u2])})
    assert sum("target engagement" in u.question for u in merged) == 1
    assert len(merged) >= len(then.gaps)


def test_diligence_names_all_failed_programs_first(now):
    risks = build_risks(now, {})
    qs = build_diligence(now, {}, risks, merge_unknowns(now, {}))
    assert MIN_QUESTIONS <= len(qs) <= MAX_QUESTIONS
    assert "Secukinumab" in qs[0].question and "Brodalumab" in qs[0].question and qs[0].requires == "patient_data"
    assert any("kill the program" in q.question for q in qs)
    assert len({q.question for q in qs}) == len(qs)


def test_diligence_minimum_without_conflicts(then):
    qs = build_diligence(then, {}, [], merge_unknowns(then, {}))
    assert len(qs) >= MIN_QUESTIONS
    assert {q.requires for q in qs} >= {"experiment", "proprietary_data", "kol"}


def test_diligence_minimum_even_with_nothing(then):
    empty = then.model_copy(update={"gaps": []})
    assert len(build_diligence(empty, {}, [], [])) >= MIN_QUESTIONS
