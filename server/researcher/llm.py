"""Thin provider-agnostic LLM wrapper. Currently backed by Gemini (google-genai SDK).

Callers depend only on the `LLM` protocol: generate_json(prompt, schema) -> schema instance.
Every call is recorded in `.calls` (LlmCall) so the bundle can expose cost/latency.
"""

from __future__ import annotations

import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from researcher.schema import LlmCall

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
RETRY_CODES = {429, 500, 503}

T = TypeVar("T", bound=BaseModel)


class LlmError(Exception):
    pass


class LLM(Protocol):
    calls: list[LlmCall]

    def generate_json(self, prompt: str, schema: type[T], purpose: str | None = None) -> T: ...


def load_env() -> None:
    """Load server/.env into os.environ without overriding variables already set."""
    load_dotenv(ENV_PATH, override=False)


class GeminiLLM:
    provider = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        max_retries: int = 4,
        backoff_base_s: float = 2.0,
        max_delay_s: float = 65.0,
        timeout_s: float = 60.0,
        client: Any = None,
    ) -> None:
        from google import genai
        from google.genai import types

        self.model = model
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.max_delay_s = max_delay_s
        self.calls: list[LlmCall] = []
        self._types = types
        self._client = client or genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000))
        )

    @classmethod
    def from_env(cls, **kw: Any) -> GeminiLLM | None:
        """Build from GEMINI_API_KEY / GEMINI_MODEL (server/.env). None if no key is configured."""
        load_env()
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            return None
        model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
        return cls(api_key=key, model=model, **kw)

    def generate_json(self, prompt: str, schema: type[T], purpose: str | None = None) -> T:
        from google.genai import errors

        config = self._types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema(),
            automatic_function_calling=self._types.AutomaticFunctionCallingConfig(disable=True),
        )
        started_at = datetime.now(UTC)
        t0 = time.monotonic()
        attempts = 0
        response = None
        error: str | None = None
        result: T | None = None

        while True:
            attempts += 1
            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=prompt, config=config
                )
                break
            except errors.APIError as e:
                error = f"{e.code} {e.status}: {e.message}"
                delay = self._retry_delay(e, attempts)
                if delay is None:
                    break
                time.sleep(delay)
            except Exception as e:  # transport errors, timeouts
                error = f"{type(e).__name__}: {e}"
                break

        if response is not None:
            error = None
            try:
                result = schema.model_validate_json(response.text or "")
            except ValidationError as e:
                error = f"invalid JSON for {schema.__name__}: {e.error_count()} errors"

        usage = getattr(response, "usage_metadata", None)
        self.calls.append(
            LlmCall(
                provider=self.provider,
                model=self.model,
                purpose=purpose,
                started_at=started_at,
                latency_s=round(time.monotonic() - t0, 3),
                attempts=attempts,
                ok=result is not None,
                input_tokens=getattr(usage, "prompt_token_count", None),
                output_tokens=getattr(usage, "candidates_token_count", None),
                thinking_tokens=getattr(usage, "thoughts_token_count", None),
                error=error,
            )
        )
        if result is None:
            raise LlmError(error or "no response")
        return result

    def _retry_delay(self, e: Any, attempts: int) -> float | None:
        if e.code not in RETRY_CODES or attempts > self.max_retries:
            return None
        delay = _server_retry_delay(e.details)
        if delay is None:
            delay = self.backoff_base_s * 2 ** (attempts - 1)
        # A long server-requested delay means the quota (e.g. daily) is exhausted: give up.
        if delay > self.max_delay_s:
            return None
        return delay


def _server_retry_delay(details: Any) -> float | None:
    """Extract google.rpc.RetryInfo.retryDelay (e.g. "17s") from an error body, if present."""
    if not isinstance(details, dict):
        return None
    for item in (details.get("error") or {}).get("details") or []:
        if isinstance(item, dict) and str(item.get("@type", "")).endswith("RetryInfo"):
            m = re.fullmatch(r"([\d.]+)s", str(item.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    return None
