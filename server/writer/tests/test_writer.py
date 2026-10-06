from __future__ import annotations

import json

from pypdf import PdfReader

from tests.conftest import StubClient, norm, pdf_text
from writer_agent import WriterAgent, WriterConfig
from writer_agent.llm import LLMError, extract_json


def run(data, tmp_path, client, **cfg):
    agent = WriterAgent(WriterConfig(**cfg), client=client)
    return agent.run(data, tmp_path / "report.pdf")


# ---------------------------------------------------------------- normal run


def test_normal_run(fixture_data, stub, tmp_path):
    res = run(fixture_data, tmp_path, stub)
    assert res.status == "ok", res.warnings
    assert res.pdf_path and (tmp_path / "report.pdf").exists()
    assert res.page_count == len(PdfReader(res.pdf_path).pages) >= 2
    assert res.recommendation == "Do not invest"  # copied from upstream
    assert res.confidence == 0.84
    assert res.sections == [
        "Target biology & human genetics",
        "Direct evidence: IL-17 blockade in Crohn's disease",
        "Safety",
        "Market & positioning",
        "Skeptic review",
    ]
    # 5 sections + 1 final synthesis
    assert len(stub.calls) == 6
    assert not any("do not occur in the input" in w for w in res.warnings)
    # Hallucinated claim id from the model is dropped with a warning.
    assert any("nope-9" in w for w in res.warnings)

    text = pdf_text(res.pdf_path)
    assert "IL-17 inhibition · Crohn's disease" in text
    assert "Mock data" in text and "sources not verified" in text
    assert "Do not invest" in text and "84%" in text
    assert "$1.5M" in text and "$2.5M" in text and "$4M" in text
    # Every claim is printed verbatim.
    for section in fixture_data["analysts"] + [fixture_data["skeptic"]]:
        for claim in section["claims"]:
            assert norm(claim["text"]) in text, claim["id"]
    # Panels appendix is present, verbatim.
    assert "Appendix – data panels" in text
    assert "Two terminated or failed randomized trials" in text
    # Duplicate source (same identifier, different case) is numbered once: 10 unique cited.
    assert "[10]" in text and "[11]" not in text


def test_upstream_verdict_never_sent_for_rewrite(fixture_data, stub, tmp_path):
    run(fixture_data, tmp_path, stub)
    final_prompt = stub.calls[-1][1]["content"]
    assert "Do not change them" in final_prompt
    assert '"verdict": "exactly one of' not in final_prompt


def test_model_verdict_when_upstream_missing(fixture_data, tmp_path):
    del fixture_data["recommendation"]
    final_bad = {"verdict": "Strong buy", "rationale": "x", "risks": []}
    final_ok = {"verdict": "do not invest", "rationale": "Randomized evidence is negative.", "risks": []}
    stub = StubClient(final=[final_bad, final_ok])
    res = run(fixture_data, tmp_path, stub)
    assert res.recommendation == "Do not invest"  # normalised to the allowed spelling
    assert res.confidence is None
    assert any("confidence" in w.lower() for w in res.warnings)


# ---------------------------------------------------------------- missing analyst


def test_missing_analyst(fixture_data, stub, tmp_path):
    fixture_data["analysts"] = [a for a in fixture_data["analysts"] if a["key"] != "safety"]
    fixture_data["analysts"][-1]["claims"] = []  # commercial sent nothing
    res = run(fixture_data, tmp_path, stub)
    assert res.status == "ok"
    assert any("'safety' is missing" in w for w in res.warnings)
    assert any("Market & positioning" in w and "no claims" in w for w in res.warnings)
    assert "Safety" not in res.sections
    text = pdf_text(res.pdf_path)
    assert "No data received from this agent" in text


def test_no_skeptic(fixture_data, stub, tmp_path):
    del fixture_data["skeptic"]
    res = run(fixture_data, tmp_path, stub)
    assert res.status == "ok"
    assert any("Skeptic" in w for w in res.warnings)


# ---------------------------------------------------------------- invalid JSON


def test_invalid_json_retry_then_success(fixture_data, tmp_path):
    good = {"takeaway": "Genetics support the axis; IL-17A itself is barrier-protective.", "synthesis": ""}
    stub = StubClient(section_overrides={"Target biology & human genetics": ["not json at all", "```json\n" + json.dumps(good) + "\n```"]})
    res = run(fixture_data, tmp_path, stub)
    assert res.status == "ok"
    assert len(stub.calls) == 7  # one retry
    retry_msgs = stub.calls[1]
    assert retry_msgs[-1]["role"] == "user" and "not usable" in retry_msgs[-1]["content"]
    assert "barrier-protective" in pdf_text(res.pdf_path)


def test_invalid_json_twice_degrades_section(fixture_data, tmp_path):
    stub = StubClient(section_overrides={"Safety": ["{broken", "still { not json"]})
    res = run(fixture_data, tmp_path, stub)
    assert res.status == "degraded"
    assert any(w.startswith("Safety: narrative unavailable") for w in res.warnings)
    text = pdf_text(res.pdf_path)
    assert "Narrative unavailable" in text
    assert norm(fixture_data["analysts"][2]["claims"][0]["text"]) in text  # claims still printed


def test_final_synthesis_failure_still_produces_pdf(fixture_data, tmp_path):
    stub = StubClient(final=LLMError("connection refused"))
    res = run(fixture_data, tmp_path, stub)
    assert res.status == "degraded"
    assert res.recommendation == "Do not invest"
    assert any("Final synthesis failed" in w for w in res.warnings)
    assert "Rationale unavailable" in pdf_text(res.pdf_path)


def test_client_crash_is_contained(fixture_data, tmp_path):
    stub = StubClient(section=RuntimeError("boom"), final=RuntimeError("boom"))
    res = run(fixture_data, tmp_path, stub)
    assert res.status == "degraded" and res.pdf_path


def test_extract_json_defensive():
    assert extract_json('Sure! Here it is:\n```json\n{"a": 1}\n```\nThanks') == {"a": 1}
    assert extract_json('noise {"a": {"b": [1, 2]}} trailing') == {"a": {"b": [1, 2]}}
    assert extract_json('{"a": "}"}') == {"a": "}"}
    for bad in ("", "[1, 2]", "no braces"):
        try:
            extract_json(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


# ---------------------------------------------------------------- numbers


def test_number_not_in_input_is_flagged(fixture_data, tmp_path):
    stub = StubClient(
        section={"takeaway": "Response rate was 73% in a 120-patient study.", "synthesis": "The trial enrolled 59 patients."},
        final={"rationale": "Capital need is $2.5M (range $1.5M–$4M) at 84% confidence; 2 trials failed.", "risks": ["Risk costs 7.5 million."]},
    )
    res = run(fixture_data, tmp_path, stub)
    flagged = [w for w in res.warnings if "do not occur in the input" in w]
    joined = " ".join(flagged)
    assert "73%" in joined and "120" in joined
    assert "7.5 million" in joined
    # Numbers present in the input (59, $2.5M, $1.5M, $4M, 84%) and derived counts (2) are accepted.
    for ok in ("'59'", "2.5M", "1.5M", "'84%'", "$4M"):
        assert ok not in joined
    assert not any("Recommendation rationale" in w for w in flagged)


# ---------------------------------------------------------------- unicode / escaping


def test_cyrillic_and_markup_in_pdf(fixture_data, stub, tmp_path):
    fixture_data["thesis"]["indication"] = "Хвороба Крона"
    fixture_data["analysts"][0]["claims"][0]["text"] = "Варіанти IL23R <захисні> & пов'язані з ризиком ≥ 2× — «тест»"
    res = run(fixture_data, tmp_path, stub)
    assert res.status == "ok"
    text = pdf_text(res.pdf_path)
    assert "Хвороба Крона" in text
    assert "Варіанти IL23R <захисні> & пов'язані з ризиком ≥ 2× — «тест»" in text


# ---------------------------------------------------------------- truncation / errors


def test_truncation_warns_but_pdf_has_all_claims(fixture_data, stub, tmp_path):
    res = run(fixture_data, tmp_path, stub, max_prompt_chars=2500)
    assert any("input truncated for the model" in w for w in res.warnings)
    text = pdf_text(res.pdf_path)
    for section in fixture_data["analysts"]:
        for claim in section["claims"]:
            assert norm(claim["text"]) in text
    for call in stub.calls:
        payload = call[1]["content"].split("INPUT JSON:\n", 1)[1]
        assert len(json.dumps(json.loads(payload), ensure_ascii=False)) <= 2500


def test_bad_input_returns_error(tmp_path, stub):
    res = run({"thesis": {"indication": "x"}}, tmp_path, stub)
    assert res.status == "error" and "mechanism" in res.message
    res = run(tmp_path / "missing.json", tmp_path, stub)
    assert res.status == "error"


def test_result_is_json_serialisable(fixture_data, stub, tmp_path):
    json.dumps(run(fixture_data, tmp_path, stub).to_dict())
