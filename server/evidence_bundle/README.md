# evidence_bundle — contract researcher → analytics

**One JSON file per run.** Researcher writes it, analytics reads it.

| What | Where |
|---|---|
| Model (source of truth) | `models.py` |
| JSON Schema (other languages / validation) | `data/schema/evidence_bundle.schema.json`, generated from `models.py` |
| Development fixtures | `data/fixtures/*.json`, generator `fixtures.py` |
| All data locations | `paths.py` (`FIXTURES_DIR`, `SCHEMA_DIR`, `RUNS_DIR`, `CACHE_DIR`, `run_dir()`) |
| Reading in code | `from evidence_bundle import load_bundle` (reads and validates) |

## Shape (abridged)

```jsonc
{
  "schema_version": "1.0",
  "run_id": "…",
  "is_fixture": false,
  "input":   { "indication": "Crohn's disease", "mechanism": "IL-17 inhibition", "evidence_cutoff": null },
  "subject": {                       // what exactly we are looking at
    "disease": { "id": "MONDO_0005011", "name": "Crohn disease", "synonyms": [] },
    "targets": [ { "ensembl_id": "ENSG…", "symbol": "IL17A", "role": "primary" } ],
    "action":  "inhibitor",
    "drugs":   [ { "name": "Secukinumab", "synonyms": ["AIN457","Cosentyx"], "target_symbols": ["IL17A"], "match": "target" } ]
  },
  "evidence": [                      // the core: atomic, citable facts
    {
      "id": "7d1e…",                 // stable; claims cite it
      "source": "clinicaltrials",    // opentargets | clinicaltrials | pubmed | openfda | chembl | web | reconciliation
      "url": "https://clinicaltrials.gov/study/NCT01150890",
      "title": "…", "snippet": "…",  // short text for humans and the LLM
      "kind": "record",              // record | literature | web | conflict
      "modules": ["pipeline","trial-design","red-flags"],   // 11 Unobio modules, used to route evidence to analysts
      "published_at": "2010-06-25",
      "related_evidence_ids": [],    // for conflicts: the evidence it reconciles
      "data": { "status": "TERMINATED", "stop_class": "safety" }
    }
  ],
  "trial_benchmarks": { "by_phase": { "PHASE2": { "enrollment_median": 120, "duration_months_median": 26 } } },
  "gaps":   [ { "question": "…", "requires": "experiment", "reason": "…" } ],
  "cutoff_policy": [],
  "source_status": [ { "source": "pubmed", "ok": true, "evidence_count": 8 } ]
}
```

## Agreements analytics relies on

- **Researcher does not judge.** No scores, no verdict: only facts, `modules` tags and reconciliation.
- **A cross-source contradiction is evidence too** (`kind: "conflict"`). Required `data` fields:
  - `rule` — e.g. `stopped_for_cause`, `mechanism_not_transferring`, `label_warning`;
  - `severity` — `high | medium | low | info`;
  - `kill_signal` — `true` if a drug on the same target already failed in the same disease.
- **Trials** carry in `data`: `status`, `why_stopped`, `stop_class` (`safety | efficacy | business | enrollment | other`), `phases`, `enrollment`, `role` (`same_target | landscape`).
- **Trial benchmarks** are an evidence item with `data.benchmark = true`, also copied into `trial_benchmarks`.
- **`evidence_cutoff`**: researcher drops everything published after the date and rebuilds ClinicalTrials.gov records as
  of the cutoff (a trial counts from its first posting; a trial still running then has its later status, stop reason
  and results hidden, and reaches analytics as `status: ONGOING_AT_CUTOFF`). Analytics trusts the bundle and does not filter again.
- **Researcher format**: the live researcher writes its own `Bundle` (`researcher/schema.py`); `adapters.py`
  (`from_researcher`) maps it to this contract, and `load_bundle()` detects the format itself.
- **Extra fields are allowed** (`extra="allow"`), but analytics only relies on the fields documented here.

## Fixtures (`data/fixtures/`)

All marked `"is_fixture": true`.

| File | Scenario | Expected verdict |
|---|---|---|
| `il17_crohn_now.json` | IL-17 × Crohn's, all data | **Do Not Invest** (kill: secukinumab / brodalumab failures) |
| `il17_crohn_2011.json` | Same, as known on 2011-06-30: trials still "ongoing", no failures visible (outcomes masked by hand; live data shows secukinumab's stop from Aug 2010, so live runs use 2009-01-01) | **Conditional** |
| `pcsk9_hypercholesterolemia.json` | Validated mechanism, strong genetics, but approved competitors already exist | **Invest / Conditional** |

Key facts (NCT ids, `whyStopped`, FAERS counts, ChEMBL phases, Open Targets scores) were checked against the live APIs. Items with `data.synthetic = true` are plausible placeholders (paraphrased, URL = PubMed search): **fine for testing the pipeline, not for claims in the demo**.
