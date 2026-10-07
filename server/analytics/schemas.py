"""Contract: analytics -> writer (and UI).

ONE file per run: `runs/<run_id>/analysis_result.json`.
Analytics WRITES it, writer READS it (together with evidence_bundle.json for the
evidence texts/URLs). Field names are snake_case and align with web/src/types.ts
(Claim, Risk, DiligenceQuestion, CapitalEstimate, Trace) so the writer mostly copies.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

Lens = Literal["science", "clinical", "market", "investment"]
LENSES: tuple[Lens, ...] = ("science", "clinical", "market", "investment")
Recommendation = Literal["Invest", "Conditional", "Do Not Invest"]
Requires = Literal["proprietary_data", "patient_data", "kol", "experiment", "cmc_ip"]
Severity = Literal["high", "medium", "low"]


def claim_id(lens: str, text: str) -> str:
    """Deterministic id: same lens + same normalized text -> same id across runs."""
    norm = " ".join(text.lower().split())
    return "C" + hashlib.sha1(f"{lens}|{norm}".encode()).hexdigest()[:10]


# ----------------------------------------------------------------------------- lens output


class EvidenceRef(BaseModel):
    evidence_id: str = Field(description="Evidence.id from evidence_bundle.json (full id, not the short E-number)")
    stance: Literal["supports", "contradicts"] = "supports"


class Claim(BaseModel):
    id: str
    lens: Lens
    text: str
    kind: Literal["source_fact", "inference"] = Field(
        description="source_fact = directly stated by cited evidence; inference = AI conclusion drawn from cited evidence"
    )
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceRef] = Field(min_length=1, description="Every claim must cite >= 1 evidence")


class Unknown(BaseModel):
    question: str
    requires: Requires
    why: str = ""


class LensResult(BaseModel):
    lens: Lens
    status: Literal["ok", "failed", "skipped"] = "ok"
    score: int | None = Field(default=None, ge=0, le=5, description="0 = kills the thesis, 5 = strongly supports")
    score_rationale: str = ""
    claims: list[Claim] = []
    unknowns: list[Unknown] = []
    counter_evidence: str = Field(default="", description="Evidence against the lens' own conclusion and why it does/doesn't change the score")
    params: dict[str, Any] = Field(default={}, description="Lens-specific structured outputs (e.g. investment: phase, trial size, peak sales range)")
    errors: list[str] = []


# ----------------------------------------------------------------------------- decision outputs


class Risk(BaseModel):
    id: str
    title: str
    severity: Severity
    description: str
    claim_ids: list[str] = []
    evidence_ids: list[str] = []


class DiligenceQuestion(BaseModel):
    question: str
    rationale: str
    requires: Requires


class CapitalEstimate(BaseModel):
    milestone: str
    months_low: int
    months_high: int
    usd_low: float
    usd_base: float
    usd_high: float
    assumptions: list[str]


class RNPV(BaseModel):
    usd_low: float
    usd_base: float
    usd_high: float
    probability_of_success: float = Field(description="Cumulative PoS from current stage to approval")
    assumptions: list[str]


class Verdict(BaseModel):
    recommendation: Recommendation
    confidence: float = Field(ge=0, le=1)
    kill_triggered: bool = False
    kill_evidence_ids: list[str] = []
    lens_scores: dict[str, int | None] = {}
    composite_score: float | None = Field(default=None, description="Weighted mean of lens scores (0-5)")
    rule_trace: list[str] = Field(default=[], description="Human-readable log of which rules fired (for the UI / judges)")


class AgentTrace(BaseModel):
    agent: str
    step: str
    model: str | None = None
    attempt: int = 1
    ok: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    error: str | None = None


# ----------------------------------------------------------------------------- top level


class AnalysisResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    bundle_run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0))
    input: dict[str, Any]
    lenses: dict[Lens, LensResult]
    verdict: Verdict
    capital: CapitalEstimate | None = None
    rnpv: RNPV | None = None
    risks: list[Risk] = []
    unknowns: list[Unknown] = []
    diligence_questions: list[DiligenceQuestion] = []
    short_ids: dict[str, str] = Field(
        default={}, description="E-number -> evidence id, to resolve inline refs like [E3] in rationale texts")
    traces: list[AgentTrace] = []

    @property
    def total_cost_usd(self) -> float:
        return round(sum(t.cost_usd for t in self.traces), 6)
