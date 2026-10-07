"""The four lenses: prompt file, answer model, title and mandatory questions.

Mandatory questions come from the challenge spec ("How to Underwrite Biotech").
They are rendered into the user prompt so every run answers the same questions.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from ..schemas import Lens
from .answers import ClinicalAnswer, InvestmentAnswer, MarketAnswer, ScienceAnswer


@dataclass(frozen=True)
class LensDef:
    lens: Lens
    title: str
    prompt_file: str
    answer_model: type[BaseModel]
    questions: tuple[str, ...]
    max_tokens: int = 2500


LENS_DEFS: dict[Lens, LensDef] = {
    "science": LensDef(
        "science", "Scientific & translational analyst", "lens_science", ScienceAnswer,
        (
            "Is the target–disease link causal (human genetics, perturbation in humans) or only correlative (expression, literature)?",
            "Fill the translation chain: molecular effect -> human exposure -> target engagement -> biological response -> patient benefit. Mark each link supported / weak / missing / contradicted.",
            "Have drugs on this target (or its pathway) shown human proof-of-mechanism anywhere, and does it plausibly transfer to THIS indication?",
            "What evidence contradicts the biology (failed same-target programs, worsening, protective role of the target)?",
        ),
    ),
    "clinical": LensDef(
        "clinical", "Clinical development & regulatory analyst", "lens_clinical", ClinicalAnswer,
        (
            "Which patient population, primary endpoint, comparator and biomarker strategy fit this indication?",
            "What trial sizes and durations are typical here (use the trial benchmarks)?",
            "Which same-target or analogous programs succeeded or failed in this indication, and what does that imply?",
            "What is the development sequence to the next value-inflection milestone?",
        ),
    ),
    "market": LensDef(
        "market", "Market & competition analyst", "lens_market", MarketAnswer,
        (
            "What is the standard of care and the remaining unmet need (refractory, loss-of-response subgroups)?",
            "Which approved and late-stage competitors exist, and what differentiation would a new drug need?",
            "How many patients are addressable, and which pricing analogues apply? (Give ranges; mark estimates as inference.)",
            "Is this scientifically interesting but commercially weak, or the reverse?",
        ),
    ),
    "investment": LensDef(
        "investment", "Investment analyst", "lens_investment", InvestmentAnswer,
        (
            "What is the current stage and the next major value-inflection milestone?",
            "Which trials are needed to reach it (phase, patients, months — as ranges based on the trial benchmarks)?",
            "What peak-sales range is plausible (low / base / high), given competition and pricing?",
            "What licensing / acquisition / partnering outcomes are plausible, and what would kill the investment case?",
        ),
        max_tokens=2000,
    ),
}
