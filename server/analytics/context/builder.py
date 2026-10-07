"""ContextBuilder: evidence_bundle -> one compact, budgeted text context per lens.

Pipeline for each lens (pure code, no LLM):

  1. route      keep only evidence routed to this lens          (routing.py)
  2. tier       P0 must-see / P1 primary / P2 shared             (priority.py)
  3. aggregate  collapse big homogeneous groups into one card    (AGGREGATES below)
  4. budget     fill P0 -> P1 -> P2 until the token budget is spent;
                P1 that does not fit is downgraded to a one-line card;
                the rest is listed by id only ("not shown")
  5. render     header (shared across lenses) + evidence section

The output is plain text + bookkeeping (which ids were shown how), so the
validator and the UI can tell exactly what each analyst saw.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from evidence_bundle import Evidence, EvidenceBundle

from ..schemas import LENSES, Lens
from .cards import aggregate_card, brief_card, full_card, subject_header
from .priority import P0, P1, P2, sort_key, tier
from .routing import lenses_for
from .short_ids import ShortIdMap


@dataclass
class ContextConfig:
    budget_tokens: int = 8000  # evidence section per lens; header is extra (shared, cacheable)
    chars_per_token: float = 4.0  # rough estimate for English text; good enough for budgeting
    snippet_limit: int = 700  # max chars of a snippet in a full card
    brief_limit: int = 140  # max chars of a title in a one-line card
    aggregate_min: int = 5  # collapse a group into one card when it has at least this many items

    def tokens(self, text: str) -> int:
        return math.ceil(len(text) / self.chars_per_token)


# Homogeneous groups that are cheaper as one card: (title, membership rule)
AGGREGATES: list[tuple[str, Callable[[Evidence], bool]]] = [
    ("Active trials of competing programs in the indication",
     lambda e: e.source == "clinicaltrials" and e.data.get("role") == "landscape"),
    ("Approved therapies (standard of care) in the indication",
     lambda e: e.data.get("role") == "approved"),
]


class LensContext(BaseModel):
    lens: Lens
    text: str  # evidence section only (prepend RunContext.header for the full prompt context)
    evidence_tokens: int
    full_ids: list[str]  # short ids rendered as full cards
    brief_ids: list[str]  # short ids rendered as one-liners
    aggregate_ids: list[str]  # A-ids rendered (members listed inside)
    dropped_ids: list[str]  # routed to the lens but not shown (budget)
    conflict_ids: list[str]
    p0_overflow: bool = False  # True if must-see evidence alone exceeded the budget

    @property
    def shown_ids(self) -> list[str]:
        return self.full_ids + self.brief_ids


class RunContext(BaseModel):
    bundle_run_id: str
    header: str
    header_tokens: int
    lenses: dict[Lens, LensContext]
    id_map: dict  # {"evidence": {"E1": full_id}, "aggregates": {"A1": [full ids]}}

    def prompt_context(self, lens: Lens) -> str:
        return f"{self.header}\n\n{self.lenses[lens].text}"

    def resolve(self, ref: str) -> list[str]:
        """Short id -> full evidence ids: 'E7' -> [id]; 'A1' -> [member ids]; unknown -> []."""
        ref = ref.strip().strip("[]").upper()
        if ref in self.id_map["evidence"]:
            return [self.id_map["evidence"][ref]]
        return list(self.id_map["aggregates"].get(ref, []))

    def short_of(self, evidence_id: str) -> str | None:
        for short, full in self.id_map["evidence"].items():
            if full == evidence_id:
                return short
        return None


@dataclass
class _Unit:
    tier: int
    key: tuple
    kind: str  # "full" | "brief" | "agg"
    evidence: list[Evidence]
    title: str = ""


class ContextBuilder:
    def __init__(self, config: ContextConfig | None = None) -> None:
        self.cfg = config or ContextConfig()

    def build(self, bundle: EvidenceBundle) -> RunContext:
        ids = ShortIdMap(bundle.evidence)
        header = subject_header(bundle)
        lenses: dict[Lens, LensContext] = {lens: self._build_lens(bundle, lens, ids) for lens in LENSES}
        return RunContext(
            bundle_run_id=bundle.run_id,
            header=header,
            header_tokens=self.cfg.tokens(header),
            lenses=lenses,
            id_map=ids.as_dict(),
        )

    # ------------------------------------------------------------------ per lens

    def _build_lens(self, bundle: EvidenceBundle, lens: Lens, ids: ShortIdMap) -> LensContext:
        routed = [e for e in bundle.evidence if lens in lenses_for(e)]
        units = self._units(routed, lens, ids)

        used, overflow = 0, False
        sections: dict[int, list[str]] = {P0: [], P1: [], P2: []}
        full_ids: list[str] = []
        brief_ids: list[str] = []
        agg_ids: list[str] = []
        dropped: list[str] = []

        for u in sorted(units, key=lambda u: (u.tier, u.key)):
            text, shown_as = self._render(u, ids, compact=False)
            cost = self.cfg.tokens(text) + 1
            if used + cost > self.cfg.budget_tokens and u.tier != P0:
                # degrade before dropping: full -> brief, aggregate -> ids only
                text, shown_as = self._render(u, ids, compact=True)
                cost = self.cfg.tokens(text) + 1
                if used + cost > self.cfg.budget_tokens:
                    dropped += [ids.short(e.id) for e in u.evidence]
                    continue
            if u.tier == P0 and used + cost > self.cfg.budget_tokens:
                overflow = True  # must-see evidence is never dropped
            used += cost
            section = P2 if shown_as == "brief" else u.tier
            sections[section].append(text)
            if shown_as == "full":
                full_ids += [ids.short(e.id) for e in u.evidence]
            elif shown_as == "brief":
                brief_ids += [ids.short(e.id) for e in u.evidence]
            else:
                agg_ids.append(u.title.split("|", 1)[0])
                full_ids += [ids.short(e.id) for e in u.evidence]

        text = self._assemble(sections, dropped)
        return LensContext(
            lens=lens,
            text=text,
            evidence_tokens=self.cfg.tokens(text),
            full_ids=full_ids,
            brief_ids=brief_ids,
            aggregate_ids=agg_ids,
            dropped_ids=dropped,
            conflict_ids=[ids.short(e.id) for e in routed if e.kind == "conflict"],
            p0_overflow=overflow,
        )

    def _units(self, routed: list[Evidence], lens: Lens, ids: ShortIdMap) -> list[_Unit]:
        units: list[_Unit] = []
        taken: set[str] = set()
        for title, rule in AGGREGATES:
            members = [e for e in routed if rule(e) and tier(e, lens) != P0 and e.id not in taken]
            if len(members) >= self.cfg.aggregate_min:
                members.sort(key=sort_key)
                agg_id = ids.add_aggregate([m.id for m in members])
                units.append(_Unit(P1, sort_key(members[0]), "agg", members, title=f"{agg_id}|{title}"))
                taken |= {m.id for m in members}
        for e in routed:
            if e.id in taken:
                continue
            t = tier(e, lens)
            units.append(_Unit(t, sort_key(e), "brief" if t == P2 else "full", [e]))
        return units

    def _render(self, u: _Unit, ids: ShortIdMap, compact: bool) -> tuple[str, str]:
        if u.kind == "agg":
            agg_id, title = u.title.split("|", 1)
            if compact:
                members = ", ".join(ids.short(e.id) for e in u.evidence)
                return f"[{agg_id}] AGGREGATE · {title}: {members}", "agg"
            return aggregate_card(agg_id, title, u.evidence, ids, self.cfg.brief_limit), "agg"
        e = u.evidence[0]
        if u.kind == "brief" or compact:
            return brief_card(e, ids, self.cfg.brief_limit), "brief"
        return full_card(e, ids, self.cfg.snippet_limit), "full"

    @staticmethod
    def _assemble(sections: dict[int, list[str]], dropped: list[str]) -> str:
        parts = []
        if sections[P0]:
            parts.append("## MUST-READ (conflicts and key evidence)\n" + "\n".join(sections[P0]))
        if sections[P1]:
            parts.append("## EVIDENCE\n" + "\n".join(sections[P1]))
        if sections[P2]:
            parts.append("## ALSO RELEVANT (titles only — request details via need_detail)\n" + "\n".join(sections[P2]))
        if dropped:
            parts.append(f"## NOT SHOWN ({len(dropped)} more items, request via need_detail): " + ", ".join(dropped))
        if not parts:
            parts.append("## EVIDENCE\n(no evidence routed to this lens — report this as an unknown)")
        return "\n\n".join(parts)
