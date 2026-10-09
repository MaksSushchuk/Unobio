"""Command line.

    python -m app run "Crohn's disease" "IL-17 inhibition"                 # live research (public APIs)
    python -m app run "Crohn's disease" "IL-17 inhibition" --cutoff 2011-06-30
    python -m app run "Crohn's disease" "IL-17 inhibition" --source fixture   # offline, data/fixtures
    python -m app run "x" "y" --bundle path/to/bundle.json                   # reuse a saved bundle
    python -m app run ... --no-pdf                                           # skip the writer step (no PDF)
    python -m app serve [--port 8000]                                        # HTTP API for the web UI
"""
from __future__ import annotations

import argparse
import asyncio
import time
from datetime import date
from typing import Any

from analytics.llm.config import load_dotenv


def _run(a: argparse.Namespace) -> int:
    from .pipeline import RunRequest, run_pipeline

    req = RunRequest(indication=a.indication, mechanism=a.mechanism, modality=a.modality, stage=a.stage,
                     biomarkers=a.biomarker, route=a.route, evidence_cutoff=a.cutoff,
                     research_llm=not a.no_research_llm, detail_round=not a.no_detail)
    if a.bundle:
        req.source, req.bundle_path = "bundle", a.bundle
    elif a.source:
        req.source = a.source
    if a.no_pdf:
        req.pdf = False
    t0 = time.perf_counter()

    def on_event(step: str, status: str, payload: dict[str, Any]) -> None:
        details = ", ".join(f"{k}={v}" for k, v in payload.items() if v not in (None, [], "", {}))
        print(f"[{time.perf_counter() - t0:6.1f}s] {step:<16} {status:<8} {details}"[:240])

    out = asyncio.run(run_pipeline(req, on_event=on_event))
    r = out.report
    print(f"\n=== {r['recommendation'].upper()} (confidence {r['confidence']:.2f}) ===")
    print(r["summary"])
    for w in out.warnings:
        print(f"warning: {w}")
    print(f"\nrun folder: {out.dir}")
    print(f"report:     {out.dir / 'report.json'}\nmarkdown:   {out.dir / 'report.md'}"
          + (f"\npdf:        {out.pdf_path}" if out.pdf_path else ""))
    return 0


def _serve(a: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("app.server:app", host=a.host, port=a.port, reload=False)
    return 0


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="app", description="Unobio end-to-end: research -> analytics -> report")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the whole pipeline once")
    r.add_argument("indication")
    r.add_argument("mechanism")
    r.add_argument("--modality")
    r.add_argument("--stage")
    r.add_argument("--biomarker", action="append")
    r.add_argument("--route")
    r.add_argument("--cutoff", type=date.fromisoformat, help="evidence cutoff date, e.g. 2011-06-30")
    r.add_argument("--source", choices=["live", "fixture"], help="default: UNOBIO_RESEARCH or live")
    r.add_argument("--bundle", help="use a saved bundle (researcher or analytics format) instead of research")
    r.add_argument("--no-pdf", action="store_true", help="skip the writer step (narrative texts + PDF)")
    r.add_argument("--no-research-llm", action="store_true", help="researcher without the LLM (rule fallbacks)")
    r.add_argument("--no-detail", action="store_true", help="disable the analysts' need_detail round")
    r.set_defaults(func=_run)

    s = sub.add_parser("serve", help="HTTP API for the web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=_serve)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
