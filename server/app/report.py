"""analysis_result + evidence bundle -> `Report` JSON for the web UI (web/src/types.ts).

Every decision field comes straight from the analytics output or the evidence bundle, so the
traceability chain Recommendation -> Claims -> Evidence -> Sources stays intact. Only two texts may come
from the writer's narrative (an LLM): `summary` (recommendation rationale) and each section's `summary`
(takeaway); without a narrative they fall back to deterministic text. Claim evidence lists derived items
followed by the primary records behind them (app/sources.py). Extra fields (`details`, `warnings`,
`downloads`) are ignored by the current UI.
"""
from __future__ import annotations

import re
from typing import Any

from analytics.schemas import AnalysisResult
from evidence_bundle import EvidenceBundle

from .sources import expand_refs

RECOMMENDATION = {"Invest": "invest", "Conditional": "conditional", "Do Not Invest": "do_not_invest"}

SECTION_TITLES = {
    "science": "Scientific & translational thesis",
    "clinical": "Clinical development",
    "market": "Competitive landscape & commercial opportunity",
    "investment": "Investment case",
}

_SHORT_ID = re.compile(r"\s*\[(?:[EA]\d+(?:\s*,\s*)?)+\]")


def strip_short_ids(text: str) -> str:
    """'... failed [E3, E7].' -> '... failed.' (short ids mean nothing outside the prompt)."""
    text = _SHORT_ID.sub("", text or "")
    text = re.sub(r"\s*,(?:\s*,)+", ",", text)  # "[E4], [E3], [E5]" leaves ", ," behind
    text = re.sub(r"[\s,]+([.;:)]|$)", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def build_report(bundle: EvidenceBundle, result: AnalysisResult, previous_run_id: str | None = None,
                 extra_traces: list[dict[str, Any]] | None = None, narrative: Any | None = None) -> dict[str, Any]:
    """`narrative` is the writer's report object (writer_agent.agent.Report: rationale, narratives) or None."""
    v = result.verdict
    by_id = bundle.by_id()
    takeaways = {k: n.takeaway for k, n in (getattr(narrative, "narratives", None) or {}).items()
                 if n is not None and getattr(n, "takeaway", "")}
    rationale = (getattr(narrative, "rationale", "") or "").strip()
    inp = bundle.input
    report_input = {"indication": inp.indication, "mechanism": inp.mechanism}
    for key in ("modality", "stage", "route", "biomarkers"):
        value = getattr(inp, key, None)
        if value:
            report_input[key] = value

    sections = []
    for lens, r in result.lenses.items():
        sections.append({
            "id": lens,
            "title": SECTION_TITLES.get(lens, lens.title()),
            "summary": _section_summary(r.status, r.score, takeaways.get(lens) or r.score_rationale, r.errors),
            "claims": [
                {"id": c.id, "text": strip_short_ids(c.text), "kind": c.kind, "confidence": c.confidence,
                 "evidence": [{"evidence_id": eid, "stance": stance} for eid, stance in
                              expand_refs(((e.evidence_id, e.stance) for e in c.evidence), by_id, keep_derived=True)]}
                for c in r.claims
            ],
        })

    capital = result.capital.model_dump() if result.capital else {
        "milestone": "not estimated", "months_low": 0, "months_high": 0,
        "usd_low": 0, "usd_base": 0, "usd_high": 0, "assumptions": ["Finance step produced no estimate."],
    }

    return {
        "id": result.run_id,
        "created_at": result.created_at.isoformat(),
        "is_mock": bool(bundle.is_fixture),
        "previous_run_id": previous_run_id,
        "input": report_input,
        "recommendation": RECOMMENDATION[v.recommendation],
        "confidence": v.confidence,
        "summary": f"{v.recommendation} (confidence {v.confidence:.0%}). {rationale}" if rationale
        else _summary(bundle, result),
        "sections": sections,
        "risks": [
            {"id": r.id, "title": r.title, "severity": r.severity, "description": strip_short_ids(r.description),
             "claim_ids": r.claim_ids}
            for r in result.risks
        ],
        "unknowns": [strip_short_ids(u.question) for u in result.unknowns],
        "diligence_questions": [{"question": strip_short_ids(q.question), "rationale": strip_short_ids(q.rationale),
                                 "requires": q.requires} for q in result.diligence_questions],
        "capital": capital,
        "evidence": [_evidence(e) for e in bundle.evidence],
        "traces": [_trace(t.model_dump()) for t in result.traces if t.step != "total"] + (extra_traces or []),
        "details": {
            "evidence_cutoff": str(inp.evidence_cutoff) if inp.evidence_cutoff else None,
            "lens_scores": v.lens_scores,
            "composite_score": v.composite_score,
            "kill_triggered": v.kill_triggered,
            "kill_evidence_ids": v.kill_evidence_ids,
            "rule_trace": v.rule_trace,
            "rnpv": result.rnpv.model_dump() if result.rnpv else None,
            "cutoff_policy": bundle.cutoff_policy,
        },
    }


def _section_summary(status: str, score: int | None, rationale: str, errors: list[str]) -> str:
    if status == "skipped":
        return f"Not assessed: {errors[0] if errors else 'no evidence'}; listed under critical unknowns."
    if status != "ok":
        return f"Analyst failed: {errors[0] if errors else 'no valid answer'}"
    return f"Score {score}/5. {strip_short_ids(rationale)}".strip()


def _summary(bundle: EvidenceBundle, result: AnalysisResult) -> str:
    v = result.verdict
    parts = [f"{v.recommendation} (confidence {v.confidence:.0%})."]
    if v.kill_triggered:
        by_id = bundle.by_id()
        titles = [by_id[i].title for i in v.kill_evidence_ids if i in by_id][:2]
        parts.append("A drug on the same target or pathway already failed in this indication: "
                     + "; ".join(titles) + ".")
    else:
        rules = [_rule_text(line) for line in v.rule_trace if line.startswith("RULE")]
        if rules:
            parts.append(rules[-1])
    scores = ", ".join(f"{k} {s}/5" for k, s in v.lens_scores.items() if s is not None)
    if scores:
        parts.append(f"Analyst scores: {scores}.")
    if bundle.input.evidence_cutoff:
        parts.append(f"Evidence as known on {bundle.input.evidence_cutoff}.")
    if result.risks:
        parts.append(f"Top risk: {strip_short_ids(result.risks[0].title)}.")
    return " ".join(parts)


def _rule_text(line: str) -> str:
    """'RULE science floor: science=1 <= 1 -> Do Not Invest' -> 'Rule "science floor": science=1 <= 1 -> Do Not Invest.'"""
    text = line[len("RULE"):].strip()
    name, sep, rest = text.partition(":")
    body = f'Rule "{name.strip()}": {rest.strip()}' if sep and len(name) < 30 else f"Rule: {text}"
    return body.rstrip(".") + "."


def _evidence(e: Any) -> dict[str, Any]:
    out = {
        "id": e.id, "source": e.source, "url": e.url, "title": e.title, "snippet": e.snippet, "kind": e.kind,
        "modules": list(e.modules), "retrieved_at": e.retrieved_at.isoformat(),
    }
    related = [r for r in e.related_evidence_ids if r != e.id]
    if related:
        out["related_evidence_ids"] = related
    return out


def _trace(t: dict[str, Any]) -> dict[str, Any]:
    out = {"agent": t["agent"], "step": t["step"], "input_tokens": t.get("input_tokens", 0),
           "output_tokens": t.get("output_tokens", 0), "latency_s": t.get("latency_s", 0.0),
           "cost_usd": t.get("cost_usd", 0.0)}
    if t.get("model"):
        out["tool"] = t["model"]
    return out


def researcher_traces(bundle: EvidenceBundle, research_seconds: float | None = None) -> list[dict[str, Any]]:
    """Researcher work as UI traces: one row per source and per researcher LLM call."""
    extra = getattr(bundle, "researcher", None) or {}
    rows: list[dict[str, Any]] = []
    for s in extra.get("sources_status") or []:
        rows.append({"agent": "researcher", "step": f"source:{s.get('source')}", "tool": s.get("source"),
                     "input_tokens": 0, "output_tokens": 0, "latency_s": round(s.get("duration_s") or 0.0, 3),
                     "cost_usd": 0.0})
    for c in extra.get("llm_calls") or []:
        rows.append({"agent": "researcher", "step": c.get("purpose") or "llm", "tool": c.get("model"),
                     "input_tokens": c.get("input_tokens") or 0, "output_tokens": c.get("output_tokens") or 0,
                     "latency_s": round(c.get("latency_s") or 0.0, 3), "cost_usd": 0.0})
    if research_seconds is not None:
        rows.append({"agent": "researcher", "step": "total", "input_tokens": 0, "output_tokens": 0,
                     "latency_s": round(research_seconds, 3), "cost_usd": 0.0})
    return rows
