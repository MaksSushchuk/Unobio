"""Researcher -> analytics contract (see README.md in this folder)."""
from pathlib import Path

from .models import (
    SCHEMA_VERSION,
    DiseaseRef,
    DrugRef,
    Evidence,
    EvidenceBundle,
    Gap,
    Module,
    ResearchInput,
    SourceStatus,
    Subject,
    TargetRef,
)

from .paths import CACHE_DIR, DATA_DIR, FIXTURES_DIR, RUNS_DIR, SCHEMA_DIR, run_dir


def load_bundle(path: str | Path) -> EvidenceBundle:
    """Read + validate a bundle. Raises pydantic.ValidationError on contract violation."""
    return EvidenceBundle.model_validate_json(Path(path).read_text(encoding="utf-8"))


__all__ = [
    "SCHEMA_VERSION", "DiseaseRef", "DrugRef", "Evidence", "EvidenceBundle", "Gap", "Module",
    "ResearchInput", "SourceStatus", "Subject", "TargetRef", "DATA_DIR", "FIXTURES_DIR", "SCHEMA_DIR", "RUNS_DIR", "CACHE_DIR", "run_dir", "load_bundle",
]
