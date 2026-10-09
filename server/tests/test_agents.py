"""Step 4 — AgentSpec + AgentRunner with FakeLLM (no network)."""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from analytics.agents import AgentRunner, AgentSpec, load_prompt, render
from analytics.llm import FakeLLM, LLMError, LLMRequest, LLMResponse, make_llm, LLMSettings


class Verdict(BaseModel):
    score: int = Field(ge=0, le=5)
    cites: list[str]


SPEC = AgentSpec(name="demo", system_prompt="You score things.", output_model=Verdict, max_attempts=3)


def runner(fake: FakeLLM) -> AgentRunner:
    return AgentRunner(make_llm(LLMSettings(provider="fake"), fake=fake))


def run(coro):
    return asyncio.run(coro)


def known_ids(v: Verdict) -> list[str]:
    return [f"unknown evidence id {c}" for c in v.cites if c not in {"E1", "E2"}]


def test_valid_first_try():
    fake = FakeLLM(script={"demo": {"score": 3, "cites": ["E1"]}})
    res = run(runner(fake).run(SPEC, "go", validate=known_ids))
    assert res.ok and res.attempts == 1 and res.output == Verdict(score=3, cites=["E1"])
    assert len(res.traces) == 1 and res.traces[0].ok
    req = fake.calls[0]
    assert req.json_schema == Verdict.model_json_schema()
    assert "Answer format" in req.messages[0].content  # schema also described in text


def test_retries_through_three_kinds_of_errors():
    fake = FakeLLM(script={"demo": [
        "sorry, I cannot answer in JSON",                # 1. parse error
        {"score": 9, "cites": ["E1"]},                    # 2. schema error (score > 5)
        {"score": 2, "cites": ["E99"]},                   # 3. semantic error (unknown id)
        {"score": 2, "cites": ["E2"]},                    # valid, but max_attempts=3 stops before it
    ]})
    res = run(runner(fake).run(SPEC, "go", validate=known_ids))
    assert not res.ok and res.attempts == 3
    assert res.errors == ["unknown evidence id E99"]
    assert [t.ok for t in res.traces] == [False, False, False]


def test_retry_prompt_contains_previous_answer_and_errors():
    fake = FakeLLM(script={"demo": [{"score": 9, "cites": ["E1"]}, {"score": 4, "cites": ["E1"]}]})
    res = run(runner(fake).run(SPEC, "go"))
    assert res.ok and res.attempts == 2
    second = fake.calls[1].messages
    assert [m.role for m in second] == ["system", "user", "assistant", "user"]
    assert "score" in second[3].content and "rejected" in second[3].content


def test_transport_failure_returns_failed_result_not_exception():
    class Down(FakeLLM):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            raise LLMError("connection refused")

    res = run(runner(Down()).run(SPEC, "go"))
    assert not res.ok and res.output is None and "connection refused" in res.errors[0]
    assert res.traces[0].error


def test_reasoning_model_think_block_is_ignored():
    fake = FakeLLM(script={"demo": '<think>maybe {"score": 0}? no...</think>\n{"score": 1, "cites": ["E1"]}'})
    res = run(runner(fake).run(SPEC, "go"))
    assert res.ok and res.output is not None and res.output.score == 1


def test_prompt_files_and_rendering():
    common = load_prompt("_common")
    assert "Use only the evidence provided" in common
    assert render("Lens: $lens, json {\"a\": 1}", lens="science") == 'Lens: science, json {"a": 1}'
    with pytest.raises(KeyError):
        render("$missing")
