"""Contracts: example bundles parse, JSON schema is in sync, analysis contract is sane."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from analytics.schemas import AnalysisResult, Claim, LensResult, Verdict, claim_id
from evidence_bundle import FIXTURES_DIR, SCHEMA_DIR, load_bundle
from evidence_bundle.models import EvidenceBundle

EXAMPLES = sorted(FIXTURES_DIR.glob("*.json"))
SCHEMA = SCHEMA_DIR / "evidence_bundle.schema.json"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_bundles_are_valid(path):
    b = load_bundle(path)
    ids = [e.id for e in b.evidence]
    assert len(ids) == len(set(ids)), "evidence ids must be unique"
    for c in b.conflicts():
        assert c.related_evidence_ids and set(c.related_evidence_ids) <= set(ids)
        assert {"rule", "severity", "kill_signal"} <= set(c.data)


def test_cutoff_example_has_no_future_facts():
    b = load_bundle(FIXTURES_DIR / "il17_crohn_2011.json")
    cutoff = b.input.evidence_cutoff
    assert cutoff is not None
    assert all(e.published_at is None or e.published_at <= cutoff for e in b.evidence)
    assert not b.conflicts()


def test_json_schema_in_sync():
    assert json.loads(SCHEMA.read_text()) == EvidenceBundle.model_json_schema(), \
        "run: python -m evidence_bundle.export_schema"


def test_claim_requires_evidence():
    with pytest.raises(ValidationError):
        Claim(id="C1", lens="science", text="x", kind="inference", confidence=0.5, evidence=[])


def test_claim_id_is_deterministic():
    assert claim_id("science", "IL-17A  blockade failed") == claim_id("science", "il-17a blockade failed")


def test_analysis_result_roundtrip():
    r = AnalysisResult(
        run_id="r1", bundle_run_id="b1", input={"indication": "x", "mechanism": "y"},
        lenses={"science": LensResult(lens="science", score=1)},
        verdict=Verdict(recommendation="Do Not Invest", confidence=0.8, kill_triggered=True),
    )
    assert AnalysisResult.model_validate_json(r.model_dump_json()) == r
