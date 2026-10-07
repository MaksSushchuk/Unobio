"""CLI: the whole workflow, input -> data/runs/<run_id>/report.json.

    python -m orchestrator "Crohn's disease" "IL-17 inhibition"
    python -m orchestrator "B-cell acute lymphoblastic leukemia" "anti-CD19 CAR-T" --pdf
    python -m orchestrator "Crohn's disease" "IL-17 inhibition" --bundle data/runs/<id>/researcher_bundle.json
    python -m orchestrator "Crohn's disease" "IL-17 inhibition" --fake      # no model: offline stand-in answers

The LLM comes from server/.env (LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL ...), one model for every module.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from analytics.llm import LLMSettings
from analytics.orchestrator import RunOptions
from evidence_bundle import run_dir
from researcher.schema import Bundle, ResearchInput

from .pipeline import REPORT_FILE, run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="orchestrator", description="Input -> researcher -> analytics -> writer -> Report")
    p.add_argument("indication")
    p.add_argument("mechanism")
    p.add_argument("--modality")
    p.add_argument("--stage")
    p.add_argument("--biomarker", action="append", dest="biomarkers")
    p.add_argument("--route")
    p.add_argument("--evidence-cutoff", type=date.fromisoformat)
    p.add_argument("--target", action="append", dest="target_symbols", help="HGNC symbol; repeatable")
    p.add_argument("--bundle", type=Path, help="reuse a saved researcher_bundle.json (skips the researcher step)")
    p.add_argument("--pdf", action="store_true", help="also render report.pdf (writer)")
    p.add_argument("--fake", action="store_true", help="LLM_PROVIDER=fake: offline stand-in answers, no model")
    p.add_argument("--budget", type=int, default=RunOptions.budget_tokens, help="analytics evidence tokens per lens")
    args = vars(p.parse_args(argv))
    bundle_path, pdf, fake, budget = args.pop("bundle"), args.pop("pdf"), args.pop("fake"), args.pop("budget")

    inp = ResearchInput(**args)
    bundle = Bundle.model_validate_json(bundle_path.read_text(encoding="utf-8")) if bundle_path else None
    settings = LLMSettings.from_env()
    if fake:
        settings = replace(settings, provider="fake", model="fake-model")
    t0 = time.perf_counter()

    def on_event(step: str, status: str, payload: dict[str, Any]) -> None:
        details = ", ".join(f"{k}={v}" for k, v in payload.items() if v not in (None, [], ""))
        print(f"[{time.perf_counter() - t0:7.1f}s] {step:<16} {status:<8} {details}", flush=True)

    print(f"provider={settings.provider} model={settings.model} base_url={settings.base_url or '-'}", flush=True)
    report = asyncio.run(run(inp, bundle=bundle, settings=settings, options=RunOptions(budget_tokens=budget),
                             pdf=pdf, on_event=on_event))
    print(f"\n=== {report.recommendation} (confidence {report.confidence:.2f}) ===\n{report.summary}")
    print(f"sections {len(report.sections)} | risks {len(report.risks)} | evidence {len(report.evidence)} | "
          f"previous run {report.previous_run_id}")
    print(run_dir(report.id) / REPORT_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
