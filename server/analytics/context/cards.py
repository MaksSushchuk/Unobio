"""Render evidence as compact text "cards" for the LLM.

Why not raw JSON: JSON keys, URLs, timestamps and hashes cost tokens but add
nothing to the judgement. A card keeps only what an analyst needs to reason
about the fact, and prefixes it with its short id so it can be cited.
"""
from __future__ import annotations

from evidence_bundle import Evidence, EvidenceBundle

from .short_ids import ShortIdMap


def _date(e: Evidence) -> str:
    return e.published_at.isoformat() if e.published_at else "n.d."


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def full_card(e: Evidence, ids: ShortIdMap, snippet_limit: int) -> str:
    sid = ids.short(e.id)
    if e.kind == "conflict":
        rule = e.data.get("rule", "?")
        sev = e.data.get("severity", "?")
        kill = ", KILL SIGNAL" if e.data.get("kill_signal") else ""
        related = ", ".join(ids.short(r) for r in e.related_evidence_ids if ids.has(r))
        return (f"[{sid}] CONFLICT ({sev}{kill}) rule={rule}\n"
                f"  {_cut(e.title, 200)}\n"
                f"  {_cut(e.snippet, snippet_limit)}\n"
                f"  based on: {related or 'n/a'}")
    synthetic = " [placeholder]" if e.data.get("synthetic") else ""
    return (f"[{sid}] {e.source} · {e.kind} · {_date(e)}{synthetic}\n"
            f"  {_cut(e.title, 200)}\n"
            f"  {_cut(e.snippet, snippet_limit)}")


def brief_card(e: Evidence, ids: ShortIdMap, limit: int) -> str:
    return f"[{ids.short(e.id)}] {e.source} · {_date(e)} · {_cut(e.title, limit)}"


def aggregate_card(agg_id: str, title: str, members: list[Evidence], ids: ShortIdMap, line_limit: int) -> str:
    lines = [f"[{agg_id}] AGGREGATE · {title} ({len(members)} items; cite {agg_id} or individual ids)"]
    for m in members:
        status = m.data.get("status")
        extra = f" · {status}" if status else ""
        lines.append(f"  - {ids.short(m.id)}{extra} · {_cut(m.title, line_limit)}")
    return "\n".join(lines)


def subject_header(bundle: EvidenceBundle) -> str:
    """Shared prefix for every lens prompt (identical text -> cacheable)."""
    s, inp = bundle.subject, bundle.input
    targets = ", ".join(f"{t.symbol} ({t.role})" for t in s.targets)
    drugs = "; ".join(
        f"{d.name} [{'/'.join(d.target_symbols) or '?'}, {d.match}"
        + (f", aka {', '.join(d.synonyms[:3])}" if d.synonyms else "")
        + (f", max stage {d.max_stage}" if d.max_stage else "") + "]"
        for d in s.drugs
    )
    lines = [
        "SUBJECT",
        f"  Indication: {inp.indication} -> {s.disease.name} ({s.disease.id})",
        f"  Mechanism:  {inp.mechanism} -> targets {targets}; action: {s.action or 'n/a'}",
        f"  Drugs on target / family: {drugs or 'none found'}",
    ]
    for field in ("modality", "stage", "route"):
        if getattr(inp, field, None):
            lines.append(f"  {field.capitalize()}: {getattr(inp, field)}")
    if inp.biomarkers:
        lines.append(f"  Biomarkers: {', '.join(inp.biomarkers)}")
    if inp.evidence_cutoff:
        lines.append(f"  EVIDENCE CUTOFF: {inp.evidence_cutoff} — only facts public by this date are provided. "
                     "Do not use knowledge of later events.")
    if bundle.gaps:
        lines.append("KNOWN GAPS (not answerable from public data)")
        lines += [f"  - {g.question} [requires: {g.requires}]" for g in bundle.gaps]
    return "\n".join(lines)
