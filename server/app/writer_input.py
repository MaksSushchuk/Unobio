"""analysis_result + evidence bundle -> Writer input JSON (writer/docs/INPUT_SCHEMA.md).

The Writer (teammate module, `writer/writer_agent`) defines its own provisional input contract
and asks for an adapter in front of it instead of changes inside it. This is that adapter.

Sources: each claim's evidence is expanded to the PRIMARY records (trials, papers, database entries) via
app/sources.py, so the PDF's "Sources [n]" next to a claim points at the actual studies.
"""
from __future__ import annotations

from typing import Any

from analytics.schemas import AnalysisResult
from evidence_bundle import EvidenceBundle

from .report import SECTION_TITLES, strip_short_ids
from .sources import expand_refs, source_entry

# Verdict wording and analyst keys as analytics produces them (passed to WriterConfig).
WRITER_VERDICTS = ("Invest", "Conditional", "Do Not Invest")
WRITER_ANALYSTS = ("science", "clinical", "market", "investment")

_PANEL_ITEMS = 8
_EXCERPT_CHARS = 400


def build_writer_input(bundle: EvidenceBundle, result: AnalysisResult) -> dict[str, Any]:
    inp = bundle.input
    by_id = bundle.by_id()

    analysts = []
    cited: list[str] = []
    for lens, r in result.lenses.items():
        claims = []
        for c in r.claims:
            refs = expand_refs(((ev.evidence_id, ev.stance) for ev in c.evidence), by_id, keep_derived=False)
            cited += [eid for eid, _ in refs if eid not in cited]
            claims.append({
                "id": c.id, "text": strip_short_ids(c.text), "confidence": c.confidence, "claim_type": c.kind,
                "evidence": [{"source_id": eid, "stance": "supporting" if stance == "supports" else "contradicting",
                              "excerpt": by_id[eid].snippet[:_EXCERPT_CHARS] if eid in by_id else ""}
                             for eid, stance in refs],
            })
        analysts.append({"key": lens, "title": SECTION_TITLES.get(lens, lens),
                         "status": {"ok": "done", "skipped": "skipped"}.get(r.status, "failed"), "claims": claims})

    cap = result.capital
    return {
        "schema_version": "1.0",
        "run": {"id": result.run_id, "timestamp": result.created_at.isoformat(), "simulated": False,
                "fixture_data": bool(bundle.is_fixture)},
        "thesis": {"indication": inp.indication, "mechanism": inp.mechanism, "modality": inp.modality or "",
                   "stage": inp.stage or "", "biomarkers": inp.biomarkers or [], "route": inp.route or ""},
        "recommendation": {"verdict": result.verdict.recommendation, "confidence": result.verdict.confidence},
        "capital_to_milestone": None if cap is None else {
            "milestone": cap.milestone, "duration": f"{cap.months_low}-{cap.months_high} months", "currency": "USD",
            "low": cap.usd_low, "base": cap.usd_base, "high": cap.usd_high, "assumptions": cap.assumptions,
        },
        "sources": [source_entry(by_id[eid]) for eid in cited if eid in by_id],
        "analysts": analysts,
        "panels": _panels(bundle, result),
    }


def _panels(bundle: EvidenceBundle, result: AnalysisResult) -> dict[str, Any]:
    """Appendix panels: per Unobio module, the titles of the evidence tagged with it, plus the verdict log."""
    panels: dict[str, Any] = {}
    for e in bundle.evidence:
        for m in e.modules:
            items = panels.setdefault(m, [])
            if len(items) < _PANEL_ITEMS:
                items.append(e.title)
    panels["verdict-rules"] = result.verdict.rule_trace
    return panels
