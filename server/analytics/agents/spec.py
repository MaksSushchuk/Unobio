"""AgentSpec — the declarative description of one agent.

An agent is NOT a class with its own logic. It is data:
  * who it is            -> name + system prompt
  * what it must return  -> output_model (Pydantic) -> JSON Schema sent to the LLM
  * how it is run        -> model, temperature, max_tokens, max_attempts

All agents are executed by the same AgentRunner. Adding an agent = adding a spec
and a prompt file, not writing a new execution loop.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class AgentSpec(Generic[T]):
    name: str  # unique, e.g. "lens:science"; also the FakeLLM script key and the trace label
    system_prompt: str
    output_model: type[T]  # shape of the RAW answer we expect from the LLM
    model: str | None = None  # override the default model for this agent (e.g. stronger model for synthesis)
    temperature: float = 0.0
    max_tokens: int = 2000
    max_attempts: int = 3  # 1 call + up to 2 validation retries
    schema_in_prompt: bool = True  # also describe the JSON shape in text (for providers without schema mode)

    def json_schema(self) -> dict:
        return self.output_model.model_json_schema()

    def full_system_prompt(self) -> str:
        if not self.schema_in_prompt:
            return self.system_prompt
        schema = json.dumps(self.json_schema(), separators=(",", ":"))
        return f"{self.system_prompt}\n\n## Answer format\nReturn one JSON object valid against this JSON Schema:\n{schema}"
