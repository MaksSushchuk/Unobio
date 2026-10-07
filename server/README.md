# Unobio — server

Backend of Unobio: turns an *indication + mechanism* pair into an investment underwriting result.

```
            ┌────────────┐  evidence_bundle.json  ┌────────────┐  analysis_result.json  ┌──────────┐
 input ───► │ researcher │ ─────────────────────► │ analytics  │ ─────────────────────► │  writer  │ ──► report.json ──► web
            └────────────┘                        └─────┬──────┘                        └──────────┘
                                                        │ (later)
                                                   ┌────▼────┐
                                                   │ sceptic │
                                                   └─────────┘
```

| Folder | Owner | Responsibility |
|---|---|---|
| `evidence_bundle/` | shared | **Contract** researcher → analytics (Pydantic models), data paths (`paths.py`), fixture generator |
| `researcher/` | teammate-2 | Collects evidence from public APIs, writes `evidence_bundle.json` |
| `analytics/` | Oleksii | Orchestrator, analyst agents, validator, verdict, capital / rNPV → `analysis_result.json` |
| `sceptic/` | — | Challenges the analysts' claims (if time allows) |
| `writer/` | teammate-1 | Turns `analysis_result.json` into report text `report.json` (contract: `web/src/types.ts`) |
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
    evidence_bundle.json    ← researcher
    analysis_result.json    ← analytics
    report.json             ← writer
    trace.jsonl             ← everyone (one line per step / LLM call)
    context/                ← analytics debug: what each analyst saw
  cache/             HTTP / LLM response caches, e.g. llm.sqlite     (git-ignored)
```

## Module interaction rules

1. **Modules talk only through JSON files that follow a contract**, never by calling each other's internals. This keeps them independent: analytics is developed on fixtures while researcher is still in progress.
2. **One run = one folder** `data/runs/<run_id>/`; get it with `evidence_bundle.run_dir(run_id)`, never build paths by hand.
3. **Changing a contract** means editing `evidence_bundle/models.py` (or `analytics/schemas.py`), regenerating the schema and telling the team.

## Setup and commands

```bash
cd server
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env                                  # LLM settings (provider: fake | ollama | openai)
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
