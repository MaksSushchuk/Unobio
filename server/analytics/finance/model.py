"""Step 7 — capital to the next milestone and risk-adjusted NPV (rNPV). Pure code, no LLM.

Inputs
  * investment lens `params` (stage, next milestone, trials to milestone, peak sales range)
  * bundle `trial_benchmarks` (fallback for trial sizes / durations)
  * verdict kill flag (a failed same-target program cuts Phase-2 success probability)

rNPV in one line:
  rNPV = P(approval) * PV(commercial cash flows)  -  sum_over_phases P(reach phase) * PV(phase cost)

Three scenarios instead of one number: low (pessimistic on every input), base, high.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evidence_bundle import EvidenceBundle

from ..schemas import RNPV, CapitalEstimate, LensResult, Verdict
from .assumptions import PHASES, TRANSITION_POS, FinanceAssumptions

STAGE_TO_PHASE = {"discovery": "preclinical", "preclinical": "preclinical", "phase1": "phase1",
                  "phase2": "phase2", "phase3": "phase3", "filed": "filed", "approved": "approved"}
TRIAL_KEY = {"phase1": "PHASE1", "phase2": "PHASE2", "phase3": "PHASE3"}


@dataclass
class Trial:
    phase: str  # PHASE1 / PHASE2 / ...
    patients: tuple[int, int]
    months: tuple[int, int]
    source: str  # "investment lens" | "trial benchmarks" | "default"


@dataclass
class FinanceResult:
    capital: CapitalEstimate
    rnpv: RNPV | None
    notes: list[str]


def build_finance(bundle: EvidenceBundle, investment: LensResult | None, verdict: Verdict | None = None,
                  a: FinanceAssumptions | None = None) -> FinanceResult:
    a = a or FinanceAssumptions()
    p: dict[str, Any] = investment.params if investment is not None and investment.status == "ok" else {}
    notes: list[str] = []
    if not p:
        notes.append("Investment lens unavailable: stage, trials and peak sales fall back to benchmarks/defaults.")

    stage = STAGE_TO_PHASE.get(str(p.get("current_stage") or "preclinical"), "preclinical")
    milestone = str(p.get("next_milestone") or "Phase 2 proof-of-concept readout")
    trials = _trials_to_milestone(p, bundle, a, notes)
    capital = _capital(stage, milestone, trials, a)
    capital.assumptions = notes + capital.assumptions

    rnpv = None
    lo, base, hi = (_positive(p.get(k)) for k in ("peak_sales_usd_low", "peak_sales_usd_base", "peak_sales_usd_high"))
    if stage != "approved" and lo is not None and base is not None and hi is not None:
        rnpv = _rnpv(stage, trials, (lo, base, hi), bundle, a, kill=bool(verdict and verdict.kill_triggered))
    else:
        notes.append("rNPV not computed: no peak-sales range from the investment lens (or asset already approved).")
    return FinanceResult(capital=capital, rnpv=rnpv, notes=notes)


def _positive(x: object) -> float | None:
    return float(x) if isinstance(x, (int, float)) and x > 0 else None


# ----------------------------------------------------------------------------- trials


def _trials_to_milestone(p: dict[str, Any], bundle: EvidenceBundle, a: FinanceAssumptions, notes: list[str]) -> list[Trial]:
    out: list[Trial] = []
    for t in p.get("trials_to_milestone") or []:
        out.append(Trial(t["phase"], (int(t["patients_low"]), int(t["patients_high"])),
                         (int(t["months_low"]), int(t["months_high"])), "investment lens"))
    if out:
        return out
    # fallback: Ph1 default + Ph2 from the indication's benchmarks
    ph1 = a.default_trials["PHASE1"]
    out.append(Trial("PHASE1", (ph1[0], ph1[1]), (ph1[2], ph1[3]), "default"))
    out.append(_benchmark_trial("PHASE2", bundle, a))
    notes.append("Trials to milestone not provided by the analyst: Ph1 default + Ph2 from trial benchmarks.")
    return out


def _benchmark_trial(phase: str, bundle: EvidenceBundle, a: FinanceAssumptions) -> Trial:
    b = (bundle.trial_benchmarks.get("by_phase") or {}).get(phase) or {}
    n, m = b.get("enrollment_median"), b.get("duration_months_median")
    if n and m:
        return Trial(phase, (int(n * 0.7), int(n * 1.3)), (int(m * 0.7), int(m * 1.3)), "trial benchmarks")
    d = a.default_trials[phase]
    return Trial(phase, (d[0], d[1]), (d[2], d[3]), "default")


# ----------------------------------------------------------------------------- capital


def _capital(stage: str, milestone: str, trials: list[Trial], a: FinanceAssumptions) -> CapitalEstimate:
    usd = [0.0, 0.0, 0.0]
    months = [0, 0]
    lines: list[str] = []
    if stage == "preclinical":
        for i in range(3):
            usd[i] += a.ind_enabling_usd[i]
        months[0] += a.ind_enabling_months[0]
        months[1] += a.ind_enabling_months[1]
        lines.append(f"IND-enabling package ${a.ind_enabling_usd[1]/1e6:.0f}M base, "
                     f"{a.ind_enabling_months[0]}–{a.ind_enabling_months[1]} months")
    for t in trials:
        c = a.cost_per_patient.get(t.phase, a.cost_per_patient["PHASE2"])
        mid = (t.patients[0] + t.patients[1]) / 2
        usd[0] += t.patients[0] * c[0]
        usd[1] += mid * c[1]
        usd[2] += t.patients[1] * c[2]
        months[0] += t.months[0]
        months[1] += t.months[1]
        lines.append(f"{t.phase}: {t.patients[0]}–{t.patients[1]} patients, {t.months[0]}–{t.months[1]} months "
                     f"({t.source}); ${c[1]/1e3:.0f}k per patient base")
    mid_months = (months[0] + months[1]) / 2
    usd[0] += a.overhead_per_year[0] * months[0] / 12
    usd[1] += a.overhead_per_year[1] * mid_months / 12
    usd[2] += a.overhead_per_year[2] * months[1] / 12
    lines.append(f"Program overhead ${a.overhead_per_year[1]/1e6:.0f}M/year base over the trial period (sequential trials)")
    return CapitalEstimate(milestone=milestone, months_low=months[0], months_high=months[1],
                           usd_low=round(usd[0], -5), usd_base=round(usd[1], -5), usd_high=round(usd[2], -5),
                           assumptions=lines)


# ----------------------------------------------------------------------------- rNPV


def _phases_ahead(stage: str) -> list[str]:
    return list(PHASES[PHASES.index(stage):]) if stage in PHASES else []


def _rnpv(stage: str, trials: list[Trial], peak: tuple[float, float, float], bundle: EvidenceBundle,
          a: FinanceAssumptions, kill: bool) -> RNPV:
    pos = dict(TRANSITION_POS)
    lines = a.lines()
    if kill:
        pos["phase2"] *= a.kill_phase2_multiplier
        lines.append(f"Kill signal (same-target failure in this indication): Phase 2 success probability "
                     f"x{a.kill_phase2_multiplier} -> {pos['phase2']:.0%}")
    ahead = _phases_ahead(stage)
    p_approval = 1.0
    for ph in ahead:
        p_approval *= pos[ph]

    # phase durations (years) and costs (low/base/high), using analyst trials where given
    by_phase = {t.phase: t for t in trials}
    durations: dict[str, float] = {}
    costs: dict[str, tuple[float, float, float]] = {}
    for ph in ahead:
        if ph == "preclinical":
            durations[ph] = sum(a.ind_enabling_months) / 2 / 12
            costs[ph] = a.ind_enabling_usd
            continue
        if ph == "filed":
            durations[ph] = a.review_months / 12
            costs[ph] = (a.filing_usd, a.filing_usd, a.filing_usd)
            continue
        key = TRIAL_KEY[ph]
        t = by_phase.get(key) or _benchmark_trial(key, bundle, a)
        c = a.cost_per_patient[key]
        yrs = (t.months[0] + t.months[1]) / 2 / 12
        durations[ph] = yrs
        costs[ph] = (t.patients[0] * c[0] + a.overhead_per_year[0] * yrs,
                     (t.patients[0] + t.patients[1]) / 2 * c[1] + a.overhead_per_year[1] * yrs,
                     t.patients[1] * c[2] + a.overhead_per_year[2] * yrs)

    years_to_launch = sum(durations.values())
    values = []
    for i in range(3):  # 0 low, 1 base, 2 high scenario
        rate, margin = a.discount_rate[i], a.operating_margin[i]
        cost_idx = 2 - i  # low scenario takes the high cost
        pv_costs, t_start, p_reach = 0.0, 0.0, 1.0
        for ph in ahead:
            pv_costs += p_reach * costs[ph][cost_idx] / (1 + rate) ** t_start
            t_start += durations[ph]
            p_reach *= pos[ph]
        pv_sales = sum(_sales(peak[i], y, a) * margin / (1 + rate) ** (years_to_launch + y + 0.5) for y in range(20))
        values.append(round(p_approval * pv_sales - pv_costs, -5))

    lines.append(f"Stage {stage}; phases ahead: {', '.join(ahead)}; ~{years_to_launch:.1f} years to launch")
    lines.append(f"Peak sales (investment lens) ${peak[0]/1e6:.0f}M / ${peak[1]/1e6:.0f}M / ${peak[2]/1e6:.0f}M")
    lines.append(f"Cumulative probability of approval {p_approval:.1%}")
    return RNPV(usd_low=values[0], usd_base=values[1], usd_high=values[2],
                probability_of_success=round(p_approval, 4), assumptions=lines)


def _sales(peak: float, year: int, a: FinanceAssumptions) -> float:
    """Sales in year `year` after launch (0-based)."""
    if year < a.ramp_years:
        return peak * (year + 1) / a.ramp_years
    if year < a.ramp_years + a.plateau_years:
        return peak
    return peak * (1 - a.erosion_per_year) ** (year - a.ramp_years - a.plateau_years + 1)
