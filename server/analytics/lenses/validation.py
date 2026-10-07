"""Semantic checks for a lens answer (after the Pydantic schema check passed).

Rules
1. Every cited id exists in the run (E… or A…).
2. Every cited id was actually SHOWN to this lens (full card, one-liner or aggregate).
   Ids listed only under "NOT SHOWN" must be requested via `need_detail` first.
3. Every KILL SIGNAL conflict shown to the lens is cited by at least one claim —
   the analyst must engage with the decisive evidence, not ignore it.
4. Numeric ranges are ordered (low <= high).

Errors are short, imperative sentences: they are sent back to the model on retry.
"""
from __future__ import annotations

from typing import Callable

from evidence_bundle import EvidenceBundle

from ..context import RunContext
from ..schemas import Lens
from .answers import InvestmentAnswer, LensAnswer, ScienceAnswer


def norm(ref: str) -> str:
    return ref.strip().strip("[]").upper()


def kill_short_ids(bundle: EvidenceBundle, ctx: RunContext, lens: Lens) -> list[str]:
    kill = {e.id for e in bundle.evidence if e.kind == "conflict" and e.data.get("kill_signal")}
    return [s for s in ctx.lenses[lens].conflict_ids if ctx.resolve(s) and ctx.resolve(s)[0] in kill]


def allowed_ids(ctx: RunContext, lens: Lens, extra: set[str] | None = None) -> set[str]:
    lc = ctx.lenses[lens]
    return set(lc.full_ids) | set(lc.brief_ids) | set(lc.aggregate_ids) | (extra or set())


def make_validator(bundle: EvidenceBundle, ctx: RunContext, lens: Lens,
                   extra_allowed: set[str] | None = None) -> Callable[[LensAnswer], list[str]]:
    allowed = allowed_ids(ctx, lens, extra_allowed)
    must_cite = kill_short_ids(bundle, ctx, lens)

    def check_ref(ref: str, where: str, errors: list[str]) -> None:
        r = norm(ref)
        if not ctx.resolve(r):
            errors.append(f"{where} cites unknown id {ref!r}. Use only ids shown in square brackets in the context.")
        elif r not in allowed:
            errors.append(f"{where} cites {r}, which is not in your context. Request it via need_detail instead.")

    def validate(answer: LensAnswer) -> list[str]:
        errors: list[str] = []
        cited: set[str] = set()
        for i, claim in enumerate(answer.claims):
            for ref in claim.evidence:
                check_ref(ref.id, f"claims[{i}]", errors)
                cited.add(norm(ref.id))
        for kid in must_cite:
            if kid not in cited:
                errors.append(f"{kid} is a KILL SIGNAL; at least one claim must cite it (supports or contradicts).")
        if isinstance(answer, ScienceAnswer):
            for link in answer.translation_chain:
                for ref in link.evidence:
                    check_ref(ref, f"translation_chain.{link.step}", errors)
            steps = [link.step for link in answer.translation_chain]
            if len(set(steps)) != 5:
                errors.append("translation_chain must contain each of the 5 steps exactly once.")
        if isinstance(answer, InvestmentAnswer):
            p = answer.params
            if not p.peak_sales_usd_low <= p.peak_sales_usd_base <= p.peak_sales_usd_high:
                errors.append("params: peak sales must satisfy low <= base <= high.")
            for j, t in enumerate(p.trials_to_milestone):
                if t.patients_low > t.patients_high or t.months_low > t.months_high:
                    errors.append(f"params.trials_to_milestone[{j}]: low must be <= high.")
        return errors

    return validate
