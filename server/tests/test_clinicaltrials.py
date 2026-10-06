import pytest

from researcher.schema import Drug, ResearchInput, SearchPlan, Subject, evidence_id
from researcher.connectors.clinicaltrials import STOPPED, ClinicalTrialsConnector, _mentions, _norm, _snippet
from researcher.http import HttpClient, HttpError
from researcher.resolve import resolve

# Same plan shape as tests/test_resolve.py; brodalumab (IL17RA) is in the plan so its stopped Crohn's
# trial is reachable.
IL17_PLAN = SearchPlan(
    target_symbols=["IL17A", "IL17F"], action="inhibition", modality="antibody",
    disease_terms=["Crohn's disease", "Crohn disease", "regional enteritis"],
    drug_names=["secukinumab", "brodalumab"],
)
PCSK9_PLAN = SearchPlan(
    target_symbols=["PCSK9"], action="inhibition",
    disease_terms=["hypercholesterolemia"], drug_names=["evolocumab", "alirocumab", "inclisiran"],
)


def _fetch(indication, mechanism, search_plan):
    subject = resolve(ResearchInput(indication=indication, mechanism=mechanism), search_plan)
    connector = ClinicalTrialsConnector()
    return subject, connector.fetch(subject), connector.last_status


@pytest.mark.live
def test_il17_crohns_stopped_trials():
    subject, evidence, status = _fetch("Crohn's disease", "IL-17 inhibition", IL17_PLAN)
    assert status.ok, status.error
    assert status.records == len(evidence) > 0
    ids = [e.id for e in evidence]
    assert len(ids) == len(set(ids))
    by_nct = {e.data["nct_id"]: e for e in evidence}
    drug_ids = {d.chembl_id for d in subject.drugs}
    for nct in ("NCT01150890", "NCT00584740"):
        e = by_nct[nct]
        assert e.id == evidence_id("clinicaltrials", nct)
        assert e.url == f"https://clinicaltrials.gov/study/{nct}"
        assert e.data["overall_status"] in STOPPED
        assert e.data["why_stopped"]
        assert e.entity_refs.get("drug") in drug_ids
        assert "red-flags" in e.modules
        assert e.published_at is not None
        assert e.data["why_stopped"][:40] in e.snippet
    for e in evidence:
        stated = e.data["overall_status"] in STOPPED and bool(e.data["why_stopped"])
        assert ("red-flags" in e.modules) == stated
        assert e.data["found_via"] in {d.name for d in subject.drugs} | {"landscape"}
        assert e.entity_refs.get("drug") == (e.data["subject_drug_chembl_ids"] or [None])[0]
    assert all(d.relation == "subject_mechanism" for d in subject.drugs), [(d.name, d.drug_type) for d in subject.drugs]


@pytest.mark.live
def test_pcsk9_completed_phase3():
    _, evidence, status = _fetch("hypercholesterolemia", "PCSK9 inhibition", PCSK9_PLAN)
    assert status.ok, status.error
    done = [e for e in evidence if e.data["overall_status"] == "COMPLETED" and "PHASE3" in e.data["phases"]]
    assert len(done) >= 5
    assert any(e.entity_refs.get("drug") for e in done)


def test_never_raises_on_http_failure():
    class Failing(HttpClient):
        def __init__(self):
            super().__init__(cache_path=None)

        def get(self, *a, **kw):
            raise HttpError("HTTP 503", 503)

    subject = Subject(indication="Crohn's disease", mechanism="IL-17 inhibition",
                      drugs=[Drug(chembl_id="CHEMBL1", name="secukinumab", origin="plan")])
    connector = ClinicalTrialsConnector(Failing())
    assert connector.fetch(subject) == []
    s = connector.last_status
    assert not s.ok and s.records == 0
    assert "stopped secukinumab" in s.error and "landscape" in s.error


def test_intervention_matching():
    assert _mentions(_norm("Evolocumab and LDL apheresis"), _norm("evolocumab"))
    assert _mentions(_norm("Repatha, Praluent"), _norm("Praluent"))
    assert _mentions(_norm("AIN 457 10 mg/kg"), _norm("AIN457"))
    assert not _mentions(_norm("PCSK9 Inhibitor"), _norm("inclisiran"))
    assert not _mentions(_norm("Placebo"), _norm("pla"))


def test_snippet():
    d = {"phases": ["PHASE2", "PHASE3"], "enrollment": 59, "enrollment_type": "ACTUAL",
         "overall_status": "TERMINATED", "why_stopped": "futility", "lead_sponsor": None}
    assert _snippet(d) == "Phase 2/3, 59 patients, TERMINATED: futility"
