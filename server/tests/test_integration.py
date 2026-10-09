"""researcher -> adapter -> analytics, offline (no network, no LLM).

Builds a researcher Bundle by hand, runs the researcher's own reconcile() (keyword stop classifier),
converts it with evidence_bundle.from_researcher and runs the full analytics pipeline with the
fake lens model. Checks the demo story: failed same-target trials -> Do Not Invest; the same
question before those trials stopped -> no kill signal.
"""
from datetime import UTC, date, datetime

from analytics.lenses import fake_lens_llm
from analytics.llm import LLMSettings, make_llm
from analytics.orchestrator import RunOptions, analyze
from evidence_bundle import from_researcher
from researcher.reconcile import reconcile
from researcher.schema import Bundle, Drug, Evidence, ResearchInput, Subject, evidence_id

NOW = datetime(2026, 1, 1, tzinfo=UTC)
IL17A, IL17RA = "ENSG00000112115", "ENSG00000177663"
SECU = Drug(chembl_id="CHEMBL1743068", name="secukinumab", synonyms=["AIN457"], origin="plan",
            target_ids=[IL17A], max_phase=4.0)
BRODA = Drug(chembl_id="CHEMBL1742996", name="brodalumab", synonyms=["AMG 827"], origin="plan",
             target_ids=[IL17RA], max_phase=4.0)


def _trial(nct: str, drug: Drug, intervention: str, status: str, why: str | None, updated: date) -> Evidence:
    return Evidence(
        id=evidence_id("clinicaltrials", nct), source="clinicaltrials", url=f"https://clinicaltrials.gov/study/{nct}",
        retrieved_at=NOW, kind="record", modules=["pipeline", "trial-design"], entity_refs={"drug": drug.chembl_id or ""},
        title=f"{nct}: {intervention} in Crohn's disease", snippet=f"Ph2, {status}. {why or ''}",
        data={"nct_id": nct, "overall_status": status, "why_stopped": why, "phases": ["PHASE2"], "enrollment": 100,
              "interventions": [{"name": intervention, "type": "BIOLOGICAL"}],
              "subject_drug_chembl_ids": [drug.chembl_id], "drug_relation": "subject_mechanism"},
        published_at=updated,
    )


def _researcher_bundle(cutoff: date | None, drugs: tuple[Drug, ...] = (SECU, BRODA), stops: int = 2) -> Bundle:
    evidence = [
        _trial("NCT00584740", SECU, "AIN457", "TERMINATED", "futility criteria were met", date(2010, 8, 1)),
        *([_trial("NCT01150890", BRODA, "AMG 827", "TERMINATED",
                  "imbalance in worsening Crohn's disease in the active arms", date(2012, 3, 1))] if stops == 2 else []),
        Evidence(id=evidence_id("opentargets", "assoc"), source="opentargets", url="https://platform.opentargets.org",
                 retrieved_at=NOW, kind="record", modules=["scientific-evidence"], title="IL17A – Crohn disease association",
                 snippet="overall association score 0.10", data={"score": 0.10, "datatypeScores": []}),
    ]
    inp = ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition", evidence_cutoff=cutoff)
    subject = Subject(indication=inp.indication, mechanism=inp.mechanism, disease_ids=["MONDO_0005011"],
                      disease_synonyms=["Crohn disease"], target_ids=[IL17A, IL17RA], target_symbols=["IL17A", "IL17RA"],
                      drugs=list(drugs))
    return reconcile(Bundle(run_id=f"it-{cutoff or 'now'}", created_at=NOW, input=inp, subject=subject, evidence=evidence))


def test_adapter_maps_subject_and_kill_signals():
    b = from_researcher(_researcher_bundle(None).model_dump(mode="json"))
    assert b.subject.disease.id == "MONDO_0005011"
    assert [t.symbol for t in b.subject.targets] == ["IL17A", "IL17RA"]
    assert {d.name for d in b.subject.drugs} == {"Secukinumab", "Brodalumab"}
    trials = [e for e in b.evidence if e.source == "clinicaltrials"]
    assert {e.data["stop_class"] for e in trials} <= {"efficacy", "safety"}  # keyword classifier may label both efficacy
    assert all(e.data["role"] == "same_target" for e in trials)
    kills = [e for e in b.evidence if e.kind == "conflict" and e.data.get("kill_signal")]
    assert kills, "efficacy/safety stops of subject drugs must become kill signals"


def test_adapter_hides_current_stage_under_cutoff():
    b = from_researcher(_researcher_bundle(date(2011, 6, 30)).model_dump(mode="json"))
    assert all(d.max_stage is None for d in b.subject.drugs)
    assert b.cutoff_policy


async def _run(cutoff):
    bundle = from_researcher(_researcher_bundle(cutoff).model_dump(mode="json"))
    llm = make_llm(LLMSettings(provider="fake"), fake=fake_lens_llm())
    try:
        return await analyze(bundle, llm, RunOptions(save=False))
    finally:
        await llm.aclose()


def test_end_to_end_verdict_flips_with_cutoff():
    import asyncio

    now = asyncio.run(_run(None))
    assert now.verdict.recommendation == "Do Not Invest" and now.verdict.kill_triggered

    # mid-2011: brodalumab trial (updated 2012) is cut off; only the secukinumab futility stop is known
    early = asyncio.run(_run(date(2009, 12, 31)))
    assert not early.verdict.kill_triggered


# ---------------------------------------------------------------- kill-signal rules (aligned with v1.0.0)


def _bundle_with(evidence_extra=(), drugs=(SECU, BRODA), stops=2) -> dict:
    raw = _researcher_bundle(None, drugs=drugs, stops=stops).model_dump(mode="json")
    raw["evidence"] += list(evidence_extra)
    return raw


def _kills(raw: dict) -> list:
    return [e for e in from_researcher(raw).evidence if e.data.get("kill_signal")]


def _phase2(d):  # not approved anywhere: reconcile R4 (approved elsewhere, failed here) does not apply
    return d.model_copy(update={"max_phase": 2.0})


def test_one_failed_trial_is_a_high_risk_not_a_kill():
    raw = _bundle_with(stops=1, drugs=(_phase2(SECU), _phase2(BRODA)))
    conflicts = [e for e in from_researcher(raw).evidence if e.kind == "conflict"]
    assert conflicts and all(e.data["severity"] == "high" for e in conflicts)
    assert not _kills(raw)


def test_failure_of_another_modality_is_not_a_kill():
    other = [d.model_copy(update={"relation": "same_target_other_modality"}) for d in (SECU, BRODA)]
    raw = _bundle_with(drugs=tuple(other))
    assert not _kills(raw)
    assert any(e.data.get("severity") == "medium" for e in from_researcher(raw).evidence if e.kind == "conflict")


def test_approved_elsewhere_failed_here_is_a_kill():
    """R4 does_not_transfer on a subject-mechanism drug (secukinumab: approved in psoriasis, futility here)."""
    kills = _kills(_bundle_with(stops=1))
    assert kills and all(e.data["rule"] == "does_not_transfer" for e in kills)


def test_no_kill_when_mechanism_is_approved_in_this_indication():
    approved = {"id": "a" * 16, "source": "opentargets", "url": "https://platform.opentargets.org", "retrieved_at": NOW.isoformat(),
                "kind": "record", "modules": ["pipeline"], "entity_refs": {"drug": SECU.chembl_id}, "title": "approved",
                "snippet": "", "data": {"maxClinicalStage": "APPROVAL"}, "related_evidence_ids": [], "published_at": None}
    raw = _bundle_with(evidence_extra=[approved])
    assert not _kills(raw)
    assert any(e.data.get("kill_suppressed") for e in from_researcher(raw).evidence)


def test_trial_rebuilt_as_of_cutoff_is_ongoing_for_the_analysts():
    raw = _researcher_bundle(None).model_dump(mode="json")
    trial = next(e for e in raw["evidence"] if e["source"] == "clinicaltrials")
    trial["data"].update({"overall_status": "RECRUITING", "as_of_cutoff": "2016-01-01", "why_stopped": None})
    b = from_researcher(raw)
    statuses = {e.data.get("status") for e in b.evidence if e.source == "clinicaltrials"}
    assert "ONGOING_AT_CUTOFF" in statuses
