"""Model client.

The model is self-hosted behind an Ollama-compatible HTTP API (POST /api/chat).
Only the standard library is used, so no other LLM provider SDK is pulled in.
Anything implementing ``LLMClient.chat`` can be injected, which is how the
tests run without the real model.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol

from .config import WriterConfig

log = logging.getLogger(__name__)

Message = dict[str, str]


class LLMError(RuntimeError):
    """The model call failed or returned unusable output."""


class LLMClient(Protocol):
    def chat(self, messages: list[Message]) -> str:
        """Return the assistant's raw text reply."""


class OllamaClient:
    def __init__(self, config: WriterConfig):
        self.config = config

    def chat(self, messages: list[Message]) -> str:
        body = {
            "model": self.config.llm_model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": self.config.llm_temperature, "num_ctx": self.config.llm_num_ctx},
        }
        req = urllib.request.Request(
            self.config.llm_url.rstrip("/") + "/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.llm_timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise LLMError(f"Model request failed: {exc}") from exc
        content = (payload.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise LLMError("Model response has no message content.")
        return content


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model reply that may contain fences or prose."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty reply")
    candidates = [text.strip()]
    candidates += [m.strip() for m in _FENCE.findall(text)]
    decoder = json.JSONDecoder()
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        # Scan for the first decodable object starting at any '{'.
        for m in re.finditer(r"\{", cand):
            try:
                obj, _ = decoder.raw_decode(cand, m.start())
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    raise ValueError("no JSON object found in reply")


def chat_json(
    client: LLMClient,
    messages: list[Message],
    validate: Callable[[dict[str, Any]], str | None],
    *,
    label: str,
) -> dict[str, Any]:
    """Call the model and parse a JSON object; retry once on invalid output.

    ``validate`` returns None when the object is acceptable, else a reason.
    Raises LLMError when both attempts fail.
    """
    convo = list(messages)
    last_problem = ""
    for attempt in (1, 2):
        try:
            reply = client.chat(convo)
        except LLMError:
            raise
        except Exception as exc:  # a misbehaving client must not crash the run
            raise LLMError(f"Model client error: {exc}") from exc
        try:
            obj = extract_json(reply)
            problem = validate(obj)
        except ValueError as exc:
            obj, problem = None, str(exc)
        if problem is None and obj is not None:
            return obj
        last_problem = problem or "invalid JSON"
        log.warning("%s: model output rejected on attempt %d: %s", label, attempt, last_problem)
        convo = list(messages) + [
            {"role": "assistant", "content": reply if isinstance(reply, str) else ""},
            {
                "role": "user",
                "content": (
                    f"Your previous reply was not usable ({last_problem}). "
                    "Reply again with ONLY one valid JSON object in exactly the requested format. "
                    "No markdown, no commentary."
                ),
            },
        ]
    raise LLMError(f"{label}: model returned invalid JSON twice ({last_problem}).")
