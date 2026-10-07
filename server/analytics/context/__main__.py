"""Inspect what each analyst would see.

    python -m analytics.context data/fixtures/il17_crohn_now.json
    python -m analytics.context <bundle.json> --budget 3000 --out data/runs/debug/context

Writes header.md, <lens>.md and id_map.json, and prints a per-lens summary.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evidence_bundle import load_bundle, run_dir

from .builder import ContextBuilder, ContextConfig


def main() -> None:
    ap = argparse.ArgumentParser(prog="analytics.context")
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--budget", type=int, default=ContextConfig.budget_tokens, help="evidence tokens per lens")
    ap.add_argument("--out", type=Path, help="output dir (default data/runs/<bundle_run_id>/context)")
    a = ap.parse_args()

    bundle = load_bundle(a.bundle)
    ctx = ContextBuilder(ContextConfig(budget_tokens=a.budget)).build(bundle)
    out = a.out or run_dir(bundle.run_id) / "context"
    out.mkdir(parents=True, exist_ok=True)
    (out / "header.md").write_text(ctx.header, encoding="utf-8")
    (out / "id_map.json").write_text(json.dumps(ctx.id_map, indent=2), encoding="utf-8")

    print(f"bundle {bundle.run_id}: {len(bundle.evidence)} evidence; header {ctx.header_tokens} tokens; budget {a.budget}/lens")
    print(f"{'lens':<11}{'tokens':>7}{'full':>6}{'brief':>7}{'aggr':>6}{'dropped':>9}{'conflicts':>11}  overflow")
    for lens, lc in ctx.lenses.items():
        (out / f"{lens}.md").write_text(ctx.prompt_context(lens), encoding="utf-8")
        print(f"{lens:<11}{lc.evidence_tokens:>7}{len(lc.full_ids):>6}{len(lc.brief_ids):>7}{len(lc.aggregate_ids):>6}"
              f"{len(lc.dropped_ids):>9}{len(lc.conflict_ids):>11}  {lc.p0_overflow}")
    print(f"written to {out}")


if __name__ == "__main__":
    main()
