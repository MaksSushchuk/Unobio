"""Full analytics run: evidence bundle -> analysis_result.json.

    python -m analytics data/fixtures/il17_crohn_now.json
    python -m analytics data/fixtures/il17_crohn_now.json --budget 4000 --max-tokens 60000
    python -m analytics data/fixtures/il17_crohn_now.json --lens science --lens clinical

LLM comes from .env (LLM_PROVIDER=fake -> offline stand-in answers, no cost).
Outputs in data/runs/<run_id>/: analysis_result.json, trace.jsonl, context/, prompts/, lenses/.
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path
from typing import Any

from evidence_bundle import load_bundle, run_dir

from .lenses import fake_lens_llm
from .llm import LLMSettings, make_llm
from .orchestrator import RunOptions, analyze
from .schemas import LENSES


def _m(x: float) -> str:
    return f"{'-' if x < 0 else ''}${abs(x) / 1e6:,.0f}M"


async def main() -> None:
    ap = argparse.ArgumentParser(prog="analytics", description="Run the four analysts + verdict + finance + findings")
    ap.add_argument("bundle", type=Path, help="evidence_bundle.json (e.g. data/fixtures/il17_crohn_now.json)")
    ap.add_argument("--lens", choices=LENSES, action="append", help="run only these lenses (repeatable)")
    ap.add_argument("--budget", type=int, default=RunOptions.budget_tokens, help="evidence tokens per lens")
    ap.add_argument("--max-tokens", type=int, default=RunOptions.max_total_tokens, help="token guard for the whole run")
    ap.add_argument("--no-detail", action="store_true", help="disable the need_detail follow-up round")
    a = ap.parse_args()

    bundle = load_bundle(a.bundle)
    settings = LLMSettings.from_env()
    llm = make_llm(settings, fake=fake_lens_llm() if settings.provider == "fake" else None)
    opts = RunOptions(lenses=tuple(a.lens or LENSES), budget_tokens=a.budget, max_total_tokens=a.max_tokens,
                      detail_round=not a.no_detail)
    t0 = time.perf_counter()

    def on_event(step: str, status: str, payload: dict[str, Any]) -> None:
        details = ", ".join(f"{k}={v}" for k, v in payload.items() if v not in (None, [], ""))
        print(f"[{time.perf_counter() - t0:6.1f}s] {step:<16} {status:<8} {details}")

    print(f"provider={settings.provider} model={llm.model} bundle={bundle.run_id}"
          + (" (FIXTURE)" if bundle.is_fixture else ""))
    try:
        r = await analyze(bundle, llm, opts, on_event)
    finally:
        await llm.aclose()

    v = r.verdict
    print(f"\n=== {v.recommendation.upper()} (confidence {v.confidence:.2f}, composite {v.composite_score}) ===")
    print("lens scores: " + ", ".join(f"{k}={s}" for k, s in v.lens_scores.items()))
    if r.capital:
        c = r.capital
        print(f"capital to '{c.milestone}': {_m(c.usd_low)} / {_m(c.usd_base)} / {_m(c.usd_high)}, "
              f"{c.months_low}-{c.months_high} months")
    if r.rnpv:
        print(f"rNPV: {_m(r.rnpv.usd_low)} / {_m(r.rnpv.usd_base)} / {_m(r.rnpv.usd_high)} "
              f"(P(approval) {r.rnpv.probability_of_success:.1%})")
    print(f"risks: {len(r.risks)} | unknowns: {len(r.unknowns)} | diligence questions: {len(r.diligence_questions)}")
    for risk in r.risks[:3]:
        print(f"  [{risk.severity}] {risk.title}")
    total = next(t for t in r.traces if t.step == "total")
    print(f"tokens in/out {total.input_tokens}/{total.output_tokens}, cost ${total.cost_usd:.4f}, "
          f"time {total.latency_s:.1f}s")
    print(f"saved: {run_dir(bundle.run_id) / 'analysis_result.json'}")


if __name__ == "__main__":
    asyncio.run(main())
