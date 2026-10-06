"""ClinicalTrials.gov connector (API v2, https://clinicaltrials.gov/api/v2/studies).

Three kinds of query per run:
1. per subject drug: condition = indication + synonyms, intervention = drug name OR synonyms;
2. the same, restricted to stopped trials (TERMINATED / WITHDRAWN / SUSPENDED). Always run, even
   when query 1 failed or hit its page cap, so stopped trials are never missed;
3. landscape: active phase 2-3 trials for the indication, any drug (capped).

Each study becomes one Evidence, deduplicated by NCT id across queries. data.found_via is the query that
found it first (the subject drug's name, or "landscape"); it is NOT a drug link.

Drug link (match_subject_drugs): a trial is linked to the subject drugs whose name or synonym appears in its
interventions (name or otherNames). The title is used only when no intervention is an investigational product
(type DRUG / BIOLOGICAL / GENETIC / COMBINATION_PRODUCT), e.g. a substudy listing only "Immunologic Monitoring":
a trial whose products are all non-subject drugs is not linked because its title mentions a subject drug as
background (e.g. "nivolumab with tisagenlecleucel reinfusion"). data.subject_drug_chembl_ids lists the matches
and entity_refs.drug is always its first element (absent when empty); data.drug_relation is that drug's relation.

Query parameters and response fields were checked against live responses (API 2.0.5):
query.cond, query.intr (Essie syntax: quoted terms joined by OR), filter.overallStatus (comma list),
filter.advanced (AREA[Phase](PHASE2 OR PHASE3)), fields, pageSize, pageToken -> nextPageToken;
protocolSection.{identificationModule.{nctId,briefTitle}, statusModule.{overallStatus,whyStopped,
startDateStruct,completionDateStruct,lastUpdatePostDateStruct}, sponsorCollaboratorsModule.leadSponsor,
conditionsModule.conditions, designModule.{phases,enrollmentInfo}, armsInterventionsModule.interventions
[{type,name,otherNames}], outcomesModule.primaryOutcomes[{measure,timeFrame}]}, hasResults.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, date, datetime
from typing import Any

from researcher.schema import Drug, Evidence, SourceStatus, Subject, evidence_id
from researcher.http import HttpClient

SOURCE = "clinicaltrials"
API_URL = "https://clinicaltrials.gov/api/v2/studies"
STUDY_URL = "https://clinicaltrials.gov/study/{}"

STOPPED = ("TERMINATED", "WITHDRAWN", "SUSPENDED")
ACTIVE = ("RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION")

FIELDS = ",".join([
    "IdentificationModule", "StatusModule", "SponsorCollaboratorsModule", "ConditionsModule",
    "DesignModule", "ArmsInterventionsModule", "OutcomesModule", "HasResults",
])

_PAGE_SIZE = 100
_DRUG_MAX_PAGES = 3  # per drug query: at most 300 studies
_LANDSCAPE_MAX = 100
_MAX_CONDITION_TERMS = 8
_MAX_DRUG_TERMS = 12
_MIN_TERM_LEN = 3
_SNIPPET_WHY_LEN = 200
# Intervention types that name an investigational product (API enum InterventionType).
PRODUCT_TYPES = ("DRUG", "BIOLOGICAL", "GENETIC", "COMBINATION_PRODUCT")


class ClinicalTrialsConnector:
    source = SOURCE

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http
        self.last_status: SourceStatus | None = None

    def fetch(self, subject: Subject) -> list[Evidence]:
        """Return trial evidence for the subject. Never raises; see `last_status`."""
        t0 = time.monotonic()
        run = _Run(subject, self.http or HttpClient())
        try:
            evidence = run.collect()
        except Exception as e:  # never raise into the pipeline
            run.errors.append(f"{type(e).__name__}: {e}"[:300])
            evidence = []
        finally:
            if self.http is None:
                run.http.close()
        self.last_status = SourceStatus(
            source=SOURCE,
            ok=not run.errors,
            records=len(evidence),
            error="; ".join(run.errors) or None,
            duration_s=round(time.monotonic() - t0, 4),
            cached=run.requests > 0 and not run.network,
        )
        return evidence


def fetch(subject: Subject, http: HttpClient | None = None) -> list[Evidence]:
    return ClinicalTrialsConnector(http).fetch(subject)


class _Run:
    def __init__(self, subject: Subject, http: HttpClient) -> None:
        self.subject, self.http = subject, http
        self.errors: list[str] = []
        self.requests = 0
        self.network = False
        self.studies: dict[str, dict[str, Any]] = {}  # NCT id -> study, in discovery order
        self.found_via: dict[str, str] = {}  # NCT id -> drug name of the first query that found it, or "landscape"
        self.matchers = subject_drug_matchers(subject.drugs)

    def collect(self) -> list[Evidence]:
        cond = _or_query(_dedupe([self.subject.indication, *self.subject.disease_synonyms])[:_MAX_CONDITION_TERMS])
        if not cond:
            self.errors.append("no indication to search")
            return []
        for drug, terms in self.matchers:
            intr = _or_query(terms[:_MAX_DRUG_TERMS])
            if not intr:
                continue
            base = {"query.cond": cond, "query.intr": intr}
            self._query(f"drug {drug.name}", base, _DRUG_MAX_PAGES * _PAGE_SIZE, drug.name)
            self._query(
                f"stopped {drug.name}", {**base, "filter.overallStatus": ",".join(STOPPED)},
                _DRUG_MAX_PAGES * _PAGE_SIZE, drug.name,
            )
        self._query(
            "landscape",
            {
                "query.cond": cond,
                "filter.overallStatus": ",".join(ACTIVE),
                "filter.advanced": "AREA[Phase](PHASE2 OR PHASE3)",
            },
            _LANDSCAPE_MAX,
            "landscape",
        )
        now = datetime.now(UTC)
        return [self._to_evidence(s, now) for s in self.studies.values()]

    def _query(self, label: str, params: dict[str, Any], cap: int, found_via: str) -> None:
        """Run one paginated search, adding studies to self.studies. Failures are recorded, not raised."""
        params = {**params, "fields": FIELDS, "pageSize": min(_PAGE_SIZE, cap)}
        seen = 0
        token: str | None = None
        try:
            while seen < cap:
                page = {**params, "pageToken": token} if token else params
                resp = self.http.get(SOURCE, API_URL, page)
                self.requests += 1
                self.network |= not resp.from_cache
                body = resp.json()
                for study in body.get("studies") or []:
                    nct = _get(study, "protocolSection", "identificationModule", "nctId")
                    if nct:
                        self.studies.setdefault(nct, study)
                        self.found_via.setdefault(nct, found_via)
                    seen += 1
                token = body.get("nextPageToken")
                if not token:
                    break
        except Exception as e:
            self.errors.append(f"{label}: {type(e).__name__}: {e}"[:300])

    def _to_evidence(self, study: dict[str, Any], now: datetime) -> Evidence:
        p = study.get("protocolSection") or {}
        ident = p.get("identificationModule") or {}
        status = p.get("statusModule") or {}
        design = p.get("designModule") or {}
        enrollment = design.get("enrollmentInfo") or {}
        nct = ident["nctId"]
        overall = status.get("overallStatus")
        interventions = [
            {k: i[k] for k in ("type", "name", "otherNames") if i.get(k)}
            for i in _get(p, "armsInterventionsModule", "interventions") or []
        ]
        title = ident.get("briefTitle") or ident.get("officialTitle") or nct
        matched = match_subject_drugs(interventions, title, self.matchers)
        last_update = _get(status, "lastUpdatePostDateStruct", "date")
        data = {
            "nct_id": nct,
            "title": ident.get("briefTitle"),
            "overall_status": overall,
            "why_stopped": status.get("whyStopped"),
            "phases": design.get("phases") or [],
            "enrollment": enrollment.get("count"),
            "enrollment_type": enrollment.get("type"),
            "start_date": _get(status, "startDateStruct", "date"),
            "completion_date": _get(status, "completionDateStruct", "date"),
            "last_update_posted": last_update,
            "has_results": bool(study.get("hasResults")),
            "lead_sponsor": _get(p, "sponsorCollaboratorsModule", "leadSponsor", "name"),
            "interventions": interventions,
            "conditions": _get(p, "conditionsModule", "conditions") or [],
            "primary_outcomes": [
                {k: o[k] for k in ("measure", "timeFrame") if o.get(k)}
                for o in _get(p, "outcomesModule", "primaryOutcomes") or []
            ],
            "subject_drug_chembl_ids": [d.chembl_id for d in matched],
            "drug_relation": matched[0].relation if matched else None,
            "found_via": self.found_via.get(nct, "landscape"),
        }
        modules = ["pipeline", "trial-design", "competitive-landscape"]
        # A stop with no stated reason is not a red flag by itself (reconcile.py notes it as "unknown").
        if overall in STOPPED and (status.get("whyStopped") or "").strip():
            modules.append("red-flags")
        refs = {"drug": matched[0].chembl_id} if matched else {}
        return Evidence(
            id=evidence_id(SOURCE, nct),
            source=SOURCE,
            url=STUDY_URL.format(nct),
            retrieved_at=now,
            kind="record",
            modules=modules,
            entity_refs=refs,
            title=title,
            snippet=_snippet(data),
            data=data,
            published_at=_parse_date(last_update),
        )


def subject_drug_matchers(drugs: list[Drug]) -> list[tuple[Drug, list[str]]]:
    return [(d, _drug_terms(d)) for d in drugs if d.chembl_id]


def match_subject_drugs(
    interventions: list[dict[str, Any]], title: str, matchers: list[tuple[Drug, list[str]]]
) -> list[Drug]:
    """Subject drugs named by the interventions (name or otherNames), in intervention order; the title is
    searched only when no intervention is an investigational product (see module docstring)."""
    out: list[Drug] = []
    for iv in interventions:
        names = [_norm(n) for n in [iv.get("name") or "", *(iv.get("otherNames") or [])]]
        for drug, terms in matchers:
            if drug not in out and any(_mentions(n, _norm(t)) for n in names for t in terms):
                out.append(drug)
    if out or any(iv.get("type") in PRODUCT_TYPES for iv in interventions):
        return out
    text = _norm(title or "")
    hits = []
    for drug, terms in matchers:
        positions = [p for p in (_position(text, _norm(t)) for t in terms) if p is not None]
        if positions:
            hits.append((min(positions), drug))
    return [d for _, d in sorted(hits, key=lambda h: h[0])]


def _position(text: str, term: str) -> int | None:
    """Index of the whole-word `term` in `text` (both normalized), None if absent (see _mentions)."""
    if not _mentions(text, term):
        return None
    i = f" {text} ".find(f" {term} ")
    return i if i >= 0 else len(text)  # matched only as a joined code: position unknown, rank last


def _drug_terms(drug: Drug) -> list[str]:
    return [t for t in _dedupe([drug.name, *drug.synonyms]) if len(t.strip()) >= _MIN_TERM_LEN]


def _or_query(terms: list[str]) -> str:
    quoted = [f'"{t.replace(chr(34), " ").strip()}"' for t in terms if t.strip()]
    return " OR ".join(quoted)


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def _mentions(text: str, term: str) -> bool:
    """Whole-word match of `term` in `text` (both normalized). 'ain 457' also matches 'ain457'."""
    if not term:
        return False
    if f" {term} " in f" {text} ":
        return True
    compact = term.replace(" ", "")
    return any(c.isdigit() for c in compact) and compact in _compact_words(text)


def _compact_words(text: str) -> list[str]:
    """Words plus adjacent pairs joined, so the code 'ain457' matches 'ain 457' / 'ain-457'."""
    words = text.split()
    return words + [a + b for a, b in zip(words, words[1:])]


def _snippet(d: dict[str, Any]) -> str:
    parts = [_phase_label(d["phases"])]
    if d["enrollment"] is not None:
        planned = " planned" if d["enrollment_type"] == "ESTIMATED" else ""
        parts.append(f"{d['enrollment']} patients{planned}")
    status = d["overall_status"] or "status unknown"
    if d["why_stopped"]:
        why = d["why_stopped"].strip()
        if len(why) > _SNIPPET_WHY_LEN:
            why = why[: _SNIPPET_WHY_LEN - 1].rstrip() + "…"
        status = f"{status}: {why}"
    parts.append(status)
    text = ", ".join(parts)
    if d["lead_sponsor"]:
        text += f" ({d['lead_sponsor']})"
    return text


def _phase_label(phases: list[str]) -> str:
    nums = [p.removeprefix("PHASE") for p in phases if p.startswith("PHASE")]
    if "EARLY_PHASE1" in phases:
        nums.insert(0, "Early 1")
    if nums:
        return "Phase " + "/".join(nums)
    return "Phase N/A" if phases else "No phase"


def _parse_date(value: str | None) -> date | None:
    """API dates are 'YYYY-MM-DD' or 'YYYY-MM'; a month-only date maps to its first day."""
    if not value:
        return None
    try:
        parts = [int(x) for x in value.split("-")]
        return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except (ValueError, IndexError):
        return None


def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out
