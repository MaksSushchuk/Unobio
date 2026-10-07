"""Capital and rNPV from saved lens results — no LLM calls.

    python -m analytics.lenses  data/fixtures/il17_crohn_now.json   # once
    python -m analytics.finance data/fixtures/il17_crohn_now.json
"""
from __future__ import annotations

import argparse
from pathlib import Path

from evidence_bundle import load_bundle

from ..verdict import decide
from ..verdict.__main__ import load_lens_results
from .model import build_finance


def _m(x: float) -> str:
    return f"{'-' if x < 0 else ''}${abs(x) / 1e6:,.0f}M"


def main() -> None:
    ap = argparse.ArgumentParser(prog="analytics.finance")
    ap.add_argument("bundle", type=Path)
    a = ap.parse_args()
    bundle = load_bundle(a.bundle)
    lenses = load_lens_results(bundle.run_id)
    verdict = decide(bundle, lenses) if lenses else None
    f = build_finance(bundle, lenses.get("investment"), verdict)
    c = f.capital
    print(f"Milestone: {c.milestone}")
    print(f"Capital:   {_m(c.usd_low)} / {_m(c.usd_base)} / {_m(c.usd_high)}  over {c.months_low}–{c.months_high} months")
    for line in c.assumptions:
        print(f"  - {line}")
    if f.rnpv:
        r = f.rnpv
        print(f"\nrNPV:      {_m(r.usd_low)} / {_m(r.usd_base)} / {_m(r.usd_high)}   P(approval) {r.probability_of_success:.1%}")
        for line in r.assumptions:
            print(f"  - {line}")
    for n in f.notes:
        print(f"note: {n}")


if __name__ == "__main__":
    main()
