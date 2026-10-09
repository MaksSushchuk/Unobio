"""End-to-end run: input -> researcher -> analytics -> writer -> report (JSON, Markdown, PDF).

    research   researcher (live public APIs)          or a saved bundle / fixture
    adapt      evidence_bundle.from_researcher         researcher format -> analytics contract
    analyze    analytics.orchestrator.analyze          four lenses + verdict + finance + findings
    writer     writer_agent (optional package)         narrative texts + report.pdf
    report     app.report / app.markdown               report.json for the web UI, report.md

One LLM client serves every module (LLM_* / GEMINI_* in server/.env): the researcher and the writer reach
it through app/llm_bridge.py (idea from the teammate's v1.0.0 orchestrator).

Everything a run produces lands in data/runs/<run_id>/:
    run.json  researcher_bundle.json  evidence_bundle.json  analysis_result.json  writer_input.json
    report.json  report.md  report.pdf  trace.jsonl  context/ prompts/ lenses/
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from analytics.lenses import fake_lens_llm
from analytics.llm import LLMSettings, make_llm
from analytics.orchestrator import RunOptions, analyze
from evidence_bundle import FIXTURES_DIR, RUNS_DIR, EvidenceBundle, bundle_from_dict, run_dir

from .llm_bridge import FAKE_WRITER_ANSWER, ResearcherLLM, WriterLLM
from .markdown import render_markdown
from .report import build_report, researcher_traces
from .writer_input import WRITER_ANALYSTS, WRITER_VERDICTS, build_writer_input

EventSink = Callable[[str, str, dict[str, Any]], None]
Source = Literal["live", "fixture", "bundle"]


@dataclass
class RunRequest:
    indication: str
    mechanism: str
    modality: str | None = None
    stage: str | None = None
    biomarkers: list[str] | None = None
    route: str | None = None
    evidence_cutoff: date | None = None
    # live = call the public APIs (researcher); fixture = pick a matching file from data/fixtures;
    # bundle = use `bundle_path` (a researcher or analytics bundle saved earlier)
    source: Source = field(default_factory=lambda: os.environ.get("UNOBIO_RESEARCH", "live"))  # type: ignore[assignment]
    bundle_path: str | None = None
    research_llm: bool = True  # researcher's planner / stop classifier on the shared LLM (False: rule fallbacks)
    # writer step: narrative texts (through the shared LLM) + report.pdf. Off = faster, no PDF.
    pdf: bool = field(default_factory=lambda: os.environ.get("UNOBIO_PDF", "1") == "1")
    detail_round: bool = True

    def __post_init__(self) -> None:
        # Typographic quotes/dashes from copy-paste broke disease resolution: "Alzheimer’s disease" (U+2019)
        # matched "family history of Alzheimer's disease" and ClinicalTrials.gov returned 0 trials.
        self.indication, self.mechanism = clean_text(self.indication), clean_text(self.mechanism)
        self.modality, self.stage, self.route = clean_text(self.modality), clean_text(self.stage), clean_text(self.route)
        if self.biomarkers:
            self.biomarkers = [b for b in (clean_text(x) for x in self.biomarkers) if b]

    def input_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"indication": self.indication, "mechanism": self.mechanism}
        for k in ("modality", "stage", "biomarkers", "route"):
            if getattr(self, k):
                out[k] = getattr(self, k)
        if self.evidence_cutoff:
            out["evidence_cutoff"] = self.evidence_cutoff.isoformat()
        return out


_TYPOGRAPHIC = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u2032": "'", "\u00b4": "'",
                              "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2010": "-", "\u2011": "-",
                              "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u00a0": " "})


def clean_text(value: Any) -> Any:
    """Typographic quotes/dashes/nbsp -> ASCII, collapsed whitespace. Non-strings pass through unchanged."""
    if not isinstance(value, str):
        return value
    return re.sub(r"\s+", " ", value.translate(_TYPOGRAPHIC)).strip()


@dataclass
class RunOutcome:
    run_id: str
    dir: Path
    report: dict[str, Any]
    pdf_path: str | None = None
    warnings: list[str] = field(default_factory=list)


def new_run_id(indication: str, mechanism: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{indication} {mechanism}".lower()).strip("-")[:40]
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{slug}-{secrets.token_hex(2)}"


def _noop(step: str, status: str, payload: dict[str, Any]) -> None:
    return None


async def run_pipeline(req: RunRequest, run_id: str | None = None, on_event: EventSink | None = None) -> RunOutcome:
    emit = on_event or _noop
    run_id = run_id or new_run_id(req.indication, req.mechanism)
    out = run_dir(run_id)
    (out / "run.json").write_text(json.dumps({"run_id": run_id, "request": asdict(req)}, default=str, indent=2))
    warnings: list[str] = []

    # One LLM client for every module (analytics' LLM layer; researcher and writer reach it via llm_bridge).
    settings = LLMSettings.from_env()
    fake = None
    if settings.provider == "fake":
        fake = fake_lens_llm()
        fake.script["writer"] = FAKE_WRITER_ANSWER
        warnings.append("LLM_PROVIDER=fake: analyst answers are offline stand-ins, not analysis")
    llm = make_llm(settings, fake=fake)
    loop = asyncio.get_running_loop()
    try:
        # 1. research -----------------------------------------------------------------
        emit("research", "started", {"source": req.source})
        t0 = time.perf_counter()
        rllm = ResearcherLLM(llm, loop, settings.provider) if req.research_llm and settings.provider != "fake" else None
        raw = await asyncio.to_thread(_research, req, rllm)
        research_s = time.perf_counter() - t0
        (out / "researcher_bundle.json").write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        bundle = bundle_from_dict(raw)
        bundle = bundle.model_copy(update={"run_id": run_id})
        (out / "evidence_bundle.json").write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        emit("research", "done", {
            "evidence": len(bundle.evidence), "conflicts": len(bundle.conflicts()),
            "kill_signals": sum(bool(e.data.get("kill_signal")) for e in bundle.conflicts()),
            "sources": {s.source: s.evidence_count for s in bundle.source_status},
            "fixture": bundle.is_fixture, "seconds": round(research_s, 1),
        })

        # 2. analytics ------------------------------------------------------------------
        emit("analytics", "started", {"provider": settings.provider, "model": llm.model})
        result = await analyze(bundle, llm, RunOptions(detail_round=req.detail_round), on_event=emit)

        # 3. writer: narrative (rationale, section takeaways) + PDF -----------------------
        narrative, writer_traces, pdf_path = None, [], None
        if req.pdf:
            emit("writer", "started", {})
            narrative, writer_traces, pdf_path, writer_warnings = await _run_writer(bundle, result, out, llm, loop,
                                                                                   settings)
            warnings += writer_warnings
            emit("writer", "done" if pdf_path else "error",
                 {"pdf": bool(pdf_path), "calls": len(writer_traces), "warnings": writer_warnings[:3]})
    finally:
        await llm.aclose()

    # 4. report for the UI + Markdown --------------------------------------------------------
    emit("report", "started", {})
    previous = find_previous_run(req.indication, req.mechanism, exclude=run_id)
    traces = researcher_traces(bundle, research_s) + [
        {"agent": t.agent, "step": t.step, "tool": t.model, "input_tokens": t.input_tokens,
         "output_tokens": t.output_tokens, "latency_s": t.latency_s, "cost_usd": t.cost_usd} for t in writer_traces]
    report = build_report(bundle, result, previous_run_id=previous, extra_traces=traces, narrative=narrative)
    if fake is not None:
        report["is_mock"] = True  # stand-in analyst answers: make it visible in the UI
    report["warnings"] = warnings
    (out / "report.md").write_text(render_markdown(report, bundle), encoding="utf-8")
    report["downloads"] = {"md": True, "pdf": bool(pdf_path), "zip": True}
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    _append_traces(out, run_id, rllm.calls if rllm else [], writer_traces)
    emit("report", "done", {"recommendation": report["recommendation"], "previous_run_id": previous,
                            "downloads": [k for k, v in report["downloads"].items() if v]})

    emit("pipeline", "done", {"run_id": run_id, "recommendation": report["recommendation"],
                              "confidence": report["confidence"]})
    return RunOutcome(run_id=run_id, dir=out, report=report, pdf_path=pdf_path, warnings=warnings)


# ----------------------------------------------------------------------------- research


def _research(req: RunRequest, llm: Any | None = None) -> dict[str, Any]:
    """Return a bundle as a plain dict (researcher format or analytics format). `llm` is the shared client
    behind the researcher's LLM protocol (planner + stop-reason classifier); None = rule-based fallbacks."""
    if req.source == "bundle":
        if not req.bundle_path:
            raise ValueError("source=bundle needs bundle_path")
        return json.loads(Path(req.bundle_path).read_text(encoding="utf-8"))
    if req.source == "fixture":
        return json.loads(pick_fixture(req).read_text(encoding="utf-8"))

    from researcher.pipeline import research_with_plan  # imported lazily: network
    from researcher.planner import plan
    from researcher.schema import ResearchInput

    inp = ResearchInput(indication=req.indication, mechanism=req.mechanism, modality=req.modality, stage=req.stage,
                        biomarkers=req.biomarkers, route=req.route, evidence_cutoff=req.evidence_cutoff)
    return research_with_plan(inp, plan(inp, llm), llm=llm).model_dump(mode="json")


def pick_fixture(req: RunRequest) -> Path:
    """Best fixture for the input: same indication + mechanism words, and the cutoff variant if asked."""
    words = set(re.findall(r"[a-z0-9]+", f"{req.indication} {req.mechanism}".lower()))
    best: tuple[int, Path] | None = None
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        inp = raw.get("input") or {}
        fwords = set(re.findall(r"[a-z0-9]+", f"{inp.get('indication', '')} {inp.get('mechanism', '')}".lower()))
        score = len(words & fwords) * 10
        if score == 0:
            continue
        has_cutoff = bool(inp.get("evidence_cutoff"))
        score += 5 if has_cutoff == bool(req.evidence_cutoff) else 0
        if best is None or score > best[0]:
            best = (score, path)
    if best is None:
        raise ValueError(f"no fixture matches '{req.indication}' + '{req.mechanism}'; "
                         f"available: {[p.name for p in FIXTURES_DIR.glob('*.json')]}")
    return best[1]


# ----------------------------------------------------------------------------- writer


async def _run_writer(bundle: EvidenceBundle, result: Any, out: Path, llm: Any, loop: asyncio.AbstractEventLoop,
                      settings: LLMSettings) -> tuple[Any | None, list[Any], str | None, list[str]]:
    """Writer narrative through the shared LLM, then the PDF. Never raises: the report is built from analytics
    anyway, the writer only adds texts and the PDF. Returns (narrative, traces, pdf path, warnings)."""
    winput = build_writer_input(bundle, result)
    (out / "writer_input.json").write_text(json.dumps(winput, indent=2, default=str), encoding="utf-8")
    try:
        from writer_agent import WriterAgent, WriterConfig  # pyright: ignore[reportMissingImports] (optional package)
        from writer_agent.render_pdf import render_pdf  # pyright: ignore[reportMissingImports]
        from writer_agent.schema import parse_input  # pyright: ignore[reportMissingImports]
    except ImportError:
        return None, [], None, ["writer not installed (uv pip install -e ./writer): no narrative, no PDF"]
    wllm = WriterLLM(llm, loop, settings.provider)
    cfg = WriterConfig()
    cfg.allowed_verdicts = WRITER_VERDICTS
    cfg.expected_analysts = WRITER_ANALYSTS
    cfg.max_prompt_chars = max(20_000, settings.num_ctx * 2) if settings.provider == "ollama" else 60_000
    try:
        narrative = await asyncio.to_thread(WriterAgent(cfg, client=wllm).build_report, parse_input(winput))
        path = out / "report.pdf"
        await asyncio.to_thread(render_pdf, narrative, path, cfg)
    except Exception as exc:  # writer is optional output; keep the run
        return None, wllm.traces, None, [f"writer failed: {type(exc).__name__}: {exc}"[:300]]
    return narrative, wllm.traces, str(path), [f"writer: {w}" for w in narrative.warnings]


def _append_traces(out: Path, run_id: str, researcher_calls: list[Any], writer: list[Any]) -> None:
    with (out / "trace.jsonl").open("a", encoding="utf-8") as f:
        for c in researcher_calls:
            f.write(json.dumps({"module": "researcher", "run_id": run_id, **c.model_dump(mode="json")}) + "\n")
        for t in writer:
            f.write(json.dumps({"module": "writer", "run_id": run_id, **t.model_dump()}) + "\n")


# ----------------------------------------------------------------------------- runs on disk


def list_runs() -> list[dict[str, Any]]:
    rows = []
    for p in sorted(RUNS_DIR.glob("*/report.json"), reverse=True):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append({"id": r["id"], "created_at": r["created_at"], "input": r["input"],
                     "recommendation": r["recommendation"], "confidence": r["confidence"], "is_mock": r["is_mock"],
                     "evidence_cutoff": (r.get("details") or {}).get("evidence_cutoff"),
                     "previous_run_id": r.get("previous_run_id")})
    return rows


def find_previous_run(indication: str, mechanism: str, exclude: str) -> str | None:
    """Latest earlier run with the same indication + mechanism (drives the UI 'What changed' tab)."""
    key = (indication.strip().lower(), mechanism.strip().lower())
    for r in list_runs():  # newest first
        if r["id"] == exclude:
            continue
        if (r["input"]["indication"].strip().lower(), r["input"]["mechanism"].strip().lower()) == key:
            return r["id"]
    return None


def load_report(run_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
        return None
    path = RUNS_DIR / run_id / "report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ----------------------------------------------------------------------------- downloads


def _run_path(run_id: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
        return None
    path = RUNS_DIR / run_id
    return path if (path / "report.json").exists() else None


def markdown_path(run_id: str) -> Path | None:
    """report.md of a run; rendered now for runs made before Markdown export existed."""
    out = _run_path(run_id)
    if out is None:
        return None
    md = out / "report.md"
    if not md.exists():
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        bundle = EvidenceBundle.model_validate_json((out / "evidence_bundle.json").read_text(encoding="utf-8"))
        md.write_text(render_markdown(report, bundle), encoding="utf-8")
    return md


async def pdf_path(run_id: str) -> tuple[Path | None, list[str]]:
    """report.pdf of a run; generated now (writer narrative through the shared LLM) if the run had none."""
    out = _run_path(run_id)
    if out is None:
        return None, ["run not found"]
    pdf = out / "report.pdf"
    if pdf.exists():
        return pdf, []
    from analytics.schemas import AnalysisResult

    bundle = EvidenceBundle.model_validate_json((out / "evidence_bundle.json").read_text(encoding="utf-8"))
    result = AnalysisResult.model_validate_json((out / "analysis_result.json").read_text(encoding="utf-8"))
    settings = LLMSettings.from_env()
    fake = None
    if settings.provider == "fake":
        fake = fake_lens_llm()
        fake.script["writer"] = FAKE_WRITER_ANSWER
    llm = make_llm(settings, fake=fake)
    try:
        _, traces, path, warnings = await _run_writer(bundle, result, out, llm, asyncio.get_running_loop(), settings)
    finally:
        await llm.aclose()
    _append_traces(out, run_id, [], traces)
    return (Path(path) if path else None), warnings


def zip_run(run_id: str) -> bytes | None:
    """Every file of the run folder (JSON, Markdown, PDF, traces, analysts' prompts and answers) as a zip."""
    import io
    import zipfile

    out = _run_path(run_id)
    if out is None:
        return None
    markdown_path(run_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                z.write(f, f"{run_id}/{f.relative_to(out)}")
    return buf.getvalue()
