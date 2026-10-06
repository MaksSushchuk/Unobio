from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "il17_crohns.json"


class StubClient:
    """Stands in for the model. Routes by prompt type; records every call.

    ``section`` / ``final`` may be a dict (returned as JSON), a str (returned
    raw), or a list of either (consumed one per call, last one repeats).
    ``section_overrides`` maps a section title to its own responses.
    """

    def __init__(self, section=None, final=None, section_overrides=None):
        self.section = section if section is not None else {
            "takeaway": "The evidence in this section is summarised here without new facts.",
            "synthesis": "Claims are weighed against each other; contradicting evidence is noted.",
        }
        self.final = final if final is not None else {
            "rationale": "Direct randomized evidence contradicts the thesis and outweighs genetic support; confidence is 84% as supplied.",
            "risks": [{"text": "Class labels warn about IBD exacerbation.", "claim_ids": ["saf-1"]}],
            "critical_unknowns": [{"text": "Whether any IL-17 approach spares the epithelial barrier.", "claim_ids": ["bio-4", "nope-9"]}],
            "diligence_questions": ["What data show the asset avoids barrier disruption?"],
        }
        self.section_overrides = section_overrides or {}
        self.calls: list[list[dict]] = []
        self._counters: dict[str, int] = {}

    def _next(self, key, spec):
        if isinstance(spec, list):
            i = self._counters.get(key, 0)
            self._counters[key] = i + 1
            spec = spec[min(i, len(spec) - 1)]
        if isinstance(spec, Exception):
            raise spec
        return spec if isinstance(spec, str) else json.dumps(spec)

    def chat(self, messages):
        self.calls.append(messages)
        user = messages[1]["content"]
        if '"rationale"' in user:
            return self._next("final", self.final)
        m = re.search(r'"section": \{\s*"title": "([^"]+)"', user)
        title = m.group(1) if m else ""
        if title in self.section_overrides:
            return self._next(f"sec:{title}", self.section_overrides[title])
        return self._next("section", self.section)


@pytest.fixture
def fixture_data():
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8")))


@pytest.fixture
def stub():
    return StubClient()


def pdf_text(path) -> str:
    from pypdf import PdfReader

    text = " ".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return " ".join(text.split())


def norm(s: str) -> str:
    return " ".join(s.split())
