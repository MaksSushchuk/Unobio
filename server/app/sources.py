"""Claim-level sources: every claim points at the primary records it rests on (trials, papers, database
entries), never only at an internal derived item.

Analysts may cite evidence the researcher DERIVED from other records (reconcile rules such as
"stop_reason" or "target_failure", conflicts). Such an item's URL is usually just one of its inputs, so a
source list built from it says "reconciliation" and hides the actual studies. `expand_refs` replaces each
derived item by the primary records in its `related_evidence_ids` (keeping the derived item first in the
UI, so the reasoning stays visible), and `source_entry` describes a record the way a reference list needs:
database, identifier (NCT / PMID / ...), year, URL.
"""
from __future__ import annotations

from typing import Any, Iterable

from evidence_bundle import Evidence

DERIVED_SOURCES = {"reconcile", "reconciliation", "conflict"}

DATABASE_LABELS = {
    "clinicaltrials": "ClinicalTrials.gov",
    "opentargets": "Open Targets",
    "pubmed": "PubMed",
    "openfda": "openFDA",
    "chembl": "ChEMBL",
    "europepmc": "Europe PMC",
    "web": "Web",
}

_ID_KEYS = (("nct_id", "NCT"), ("pmid", "PMID"), ("doi", "DOI"), ("set_id", "SPL"), ("chembl_id", "ChEMBL"))


def is_derived(e: Evidence) -> bool:
    return e.source in DERIVED_SOURCES or (e.kind == "conflict" and bool(e.related_evidence_ids))


def primary_ids(evidence_id: str, by_id: dict[str, Evidence], _depth: int = 3) -> list[str]:
    """Primary records behind an evidence id (itself if it is primary or has no resolvable inputs)."""
    e = by_id.get(evidence_id)
    if e is None or not is_derived(e) or _depth == 0:
        return [evidence_id]
    out: list[str] = []
    for rid in e.related_evidence_ids:
        if rid == evidence_id or rid not in by_id:
            continue
        for pid in primary_ids(rid, by_id, _depth - 1):
            if pid not in out:
                out.append(pid)
    return out or [evidence_id]


def expand_refs(refs: Iterable[tuple[str, str]], by_id: dict[str, Evidence],
                keep_derived: bool) -> list[tuple[str, str]]:
    """[(evidence_id, stance)] with derived items expanded into their primary records (same stance).
    keep_derived=True keeps the derived item before its inputs (UI); False drops it (reference lists)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for eid, stance in refs:
        prim = primary_ids(eid, by_id)
        ids = ([eid] if keep_derived and prim != [eid] else []) + prim
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append((i, stance))
    return out


def identifier(e: Evidence) -> str:
    refs = {**e.entity_refs, **e.data}
    for key, label in _ID_KEYS:
        value = refs.get(key)
        if value:
            value = str(value)
            return value if value.upper().startswith(label.upper()) else f"{label} {value}"
    return ""


def source_entry(e: Evidence) -> dict[str, Any]:
    """One entry of a numbered reference list."""
    return {
        "id": e.id,
        "title": e.title,
        "url": e.url,
        "database": DATABASE_LABELS.get(e.source, "Derived (cross-source check)" if is_derived(e) else e.source),
        "identifier": identifier(e),
        "year": str(e.published_at.year) if e.published_at else "",
    }
