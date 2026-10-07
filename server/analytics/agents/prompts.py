"""Prompt files.

Prompts live as Markdown files in `analytics/prompts/`, not inside Python code:
they are easy to read, review and tune without touching logic.

Placeholders use `$name` / `${name}` (string.Template), so JSON examples with
curly braces inside prompts need no escaping. A missing variable is an error.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Load `analytics/prompts/<name>.md`."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def render(template: str, **variables: object) -> str:
    """Fill `$placeholders`; raises KeyError if one is missing."""
    return Template(template).substitute({k: str(v) for k, v in variables.items()})
