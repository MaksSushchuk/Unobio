"""LLM settings from environment variables (optionally loaded from server/.env).

| Variable            | Default            | Meaning                                                     |
|---------------------|--------------------|-------------------------------------------------------------|
| LLM_PROVIDER        | fake               | fake | ollama | openai (any OpenAI-compatible API)          |
| LLM_BASE_URL        | provider default   | ollama: http://localhost:11434 ; openai: https://.../v1     |
| LLM_API_KEY         | (empty)            | Bearer token for OpenAI-compatible APIs                    |
| LLM_MODEL           | provider default   | e.g. qwen2.5:14b, llama-3.3-70b-versatile, gpt-4o-mini      |
| LLM_NUM_CTX         | 16384              | Ollama context window (Ollama defaults to 2048 and truncates silently!) |
| LLM_JSON_MODE       | schema             | schema | object | none — how strictly JSON is requested     |
| LLM_PRICE_IN/OUT    | 0                  | USD per 1M input / output tokens (for cost per run)        |
| LLM_TIMEOUT         | 180                | seconds per call                                            |
| LLM_MAX_CONCURRENCY | 2                  | parallel calls (a shared Ollama server queues anyway)       |
| LLM_CACHE           | 1                  | cache responses in data/cache/llm.sqlite (reruns are free)  |
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from evidence_bundle.paths import CACHE_DIR, SERVER_DIR

Provider = Literal["fake", "ollama", "openai"]
JsonMode = Literal["schema", "object", "none"]

DEFAULTS = {
    "ollama": ("http://localhost:11434", "qwen2.5:14b"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "fake": ("", "fake-model"),
}


def load_dotenv(path: Path = SERVER_DIR / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass
class LLMSettings:
    provider: Provider = "fake"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    num_ctx: int = 16384
    json_mode: JsonMode = "schema"
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0
    timeout_s: float = 180.0
    max_concurrency: int = 2
    cache_enabled: bool = True
    cache_path: Path = field(default_factory=lambda: CACHE_DIR / "llm.sqlite")

    @classmethod
    def from_env(cls) -> "LLMSettings":
        load_dotenv()
        provider = _env("LLM_PROVIDER", "fake")
        if provider not in DEFAULTS:
            raise ValueError(f"LLM_PROVIDER must be one of {list(DEFAULTS)}, got {provider!r}")
        base_url, model = DEFAULTS[provider]
        return cls(
            provider=provider,  # type: ignore[arg-type]
            base_url=_env("LLM_BASE_URL", base_url).rstrip("/"),
            api_key=_env("LLM_API_KEY", ""),
            model=_env("LLM_MODEL", model),
            num_ctx=int(_env("LLM_NUM_CTX", "16384")),
            json_mode=_env("LLM_JSON_MODE", "schema"),  # type: ignore[arg-type]
            price_in_per_mtok=float(_env("LLM_PRICE_IN", "0")),
            price_out_per_mtok=float(_env("LLM_PRICE_OUT", "0")),
            timeout_s=float(_env("LLM_TIMEOUT", "180")),
            max_concurrency=int(_env("LLM_MAX_CONCURRENCY", "2")),
            cache_enabled=_env("LLM_CACHE", "1") == "1",
        )

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return round(input_tokens / 1e6 * self.price_in_per_mtok + output_tokens / 1e6 * self.price_out_per_mtok, 6)
