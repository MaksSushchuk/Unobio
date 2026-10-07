"""Wrapper that every agent call goes through: cache -> concurrency limit -> retries.

* Cache (SQLite, data/cache/llm.sqlite): identical request + model -> stored answer.
  Makes demo reruns instant, free and identical.
* Concurrency limit: at most N calls in flight (a shared Ollama server would queue anyway).
* Transport retries with exponential backoff for network errors, 429 and 5xx.
  (Retries because the ANSWER is invalid are a different thing — the agent runner
  in step 4 does those, with the validation errors in the prompt.)
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from .base import LLMClient, LLMError, LLMRequest, LLMResponse
from .providers import _Retryable


class _Cache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, response TEXT)")
        self.db.commit()

    def get(self, key: str) -> LLMResponse | None:
        row = self.db.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        return LLMResponse.model_validate_json(row[0]) if row else None

    def put(self, key: str, response: LLMResponse) -> None:
        self.db.execute("INSERT OR REPLACE INTO llm_cache VALUES (?, ?)", (key, response.model_dump_json()))
        self.db.commit()


class ResilientLLM:
    def __init__(self, inner: LLMClient, max_concurrency: int = 2, cache_path: Path | None = None,
                 retries: int = 3, backoff_s: float = 2.0) -> None:
        self.inner = inner
        self.model = inner.model
        self._sem = asyncio.Semaphore(max_concurrency)
        self._cache = _Cache(cache_path) if cache_path else None
        self.retries = retries
        self.backoff_s = backoff_s

    async def complete(self, request: LLMRequest) -> LLMResponse:
        key = request.cache_key(request.model or self.model)
        if self._cache and (hit := self._cache.get(key)):
            return hit.model_copy(update={"cached": True, "latency_s": 0.0, "cost_usd": 0.0})
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                async with self._sem:
                    response = await self.inner.complete(request)
                break
            except _Retryable as exc:
                last = exc
                if attempt < self.retries:
                    await asyncio.sleep(self.backoff_s * 2**attempt)
        else:
            raise LLMError(f"LLM call failed after {self.retries + 1} attempts: {last}")
        if self._cache:
            self._cache.put(key, response)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()
