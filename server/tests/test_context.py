"""Step 2 — ContextBuilder on the fixture bundles."""
from __future__ import annotations

import pytest

from analytics.context import ContextBuilder, ContextConfig, ShortIdMap, lenses_for
from analytics.schemas import LENSES
from evidence_bundle import FIXTURES_DIR, load_bundle


@pytest.fixture(scope="module")
def now():
    return load_bundle(FIXTURES_DIR / "il17_crohn_now.json")


@pytest.fixture(scope="module")
def ctx_now(now):
    return ContextBuilder().build(now)


def test_every_evidence_reaches_at_least_one_lens(now):
    assert all(lenses_for(e) for e in now.evidence)


def test_conflicts_reach_every_lens(now, ctx_now):
    n_conflicts = len(now.conflicts())
    for lens in LENSES:
        assert len(ctx_now.lenses[lens].conflict_ids) == n_conflicts


def test_short_ids_are_global_and_bijective(now):
    ids = ShortIdMap(now.evidence)
    shorts = [ids.short(e.id) for e in now.evidence]
    assert sorted(shorts, key=lambda s: int(s[1:])) == [f"E{i}" for i in range(1, len(now.evidence) + 1)]
    assert all(ids.full(ids.short(e.id)) == e.id for e in now.evidence)
    assert ids.resolve("e1") == [ids.full("E1")] and ids.resolve("E999") == []


def test_build_is_deterministic(now):
    a, b = ContextBuilder().build(now), ContextBuilder().build(now)
    assert a.model_dump() == b.model_dump()


def test_kill_conflicts_are_must_read(ctx_now):
    text = ctx_now.lenses["science"].text
    must_read = text.split("## EVIDENCE")[0]
    assert must_read.count("KILL SIGNAL") == 3


def test_aggregate_resolves_to_members(ctx_now):
    aggs = ctx_now.id_map["aggregates"]
    assert aggs, "approved-therapies group (6 items) should be aggregated"
    a_id, members = next(iter(aggs.items()))
    assert len(members) >= ContextConfig().aggregate_min
    assert f"[{a_id}] AGGREGATE" in ctx_now.lenses["market"].text


@pytest.mark.parametrize("budget", [600, 1200, 3000])
def test_budget_is_respected_but_must_read_never_dropped(now, budget):
    ctx = ContextBuilder(ContextConfig(budget_tokens=budget)).build(now)
    for lens, lc in ctx.lenses.items():
        if not lc.p0_overflow:
            assert lc.evidence_tokens <= budget + 60, lens  # +60: section headings / dropped-ids line
        assert set(lc.conflict_ids) <= set(lc.full_ids), "conflicts must always be shown in full"
        shown = set(lc.full_ids) | set(lc.brief_ids)
        assert not shown & set(lc.dropped_ids)


def test_tight_budget_keeps_red_flag_literature_over_neutral_records(now):
    ctx = ContextBuilder(ContextConfig(budget_tokens=1200)).build(now)
    ids = ShortIdMap(now.evidence)
    hueber = next(e for e in now.evidence if e.entity_refs.get("pmid") == "22595313")
    assert ids.short(hueber.id) not in ctx.lenses["science"].dropped_ids


def test_cutoff_bundle_header_warns_and_has_no_conflicts():
    b = load_bundle(FIXTURES_DIR / "il17_crohn_2011.json")
    ctx = ContextBuilder().build(b)
    assert "EVIDENCE CUTOFF: 2011-06-30" in ctx.header
    assert all(not lc.conflict_ids for lc in ctx.lenses.values())
    assert "Cosentyx" not in ctx.prompt_context("science")  # brand names hidden under cutoff
