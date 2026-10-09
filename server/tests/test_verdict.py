"""Step 6 — verdict rules (pure code, no LLM)."""
from __future__ import annotations

import asyncio

import pytest

from analytics.agents import AgentRunner
from analytics.context import ContextBuilder
from analytics.lenses import fake_lens_llm, run_lenses
from analytics.llm import LLMSettings, make_llm
from analytics.schemas import LENSES, Claim, EvidenceRef, LensResult
from analytics.verdict import VerdictConfig, decide
from evidence_bundle import FIXTURES_DIR, load_bundle


@pytest.fixture(scope="module")
def now():
    return load_bundle(FIXTURES_DIR / "il17_crohn_now.json")


@pytest.fixture(scope="module")
def pcsk9():
    return load_bundle(FIXTURES_DIR / "pcsk9_hypercholesterolemia.json")


def lenses(science=4, clinical=4, market=4, investment=4, n_facts=3, failed=()) -> dict:
    out = {}
    for lens, score in zip(LENSES, (science, clinical, market, investment)):
        if lens in failed:
            out[lens] = LensResult(lens=lens, status="failed", errors=["boom"])
            continue
        claims = [Claim(id=f"C{lens}{i}", lens=lens, text=f"fact {i} about {lens}", kind="source_fact", confidence=0.8,
                        evidence=[EvidenceRef(evidence_id="x")]) for i in range(n_facts)]
        out[lens] = LensResult(lens=lens, score=score, claims=claims)
    return out


def test_kill_rule_beats_any_scores(now):
    v = decide(now, lenses(5, 5, 5, 5))
    assert v.recommendation == "Do Not Invest" and v.kill_triggered
    assert set(v.kill_evidence_ids) == {e.id for e in now.conflicts() if e.data.get("kill_signal")}
    assert v.confidence >= 0.85  # two independent failed programs
    assert any(line.startswith("RULE kill") for line in v.rule_trace)


def test_overridden_kills_cap_at_conditional(now):
    overrides = {e.id for e in now.conflicts() if e.data.get("kill_signal")}
    v = decide(now, lenses(5, 5, 5, 5), kill_overrides=overrides)
    assert v.recommendation == "Conditional" and not v.kill_triggered
    assert any("OVERRIDDEN" in line for line in v.rule_trace)


def test_strong_validated_case_is_invest(pcsk9):
    v = decide(pcsk9, lenses(5, 4, 4, 4))
    assert v.recommendation == "Invest" and v.composite_score == pytest.approx(4.35)


@pytest.mark.parametrize("scores,expected", [
    ((1, 4, 4, 4), "Do Not Invest"),   # science floor
    ((4, 0, 4, 4), "Do Not Invest"),   # clinical floor
    ((2, 2, 1, 2), "Do Not Invest"),   # composite < 2.0
    ((3, 3, 3, 3), "Conditional"),
    ((4, 4, 4, 2), "Conditional"),     # composite 3.6 but one lens < 3 -> cap
])
def test_thresholds(pcsk9, scores, expected):
    assert decide(pcsk9, lenses(*scores)).recommendation == expected


def test_high_severity_conflict_caps_invest(now):
    no_kill = now.model_copy(deep=True)
    for e in no_kill.evidence:
        e.data["kill_signal"] = False
    v = decide(no_kill, lenses(5, 5, 5, 5))
    assert v.recommendation == "Conditional"
    assert any(line.startswith("CAP") and "high-severity" in line for line in v.rule_trace)


def test_failed_lenses_cap_and_lower_confidence(pcsk9):
    full = decide(pcsk9, lenses(5, 5, 5, 5))
    partial = decide(pcsk9, lenses(5, 5, 5, 5, failed=("market",)))
    assert full.recommendation == "Invest" and partial.recommendation == "Conditional"
    assert partial.confidence < full.confidence
    assert partial.lens_scores["market"] is None


def test_custom_config(pcsk9):
    strict = VerdictConfig(invest_min_composite=4.8)
    assert decide(pcsk9, lenses(5, 4, 4, 4), strict).recommendation == "Conditional"


def test_demo_flip_with_offline_lenses():
    """The demo story: same question, evidence as of 2011 vs today -> verdict changes."""
    def verdict(name: str):
        b = load_bundle(FIXTURES_DIR / name)
        runner = AgentRunner(make_llm(LLMSettings(provider="fake"), fake=fake_lens_llm()))
        runs = asyncio.run(run_lenses(b, ContextBuilder().build(b), runner))
        return decide(b, {k: r.result for k, r in runs.items()})

    assert verdict("il17_crohn_2011.json").recommendation == "Conditional"
    assert verdict("il17_crohn_now.json").recommendation == "Do Not Invest"


def test_thin_evidence_lowers_confidence(pcsk9):
    thin = pcsk9.model_copy(deep=True)
    thin.evidence = thin.evidence[:3]
    full, sparse = decide(pcsk9, lenses(3, 3, 3, 3)), decide(thin, lenses(3, 3, 3, 3))
    assert sparse.recommendation == full.recommendation == "Conditional"
    assert sparse.confidence < full.confidence
    assert any("primary evidence items" in line for line in sparse.rule_trace)


def test_composite_near_threshold_is_less_certain(pcsk9):
    # composite 2.80 sits mid-way in the Conditional band; 3.35 sits next to the 3.5 Invest line (same agreement)
    mid = decide(pcsk9, lenses(3, 3, 2, 3))
    edge = decide(pcsk9, lenses(4, 3, 3, 3))
    assert mid.recommendation == edge.recommendation == "Conditional"
    assert edge.composite_score > mid.composite_score
    assert edge.confidence < mid.confidence
    assert "margin" in next(line for line in edge.rule_trace if line.startswith("confidence"))


def test_kill_programs_counted_from_synthesized_signal(now):
    from analytics.verdict.engine import kill_programs
    sig = now.model_copy(deep=True)
    kill = next(e for e in sig.evidence if e.data.get("kill_signal"))
    kill.data["drugs"] = [{"name": "drug-a"}, {"name": "drug-b"}, {"name": "drug-c"}]
    kill.data.pop("drug", None)
    assert {"drug-a", "drug-b", "drug-c"} <= set(kill_programs(sig, [kill.id]))
