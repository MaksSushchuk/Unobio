import pytest
from pydantic import ValidationError

from researcher.schema import LlmCall, ResearchInput, Subject
from researcher import llm as llm_mod
from researcher import pipeline
from researcher.llm import GeminiLLM, LlmError
from researcher.planner import _LlmPlanWithTargets, plan, strip_action_words

CASES = [
    ("Crohn's disease", "IL-17 inhibition", {"IL17A", "IL17RA", "IL17F"}),
    ("hypercholesterolemia", "PCSK9 inhibition", {"PCSK9"}),
    ("obesity", "GLP-1R agonist", {"GLP1R"}),
    ("rheumatoid arthritis", "JAK inhibition", {"JAK1", "JAK2", "JAK3", "TYK2"}),
    ("B-cell acute lymphoblastic leukemia", "anti-CD19 CAR-T", {"CD19"}),
    ("Alzheimer's disease", "amyloid beta antibody", {"APP"}),
]


@pytest.fixture(scope="module")
def gemini():
    return GeminiLLM.from_env()


@pytest.mark.llm
@pytest.mark.parametrize("indication,mechanism,expected", CASES, ids=[c[1] for c in CASES])
def test_llm_plan_suggests_expected_target(gemini, indication, mechanism, expected):
    p = plan(ResearchInput(indication=indication, mechanism=mechanism), gemini)
    assert "source: llm" in p.notes, p.notes
    assert expected & set(p.target_symbols), p.target_symbols
    assert indication in p.disease_terms
    assert gemini.calls[-1].ok and gemini.calls[-1].purpose == "planner"


@pytest.mark.llm
@pytest.mark.llm
@pytest.mark.parametrize("indication,mechanism,modality", [
    ("Crohn's disease", "IL-17 inhibition", "antibody"),  # not in the text: every IL-17 drug is an antibody
    ("B-cell acute lymphoblastic leukemia", "anti-CD19 CAR-T", "cell_therapy"),
])
def test_llm_plan_modality(gemini, indication, mechanism, modality):
    assert plan(ResearchInput(indication=indication, mechanism=mechanism), gemini).modality == modality


@pytest.mark.parametrize("mechanism,modality", [
    ("anti-CD19 CAR-T", "cell_therapy"), ("IL-17 inhibition", "unspecified"), ("amyloid beta antibody", "antibody"),
    ("CD19xCD3 BiTE", "bispecific"), ("anti-CD19 antibody-drug conjugate", "adc"), ("PCSK9 siRNA", "oligonucleotide"),
])
def test_fallback_modality(mechanism, modality):
    assert plan(ResearchInput(indication="x", mechanism=mechanism)).modality == modality


def test_llm_plan_keeps_given_targets(gemini):
    inp = ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition", target_symbols=["il17ra"])
    p = plan(inp, gemini)
    assert p.target_symbols == ["IL17RA"]
    assert "target_symbols taken from input" in p.notes


# --- offline ---------------------------------------------------------------


class FakeLLM:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.calls: list[LlmCall] = []
        self.schemas = []
        self.prompts = []

    def generate_json(self, prompt, schema, purpose=None):
        self.prompts.append(prompt)
        self.schemas.append(schema)
        if self.error:
            raise self.error
        return schema.model_validate(self.response)


LLM_OUT = {
    "target_symbols": ["il17a", "IL-17 receptor A", "IL17A"],
    "action": "inhibition",
    "modality": "antibody",
    "disease_terms": ["Crohn disease"],
    "drug_names": ["secukinumab"],
    "extra_literature_terms": ["Th17"],
}


def test_plan_normalizes_llm_output():
    p = plan(ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition"), FakeLLM(LLM_OUT))
    assert p.target_symbols == ["IL17A"]
    assert p.action == "inhibition"
    assert p.disease_terms == ["Crohn's disease", "Crohn disease"]
    assert any("dropped non-symbol" in n for n in p.notes)


def test_given_targets_are_not_requested_from_llm():
    fake = FakeLLM({k: v for k, v in LLM_OUT.items() if k != "target_symbols"})
    inp = ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition", target_symbols=["IL17A"])
    p = plan(inp, fake)
    assert "target_symbols" not in fake.schemas[0].model_fields
    assert "HGNC" not in fake.prompts[0]
    assert p.target_symbols == ["IL17A"]


def test_prompt_asks_for_hgnc_and_allows_empty():
    fake = FakeLLM(LLM_OUT)
    plan(ResearchInput(indication="x", mechanism="y"), fake)
    assert "HGNC" in fake.prompts[0]
    assert "Do not guess" in fake.prompts[0]
    assert "inhibition, activation, modulation, degradation, other" in fake.prompts[0]


def test_llm_action_must_be_one_of_the_literals():
    assert _LlmPlanWithTargets.model_json_schema()["properties"]["action"]["anyOf"][0]["enum"] == [
        "inhibition", "activation", "modulation", "degradation", "other"
    ]
    with pytest.raises(ValidationError):
        _LlmPlanWithTargets.model_validate(dict(LLM_OUT, action="inhibitor"))


def test_llm_error_falls_back():
    p = plan(ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition"), FakeLLM(error=LlmError("429")))
    assert p.notes[0].startswith("source: fallback (LLM error: 429")
    assert p.extra_literature_terms == ["IL-17"]


@pytest.mark.parametrize(
    "mechanism,term,action",
    [
        ("IL-17 inhibition", "IL-17", "inhibition"),
        ("PCSK9 inhibitor", "PCSK9", "inhibition"),
        ("GLP-1R agonist", "GLP-1R", "activation"),
        ("anti-CD19 CAR-T", "CD19", None),
        ("amyloid beta antibody", "amyloid beta", None),
        ("TNF blockade", "TNF", "inhibition"),
    ],
)
def test_strip_action_words(mechanism, term, action):
    assert strip_action_words(mechanism) == (term, action)


def test_no_key_falls_back_without_llm(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(llm_mod, "ENV_PATH", tmp_path / "missing.env")
    assert GeminiLLM.from_env() is None
    monkeypatch.setattr(
        pipeline, "resolve", lambda inp, plan, statuses, **kw: Subject(indication=inp.indication, mechanism=inp.mechanism)
    )
    monkeypatch.setattr(pipeline, "default_connectors", lambda http: [])

    bundle = pipeline.run_research(ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition"))
    p = bundle.plan
    assert p.notes[0] == "source: fallback (no LLM configured)"
    assert p.target_symbols == []
    assert p.action == "inhibition"
    assert p.disease_terms == ["Crohn's disease"]
    assert p.extra_literature_terms == ["IL-17"]
    assert bundle.llm_calls == []
