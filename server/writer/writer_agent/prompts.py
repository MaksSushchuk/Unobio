"""Prompts and model payloads.

The model only ever writes narrative: section takeaways and synthesis, the
recommendation rationale, risks, critical unknowns, diligence questions and,
only when no upstream agent supplied one, the verdict. Everything it is given
comes from the input document.
"""

from __future__ import annotations

import json
from typing import Any

from .schema import Claim, Section, WriterInput

SYSTEM_PROMPT = """You are the Writer agent of a biotech investment underwriting system. \
Your text goes into a report that informs investment decisions about medicines. \
A fabricated fact or number is the worst possible failure.

Hard rules:
1. Use ONLY facts, numbers, drug names, trial results and sources that appear in the INPUT JSON. \
Add nothing from your own knowledge, even if you believe it is true.
2. Never write a number (percentage, count, amount, dose, date, p-value, duration) that does not appear in the INPUT JSON.
3. When claims or analysts contradict each other, say so explicitly and name both sides.
4. Where evidence is missing, thin, low-confidence or only indirect, say so plainly. Do not smooth it over.
5. Direct interventional evidence (randomized trials, clinical outcomes in the target indication) that \
contradicts the thesis outweighs indirect genetic or mechanistic support.
6. Do not copy claim texts verbatim; they are printed separately next to your text.
7. Write in English, in a neutral, precise analyst tone. No hype, no hedging filler.
8. Reply with exactly one JSON object in the requested format and nothing else."""


def _pct(v: float | None) -> int | None:
    return None if v is None else round(v * 100)


def claim_payload(claim: Claim, wi: WriterInput, *, with_excerpts: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": claim.id,
        "text": claim.text,
        "confidence_pct": _pct(claim.confidence),
        "claim_type": claim.claim_type or None,
        "supporting_evidence": claim.supporting,
        "contradicting_evidence": claim.contradicting,
    }
    if claim.related_claim_ids:
        out["related_claim_ids"] = claim.related_claim_ids
    evidence = []
    for e in claim.evidence:
        src = wi.sources.get(e.source_id)
        item: dict[str, Any] = {"stance": e.stance}
        if src:
            item["source"] = " ".join(p for p in (src.title, src.database, src.identifier, src.year) if p)
        if with_excerpts and e.excerpt:
            item["excerpt"] = e.excerpt
        evidence.append(item)
    if evidence:
        out["evidence"] = evidence
    return out


def thesis_payload(wi: WriterInput) -> dict[str, Any]:
    t = wi.thesis
    return {
        "indication": t.indication,
        "mechanism": t.mechanism,
        "modality": t.modality or None,
        "stage": t.stage or None,
        "biomarkers": t.biomarkers or None,
        "route": t.route or None,
    }


class Budget:
    """Fit a payload into ``max_chars`` by dropping detail in a fixed order."""

    def __init__(self, max_chars: int):
        self.max_chars = max_chars

    def fits(self, payload: Any) -> bool:
        return len(json.dumps(payload, ensure_ascii=False)) <= self.max_chars


def _fit_claims(claims: list[Claim], wi: WriterInput, build, budget: Budget) -> tuple[dict[str, Any], str | None]:
    """Build a payload with ``build(claim_dicts)``; shrink until it fits."""
    full = [claim_payload(c, wi, with_excerpts=True) for c in claims]
    payload = build(full)
    if budget.fits(payload):
        return payload, None
    lean = [claim_payload(c, wi, with_excerpts=False) for c in claims]
    payload = build(lean)
    if budget.fits(payload):
        return payload, "evidence excerpts were omitted from the model input"
    # Keep the highest-confidence claims, but always keep claims with
    # contradicting evidence: they matter most for an honest narrative.
    order = sorted(
        range(len(claims)),
        key=lambda i: (claims[i].contradicting == 0, -(claims[i].confidence or 0)),
    )
    kept: list[int] = []
    for i in order:
        trial = sorted(kept + [i])
        if budget.fits(build([lean[j] for j in trial])):
            kept = trial
    payload = build([lean[j] for j in kept])
    dropped = len(claims) - len(kept)
    return payload, f"{dropped} of {len(claims)} claims were omitted from the model input (context limit)"


SECTION_FORMAT = {
    "takeaway": "1-2 sentences: the single most decision-relevant conclusion of this section",
    "synthesis": "a short paragraph (3-6 sentences) weighing the claims, naming contradictions and gaps",
}


def section_messages(section: Section, wi: WriterInput, budget: Budget) -> tuple[list[dict[str, str]], str | None]:
    ids = {c.id for c in section.claims}
    if section.is_skeptic:
        related = [c for s in wi.analysts for c in s.claims if c.id in {r for c in section.claims for r in c.related_claim_ids}]
        context_key = "analyst_claims_challenged_by_skeptic"
    else:
        related = [c for c in (wi.skeptic.claims if wi.skeptic else []) if ids & set(c.related_claim_ids)]
        context_key = "skeptic_challenges_to_this_section"
    related_payload = [claim_payload(c, wi, with_excerpts=False) for c in related]

    def build(claim_dicts):
        p = {
            "thesis": thesis_payload(wi),
            "section": {"title": section.title, "claims": claim_dicts},
        }
        if related_payload:
            p[context_key] = related_payload
        return p

    payload, note = _fit_claims(section.claims, wi, build, budget)
    if not budget.fits(payload) and related_payload:
        payload.pop(context_key, None)
        note = (note + "; " if note else "") + "related cross-section claims were omitted from the model input"
    role = (
        "This section is the Skeptic's review of the analysts' work. Summarise what it challenges and how strong the challenge is."
        if section.is_skeptic
        else "Summarise this analyst section."
    )
    user = (
        f"{role}\n\nReturn JSON with exactly these keys:\n{json.dumps(SECTION_FORMAT, indent=2)}\n\n"
        f"INPUT JSON:\n{json.dumps(payload, ensure_ascii=False, indent=1)}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}], note


def final_format(need_verdict: bool, allowed_verdicts: tuple[str, ...]) -> dict[str, Any]:
    fmt: dict[str, Any] = {}
    if need_verdict:
        fmt["verdict"] = f"exactly one of: {', '.join(allowed_verdicts)}"
    fmt.update(
        {
            "rationale": "one paragraph (4-7 sentences) justifying the recommendation from the evidence",
            "risks": [{"text": "a specific risk", "claim_ids": ["ids of input claims it rests on"]}],
            "critical_unknowns": [{"text": "a question the evidence cannot yet answer", "claim_ids": []}],
            "diligence_questions": [{"text": "a concrete question to put to the company or experts", "claim_ids": []}],
        }
    )
    return fmt


def final_messages(wi: WriterInput, budget: Budget, allowed_verdicts: tuple[str, ...]) -> tuple[list[dict[str, str]], str | None]:
    need_verdict = wi.verdict is None
    claims = wi.all_claims()
    section_of = {c.id: s.title for s in wi.sections for c in s.claims}
    panels = wi.panels or None
    notes: list[str] = []

    def build(claim_dicts, include_panels=True):
        by_section: dict[str, list] = {}
        for cd in claim_dicts:
            by_section.setdefault(section_of[cd["id"]], []).append(cd)
        p: dict[str, Any] = {"thesis": thesis_payload(wi)}
        if not need_verdict:
            p["upstream_recommendation"] = {"verdict": wi.verdict, "confidence_pct": _pct(wi.confidence)}
        if wi.capital:
            c = wi.capital
            p["capital_to_milestone"] = {
                "milestone": c.milestone, "duration": c.duration, "currency": c.currency,
                "low": c.low, "base": c.base, "high": c.high, "assumptions": c.assumptions,
            }
        p["sections"] = [{"title": t, "claims": cl} for t, cl in by_section.items()]
        missing = [s.title for s in wi.sections if not s.claims]
        if missing:
            p["sections_without_data"] = missing
        if include_panels and panels:
            p["data_panels"] = panels
        return p

    if panels and not budget.fits(build([claim_payload(c, wi, with_excerpts=False) for c in claims])):
        panels = None
        notes.append("data panels were omitted from the model input")
    payload, note = _fit_claims(claims, wi, build, budget)
    if note:
        notes.append(note)

    task = (
        "Write the overall recommendation for this thesis."
        if need_verdict
        else "The verdict and confidence were set upstream (see upstream_recommendation). Do not change them; "
        "write the rationale that explains them from the evidence, and say so if the evidence does not support them."
    )
    user = (
        f"{task} Then list the main risks, critical unknowns and diligence questions. "
        "Use claim ids from the input in claim_ids; never invent ids.\n\n"
        f"Return JSON with exactly these keys:\n{json.dumps(final_format(need_verdict, allowed_verdicts), indent=2)}\n\n"
        f"INPUT JSON:\n{json.dumps(payload, ensure_ascii=False, indent=1)}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}], "; ".join(notes) or None
