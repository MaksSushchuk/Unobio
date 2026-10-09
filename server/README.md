# Unobio — server

Backend of Unobio: turns an *indication + mechanism* pair into an investment underwriting report.

```
                 researcher Bundle            EvidenceBundle              AnalysisResult              Report JSON
 web form ─► researcher ─────────────► adapter ──────────────► analytics ───────────────► app.report ───────────► web
 (POST /api/runs)  (public APIs)   evidence_bundle/adapters.py   (4 lenses + rules)            │
                                                                                               └─► writer ─► report.pdf
                                         app/  (pipeline, CLI, HTTP API with live progress events)
```

| Folder | Owner | Responsibility |
|---|---|---|
| `app/` | Oleksii | **End-to-end glue**: pipeline, CLI (`python -m app`), HTTP API for the web UI, report + writer adapters |
| `evidence_bundle/` | shared | Contract researcher → analytics (`models.py`), **adapter from the researcher format** (`adapters.py`), data paths, fixtures generator, `examples/` (researcher's hand-made bundle) |
| `researcher/` | teammate-2 | Collects evidence from public APIs (`researcher/schema.py` Bundle) — see `CLAUDE.md` |
| `analytics/` | Oleksii | Orchestrator, analyst agents, validator, verdict, capital / rNPV → `analysis_result.json` |
| `writer/` | teammate-1 | Separate package `writer_agent`: PDF report from its own input contract (`writer/docs/INPUT_SCHEMA.md`) |
| `sceptic/` | — | Challenges the analysts' claims (if time allows) |
| `tests/` | all | Contract, module and end-to-end tests |
| `data/` | — | **Everything that is not code** (see below) |

## How the modules are connected

Each module keeps its own contract; adapters translate between them, so nobody has to change
their module when another one evolves:

| From → to | Adapter | What it does |
|---|---|---|
| researcher → analytics | `evidence_bundle/adapters.py` (`from_researcher`) | subject/drugs/targets mapping; trial `status`, `stop_class`, `role`; reconcile rules → conflicts with `severity` / `kill_signal`; coverage → gaps; trial benchmarks from the bundle's trials. `load_bundle()` detects the format itself |
| analytics → web | `app/report.py` (`build_report`) | `Report` exactly as in `web/src/types.ts` (+ `details`: rule trace, rNPV, cutoff) |
| analytics → writer | `app/writer_input.py` (`build_writer_input`) | writer input JSON (`thesis`, `recommendation`, `analysts[]`, `sources[]`, `panels`) |
| one LLM for all | `app/llm_bridge.py` | researcher (planner, stop classifier) and writer (narrative) use the analysts' client — Gemini / Ollama / OpenAI from `.env`, with its cache, rate-limit handling and traces (idea from the v1.0.0 orchestrator) |
| claim → sources | `app/sources.py` | a claim citing a derived item (reconcile rule, conflict) is expanded to the primary records behind it, so the PDF / Markdown reference list shows the actual trials and papers (NCT / PMID ids) |

## Data layout — `data/`

All files the system reads or writes live under `data/` (override with `UNOBIO_DATA_DIR`;
paths are defined once in `evidence_bundle/paths.py`).

```
data/
  fixtures/          hand-made evidence bundles for offline development/demo   (committed)
  schema/            JSON Schemas generated from the Pydantic models           (committed)
  runs/<run_id>/     one folder per run                                       (git-ignored)
    run.json                 request
    researcher_bundle.json   researcher output (its own format)
    evidence_bundle.json     after the adapter (analytics contract)
    analysis_result.json     analytics output
    report.json              what the web UI shows
    report.md                Markdown export (claims with numbered sources)
    writer_input.json, report.pdf   writer step (on by default; UNOBIO_PDF=0 skips it)
    trace.jsonl              one line per step / LLM call
    context/ prompts/ lenses/   analytics debug: what each analyst saw and answered
  cache/             LLM response cache (llm.sqlite)                          (git-ignored)
```
The researcher keeps its own HTTP cache in `server/.cache/` (git-ignored).

## Setup

```bash
cd server
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"            # researcher + analytics + app
uv pip install -e ./writer            # optional: PDF report
cp .env.example .env                  # GEMINI_* (researcher), LLM_* (analysts), WRITER_* (PDF), UNOBIO_*
```

## Run the whole system

```bash
# 1) one run from the command line
python -m app run "Crohn's disease" "IL-17 inhibition"                       # live public APIs
python -m app run "Crohn's disease" "IL-17 inhibition" --cutoff 2009-01-01   # evidence as known then
python -m app run "Crohn's disease" "IL-17 inhibition" --source fixture      # offline demo data
python -m app run "x" "y" --bundle evidence_bundle/examples/il17_crohns.json # a saved bundle
python -m app run ... --no-pdf                                               # skip the writer (no narrative, no PDF)

# 2) web UI: backend + frontend in two terminals
python -m app serve                    # http://127.0.0.1:8000  (API under /api)
cd ../web && npm run dev               # http://localhost:5173  (proxies /api to the backend)
```
Without a running backend the web UI falls back to its simulated run on fixtures.

| Variable | Default | Meaning |
|---|---|---|
| `UNOBIO_RESEARCH` | `live` | `live` = public APIs, `fixture` = `data/fixtures` (offline demo) |
| `UNOBIO_PDF` | `1` | writer step on every run: narrative texts + `report.pdf` (same LLM as the analysts; needs `uv pip install -e ./writer`) |
| `LLM_PROVIDER` | `fake` | analysts' model: `fake` (offline stand-in) / `gemini` (uses `GEMINI_API_KEY` + `GEMINI_MODEL`, like the researcher) / `ollama` / `openai` |
| `LLM_CACHE` | `1` | `0` = do not reuse cached LLM answers (`data/cache/llm.sqlite`); same request = same answer otherwise |

**Evidence as of a date** (`--cutoff`, web field *Evidence as of*): everything published later is dropped, and
ClinicalTrials.gov records are rebuilt as they stood then (a trial counts from its first posting; a trial still
running then shows no later stop or results). Text inputs are normalized (typographic quotes and dashes → ASCII).

## HTTP API (`app/server.py`)

| Method | Path | Returns |
|---|---|---|
| POST | `/api/runs` `{input, options:{evidence_cutoff?, source?, pdf?}}` | `{run_id}` (run starts in the background) |
| GET | `/api/runs/{id}` | request (input, options), progress, outcome — the home screen reopens a run from it |
| GET | `/api/runs/{id}/events` | Server-Sent Events: `{seq, step, status, payload}` …, then `event: end` |
| GET | `/api/runs/{id}/report` | `Report` JSON (web contract) |
| GET | `/api/runs/{id}/report.pdf` | PDF (generated on first request if the run had none) |
| GET | `/api/runs/{id}/report.md` | Markdown report with numbered sources per claim |
| GET | `/api/runs/{id}/files.zip` | the whole run folder |
| GET | `/api/runs` | past runs, newest first |
| GET | `/api/health` | configured LLM provider / research mode |

## Other commands

```bash
pytest -m "not live and not llm"                    # offline tests (all modules + end-to-end)
pytest                                              # + live API tests (network)
python -m analytics.llm                             # check the analysts' model
python -m analytics data/fixtures/il17_crohn_now.json   # analytics only
python -m researcher "Crohn's disease" "IL-17 inhibition"   # researcher only
python -m evidence_bundle.fixtures                  # regenerate data/fixtures
python -m evidence_bundle.export_schema             # regenerate data/schema
```

## Module interaction rules

1. **Modules talk only through JSON contracts**, never through each other's internals. Format differences are fixed in the adapters (table above), not inside the modules.
2. **One run = one folder** `data/runs/<run_id>/`; get it with `evidence_bundle.run_dir(run_id)`.
3. **Changing a contract** means updating the adapter and its test (`tests/test_integration.py`, `tests/test_app.py`) and telling the team.

## Conventions

- All code, comments and docs are in English.
- Python ≥ 3.11, Pydantic v2, type hints everywhere.
