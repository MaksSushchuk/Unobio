"""Every number the financial model uses, in one place, with its basis.

These are order-of-magnitude industry benchmarks, deliberately shown as ranges.
They are printed into `assumptions` of the output so a reader can challenge them.
Change them here, not in the formulas.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Range = tuple[float, float, float]  # (low, base, high)

PHASES = ("preclinical", "phase1", "phase2", "phase3", "filed")

# Probability of moving to the next stage. Clinical values: BIO / Informa / QLS,
# "Clinical Development Success Rates 2011–2020" (all indications). Preclinical -> Ph1 is an assumption.
TRANSITION_POS: dict[str, float] = {
    "preclinical": 0.65,  # IND-enabling package -> first in human (assumption)
    "phase1": 0.52,
    "phase2": 0.29,
    "phase3": 0.58,
    "filed": 0.91,
}


@dataclass(frozen=True)
class FinanceAssumptions:
    # trial cost per enrolled patient, USD (low, base, high) — order of magnitude for interventional trials
    cost_per_patient: dict[str, Range] = field(default_factory=lambda: {
        "PHASE1": (25_000, 40_000, 70_000),
        "PHASE1B": (30_000, 50_000, 80_000),
        "PHASE2": (30_000, 50_000, 90_000),
        "PHASE2B": (30_000, 50_000, 90_000),
        "PHASE3": (30_000, 45_000, 80_000),
    })
    # program overhead while trials run (CMC, tox, regulatory, team), USD per year
    overhead_per_year: Range = (5e6, 8e6, 15e6)
    # IND-enabling package when starting from discovery / preclinical
    ind_enabling_usd: Range = (3e6, 5e6, 8e6)
    ind_enabling_months: tuple[int, int] = (12, 18)
    # defaults when a phase is not described by the investment analyst
    default_trials: dict[str, tuple[int, int, int, int]] = field(default_factory=lambda: {
        # phase: (patients_low, patients_high, months_low, months_high)
        "PHASE1": (40, 80, 9, 15),
        "PHASE2": (80, 200, 18, 30),
        "PHASE3": (300, 900, 30, 48),
    })
    filing_usd: float = 5e6
    review_months: int = 12
    # commercial model
    ramp_years: int = 5  # linear ramp from launch to peak
    plateau_years: int = 6  # years at peak before loss of exclusivity
    erosion_per_year: float = 0.5  # sales fall by this fraction per year after exclusivity
    operating_margin: Range = (0.25, 0.35, 0.45)
    discount_rate: Range = (0.15, 0.12, 0.10)  # low scenario discounts hardest
    # PoS haircut when a same-target program already failed in this indication (verdict kill signal)
    kill_phase2_multiplier: float = 0.25

    def lines(self) -> list[str]:
        return [
            "Phase transition probabilities (BIO/Informa/QLS 2011–2020, all indications): "
            + ", ".join(f"{k}->{v:.0%}" for k, v in TRANSITION_POS.items()) + " (preclinical value is an assumption)",
            "Trial cost per patient (USD, low/base/high): "
            + "; ".join(f"{p} {r[0]/1e3:.0f}k/{r[1]/1e3:.0f}k/{r[2]/1e3:.0f}k" for p, r in self.cost_per_patient.items()),
            f"Program overhead while trials run: ${self.overhead_per_year[0]/1e6:.0f}M/${self.overhead_per_year[1]/1e6:.0f}M/"
            f"${self.overhead_per_year[2]/1e6:.0f}M per year",
            f"Sales: linear ramp to peak over {self.ramp_years} y, {self.plateau_years} y at peak, then -{self.erosion_per_year:.0%}/y",
            f"Operating margin {self.operating_margin[0]:.0%}/{self.operating_margin[1]:.0%}/{self.operating_margin[2]:.0%}; "
            f"discount rate {self.discount_rate[0]:.0%}/{self.discount_rate[1]:.0%}/{self.discount_rate[2]:.0%} (low/base/high scenario)",
        ]
