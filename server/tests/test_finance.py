"""Step 7 — capital to milestone and rNPV (pure code)."""
from __future__ import annotations

import math

import pytest

from analytics.finance import TRANSITION_POS, FinanceAssumptions, build_finance
from analytics.schemas import LensResult, Verdict
from evidence_bundle import FIXTURES_DIR, load_bundle


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(FIXTURES_DIR / "il17_crohn_2011.json")


def investment(stage="preclinical", peak=(2e8, 6e8, 1.5e9), trials=None) -> LensResult:
    trials = trials if trials is not None else [
        {"phase": "PHASE1", "patients_low": 40, "patients_high": 80, "months_low": 9, "months_high": 15},
        {"phase": "PHASE2", "patients_low": 100, "patients_high": 200, "months_low": 18, "months_high": 30},
    ]
    return LensResult(lens="investment", score=3, params={
        "current_stage": stage, "next_milestone": "Phase 2 PoC readout", "trials_to_milestone": trials,
        "peak_sales_usd_low": peak[0], "peak_sales_usd_base": peak[1], "peak_sales_usd_high": peak[2]})


def kill_verdict(kill: bool) -> Verdict:
    return Verdict(recommendation="Do Not Invest" if kill else "Conditional", confidence=0.8, kill_triggered=kill)


def test_capital_from_analyst_trials(bundle):
    c = build_finance(bundle, investment()).capital
    assert (c.months_low, c.months_high) == (12 + 9 + 18, 18 + 15 + 30)  # IND-enabling + Ph1 + Ph2
    assert c.usd_low < c.usd_base < c.usd_high
    a = FinanceAssumptions()
    expected_low = a.ind_enabling_usd[0] + 40 * 25_000 + 100 * 30_000 + a.overhead_per_year[0] * 39 / 12
    assert c.usd_low == pytest.approx(expected_low, rel=0.01)
    assert c.milestone == "Phase 2 PoC readout" and any("IND-enabling" in x for x in c.assumptions)


def test_phase2_asset_skips_ind_package(bundle):
    c = build_finance(bundle, investment(stage="phase2", trials=[
        {"phase": "PHASE2", "patients_low": 100, "patients_high": 200, "months_low": 18, "months_high": 30}])).capital
    assert (c.months_low, c.months_high) == (18, 30)
    assert not any("IND-enabling" in x for x in c.assumptions)


def test_probability_of_success_is_product_of_transitions(bundle):
    r = build_finance(bundle, investment()).rnpv
    assert r is not None
    assert r.probability_of_success == pytest.approx(math.prod(TRANSITION_POS.values()), abs=1e-4)
    assert r.usd_low < r.usd_base < r.usd_high


def test_kill_signal_cuts_value(bundle):
    clean = build_finance(bundle, investment(), kill_verdict(False)).rnpv
    killed = build_finance(bundle, investment(), kill_verdict(True)).rnpv
    assert clean is not None and killed is not None
    assert killed.probability_of_success == pytest.approx(clean.probability_of_success * 0.25, abs=1e-4)
    assert killed.usd_base < clean.usd_base
    assert any("Kill signal" in x for x in killed.assumptions)


def test_bigger_market_means_higher_rnpv(bundle):
    small = build_finance(bundle, investment(peak=(1e8, 2e8, 4e8))).rnpv
    big = build_finance(bundle, investment(peak=(1e9, 2e9, 4e9))).rnpv
    assert small is not None and big is not None and big.usd_base > small.usd_base


def test_fallback_without_investment_lens(bundle):
    f = build_finance(bundle, LensResult(lens="investment", status="failed"))
    assert f.rnpv is None
    assert any("benchmarks" in n for n in f.notes)
    assert any("trial benchmarks" in x for x in f.capital.assumptions)  # Ph2 sized from the indication
    assert f.capital.usd_base > 0
