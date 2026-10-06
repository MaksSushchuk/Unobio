"""Writer configuration.

All values can be overridden through environment variables so the agent can be
pointed at a different model host without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

# Verdicts the Writer accepts. An upstream verdict outside this list is still
# copied verbatim (it is upstream data), but a warning is raised. A verdict
# written by the model must be one of these, otherwise it is discarded.
DEFAULT_VERDICTS = ("Invest", "Watch", "Do not invest")

# Analysts the report expects. A missing one produces a warning, never a failure.
DEFAULT_EXPECTED_ANALYSTS = ("biology", "clinical", "safety", "commercial")

# Titles for the 11 data panels, in report order.
PANEL_TITLES = {
    "unmet-need": "Unmet need, standard of care & target product profile",
    "market-sentiment": "Market sentiment & expert intelligence",
    "scientific-evidence": "Scientific evidence: efficacy, safety & differentiation",
    "patient-population": "Patient population, treatment dynamics & TAM",
    "competitive-landscape": "Existing competitive landscape",
    "pipeline": "Pipeline & drugs in development",
    "patent-ip": "Patent & IP review",
    "trial-design": "Clinical trial design intelligence",
    "manufacturing": "Manufacturing, supply chain & cost of goods",
    "red-flags": "Red flag monitor",
    "green-flags": "Green flag & regulatory advantage monitor",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class WriterConfig:
    # Model (Ollama-compatible HTTP API).
    llm_url: str = field(default_factory=lambda: os.environ.get("WRITER_LLM_URL", "http://10.0.1.24:11434"))
    llm_model: str = field(default_factory=lambda: os.environ.get("WRITER_LLM_MODEL", "llama3.1:8b"))
    llm_timeout_s: float = field(default_factory=lambda: _env_float("WRITER_LLM_TIMEOUT", 180.0))
    llm_num_ctx: int = field(default_factory=lambda: _env_int("WRITER_LLM_NUM_CTX", 8192))
    llm_temperature: float = field(default_factory=lambda: _env_float("WRITER_LLM_TEMPERATURE", 0.0))
    # Upper bound on the JSON payload sent to the model in one call, in
    # characters. Roughly 4 chars per token; keep well below num_ctx.
    max_prompt_chars: int = field(default_factory=lambda: _env_int("WRITER_MAX_PROMPT_CHARS", 20000))

    allowed_verdicts: tuple[str, ...] = DEFAULT_VERDICTS
    expected_analysts: tuple[str, ...] = DEFAULT_EXPECTED_ANALYSTS
    language: str = "en"

    font_regular: Path = PACKAGE_DIR / "fonts" / "DejaVuSans.ttf"
    font_bold: Path = PACKAGE_DIR / "fonts" / "DejaVuSans-Bold.ttf"
