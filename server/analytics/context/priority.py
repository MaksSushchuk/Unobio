"""Priority tiers: what an analyst must see first when the context budget is tight.

P0  must-see, full card      conflicts + the lens' "anchor" evidence (see ANCHORS)
P1  important, full card     evidence from the lens' own (primary) modules
P2  supporting, one line     evidence that reached the lens only via a shared/secondary module
P3  dropped                  whatever does not fit the budget (only counted)

The tiers are deterministic rules, not LLM judgement: same bundle -> same context.
"""
from __future__ import annotations

from typing import Callable

from evidence_bundle import Evidence

from ..schemas import Lens
from .routing import is_primary


def _same_target_trial(e: Evidence) -> bool:
    return e.source == "clinicaltrials" and e.data.get("role") == "same_target"


def _association_or_genetics(e: Evidence) -> bool:
    return e.source == "opentargets" and ("has_genetic_evidence" in e.data or e.data.get("assumed_time_stable"))


def _benchmark(e: Evidence) -> bool:
    return bool(e.data.get("benchmark"))


def _landscape_summary(e: Evidence) -> bool:
    return e.source == "opentargets" and "n_drugs" in e.data


# Evidence a lens cannot reason without, regardless of budget.
ANCHORS: dict[Lens, list[Callable[[Evidence], bool]]] = {
    "science": [_association_or_genetics, _same_target_trial],
    "clinical": [_same_target_trial, _benchmark],
    "market": [_landscape_summary],
    "investment": [_benchmark],
}

P0, P1, P2 = 0, 1, 2


def tier(e: Evidence, lens: Lens) -> int:
    if e.kind == "conflict" or any(rule(e) for rule in ANCHORS[lens]):
        return P0
    if is_primary(e, lens):
        return P1
    return P2


def sort_key(e: Evidence) -> tuple:
    """Within a tier: higher severity, then red-flag evidence, then records before
    literature, then newest first. Puts the facts that can change the verdict first,
    so they survive a tight budget."""
    severity = {"high": 0, "medium": 1, "low": 2, "info": 3}.get(str(e.data.get("severity")), 4)
    red_flag = 0 if "red-flags" in e.modules else 1
    placeholder = 1 if e.data.get("synthetic") else 0
    kind = {"conflict": 0, "record": 1, "literature": 2, "web": 3}.get(e.kind, 9)
    newest_first = -(e.published_at.toordinal() if e.published_at else 0)
    return (severity, red_flag, placeholder, kind, newest_first, e.id)
