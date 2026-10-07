import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from analytics.llm import LLMSettings
from evidence_bundle import paths
from orchestrator import Report, run
from orchestrator.bundle_adapter import to_evidence_bundle
from orchestrator.pipeline import REPORT_FILE
from researcher.schema import Bundle, CoverageItem, Drug, Evidence, ResearchInput, SearchPlan, Subject, evidence_id

WEB_FIXTURES = Path(__file__).resolve().parents[2] / "web" / "src" / "fixtures"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
FAKE = LLMSettings(provider="fake", model="fake-model", cache_enabled=False)

A = Drug(chembl_id="CHEMBL_A", name="drug-a", max_phase=4, origin="plan", target_ids=["ENSG_X"], drug_type="Antibody")
B = Drug(chembl_id="CHEMBL_B", name="drug-b", max_phase=2, origin="plan", target_ids=["ENSG_X"], drug_type="Antibody")
C = Drug(chembl_id="CHEMBL_C", name="drug-c", max_phase=4, origin="database", target_ids=["ENSG_X"],
         drug_type="Antibody drug conjugate", relation="same_target_other_modality")


def _ev(source, native, kind="record", modules=("pipeline",), data=None, refs=None, related=(), published=date(2020, 1, 1)):
    return Evidence(id=evidence_id(source, native), source=source, url=f"https://x/{native}", retrieved_at=NOW, kind=kind,
                    modules=list(modules), entity_refs=refs or {}, title=f"title {native}", snippet=f"snippet {native}",
                    data=data or {}, related_evidence_ids=list(related), published_at=published)


def _trial(nct, status, drug=None, why=None, category=None, phases=("PHASE2",), enrollment=100):
    rel = {"CHEMBL_A": "subject_mechanism", "CHEMBL_B": "subject_mechanism", "CHEMBL_C": "same_target_other_modality"}
    return _ev("clinicaltrials", nct, modules=["pipeline", "trial-design"], refs={"drug": drug} if drug else {},
               data={"nct_id": nct, "overall_status": status, "why_stopped": why, "stop_category": category,
                     "phases": list(phases), "enrollment": enrollment, "enrollment_type": "ACTUAL",
                     "start_date": "2015-01", "completion_date": "2017-01-15",
                     "subject_drug_chembl_ids": [drug] if drug else [], "drug_relation": rel.get(drug)})


def _rule(rule, related, **data):
    return _ev("reconcile", f"{rule}:{','.join(related)}", modules=["red-flags", "pipeline"],
               related=related, data={"rule": rule, **data})


def researcher_bundle(run_id="r1", other_modality_failure=False) -> Bundle:
    t1 = _trial("NCT00000001", "TERMINATED", "CHEMBL_A", "lack of efficacy", "efficacy")
    t2 = _trial("NCT00000002", "TERMINATED", "CHEMBL_B", "safety", "safety")
    t3 = _trial("NCT00000003", "COMPLETED", "CHEMBL_C", phases=("PHASE3",), enrollment=400)
    t4 = _trial("NCT00000004", "WITHDRAWN", "CHEMBL_A")  # no reason: stop_category unknown -> stop_class None
    failing = ["CHEMBL_A", "CHEMBL_C"] if other_modality_failure else ["CHEMBL_A", "CHEMBL_B"]
    evidence = [
        t1, t2, t3, t4,
        _ev("opentargets", "assoc", modules=["scientific-evidence"], published=None, data={"score": 0.3}),
        _rule("stop_reason", [t1.id], nct_id="NCT00000001", stop_category="efficacy", drug_chembl_ids=["CHEMBL_A"]),
        _rule("target_failure", [t1.id, t2.id], severity="high",
              drugs=[{"chembl_id": c, "name": c} for c in failing]),
        _rule("does_not_transfer", [t3.id], chembl_id="CHEMBL_C"),
    ]
    inp = ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition", modality="antibody")
    return Bundle(
        run_id=run_id, created_at=NOW, input=inp,
        subject=Subject(indication=inp.indication, mechanism=inp.mechanism, disease_ids=["MONDO_0005011"],
                        disease_synonyms=["Crohn disease"], target_ids=["ENSG_X"], target_symbols=["IL17A"],
                        drugs=[A, B, C]),
        plan=SearchPlan(target_symbols=["IL17A"], action="inhibition", modality="antibody"),
        evidence=evidence,
        coverage=[CoverageItem(id="cmc", question="Is it manufacturable?", module="manufacturing", status="not_public",
                               public_answerable=False, requires=["cmc_ip"], note="requires cmc_ip")],
    )


# --- contract: the Report model mirrors web/src/types.ts ------------------------------------------------------


@pytest.mark.parametrize("name", ["run_a.json", "run_b.json"])
def test_web_fixtures_validate_against_report_model(name):
    raw = json.loads((WEB_FIXTURES / name).read_text())
    report = Report.model_validate(raw)
    assert report.to_json_dict() == raw  # same keys, same optional-field omission


# --- researcher Bundle -> analytics EvidenceBundle --------------------------------------------------------------


def test_bundle_adapter():
    eb = to_evidence_bundle(researcher_bundle())
    assert eb.subject.disease.id == "MONDO_0005011" and eb.subject.targets[0].symbol == "IL17A"
    assert eb.subject.action == "inhibitor" and eb.subject.drugs[0].max_stage == "APPROVAL"
    by_rule = {e.data.get("reconcile_rule"): e for e in eb.evidence if e.source == "reconcile" and e.kind == "conflict"}
    assert set(by_rule) == {"stop_reason", "target_failure", "does_not_transfer"}
    assert by_rule["target_failure"].data["kill_signal"] is True  # two subject-mechanism drugs failed
    assert by_rule["target_failure"].data["drug"] == "drug-a, drug-b"
    assert by_rule["stop_reason"].data["kill_signal"] is False  # one stopped trial is not a kill
    assert by_rule["stop_reason"].data["severity"] == "high"
    assert by_rule["does_not_transfer"].data["kill_signal"] is False  # other modality: not a kill
    assert by_rule["does_not_transfer"].data["severity"] == "medium"
    trials = {e.data["nct_id"]: e.data for e in eb.evidence if e.source == "clinicaltrials"}
    assert (trials["NCT00000001"]["status"], trials["NCT00000001"]["stop_class"], trials["NCT00000001"]["role"]) == \
        ("TERMINATED", "efficacy", "same_target")
    assert trials["NCT00000003"]["role"] == "landscape" and trials["NCT00000004"]["stop_class"] is None
    p2 = eb.trial_benchmarks["by_phase"]["PHASE2"]
    assert (p2["n"], p2["enrollment_median"], p2["duration_months_median"]) == (2, 100, 24)
    assert any(e.data.get("benchmark") for e in eb.evidence)
    assert [g.requires for g in eb.gaps] == ["cmc_ip"]


def test_no_kill_when_mechanism_is_approved_in_this_indication():
    b = researcher_bundle()
    approved = _ev("opentargets", "drug:CHEMBL_B", published=None, refs={"drug": "CHEMBL_B"},
                   data={"maxClinicalStage": "APPROVAL"})
    eb = to_evidence_bundle(b.model_copy(update={"evidence": [*b.evidence, approved]}))
    (tf,) = [e for e in eb.conflicts() if e.data["reconcile_rule"] == "target_failure"]
    assert tf.data["kill_signal"] is False and tf.data["severity"] == "high" and "drug-b" in tf.data["kill_suppressed"]


def test_other_modality_failures_are_not_a_kill():
    eb = to_evidence_bundle(researcher_bundle(other_modality_failure=True))
    assert not any(e.data.get("kill_signal") for e in eb.conflicts())


# --- end to end, offline (fake LLM) ----------------------------------------------------------------------------


def test_workflow_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RUNS_DIR", tmp_path)
    bundle = researcher_bundle()
    report = asyncio.run(run(bundle.input, bundle=bundle, settings=FAKE))

    out = tmp_path / bundle.run_id
    for f in ("researcher_bundle.json", "evidence_bundle.json", "analysis_result.json", REPORT_FILE, "trace.jsonl"):
        assert (out / f).exists(), f
    saved = json.loads((out / REPORT_FILE).read_text())
    assert Report.model_validate(saved).to_json_dict() == saved

    assert report.recommendation == "do_not_invest"  # kill signal: two subject-mechanism drugs failed
    assert report.input.modality == "antibody" and report.previous_run_id is None
    assert [s.id for s in report.sections] == ["sec_science", "sec_clinical", "sec_market", "sec_investment"]
    assert all(s.summary == "[fake writer] Section takeaway." for s in report.sections)
    evidence_ids = {e.id for e in report.evidence}
    claim_ids = {c.id for s in report.sections for c in s.claims}
    assert all(r.evidence_id in evidence_ids for s in report.sections for c in s.claims for r in c.evidence)
    assert all(set(r.claim_ids) <= claim_ids for r in report.risks)
    assert all(e.id not in (e.related_evidence_ids or []) for e in report.evidence)
    assert {t.agent for t in report.traces} >= {"orchestrator", "writer"}
    assert report.capital.usd_low <= report.capital.usd_base <= report.capital.usd_high

    # a second run with the same input links back to the first
    second = researcher_bundle(run_id="r2").model_copy(update={"created_at": datetime(2026, 2, 1, tzinfo=UTC)})
    report2 = asyncio.run(run(second.input, bundle=second, settings=FAKE))
    assert report2.previous_run_id == "r1"


@pytest.mark.live
def test_workflow_il17_crohns_live_research(tmp_path, monkeypatch):
    """Real researcher (public APIs), fake LLM: the IL-17 failures in Crohn's must reach the verdict as a kill."""
    monkeypatch.setattr(paths, "RUNS_DIR", tmp_path)
    inp = ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition", modality="antibody")
    report = asyncio.run(run(inp, settings=FAKE))
    assert report.recommendation == "do_not_invest"
    eb = json.loads((tmp_path / report.id / "evidence_bundle.json").read_text())
    assert any(e["data"].get("kill_signal") for e in eb["evidence"] if e["kind"] == "conflict")
