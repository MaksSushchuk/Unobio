"""Compute the verdict from saved lens results — no LLM calls, so thresholds can be tuned instantly.

    python -m analytics.lenses  data/fixtures/il17_crohn_now.json   # once: produces data/runs/<id>/lenses/*.json
    python -m analytics.verdict data/fixtures/il17_crohn_now.json   # as often as you like
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evidence_bundle import load_bundle, run_dir

from ..schemas import LENSES, Lens, LensResult
from .engine import decide


def load_lens_results(run_id: str) -> dict[Lens, LensResult]:
    folder = run_dir(run_id) / "lenses"
    out: dict[Lens, LensResult] = {}
    for lens in LENSES:
        path = folder / f"{lens}.json"
        if path.exists():
            out[lens] = LensResult.model_validate(json.loads(path.read_text(encoding="utf-8"))["result"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="analytics.verdict")
    ap.add_argument("bundle", type=Path)
    a = ap.parse_args()
    bundle = load_bundle(a.bundle)
    lenses = load_lens_results(bundle.run_id)
    if not lenses:
        raise SystemExit(f"No lens results in {run_dir(bundle.run_id) / 'lenses'} — run `python -m analytics.lenses {a.bundle}` first.")
    v = decide(bundle, lenses)
    print("\n".join(v.rule_trace))
    print(f"\n=> {v.recommendation} (confidence {v.confidence:.2f})")


if __name__ == "__main__":
    main()
