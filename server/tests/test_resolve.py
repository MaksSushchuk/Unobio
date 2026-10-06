import json

import httpx
import pytest

from researcher.schema import ResearchInput, SearchPlan, Subject
from researcher import http as http_mod
from researcher import pipeline
from researcher.http import HttpClient
from researcher.llm import GeminiLLM
from researcher.planner import plan
from researcher.resolve import CHEMBL_STATUS, OT_STATUS, resolve

# Plans shaped like planner output: each has one bogus target and one bogus drug that must be dropped.
CASES = [
    (
        "Crohn's disease", "IL-17 inhibition", {"IL17A", "IL17RA", "IL17F"},
        SearchPlan(target_symbols=["IL17A", "IL17F", "NOTAGENE9"], action="inhibition", modality="antibody",
                   disease_terms=["Crohn's disease", "Crohn disease", "regional enteritis"],
                   drug_names=["secukinumab", "brodalumab", "notadrugumab"]),
    ),
    (
        "hypercholesterolemia", "PCSK9 inhibition", {"PCSK9"},
        SearchPlan(target_symbols=["PCSK9", "NOTAGENE9"], action="inhibition",
                   disease_terms=["hypercholesterolemia"], drug_names=["evolocumab", "inclisiran", "notadrugumab"]),
    ),
    (
        "obesity", "GLP-1R agonist", {"GLP1R"},
        SearchPlan(target_symbols=["GLP1R", "NOTAGENE9"], action="activation",
                   disease_terms=["obesity"], drug_names=["semaglutide", "liraglutide", "notadrugumab"]),
    ),
    (
        "rheumatoid arthritis", "JAK inhibition", {"JAK1", "JAK2", "JAK3", "TYK2"},
        SearchPlan(target_symbols=["JAK1", "JAK2", "JAK3", "NOTAGENE9"], action="inhibition",
                   disease_terms=["rheumatoid arthritis"], drug_names=["tofacitinib", "baricitinib", "notadrugumab"]),
    ),
    (
        "B-cell acute lymphoblastic leukemia", "anti-CD19 CAR-T", {"CD19"},
        SearchPlan(target_symbols=["CD19", "NOTAGENE9"], action="other",
                   disease_terms=["B-cell acute lymphoblastic leukemia", "B-ALL"],
                   drug_names=["tisagenlecleucel", "blinatumomab", "notadrugumab"]),
    ),
    (
        "Alzheimer's disease", "amyloid beta antibody", {"APP"},
        SearchPlan(target_symbols=["APP", "NOTAGENE9"], action="other",
                   disease_terms=["Alzheimer's disease", "Alzheimer disease"],
                   drug_names=["lecanemab", "donanemab", "notadrugumab"]),
    ),
]


def _resolve(indication, mechanism, search_plan, **kw):
    statuses = []
    subject = resolve(ResearchInput(indication=indication, mechanism=mechanism), search_plan, statuses=statuses, **kw)
    return subject, {s.source: s for s in statuses}


@pytest.mark.live
@pytest.mark.parametrize("indication,mechanism,expected,search_plan", CASES, ids=[c[1] for c in CASES])
def test_resolve_cases(indication, mechanism, expected, search_plan):
    subject, statuses = _resolve(indication, mechanism, search_plan)
    assert expected & set(subject.target_symbols), subject
    assert len(subject.target_ids) == len(subject.target_symbols)
    assert all(t.startswith("ENSG") for t in subject.target_ids)
    assert subject.disease_ids and subject.disease_ids[0], subject.resolution_notes
    assert any(d.synonyms for d in subject.drugs), subject.drugs
    assert "NOTAGENE9" not in subject.target_symbols
    assert any("NOTAGENE9" in n for n in subject.resolution_notes)
    assert any("notadrugumab" in n for n in subject.resolution_notes)
    assert len({d.chembl_id for d in subject.drugs}) == len(subject.drugs)
    for d in subject.drugs:
        assert d.name == d.name.lower() and set(d.target_ids) <= set(subject.target_ids), d
    assert statuses[OT_STATUS].ok


def _drug(subject, name):
    return next(d for d in subject.drugs if d.name.lower() == name.lower())


@pytest.mark.live
@pytest.mark.parametrize("planned_targets", [["IL17A", "IL17F", "NOTAGENE9"], ["IL17A"]], ids=["A+F", "A-only"])
def test_crohns_il17_target_consistency(planned_targets):
    search_plan = CASES[0][3].model_copy(update={"target_symbols": planned_targets})
    subject, _ = _resolve("Crohn's disease", "IL-17 inhibition", search_plan)
    assert subject.disease_ids == ["MONDO_0005011"]
    notes = [n.lower() for n in subject.resolution_notes]

    # Same-family co-targets join the subject: IL17RA (brodalumab), IL17F (bimekizumab, sonelokimab).
    assert {"IL17A", "IL17F", "IL17RA"} <= set(subject.target_symbols)
    assert "TNF" not in subject.target_symbols and "TNFSF13B" not in subject.target_symbols
    assert any("added target il17ra" in n and "brodalumab" in n for n in notes)

    # Every drug target is either in the subject or listed as an outside co-target.
    for d in subject.drugs:
        assert set(d.target_ids) <= set(subject.target_ids), d
        assert d.name == d.name.lower()
    remtolumab = _drug(subject, "Remtolumab")
    assert "TNF" in remtolumab.other_target_symbols
    assert any("remtolumab also targets tnf" in n and "multi-specific" in n for n in notes)

    # Code-named drugs take their INN; the code stays a synonym.
    sonelokimab = _drug(subject, "SONELOKIMAB")
    assert sonelokimab.chembl_id == "CHEMBL4298023"
    assert "m-1095" in {s.lower() for s in sonelokimab.synonyms}

    planned = {"secukinumab", "brodalumab"}
    database = {d.name for d in subject.drugs if d.origin == "database"}
    assert database and not database & planned
    secukinumab = _drug(subject, "secukinumab")
    assert secukinumab.origin == "both"
    assert "cosentyx" in {s.lower() for s in secukinumab.synonyms} and secukinumab.max_phase == 4.0


@pytest.mark.live
def test_unmappable_mechanism_returns_empty_targets():
    search_plan = SearchPlan(target_symbols=["ZORGLE1"], action="other", disease_terms=["Crohn's disease"])
    subject, _ = _resolve("Crohn's disease", "zorgle frobnication", search_plan)
    assert subject.target_ids == [] and subject.target_symbols == [] and subject.drugs == []
    assert any("no target could be resolved" in n and "zorgle frobnication" in n for n in subject.resolution_notes)
    assert subject.disease_ids == ["MONDO_0005011"]


@pytest.mark.live
def test_fallback_plan_resolves_target_from_mechanism_text():
    inp = ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition")
    subject = resolve(inp, plan(inp, None))
    assert "IL17A" in subject.target_symbols
    assert any("mechanism text" in n for n in subject.resolution_notes)
    assert any(d.origin == "database" for d in subject.drugs)


@pytest.mark.live
@pytest.mark.llm
def test_llm_plan_then_resolve():
    inp = ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition")
    subject = resolve(inp, plan(inp, GeminiLLM.from_env()))
    assert {"IL17A", "IL17RA"} & set(subject.target_symbols)
    assert any(d.origin == "database" for d in subject.drugs)


# --- offline: ChEMBL down, Open Targets only --------------------------------

OT_RESPONSES = {
    "MapIds": {"mapIds": {"mappings": [
        {"term": "IL17A", "hits": [{"id": "ENSG_A", "name": "IL17A", "score": 1}]},
        {"term": "BOGUS1", "hits": []},
    ]}},
    "MapDrugs": {"mapIds": {"mappings": [
        {"term": "drugmab", "hits": [{"id": "CHEMBL_P", "name": "DRUGMAB", "score": 1}]},
    ]}},
    "SearchDisease": {"search": {"hits": [{"id": "MONDO_1", "name": "some disease", "score": 10.0}]}},
    "Disease": {"disease": {"id": "MONDO_1", "name": "some disease",
                            "synonyms": [{"relation": "hasExactSynonym", "terms": ["disease, some"]}]}},
    "Drugs": {"drugs": [{
        "id": "CHEMBL_P", "name": "DRUGMAB", "maximumClinicalStage": "PHASE_2_3",
        "synonyms": [{"label": "DM-1"}], "tradeNames": [{"label": "Drugmax"}],
        "mechanismsOfAction": {"rows": [{"actionType": "ANTAGONIST", "targets": [{"id": "ENSG_R", "approvedSymbol": "IL17RA"}]}]},
    }]},
    "TargetFamily": {"targets": [
        {"id": "ENSG_A", "approvedSymbol": "IL17A", "targetClass": [{"label": "Secreted protein", "level": "l1"}]},
        {"id": "ENSG_R", "approvedSymbol": "IL17RA", "targetClass": [{"label": "Membrane receptor", "level": "l1"}]},
        {"id": "ENSG_T", "approvedSymbol": "TNF", "targetClass": [{"label": "Secreted protein", "level": "l1"}]},
    ]},
    "TargetDrugs": {"targets": [{"id": "ENSG_A", "approvedSymbol": "IL17A", "drugAndClinicalCandidates": {"rows": [
        {"drug": {"id": "CHEMBL_D", "name": "OM-7", "maximumClinicalStage": "APPROVAL",
                  "synonyms": [{"label": "Othermab"}, {"label": "OM7"}], "tradeNames": [{"label": "Otherix"}],
                  "mechanismsOfAction": {"rows": [
                      {"actionType": "INHIBITOR", "targets": [{"id": "ENSG_A", "approvedSymbol": "IL17A"}]},
                      {"actionType": "INHIBITOR", "targets": [{"id": "ENSG_T", "approvedSymbol": "TNF"}]}]}}},
        {"drug": {"id": "CHEMBL_X", "name": "AGONISTIN", "maximumClinicalStage": "PHASE_1",
                  "synonyms": [], "tradeNames": [],
                  "mechanismsOfAction": {"rows": [{"actionType": "AGONIST", "targets": [{"id": "ENSG_A", "approvedSymbol": "IL17A"}]}]}}},
    ]}}]},
}


def _ot_handler(chembl_calls, ot_down=False):
    def handler(req: httpx.Request) -> httpx.Response:
        if "ebi.ac.uk/chembl" in str(req.url):
            chembl_calls.append(str(req.url))
            return httpx.Response(500, text="<html>Internal Server Error</html>")
        if ot_down:
            return httpx.Response(503)
        body = json.loads(req.content)
        op = body["query"].split("(")[0].split()[-1]
        if op == "MapIds" and body["variables"]["entities"] == ["drug"]:
            op = "MapDrugs"
        return httpx.Response(200, json={"data": OT_RESPONSES[op]})
    return handler


@pytest.fixture
def mock_client(monkeypatch):
    monkeypatch.setattr(http_mod, "RATE_LIMITS", {})
    monkeypatch.setattr(http_mod, "DEFAULT_MIN_INTERVAL", 0.0)

    def make(**kw):
        calls: list[str] = []
        client = HttpClient(cache_path=None, backoff_base_s=0.0, max_retries=2,
                            transport=httpx.MockTransport(_ot_handler(calls, **kw)))
        return client, calls
    return make


def test_chembl_failure_continues_with_open_targets(mock_client):
    client, chembl_calls = mock_client()
    search_plan = SearchPlan(target_symbols=["IL17A", "BOGUS1"], action="inhibition",
                             disease_terms=["some disease"], drug_names=["drugmab"])
    subject, statuses = _resolve("some disease", "X inhibition", search_plan, http=client)

    assert len(chembl_calls) == 3  # one request, retried by http.py, then ChEMBL is skipped for the run
    assert statuses[CHEMBL_STATUS].ok is False and "500" in statuses[CHEMBL_STATUS].error
    assert statuses[OT_STATUS].ok is True
    assert any("ChEMBL unavailable" in n for n in subject.resolution_notes)

    assert subject.target_symbols == ["IL17A", "IL17RA"]  # IL17RA: same symbol stem as IL17A
    assert subject.disease_ids == ["MONDO_1"] and "disease, some" in subject.disease_synonyms
    drugs = {d.name: d for d in subject.drugs}
    assert drugs["drugmab"].origin == "plan"
    assert drugs["drugmab"].synonyms == ["Drugmax", "DM-1"] and drugs["drugmab"].max_phase == 2.0
    # Code-named drug takes the all-letter non-trade synonym as INN; TNF is outside the IL17 family.
    other = drugs["othermab"]
    assert other.origin == "database" and other.max_phase == 4.0
    assert other.synonyms == ["OM-7", "Otherix", "OM7"]
    assert other.target_ids == ["ENSG_A"] and other.other_target_symbols == ["TNF"]
    assert "TNF" not in subject.target_symbols
    assert "othermab also targets TNF (multi-specific; outside the subject mechanism)" in subject.resolution_notes
    assert "agonistin" not in drugs  # opposite action to "inhibition"
    assert statuses[OT_STATUS].cached is False
    assert any("BOGUS1" in n for n in subject.resolution_notes)


def test_open_targets_down_does_not_raise(mock_client):
    client, _ = mock_client(ot_down=True)
    search_plan = SearchPlan(target_symbols=["IL17A"], disease_terms=["some disease"])
    subject, statuses = _resolve("some disease", "X inhibition", search_plan, http=client)
    assert subject.target_ids == [] and subject.disease_ids == [] and subject.drugs == []
    assert statuses[OT_STATUS].ok is False
    assert any("targets: failed" in n for n in subject.resolution_notes)


def test_pipeline_calls_resolve_with_plan(monkeypatch, tmp_path):
    seen = {}

    def fake_resolve(inp, search_plan, statuses, **kw):
        seen["plan"] = search_plan
        return Subject(indication=inp.indication, mechanism=inp.mechanism, target_symbols=["IL17A"])

    monkeypatch.setattr(pipeline, "resolve", fake_resolve)
    monkeypatch.setattr(pipeline, "default_connectors", lambda http: [])
    bundle = pipeline.run_research(ResearchInput(indication="x", mechanism="IL-17 inhibition"), use_llm=False)
    assert seen["plan"] is bundle.plan
    assert bundle.subject.target_symbols == ["IL17A"]


@pytest.mark.live
def test_repeat_run_reports_cache():
    _resolve("Crohn's disease", "IL-17 inhibition", CASES[0][3])
    _, statuses = _resolve("Crohn's disease", "IL-17 inhibition", CASES[0][3])
    assert statuses[OT_STATUS].cached and statuses[CHEMBL_STATUS].cached
    assert statuses[OT_STATUS].duration_s < 5


# --- modality / relation -------------------------------------------------------------------------------

from researcher.schema import Drug  # noqa: E402
from researcher.resolve import drug_modality, drug_relation  # noqa: E402


@pytest.mark.parametrize("name,drug_type,others,modality", [
    ("tisagenlecleucel", "Cell", [], "cell_therapy"),
    ("obecabtagene autoleucel", "Gene", [], "cell_therapy"),  # gene-modified cells: INN stem -cel
    ("onasemnogene abeparvovec", "Gene", [], "gene_therapy"),
    ("blinatumomab", "Antibody", ["CD3E"], "bispecific"),
    ("tafasitamab", "Antibody", [], "antibody"),
    ("loncastuximab tesirine", "Antibody drug conjugate", [], "adc"),
    ("sonelokimab", "Unknown", [], "antibody"),  # INN stem -mab
    ("izokibep", "Protein", [], "protein"),
    ("oncolysin b", "Unknown", [], "unknown"),
])
def test_drug_modality(name, drug_type, others, modality):
    d = Drug(name=name, origin="database", drug_type=drug_type, other_target_symbols=others)
    assert drug_modality(d) == modality


def test_drug_relation():
    cart = Drug(name="tisagenlecleucel", origin="database", drug_type="Cell")
    bite = Drug(name="blinatumomab", origin="database", drug_type="Antibody", other_target_symbols=["CD3E"])
    mab = Drug(name="remtolumab", origin="database", drug_type="Antibody", other_target_symbols=["TNF"])
    unknown = Drug(name="cjm-112", origin="database", drug_type="Unknown")
    assert drug_relation(cart, "cell_therapy") == "subject_mechanism"
    assert drug_relation(bite, "cell_therapy") == "same_target_other_modality"
    assert drug_relation(bite, "unspecified") == "subject_mechanism"
    assert drug_relation(mab, "antibody") == "subject_mechanism"  # a bispecific antibody is still an antibody
    assert drug_relation(cart, "antibody") == "same_target_other_modality"
    assert drug_relation(unknown, "cell_therapy") == "subject_mechanism"  # unknown modality is not demoted
