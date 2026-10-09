from datetime import UTC, date, datetime

import pytest

from researcher.schema import Bundle, Drug, Evidence, ResearchInput, SearchPlan, Subject, evidence_id
from researcher.pipeline import research_with_plan
from researcher.reconcile import reconcile

IL17_PLAN = SearchPlan(
    target_symbols=["IL17A", "IL17F"], action="inhibition", modality="antibody",
    disease_terms=["Crohn's disease", "Crohn disease", "regional enteritis"],
    drug_names=["secukinumab", "brodalumab"],
)
STOPPED = ("NCT01150890", "NCT00584740")
NOW = datetime(2026, 1, 1, tzinfo=UTC)
SECU = Drug(chembl_id="CHEMBL1743068", name="secukinumab", synonyms=["Cosentyx", "AIN457", "AIN"], origin="plan")
BRODA = Drug(chembl_id="CHEMBL1742996", name="brodalumab", synonyms=["AMG 827"], origin="plan")


def _ev(source, native, title="t", snippet="s", modules=("pipeline",), refs=None, data=None, published=None):
    return Evidence(id=evidence_id(source, native), source=source, url="https://x", retrieved_at=NOW, kind="record",
                    modules=list(modules), entity_refs=refs or {}, title=title, snippet=snippet, data=data or {},
                    published_at=published)


def _bundle(evidence, cutoff=None, drugs=(SECU, BRODA)):
    inp = ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition", evidence_cutoff=cutoff)
    return Bundle(run_id="r", created_at=NOW, input=inp, evidence=list(evidence),
                  subject=Subject(indication=inp.indication, mechanism=inp.mechanism, drugs=list(drugs)))


def test_dedupe_merges_modules_and_keeps_first_refs():
    a = _ev("ct", "1", modules=["pipeline", "trial-design"], refs={"drug": "CHEMBL1"})
    b = _ev("ct", "1", modules=["red-flags", "pipeline"], refs={"drug": "CHEMBL2", "target": "ENSG1"})
    out = reconcile(_bundle([a, b, _ev("ct", "2")]))
    assert len(out.evidence) == 2
    merged = out.evidence[0]
    assert merged.modules == ["pipeline", "trial-design", "red-flags"]
    assert merged.entity_refs == {"drug": "CHEMBL1", "target": "ENSG1"}
    assert out.reconcile_stats.total_before == 3 and out.reconcile_stats.duplicates_merged == 1


def test_drug_linking():
    items = [
        _ev("x", "iv", title="Brodalumab vs secukinumab",
            data={"interventions": [{"name": "Placebo"}, {"name": "AIN457 10 mg/kg"}]}),  # interventions win
        _ev("x", "title", title="NIH substudy of AIN-457 in Crohn's"),                      # code, punctuation
        _ev("x", "snippet", snippet="compared with AMG-827 and Cosentyx"),                  # earliest in text
        _ev("x", "short", title="AIN study"),                                                # 'AIN' < 4 chars
        _ev("x", "partial", title="secukinumabXYZ"),                                         # not whole word
        _ev("x", "kept", title="secukinumab", refs={"drug": "CHEMBL9"}),                     # never overwrite
    ]
    out = reconcile(_bundle(items))
    refs = {e.id: e.entity_refs.get("drug") for e in out.evidence}
    assert refs[evidence_id("x", "iv")] == SECU.chembl_id
    assert refs[evidence_id("x", "title")] is None  # "ain 457" is not the term "ain457"
    assert refs[evidence_id("x", "snippet")] == BRODA.chembl_id
    assert refs[evidence_id("x", "short")] is None
    assert refs[evidence_id("x", "partial")] is None
    assert refs[evidence_id("x", "kept")] == "CHEMBL9"
    assert out.reconcile_stats.drugs_linked == 2


def test_cutoff_and_leak_guard_offline():
    cutoff = date(2012, 1, 1)
    items = [
        _ev("clinicaltrials", "old", data={"nct_id": "NCT00000001"}, published=date(2011, 5, 1)),
        _ev("clinicaltrials", "new", data={"nct_id": "NCT00000002"}, published=date(2020, 5, 1)),
        _ev("opentargets", "drug-old", data={"maxClinicalStage": "PHASE_2", "nct_ids": ["NCT00000001"]}),
        _ev("opentargets", "drug-new", data={"maxClinicalStage": "PHASE_3", "nct_ids": ["NCT00000001", "NCT00000002"]}),
        _ev("opentargets", "drug-unknown", data={"maxClinicalStage": "PHASE_2", "nct_ids": ["NCT00000099"]}),
        _ev("opentargets", "drug-approval", data={"maxClinicalStage": "APPROVAL", "nct_ids": [],
                                                  "clinicalReports": [{"id": "emea/h/c/1"}]}),
        _ev("opentargets", "assoc", data={"score": 0.1}),
    ]
    out = reconcile(_bundle(items, cutoff=cutoff))
    kept = {e.id for e in out.evidence}
    assert kept == {evidence_id("clinicaltrials", "old"), evidence_id("opentargets", "drug-old"),
                    evidence_id("opentargets", "assoc")}
    s = out.reconcile_stats
    assert (s.dropped_by_cutoff, s.dropped_by_leak_guard) == (1, 3)
    why = {d.id: d.detail for d in s.dropped}
    assert "NCT00000002 (2020-05-01)" in why[evidence_id("opentargets", "drug-new")]
    assert "NCT00000099" in why[evidence_id("opentargets", "drug-unknown")]
    assert "non-trial" in why[evidence_id("opentargets", "drug-approval")]


def test_no_cutoff_drops_nothing():
    items = [_ev("clinicaltrials", "new", data={"nct_id": "NCT00000002"}, published=date(2020, 5, 1)),
             _ev("opentargets", "drug", data={"maxClinicalStage": "PHASE_3", "nct_ids": ["NCT00000099"]})]
    s = reconcile(_bundle(items)).reconcile_stats
    assert len(reconcile(_bundle(items)).evidence) == 2 and s.dropped == []


def _cites(e, nct):
    return nct in (e.data.get("nct_ids") or []) or e.data.get("nct_id") == nct


@pytest.mark.live
def test_il17_crohns_without_cutoff():
    b = research_with_plan(ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition"), IL17_PLAN)
    ids = [e.id for e in b.evidence]
    assert len(ids) == len(set(ids))
    s = b.reconcile_stats
    assert s.dropped == [] and s.total_before - s.duplicates_merged + sum(s.derived_by_rule.values()) == len(b.evidence)
    for nct in STOPPED:
        assert any(e.source == "clinicaltrials" and e.data["nct_id"] == nct for e in b.evidence)
        assert any(e.source == "opentargets" and _cites(e, nct) for e in b.evidence)
    # NCT00936585 (an AIN457 substudy) lists only "Immunologic Monitoring" as intervention; linked via its title
    # by the connector, with the same rule reconcile re-applies (so reconcile changes no trial link).
    substudy = next(e for e in b.evidence if e.data.get("nct_id") == "NCT00936585")
    assert substudy.entity_refs["drug"] == next(d.chembl_id for d in b.subject.drugs if d.name == "secukinumab")
    assert s.drugs_unlinked == 0
    assert all(d.relation == "subject_mechanism" for d in b.subject.drugs)  # plan.modality antibody


@pytest.mark.live
def test_il17_crohns_with_cutoff():
    # Both trials were first posted before 2009-01-01, but their current status (last update posted:
    # NCT00584740 2015-03-31, NCT01150890 2022-01-03) became public after it.
    cutoff = date(2009, 1, 1)
    b = research_with_plan(
        ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition", evidence_cutoff=cutoff), IL17_PLAN)
    for nct in STOPPED:
        assert not any(_cites(e, nct) for e in b.evidence)
    assert all(e.published_at is None or e.published_at <= cutoff for e in b.evidence)
    s = b.reconcile_stats
    assert s.dropped_by_cutoff > 0 and s.dropped_by_leak_guard >= 2
    assert len(b.evidence) == (s.total_before - s.duplicates_merged - s.dropped_by_cutoff - s.dropped_by_leak_guard
                               + sum(s.derived_by_rule.values()))


# --- part 2: derived evidence ---------------------------------------------------------------------

from researcher.reconcile import classify_stop  # noqa: E402

PCSK9_PLAN = SearchPlan(
    target_symbols=["PCSK9"], action="inhibition",
    disease_terms=["hypercholesterolemia"], drug_names=["evolocumab", "alirocumab", "inclisiran"],
)


@pytest.mark.parametrize("why,category", [
    ("The study was terminated prematurely after futility criterion was met", "efficacy"),
    ("imbalance in worsening Crohn's disease in active treatment groups", "efficacy"),
    ("Did not meet the primary endpoint at interim analysis", "efficacy"),
    ("Terminated due to safety concerns", "safety"),
    ("Sponsor decision after serious adverse events", "safety"),
    ("Unacceptable toxicity", "safety"),
    ("Study halted by the sponsor", "business"),
    ("loss of funding", "business"),
    ("poor recruitment", "enrollment"),
    ("Slow enrollment; no safety concerns", "enrollment"),
    ("The primary objective (to evaluate the long-term safety of Praluent) was adequately evaluated in other studies",
     "other"),
    ("The participants' recruitment rate failed to meet the target", "enrollment"),
    ("COVID-19 Pandemic Measures", "other"),
    ("Absence of significant therapeutic benefit over existing therapies", "efficacy"),
    ("The decision to terminate is not based on safety or efficacy but due to slow enrollment impacting the "
     "ability to complete the study as planned.", "enrollment"),
    ("Stopped early for overwhelming efficacy at the interim analysis", "positive_early_stop"),
    ("", "other"),
    (None, "other"),
])
def test_classify_stop(why, category):
    assert classify_stop(why)[0] == category


_NAMES = {"CHEMBL_A": "drug-a", "CHEMBL_B": "drug-b"}


def _trial(nct, status, why=None, drug=None, phases=("PHASE2",), updated=date(2020, 1, 1), has_results=False):
    interventions = [{"type": "DRUG", "name": _NAMES[drug]}] if drug else []
    return _ev("clinicaltrials", nct, title=nct, refs={"drug": drug} if drug else None, published=updated,
               data={"nct_id": nct, "overall_status": status, "why_stopped": why, "phases": list(phases),
                     "interventions": interventions, "has_results": has_results})


def _ot_drug(chembl, trials):
    return _ev("opentargets", f"drug:{chembl}", refs={"drug": chembl},
               data={"maxClinicalStage": "PHASE_2", "nct_ids": [t["nct_id"] for t in trials], "trials": trials})


def _derived(bundle, rule):
    return [e for e in bundle.evidence if e.source == "reconcile" and e.data["rule"] == rule]


A, B = Drug(chembl_id="CHEMBL_A", name="drug-a", max_phase=4, origin="plan", target_ids=["ENSG_X"]), \
    Drug(chembl_id="CHEMBL_B", name="drug-b", max_phase=2, origin="plan", target_ids=["ENSG_Y"])


def _subject_bundle(evidence, cutoff=None):
    b = _bundle(evidence, cutoff=cutoff, drugs=(A, B))
    subject = b.subject.model_copy(update={"target_ids": ["ENSG_X", "ENSG_Y"], "target_symbols": ["X1", "X1R"]})
    return b.model_copy(update={"subject": subject})


def test_rules_offline():
    items = [
        _trial("NCT00000001", "TERMINATED", "lack of efficacy", drug="CHEMBL_A"),
        _trial("NCT00000002", "TERMINATED", "safety concerns", drug="CHEMBL_B"),
        _trial("NCT00000003", "WITHDRAWN", "funding", drug="CHEMBL_B"),
        _trial("NCT00000004", "RECRUITING", updated=date(2021, 1, 1)),
        _trial("NCT00000005", "RECRUITING", updated=date(2025, 12, 1)),
        _ot_drug("CHEMBL_A", [{"nct_id": "NCT00000001", "phase": "PHASE3", "status": "COMPLETED", "why_stopped": None}]),
    ]
    out = reconcile(_subject_bundle(items))
    by_nct = {e.data["nct_id"]: e for e in out.evidence if e.source == "clinicaltrials"}
    assert [by_nct[f"NCT0000000{i}"].data["stop_category"] for i in (1, 2, 3)] == ["efficacy", "safety", "business"]
    assert "stop_category" not in by_nct["NCT00000004"].data

    r1 = _derived(out, "stop_reason")
    assert {e.data["nct_id"] for e in r1} == {"NCT00000001", "NCT00000002"}
    assert all(e.kind == "record" and "red-flags" in e.modules and e.related_evidence_ids for e in r1)

    (r2,) = _derived(out, "status_mismatch")
    assert r2.kind == "conflict" and set(r2.data["differences"]) == {"phase", "status"}
    assert r2.related_evidence_ids == sorted([by_nct["NCT00000001"].id, evidence_id("opentargets", "drug:CHEMBL_A")])
    assert r2.data["supporting"] == [by_nct["NCT00000001"].id]

    (r3,) = _derived(out, "stale_active")  # as-of = created_at 2026-01-01; 2025-12 is fresh
    assert r3.data["nct_id"] == "NCT00000004" and r3.data["months_since_update"] == 60

    (r4,) = _derived(out, "does_not_transfer")  # only drug-a is approved
    assert r4.kind == "conflict" and r4.entity_refs["drug"] == "CHEMBL_A"

    (r5,) = _derived(out, "target_failure")
    assert r5.data["severity"] == "high" and r5.data["target_symbols"] == ["X1", "X1R"]

    for e in _derived(out, "stop_reason") + [r2, r3, r4, r5]:
        assert e.id == evidence_id("reconcile", f"{e.data['rule']}:{','.join(sorted(e.related_evidence_ids))}")
        assert set(e.related_evidence_ids) <= {x.id for x in out.evidence}
    assert reconcile(_subject_bundle(items)).evidence == out.evidence  # deterministic
    assert out.reconcile_stats.derived_by_rule == {"stop_reason": 2, "status_mismatch": 1, "stale_active": 1,
                                                   "does_not_transfer": 1, "target_failure": 1}


def test_r5_needs_two_drugs_and_r4_skipped_under_cutoff():
    items = [_trial("NCT00000001", "TERMINATED", "futility", drug="CHEMBL_A", updated=date(2010, 1, 1)),
             _trial("NCT00000002", "TERMINATED", "futility", drug="CHEMBL_A", updated=date(2010, 1, 1))]
    out = reconcile(_subject_bundle(items, cutoff=date(2011, 1, 1)))
    assert _derived(out, "target_failure") == [] and _derived(out, "does_not_transfer") == []
    assert "does_not_transfer" in out.reconcile_stats.rules_skipped
    assert len(_derived(out, "stop_reason")) == 2


@pytest.mark.live
def test_rules_il17_crohns():
    b = research_with_plan(ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition"), IL17_PLAN)
    ct = {e.data["nct_id"]: e for e in b.evidence if e.source == "clinicaltrials"}
    assert ct["NCT01150890"].data["stop_category"] in ("efficacy", "safety")
    assert any(e.data["nct_id"] == "NCT01150890" for e in _derived(b, "stop_reason"))
    assert _derived(b, "does_not_transfer")
    (r5,) = _derived(b, "target_failure")
    assert {"IL17A", "IL17RA"} <= set(r5.data["target_symbols"]) and r5.data["severity"] == "high"
    ids = [e.id for e in b.evidence]
    assert len(ids) == len(set(ids))


@pytest.mark.live
def test_rules_pcsk9_no_target_failure():
    b = research_with_plan(ResearchInput(indication="hypercholesterolemia", mechanism="PCSK9 inhibition"), PCSK9_PLAN)
    assert _derived(b, "target_failure") == []


@pytest.mark.live
def test_rules_il17_crohns_with_cutoff():
    b = research_with_plan(ResearchInput(indication="Crohn's disease", mechanism="IL-17 inhibition",
                                         evidence_cutoff=date(2009, 1, 1)), IL17_PLAN)
    assert _derived(b, "does_not_transfer") == [] and _derived(b, "target_failure") == []


# --- stop classification (LLM batch + fallback), unknown stops, trial drug links --------------------------------

from researcher.llm import LlmError  # noqa: E402
from researcher.reconcile import RESULTS_NOTE, classify_stops  # noqa: E402


class FakeStopLLM:
    def __init__(self, labels=None, error=None):
        self.labels, self.error = labels, error
        self.calls, self.prompts = [], []

    def generate_json(self, prompt, schema, purpose=None):
        self.prompts.append((prompt, purpose))
        if self.error:
            raise self.error
        return schema.model_validate({"labels": self.labels})


def test_classify_stops_one_batched_call_with_fallback():
    texts = ["Not based on safety or efficacy; slow enrollment", "futility", "futility", "", None, "loss of funding"]
    llm = FakeStopLLM([{"id": 0, "category": "enrollment"}, {"id": 1, "category": "efficacy"}])  # id 2 left out
    out = classify_stops(texts, llm)
    assert len(llm.prompts) == 1 and llm.prompts[0][1] == "stop_reason"
    assert "0. Not based on safety" in llm.prompts[0][0] and "2. loss of funding" in llm.prompts[0][0]
    assert out["Not based on safety or efficacy; slow enrollment"][:2] == ("enrollment", "llm")
    assert out["futility"][:2] == ("efficacy", "llm")
    assert out["loss of funding"][:2] == ("business", "keyword")  # not labelled by the LLM -> keyword
    assert classify_stops(["futility"], FakeStopLLM(error=LlmError("429")))["futility"][:2] == ("efficacy", "keyword")
    assert classify_stops([], llm) == {} and len(llm.prompts) == 1  # nothing to classify -> no call


def test_unknown_stop_reason_is_not_a_red_flag():
    items = [
        _trial("NCT00000001", "TERMINATED", None, drug="CHEMBL_A", has_results=True),
        _trial("NCT00000002", "WITHDRAWN", "  ", drug="CHEMBL_A"),
        _trial("NCT00000003", "TERMINATED", "Stopped early for overwhelming efficacy", drug="CHEMBL_A"),
        _trial("NCT00000004", "TERMINATED", "futility", drug="CHEMBL_A"),
    ]
    items = [e.model_copy(update={"modules": ["pipeline", "red-flags"]}) for e in items]
    llm = FakeStopLLM([{"id": 0, "category": "positive_early_stop"}, {"id": 1, "category": "efficacy"}])
    out = reconcile(_subject_bundle(items), llm)
    by_nct = {e.data["nct_id"]: e for e in out.evidence if e.source == "clinicaltrials"}
    one, two, three, four = (by_nct[f"NCT0000000{i}"] for i in (1, 2, 3, 4))
    assert (one.data["stop_category"], one.data["stop_category_source"]) == ("unknown", "none")
    assert one.data["stop_note"] == RESULTS_NOTE and "stop_note" not in two.data
    assert "red-flags" not in one.modules and "red-flags" not in two.modules
    assert three.data["stop_category"] == "positive_early_stop" and "green-flags" in three.modules
    assert "red-flags" not in three.modules
    assert (four.data["stop_category"], four.data["stop_category_source"]) == ("efficacy", "llm")
    assert "red-flags" in four.modules
    assert {e.data["nct_id"] for e in _derived(out, "stop_reason")} == {"NCT00000004"}


def test_trial_drug_links_follow_interventions_and_title():
    def trial(native, title, interventions, refs=None, data=None):
        return _ev("clinicaltrials", native, title=title, refs=refs,
                   data={"nct_id": native, "title": title, "interventions": interventions, **(data or {})})

    items = [
        # Products listed, none is a subject drug: the title mention is background, and the old link is removed.
        trial("bg", "Nivolumab with secukinumab reinfusion", [{"type": "DRUG", "name": "Nivolumab"}],
              refs={"drug": SECU.chembl_id}, data={"subject_drug_chembl_ids": [], "found_via": "secukinumab"}),
        # No product intervention: the title names the drug.
        trial("sub", "NIH substudy of AIN457", [{"type": "OTHER", "name": "Immunologic Monitoring"}]),
        # Intervention otherNames; data and refs are made to agree.
        trial("iv", "A study", [{"type": "BIOLOGICAL", "name": "IL-17 mAb", "otherNames": ["AMG 827"]}],
              data={"subject_drug_chembl_ids": [SECU.chembl_id]}),
    ]
    out = reconcile(_bundle(items))
    by = {e.data["nct_id"]: e for e in out.evidence}
    assert "drug" not in by["bg"].entity_refs and by["bg"].data["subject_drug_chembl_ids"] == []
    assert by["bg"].data["found_via"] == "secukinumab" and by["bg"].data["drug_relation"] is None
    assert by["sub"].entity_refs["drug"] == SECU.chembl_id
    assert by["iv"].entity_refs["drug"] == BRODA.chembl_id == by["iv"].data["subject_drug_chembl_ids"][0]
    assert by["iv"].data["drug_relation"] == "subject_mechanism"
    s = out.reconcile_stats
    assert (s.drugs_linked, s.drugs_unlinked) == (2, 1)


# --- trial records rebuilt as of the cutoff ------------------------------------------------------------------


def _registry_trial(native, *, first, last, status, why=None, start="2010-01-01", end=None, end_type="ACTUAL",
           enrollment=100, enrollment_type="ACTUAL", results=None):
    data = {"nct_id": native, "overall_status": status, "why_stopped": why, "phases": ["PHASE3"],
            "enrollment": enrollment, "enrollment_type": enrollment_type, "start_date": start,
            "completion_date": end, "completion_date_type": end_type, "first_posted": first,
            "results_first_posted": results, "last_update_posted": last, "has_results": bool(results),
            "lead_sponsor": "X"}
    modules = ("pipeline", "red-flags") if status == "TERMINATED" else ("pipeline",)
    return _ev("clinicaltrials", native, data=data, modules=modules, published=date.fromisoformat(last))


def test_trial_registered_before_cutoff_is_kept_with_later_status_hidden():
    from researcher.reconcile import trial_as_of
    ev = _registry_trial("NCT10000001", first="2012-12-01", last="2018-03-01", status="TERMINATED", why="futility",
                end="2017-02-14", results="2018-03-01")
    out, _ = trial_as_of(ev, date(2016, 1, 1))
    assert out is not None and out.published_at == date(2012, 12, 1)
    d = out.data
    assert d["overall_status"] == "RECRUITING" and d["why_stopped"] is None
    assert d["completion_date"] is None and d["enrollment"] is None and not d["has_results"]
    assert d["last_update_posted"] is None and d["as_of_cutoff"] == "2016-01-01"
    assert "red-flags" not in out.modules and "futility" not in out.snippet


def test_trial_that_ended_before_cutoff_keeps_its_status():
    from researcher.reconcile import trial_as_of
    ev = _registry_trial("NCT10000002", first="2010-01-01", last="2019-01-01", status="TERMINATED", why="safety",
                end="2011-06-01")
    out, _ = trial_as_of(ev, date(2012, 1, 1))
    assert out.data["overall_status"] == "TERMINATED" and out.data["why_stopped"] == "safety"
    assert "red-flags" in out.modules


def test_trial_first_posted_after_cutoff_is_dropped_and_not_started_is_flagged():
    from researcher.reconcile import trial_as_of
    late = _registry_trial("NCT10000003", first="2013-01-01", last="2014-01-01", status="COMPLETED", end="2013-12-01")
    assert trial_as_of(late, date(2012, 1, 1))[0] is None
    planned = _registry_trial("NCT10000004", first="2011-11-01", last="2015-01-01", status="COMPLETED", start="2012-03-01",
                     end="2014-01-01")
    assert trial_as_of(planned, date(2012, 1, 1))[0].data["overall_status"] == "NOT_YET_RECRUITING"


def test_cutoff_keeps_registered_trials_without_post_cutoff_dates():
    items = [_registry_trial("NCT10000005", first="2011-01-01", last="2020-01-01", status="TERMINATED", why="efficacy",
                    end="2015-01-01"),
             _registry_trial("NCT10000006", first="2013-01-01", last="2020-01-01", status="COMPLETED", end="2015-01-01")]
    out = reconcile(_bundle(items, cutoff=date(2012, 1, 1)))
    kept = [e for e in out.evidence if e.source == "clinicaltrials"]
    assert [e.data["nct_id"] for e in kept] == ["NCT10000005"]
    assert all(e.published_at <= date(2012, 1, 1) for e in out.evidence if e.published_at)
    assert out.reconcile_stats.dropped_by_cutoff == 1
    assert not any(e.data.get("rule") == "stop_reason" for e in out.evidence)  # the 2015 stop is not visible
