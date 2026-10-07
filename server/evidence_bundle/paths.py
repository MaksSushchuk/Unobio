"""Single place for all non-code locations (inputs, outputs, generated artifacts).

server/data/
  fixtures/        hand-made evidence bundles for development     (committed)
  schema/          JSON Schemas generated from the Pydantic models (committed)
  runs/<run_id>/   runtime artifacts of one run                    (git-ignored)
  cache/           HTTP / LLM response caches                      (git-ignored)

Override the root with the UNOBIO_DATA_DIR environment variable.
"""
from __future__ import annotations

import os
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("UNOBIO_DATA_DIR") or SERVER_DIR / "data")

FIXTURES_DIR = DATA_DIR / "fixtures"
SCHEMA_DIR = DATA_DIR / "schema"
RUNS_DIR = DATA_DIR / "runs"
CACHE_DIR = DATA_DIR / "cache"


def run_dir(run_id: str) -> Path:
    """runs/<run_id>/ — evidence_bundle.json, analysis_result.json, report.json, trace.jsonl, context/."""
    path = RUNS_DIR / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path
