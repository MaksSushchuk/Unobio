"""The final Report: a 1:1 mirror of web/src/types.ts (snake_case, same optional fields, same enums).

Taken from the teammate's v1.0.0 orchestrator (orchestrator/report_schema.py). Here it guards the contract:
app/report.py builds a dict with a few extra keys the UI ignores (`details`, `warnings`, `downloads`);
`validate_report` checks everything else against this model.

extra="forbid" everywhere, so a field the frontend does not know cannot slip in. Optional TS fields
(`modality?`, `summary?`, `related_evidence_ids?`, `tool?`) are omitted from the JSON when unset;
`previous_run_id` is always present (string or null). Serialize with `Report.to_json_dict()`.
tests/test_app.py validates our reports and the frontend fixtures (web/src/fixtures/*.json) against this model.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Recommendation = Literal["invest", "conditional", "do_not_invest"]
ClaimKind = Literal["source_fact", "inference"]
Stance = Literal["supports", "contradicts"]
EvidenceKind = Literal["record", "literature", "web", "conflict"]
Severity = Literal["low", "medium", "high"]
DiligenceRequirement = Literal["proprietary_data", "patient_data", "kol", "experiment", "cmc_ip"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportInput(_M):
    indication: str
    mechanism: str
    modality: str | None = None
    stage: str | None = None
    biomarkers: list[str] | None = None
    route: str | None = None


class EvidenceRef(_M):
    evidence_id: str
    stance: Stance


class Claim(_M):
    id: str
    text: str
    kind: ClaimKind
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceRef]


class Section(_M):
    id: str
    title: str
    summary: str | None = None
    claims: list[Claim]


class Evidence(_M):
    id: str
    source: str
    url: str
    title: str
    snippet: str
    kind: EvidenceKind
    modules: list[str]
    retrieved_at: str
    related_evidence_ids: list[str] | None = None


class Risk(_M):
    id: str
    title: str
    severity: Severity
    description: str
    claim_ids: list[str]


class DiligenceQuestion(_M):
    question: str
    rationale: str
    requires: DiligenceRequirement


class CapitalEstimate(_M):
    milestone: str
    months_low: float
    months_high: float
    usd_low: float
    usd_base: float
    usd_high: float
    assumptions: list[str]


class Trace(_M):
    agent: str
    step: str
    tool: str | None = None
    input_tokens: int
    output_tokens: int
    latency_s: float
    cost_usd: float


class Report(_M):
    id: str
    created_at: str
    is_mock: bool
    previous_run_id: str | None
    input: ReportInput
    recommendation: Recommendation
    confidence: float = Field(ge=0, le=1)
    summary: str
    sections: list[Section]
    risks: list[Risk]
    unknowns: list[str]
    diligence_questions: list[DiligenceQuestion]
    capital: CapitalEstimate
    evidence: list[Evidence]
    traces: list[Trace]

    def to_json_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json", exclude_none=True)
        d["previous_run_id"] = self.previous_run_id  # required key, may be null
        return d


EXTRA_KEYS = ("details", "warnings", "downloads")


def validate_report(report: dict[str, Any]) -> Report:
    """Raise pydantic.ValidationError if `report` (minus our extra keys) does not match web/src/types.ts."""
    return Report.model_validate({k: v for k, v in report.items() if k not in EXTRA_KEYS})
