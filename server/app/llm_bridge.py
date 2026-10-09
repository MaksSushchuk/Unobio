"""One LLM for the whole run: the analytics client (LLM_* / GEMINI_* settings, cache, retries, concurrency
limit) behind the synchronous interfaces the researcher and the writer expect.

Idea taken from the teammate's v1.0.0 orchestrator (orchestrator/llm_bridge.py). researcher and writer are
synchronous and run in worker threads (asyncio.to_thread); every call is submitted back to the event loop
that owns the shared analytics client, so the cache, the rate-limit handling and the traces cover all modules.
Without it the writer could only talk to an Ollama server (WRITER_LLM_URL).
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from analytics.llm import LLMClient, LLMError, LLMRequest, Message, extract_json
from analytics.schemas import AgentTrace

T = TypeVar("T", bound=BaseModel)

CALL_TIMEOUT_S = 600  # per call, on top of the client's own timeout and retries
RESEARCHER_MAX_TOKENS = 2000
RESEARCHER_ATTEMPTS = 2  # first answer + one corrective retry on invalid JSON
WRITER_MAX_TOKENS = 2500

# Offline stand-in answers for LLM_PROVIDER=fake so the writer step runs without a model. NOT analysis.
FAKE_WRITER_ANSWER: dict[str, Any] = {
    "takeaway": "[fake writer] Section takeaway.", "synthesis": "",
    "rationale": "[fake writer] Recommendation rationale.",
    "risks": [], "critical_unknowns": [], "diligence_questions": [],
}


class _Bridge:
    def __init__(self, llm: LLMClient, loop: asyncio.AbstractEventLoop, provider: str) -> None:
        self.llm, self.loop, self.provider = llm, loop, provider

    def _complete(self, request: LLMRequest):
        return asyncio.run_coroutine_threadsafe(self.llm.complete(request), self.loop).result(CALL_TIMEOUT_S)


class ResearcherLLM(_Bridge):
    """researcher.llm.LLM protocol: generate_json(prompt, schema, purpose) -> schema instance; raises LlmError.
    Calls are recorded in `.calls` (researcher.schema.LlmCall), as the researcher's own Gemini client does."""

    def __init__(self, llm: LLMClient, loop: asyncio.AbstractEventLoop, provider: str) -> None:
        super().__init__(llm, loop, provider)
        self.calls: list[Any] = []

    def generate_json(self, prompt: str, schema: type[T], purpose: str | None = None) -> T:
        from researcher.llm import LlmError
        from researcher.schema import LlmCall

        # The JSON Schema goes into the prompt too: providers in "object" JSON mode (Gemini's default here)
        # do not enforce it, and without it the model echoed the input instead of the plan.
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        content = (f"{prompt}\n\nReturn ONLY one JSON object that conforms to this JSON Schema "
                   f"(fill every required field; do not repeat the input):\n{schema_json}")
        started, t0 = datetime.now(UTC), time.monotonic()
        response, result, error, attempts, in_tok, out_tok = None, None, None, 0, 0, 0
        for attempts in range(1, RESEARCHER_ATTEMPTS + 1):
            messages = [Message(role="user", content=content)]
            if error and response is not None:  # one corrective retry with the validation error
                messages += [Message(role="assistant", content=response.text),
                             Message(role="user", content=f"That answer is invalid: {error}. "
                                                          "Return the corrected JSON object only.")]
            request = LLMRequest(messages=messages, json_schema=schema.model_json_schema(),
                                 schema_name=schema.__name__, max_tokens=RESEARCHER_MAX_TOKENS,
                                 tag=f"researcher:{purpose or 'call'}")
            try:
                response = self._complete(request)
                in_tok += response.input_tokens or 0
                out_tok += response.output_tokens or 0
                result = schema.model_validate(extract_json(response.text))
                error = None
                break
            except (LLMError, TimeoutError) as e:  # transport: the client already retried
                error = f"{type(e).__name__}: {e}"[:300]
                break
            except (ValidationError, ValueError) as e:
                error = f"{type(e).__name__}: {e}"[:300]
        self.calls.append(LlmCall(
            provider=self.provider, model=response.model if response else self.llm.model, purpose=purpose,
            started_at=started, latency_s=round(time.monotonic() - t0, 3), attempts=attempts, ok=result is not None,
            input_tokens=in_tok or None, output_tokens=out_tok or None, error=error,
        ))
        if result is None:
            raise LlmError(error or "no response")
        return result


class WriterLLM(_Bridge):
    """writer_agent.llm.LLMClient protocol: chat(messages) -> raw text; raises the writer's LLMError."""

    def __init__(self, llm: LLMClient, loop: asyncio.AbstractEventLoop, provider: str) -> None:
        super().__init__(llm, loop, provider)
        self.traces: list[AgentTrace] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        from writer_agent.llm import LLMError as WriterLLMError  # pyright: ignore[reportMissingImports]

        # No json_schema: the writer's prompts spell out the JSON shape and its parser extracts it.
        # A bare {"type": "object"} schema makes strict providers (Gemini) answer with an empty object.
        request = LLMRequest(messages=[Message(role=m["role"], content=m["content"]) for m in messages],  # type: ignore[arg-type]
                             max_tokens=WRITER_MAX_TOKENS, tag="writer")
        t0 = time.monotonic()
        try:
            response = self._complete(request)
        except (LLMError, TimeoutError) as e:
            self.traces.append(AgentTrace(agent="writer", step="narrative", model=self.llm.model, ok=False,
                                          latency_s=round(time.monotonic() - t0, 3), error=str(e)[:300]))
            raise WriterLLMError(str(e)) from e
        self.traces.append(AgentTrace(agent="writer", step="narrative", model=response.model,
                                      attempt=len(self.traces) + 1, input_tokens=response.input_tokens,
                                      output_tokens=response.output_tokens, latency_s=response.latency_s,
                                      cost_usd=response.cost_usd))
        return response.text
