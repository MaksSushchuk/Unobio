"""Short evidence ids for prompts: `E1 … En` (and `A1 … Ak` for aggregate cards).

Why: real ids are 16-hex hashes. LLMs copy short tokens far more reliably and
they cost fewer tokens. The map is GLOBAL for the run (same E-number in every
lens, in the sceptic and in the writer) and DETERMINISTIC (same bundle -> same
numbering), so runs are reproducible and diffs are readable.
"""
from __future__ import annotations

from evidence_bundle import Evidence

_KIND_ORDER = {"conflict": 0, "record": 1, "literature": 2, "web": 3}


class ShortIdMap:
    def __init__(self, evidence: list[Evidence]) -> None:
        ordered = sorted(evidence, key=lambda e: (_KIND_ORDER.get(e.kind, 9), e.source, e.id))
        self._to_short = {e.id: f"E{i}" for i, e in enumerate(ordered, 1)}
        self._to_full = {s: f for f, s in self._to_short.items()}
        self._aggregates: dict[str, list[str]] = {}  # "A1" -> [full evidence ids]

    # ---- evidence ids

    def short(self, evidence_id: str) -> str:
        return self._to_short[evidence_id]

    def has(self, evidence_id: str) -> bool:
        return evidence_id in self._to_short

    def full(self, short_id: str) -> str:
        return self._to_full[short_id]

    # ---- aggregate ids

    def add_aggregate(self, member_ids: list[str]) -> str:
        key = tuple(sorted(member_ids))
        for a, members in self._aggregates.items():
            if tuple(sorted(members)) == key:
                return a
        a = f"A{len(self._aggregates) + 1}"
        self._aggregates[a] = list(member_ids)
        return a

    def aggregate_members(self, agg_id: str) -> list[str]:
        return self._aggregates[agg_id]

    # ---- resolution used by the validator (step 5)

    def resolve(self, ref: str) -> list[str]:
        """'E7' -> [full id]; 'A2' -> [full ids of members]; unknown -> []."""
        ref = ref.strip().upper()
        if ref in self._to_full:
            return [self._to_full[ref]]
        if ref in self._aggregates:
            return list(self._aggregates[ref])
        return []

    def as_dict(self) -> dict[str, object]:
        return {"evidence": dict(self._to_full), "aggregates": dict(self._aggregates)}
