"""Which lens (analyst) sees which evidence.

Researcher tags every evidence with Unobio `modules`. Each lens owns a set of
modules (its *primary* modules). `red-flags` is shared: every lens must see the
warnings, so it can weigh evidence against its own conclusion.
"""
from __future__ import annotations

from evidence_bundle import Evidence

from ..schemas import LENSES, Lens

LENS_MODULES: dict[Lens, frozenset[str]] = {
    "science": frozenset({"scientific-evidence"}),
    "clinical": frozenset({"trial-design", "pipeline", "green-flags"}),
    "market": frozenset({"unmet-need", "patient-population", "competitive-landscape", "market-sentiment"}),
    "investment": frozenset({"patent-ip", "manufacturing"}),
}
SHARED_MODULES: frozenset[str] = frozenset({"red-flags"})
# Seen as one-line context only. The investment lens prices a program, so it needs to
# know the pipeline and competition, but does not need to re-analyse them in depth.
SECONDARY_MODULES: dict[Lens, frozenset[str]] = {
    "investment": frozenset({"pipeline", "competitive-landscape", "trial-design"}),
}


def is_primary(e: Evidence, lens: Lens) -> bool:
    """True if the evidence belongs to the lens' own modules (not only via a shared module)."""
    if lens == "investment" and e.data.get("benchmark"):
        return True  # trial size / duration feed the capital estimate
    return bool(set(e.modules) & LENS_MODULES[lens])


def lenses_for(e: Evidence) -> list[Lens]:
    """All lenses that should receive this evidence."""
    mods = set(e.modules)
    shared = bool(mods & SHARED_MODULES)
    return [
        lens for lens in LENSES
        if shared or is_primary(e, lens) or mods & SECONDARY_MODULES.get(lens, frozenset())
    ]
