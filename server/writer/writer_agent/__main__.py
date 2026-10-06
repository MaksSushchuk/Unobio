"""Command line: python -m writer_agent input.json -o report.pdf

Prints the WriterResult as JSON. Exit code 0 for ok/degraded, 1 for error.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .agent import WriterAgent
from .config import WriterConfig


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="writer_agent", description="Render the underwriting PDF report from a JSON input file.")
    ap.add_argument("input", help="Path to the Writer input JSON")
    ap.add_argument("-o", "--output", default="report.pdf", help="Output PDF path")
    ap.add_argument("--model", help="Override WRITER_LLM_MODEL")
    ap.add_argument("--url", help="Override WRITER_LLM_URL")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    cfg = WriterConfig()
    if args.model:
        cfg.llm_model = args.model
    if args.url:
        cfg.llm_url = args.url
    result = WriterAgent(cfg).run(args.input, args.output)
    json.dump(result.to_dict(), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 1 if result.status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
