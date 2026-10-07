"""Regenerate data/schema/evidence_bundle.schema.json from models.py:  python -m evidence_bundle.export_schema"""
import json

from .models import EvidenceBundle
from .paths import SCHEMA_DIR

if __name__ == "__main__":
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    out = SCHEMA_DIR / "evidence_bundle.schema.json"
    out.write_text(json.dumps(EvidenceBundle.model_json_schema(), indent=2), encoding="utf-8")
    print(f"written {out}")
