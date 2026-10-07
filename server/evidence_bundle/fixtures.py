"""Generates hand-made test bundles (is_fixture=true) until the real researcher is ready.

    python -m evidence_bundle.fixtures      # writes data/fixtures/*.json

Facts marked REAL were checked against live APIs (Open Targets 26.9, ClinicalTrials.gov v2,
openFDA, ChEMBL) in Oct 2026. Items with data.synthetic=true are plausible placeholders
(paraphrased, URL = PubMed search) — fine for pipeline testing, NOT for demo claims.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

from evidence_bundle.models import EvidenceBundle
from evidence_bundle.paths import FIXTURES_DIR

OUT = FIXTURES_DIR
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def sid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def ev(source, native, title, snippet, kind, modules, url, published=None, refs=None, data=None,
       related=None, snapshot=False):
    """snapshot=True -> 'as of today' fact (OT/ChEMBL/FDA label): dropped under cutoff."""
    return {
        "id": sid(source, native), "source": source, "url": url, "retrieved_at": NOW.isoformat(),
        "title": title, "snippet": snippet, "kind": kind, "modules": modules,
        "published_at": published.isoformat() if published else None,
        "related_evidence_ids": related or [], "entity_refs": refs or {}, "data": {**(data or {}), "_snapshot": snapshot},
    }


def pubmed_search(q: str) -> str:
    return "https://pubmed.ncbi.nlm.nih.gov/?term=" + q.replace(" ", "+")


def trial(nct, title, drug, status, why, stop_class, phase, start, posted, enrollment, outcome, role, sponsor="Industry"):
    phases = "/".join(p.replace("PHASE", "Ph") for p in phase)
    snippet = (f"{phases}, {status}" + (f" (why stopped: {why})" if why else "")
               + f". Interventions: {drug}. Sponsor: {sponsor}. Enrollment: {enrollment or 'n/a'}. Start {start}. "
               f"Primary outcome: {outcome}.")
    mods = ["pipeline", "trial-design"] if role == "same_target" else ["competitive-landscape", "pipeline"]
    if stop_class in {"safety", "efficacy"}:
        mods.append("red-flags")
    return ev("clinicaltrials", nct, f"{nct}: {title}", snippet, "record", mods,
              f"https://clinicaltrials.gov/study/{nct}", date.fromisoformat(posted),
              {"nct_id": nct, "drug": drug},
              {"nct_id": nct, "status": status, "why_stopped": why, "stop_class": stop_class, "phases": phase,
               "enrollment": enrollment, "start_date": start, "role": role})


# ============================================================================ IL-17 x Crohn's


def il17_crohn() -> tuple[dict, list[dict], dict]:
    subject = {
        "disease": {"id": "MONDO_0005011", "name": "Crohn disease",
                    "synonyms": ["Crohn's disease", "regional enteritis", "Ileitis, Terminal"]},
        "targets": [{"ensembl_id": "ENSG00000112115", "symbol": "IL17A", "name": "interleukin 17A", "role": "primary"}],
        "action": "inhibitor",
        "drugs": [
            {"chembl_id": "CHEMBL1743068", "name": "Secukinumab", "synonyms": ["AIN457", "AIN-457", "Cosentyx"],
             "target_symbols": ["IL17A"], "match": "target", "max_stage": "APPROVAL"},
            {"chembl_id": "CHEMBL1743034", "name": "Ixekizumab", "synonyms": ["LY2439821", "Taltz"],
             "target_symbols": ["IL17A"], "match": "target", "max_stage": "APPROVAL"},
            {"chembl_id": None, "name": "Bimekizumab", "synonyms": [], "target_symbols": ["IL17A", "IL17F"],
             "match": "target", "max_stage": "APPROVAL"},
            {"chembl_id": "CHEMBL2108512", "name": "Brodalumab", "synonyms": ["AMG 827", "Siliq"],
             "target_symbols": ["IL17RA"], "match": "family", "max_stage": "APPROVAL"},
        ],
        "notes": ["Mechanism 'IL-17 inhibition' resolved to IL17A; IL17RA drugs added as same-family."],
    }
    E: dict[str, dict] = {}

    # ---- Open Targets (REAL numbers, snapshot)
    E["assoc"] = ev("opentargets", "assoc:IL17A:MONDO_0005011", "Target–disease association: IL17A / Crohn disease",
        "Open Targets association IL17A–Crohn disease: overall score 0.100 (0–1). By evidence type: literature=0.503, "
        "clinical=0.137, rna_expression=0.126. No human genetic evidence (correlative evidence only).",
        "record", ["scientific-evidence"], "https://platform.opentargets.org/evidence/ENSG00000112115/MONDO_0005011",
        refs={"target": "IL17A"}, data={"overall_score": 0.100, "has_genetic_evidence": False,
        "datatype_scores": {"literature": 0.503, "clinical": 0.137, "rna_expression": 0.126}}, snapshot=True)
    E["secu"] = ev("opentargets", "drug:secukinumab", "Secukinumab: Interleukin 17A inhibitor (max stage APPROVAL)",
        "Secukinumab (antibody) inhibits IL17A; approved. Developed for: psoriasis, psoriatic arthritis, ankylosing "
        "spondylitis, hidradenitis suppurativa, Crohn's disease. In this indication: max stage PHASE_2.",
        "record", ["pipeline", "competitive-landscape", "scientific-evidence", "green-flags"],
        "https://platform.opentargets.org/drug/CHEMBL1743068", refs={"drug": "Secukinumab"},
        data={"drug": "Secukinumab", "max_stage": "APPROVAL", "stage_in_indication": "PHASE_2", "role": "same_target"},
        snapshot=True)
    E["ixe"] = ev("opentargets", "drug:ixekizumab", "Ixekizumab: Interleukin 17A inhibitor (max stage APPROVAL)",
        "Ixekizumab (antibody) inhibits IL17A; approved for psoriasis, psoriatic arthritis, axial spondyloarthritis. "
        "Not developed in Crohn's disease.", "record", ["pipeline", "competitive-landscape", "scientific-evidence", "green-flags"],
        "https://platform.opentargets.org/drug/CHEMBL1743034", refs={"drug": "Ixekizumab"},
        data={"drug": "Ixekizumab", "max_stage": "APPROVAL", "role": "same_target"}, snapshot=True)
    E["bime"] = ev("opentargets", "drug:bimekizumab", "Bimekizumab: IL-17A/IL-17F inhibitor (max stage APPROVAL)",
        "Bimekizumab (antibody) inhibits IL17A and IL17F; approved for psoriasis-spectrum diseases. Not developed in Crohn's disease.",
        "record", ["pipeline", "competitive-landscape", "scientific-evidence"], "https://platform.opentargets.org/drugs",
        refs={"drug": "Bimekizumab"}, data={"drug": "Bimekizumab", "max_stage": "APPROVAL", "role": "same_target"}, snapshot=True)
    E["bro"] = ev("opentargets", "drug:brodalumab", "Brodalumab: IL-17 receptor A antagonist (max stage APPROVAL)",
        "Brodalumab (antibody) blocks IL17RA (same IL-17 pathway, receptor); approved for psoriasis. "
        "In this indication: max stage PHASE_2.", "record", ["pipeline", "competitive-landscape", "scientific-evidence"],
        "https://platform.opentargets.org/drug/CHEMBL2108512", refs={"drug": "Brodalumab"},
        data={"drug": "Brodalumab", "max_stage": "APPROVAL", "stage_in_indication": "PHASE_2", "role": "same_target",
              "match": "family"}, snapshot=True)
    for name, moa in [("Infliximab", "TNF-alpha inhibitor"), ("Adalimumab", "TNF-alpha inhibitor"),
                      ("Vedolizumab", "integrin alpha4beta7 inhibitor"), ("Ustekinumab", "IL-12/IL-23 p40 inhibitor"),
                      ("Risankizumab", "IL-23 p19 inhibitor"), ("Upadacitinib", "JAK1 inhibitor")]:
        E[f"soc_{name}"] = ev("opentargets", f"soc:{name}", f"{name} in the indication (approved)",
            f"{name} ({moa}) — approved in Crohn's disease; FDA label lists this indication.",
            "record", ["unmet-need", "competitive-landscape"], "https://platform.opentargets.org/disease/MONDO_0005011",
            refs={"drug": name}, data={"drug": name, "role": "approved", "mechanism": moa}, snapshot=True)
    E["landscape"] = ev("opentargets", "summary:MONDO_0005011", "Drug development landscape: Crohn disease",
        "Open Targets lists 198 drugs/candidates ever developed for Crohn disease, 23 of them approved (incl. anti-TNF, "
        "anti-integrin, anti-IL-12/23, anti-IL-23 p19, JAK inhibitors, corticosteroids, thiopurines).",
        "record", ["competitive-landscape", "unmet-need"], "https://platform.opentargets.org/disease/MONDO_0005011",
        data={"n_drugs": 198, "n_approved": 23}, snapshot=True)

    # ---- ClinicalTrials.gov (REAL ids, statuses and whyStopped)
    E["t_secu"] = trial("NCT00584740", "Proof-of-concept study of AIN457 (anti-IL-17) in moderate to severe active Crohn's disease",
        "Secukinumab", "TERMINATED", "The study was terminated prematurely after futility criterion was met at planned interim "
        "analysis of 41 patients.", "efficacy", ["PHASE2"], "2008-08-31", "2007-12-24", None,
        "Change in CDAI at week 6", "same_target", "Novartis")
    E["t_secu_ext"] = trial("NCT01009281", "52-week open-label extension of AIN457 in moderate to severe Crohn's disease",
        "Secukinumab", "TERMINATED", None, None, ["PHASE2"], "2009-10-30", "2009-11-06", None,
        "Safety and tolerability", "same_target", "Novartis")
    E["t_bro"] = trial("NCT01150890", "Brodalumab (AMG 827) in adults with moderate to severe Crohn's disease",
        "Brodalumab", "TERMINATED", "The study was terminated early based on an imbalance in worsening Crohn's disease in "
        "active treatment groups", "safety", ["PHASE2"], "2010-11-09", "2010-06-25", 130,
        "Percentage of participants who achieved clinical remission at week 6", "same_target", "Amgen")
    E["t_bro_ext"] = trial("NCT01199302", "Long-term safety and efficacy of AMG 827 in subjects with Crohn's disease",
        "Brodalumab", "TERMINATED", "The study was terminated early based on an imbalance in worsening Crohn's disease in "
        "active treatment groups.", "safety", ["PHASE2"], "2011-02-02", "2010-09-10", None,
        "Adverse events", "same_target", "Amgen")
    # active competitors (SYNTHETIC ids)
    for i, (drug, ph) in enumerate([("Anti-IL-23 p19 antibody A", "PHASE3"), ("Oral JAK1 inhibitor B", "PHASE3"),
                                    ("Anti-TL1A antibody C", "PHASE3"), ("S1P modulator D", "PHASE2")], 1):
        e = trial(f"NCT9000000{i}", f"{drug} in moderately to severely active Crohn's disease", drug, "RECRUITING", None, None,
                  [ph], "2024-03-01", "2024-01-15", None, "Clinical remission (CDAI<150) and endoscopic response at week 12",
                  "landscape")
        e["data"]["synthetic"] = True
        E[f"comp{i}"] = e
    E["bench"] = ev("clinicaltrials", "benchmarks:MONDO_0005011", "Trial benchmarks: Ph2/3 interventional trials in Crohn disease",
        "Computed from 300 of 552 Ph2/3 interventional trials. PHASE2: median enrollment 120, median duration 26 mo, "
        "termination rate 0.21; PHASE3: median enrollment 410, median duration 38 mo, termination rate 0.14. "
        "Stop reasons among terminated: business 31, efficacy 18, enrollment 15, safety 6. Common primary endpoints: "
        "clinical remission (CDAI<150); endoscopic response (SES-CD); CDAI-100 response.",
        "record", ["trial-design"], "https://clinicaltrials.gov/search?cond=Crohn+disease",
        data={"benchmark": True, "synthetic": True, "by_phase": {
            "PHASE2": {"n": 330, "enrollment_median": 120, "duration_months_median": 26, "termination_rate": 0.21},
            "PHASE3": {"n": 222, "enrollment_median": 410, "duration_months_median": 38, "termination_rate": 0.14}}})

    # ---- PubMed
    E["hueber2012"] = ev("pubmed", "22595313",
        "Secukinumab, a human anti-IL-17A monoclonal antibody, for moderate to severe Crohn's disease: unexpected results "
        "of a randomised, double-blind placebo-controlled trial",
        "Randomised placebo-controlled proof-of-concept trial of secukinumab in active Crohn's disease. IL-17A blockade was "
        "ineffective and was associated with higher rates of adverse events (incl. worsening/infections) than placebo. "
        "[paraphrased]", "literature", ["scientific-evidence", "red-flags", "pipeline"],
        "https://pubmed.ncbi.nlm.nih.gov/22595313/", date(2012, 5, 19), {"pmid": "22595313"},
        {"journal": "Gut", "pub_types": ["Randomized Controlled Trial"]})
    lit = [
        ("expr", date(2003, 1, 1), "Increased expression of interleukin 17 in inflammatory bowel disease mucosa",
         "IL-17 transcripts and IL-17+ cells are increased in inflamed mucosa of Crohn's disease and ulcerative colitis "
         "patients versus controls (correlative).", ["scientific-evidence"], "interleukin 17 expression inflammatory bowel disease"),
        ("th17", date(2009, 6, 1), "The IL-23/Th17 axis in Crohn's disease: genetics and immunology (review)",
         "Review: IL23R variants are genetically associated with Crohn's disease; Th17 cells and IL-17A are proposed "
         "effectors of intestinal inflammation. Causality of IL-17A itself is not established.",
         ["scientific-evidence"], "IL-23 Th17 axis Crohn disease review"),
        ("barrier", date(2009, 7, 1), "IL-17A protects the intestinal epithelium in experimental colitis",
         "In mouse colitis models, neutralising IL-17A exacerbated disease; IL-17A supports epithelial barrier integrity "
         "(tight junctions). Suggests blocking IL-17A could worsen gut inflammation.",
         ["scientific-evidence", "red-flags"], "IL-17A intestinal epithelium protective colitis"),
        ("pso2010", date(2010, 10, 6), "Effects of AIN457, a fully human antibody to IL-17A, on psoriasis, rheumatoid arthritis and uveitis",
         "Early clinical study: single doses of AIN457 (secukinumab) reduced clinical signs of psoriasis and showed activity "
         "in RA and uveitis — human proof-of-mechanism for IL-17A blockade outside the gut.",
         ["scientific-evidence", "green-flags"], "AIN457 psoriasis rheumatoid arthritis uveitis"),
        ("bro2016", date(2016, 8, 1), "Randomized phase 2 study of brodalumab in moderate-to-severe Crohn's disease",
         "Brodalumab (anti-IL-17RA) did not show efficacy in Crohn's disease; the study was stopped early because of "
         "disproportionate worsening of Crohn's disease in active arms.", ["scientific-evidence", "red-flags", "pipeline"],
         "brodalumab Crohn disease phase 2"),
        ("unmet", date(2010, 3, 1), "Primary non-response and loss of response to anti-TNF therapy in Crohn's disease (review)",
         "About one third of patients do not respond to anti-TNF induction and a further share lose response each year; "
         "refractory Crohn's disease remains a major unmet need.", ["unmet-need"], "anti-TNF loss of response Crohn review"),
        ("epi", date(2017, 12, 1), "Worldwide incidence and prevalence of inflammatory bowel disease (systematic review)",
         "Prevalence of Crohn's disease is highest in North America and Europe (often >300 per 100,000); incidence is "
         "rising in newly industrialised countries.", ["patient-population"], "worldwide prevalence inflammatory bowel disease systematic review"),
    ]
    for key, pub, title, snip, mods, q in lit:
        E[key] = ev("pubmed", f"synth:{key}", title, snip + " [synthetic placeholder]", "literature", mods,
                    pubmed_search(q), pub, data={"synthetic": True})

    # ---- openFDA (REAL)
    E["label"] = ev("openfda", "label:secukinumab", "FDA label: Cosentyx — warning mentions the indication",
        "FDA label for Cosentyx (secukinumab). Indications: plaque psoriasis, psoriatic arthritis, ankylosing spondylitis, "
        "nr-axSpA, enthesitis-related arthritis, hidradenitis suppurativa. WARNING mentions the indication: Inflammatory "
        "Bowel Disease (IBD): cases of IBD, including Crohn's disease and ulcerative colitis, were reported; exacerbations occurred.",
        "record", ["scientific-evidence", "competitive-landscape", "red-flags"],
        "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=77c4b13e-7df3-42d4-81db-3d0cddb7f67a",
        refs={"drug": "Secukinumab"}, data={"generic": "Secukinumab", "warning_mentions_indication": True}, snapshot=True)
    E["faers"] = ev("openfda", "faers:secukinumab:crohn", "FAERS: 'Crohn's disease' reported with Secukinumab",
        "FAERS (all reports to date): 1342 of 160421 adverse-event reports for secukinumab list Crohn's disease as a "
        "reaction (0.84%). Spontaneous reports: signal, not causality.", "record", ["red-flags"],
        "https://api.fda.gov/drug/event.json?search=patient.drug.openfda.generic_name:%22secukinumab%22+AND+patient.reaction.reactionmeddrapt:crohn",
        refs={"drug": "Secukinumab"}, data={"generic": "Secukinumab", "reports_with_reaction": 1342, "reports_total": 160421,
        "share": 0.0084}, snapshot=True)
    # ---- ChEMBL (REAL)
    E["chembl"] = ev("chembl", "ind:CHEMBL1743068", "ChEMBL indications: Secukinumab",
        "ChEMBL indications for secukinumab: approved (phase 4) for psoriasis, psoriatic arthritis, psoriasis vulgaris; "
        "max phase in Crohn's disease: 2.", "record", ["scientific-evidence", "pipeline", "competitive-landscape"],
        "https://www.ebi.ac.uk/chembl/explore/compound/CHEMBL1743068", refs={"drug": "Secukinumab"},
        data={"drug": "Secukinumab", "approved_indications": ["psoriasis", "psoriatic arthritis"], "phase_in_indication": 2.0},
        snapshot=True)

    # ---- conflicts (reconciliation)
    def conflict(rule, sev, kill, title, snip, rel, mods, drug):
        ids = sorted(E[k]["id"] for k in rel)
        return ev("reconciliation", f"{rule}:{'|'.join(ids)}", title, snip, "conflict", ["red-flags", *mods],
                  E[rel[0]]["url"], max(date.fromisoformat(E[k]["published_at"]) for k in rel if E[k]["published_at"]) if any(E[k]["published_at"] for k in rel) else None,
                  data={"rule": rule, "severity": sev, "kill_signal": kill, "drug": drug}, related=ids)
    E["c1"] = conflict("stopped_for_cause", "high", True, "Secukinumab (same target) trial in Crohn disease stopped for efficacy",
        "NCT00584740: secukinumab in Crohn's disease terminated for futility at interim analysis. A drug acting on the same "
        "target already failed in this indication.", ["t_secu"], ["pipeline", "scientific-evidence"], "Secukinumab")
    E["c2"] = conflict("stopped_for_cause", "high", True, "Brodalumab (same target family) trial in Crohn disease stopped for safety",
        "NCT01150890 and NCT01199302: brodalumab (IL17RA) trials in Crohn's disease terminated due to worsening of Crohn's "
        "disease in active arms — signs of harm.", ["t_bro", "t_bro_ext"], ["pipeline", "scientific-evidence"], "Brodalumab")
    E["c3"] = conflict("mechanism_not_transferring", "high", True, "Secukinumab: approved in psoriasis, failed in Crohn disease",
        "Secukinumab is approved in psoriasis/PsA (ChEMBL phase 4) but its Crohn's trial was stopped for futility; "
        "human-validated IL-17A biology does not transfer to this indication.", ["t_secu", "chembl", "secu"],
        ["scientific-evidence", "pipeline"], "Secukinumab")
    E["c4"] = conflict("label_warning", "high", False, "FDA label of Cosentyx warns about Crohn disease",
        "The FDA label of secukinumab (same target) warns about new-onset/exacerbation of IBD incl. Crohn's disease.",
        ["label"], ["scientific-evidence"], "Secukinumab")
    E["c5"] = conflict("faers_class_signal", "medium", False, "FAERS: Crohn disease reported as adverse event of Secukinumab",
        "1342 FAERS reports (0.84%) for secukinumab list Crohn's disease as an adverse reaction — possible class effect.",
        ["faers"], ["scientific-evidence"], "Secukinumab")
    return subject, list(E.values()), {}


# ============================================================================ PCSK9 x hypercholesterolemia (contrast)


def pcsk9() -> tuple[dict, list[dict], dict]:
    subject = {
        "disease": {"id": "MONDO_0005439", "name": "familial hypercholesterolemia", "synonyms": ["hypercholesterolemia"]},
        "targets": [{"ensembl_id": "ENSG00000169174", "symbol": "PCSK9", "name": "proprotein convertase subtilisin/kexin type 9", "role": "primary"}],
        "action": "inhibitor",
        "drugs": [{"name": "Evolocumab", "synonyms": ["AMG 145", "Repatha"], "target_symbols": ["PCSK9"], "max_stage": "APPROVAL"},
                  {"name": "Alirocumab", "synonyms": ["REGN727", "Praluent"], "target_symbols": ["PCSK9"], "max_stage": "APPROVAL"},
                  {"name": "Inclisiran", "synonyms": ["Leqvio"], "target_symbols": ["PCSK9"], "max_stage": "APPROVAL"}],
        "notes": [],
    }
    E: dict[str, dict] = {}
    E["assoc"] = ev("opentargets", "assoc:PCSK9:MONDO_0005439", "Target–disease association: PCSK9 / familial hypercholesterolemia",
        "Open Targets association: overall score 0.856. By evidence type: clinical=0.972, genetic_association=0.866, "
        "genetic_literature=0.864, literature=0.775, animal_model=0.510. Human genetic evidence present.",
        "record", ["scientific-evidence"], "https://platform.opentargets.org/evidence/ENSG00000169174/MONDO_0005439",
        data={"overall_score": 0.856, "has_genetic_evidence": True}, snapshot=True)
    E["gen"] = ev("opentargets", "genetic:PCSK9", "Human genetic evidence (genomics_england, eva, gwas): PCSK9 / FH",
        "Gain-of-function PCSK9 variants cause autosomal dominant hypercholesterolemia; loss-of-function variants lower "
        "LDL-C and coronary risk — natural human 'knock-down' experiment supporting causality and safety of inhibition.",
        "record", ["scientific-evidence"], "https://platform.opentargets.org/evidence/ENSG00000169174/MONDO_0005439",
        data={"assumed_time_stable": True})
    for name, extra in [("Evolocumab", "CV outcomes trial positive (FOURIER)"), ("Alirocumab", "CV outcomes trial positive (ODYSSEY OUTCOMES)"),
                        ("Inclisiran", "siRNA, twice-yearly dosing")]:
        E[name] = ev("opentargets", f"drug:{name}", f"{name}: PCSK9 inhibitor (approved in this indication)",
            f"{name} acts on PCSK9; approved for hypercholesterolemia incl. heterozygous FH. {extra}.",
            "record", ["pipeline", "competitive-landscape", "scientific-evidence", "green-flags"],
            "https://platform.opentargets.org/target/ENSG00000169174",
            data={"drug": name, "max_stage": "APPROVAL", "stage_in_indication": "APPROVAL", "role": "same_target"}, snapshot=True)
    E["t_fourier"] = trial("NCT01764633", "FOURIER: evolocumab cardiovascular outcomes", "Evolocumab", "COMPLETED", None, None,
                           ["PHASE3"], "2013-02-01", "2013-01-08", 27564, "Major cardiovascular events", "same_target", "Amgen")
    E["soc"] = ev("opentargets", "soc:statins", "Statins / ezetimibe (approved, generic)",
        "High-intensity statins ± ezetimibe are standard of care; a substantial share of FH patients do not reach LDL-C goals.",
        "record", ["unmet-need", "competitive-landscape"], "https://platform.opentargets.org/disease/MONDO_0005439",
        data={"role": "approved"}, snapshot=True)
    E["bench"] = ev("clinicaltrials", "benchmarks:MONDO_0005439", "Trial benchmarks: Ph2/3 interventional trials in familial hypercholesterolemia",
        "Computed from 140 Ph2/3 interventional trials. PHASE2: median enrollment 90, median duration 14 mo, termination rate 0.10; "
        "PHASE3: median enrollment 300 (CV-outcomes trials: >10,000), median duration 24 mo, termination rate 0.08. "
        "Common primary endpoint: percent change in LDL-C at week 12/24.",
        "record", ["trial-design"], "https://clinicaltrials.gov/search?cond=familial+hypercholesterolemia",
        data={"benchmark": True, "synthetic": True, "by_phase": {
            "PHASE2": {"n": 80, "enrollment_median": 90, "duration_months_median": 14, "termination_rate": 0.10},
            "PHASE3": {"n": 60, "enrollment_median": 300, "duration_months_median": 24, "termination_rate": 0.08}}})
    E["oral"] = ev("pubmed", "synth:oralpcsk9", "Oral PCSK9 inhibitors in late-stage development (review)",
        "Several oral macrocyclic PCSK9 inhibitors are in phase 3; differentiation for a new PCSK9 agent will hinge on route, "
        "dosing interval and price. [synthetic placeholder]", "literature", ["competitive-landscape", "market-sentiment"],
        pubmed_search("oral PCSK9 inhibitor"), date(2025, 6, 1), data={"synthetic": True})
    E["c1"] = ev("reconciliation", "validated:pcsk9", "Evolocumab (same target) is approved in familial hypercholesterolemia",
        "Same-target drugs reached approval in this indication — human proof-of-mechanism (and incumbent competitors).",
        "record", ["green-flags", "competitive-landscape", "scientific-evidence"], E["Evolocumab"]["url"],
        data={"rule": "validated_in_indication", "severity": "info", "kill_signal": False}, related=[E["Evolocumab"]["id"]])
    return subject, list(E.values()), {}


# ============================================================================ cutoff + assembly


STATIC_GAPS = [
    {"question": "Is the efficacious exposure achievable safely in humans for this specific asset?", "requires": "experiment",
     "reason": "Needs asset-level PK/PD and tox data."},
    {"question": "Is target engagement demonstrated in the relevant diseased tissue?", "requires": "proprietary_data",
     "reason": "Tissue-level engagement data is rarely public."},
    {"question": "What is the IP position and CMC/manufacturability of the asset?", "requires": "cmc_ip",
     "reason": "Input is mechanism-level; no specific asset."},
    {"question": "What are realistic net prices / payer access?", "requires": "kol", "reason": "No open pricing source."},
]


def apply_cutoff(evidence: list[dict], cutoff: date) -> list[dict]:
    out = []
    for e in evidence:
        if e["data"].get("_snapshot") or e["kind"] == "conflict":
            continue  # snapshots and conflicts derived from post-cutoff facts are not knowable
        pub = date.fromisoformat(e["published_at"]) if e["published_at"] else None
        if pub and pub > cutoff:
            continue
        if e["source"] == "clinicaltrials" and e["data"].get("status") in {"TERMINATED", "COMPLETED"}:
            e = {**e, "data": {**e["data"], "status": "ONGOING_AT_CUTOFF", "why_stopped": None, "stop_class": None,
                               "enrollment": None, "masked_by_cutoff": True},
                 "modules": [m for m in e["modules"] if m != "red-flags"]}
            e["snippet"] = e["snippet"].split(", ")[0] + ", ONGOING_AT_CUTOFF. [Outcome masked: not public at evidence cutoff.]"
        out.append(e)
    return out


def bundle(run_id, indication, mechanism, subject, evidence, cutoff=None) -> EvidenceBundle:
    policy = []
    if cutoff:
        evidence = apply_cutoff(evidence, cutoff)
        subject = {**subject, "drugs": [{**d, "max_stage": None, "synonyms": [s for s in d["synonyms"] if s.upper() == s or any(c.isdigit() for c in s)]}
                                        for d in subject["drugs"] if d["name"] in {"Secukinumab", "Brodalumab"}]}
        policy = [f"evidence_cutoff={cutoff}: only facts public on/before this date.",
                  "Open Targets / ChEMBL / FDA label snapshots excluded; CT.gov outcomes after cutoff masked (ONGOING_AT_CUTOFF)."]
    for e in evidence:
        e["data"].pop("_snapshot", None)
    data = {
        "schema_version": "1.0", "run_id": run_id, "created_at": NOW.isoformat(), "is_fixture": True,
        "input": {"indication": indication, "mechanism": mechanism, "evidence_cutoff": cutoff.isoformat() if cutoff else None},
        "subject": subject, "evidence": evidence,
        "trial_benchmarks": next((e["data"] for e in evidence if e["data"].get("benchmark")), {}),
        "gaps": STATIC_GAPS, "cutoff_policy": policy,
        "source_status": [{"source": s, "ok": True, "evidence_count": sum(e["source"] == s for e in evidence)}
                          for s in sorted({e["source"] for e in evidence})],
    }
    return EvidenceBundle.model_validate(data)


def main() -> None:
    import copy
    s, ev_now, _ = il17_crohn()
    outputs = {
        "il17_crohn_now.json": bundle("fixture-il17-crohn-now", "Crohn's disease", "IL-17 inhibition", s, copy.deepcopy(ev_now)),
        "il17_crohn_2011.json": bundle("fixture-il17-crohn-2011", "Crohn's disease", "IL-17 inhibition", s,
                                       copy.deepcopy(ev_now), cutoff=date(2011, 6, 30)),
    }
    s2, ev2, _ = pcsk9()
    outputs["pcsk9_hypercholesterolemia.json"] = bundle("fixture-pcsk9", "hypercholesterolemia", "PCSK9 inhibition", s2, ev2)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, b in outputs.items():
        (OUT / name).write_text(b.model_dump_json(indent=2), encoding="utf-8")
        print(f"{name}: {len(b.evidence)} evidence, {len(b.conflicts())} conflicts")


if __name__ == "__main__":
    main()
