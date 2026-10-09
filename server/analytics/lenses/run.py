"""Run one lens / all lenses.

For each lens:
  1. build AgentSpec (common rules + lens prompt + answer model)
  2. user prompt = lens context (header + evidence cards) + mandatory questions
  3. AgentRunner: call -> parse -> schema -> semantic validation -> retry
  4. optional detail round: if the analyst asked for `need_detail` ids, send their
     full cards and ask for a revised answer (one round, max 8 ids)
  5. map short ids -> full ids -> LensResult
Lenses run concurrently; the LLM client limits real parallelism.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from evidence_bundle import EvidenceBundle

from ..agents import AgentRunner, AgentSpec, load_prompt, render
from ..context import RunContext, ShortIdMap
from ..context.cards import full_card
from ..schemas import LENSES, AgentTrace, Lens, LensResult
from .answers import LensAnswer
from .definitions import LENS_DEFS
from .mapping import failed_result, skipped_result, to_lens_result
from .validation import make_validator, norm

MAX_DETAIL_IDS = 8


@dataclass
class LensRun:
    result: LensResult
    traces: list[AgentTrace] = field(default_factory=list)
    detail_ids: list[str] = field(default_factory=list)  # short ids added in the detail round


def lens_spec(lens: Lens, model: str | None = None) -> AgentSpec:
    d = LENS_DEFS[lens]
    system = load_prompt("_common") + "\n\n" + load_prompt(d.prompt_file)
    return AgentSpec(name=f"lens:{lens}", system_prompt=system, output_model=d.answer_model,
                     model=model, max_tokens=d.max_tokens, max_attempts=3)


def lens_user_prompt(lens: Lens, ctx: RunContext, extra_cards: str = "") -> str:
    d = LENS_DEFS[lens]
    context = ctx.prompt_context(lens)
    if extra_cards:
        context += "\n\n## ADDITIONAL DETAIL (you requested these)\n" + extra_cards
    questions = "\n".join(f"{i}. {q}" for i, q in enumerate(d.questions, 1))
    return render(load_prompt("lens_user"), context=context, title=d.title, questions=questions)


async def run_lens(lens: Lens, bundle: EvidenceBundle, ctx: RunContext, runner: AgentRunner,
                   model: str | None = None, detail_round: bool = True) -> LensRun:
    lc = ctx.lenses[lens]
    if not lc.shown_ids and not lc.aggregate_ids and not lc.dropped_ids:
        # Nothing to analyse: an LLM call would only invent claims (or fail the min-1-evidence rule 3 times).
        return LensRun(skipped_result(lens), [])
    spec = lens_spec(lens, model)
    first = await runner.run(spec, lens_user_prompt(lens, ctx), validate=make_validator(bundle, ctx, lens))
    traces = list(first.traces)
    if not first.ok or first.output is None:
        return LensRun(failed_result(lens, first.errors), traces)
    answer: LensAnswer = first.output  # type: ignore[assignment]

    requested = _requested_ids(answer, ctx, lens) if detail_round else []
    if requested:
        cards = _detail_cards(bundle, requested)
        second = await runner.run(spec, lens_user_prompt(lens, ctx, cards),
                                  validate=make_validator(bundle, ctx, lens, extra_allowed=set(requested)))
        traces += second.traces
        if second.ok and second.output is not None:
            answer = second.output  # type: ignore[assignment]
    return LensRun(to_lens_result(lens, answer, ctx), traces, requested)


async def run_lenses(bundle: EvidenceBundle, ctx: RunContext, runner: AgentRunner,
                     lenses: tuple[Lens, ...] = LENSES, models: dict[Lens, str] | None = None) -> dict[Lens, LensRun]:
    models = models or {}
    runs = await asyncio.gather(*(run_lens(lens, bundle, ctx, runner, models.get(lens)) for lens in lenses))
    return dict(zip(lenses, runs))


def _requested_ids(answer: LensAnswer, ctx: RunContext, lens: Lens) -> list[str]:
    full_shown = set(ctx.lenses[lens].full_ids)
    out: list[str] = []
    for ref in answer.need_detail:
        r = norm(ref)
        if r.startswith("E") and ctx.resolve(r) and r not in full_shown and r not in out:
            out.append(r)
    return out[:MAX_DETAIL_IDS]


def _detail_cards(bundle: EvidenceBundle, short_ids: list[str]) -> str:
    ids = ShortIdMap(bundle.evidence)  # deterministic: same numbering as the context
    by_id = bundle.by_id()
    return "\n".join(full_card(by_id[ids.full(s)], ids, snippet_limit=900) for s in short_ids)
