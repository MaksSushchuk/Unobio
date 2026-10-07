# researcher — evidence bundle from public APIs

Standalone Python module that collects biotech evidence from public APIs for an
(indication, mechanism) pair and writes an **evidence bundle** for downstream analysts.

Out of scope: orchestration (see `orchestrator/`), web servers, LLM analysis/synthesis. The LLM is used for two narrow tasks:
the search planner, which suggests search terms (everything it suggests is verified against public APIs),
and classifying trial stop reasons in reconcile.py (one batched call per run; keyword classifier as fallback).

## Stack
Python 3.11+, httpx, pydantic v2, pytest, google-genai (Gemini), python-dotenv.

Config: standalone (`python -m researcher`) uses Gemini: `GEMINI_API_KEY`, `GEMINI_MODEL` in `server/.env`.
Without a key everything still runs; the planner uses its rule-based fallback. Inside the full workflow
(`python -m orchestrator`) the orchestrator passes the shared model (`LLM_*` settings) through the same
`LLM` protocol instead (`orchestrator/llm_bridge.py`).

```
cd server
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
python -m researcher "Crohn's disease" "IL-17 inhibition"   # -> data/runs/<run_id>/researcher_bundle.json
pytest                                                       # includes live API tests
pytest -m "not live and not llm"                             # offline only
python -m researcher "..." "..." --no-llm                    # skip the planner LLM
```

## Structure
```
server/
  data/runs/<run_id>/researcher_bundle.json   # output (gitignored); data/cache/http_cache.db  HTTP cache
  evidence_bundle/examples/il17_crohns.json   # hand-made example bundle (is_example: true); validates against researcher/schema.py
  researcher/              # all agent logic (the Python package); no tests, config or results here
    schema.py              # pydantic models: ResearchInput, Subject, Drug, Evidence, SourceStatus, CoverageItem,
                           #   SearchPlan, LlmCall, Bundle
    __main__.py            # CLI: python -m researcher "<indication>" "<mechanism>" [--modality ...]
    pipeline.py            # run_research(input) -> Bundle; write_bundle()
    http.py                # the ONLY place that does HTTP (except the LLM SDK, see llm.py)
    llm.py                 # LLM protocol + GeminiLLM: generate_json(prompt, schema), 429 retry, call log
    planner.py             # plan(input) -> SearchPlan (LLM suggestion, rule-based fallback)
    resolve.py             # resolve(input, plan) -> Subject: verify plan targets/disease/drugs against
                           #   Open Targets / ChEMBL, add database-known drugs, explain in resolution_notes
    reconcile.py           # reconcile(bundle, llm): dedupe + merge modules, cross-source drug linking,
                           #   evidence_cutoff + leak guard, then rules R1-R5 -> derived evidence
                           #   (source "reconcile"); stats in Bundle.reconcile_stats
    coverage_checklist.py  # CHECKLIST of underwriting questions (data, editable) + evaluate_coverage(bundle);
                           #   runs last. covered = answerable from the bundle (answer in note)
    connectors/
      base.py              # Connector protocol
      <source>.py          # one module per public source (clinicaltrials, pubmed, chembl, opentargets, openfda, ...)
  tests/
    test_<source>.py       # live test per connector
```
The researcher `Bundle` is converted to the analytics contract (`evidence_bundle/models.py`) by
`orchestrator/bundle_adapter.py`; keep that adapter in sync when changing `schema.py`.

## Rules
- **LLM access goes only through `researcher/llm.py`** (`LLM` protocol; `GeminiLLM` uses the google-genai
  SDK, which does its own HTTP). Calls use temperature 0 and structured JSON output validated by a
  pydantic model, and every call is recorded in `Bundle.llm_calls`. LLM code must not raise into the
  pipeline: callers catch `LlmError` and fall back.
- **The SearchPlan is a suggestion.** Never put an LLM-suggested target or drug into `Subject` or
  evidence without first verifying it against Open Targets / ChEMBL (resolve.py).
- **All HTTP goes through `researcher/http.py`** (`HttpClient`). It provides a SQLite cache keyed by
  method + URL + params (+ JSON body for POST), a per-source rate limit (`RATE_LIMITS`), retry with
  exponential backoff on 429/5xx/transport errors (honours `Retry-After`), and timeouts.
  Never import httpx/requests/urllib in a connector.
- **Every connector implements `fetch(subject: Subject) -> list[Evidence]` and never raises.**
  Errors are caught and recorded as a `SourceStatus` entry in `bundle.sources_status`
  (`ok=False`, `error=...`). Partial results are fine; crashing the run is not.
- **Evidence ids are deterministic:** `sha1(f"{source}:{native_id}")[:16]` — use
  `researcher.schema.evidence_id(source, native_id)`. Same record ⇒ same id across runs.
- **Every connector has a pytest test hitting the real API** for indication `"Crohn's disease"` +
  mechanism `"IL-17 inhibition"`. Mark these tests `@pytest.mark.live`.
- **Never invent API response fields.** Before reading a field, check a real response
  (curl it or inspect the cached body). Put raw-ish source fields in `Evidence.data` under the
  names the API uses.
- `Evidence.modules` must use only the 11 module names in `schema.Module`.
- `kind="conflict"` evidence links the disagreeing items via `related_evidence_ids`
  (and `data.supporting` / `data.contradicting`).
- Respect `ResearchInput.evidence_cutoff`: drop evidence published after it.
  Evidence with `published_at=None` is never dropped. Open Targets records are not dated per record
  (`opentargets` evidence always has `published_at=None`), so the cutoff does not filter them; they reflect
  the current platform release.
  Exception (leak guard, reconcile.py): an undated record that states clinical status (stage, trial
  phases/statuses) is dropped under a cutoff unless every trial it cites has a ClinicalTrials.gov record
  in the bundle dated on or before the cutoff and it cites no undated non-trial reports.
- **Modality and drug relation** (resolve.py). `SearchPlan.modality` is one of small_molecule, antibody,
  bispecific, adc, cell_therapy, gene_therapy, oligonucleotide, other, unspecified (planner: from the mechanism
  text / `ResearchInput.modality`; regex fallback without the LLM). `Drug.drug_type` is Open Targets `drugType`,
  else ChEMBL `molecule_type` when Open Targets says Unknown. Drug modality: type map (Small molecule ->
  small_molecule, Antibody -> antibody, Antibody drug conjugate -> adc, Cell -> cell_therapy, Gene ->
  gene_therapy, Oligonucleotide -> oligonucleotide, Protein/Enzyme -> protein, Oligosaccharide -> other);
  Unknown -> WHO INN stem (-mab antibody, -cel cell_therapy, -gene/-vec gene_therapy, -rsen/-siran
  oligonucleotide), else unknown; type Gene with a -cel stem is cell_therapy (CAR-T are typed Gene); an
  antibody with `other_target_symbols` is bispecific. `Drug.relation` = "subject_mechanism" when
  plan.modality is unspecified, the drug modality is unknown (not demoted without evidence), or it is in
  `MODALITY_COMPATIBLE[plan.modality]` (antibody accepts antibody, bispecific, protein; other accepts other,
  protein; every other modality accepts only itself); else "same_target_other_modality". All subject drugs
  share the subject target family by construction, so "same target" holds for both values.
- **Trial drug links** (clinicaltrials.match_subject_drugs, re-applied in reconcile.py): `entity_refs.drug`
  is set only when a subject drug name/synonym is in the trial's interventions (name or otherNames), or in the
  title when no intervention is a product (DRUG / BIOLOGICAL / GENETIC / COMBINATION_PRODUCT).
  `entity_refs.drug == data.subject_drug_chembl_ids[0]` always; `data.drug_relation` is that drug's relation;
  `data.found_via` is the query that found the trial (drug name or "landscape") and is not a link. Coverage
  reports trial counts separately for subject_mechanism / same_target_other_modality / no subject drug.
- **Stop reasons** (reconcile.py R1): `data.stop_category` in efficacy | safety | business | enrollment |
  positive_early_stop | other (+ `stop_category_source` llm | keyword), or "unknown" (source "none") when
  there is no why_stopped: such trials get no red-flags module, and `data.stop_note` = "results posted — check
  outcome" if has_results. positive_early_stop -> green-flags instead of red-flags.
- `CoverageItem.answer` (yes / no / unknown; null for not_public) is the answer, separate from `status`.
- Agent code lives only in `researcher/`; tests, config, cache and results stay outside it.
- Keep `evidence_bundle/examples/il17_crohns.json` valid when changing the schema (`tests/test_schema.py` checks it).
