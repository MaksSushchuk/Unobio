"""Run the four analysts on a bundle and inspect their results.

    python -m analytics.lenses data/fixtures/il17_crohn_now.json
    python -m analytics.lenses data/fixtures/il17_crohn_now.json --lens science --budget 4000

Uses the LLM configured in .env (LLM_PROVIDER=fake -> offline stand-in answers).
Writes data/runs/<bundle_run_id>/lenses/<lens>.json and prompts/<lens>.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from evidence_bundle import load_bundle, run_dir

from ..agents import AgentRunner
from ..context import ContextBuilder, ContextConfig
from ..llm import LLMSettings, make_llm
from ..schemas import LENSES
from .fake import fake_lens_llm
from .run import lens_spec, lens_user_prompt, run_lenses


async def main() -> None:
    ap = argparse.ArgumentParser(prog="analytics.lenses")
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--lens", choices=LENSES, action="append", help="run only these lenses (repeatable)")
    ap.add_argument("--budget", type=int, default=ContextConfig.budget_tokens)
    a = ap.parse_args()

    bundle = load_bundle(a.bundle)
    ctx = ContextBuilder(ContextConfig(budget_tokens=a.budget)).build(bundle)
    settings = LLMSettings.from_env()
    llm = make_llm(settings, fake=fake_lens_llm() if settings.provider == "fake" else None)
    lenses = tuple(a.lens or LENSES)
    out = run_dir(bundle.run_id)
    (out / "prompts").mkdir(exist_ok=True)
    (out / "lenses").mkdir(exist_ok=True)
    for lens in lenses:  # save exactly what was sent, for debugging prompts
        (out / "prompts" / f"{lens}.md").write_text(
            lens_spec(lens).full_system_prompt() + "\n\n=== USER ===\n\n" + lens_user_prompt(lens, ctx), encoding="utf-8")

    print(f"provider={settings.provider} model={llm.model} bundle={bundle.run_id} lenses={','.join(lenses)}")
    try:
        runs = await run_lenses(bundle, ctx, AgentRunner(llm), lenses)
    finally:
        await llm.aclose()

    total_cost = 0.0
    for lens, r in runs.items():
        res = r.result
        tokens = sum(t.input_tokens + t.output_tokens for t in r.traces)
        cost = sum(t.cost_usd for t in r.traces)
        total_cost += cost
        print(f"\n[{lens}] status={res.status} score={res.score} claims={len(res.claims)} unknowns={len(res.unknowns)} "
              f"attempts={len(r.traces)} tokens={tokens} cost=${cost:.4f}" + (f" detail={r.detail_ids}" if r.detail_ids else ""))
        if res.errors:
            print("  errors: " + " | ".join(res.errors[:3]))
        if res.score_rationale:
            print(f"  rationale: {res.score_rationale[:200]}")
        (out / "lenses" / f"{lens}.json").write_text(
            json.dumps({"result": res.model_dump(mode="json"), "traces": [t.model_dump() for t in r.traces]}, indent=2),
            encoding="utf-8")
    print(f"\ntotal cost ${total_cost:.4f}; written to {out}")


if __name__ == "__main__":
    asyncio.run(main())
