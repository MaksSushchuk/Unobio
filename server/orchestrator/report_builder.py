"""analytics AnalysisResult (+ EvidenceBundle) -> writer input, and -> the final web Report.

Field sources in the Report:
  recommendation, confidence, risks, unknowns, diligence questions, capital  <- analytics (deterministic, typed)
  summary                <- writer's recommendation rationale (fallback: analytics' rule trace)
  section.summary        <- writer's section takeaway (fallback: the lens score rationale)
  sections / claims      <- analytics lenses (one section per lens that ran)
  evidence               <- every evidence item of the bundle, so every cited id resolves
  traces                 <- researcher LLM calls and source fetches, analytics traces, writer calls
"""
from __future__ import annotations

import re
from typing import Any

from analytics.lenses import LENS_DEFS
from analytics.schemas import AgentTrace, AnalysisResult
from evidence_bundle import EvidenceBundle
from researcher.schema import Bundle

from .report_schema import Report

RECOMMENDATION = {"Invest": "invest", "Conditional": "conditional", "Do Not Invest": "do_not_invest"}
WRITER_VERDICTS = tuple(RECOMMENDATION)  # writer copies the upstream verdict; these are the values it accepts
_SHORT_REF = re.compile(r"\s*\[(?:[EA]\d+(?:\s*,\s*)?)+\]")


def lens_title(lens: str) -> str:
    return LENS_DEFS[lens].title if lens in LENS_DEFS else lens.title()


# ----------------------------------------------------------------------------- writer input


def to_writer_input(a: AnalysisResult, eb: EvidenceBundle) -> dict[str, Any]:
    """writer docs/INPUT_SCHEMA.md. Analyst keys are the lens names (writer is configured to expect them)."""
    by_id = eb.by_id()
    cited = {r.evidence_id for lr in a.lenses.values() for c in lr.claims for r in c.evidence}
    sources = []
    for eid in sorted(cited):
        e = by_id.get(eid)
        if e is None:
            continue
        sources.append({"id": e.id, "title": e.title, "url": e.url, "database": e.source,
                        "identifier": str(e.data.get("nct_id") or e.entity_refs.get("nct_id") or ""),
                        "year": str(e.published_at.year) if e.published_at else ""})
    analysts = []
    for lens, lr in a.lenses.items():
        analysts.append({
            "key": lens, "title": lens_title(lens), "status": "done" if lr.status == "ok" else lr.status,
            "claims": [{
                "id": c.id, "text": c.text, "confidence": c.confidence, "claim_type": c.kind,
                "evidence": [{"source_id": r.evidence_id, "stance": r.stance,
                              "excerpt": by_id[r.evidence_id].snippet if r.evidence_id in by_id else ""}
                             for r in c.evidence],
            } for c in lr.claims],
        })
    out: dict[str, Any] = {
        "schema_version": "1.0",
        "run": {"id": a.run_id, "timestamp": a.created_at.isoformat(), "fixture_data": eb.is_fixture},
        "thesis": {k: v for k, v in a.input.items() if k in ("indication", "mechanism", "modality", "stage", "biomarkers", "route") and v},
        "recommendation": {"verdict": a.verdict.recommendation, "confidence": a.verdict.confidence},
        "sources": sources,
        "analysts": analysts,
    }
    if a.capital:
        c = a.capital
        out["capital_to_milestone"] = {"milestone": c.milestone, "duration": f"{c.months_low}-{c.months_high} months",
                                       "currency": "USD", "low": c.usd_low, "base": c.usd_base, "high": c.usd_high,
                                       "assumptions": c.assumptions}
    return out


# ----------------------------------------------------------------------------- report


def build_report(a: AnalysisResult, eb: EvidenceBundle, researcher: Bundle | None, writer_report: Any | None,
                 writer_traces: list[AgentTrace], previous_run_id: str | None) -> Report:
    """`writer_report` is writer_agent.agent.Report (narratives, rationale) or None when writer failed."""
    narratives = getattr(writer_report, "narratives", {}) or {}
    rationale = (getattr(writer_report, "rationale", "") or "").strip()

    sections = []
    for lens, lr in a.lenses.items():
        if lr.status != "ok" or not lr.claims:
            continue
        n = narratives.get(lens)
        summary = (n.takeaway if n and n.takeaway else _clean(lr.score_rationale)) or None
        sections.append({
            "id": f"sec_{lens}", "title": lens_title(lens), "summary": summary,
            "claims": [{"id": c.id, "text": _clean(c.text), "kind": c.kind, "confidence": c.confidence,
                        "evidence": [{"evidence_id": r.evidence_id, "stance": r.stance} for r in c.evidence]}
                       for c in lr.claims],
        })
    claim_ids = {c["id"] for s in sections for c in s["claims"]}

    if a.capital is None:
        raise ValueError("analytics produced no capital estimate; the Report requires one")
    v = a.verdict
    summary = rationale or _verdict_summary(a)
    report = Report.model_validate({
        "id": a.run_id,
        "created_at": eb.created_at.isoformat(),  # when the run started (researcher), not when analytics finished
        "is_mock": eb.is_fixture,
        "previous_run_id": previous_run_id,
        "input": {k: v for k, v in a.input.items() if k in ("indication", "mechanism", "modality", "stage", "biomarkers", "route")},
        "recommendation": RECOMMENDATION[v.recommendation],
        "confidence": v.confidence,
        "summary": summary,
        "sections": sections,
        "risks": [{"id": r.id, "title": r.title, "severity": r.severity, "description": _clean(r.description),
                   "claim_ids": [c for c in r.claim_ids if c in claim_ids]} for r in a.risks],
        "unknowns": [u.question for u in a.unknowns],
        "diligence_questions": [q.model_dump() for q in a.diligence_questions],
        "capital": a.capital.model_dump(),
        "evidence": [_evidence(e) for e in eb.evidence],
        "traces": _traces(researcher, a.traces, writer_traces),
    })
    return report


def _evidence(e: Any) -> dict[str, Any]:
    related = [i for i in e.related_evidence_ids if i != e.id]
    return {"id": e.id, "source": e.source, "url": e.url, "title": e.title, "snippet": e.snippet, "kind": e.kind,
            "modules": list(e.modules), "retrieved_at": e.retrieved_at.isoformat(),
            **({"related_evidence_ids": related} if related else {})}


def _traces(researcher: Bundle | None, analytics: list[AgentTrace], writer: list[AgentTrace]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if researcher is not None:
        for s in researcher.sources_status:
            out.append({"agent": "researcher", "step": "fetch", "tool": s.source, "input_tokens": 0, "output_tokens": 0,
                        "latency_s": s.duration_s, "cost_usd": 0.0})
        for c in researcher.llm_calls:
            out.append({"agent": "researcher", "step": c.purpose or "llm", "input_tokens": c.input_tokens or 0,
                        "output_tokens": c.output_tokens or 0, "latency_s": c.latency_s, "cost_usd": 0.0})
    for t in [*analytics, *writer]:
        if t.step == "total":
            continue
        out.append({"agent": t.agent, "step": t.step, "input_tokens": t.input_tokens, "output_tokens": t.output_tokens,
                    "latency_s": t.latency_s, "cost_usd": t.cost_usd})
    return out


def _verdict_summary(a: AnalysisResult) -> str:
    v = a.verdict
    rules = [r for r in v.rule_trace if r.startswith("RULE")]
    return f"{v.recommendation} (confidence {v.confidence:.0%})" + (f": {rules[-1][5:]}" if rules else ".")


def _clean(text: str) -> str:
    """Drop analytics' prompt-local short refs ([E3], [A1, E7]); the Report links evidence through claims."""
    return " ".join(_SHORT_REF.sub("", text or "").split())
