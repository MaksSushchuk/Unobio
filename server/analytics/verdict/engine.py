"""Step 6 — Verdict: deterministic rules over lens scores and conflict evidence. No LLM.

Why code and not an LLM: the recommendation must be reproducible (same inputs ->
same verdict), explainable to judges (every rule that fired is logged in
`rule_trace`) and tunable (all thresholds live in VerdictConfig).

Order of rules
  1. KILL      a same-target program already failed in this indication
               (conflict evidence with kill_signal) -> Do Not Invest
  2. FLOORS    science or clinical score at/below its floor -> Do Not Invest
  3. COMPOSITE weighted mean of available lens scores -> Invest / Conditional / Do Not Invest
  4. CAPS      conditions that forbid "Invest" (high-severity conflicts, failed lenses,
               weak science) -> at most Conditional
  5. CONFIDENCE how much the verdict can be trusted (coverage, agreement, evidence, kill)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from evidence_bundle import EvidenceBundle

from ..schemas import LENSES, Lens, LensResult, Recommendation, Verdict


@dataclass(frozen=True)
class VerdictConfig:
    weights: dict[str, float] = field(default_factory=lambda: {
        "science": 0.35, "clinical": 0.25, "market": 0.20, "investment": 0.20})
    invest_min_composite: float = 3.5  # composite >= this -> Invest (unless capped)
    reject_max_composite: float = 2.0  # composite <  this -> Do Not Invest
    invest_min_any_lens: int = 3  # every available lens must score >= this for Invest
    invest_min_science: int = 3
    science_floor: int = 1  # science <= floor -> Do Not Invest (biology contradicted)
    clinical_floor: int = 0  # clinical <= floor -> Do Not Invest
    max_failed_lenses_for_invest: int = 0


@dataclass
class _Trace:
    lines: list[str] = field(default_factory=list)

    def add(self, line: str) -> None:
        self.lines.append(line)


def decide(bundle: EvidenceBundle, lenses: dict[Lens, LensResult], cfg: VerdictConfig | None = None,
           kill_overrides: set[str] | None = None) -> Verdict:
    """`kill_overrides`: kill-signal evidence ids judged NOT target-related (e.g. by an adjudicator agent).
    An overridden kill no longer forces Do Not Invest but still caps the verdict at Conditional."""
    cfg = cfg or VerdictConfig()
    overrides = kill_overrides or set()
    t = _Trace()
    scores: dict[str, int | None] = {lens: (r.score if r.status == "ok" else None)
                                     for lens in LENSES if (r := lenses.get(lens)) is not None}
    available = {k: v for k, v in scores.items() if v is not None}
    failed = [k for k in LENSES if k not in available]
    t.add(f"lens scores: {', '.join(f'{k}={v}' for k, v in scores.items())}"
          + (f"; missing/failed: {', '.join(failed)}" if failed else ""))

    # ---- 1. KILL
    kills = [e for e in bundle.evidence if e.kind == "conflict" and e.data.get("kill_signal")]
    active_kills = [e for e in kills if e.id not in overrides]
    for e in kills:
        state = "OVERRIDDEN" if e.id in overrides else "active"
        t.add(f"kill signal ({state}): {e.data.get('rule')} — {e.title}")

    composite = _composite(available, cfg)
    if composite is not None:
        t.add(f"composite score {composite:.2f} (weights {_fmt_weights(available, cfg)})")

    if active_kills:
        t.add("RULE kill: a drug on the same target/pathway already failed in this indication -> Do Not Invest")
        return _verdict("Do Not Invest", bundle, lenses, scores, composite, t, cfg,
                        kill=True, kill_ids=[e.id for e in active_kills])

    # ---- 2. FLOORS
    sci, clin = available.get("science"), available.get("clinical")
    if sci is not None and sci <= cfg.science_floor:
        t.add(f"RULE science floor: science={sci} <= {cfg.science_floor} -> Do Not Invest")
        return _verdict("Do Not Invest", bundle, lenses, scores, composite, t, cfg)
    if clin is not None and clin <= cfg.clinical_floor:
        t.add(f"RULE clinical floor: clinical={clin} <= {cfg.clinical_floor} -> Do Not Invest")
        return _verdict("Do Not Invest", bundle, lenses, scores, composite, t, cfg)

    # ---- 3. COMPOSITE
    if composite is None:
        t.add("RULE no lens produced a score -> Conditional (insufficient analysis)")
        return _verdict("Conditional", bundle, lenses, scores, composite, t, cfg)
    if composite < cfg.reject_max_composite:
        t.add(f"RULE composite {composite:.2f} < {cfg.reject_max_composite} -> Do Not Invest")
        return _verdict("Do Not Invest", bundle, lenses, scores, composite, t, cfg)
    rec: Recommendation = "Invest" if composite >= cfg.invest_min_composite else "Conditional"
    t.add(f"RULE composite {composite:.2f} -> {rec}")

    # ---- 4. CAPS (only relevant when heading for Invest)
    if rec == "Invest":
        caps = _caps(bundle, available, failed, kills, cfg)
        for c in caps:
            t.add(f"CAP {c} -> at most Conditional")
        if caps:
            rec = "Conditional"
    elif kills:
        t.add("note: overridden kill signals keep the verdict at Conditional at best")
    return _verdict(rec, bundle, lenses, scores, composite, t, cfg)


# ----------------------------------------------------------------------------- helpers


def _composite(available: dict[str, int], cfg: VerdictConfig) -> float | None:
    if not available:
        return None
    total_w = sum(cfg.weights.get(k, 0) for k in available)
    if total_w == 0:
        return None
    return round(sum(cfg.weights.get(k, 0) * v for k, v in available.items()) / total_w, 2)


def _fmt_weights(available: dict[str, int], cfg: VerdictConfig) -> str:
    return ", ".join(f"{k} {cfg.weights.get(k, 0):.2f}" for k in available)


def _caps(bundle: EvidenceBundle, available: dict[str, int], failed: list[str], kills: list, cfg: VerdictConfig) -> list[str]:
    caps: list[str] = []
    if kills:
        caps.append("kill signals exist (overridden as not target-related, but unresolved)")
    high = [e for e in bundle.evidence if e.kind == "conflict" and e.data.get("severity") == "high"
            and not e.data.get("kill_signal")]
    if high:
        caps.append(f"{len(high)} high-severity conflict(s): " + "; ".join(e.data.get("rule", "?") for e in high[:3]))
    if len(failed) > cfg.max_failed_lenses_for_invest:
        caps.append(f"lens(es) without a result: {', '.join(failed)}")
    low = [f"{k}={v}" for k, v in available.items() if v < cfg.invest_min_any_lens]
    if low:
        caps.append(f"lens score below {cfg.invest_min_any_lens}: {', '.join(low)}")
    sci = available.get("science")
    if sci is not None and sci < cfg.invest_min_science:
        caps.append(f"science {sci} < {cfg.invest_min_science}")
    return caps


def _confidence(bundle: EvidenceBundle, lenses: dict[Lens, LensResult], scores: dict[str, int | None],
                kill_ids: list[str], t: _Trace) -> float:
    ok = [r for r in lenses.values() if r.status == "ok"]
    coverage = len(ok) / len(LENSES)
    vals = [v for v in scores.values() if v is not None]
    agreement = 1 - min(statistics.pstdev(vals) / 2.5, 1) if len(vals) > 1 else 0.5
    sourced = sum(1 for r in ok for c in r.claims if c.kind == "source_fact")
    evidence_strength = min(sourced / 12, 1)
    conf = 0.35 * coverage + 0.35 * agreement + 0.30 * evidence_strength
    parts = f"coverage {coverage:.2f}, agreement {agreement:.2f}, sourced claims {sourced}"
    if kill_ids:
        by_id = bundle.by_id()
        programs = {str(by_id[i].data.get("drug") or i) for i in kill_ids if i in by_id}
        floor = min(0.80 + 0.05 * (len(programs) - 1), 0.95)  # independent failed programs raise certainty
        conf = max(conf, floor)
        parts += f", kill signals from {len(programs)} program(s) -> floor {floor:.2f}"
    if bundle.is_fixture:
        parts += " (fixture bundle)"
    conf = round(min(max(conf, 0.2), 0.95), 2)
    t.add(f"confidence {conf:.2f} ({parts})")
    return conf


def _verdict(rec: Recommendation, bundle: EvidenceBundle, lenses: dict[Lens, LensResult],
             scores: dict[str, int | None], composite: float | None, t: _Trace, cfg: VerdictConfig,
             kill: bool = False, kill_ids: list[str] | None = None) -> Verdict:
    conf = _confidence(bundle, lenses, scores, kill_ids or [], t)
    t.add(f"VERDICT: {rec}")
    return Verdict(recommendation=rec, confidence=conf, kill_triggered=kill, kill_evidence_ids=kill_ids or [],
                   lens_scores=scores, composite_score=composite, rule_trace=t.lines)
