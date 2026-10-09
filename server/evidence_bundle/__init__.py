"""Researcher -> analytics contract (see README.md in this folder)."""
import json
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

from .adapters import from_researcher, is_researcher_bundle
from .paths import CACHE_DIR, DATA_DIR, FIXTURES_DIR, RUNS_DIR, SCHEMA_DIR, run_dir


def load_bundle(path: str | Path) -> EvidenceBundle:
    """Read + validate a bundle. Accepts both the analytics contract and the researcher's own
    bundle format (converted by `adapters.from_researcher`). Raises pydantic.ValidationError
    on contract violation."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return bundle_from_dict(raw)


def bundle_from_dict(raw: dict) -> EvidenceBundle:
    if is_researcher_bundle(raw):
        return from_researcher(raw)
    return EvidenceBundle.model_validate(raw)


__all__ = [
    "SCHEMA_VERSION", "DiseaseRef", "DrugRef", "Evidence", "EvidenceBundle", "Gap", "Module",
    "ResearchInput", "SourceStatus", "Subject", "TargetRef", "DATA_DIR", "FIXTURES_DIR", "SCHEMA_DIR", "RUNS_DIR", "CACHE_DIR", "run_dir", "load_bundle", "bundle_from_dict", "from_researcher", "is_researcher_bundle",
]
