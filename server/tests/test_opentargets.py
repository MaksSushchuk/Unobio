import pytest

from researcher.schema import Drug, ResearchInput, SearchPlan, Subject, evidence_id
from researcher.connectors.opentargets import OpenTargetsConnector
from researcher.http import HttpClient, HttpError
from researcher.resolve import resolve

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
    connector = OpenTargetsConnector()
    return subject, connector.fetch(subject), connector.last_status


def _check_structure(subject, evidence, status, symbol):
    assert status.ok, status.error
    assert status.records == len(evidence) > 0
    ids = [e.id for e in evidence]
    assert len(ids) == len(set(ids))
    assert all(e.published_at is None and e.url.startswith("https://platform.opentargets.org/") for e in evidence)

    disease = subject.disease_ids[0]
    tid = subject.target_ids[subject.target_symbols.index(symbol)]
    assoc = next(e for e in evidence if e.id == evidence_id("opentargets", f"assoc:{tid}:{disease}"))
    assert 0 < assoc.data["score"] <= 1
    assert assoc.data["datatypeScores"]
    assert assoc.entity_refs == {"target": tid, "disease": disease}
    assert assoc.modules == ["scientific-evidence"]
    assert assoc.snippet.startswith(f"{symbol}–")

    drugs = [e for e in evidence if "drug" in e.entity_refs]
    assert drugs and all(e.data["maxClinicalStage"] for e in drugs)
    assert all(set(e.modules) == {"pipeline", "competitive-landscape"} for e in drugs)
    assert all(e.entity_refs["target"] in subject.target_ids for e in drugs)
    assert any(e.data["nct_ids"] for e in drugs)
    for e in drugs:
        assert all(t["nct_id"].startswith("NCT") and t["nct_id"] in e.data["nct_ids"] for t in e.data["trials"])
    assert any(e.data.get("tractability") for e in evidence)
    return drugs


@pytest.mark.live
def test_il17_crohns():
    subject, evidence, status = _fetch("Crohn's disease", "IL-17 inhibition", IL17_PLAN)
    drugs = _check_structure(subject, evidence, status, "IL17A")
    by_name = {e.data["drug_name"].lower(): e for e in drugs}
    assert "NCT00584740" in by_name["secukinumab"].data["nct_ids"]
    assert "NCT01150890" in by_name["brodalumab"].data["nct_ids"]


@pytest.mark.live
def test_pcsk9_hypercholesterolemia():
    subject, evidence, status = _fetch("hypercholesterolemia", "PCSK9 inhibition", PCSK9_PLAN)
    drugs = _check_structure(subject, evidence, status, "PCSK9")
    assert any(e.data["maxClinicalStage"] == "APPROVAL" for e in drugs)
    genetic = [e for e in evidence if e.data.get("datasourceId") in ("gwas_credible_sets", "gene_burden")]
    assert 0 < len(genetic) <= 10


def test_never_raises_on_http_failure():
    class Failing(HttpClient):
        def __init__(self):
            super().__init__(cache_path=None)

        def post(self, *a, **kw):
            raise HttpError("HTTP 503", 503)

    subject = Subject(indication="Crohn's disease", mechanism="IL-17 inhibition", disease_ids=["MONDO_0005011"],
                      target_ids=["ENSG00000112115"], target_symbols=["IL17A"],
                      drugs=[Drug(chembl_id="CHEMBL1", name="secukinumab", origin="plan")])
    connector = OpenTargetsConnector(Failing())
    assert connector.fetch(subject) == []
    s = connector.last_status
    assert not s.ok and s.records == 0
    assert "targets:" in s.error and "disease MONDO_0005011" in s.error and "genetic" in s.error
