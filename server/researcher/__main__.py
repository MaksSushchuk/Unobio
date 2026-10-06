"""CLI: python -m researcher "<indication>" "<mechanism>" [options]"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from researcher.schema import ResearchInput
from researcher.pipeline import run_research, write_bundle


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="researcher", description="Collect biotech evidence.")
    p.add_argument("indication")
    p.add_argument("mechanism")
    p.add_argument("--modality")
    p.add_argument("--stage")
    p.add_argument("--biomarker", action="append", dest="biomarkers")
    p.add_argument("--route")
    p.add_argument("--evidence-cutoff", type=date.fromisoformat)
    p.add_argument("--target", action="append", dest="target_symbols", help="HGNC symbol; repeatable")
    p.add_argument("--no-llm", action="store_true", help="skip the LLM planner (use fallback)")
    args = vars(p.parse_args(argv))
    use_llm = not args.pop("no_llm")

    inp = ResearchInput(**args)
    bundle = run_research(inp, use_llm=use_llm)
    path = write_bundle(bundle)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
