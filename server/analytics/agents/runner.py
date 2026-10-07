"""AgentRunner — executes any AgentSpec with a validate-and-retry loop.

    attempt 1:  [system, user]                                  -> LLM -> parse -> validate
    attempt 2:  [system, user, assistant(bad answer), user(errors)] -> LLM -> parse -> validate
    ...         up to spec.max_attempts

Three layers of checks, cheapest first:
  1. parse      the text contains a JSON object                  (json_utils.extract_json)
  2. schema     the object matches spec.output_model             (Pydantic)
  3. semantics  caller-supplied checks, e.g. "cited ids exist"   (validate callback)

The runner never raises because of a bad answer: it returns AgentResult with
ok=False and the errors, and the orchestrator decides what to do (mark the lens
as failed, continue without it, ...). Every LLM call is recorded as an AgentTrace.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Generic

from pydantic import ValidationError

from ..llm import JSONExtractionError, LLMClient, LLMError, LLMRequest, Message, extract_json
from ..schemas import AgentTrace
from .spec import AgentSpec, T

Validator = Callable[[T], list[str]]  # returns human-readable errors; empty list = valid


@dataclass
class AgentResult(Generic[T]):
    agent: str
    ok: bool
    output: T | None
    attempts: int
    errors: list[str] = field(default_factory=list)  # errors of the LAST attempt (or transport error)
    traces: list[AgentTrace] = field(default_factory=list)
    raw_text: str = ""  # last raw answer, for debugging


class AgentRunner:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def run(self, spec: AgentSpec[T], user_prompt: str, validate: Validator[T] | None = None) -> AgentResult[T]:
        messages = [Message(role="system", content=spec.full_system_prompt()), Message(role="user", content=user_prompt)]
        traces: list[AgentTrace] = []
        errors: list[str] = []
        raw = ""

        for attempt in range(1, spec.max_attempts + 1):
            request = LLMRequest(
                messages=messages, json_schema=spec.json_schema(), schema_name=spec.name.replace(":", "_"),
                temperature=spec.temperature, max_tokens=spec.max_tokens, model=spec.model, tag=spec.name,
            )
            t0 = time.perf_counter()
            try:
                response = await self.llm.complete(request)
            except LLMError as exc:
                traces.append(AgentTrace(agent=spec.name, step="llm_call", model=spec.model or self.llm.model,
                                         attempt=attempt, ok=False, latency_s=round(time.perf_counter() - t0, 3),
                                         error=str(exc)[:300]))
                return AgentResult(spec.name, False, None, attempt, [f"LLM call failed: {exc}"], traces, raw)

            raw = response.text
            output, errors = self._check(spec, raw, validate)
            traces.append(AgentTrace(
                agent=spec.name, step="llm_call", model=response.model, attempt=attempt, ok=not errors,
                input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                latency_s=response.latency_s, cost_usd=response.cost_usd,
                error="; ".join(errors)[:300] if errors else None,
            ))
            if not errors:
                return AgentResult(spec.name, True, output, attempt, [], traces, raw)
            messages = [*messages[:2],
                        Message(role="assistant", content=raw[:4000]),
                        Message(role="user", content=retry_feedback(errors))]

        return AgentResult(spec.name, False, None, spec.max_attempts, errors, traces, raw)

    @staticmethod
    def _check(spec: AgentSpec[T], text: str, validate: Validator[T] | None) -> tuple[T | None, list[str]]:
        try:
            data = extract_json(text)
        except JSONExtractionError:
            return None, ["The answer is not a JSON object. Reply with one JSON object only."]
        try:
            output = spec.output_model.model_validate(data)
        except ValidationError as exc:
            return None, format_validation_error(exc)
        semantic = validate(output) if validate else []
        return (None, semantic) if semantic else (output, [])


def format_validation_error(exc: ValidationError, limit: int = 12) -> list[str]:
    out = []
    for err in exc.errors()[:limit]:
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        out.append(f"{loc}: {err['msg']}")
    return out


def retry_feedback(errors: list[str]) -> str:
    bullet = "\n".join(f"- {e}" for e in errors)
    return (f"Your previous answer was rejected by the validator:\n{bullet}\n\n"
            "Fix ALL of these problems and return the complete corrected JSON object only.")
