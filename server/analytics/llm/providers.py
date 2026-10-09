"""Concrete providers: Ollama (native API), Google Gemini (native REST API) and any OpenAI-compatible API.

Why Ollama uses its native API and not its OpenAI-compatible `/v1` endpoint:
only the native `/api/chat` lets us set `num_ctx` per request (Ollama's default
context is 2048 tokens and longer prompts are truncated SILENTLY) and pass a
JSON Schema in `format` for constrained decoding.
"""
from __future__ import annotations

import asyncio
import re
import time

import httpx

from .base import LLMError, LLMRequest, LLMResponse
from .config import LLMSettings


class _Retryable(LLMError):
    """Transient failure: the resilient wrapper retries it with backoff."""


class _HttpProvider:
    def __init__(self, settings: LLMSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.s = settings
        self.model = settings.model
        headers = self._auth_headers(settings.api_key) if settings.api_key else {}
        self._http = httpx.AsyncClient(timeout=settings.timeout_s, headers=headers, transport=transport)

    @staticmethod
    def _auth_headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, url: str, body: dict) -> tuple[dict, float]:
        t0 = time.perf_counter()
        try:
            resp = await self._http.post(url, json=body)
        except httpx.HTTPError as exc:
            raise _Retryable(f"{exc.__class__.__name__}: {exc}") from exc
        latency = time.perf_counter() - t0
        if resp.status_code in (408, 409, 429) or resp.status_code >= 500:
            raise _Retryable(f"HTTP {resp.status_code}: {resp.text[:1500]}")
        if resp.status_code >= 400:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json(), latency


class OllamaProvider(_HttpProvider):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model or self.model
        body: dict = {
            "model": model,
            "messages": [m.model_dump() for m in request.messages],
            "stream": False,
            "options": {"temperature": request.temperature, "num_ctx": self.s.num_ctx, "num_predict": request.max_tokens},
        }
        if request.json_schema is not None and self.s.json_mode != "none":
            body["format"] = request.json_schema if self.s.json_mode == "schema" else "json"
        data, latency = await self._post(f"{self.s.base_url}/api/chat", body)
        try:
            text = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Ollama response: {str(data)[:300]}") from exc
        tin, tout = int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)
        if tin and tin >= self.s.num_ctx - 16:
            raise LLMError(f"Prompt filled the whole context window ({tin}/{self.s.num_ctx} tokens) — "
                           "it was probably truncated. Lower the context budget or raise LLM_NUM_CTX.")
        return LLMResponse(text=text, model=model, input_tokens=tin, output_tokens=tout,
                           latency_s=round(latency, 3), cost_usd=self.s.cost(tin, tout))


class OpenAICompatProvider(_HttpProvider):
    """OpenAI chat-completions format: OpenAI, Groq, OpenRouter, Together, vLLM, LM Studio, ..."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model or self.model
        body: dict = {
            "model": model,
            "messages": [m.model_dump() for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.json_schema is not None:
            if self.s.json_mode == "schema":
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": request.schema_name, "schema": request.json_schema, "strict": False}}
            elif self.s.json_mode == "object":
                body["response_format"] = {"type": "json_object"}
        data, latency = await self._post(f"{self.s.base_url}/chat/completions", body)
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response: {str(data)[:300]}") from exc
        if choice.get("finish_reason") == "length":
            raise LLMError("Answer was cut off by max_tokens (finish_reason=length).")
        usage = data.get("usage") or {}
        tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        return LLMResponse(text=text, model=data.get("model", model), input_tokens=tin, output_tokens=tout,
                           latency_s=round(latency, 3), cost_usd=self.s.cost(tin, tout))


class GeminiProvider(_HttpProvider):
    """Google Gemini, native `generateContent` REST API (same key/model as the researcher: GEMINI_*).

    * system messages -> `systemInstruction`; assistant -> role "model".
    * JSON: `responseMimeType=application/json` (json_mode object, the default for Gemini);
      json_mode schema also sends `responseJsonSchema`.
    * 429: waits the `retryDelay` Google returns (free tier is limited per minute), then the
      resilient wrapper retries.
    * Thinking models spend output tokens on "thoughts": maxOutputTokens gets headroom for that,
      and thought parts are dropped from the answer text.
    """

    THINKING_HEADROOM = 4096
    MAX_RETRY_WAIT_S = 60.0

    @staticmethod
    def _auth_headers(api_key: str) -> dict[str, str]:
        return {"x-goog-api-key": api_key}

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model or self.model
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        contents = [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
                    for m in request.messages if m.role != "system"]
        config: dict = {"temperature": request.temperature,
                        "maxOutputTokens": request.max_tokens + self.THINKING_HEADROOM}
        if request.json_schema is not None and self.s.json_mode != "none":
            config["responseMimeType"] = "application/json"
            if self.s.json_mode == "schema":
                config["responseJsonSchema"] = request.json_schema
        body: dict = {"contents": contents, "generationConfig": config}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        try:
            data, latency = await self._post(f"{self.s.base_url}/models/{model}:generateContent", body)
        except _Retryable as exc:
            if "HTTP 429" in str(exc):
                await asyncio.sleep(_retry_delay(str(exc), self.MAX_RETRY_WAIT_S))
            raise
        try:
            candidate = data["candidates"][0]
            parts = candidate.get("content", {}).get("parts") or []
        except (KeyError, IndexError, TypeError) as exc:
            feedback = data.get("promptFeedback") if isinstance(data, dict) else None
            raise LLMError(f"Unexpected Gemini response (blocked?): {str(feedback or data)[:300]}") from exc
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        finish = candidate.get("finishReason")
        if finish == "MAX_TOKENS":
            raise LLMError("Answer was cut off by maxOutputTokens (finishReason=MAX_TOKENS).")
        if finish not in (None, "STOP") and not text:
            raise LLMError(f"Gemini returned no text (finishReason={finish}).")
        usage = data.get("usageMetadata") or {}
        tin = int(usage.get("promptTokenCount") or 0)
        tout = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
        return LLMResponse(text=text, model=data.get("modelVersion", model), input_tokens=tin, output_tokens=tout,
                           latency_s=round(latency, 3), cost_usd=self.s.cost(tin, tout))


def _retry_delay(message: str, cap: float) -> float:
    """Google puts e.g. "retryDelay": "23s" in the 429 body; default 10 s."""
    m = re.search(r'retryDelay"?\s*:\s*"?(\d+(?:\.\d+)?)s', message)
    return min(float(m.group(1)) if m else 10.0, cap)
