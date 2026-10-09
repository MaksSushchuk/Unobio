"""HTTP API for the web UI.

    POST /api/runs                     start a run            -> {"run_id": ...}
    GET  /api/runs/{id}/events         progress (Server-Sent Events; replays history, ends with "end")
    GET  /api/runs/{id}/report         Report JSON (web/src/types.ts)
    GET  /api/runs/{id}/report.pdf     PDF from the writer (generated on first request if missing)
    GET  /api/runs/{id}/report.md      the report as Markdown (claims with numbered sources)
    GET  /api/runs/{id}/files.zip      the whole run folder
    GET  /api/runs                     past runs, newest first
    GET  /api/health                   status + which LLM / research mode is configured

Runs execute in the background of this process (one asyncio task each). Event history is kept in
memory; reports are read from data/runs/<id>/ so they survive a restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import traceback
from dataclasses import dataclass, field
from datetime import date
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from analytics.llm import LLMSettings
from analytics.llm.config import load_dotenv
from evidence_bundle import RUNS_DIR

from .pipeline import (RunRequest, list_runs, load_report, markdown_path, new_run_id, pdf_path, run_pipeline,
                       zip_run)

log = logging.getLogger("unobio.api")
load_dotenv()

app = FastAPI(title="Unobio API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


# ----------------------------------------------------------------------------- request models


class ReportInput(BaseModel):
    indication: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    modality: str | None = None
    stage: str | None = None
    biomarkers: list[str] | None = None
    route: str | None = None


class RunOptionsIn(BaseModel):
    evidence_cutoff: date | None = None
    source: Literal["live", "fixture"] | None = None
    pdf: bool | None = None
    research_llm: bool = True


class StartRun(BaseModel):
    input: ReportInput
    options: RunOptionsIn = RunOptionsIn()


# ----------------------------------------------------------------------------- in-memory run registry


@dataclass
class RunState:
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    error: str | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def push(self, step: str, status: str, payload: dict[str, Any]) -> None:
        self.events.append({"seq": len(self.events), "step": step, "status": status, "payload": payload})
        self.changed.set()


RUNS: dict[str, RunState] = {}
_RUN_ID = re.compile(r"[A-Za-z0-9._-]+")


async def _execute(run_id: str, req: RunRequest) -> None:
    state = RUNS[run_id]
    try:
        await run_pipeline(req, run_id=run_id, on_event=state.push)
    except Exception as exc:  # report to the UI instead of dying silently
        log.exception("run %s failed", run_id)
        state.error = f"{type(exc).__name__}: {exc}"
        state.push("pipeline", "error", {"message": state.error, "trace": traceback.format_exc()[-2000:]})
    finally:
        state.done = True
        state.changed.set()


# ----------------------------------------------------------------------------- routes


@app.get("/api/health")
def health() -> dict[str, Any]:
    s = LLMSettings.from_env()
    probe = RunRequest(indication="x", mechanism="x")
    return {"ok": True, "llm_provider": s.provider, "llm_model": s.model or None,
            "research": probe.source, "pdf": probe.pdf, "runs_dir": str(RUNS_DIR)}


@app.post("/api/runs")
async def start_run(body: StartRun) -> dict[str, str]:
    i, o = body.input, body.options
    req = RunRequest(indication=i.indication, mechanism=i.mechanism, modality=i.modality, stage=i.stage,
                     biomarkers=i.biomarkers, route=i.route, evidence_cutoff=o.evidence_cutoff,
                     research_llm=o.research_llm)
    if o.source:
        req.source = o.source
    if o.pdf is not None:
        req.pdf = o.pdf
    run_id = new_run_id(i.indication, i.mechanism)
    RUNS[run_id] = RunState()
    asyncio.create_task(_execute(run_id, req))
    return {"run_id": run_id}


@app.get("/api/runs")
def runs() -> list[dict[str, Any]]:
    return list_runs()


@app.get("/api/runs/{run_id}")
def run_status(run_id: str) -> dict[str, Any]:
    """State of one run: request (input + options), progress and the outcome once the report exists."""
    if not _RUN_ID.fullmatch(run_id):
        raise HTTPException(404, f"run not found: {run_id}")
    state = RUNS.get(run_id)
    report = load_report(run_id)
    run_file = RUNS_DIR / run_id / "run.json"
    if state is None and report is None and not run_file.exists():
        raise HTTPException(404, f"run not found: {run_id}")
    request = json.loads(run_file.read_text(encoding="utf-8")).get("request", {}) if run_file.exists() else {}
    keys = ("indication", "mechanism", "modality", "stage", "biomarkers", "route")
    return {
        "run_id": run_id,
        "done": state.done if state else True,
        "error": state.error if state else (None if report else "run did not finish (server restarted?)"),
        "events": len(state.events) if state else 0,
        "input": {k: request[k] for k in keys if request.get(k)},
        "options": {"evidence_cutoff": request.get("evidence_cutoff"), "source": request.get("source"),
                    "pdf": request.get("pdf")},
        "result": None if report is None else {"recommendation": report["recommendation"],
                                                "confidence": report["confidence"], "summary": report["summary"],
                                                "previous_run_id": report.get("previous_run_id")},
    }


@app.get("/api/runs/{run_id}/report")
def report(run_id: str) -> dict[str, Any]:
    r = load_report(run_id)
    if r is None:
        raise HTTPException(404, f"report not found: {run_id}")
    return r


@app.get("/api/runs/{run_id}/report.pdf")
async def report_pdf(run_id: str) -> FileResponse:
    """The writer's PDF; generated on first request if the run was made without it (takes a few LLM calls)."""
    path, warnings = await pdf_path(run_id)
    if path is None:
        raise HTTPException(404 if warnings == ["run not found"] else 503,
                            "; ".join(warnings) or "PDF could not be generated")
    return FileResponse(path, media_type="application/pdf", filename=f"unobio-{run_id}.pdf")


@app.get("/api/runs/{run_id}/report.md")
def report_md(run_id: str) -> FileResponse:
    path = markdown_path(run_id)
    if path is None:
        raise HTTPException(404, f"run not found: {run_id}")
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=f"unobio-{run_id}.md")


@app.get("/api/runs/{run_id}/files.zip")
def run_zip(run_id: str) -> Response:
    data = zip_run(run_id)
    if data is None:
        raise HTTPException(404, f"run not found: {run_id}")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="unobio-{run_id}.zip"'})


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str) -> StreamingResponse:
    state = RUNS.get(run_id)
    if state is None:
        if load_report(run_id) is None:
            raise HTTPException(404, f"run not found: {run_id}")
        state = RunState(done=True)  # finished in an earlier server process

    async def stream() -> AsyncIterator[str]:
        sent = 0
        while True:
            state.changed.clear()  # before reading: a push after this line wakes the wait below
            while sent < len(state.events):
                yield f"data: {json.dumps(state.events[sent], default=str)}\n\n"
                sent += 1
            if state.done:
                yield f"event: end\ndata: {json.dumps({'run_id': run_id, 'error': state.error})}\n\n"
                return
            try:
                await asyncio.wait_for(state.changed.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
