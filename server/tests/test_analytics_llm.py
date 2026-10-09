"""Step 3 — LLM layer, fully offline (httpx.MockTransport instead of a real server)."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from analytics.llm import (
    FakeLLM, GeminiProvider, JSONExtractionError, LLMError, LLMRequest, LLMSettings, Message, OllamaProvider,
    OpenAICompatProvider, ResilientLLM, extract_json, make_llm,
)

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def req(tag: str = "t", schema=SCHEMA) -> LLMRequest:
    return LLMRequest(messages=[Message(role="system", content="sys"), Message(role="user", content="hello")],
                      json_schema=schema, tag=tag)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- JSON extraction


@pytest.mark.parametrize("text", [
    '{"ok": true}',
    'Sure! ```json\n{"ok": true}\n``` hope it helps',
    'Here is the answer: {"ok": true, "note": "a } brace and a \\"quote\\" inside"} done',
])
def test_extract_json_tolerates_wrapping(text):
    assert extract_json(text)["ok"] is True


def test_extract_json_fails_loudly():
    with pytest.raises(JSONExtractionError):
        extract_json("no json here")


# ---------------------------------------------------------------- providers (request shape + parsing)


def test_ollama_request_uses_native_api_with_num_ctx_and_schema():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": '{"ok": true}'}, "prompt_eval_count": 120, "eval_count": 8})

    s = LLMSettings(provider="ollama", base_url="http://ollama:11434", model="qwen2.5:14b", num_ctx=8192,
                    price_in_per_mtok=1, price_out_per_mtok=5)
    p = OllamaProvider(s, transport=httpx.MockTransport(handler))
    r = run(p.complete(req()))
    assert seen["url"] == "http://ollama:11434/api/chat"
    assert seen["body"]["options"]["num_ctx"] == 8192 and seen["body"]["format"] == SCHEMA
    assert seen["body"]["stream"] is False
    assert (r.input_tokens, r.output_tokens) == (120, 8)
    assert r.cost_usd == pytest.approx(120 / 1e6 + 8 * 5 / 1e6)


def test_ollama_detects_silent_truncation():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "{}"}, "prompt_eval_count": 2048, "eval_count": 1})

    s = LLMSettings(provider="ollama", base_url="http://x", model="m", num_ctx=2048)
    with pytest.raises(LLMError, match="truncated"):
        run(OllamaProvider(s, transport=httpx.MockTransport(handler)).complete(req()))


def test_openai_compat_request_and_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "llama-3.3-70b", "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 5}})

    s = LLMSettings(provider="openai", base_url="https://api.groq.com/openai/v1", api_key="k", model="llama-3.3-70b")
    r = run(OpenAICompatProvider(s, transport=httpx.MockTransport(handler)).complete(req()))
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert r.text == '{"ok": true}' and r.input_tokens == 50


def test_openai_compat_json_object_mode():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    s = LLMSettings(provider="openai", base_url="http://x/v1", model="m", json_mode="object")
    run(OpenAICompatProvider(s, transport=httpx.MockTransport(handler)).complete(req()))
    assert seen["body"]["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------- resilience: retries, cache, concurrency


def test_retries_transient_errors_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"message": {"content": "{}"}, "prompt_eval_count": 1, "eval_count": 1})

    s = LLMSettings(provider="ollama", base_url="http://x", model="m")
    llm = ResilientLLM(OllamaProvider(s, transport=httpx.MockTransport(handler)), backoff_s=0)
    run(llm.complete(req()))
    assert calls["n"] == 3


def test_client_errors_are_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, text="bad model name")

    s = LLMSettings(provider="ollama", base_url="http://x", model="m")
    with pytest.raises(LLMError, match="400"):
        run(ResilientLLM(OllamaProvider(s, transport=httpx.MockTransport(handler)), backoff_s=0).complete(req()))
    assert calls["n"] == 1


def test_cache_makes_reruns_free(tmp_path):
    fake = FakeLLM(script={"t": {"ok": True}})
    llm = ResilientLLM(fake, cache_path=tmp_path / "llm.sqlite")
    first = run(llm.complete(req()))
    second = run(llm.complete(req()))
    assert not first.cached and second.cached and second.cost_usd == 0
    assert len(fake.calls) == 1


def test_concurrency_limit():
    state = {"now": 0, "max": 0}

    class Slow(FakeLLM):
        async def complete(self, request):
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
            await asyncio.sleep(0.01)
            state["now"] -= 1
            return await super().complete(request)

    llm = ResilientLLM(Slow(default={"ok": True}), max_concurrency=2)

    async def many():
        await asyncio.gather(*(llm.complete(req(tag=str(i))) for i in range(8)))

    run(many())
    assert state["max"] == 2


# ---------------------------------------------------------------- FakeLLM


def test_fake_llm_sequences_and_records_calls():
    fake = FakeLLM(script={"lens:science": ["not json", {"ok": True}]})
    llm = make_llm(LLMSettings(provider="fake"), fake=fake)
    a = run(llm.complete(req("lens:science")))
    b = run(llm.complete(req("lens:science")))
    assert a.text == "not json" and json.loads(b.text) == {"ok": True}
    assert [c.tag for c in fake.calls] == ["lens:science", "lens:science"]


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.1:8b")
    monkeypatch.setenv("LLM_NUM_CTX", "8192")
    s = LLMSettings.from_env()
    assert (s.provider, s.model, s.num_ctx, s.base_url) == ("ollama", "llama3.1:8b", 8192, "http://localhost:11434")


# ---------------------------------------------------------------- Gemini


def _gemini_settings(**kw) -> LLMSettings:
    return LLMSettings(provider="gemini", base_url="https://g.test/v1beta", api_key="k", model="gemini-x",
                       json_mode="object", **kw)


def test_gemini_request_shape_and_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"], seen["body"] = str(request.url), request.headers.get("x-goog-api-key"), json.loads(request.content)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"ok": true}'}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 5, "thoughtsTokenCount": 3},
            "modelVersion": "gemini-x-001"})

    r = run(GeminiProvider(_gemini_settings(), transport=httpx.MockTransport(handler)).complete(req()))
    assert seen["url"] == "https://g.test/v1beta/models/gemini-x:generateContent" and seen["key"] == "k"
    body = seen["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseJsonSchema" not in body["generationConfig"]
    assert r.text == '{"ok": true}' and (r.input_tokens, r.output_tokens) == (12, 8)


def test_gemini_max_tokens_is_an_error():
    def handler(request):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": tr'}]},
                                                          "finishReason": "MAX_TOKENS"}]})
    with pytest.raises(LLMError, match="MAX_TOKENS"):
        run(GeminiProvider(_gemini_settings(), transport=httpx.MockTransport(handler)).complete(req()))


def test_gemini_rate_limit_waits_retry_delay_then_retries(monkeypatch):
    calls = {"n": 0}
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("analytics.llm.providers.asyncio.sleep", fake_sleep)

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"details": [{"retryDelay": "7s"}]}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}, "finishReason": "STOP"}]})

    llm = ResilientLLM(GeminiProvider(_gemini_settings(), transport=httpx.MockTransport(handler)), backoff_s=0)
    assert run(llm.complete(req())).text == '{"ok": true}'
    assert calls["n"] == 2 and slept[0] == 7.0


def test_gemini_settings_reuse_researcher_variables(monkeypatch):
    for k in ("LLM_API_KEY", "LLM_MODEL", "LLM_JSON_MODE", "LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "abc")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    s = LLMSettings.from_env()
    assert (s.provider, s.api_key, s.model, s.json_mode) == ("gemini", "abc", "gemini-3.5-flash-lite", "object")
    assert s.base_url == "https://generativelanguage.googleapis.com/v1beta"
