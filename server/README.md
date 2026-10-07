# Unobio — server

Backend of Unobio: turns an *indication + mechanism* pair into an investment underwriting result.

```
            ┌────────────┐ researcher_bundle ┌─────────┐ evidence_bundle ┌───────────┐ analysis_result ┌────────┐
 input ───► │ researcher │ ────────────────► │ adapter │ ──────────────► │ analytics │ ──────────────► │ writer │ ──► report.json ──► web
            └────────────┘                   └─────────┘                 └───────────┘                 └────────┘
                         all steps driven by orchestrator/ (python -m orchestrator), one LLM for every module
```

| Folder | Owner | Responsibility |
|---|---|---|
| `evidence_bundle/` | shared | **Contract** researcher → analytics (Pydantic models), data paths (`paths.py`), fixture generator |
| `researcher/` | teammate-2 | Collects evidence from public APIs, writes `evidence_bundle.json` |
| `analytics/` | Oleksii | Orchestrator, analyst agents, validator, verdict, capital / rNPV → `analysis_result.json` |
| `writer/` | teammate-1 | Narrative (section takeaways, rationale) and the PDF from the analysts' output |
| `orchestrator/` | — | End-to-end workflow, contract adapters, the final `report.json` (contract: `web/src/types.ts`) |
| `tests/` | all | Contract and module tests |
| `data/` | — | **Everything that is not code**: inputs, outputs, generated artifacts (see below) |

## Data layout — `data/`

Code folders contain only code. All files the system reads or writes live under `data/`
(override the location with `UNOBIO_DATA_DIR`; paths are defined once in `evidence_bundle/paths.py`).

```
data/
  fixtures/          hand-made evidence bundles for development      (committed)
  schema/            JSON Schemas generated from the Pydantic models  (committed)
  runs/<run_id>/     one folder per run                              (git-ignored)
    researcher_bundle.json  ← researcher (its own schema, researcher/schema.py)
    evidence_bundle.json    ← orchestrator adapter (contract for analytics)
    analysis_result.json    ← analytics
    report.json             ← orchestrator (web/src/types.ts Report; narrative by writer)
    report.pdf              ← writer (with --pdf)
    trace.jsonl             ← everyone (one line per step / LLM call)
    context/                ← analytics debug: what each analyst saw
  cache/             HTTP / LLM response caches: http_cache.db, llm.sqlite (git-ignored)
```

## Module interaction rules

1. **Modules talk only through JSON files that follow a contract**, never by calling each other's internals. This keeps them independent: analytics is developed on fixtures while researcher is still in progress.
2. **One run = one folder** `data/runs/<run_id>/`; get it with `evidence_bundle.run_dir(run_id)`, never build paths by hand.
3. **Changing a contract** means editing `evidence_bundle/models.py` (or `analytics/schemas.py`), regenerating the schema and telling the team.

## Setup and commands

```bash
cd server
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]" -e ./writer
cp .env.example .env                                  # LLM settings (provider: fake | ollama | openai)
python -m orchestrator "Crohn's disease" "IL-17 inhibition"          # full workflow -> data/runs/<id>/report.json
python -m orchestrator "Crohn's disease" "IL-17 inhibition" --pdf    # + report.pdf
python -m orchestrator "Crohn's disease" "IL-17 inhibition" --fake   # offline, no model
python -m analytics.llm                               # check the configured model
pytest                                                # all tests
python -m evidence_bundle.fixtures                    # regenerate data/fixtures
python -m evidence_bundle.export_schema               # regenerate data/schema
python -m analytics data/fixtures/il17_crohn_now.json           # full analytics run -> data/runs/<id>/analysis_result.json
python -m analytics.context data/fixtures/il17_crohn_now.json   # inspect analyst contexts only
```

## Conventions

- All code, comments and docs are in English.
- Python ≥ 3.11, Pydantic v2, type hints everywhere.
