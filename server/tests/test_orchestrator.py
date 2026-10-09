"""Step 9 — orchestrator end-to-end with the offline FakeLLM."""
from __future__ import annotations

import asyncio
import json

import pytest

from analytics.lenses import fake_lens_llm
from analytics.llm import FakeLLM, LLMSettings, make_llm
from analytics.orchestrator import RunOptions, analyze
from analytics.schemas import AnalysisResult
from evidence_bundle import FIXTURES_DIR, load_bundle


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Write run artifacts into a temp folder, not data/runs."""
    import evidence_bundle.paths as paths
    monkeypatch.setattr(paths, "RUNS_DIR", tmp_path / "runs")
    return tmp_path / "runs"


def run(bundle_name: str, fake: FakeLLM | None = None, **opts):
    bundle = load_bundle(FIXTURES_DIR / bundle_name)
    llm = make_llm(LLMSettings(provider="fake"), fake=fake or fake_lens_llm())
    events: list[tuple[str, str]] = []
    result = asyncio.run(analyze(bundle, llm, RunOptions(**opts), lambda s, st, p: events.append((s, st))))
    return bundle, result, events


def test_full_run_writes_valid_analysis_result(data_dir):
    bundle, r, events = run("il17_crohn_now.json")
    assert r.verdict.recommendation == "Do Not Invest" and r.verdict.kill_triggered
    assert set(r.lenses) == {"science", "clinical", "market", "investment"}
    assert r.capital is not None and r.rnpv is not None and r.risks and 5 <= len(r.diligence_questions) <= 10
    assert r.short_ids["E1"] in bundle.by_id()
    out = data_dir / bundle.run_id
    saved = AnalysisResult.model_validate_json((out / "analysis_result.json").read_text())
    assert saved.verdict == r.verdict
    for name in ("evidence_bundle.json", "trace.jsonl", "context/science.md", "prompts/science.md", "lenses/science.json"):
        assert (out / name).exists(), name
    lines = [json.loads(x) for x in (out / "trace.jsonl").read_text().splitlines()]
    assert lines[-1]["step"] == "total" and lines[-1]["input_tokens"] > 0


def test_events_follow_the_pipeline_order(data_dir):
    _, _, events = run("il17_crohn_2011.json")
    steps = [s for s, st in events if st == "done"]
    assert steps[0] == "context" and steps[-1] == "run"
    assert steps.index("verdict") > max(steps.index(f"lens:{x}") for x in ("science", "clinical", "market", "investment"))
    assert steps.index("finance") < steps.index("findings") < steps.index("save")


def test_demo_flip(data_dir):
    _, then, _ = run("il17_crohn_2011.json")
    _, now, _ = run("il17_crohn_now.json")
    assert (then.verdict.recommendation, now.verdict.recommendation) == ("Conditional", "Do Not Invest")


def test_failed_lens_does_not_break_the_run(data_dir):
    fake = fake_lens_llm()
    fake.script["lens:market"] = "not json at all"
    _, r, events = run("il17_crohn_2011.json", fake=fake)
    assert r.lenses["market"].status == "failed"
    assert ("lens:market", "error") in events
    assert r.verdict.lens_scores["market"] is None
    assert r.verdict.recommendation == "Conditional"


def test_token_guard_shrinks_evidence_budget(data_dir):
    bundle, r, events = run("il17_crohn_now.json", max_total_tokens=12_000, budget_tokens=8000)
    assert ("context", "progress") in events
    assert r.verdict.recommendation == "Do Not Invest"  # kill signals are P0 and survive any budget


def test_only_selected_lenses(data_dir):
    _, r, _ = run("pcsk9_hypercholesterolemia.json", lenses=("science",), save=False)
    assert set(r.lenses) == {"science"} and not (data_dir / "fixture-pcsk9").exists()
