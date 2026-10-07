"""Critical unknowns: what public evidence cannot answer.

Merged from every lens (`LensResult.unknowns`) and from the researcher's `gaps`,
then de-duplicated by wording similarity (the same question asked by two lenses
appears once).
"""
from __future__ import annotations

from evidence_bundle import EvidenceBundle

from ..schemas import LENSES, Lens, LensResult, Unknown
from .text import similar

MAX_UNKNOWNS = 12


def merge_unknowns(bundle: EvidenceBundle, lenses: dict[Lens, LensResult]) -> list[Unknown]:
    candidates: list[Unknown] = []
    for lens in LENSES:
        r = lenses.get(lens)
        if r is not None and r.status == "ok":
            candidates += r.unknowns
    candidates += [Unknown(question=g.question, requires=g.requires, why=g.reason) for g in bundle.gaps]

    out: list[Unknown] = []
    for u in candidates:
        if not any(similar(u.question, o.question) for o in out):
            out.append(u)
    return out[:MAX_UNKNOWNS]
