"""Evidence bundle schema: the contract between the researcher and downstream analysts."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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

Action = Literal["inhibition", "activation", "modulation", "degradation", "other"]

# Where a Drug in Subject came from: suggested by the planner and verified, found by the databases
# for the verified targets, or both.
DrugOrigin = Literal["plan", "database", "both"]

# Therapeutic modality of the subject mechanism (SearchPlan.modality).
Modality = Literal[
    "small_molecule", "antibody", "bispecific", "adc", "cell_therapy", "gene_therapy", "oligonucleotide",
    "other", "unspecified",
]

# How a subject drug relates to the subject mechanism (rule in resolve.py, documented in CLAUDE.md).
DrugRelation = Literal["subject_mechanism", "same_target_other_modality"]


def evidence_id(source: str, native_id: str) -> str:
    """Deterministic evidence id: sha1("<source>:<native_id>")[:16]."""
    return hashlib.sha1(f"{source}:{native_id}".encode()).hexdigest()[:16]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResearchInput(_Model):
    indication: str
    mechanism: str
    modality: str | None = None
    stage: str | None = None
    biomarkers: list[str] | None = None
    route: str | None = None
    evidence_cutoff: date | None = None
    target_symbols: list[str] | None = None


class Drug(_Model):
    chembl_id: str | None = None
    # Lowercase, whitespace-normalized; the INN when one exists (codes go to synonyms).
    # Compare drug names case-insensitively.
    name: str
    synonyms: list[str] = Field(default_factory=list)
    max_phase: float | None = None
    origin: DrugOrigin
    # Ensembl ids of the drug's mechanism targets that are in Subject.target_ids.
    target_ids: list[str] = Field(default_factory=list)
    # Approved symbols of the drug's other mechanism targets, outside the subject mechanism
    # (multi-specific drugs, e.g. a bispecific that also binds TNF).
    other_target_symbols: list[str] = Field(default_factory=list)
    # Open Targets drugType / ChEMBL molecule_type as reported ("Antibody", "Cell", "Gene", ...); None if unknown.
    drug_type: str | None = None
    relation: DrugRelation = "subject_mechanism"


class Subject(_Model):
    indication: str
    mechanism: str
    disease_ids: list[str] = Field(default_factory=list)
    disease_synonyms: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    target_symbols: list[str] = Field(default_factory=list)
    drugs: list[Drug] = Field(default_factory=list)
    # What resolve.py dropped, renamed, added or could not verify, in plain language.
    resolution_notes: list[str] = Field(default_factory=list)


class Evidence(_Model):
    id: str = Field(pattern=r"^[0-9a-f]{16}$")
    source: str
    url: str
    retrieved_at: datetime
    kind: EvidenceKind
    modules: list[Module]
    entity_refs: dict[str, str] = Field(default_factory=dict)
    title: str
    snippet: str
    data: dict[str, Any] = Field(default_factory=dict)
    related_evidence_ids: list[str] = Field(default_factory=list)
    published_at: date | None = None


class SourceStatus(_Model):
    source: str
    ok: bool
    records: int
    error: str | None = None
    # Wall-clock seconds actually spent on this source in this run (near zero when cached).
    duration_s: float
    # True when every response for this source came from the local HTTP cache (no network calls).
    cached: bool = False


# What a non-public diligence question needs (same values as the frontend's DiligenceRequirement).
DiligenceRequirement = Literal["proprietary_data", "patient_data", "kol", "experiment", "cmc_ip"]

# covered: the bundle answers the question (the answer is in `note`, and may be "no");
# missing: answerable from public data but not by this bundle; not_public: needs `requires`.
CoverageStatus = Literal["covered", "missing", "not_public"]

# The answer to the question, separate from status: a covered question can be answered "no", and a
# missing one can still be known to be "no" (e.g. a stopped trial with no stated reason). null for not_public.
CoverageAnswer = Literal["yes", "no", "unknown"]


class CoverageItem(_Model):
    id: str
    question: str
    module: Module
    status: CoverageStatus
    answer: CoverageAnswer | None = None
    public_answerable: bool
    requires: list[DiligenceRequirement] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class SearchPlan(_Model):
    """Planner output. A suggestion only: targets and drugs must be verified in resolve.py."""

    target_symbols: list[str] = Field(default_factory=list)
    action: Action | None = None
    modality: Modality = "unspecified"
    disease_terms: list[str] = Field(default_factory=list)
    drug_names: list[str] = Field(default_factory=list)
    extra_literature_terms: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LlmCall(_Model):
    provider: str
    model: str
    purpose: str | None = None
    started_at: datetime
    latency_s: float
    attempts: int
    ok: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    error: str | None = None


DropReason = Literal["cutoff", "leak_guard"]


class DroppedEvidence(_Model):
    id: str
    source: str
    title: str
    reason: DropReason
    detail: str


class ReconcileStats(_Model):
    total_before: int
    duplicates_merged: int
    drugs_linked: int
    # ClinicalTrials.gov drug links removed because no subject drug is named by the trial's interventions/title.
    drugs_unlinked: int = 0
    dropped_by_cutoff: int
    dropped_by_leak_guard: int
    # One entry per dropped evidence item, with the reason in plain language.
    dropped: list[DroppedEvidence] = Field(default_factory=list)
    # Derived evidence created per rule (reconcile.py part 2), and rules skipped with the reason.
    derived_by_rule: dict[str, int] = Field(default_factory=dict)
    rules_skipped: dict[str, str] = Field(default_factory=dict)


class Bundle(_Model):
    run_id: str
    created_at: datetime
    is_example: bool = False
    input: ResearchInput
    subject: Subject
    plan: SearchPlan | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    coverage: list[CoverageItem] = Field(default_factory=list)
    sources_status: list[SourceStatus] = Field(default_factory=list)
    llm_calls: list[LlmCall] = Field(default_factory=list)
    reconcile_stats: ReconcileStats | None = None
