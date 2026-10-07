"""Risks, unknowns and diligence questions from saved lens results — no LLM calls.

    python -m analytics.lenses   data/fixtures/il17_crohn_now.json   # once
    python -m analytics.findings data/fixtures/il17_crohn_now.json
"""
from __future__ import annotations

import argparse
from pathlib import Path

from evidence_bundle import load_bundle

from ..verdict.__main__ import load_lens_results
from . import build_diligence, build_risks, merge_unknowns


def main() -> None:
    ap = argparse.ArgumentParser(prog="analytics.findings")
    ap.add_argument("bundle", type=Path)
    a = ap.parse_args()
    bundle = load_bundle(a.bundle)
    lenses = load_lens_results(bundle.run_id)
    risks = build_risks(bundle, lenses)
    unknowns = merge_unknowns(bundle, lenses)
    questions = build_diligence(bundle, lenses, risks, unknowns)
    print(f"RISKS ({len(risks)})")
    for r in risks:
        print(f"  [{r.severity}] {r.title}  (evidence {len(r.evidence_ids)}, claims {len(r.claim_ids)})")
    print(f"\nUNKNOWNS ({len(unknowns)})")
    for u in unknowns:
        print(f"  [{u.requires}] {u.question}")
    print(f"\nDILIGENCE QUESTIONS ({len(questions)})")
    for i, q in enumerate(questions, 1):
        print(f"  {i}. [{q.requires}] {q.question}")


if __name__ == "__main__":
    main()
