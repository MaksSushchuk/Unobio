"""Input contract for the Writer (schema version 1.0) and a defensive parser.

The Writer receives exactly one JSON document per run. The full contract is
documented in docs/INPUT_SCHEMA.md and schemas/writer_input.schema.json. This
module is the only place that knows field names, so if the upstream schema
changes, this is the only module that needs remapping.

Parsing never raises on malformed content: problems become warnings and the
affected item is skipped or left empty.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTING = {"supporting", "supports", "support", "for"}
CONTRADICTING = {"contradicting", "contradicts", "contradict", "refutes", "against"}


class InputError(ValueError):
    """The input cannot be turned into a report at all."""


@dataclass
class Source:
    id: str
    title: str = ""
    url: str = ""
    database: str = ""
    identifier: str = ""
    year: str = ""
    authors: str = ""

    def dedup_key(self) -> str:
        for value in (self.url, self.identifier):
            if value:
                return value.strip().lower().rstrip("/")
        if self.title:
            return " ".join(self.title.lower().split())
        return f"id:{self.id}"


@dataclass
class Evidence:
    source_id: str
    stance: str = "neutral"  # supporting | contradicting | neutral
    excerpt: str = ""


@dataclass
class Claim:
    id: str
    text: str
    confidence: float | None = None  # 0..1
    claim_type: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    related_claim_ids: list[str] = field(default_factory=list)

    @property
    def supporting(self) -> int:
        return sum(1 for e in self.evidence if e.stance == "supporting")

    @property
    def contradicting(self) -> int:
        return sum(1 for e in self.evidence if e.stance == "contradicting")


@dataclass
class Section:
    key: str
    title: str
    status: str = "done"
    claims: list[Claim] = field(default_factory=list)
    follow_up_queries: list[str] = field(default_factory=list)
    is_skeptic: bool = False


@dataclass
class Capital:
    milestone: str = ""
    duration: str = ""
    currency: str = "USD"
    low: float | None = None
    base: float | None = None
    high: float | None = None
    assumptions: list[str] = field(default_factory=list)


@dataclass
class Thesis:
    indication: str
    mechanism: str
    modality: str = ""
    stage: str = ""
    biomarkers: list[str] = field(default_factory=list)
    route: str = ""


@dataclass
class RunInfo:
    id: str = ""
    timestamp: str = ""
    simulated: bool = False
    fixture_data: bool = False

    @property
    def is_mock(self) -> bool:
        return self.simulated or self.fixture_data


@dataclass
class WriterInput:
    run: RunInfo
    thesis: Thesis
    verdict: str | None
    confidence: float | None
    capital: Capital | None
    sources: dict[str, Source]
    analysts: list[Section]
    skeptic: Section | None
    panels: dict[str, Any]
    raw: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def sections(self) -> list[Section]:
        return self.analysts + ([self.skeptic] if self.skeptic else [])

    def all_claims(self) -> list[Claim]:
        return [c for s in self.sections for c in s.claims]


# --------------------------------------------------------------------------- helpers


def _str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", ""))
        except ValueError:
            return None
    return None


def normalise_confidence(value: Any) -> float | None:
    """Confidence is 0..1 in the contract; values in (1, 100] are read as percent."""
    num = _num(value)
    if num is None or num < 0:
        return None
    if num > 1:
        num = num / 100.0 if num <= 100 else None
    return num


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, list):
        return [s for s in (_str(v) for v in value) if s]
    return []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stance(value: Any) -> str:
    s = _str(value).lower()
    if s in SUPPORTING:
        return "supporting"
    if s in CONTRADICTING:
        return "contradicting"
    return "neutral"


# --------------------------------------------------------------------------- parsing


def _parse_claims(raw_claims: Any, where: str, sources: dict[str, Source], warnings: list[str]) -> list[Claim]:
    claims: list[Claim] = []
    if not isinstance(raw_claims, list):
        return claims
    for i, rc in enumerate(raw_claims):
        if not isinstance(rc, dict):
            warnings.append(f"{where}: claim #{i + 1} is not an object and was skipped.")
            continue
        text = _str(rc.get("text"))
        if not text:
            warnings.append(f"{where}: claim #{i + 1} has no text and was skipped.")
            continue
        evidence: list[Evidence] = []
        for re_ in rc.get("evidence") or []:
            if not isinstance(re_, dict):
                continue
            sid = _str(re_.get("source_id"))
            if not sid and isinstance(re_.get("source"), dict):
                # Inline source object: register it under a synthetic id.
                src = _parse_source(re_["source"], "")
                if not src.id:
                    src.id = f"inline:{src.dedup_key()}"
                sid = src.id
                sources.setdefault(sid, src)
            if sid and sid not in sources:
                warnings.append(f"{where}: claim '{_str(rc.get('id')) or i + 1}' cites unknown source '{sid}'.")
            evidence.append(Evidence(source_id=sid, stance=_stance(re_.get("stance")), excerpt=_str(re_.get("excerpt"))))
        conf_raw = rc.get("confidence")
        conf = normalise_confidence(conf_raw)
        if conf_raw is not None and conf is None:
            warnings.append(f"{where}: claim '{_str(rc.get('id')) or i + 1}' has an unreadable confidence value.")
        claims.append(
            Claim(
                id=_str(rc.get("id")) or f"{where.lower().replace(' ', '_')}_{i + 1}",
                text=text,
                confidence=conf,
                claim_type=_str(rc.get("claim_type")),
                evidence=evidence,
                related_claim_ids=_str_list(rc.get("related_claim_ids")),
            )
        )
    return claims


def _parse_source(rs: dict[str, Any], fallback_id: str) -> Source:
    return Source(
        id=_str(rs.get("id")) or fallback_id,
        title=_str(rs.get("title")),
        url=_str(rs.get("url")),
        database=_str(rs.get("database")),
        identifier=_str(rs.get("identifier")),
        year=_str(rs.get("year")),
        authors=_str(rs.get("authors")),
    )


def _parse_section(rs: dict[str, Any], default_key: str, sources: dict[str, Source], warnings: list[str], *, skeptic: bool) -> Section:
    key = _str(rs.get("key")) or default_key
    title = _str(rs.get("title")) or key.replace("_", " ").replace("-", " ").title()
    section = Section(
        key=key,
        title=title,
        status=_str(rs.get("status")).lower() or "done",
        follow_up_queries=_str_list(rs.get("follow_up_queries")) if isinstance(rs.get("follow_up_queries"), list) else [],
        is_skeptic=skeptic,
    )
    section.claims = _parse_claims(rs.get("claims"), title, sources, warnings)
    return section


def parse_input(data: dict[str, Any] | str | Path) -> WriterInput:
    """Parse a Writer input document. Raises InputError only when no report is possible."""
    if isinstance(data, (str, Path)):
        try:
            data = json.loads(Path(data).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InputError(f"Cannot read input JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InputError("Input must be a JSON object.")

    warnings: list[str] = []

    rt = _dict(data.get("thesis"))
    thesis = Thesis(
        indication=_str(rt.get("indication")),
        mechanism=_str(rt.get("mechanism")),
        modality=_str(rt.get("modality")),
        stage=_str(rt.get("stage")),
        biomarkers=_str_list(rt.get("biomarkers")),
        route=_str(rt.get("route")),
    )
    if not thesis.indication or not thesis.mechanism:
        raise InputError("thesis.indication and thesis.mechanism are required.")

    rr = _dict(data.get("run"))
    run = RunInfo(
        id=_str(rr.get("id")),
        timestamp=_str(rr.get("timestamp")),
        simulated=bool(rr.get("simulated", False)),
        fixture_data=bool(rr.get("fixture_data", False)),
    )

    sources: dict[str, Source] = {}
    for i, rs in enumerate(data.get("sources") or []):
        if isinstance(rs, dict):
            src = _parse_source(rs, f"src-{i + 1}")
            if src.id in sources:
                warnings.append(f"Duplicate source id '{src.id}'; the first entry was kept.")
                continue
            sources[src.id] = src

    rec = _dict(data.get("recommendation"))
    verdict = _str(rec.get("verdict")) or None
    confidence = normalise_confidence(rec.get("confidence"))

    capital = None
    rc = data.get("capital_to_milestone")
    if isinstance(rc, dict):
        capital = Capital(
            milestone=_str(rc.get("milestone")),
            duration=_str(rc.get("duration")),
            currency=_str(rc.get("currency")) or "USD",
            low=_num(rc.get("low")),
            base=_num(rc.get("base")),
            high=_num(rc.get("high")),
            assumptions=_str_list(rc.get("assumptions")) if isinstance(rc.get("assumptions"), list) else [],
        )

    analysts: list[Section] = []
    raw_analysts = data.get("analysts")
    if isinstance(raw_analysts, dict):  # tolerate {"biology": {...}} form
        raw_analysts = [{"key": k, **v} for k, v in raw_analysts.items() if isinstance(v, dict)]
    for i, ra in enumerate(raw_analysts or []):
        if isinstance(ra, dict):
            analysts.append(_parse_section(ra, f"analyst_{i + 1}", sources, warnings, skeptic=False))

    skeptic = None
    if isinstance(data.get("skeptic"), dict):
        skeptic = _parse_section({"title": "Skeptic review", **data["skeptic"]}, "skeptic", sources, warnings, skeptic=True)

    panels = _dict(data.get("panels"))

    return WriterInput(
        run=run,
        thesis=thesis,
        verdict=verdict,
        confidence=confidence,
        capital=capital,
        sources=sources,
        analysts=analysts,
        skeptic=skeptic,
        panels=panels,
        raw=data,
        warnings=warnings,
    )
