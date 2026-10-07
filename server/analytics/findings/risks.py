"""Key risks and failure modes, assembled by code from three sources.

1. Conflict evidence from the researcher (cross-source contradictions) — the strongest signal.
2. Weak lenses: lenses scoring <= 2 form one combined risk, explained by the weakest lens' rationale.
3. Translation-chain gaps from the science lens (missing / contradicted links).

Every risk links back to evidence ids and to the claims that cite them, so the UI can
drill Risk -> Claims -> Evidence -> Sources.
"""
from __future__ import annotations

from evidence_bundle import EvidenceBundle

from ..schemas import LENSES, Lens, LensResult, Risk, Severity
from .text import short_hash, similar

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
MAX_RISKS = 10


def claims_citing(lenses: dict[Lens, LensResult], evidence_ids: set[str]) -> list[str]:
    return [c.id for r in lenses.values() for c in r.claims if {e.evidence_id for e in c.evidence} & evidence_ids]


def build_risks(bundle: EvidenceBundle, lenses: dict[Lens, LensResult]) -> list[Risk]:
    ranked: list[tuple[int, Risk]] = []  # (priority, risk): lower priority value = shown first

    # 1. researcher conflicts (kill signals first)
    for e in bundle.conflicts():
        conflict_sev = str(e.data.get("severity"))
        if conflict_sev not in SEVERITY_ORDER:
            continue  # "info" = consistency signal, not a risk
        ids = {e.id, *e.related_evidence_ids}
        kill = bool(e.data.get("kill_signal"))
        ranked.append((0 if kill else 1, Risk(
            id=short_hash(e.id, prefix="R"), title=e.title, severity=conflict_sev,  # type: ignore[arg-type]
            description=e.snippet + (" Same-target failure in this indication." if kill else ""),
            evidence_ids=sorted(ids), claim_ids=claims_citing(lenses, ids))))

    # 2. weak lenses — one combined risk, explained by the weakest lens' rationale
    weak = [(lens, r) for lens in LENSES if (r := lenses.get(lens)) is not None
            and r.status == "ok" and r.score is not None and r.score <= 2]
    if weak:
        weakest = min(weak, key=lambda x: x[1].score or 0)[1]
        sev: Severity = "high" if (weakest.score or 0) <= 1 else "medium"
        against = [c for _, r in weak for c in r.claims if any(e.stance == "contradicts" for e in c.evidence)] \
            or [c for _, r in weak for c in r.claims[:1]]
        ranked.append((2, Risk(
            id=short_hash("weak", *[lens for lens, _ in weak], prefix="R"),
            title="Low analyst scores: " + ", ".join(f"{lens} {r.score}/5" for lens, r in weak),
            severity=sev, description=weakest.score_rationale,
            claim_ids=[c.id for c in against[:6]],
            evidence_ids=sorted({e.evidence_id for c in against[:6] for e in c.evidence}))))

    # 3. translation-chain gaps
    sci = lenses.get("science")
    if sci is not None and sci.status == "ok":
        for link in sci.params.get("translation_chain", []):
            status = link.get("status")
            if status not in {"missing", "contradicted"}:
                continue
            step = str(link.get("step", "?")).replace("_", " ")
            ranked.append((3, Risk(
                id=short_hash("chain", step, prefix="R"),
                title=f"Translation gap: {step} {'contradicted' if status == 'contradicted' else 'not demonstrated'}",
                severity="high" if status == "contradicted" else "medium",
                description=f"The chain molecular effect -> exposure -> engagement -> response -> benefit has a "
                            f"{status} link at '{step}'.",
                evidence_ids=list(link.get("evidence_ids", [])),
                claim_ids=claims_citing(lenses, set(link.get("evidence_ids", []))),
            )))

    ranked.sort(key=lambda pr: (SEVERITY_ORDER[pr[1].severity], pr[0], pr[1].title))
    return _dedupe([r for _, r in ranked])[:MAX_RISKS]


def _dedupe(risks: list[Risk]) -> list[Risk]:
    out: list[Risk] = []
    for r in risks:  # already ranked
        twin = next((o for o in out if similar(o.title, r.title, 0.7)), None)
        if twin is None:
            out.append(r)
        else:  # merge evidence of near-duplicates into the higher-severity one
            twin.evidence_ids = sorted(set(twin.evidence_ids) | set(r.evidence_ids))
            twin.claim_ids = sorted(set(twin.claim_ids) | set(r.claim_ids))
    return out
