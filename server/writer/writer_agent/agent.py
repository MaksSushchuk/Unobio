"""Writer agent: turns the underwriting JSON into a PDF report.

Public entry point: ``WriterAgent.run``. It never raises; failures come back as
``WriterResult(status="error", message=...)`` and partial failures as warnings.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import WriterConfig
from .llm import LLMClient, LLMError, OllamaClient, chat_json
from .numbers import allowed_values, unverified_numbers
from .prompts import Budget, final_messages, section_messages
from .schema import InputError, Section, WriterInput, parse_input

log = logging.getLogger(__name__)


@dataclass
class WriterResult:
    status: str  # "ok" | "degraded" | "error"
    pdf_path: str | None = None
    page_count: int = 0
    recommendation: str | None = None
    confidence: float | None = None  # 0..1, copied from input
    sections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Narrative:
    takeaway: str = ""
    synthesis: str = ""


@dataclass
class ListItem:
    text: str
    claim_ids: list[str] = field(default_factory=list)


@dataclass
class Report:
    """Everything the PDF renderer needs, already resolved and checked."""

    input: WriterInput
    verdict: str | None
    verdict_source: str  # "upstream" | "model" | "none"
    confidence: float | None
    rationale: str
    narratives: dict[str, Narrative | None]  # section key -> narrative (None = model failed)
    risks: list[ListItem]
    unknowns: list[ListItem]
    questions: list[ListItem]
    final_ok: bool
    warnings: list[str]


# ---------------------------------------------------------------- validators


def _validate_section(obj: dict[str, Any]) -> str | None:
    if not isinstance(obj.get("takeaway"), str) or not obj["takeaway"].strip():
        return "missing 'takeaway' string"
    if not isinstance(obj.get("synthesis", ""), str):
        return "'synthesis' must be a string"
    return None


def _make_final_validator(need_verdict: bool, allowed: tuple[str, ...]):
    def validate(obj: dict[str, Any]) -> str | None:
        if not isinstance(obj.get("rationale"), str) or not obj["rationale"].strip():
            return "missing 'rationale' string"
        for key in ("risks", "critical_unknowns", "diligence_questions"):
            if key in obj and not isinstance(obj[key], list):
                return f"'{key}' must be a list"
        if need_verdict:
            v = obj.get("verdict")
            if not isinstance(v, str) or v.strip().lower() not in {a.lower() for a in allowed}:
                return f"'verdict' must be one of {list(allowed)}"
        return None

    return validate


def _items(raw: Any, known_ids: set[str], label: str, warnings: list[str]) -> list[ListItem]:
    out: list[ListItem] = []
    for r in raw or []:
        if isinstance(r, str) and r.strip():
            out.append(ListItem(r.strip()))
        elif isinstance(r, dict) and isinstance(r.get("text"), str) and r["text"].strip():
            ids = [str(i) for i in (r.get("claim_ids") or []) if isinstance(i, (str, int))]
            unknown = [i for i in ids if i not in known_ids]
            if unknown:
                warnings.append(f"{label}: model cited unknown claim id(s) {unknown}; they were dropped.")
            out.append(ListItem(r["text"].strip(), [i for i in ids if i in known_ids]))
    return out


# ---------------------------------------------------------------- agent


class WriterAgent:
    name = "writer"

    def __init__(self, config: WriterConfig | None = None, client: LLMClient | None = None):
        self.config = config or WriterConfig()
        self.client = client or OllamaClient(self.config)

    # The orchestrator-facing call.
    def run(self, input_data: dict[str, Any] | str | Path, output_path: str | Path) -> WriterResult:
        try:
            return self._run(input_data, Path(output_path))
        except Exception as exc:  # never raise to the caller
            log.exception("Writer failed")
            return WriterResult(status="error", message=f"{type(exc).__name__}: {exc}")

    def _run(self, input_data, output_path: Path) -> WriterResult:
        try:
            wi = parse_input(input_data)
        except InputError as exc:
            return WriterResult(status="error", message=str(exc))

        report = self.build_report(wi)

        from .render_pdf import render_pdf  # imported lazily: reportlab is heavy

        output_path.parent.mkdir(parents=True, exist_ok=True)
        page_count = render_pdf(report, output_path, self.config)

        degraded = not report.final_ok or any(n is None for n in report.narratives.values())
        return WriterResult(
            status="degraded" if degraded else "ok",
            pdf_path=str(output_path),
            page_count=page_count,
            recommendation=report.verdict,
            confidence=report.confidence,
            sections=[s.title for s in wi.sections],
            warnings=report.warnings,
        )

    def build_report(self, wi: WriterInput) -> Report:
        cfg = self.config
        warnings = list(wi.warnings)
        budget = Budget(cfg.max_prompt_chars)

        # --- missing / empty analysts
        present = {s.key: s for s in wi.analysts}
        for key in cfg.expected_analysts:
            if key not in present:
                warnings.append(f"Analyst '{key}' is missing from the input; the report was built without it.")
        for s in wi.analysts:
            if not s.claims:
                warnings.append(f"Analyst '{s.title}' sent no claims (status: {s.status}).")
        if wi.skeptic is None:
            warnings.append("No Skeptic review in the input.")
        if not wi.all_claims():
            warnings.append("The input contains no claims at all; the report has no evidence base.")

        # --- upstream verdict
        allowed_lower = {v.lower(): v for v in cfg.allowed_verdicts}
        if wi.verdict and wi.verdict.lower() not in allowed_lower:
            warnings.append(f"Upstream verdict '{wi.verdict}' is not one of {list(cfg.allowed_verdicts)}; shown as received.")
        if wi.confidence is None:
            warnings.append("No upstream recommendation confidence in the input; confidence is not shown.")

        derived = [n for c in wi.all_claims() for n in (c.supporting, c.contradicting)]
        derived += [len(s.claims) for s in wi.sections]
        allowed = allowed_values(wi.raw, extra=derived)

        def check(label: str, text: str) -> None:
            bad = unverified_numbers(text, allowed)
            if bad:
                warnings.append(f"{label}: number(s) {bad} in model-written text do not occur in the input.")

        # --- per-section narrative
        narratives: dict[str, Narrative | None] = {}
        for section in wi.sections:
            narratives[section.key] = self._section_narrative(section, wi, budget, warnings, check)

        # --- final synthesis
        known_ids = {c.id for c in wi.all_claims()}
        verdict, verdict_source = (wi.verdict, "upstream") if wi.verdict else (None, "none")
        rationale, risks, unknowns, questions, final_ok = "", [], [], [], False
        messages, note = final_messages(wi, budget, cfg.allowed_verdicts)
        if note:
            warnings.append(f"Final synthesis: input truncated for the model — {note}. Claims in the PDF are complete.")
        try:
            obj = chat_json(self.client, messages, _make_final_validator(wi.verdict is None, cfg.allowed_verdicts), label="Final synthesis")
            rationale = obj["rationale"].strip()
            check("Recommendation rationale", rationale)
            risks = _items(obj.get("risks"), known_ids, "Risks", warnings)
            unknowns = _items(obj.get("critical_unknowns"), known_ids, "Critical unknowns", warnings)
            questions = _items(obj.get("diligence_questions"), known_ids, "Diligence questions", warnings)
            for label, items in (("Risks", risks), ("Critical unknowns", unknowns), ("Diligence questions", questions)):
                for it in items:
                    check(label, it.text)
            if wi.verdict is None:
                verdict, verdict_source = allowed_lower[obj["verdict"].strip().lower()], "model"
            final_ok = True
        except LLMError as exc:
            warnings.append(f"Final synthesis failed ({exc}); rationale, risks, unknowns and diligence questions are missing.")

        if verdict is None:
            warnings.append("No verdict available: none supplied upstream and the model did not produce one.")

        return Report(
            input=wi,
            verdict=verdict,
            verdict_source=verdict_source,
            confidence=wi.confidence,
            rationale=rationale,
            narratives=narratives,
            risks=risks,
            unknowns=unknowns,
            questions=questions,
            final_ok=final_ok,
            warnings=warnings,
        )

    def _section_narrative(self, section: Section, wi: WriterInput, budget: Budget, warnings: list[str], check) -> Narrative | None:
        if not section.claims:
            return Narrative()  # nothing to summarise; renderer notes the absence
        messages, note = section_messages(section, wi, budget)
        if note:
            warnings.append(f"{section.title}: input truncated for the model — {note}. Claims in the PDF are complete.")
        try:
            obj = chat_json(self.client, messages, _validate_section, label=section.title)
        except LLMError as exc:
            warnings.append(f"{section.title}: narrative unavailable ({exc}); claims are printed without it.")
            return None
        n = Narrative(takeaway=obj["takeaway"].strip(), synthesis=(obj.get("synthesis") or "").strip())
        check(f"{section.title} takeaway", n.takeaway)
        check(f"{section.title} synthesis", n.synthesis)
        return n
