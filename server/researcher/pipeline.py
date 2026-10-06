"""run_research(): plan, resolve the subject, run connectors, reconcile, evaluate coverage."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from researcher.schema import Bundle, Evidence, LlmCall, ResearchInput, SearchPlan, SourceStatus, Subject
from researcher.connectors.base import Connector
from researcher.connectors.clinicaltrials import ClinicalTrialsConnector
from researcher.connectors.opentargets import OpenTargetsConnector
from researcher.http import HttpClient
from researcher.llm import LLM, GeminiLLM
from researcher.coverage_checklist import evaluate_coverage
from researcher.planner import plan
from researcher.reconcile import reconcile
from researcher.resolve import resolve

RUNS_DIR = Path(__file__).resolve().parent.parent / "evidence_bundle" / "runs"


def new_run_id(inp: ResearchInput, now: datetime) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{inp.indication} {inp.mechanism}".lower()).strip("-")
    return f"{now:%Y%m%dT%H%M%SZ}-{slug[:48]}-{secrets.token_hex(3)}"


def run_research(inp: ResearchInput, use_llm: bool = True) -> Bundle:
    llm = GeminiLLM.from_env() if use_llm else None
    return research_with_plan(inp, plan(inp, llm), llm=llm)


def research_with_plan(inp: ResearchInput, search_plan: SearchPlan, llm_calls: list[LlmCall] | None = None,
                       llm: LLM | None = None) -> Bundle:
    """Everything after planning: resolve, run connectors, reconcile, coverage (last).
    `llm` (optional) classifies stop reasons in reconcile; its calls (planner included) go to Bundle.llm_calls."""
    now = datetime.now(UTC)
    sources_status: list[SourceStatus] = []
    with HttpClient() as http:
        subject = resolve(inp, search_plan, http=http, statuses=sources_status)
        evidence = run_connectors(subject, default_connectors(http), sources_status)
    bundle = Bundle(
        run_id=new_run_id(inp, now),
        created_at=now,
        input=inp,
        subject=subject,
        plan=search_plan,
        evidence=evidence,
        coverage=[],
        sources_status=sources_status,
        llm_calls=llm_calls or [],
    )
    bundle = reconcile(bundle, llm)
    if llm is not None:
        bundle = bundle.model_copy(update={"llm_calls": [*(llm_calls or []), *llm.calls]})
    return bundle.model_copy(update={"coverage": evaluate_coverage(bundle)})


def default_connectors(http: HttpClient) -> list[Connector]:
    return [ClinicalTrialsConnector(http), OpenTargetsConnector(http)]


def run_connectors(
    subject: Subject, connectors: list[Connector], statuses: list[SourceStatus]
) -> list[Evidence]:
    """Run each connector and collect its evidence and status. Duplicates are merged by reconcile()."""
    evidence: list[Evidence] = []
    for c in connectors:
        try:
            items = c.fetch(subject)
            status = c.last_status
        except Exception as e:  # connectors must not raise; guard anyway
            items = []
            status = SourceStatus(source=c.source, ok=False, records=0, error=f"{type(e).__name__}: {e}"[:300], duration_s=0.0)
        evidence.extend(items)
        if status is not None:
            statuses.append(status)
    return evidence


def write_bundle(bundle: Bundle, runs_dir: Path | None = None) -> Path:
    out_dir = (runs_dir or RUNS_DIR) / bundle.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "bundle.json"
    path.write_text(bundle.model_dump_json(indent=2))
    return path
