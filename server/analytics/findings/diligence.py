"""5–10 highest-value diligence questions.

A good diligence question (challenge spec):
  * cannot be answered from public information,
  * says WHAT data would answer it (proprietary data, patient-level data, KOL, experiment, CMC/IP),
  * would change the decision if answered.

Sources, in priority order:
  1. Kill signals / stopped same-target trials -> "Why did <drug> fail here: target, molecule, dose or population?"
  2. High-severity risks -> "What would resolve <risk>?"
  3. Translation-chain gaps (exposure, target engagement, biomarker)
  4. Unknowns raised by the lenses and gaps from the researcher
  5. Always: "What single result would kill the program?"
Ranking is deterministic; duplicates are removed by wording similarity.
"""
from __future__ import annotations

from evidence_bundle import EvidenceBundle

from ..schemas import DiligenceQuestion, Lens, LensResult, Risk, Unknown
from .text import similar

MIN_QUESTIONS, MAX_QUESTIONS = 5, 10

CHAIN_QUESTIONS = {
    "human_exposure": ("Is the efficacious exposure achievable safely in humans (PK/PD, therapeutic window)?",
                       "experiment"),
    "target_engagement": ("Is target engagement demonstrated in the relevant diseased tissue?", "proprietary_data"),
    "biological_response": ("Does a pathway / biomarker response predict clinical benefit in this indication?",
                            "patient_data"),
    "molecular_effect": ("Is the observed biology target-specific (orthogonal tools, knockout/rescue data)?",
                         "experiment"),
    "patient_benefit": ("Which patient subgroup, if any, could plausibly benefit, and how would it be selected?",
                        "patient_data"),
}


def build_diligence(bundle: EvidenceBundle, lenses: dict[Lens, LensResult], risks: list[Risk],
                    unknowns: list[Unknown]) -> list[DiligenceQuestion]:
    qs: list[DiligenceQuestion] = []
    disease = bundle.subject.disease.name

    # 1. failed same-target programs (decisive) — one question naming all of them
    failed: dict[str, str] = {}
    for e in bundle.conflicts():
        drug = str(e.data.get("drug") or "")
        if e.data.get("kill_signal") and drug and drug not in failed:
            failed[drug] = e.title
    if failed:
        names = " and ".join(failed) if len(failed) <= 2 else ", ".join(list(failed)[:-1]) + f" and {list(failed)[-1]}"
        qs.append(DiligenceQuestion(
            question=f"Why did {names} fail in {disease} — is the failure driven by the target itself, "
                     f"or by the molecule, dose, or patient population?",
            rationale="Kill signal(s): " + "; ".join(failed.values()) + ". If the failure is target-driven the thesis "
                      "is dead; if not, it is the key differentiation argument.",
            requires="patient_data"))
    seen_drugs = set(failed)

    # 2. high-severity risks that are not kill signals
    for r in risks:
        if r.severity == "high" and not r.title.lower().startswith(("weak", "translation gap")) and \
                not any(d.lower() in r.title.lower() for d in seen_drugs):
            qs.append(DiligenceQuestion(question=f"What evidence would resolve: {r.title}?",
                                        rationale=r.description[:300], requires="kol"))

    # 3. translation-chain gaps
    sci = lenses.get("science")
    if sci is not None and sci.status == "ok":
        for link in sci.params.get("translation_chain", []):
            if link.get("status") in {"missing", "weak", "contradicted"} and link.get("step") in CHAIN_QUESTIONS:
                q, req = CHAIN_QUESTIONS[link["step"]]
                qs.append(DiligenceQuestion(question=q, rationale=f"Translation chain link '{link['step']}' is "
                                                                  f"{link.get('status')} in public evidence.",
                                            requires=req))  # type: ignore[arg-type]

    # 4. unknowns from lenses and researcher gaps
    for u in unknowns:
        qs.append(DiligenceQuestion(question=u.question, rationale=u.why or "Raised as a critical unknown.",
                                    requires=u.requires))

    # 5. always
    qs.append(DiligenceQuestion(
        question="What single experiment or data readout would kill the program, and when can it be obtained?",
        rationale="Defines the cheapest path to a go/no-go decision before committing the full round.",
        requires="kol"))

    # 6. top up to the minimum with the standard translational questions
    for q_text, req in CHAIN_QUESTIONS.values():
        qs.append(DiligenceQuestion(question=q_text, rationale="Standard translational diligence question.",
                                    requires=req))  # type: ignore[arg-type]

    out: list[DiligenceQuestion] = []
    for q in qs:
        if not any(similar(q.question, o.question) for o in out):
            out.append(q)
        if len(out) >= MAX_QUESTIONS:
            break
    return out
