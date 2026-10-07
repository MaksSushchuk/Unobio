"""Provider-agnostic LLM interface.

Every provider (Ollama, any OpenAI-compatible API, the fake used in tests)
implements one method:

    async def complete(request: LLMRequest) -> LLMResponse

Agents (step 4) only see this interface, so switching provider or model is a
configuration change, not a code change.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str


class LLMRequest(BaseModel):
    messages: list[Message]
    json_schema: dict[str, Any] | None = Field(
        default=None, description="If set, the provider is asked to return JSON matching this schema"
    )
    schema_name: str = "answer"
    temperature: float = 0.0  # deterministic as far as the provider allows
    max_tokens: int = 2000
    model: str | None = Field(default=None, description="Override the client's default model for this call")
    tag: str = Field(default="", description="Who is calling (agent / lens); used by FakeLLM and traces")

    def cache_key(self, model: str) -> str:
        payload = self.model_dump(exclude={"tag"}) | {"model": model}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class LLMResponse(BaseModel):
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    cached: bool = False


class LLMError(RuntimeError):
    """Provider failed after transport retries (network, 5xx, rate limit, bad response)."""


class LLMClient(Protocol):
    model: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    async def aclose(self) -> None: ...
