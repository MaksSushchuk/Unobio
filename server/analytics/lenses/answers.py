"""Raw answer models: exactly what each lens LLM must return (JSON Schema is generated from these).

They use SHORT ids (E7, A1) — the ids the model saw in its context. `mapping.py`
converts them to full evidence ids for analysis_result.json.

Keep these models small and flat: small local models follow simple schemas much
more reliably than deeply nested ones.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Requires = Literal["proprietary_data", "patient_data", "kol", "experiment", "cmc_ip"]


class RawRef(BaseModel):
    id: str = Field(description="Evidence id as shown in the context, e.g. E7 or A1")
    stance: Literal["supports", "contradicts"] = Field(description="Does this evidence support or contradict the claim?")


class RawClaim(BaseModel):
    text: str = Field(min_length=10, max_length=400, description="One precise statement")
    kind: Literal["source_fact", "inference"]
    confidence: float = Field(ge=0, le=1)
    evidence: list[RawRef] = Field(min_length=1, max_length=6)


class RawUnknown(BaseModel):
    question: str = Field(min_length=10, max_length=300)
    requires: Requires
    why: str = Field(default="", max_length=300)


class LensAnswer(BaseModel):
    """Fields common to every lens."""

    score: int = Field(ge=0, le=5, description="0 = kills the thesis ... 5 = strongly supports it (see rubric)")
    score_rationale: str = Field(min_length=20, max_length=600, description="Why this score; cite ids inline like [E3]")
    claims: list[RawClaim] = Field(min_length=2, max_length=8)
    counter_evidence: str = Field(max_length=600, description="Strongest evidence AGAINST your conclusion and why it does or does not change the score")
    unknowns: list[RawUnknown] = Field(default=[], max_length=6)
    need_detail: list[str] = Field(default=[], max_length=8, description="Ids shown as titles only / not shown that you need in full; [] if none")


# ----------------------------------------------------------------------------- lens-specific additions


class ChainLink(BaseModel):
    step: Literal["molecular_effect", "human_exposure", "target_engagement", "biological_response", "patient_benefit"]
    status: Literal["supported", "weak", "missing", "contradicted"]
    evidence: list[str] = Field(default=[], description="Ids supporting the status; [] if missing")


class ScienceAnswer(LensAnswer):
    causality: Literal["causal_human", "causal_preclinical", "correlative", "contradicted", "unknown"]
    translation_chain: list[ChainLink] = Field(min_length=5, max_length=5,
                                               description="Exactly the 5 steps, in order")


class DevelopmentPlan(BaseModel):
    population: str
    primary_endpoint: str
    comparator: str
    biomarker_strategy: str
    phase_sequence: str = Field(description="e.g. 'Ph1b in patients -> Ph2 PoC (n~120, 12 wk) -> Ph3'")
    analogues: str = Field(description="Closest prior programs and what they imply, with ids")


class ClinicalAnswer(LensAnswer):
    development_plan: DevelopmentPlan


class MarketView(BaseModel):
    standard_of_care: str
    unmet_need: str
    addressable_patients: str = Field(description="Range with basis, e.g. '150-250k US moderate-severe [E12] (inference)'")
    pricing_analogues: str
    differentiation_required: str
    scientific_vs_commercial: str = Field(description="Is it scientifically interesting but commercially weak, or vice versa?")


class MarketAnswer(LensAnswer):
    market: MarketView


class TrialStep(BaseModel):
    phase: Literal["PHASE1", "PHASE1B", "PHASE2", "PHASE2B", "PHASE3"]
    patients_low: int = Field(ge=0)
    patients_high: int = Field(ge=0)
    months_low: int = Field(ge=0)
    months_high: int = Field(ge=0)


class InvestmentParams(BaseModel):
    current_stage: Literal["discovery", "preclinical", "phase1", "phase2", "phase3", "filed", "approved"]
    next_milestone: str = Field(description="e.g. 'Phase 2 proof-of-concept readout'")
    trials_to_milestone: list[TrialStep] = Field(min_length=1, max_length=4)
    peak_sales_usd_low: float = Field(ge=0)
    peak_sales_usd_base: float = Field(ge=0)
    peak_sales_usd_high: float = Field(ge=0)
    exit_options: str = Field(description="Licensing / acquisition / partnering scenarios and value inflection points")


class InvestmentAnswer(LensAnswer):
    params: InvestmentParams
