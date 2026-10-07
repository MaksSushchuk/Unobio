"""Step 5 — the four analyst lenses (science, clinical, market, investment)."""
from .answers import ClinicalAnswer, InvestmentAnswer, LensAnswer, MarketAnswer, ScienceAnswer
from .definitions import LENS_DEFS, LensDef
from .fake import fake_lens_llm
from .run import LensRun, lens_spec, lens_user_prompt, run_lens, run_lenses
from .validation import make_validator

__all__ = [
    "ClinicalAnswer", "InvestmentAnswer", "LensAnswer", "MarketAnswer", "ScienceAnswer", "LENS_DEFS", "LensDef",
    "fake_lens_llm", "LensRun", "lens_spec", "lens_user_prompt", "run_lens", "run_lenses", "make_validator",
]
