"""Extract a JSON object from model output.

Even with JSON mode, small models sometimes wrap the answer in ```json fences
or add a sentence before/after it. We take the first balanced {...} block.
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)  # reasoning models (qwen3, deepseek-r1) emit this first


class JSONExtractionError(ValueError):
    pass


def extract_json(text: str) -> dict[str, Any]:
    text = _THINK.sub("", text)
    candidates = [text.strip()]
    candidates += [m.strip() for m in _FENCE.findall(text)]
    block = _first_object(text)
    if block:
        candidates.append(block)
    for c in candidates:
        try:
            value = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise JSONExtractionError(f"No JSON object found in model output: {text[:200]!r}")


def _first_object(text: str) -> str | None:
    start = text.find("{")
    while start != -1:
        depth, in_str, escaped = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start: i + 1]
        start = text.find("{", start + 1)
    return None
