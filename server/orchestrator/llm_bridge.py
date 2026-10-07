"""One LLM for the whole workflow: analytics' client (LLM_* settings, cache, retries) behind the sync
interfaces researcher and writer expect.

researcher and writer are synchronous and run in worker threads (asyncio.to_thread); each call is
submitted to the orchestrator's event loop, where the shared analytics client lives, so the concurrency
limit and the cache apply to every module.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from analytics.llm import LLMClient, LLMError, LLMRequest, Message, extract_json
from analytics.schemas import AgentTrace
from researcher.llm import LlmError
from researcher.schema import LlmCall
from writer_agent.llm import LLMError as WriterLLMError

T = TypeVar("T", bound=BaseModel)

CALL_TIMEOUT_S = 600  # per call, on top of the client's own timeout and retries
RESEARCHER_MAX_TOKENS = 2000
WRITER_MAX_TOKENS = 2500


class _Bridge:
    def __init__(self, llm: LLMClient, loop: asyncio.AbstractEventLoop, provider: str) -> None:
        self.llm, self.loop, self.provider = llm, loop, provider

    def _complete(self, request: LLMRequest):
        return asyncio.run_coroutine_threadsafe(self.llm.complete(request), self.loop).result(CALL_TIMEOUT_S)


class ResearcherLLM(_Bridge):
    """researcher.llm.LLM protocol: generate_json(prompt, schema, purpose) -> schema instance; raises LlmError."""

    def __init__(self, llm: LLMClient, loop: asyncio.AbstractEventLoop, provider: str) -> None:
        super().__init__(llm, loop, provider)
        self.calls: list[LlmCall] = []

    def generate_json(self, prompt: str, schema: type[T], purpose: str | None = None) -> T:
        started, t0 = datetime.now(UTC), time.monotonic()
        request = LLMRequest(messages=[Message(role="user", content=prompt)], json_schema=schema.model_json_schema(),
                             schema_name=schema.__name__, max_tokens=RESEARCHER_MAX_TOKENS,
                             tag=f"researcher:{purpose or 'call'}")
        response, result, error = None, None, None
        try:
            response = self._complete(request)
            result = schema.model_validate(extract_json(response.text))
        except (LLMError, ValidationError, ValueError, TimeoutError) as e:
            error = f"{type(e).__name__}: {e}"[:300]
        self.calls.append(LlmCall(
            provider=self.provider, model=response.model if response else self.llm.model, purpose=purpose,
            started_at=started, latency_s=round(time.monotonic() - t0, 3), attempts=1, ok=result is not None,
            input_tokens=response.input_tokens if response else None,
            output_tokens=response.output_tokens if response else None, error=error,
        ))
        if result is None:
            raise LlmError(error or "no response")
        return result


class WriterLLM(_Bridge):
    """writer_agent.llm.LLMClient protocol: chat(messages) -> raw text; raises writer's LLMError."""

    def __init__(self, llm: LLMClient, loop: asyncio.AbstractEventLoop, provider: str) -> None:
        super().__init__(llm, loop, provider)
        self.traces: list[AgentTrace] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        # No json_schema: writer's prompts spell out the JSON shape and its parser extracts it. A bare
        # {"type": "object"} schema makes strict providers (Gemini) answer with an empty object.
        request = LLMRequest(messages=[Message(role=m["role"], content=m["content"]) for m in messages],
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


# Offline stand-in answers (LLM_PROVIDER=fake) so the workflow runs end-to-end without a model. NOT analysis.
FAKE_WRITER_ANSWER: dict[str, Any] = {
    "takeaway": "[fake writer] Section takeaway.", "synthesis": "",
    "rationale": "[fake writer] Recommendation rationale.",
    "risks": [], "critical_unknowns": [], "diligence_questions": [],
}
