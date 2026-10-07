"""FakeLLM: a scripted provider for tests and offline development.

No network, no cost, fully deterministic. Answers are looked up by the request
`tag` (e.g. "lens:science"): a fixed string/dict, a list (returned in order,
to simulate "first answer invalid, retry valid"), or a function of the request.
Every call is recorded in `calls` so tests can assert what the agent sent.
"""
from __future__ import annotations

import json
import math
from typing import Any, Callable

from .base import LLMRequest, LLMResponse

Script = str | dict | list | Callable[[LLMRequest], str | dict]


class FakeLLM:
    def __init__(self, script: dict[str, Script] | None = None, default: Script | None = None,
                 model: str = "fake-model") -> None:
        self.model = model
        self.script: dict[str, Any] = dict(script or {})
        self.default = default
        self.calls: list[LLMRequest] = []
        self._cursor: dict[str, int] = {}

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        answer = self._answer(request)
        text = answer if isinstance(answer, str) else json.dumps(answer)
        prompt_chars = sum(len(m.content) for m in request.messages)
        return LLMResponse(text=text, model=request.model or self.model,
                           input_tokens=math.ceil(prompt_chars / 4), output_tokens=math.ceil(len(text) / 4))

    async def aclose(self) -> None:
        return None

    def _answer(self, request: LLMRequest) -> str | dict:
        entry = self.script.get(request.tag, self.default)
        if entry is None:
            raise KeyError(f"FakeLLM has no scripted answer for tag {request.tag!r}")
        if isinstance(entry, list):
            i = self._cursor.get(request.tag, 0)
            self._cursor[request.tag] = i + 1
            entry = entry[min(i, len(entry) - 1)]
        return entry(request) if callable(entry) else entry
