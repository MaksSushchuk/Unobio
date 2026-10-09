"""Raw lens answer (short ids) -> LensResult (full evidence ids) for analysis_result.json."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..context import RunContext
from ..schemas import Claim, EvidenceRef, Lens, LensResult, Unknown, claim_id
from .answers import LensAnswer, ScienceAnswer
from .validation import norm

COMMON_FIELDS = set(LensAnswer.model_fields)
# An aggregate (e.g. "A1: 100 landscape trials") cited by a claim expands to at most this many members;
# expanding to all of them turned every claim into a 100-source citation.
MAX_AGGREGATE_MEMBERS = 5
SKIPPED_REASON = "no public evidence was routed to this lens"


def to_lens_result(lens: Lens, answer: LensAnswer, ctx: RunContext) -> LensResult:
    claims: list[Claim] = []
    seen: set[str] = set()
    for c in answer.claims:
        refs: list[EvidenceRef] = []
        for r in c.evidence:
            for full in ctx.resolve(norm(r.id))[:MAX_AGGREGATE_MEMBERS]:  # an aggregate expands to its first members
                if full not in {x.evidence_id for x in refs}:
                    refs.append(EvidenceRef(evidence_id=full, stance=r.stance))
        cid = claim_id(lens, c.text)
        if cid in seen or not refs:
            continue
        seen.add(cid)
        claims.append(Claim(id=cid, lens=lens, text=c.text, kind=c.kind, confidence=c.confidence, evidence=refs))

    return LensResult(
        lens=lens,
        status="ok",
        score=answer.score,
        score_rationale=answer.score_rationale,
        claims=claims,
        unknowns=[Unknown(question=u.question, requires=u.requires, why=u.why) for u in answer.unknowns],
        counter_evidence=answer.counter_evidence,
        params=_lens_params(answer, ctx),
    )


def _lens_params(answer: LensAnswer, ctx: RunContext) -> dict[str, Any]:
    """Lens-specific fields (everything beyond the common ones), with chain ids resolved to full ids."""
    extra = {k: v for k, v in answer.model_dump().items() if k not in COMMON_FIELDS}
    if isinstance(answer, ScienceAnswer):
        extra["translation_chain"] = [
            {"step": link.step, "status": link.status,
             "evidence_ids": [full for ref in link.evidence for full in ctx.resolve(norm(ref))]}
            for link in answer.translation_chain
        ]
    if "params" in extra and isinstance(extra["params"], dict):  # investment: flatten one level
        extra = {**extra.pop("params"), **extra}
    return extra


def failed_result(lens: Lens, errors: list[str]) -> LensResult:
    return LensResult(lens=lens, status="failed", errors=errors[:10])


def skipped_result(lens: Lens) -> LensResult:
    """No evidence for the lens: no LLM call, the gap itself becomes a critical unknown."""
    from .definitions import LENS_DEFS

    title = LENS_DEFS[lens].title
    return LensResult(lens=lens, status="skipped", errors=[SKIPPED_REASON], unknowns=[Unknown(
        question=f"{title}: no public evidence was found for this indication and mechanism",
        requires="proprietary_data",
        why="The lens could not be assessed from public sources; the verdict cannot rely on it.",
    )])


def as_dict(model: BaseModel | None) -> dict[str, Any]:
    return model.model_dump() if model is not None else {}
