from types import SimpleNamespace

import pytest
from google.genai import errors
from pydantic import BaseModel

from researcher import llm as llm_mod
from researcher.llm import GeminiLLM, LlmError


class Out(BaseModel):
    x: int


def rate_limited(delay="1s"):
    return errors.ClientError(
        429,
        {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                   "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]}},
    )


def ok_response(text='{"x": 1}'):
    usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=3, thoughts_token_count=None)
    return SimpleNamespace(text=text, usage_metadata=usage)


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.configs = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.configs.append(config)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_mod.time, "sleep", calls.append)
    return calls


def make(outcomes, **kw):
    return GeminiLLM(api_key="test", model="m", client=FakeClient(outcomes), **kw)


def test_structured_output_temperature_zero_and_recorded(sleeps):
    g = make([ok_response()])
    assert g.generate_json("p", Out, purpose="t") == Out(x=1)
    cfg = g._client.configs[0]
    assert cfg.temperature == 0
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == Out.model_json_schema()
    call = g.calls[0]
    assert (call.ok, call.attempts, call.input_tokens, call.output_tokens, call.purpose) == (True, 1, 10, 3, "t")


def test_retries_429_using_server_delay(sleeps):
    g = make([rate_limited("7s"), rate_limited("2.5s"), ok_response()])
    assert g.generate_json("p", Out).x == 1
    assert sleeps == [7.0, 2.5]
    assert g.calls[0].attempts == 3 and g.calls[0].ok


def test_gives_up_on_long_quota_delay(sleeps):
    g = make([rate_limited("3600s")])
    with pytest.raises(LlmError):
        g.generate_json("p", Out)
    assert sleeps == []
    assert not g.calls[0].ok and "429" in g.calls[0].error


def test_gives_up_after_max_retries(sleeps):
    g = make([rate_limited()] * 3, max_retries=2)
    with pytest.raises(LlmError):
        g.generate_json("p", Out)
    assert g.calls[0].attempts == 3


def test_invalid_json_is_an_error(sleeps):
    g = make([ok_response('{"x": "nope"}')])
    with pytest.raises(LlmError, match="invalid JSON"):
        g.generate_json("p", Out)
    assert not g.calls[0].ok
