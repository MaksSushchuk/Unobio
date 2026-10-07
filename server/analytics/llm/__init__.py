"""Step 3 — LLM layer. Use `make_llm()` to get a ready client (provider from env, cache, retries, limits)."""
from __future__ import annotations

from .base import LLMClient, LLMError, LLMRequest, LLMResponse, Message
from .config import LLMSettings
from .fake import FakeLLM
from .json_utils import JSONExtractionError, extract_json
from .providers import OllamaProvider, OpenAICompatProvider
from .resilient import ResilientLLM


def make_llm(settings: LLMSettings | None = None, fake: FakeLLM | None = None) -> ResilientLLM:
    """Build the client agents use. Pass `fake` to force a FakeLLM (tests)."""
    s = settings or LLMSettings.from_env()
    if fake is not None or s.provider == "fake":
        inner: LLMClient = fake or FakeLLM(
            script={"ping": {"ok": True, "model_name": "fake-model", "two_plus_two": 4}},
            default={"note": "FakeLLM default answer — set LLM_PROVIDER to use a real model"},
        )
        return ResilientLLM(inner, max_concurrency=s.max_concurrency, cache_path=None)
    inner = OllamaProvider(s) if s.provider == "ollama" else OpenAICompatProvider(s)
    return ResilientLLM(inner, max_concurrency=s.max_concurrency, cache_path=s.cache_path if s.cache_enabled else None)


__all__ = [
    "LLMClient", "LLMError", "LLMRequest", "LLMResponse", "Message", "LLMSettings", "FakeLLM",
    "JSONExtractionError", "extract_json", "OllamaProvider", "OpenAICompatProvider", "ResilientLLM", "make_llm",
]
