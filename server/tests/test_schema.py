import json
from pathlib import Path

from researcher.schema import Bundle, evidence_id

EXAMPLE = Path(__file__).resolve().parent.parent / "evidence_bundle" / "examples" / "il17_crohns.json"


def test_evidence_id_is_deterministic():
    assert evidence_id("pubmed", "22595313") == evidence_id("pubmed", "22595313")
    assert evidence_id("pubmed", "22595313") != evidence_id("pubmed", "27481309")
    assert len(evidence_id("x", "y")) == 16


def test_example_bundle_validates():
    bundle = Bundle.model_validate(json.loads(EXAMPLE.read_text()))
    assert bundle.is_example
    kinds = {e.kind for e in bundle.evidence}
    assert "conflict" in kinds
    assert any(e.data.get("overallStatus") == "TERMINATED" for e in bundle.evidence)


def test_example_references_resolve():
    bundle = Bundle.model_validate(json.loads(EXAMPLE.read_text()))
    ids = {e.id for e in bundle.evidence}
    assert len(ids) == len(bundle.evidence)
    for e in bundle.evidence:
        assert set(e.related_evidence_ids) <= ids
    for c in bundle.coverage:
        assert set(c.evidence_ids) <= ids
