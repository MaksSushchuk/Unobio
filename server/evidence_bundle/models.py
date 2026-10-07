"""Contract: researcher -> analytics.

ONE file per run: `evidence_bundle.json`. Researcher WRITES it, analytics READS it.
This module is the single source of truth for its shape (JSON Schema is generated
from it into `data/schema/evidence_bundle.schema.json`).

Design rules
* Evidence = one atomic, citable fact with a stable `id`, a source URL and a date.
* Researcher does NOT judge (no scores, no verdict). It only collects, tags and
  reconciles. Judging is the job of analytics.
* Cross-source contradictions are evidence too: `kind="conflict"` with
  `related_evidence_ids` and `data.rule / data.severity / data.kill_signal`.
* Unknown extra fields are allowed (`extra="allow"`): researcher may add fields
  without breaking analytics; analytics must only rely on fields defined here.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

# 11 Unobio modules (Unobio_Data_Sources_Directions.xlsx). Researcher tags each
# evidence with the modules it is relevant for; analytics routes by these tags.
Module = Literal[
    "unmet-need",
    "market-sentiment",
    "scientific-evidence",
    "patient-population",
    "competitive-landscape",
    "pipeline",
    "patent-ip",
    "trial-design",
    "manufacturing",
    "red-flags",
    "green-flags",
]
EvidenceKind = Literal["record", "literature", "web", "conflict"]
Severity = Literal["high", "medium", "low", "info"]
Requires = Literal["proprietary_data", "patient_data", "kol", "experiment", "cmc_ip"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


# ----------------------------------------------------------------------------- input


class ResearchInput(_Model):
    indication: str = Field(description="Disease as typed by the user, e.g. \"Crohn's disease\"")
    mechanism: str = Field(description="Mechanism / target as typed, e.g. 'IL-17 inhibition'")
    modality: str | None = None
    stage: str | None = None
    biomarkers: list[str] | None = None
    route: str | None = None
    evidence_cutoff: date | None = Field(
        default=None, description="If set, the bundle contains only facts public on/before this date (demo)."
    )


# ----------------------------------------------------------------------------- subject


class DiseaseRef(_Model):
    id: str = Field(description="MONDO_/EFO_/Orphanet_ id")
    name: str
    synonyms: list[str] = []


class TargetRef(_Model):
    ensembl_id: str
    symbol: str = Field(description="HGNC symbol, e.g. IL17A")
    name: str = ""
    role: Literal["primary", "related"] = "primary"


class DrugRef(_Model):
    chembl_id: str | None = None
    name: str
    synonyms: list[str] = Field(default=[], description="Code names, brands: AIN457, Cosentyx ...")
    target_symbols: list[str] = []
    match: Literal["target", "family"] = Field(
        default="target", description="target = acts on a resolved target; family = same gene family (IL17RA for IL17A)"
    )
    max_stage: str | None = Field(default=None, description="Highest stage today (hidden under cutoff)")


class Subject(_Model):
    disease: DiseaseRef
    targets: list[TargetRef]
    action: str | None = Field(default=None, description="inhibitor / agonist / antagonist / degrader ...")
    drugs: list[DrugRef] = []
    notes: list[str] = Field(default=[], description="Resolution ambiguities")


# ----------------------------------------------------------------------------- evidence


class Evidence(_Model):
    id: str = Field(description="Stable id (hash of source + native id). Cited by claims.")
    source: str = Field(description="opentargets | clinicaltrials | pubmed | openfda | chembl | web | reconciliation")
    url: str
    retrieved_at: datetime
    title: str
    snippet: str = Field(description="Short human/LLM-readable text, <= ~900 chars")
    kind: EvidenceKind
    modules: list[Module]
    published_at: date | None = Field(default=None, description="When the fact became public")
    related_evidence_ids: list[str] = Field(default=[], description="For kind=conflict: the evidence it reconciles")
    entity_refs: dict[str, Any] = Field(default={}, description="nct_id, pmid, drug, target ...")
    data: dict[str, Any] = Field(
        default={},
        description=(
            "Structured fields. Conventions: CT.gov -> status, why_stopped, stop_class, phases, enrollment; "
            "conflict -> rule, severity, kill_signal, drug; benchmark -> benchmark=true"
        ),
    )


class Gap(_Model):
    question: str
    requires: Requires
    reason: str


class SourceStatus(_Model):
    source: str
    ok: bool
    evidence_count: int = 0
    errors: list[str] = []


# ----------------------------------------------------------------------------- bundle


class EvidenceBundle(_Model):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    created_at: datetime
    is_fixture: bool = Field(default=False, description="True for hand-made test bundles (not real research output)")
    input: ResearchInput
    subject: Subject
    evidence: list[Evidence]
    trial_benchmarks: dict[str, Any] = Field(
        default={}, description="Ph2/Ph3 base rates in the indication: enrollment, duration, termination rate"
    )
    gaps: list[Gap] = Field(default=[], description="What public sources cannot answer")
    cutoff_policy: list[str] = []
    source_status: list[SourceStatus] = []

    # ---- helpers for readers (not part of JSON)

    def by_id(self) -> dict[str, Evidence]:
        return {e.id: e for e in self.evidence}

    def conflicts(self) -> list[Evidence]:
        return [e for e in self.evidence if e.kind == "conflict"]
