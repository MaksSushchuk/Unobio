"""End-to-end workflow: input -> researcher -> analytics -> writer -> Report (web/src/types.ts).

    ResearchInput ─► researcher (thread) ─► bundle_adapter ─► analytics.analyze ─► writer (thread) ─► Report
                      planner + reconcile      code              4 lenses (LLM)       narrative (LLM)
                      use the shared LLM                          verdict/finance      + optional PDF

One LLM client (analytics' LLM layer, LLM_* settings in server/.env) serves every module; researcher and writer
reach it through orchestrator/llm_bridge.py. All files of a run go to data/runs/<run_id>/:
researcher_bundle.json, evidence_bundle.json, analysis_result.json (+ analytics' context/, prompts/, lenses/),
report.json, report.pdf (with pdf=True), writer_warnings.json, trace.jsonl.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

from analytics.lenses import fake_lens_llm
from analytics.llm import LLMSettings, make_llm
from analytics.orchestrator import RunOptions, analyze
from analytics.schemas import AgentTrace
from evidence_bundle import paths, run_dir
from researcher.pipeline import BUNDLE_FILE, research_with_plan
from researcher.planner import plan
from researcher.schema import Bundle, ResearchInput
from writer_agent import WriterAgent, WriterConfig
from writer_agent.schema import parse_input

from .bundle_adapter import to_evidence_bundle
from .llm_bridge import FAKE_WRITER_ANSWER, ResearcherLLM, WriterLLM
from .report_builder import WRITER_VERDICTS, build_report, to_writer_input
from .report_schema import Report

EventSink = Callable[[str, str, dict[str, Any]], None]  # (step, status, payload), same as analytics
REPORT_FILE = "report.json"
_INPUT_KEYS = ("indication", "mechanism", "modality", "stage", "biomarkers", "route")


async def run(inp: ResearchInput, *, bundle: Bundle | None = None, settings: LLMSettings | None = None,
              options: RunOptions | None = None, pdf: bool = False, on_event: EventSink | None = None) -> Report:
    """Run the whole workflow and save the run folder. `bundle` skips the researcher step (reuse a saved
    researcher_bundle.json). Raises only if analytics cannot produce a result; writer failures degrade the
    narrative (fallback texts) and are listed in writer_warnings.json."""
    emit = on_event or (lambda *_: None)
    s = settings or LLMSettings.from_env()
    fake = None
    if s.provider == "fake":
        fake = fake_lens_llm()
        fake.script["writer"] = FAKE_WRITER_ANSWER
    llm = make_llm(s, fake=fake)
    loop = asyncio.get_running_loop()
    try:
        # 1. researcher
        if bundle is None:
            emit("research", "started", {})
            t0 = time.perf_counter()
            bundle = await asyncio.to_thread(_research, inp, ResearcherLLM(llm, loop, s.provider))
            emit("research", "done", {"evidence": len(bundle.evidence), "drugs": len(bundle.subject.drugs),
                                      "seconds": round(time.perf_counter() - t0, 1)})
        out = run_dir(bundle.run_id)
        (out / BUNDLE_FILE).write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

        # 2. contract adapter
        eb = to_evidence_bundle(bundle)
        (out / "evidence_bundle.json").write_text(eb.model_dump_json(indent=2), encoding="utf-8")
        emit("adapt", "done", {"evidence": len(eb.evidence), "conflicts": len(eb.conflicts()),
                               "kill_signals": sum(bool(e.data.get("kill_signal")) for e in eb.conflicts())})

        # 3. analytics
        analysis = await analyze(eb, llm, options or RunOptions(), emit)

        # 4. writer
        emit("writer", "started", {})
        wllm = WriterLLM(llm, loop, s.provider)
        cfg = WriterConfig(allowed_verdicts=WRITER_VERDICTS, expected_analysts=tuple(analysis.lenses),
                           max_prompt_chars=writer_prompt_chars(s))
        writer_report, warnings = None, []
        try:
            writer_input = parse_input(to_writer_input(analysis, eb))
            writer_report = await asyncio.to_thread(WriterAgent(cfg, client=wllm).build_report, writer_input)
            warnings = writer_report.warnings
            if pdf:
                from writer_agent.render_pdf import render_pdf
                pages = await asyncio.to_thread(render_pdf, writer_report, out / "report.pdf", cfg)
                emit("pdf", "done", {"path": str(out / "report.pdf"), "pages": pages})
        except Exception as e:  # writer is narrative only: the report is still built from analytics
            warnings.append(f"writer failed: {type(e).__name__}: {e}"[:300])
        (out / "writer_warnings.json").write_text(json.dumps(warnings, indent=2, ensure_ascii=False), encoding="utf-8")
        emit("writer", "done" if writer_report is not None else "error",
             {"calls": len(wllm.traces), "warnings": len(warnings)})

        # 5. report
        report = build_report(analysis, eb, bundle, writer_report, wllm.traces,
                              previous_run_id(analysis.input, bundle.run_id, eb.created_at.isoformat()))
        (out / REPORT_FILE).write_text(json.dumps(report.to_json_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        _append_traces(out, bundle, wllm.traces)
        emit("report", "done", {"path": str(out / REPORT_FILE), "recommendation": report.recommendation})
        return report
    finally:
        await llm.aclose()


def writer_prompt_chars(s: LLMSettings) -> int:
    """Writer payload limit from the shared model's context: ~4 chars/token, half the window for the payload."""
    return max(20_000, s.num_ctx * 2) if s.provider == "ollama" else 60_000


def _research(inp: ResearchInput, llm: ResearcherLLM) -> Bundle:
    return research_with_plan(inp, plan(inp, llm), llm=llm)


def previous_run_id(inp: dict[str, Any], run_id: str, created_at: str, runs_dir: Path | None = None) -> str | None:
    """Latest earlier run whose report has the same input (case-insensitive), or None."""
    key = _input_key(inp)
    best: tuple[str, str] | None = None
    for f in (runs_dir or paths.RUNS_DIR).glob(f"*/{REPORT_FILE}"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("id") != run_id and _input_key(r.get("input") or {}) == key and r.get("created_at", "") < created_at:
            if best is None or r["created_at"] > best[0]:
                best = (r["created_at"], r["id"])
    return best[1] if best else None


def _input_key(inp: dict[str, Any]) -> tuple:
    def norm(v: Any) -> Any:
        if isinstance(v, list):
            return tuple(sorted(" ".join(str(x).lower().split()) for x in v))
        return " ".join(str(v).lower().split()) if v else None
    return tuple(norm(inp.get(k)) for k in _INPUT_KEYS)


def _append_traces(out: Path, bundle: Bundle, writer: list[AgentTrace]) -> None:
    with (out / "trace.jsonl").open("a", encoding="utf-8") as f:
        for c in bundle.llm_calls:
            f.write(json.dumps({"module": "researcher", "run_id": bundle.run_id, **c.model_dump(mode="json")}) + "\n")
        for t in writer:
            f.write(json.dumps({"module": "writer", "run_id": bundle.run_id, **t.model_dump()}) + "\n")
