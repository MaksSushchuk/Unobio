"""Step 9 — the orchestrator: one call from evidence bundle to analysis_result.json.

    bundle ─► context ─► 4 lenses (parallel) ─► verdict ─► finance ─► findings ─► AnalysisResult
                                   LLM            code       code       code

The orchestrator is plain code: a fixed sequence of steps. It
  * emits progress events (step, status, payload) — the API layer turns them into SSE for the UI,
  * guards the token budget BEFORE calling the LLM (shrinks the evidence budget if needed),
  * records every LLM call and every code step as a trace (tokens, latency, cost),
  * saves everything a human needs to debug a run into data/runs/<run_id>/.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from evidence_bundle import EvidenceBundle, run_dir

from .agents import AgentRunner
from .context import ContextBuilder, ContextConfig, RunContext
from .finance import build_finance
from .findings import build_diligence, build_risks, merge_unknowns
from .lenses import LENS_DEFS, lens_spec, lens_user_prompt, run_lens
from .lenses.run import LensRun
from .llm import LLMClient
from .schemas import LENSES, AgentTrace, AnalysisResult, Lens, LensResult
from .verdict import VerdictConfig, decide

EventSink = Callable[[str, str, dict[str, Any]], None]  # (step, status, payload)


@dataclass
class RunOptions:
    lenses: tuple[Lens, ...] = LENSES
    budget_tokens: int = ContextConfig.budget_tokens  # evidence tokens per lens
    max_total_tokens: int = 120_000  # whole run (all lenses, all attempts) — guard against runaway cost
    models: dict[Lens, str] = field(default_factory=dict)  # per-lens model override
    detail_round: bool = True
    save: bool = True  # write files into data/runs/<run_id>/
    verdict: VerdictConfig = field(default_factory=VerdictConfig)


def _noop(step: str, status: str, payload: dict[str, Any]) -> None:
    return None


async def analyze(bundle: EvidenceBundle, llm: LLMClient, options: RunOptions | None = None,
                  on_event: EventSink | None = None) -> AnalysisResult:
    opt = options or RunOptions()
    emit = on_event or _noop
    traces: list[AgentTrace] = []
    t_run = time.perf_counter()

    def step_done(name: str, t0: float, **payload: Any) -> None:
        traces.append(AgentTrace(agent="orchestrator", step=name, latency_s=round(time.perf_counter() - t0, 3)))
        emit(name, "done", payload)

    # 1. context (+ token guard)
    t0 = time.perf_counter()
    emit("context", "started", {})
    ctx = _build_context_within_budget(bundle, opt, emit)
    step_done("context", t0, evidence=len(bundle.evidence),
              tokens_per_lens={lens: ctx.lenses[lens].evidence_tokens for lens in opt.lenses})

    # 2. lenses in parallel (the LLM client limits real concurrency)
    runner = AgentRunner(llm)

    async def one(lens: Lens) -> tuple[Lens, LensRun]:
        emit(f"lens:{lens}", "started", {})
        run = await run_lens(lens, bundle, ctx, runner, opt.models.get(lens), opt.detail_round)
        emit(f"lens:{lens}", "done" if run.result.status == "ok" else "error",
             {"score": run.result.score, "claims": len(run.result.claims), "attempts": len(run.traces),
              "errors": run.result.errors[:2]})
        return lens, run

    lens_runs = dict(await asyncio.gather(*(one(lens) for lens in opt.lenses)))
    lenses: dict[Lens, LensResult] = {lens: r.result for lens, r in lens_runs.items()}
    for r in lens_runs.values():
        traces += r.traces

    # 3. verdict
    t0 = time.perf_counter()
    emit("verdict", "started", {})
    verdict = decide(bundle, lenses, opt.verdict)
    step_done("verdict", t0, recommendation=verdict.recommendation, confidence=verdict.confidence)

    # 4. finance
    t0 = time.perf_counter()
    emit("finance", "started", {})
    finance = build_finance(bundle, lenses.get("investment"), verdict)
    step_done("finance", t0, capital_base=finance.capital.usd_base,
              rnpv_base=finance.rnpv.usd_base if finance.rnpv else None)

    # 5. findings
    t0 = time.perf_counter()
    emit("findings", "started", {})
    risks = build_risks(bundle, lenses)
    unknowns = merge_unknowns(bundle, lenses)
    questions = build_diligence(bundle, lenses, risks, unknowns)
    step_done("findings", t0, risks=len(risks), unknowns=len(unknowns), diligence_questions=len(questions))

    traces.append(AgentTrace(agent="orchestrator", step="total", latency_s=round(time.perf_counter() - t_run, 3),
                             input_tokens=sum(t.input_tokens for t in traces),
                             output_tokens=sum(t.output_tokens for t in traces),
                             cost_usd=round(sum(t.cost_usd for t in traces), 6)))
    result = AnalysisResult(
        run_id=bundle.run_id, bundle_run_id=bundle.run_id, input=bundle.input.model_dump(mode="json"),
        lenses=lenses, verdict=verdict, capital=finance.capital, rnpv=finance.rnpv,
        risks=risks, unknowns=unknowns, diligence_questions=questions,
        short_ids=dict(ctx.id_map["evidence"]), traces=traces,
    )

    if opt.save:
        out = save_run(bundle, ctx, lens_runs, result)
        emit("save", "done", {"path": str(out)})
    emit("run", "done", {"recommendation": verdict.recommendation, "cost_usd": result.total_cost_usd})
    return result


# ----------------------------------------------------------------------------- token guard


def _estimate_run_tokens(ctx: RunContext, lenses: tuple[Lens, ...]) -> int:
    total = 0
    for lens in lenses:
        prompt = len(lens_spec(lens).full_system_prompt()) / 4 + len(lens_user_prompt(lens, ctx)) / 4
        total += int(prompt) + LENS_DEFS[lens].max_tokens
    return total


def _build_context_within_budget(bundle: EvidenceBundle, opt: RunOptions, emit: EventSink) -> RunContext:
    """Shrink the per-lens evidence budget until one attempt per lens fits into max_total_tokens."""
    budget = opt.budget_tokens
    ctx = ContextBuilder(ContextConfig(budget_tokens=budget)).build(bundle)
    estimate = _estimate_run_tokens(ctx, opt.lenses)
    while estimate > opt.max_total_tokens and budget > 1000:
        budget = max(1000, int(budget * 0.7))
        ctx = ContextBuilder(ContextConfig(budget_tokens=budget)).build(bundle)
        estimate = _estimate_run_tokens(ctx, opt.lenses)
        emit("context", "progress", {"message": f"token guard: evidence budget reduced to {budget}/lens",
                                     "estimated_tokens": estimate})
    return ctx


# ----------------------------------------------------------------------------- persistence


def save_run(bundle: EvidenceBundle, ctx: RunContext, lens_runs: dict[Lens, LensRun], result: AnalysisResult) -> Path:
    out = run_dir(bundle.run_id)
    bundle_file = out / "evidence_bundle.json"
    if not bundle_file.exists():  # writer and UI expect the bundle next to the result
        bundle_file.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    (out / "analysis_result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    for sub in ("context", "prompts", "lenses"):
        (out / sub).mkdir(exist_ok=True)
    (out / "context" / "header.md").write_text(ctx.header, encoding="utf-8")
    (out / "context" / "id_map.json").write_text(json.dumps(ctx.id_map, indent=2), encoding="utf-8")
    for lens, run in lens_runs.items():
        (out / "context" / f"{lens}.md").write_text(ctx.prompt_context(lens), encoding="utf-8")
        (out / "prompts" / f"{lens}.md").write_text(
            lens_spec(lens).full_system_prompt() + "\n\n=== USER ===\n\n" + lens_user_prompt(lens, ctx), encoding="utf-8")
        (out / "lenses" / f"{lens}.json").write_text(json.dumps(
            {"result": run.result.model_dump(mode="json"), "traces": [t.model_dump() for t in run.traces]}, indent=2),
            encoding="utf-8")
    with (out / "trace.jsonl").open("a", encoding="utf-8") as f:
        for t in result.traces:
            f.write(json.dumps({"module": "analytics", "run_id": result.run_id, **t.model_dump()}) + "\n")
    return out
