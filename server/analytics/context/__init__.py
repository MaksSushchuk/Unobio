"""Step 2 — context preparation (no LLM). See analytics/README.md, section "Context"."""
from .builder import ContextBuilder, ContextConfig, LensContext, RunContext
from .routing import LENS_MODULES, lenses_for
from .short_ids import ShortIdMap

__all__ = ["ContextBuilder", "ContextConfig", "LensContext", "RunContext", "LENS_MODULES", "lenses_for", "ShortIdMap"]
