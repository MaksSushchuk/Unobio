# server/

Backend of Unobio. Start with `README.md` (layout, data folders, commands); each module documents itself:
`researcher/README.md`, `analytics/README.md`, `writer/README.md`, `evidence_bundle/README.md`.

## Workflow
`python -m orchestrator "<indication>" "<mechanism>"` runs input -> researcher -> analytics -> writer and writes
`data/runs/<run_id>/report.json`, the Report of `web/src/types.ts`. The code is `orchestrator/`:

| File | Role |
|---|---|
| `pipeline.py` | `async run(inp, bundle=None, settings=None, options=None, pdf=False, on_event=None) -> Report` |
| `llm_bridge.py` | one LLM client (analytics' `LLM_*` layer) behind researcher's and writer's sync LLM interfaces |
| `bundle_adapter.py` | researcher `Bundle` -> analytics `EvidenceBundle` (subject, trial fields, conflicts + kill signals, benchmarks, gaps) |
| `report_builder.py` | `AnalysisResult` -> writer input; `AnalysisResult` + writer narrative -> Report |
| `report_schema.py` | pydantic mirror of `web/src/types.ts` (extra="forbid"); the web fixtures are tested against it |

## Rules
- Modules talk through their contracts; mismatches are fixed by thin adapters in `orchestrator/`, not by editing
  another module's internals.
- `report_schema.py` must match `web/src/types.ts` exactly; change both together.
- Kill signals (forced Do Not Invest) come only from `bundle_adapter.py`: reconcile R4 / R5 on
  subject-mechanism drugs, and never when a subject-mechanism drug is already approved in this indication.
  Document any change to that rule there.
- One LLM for every module in a workflow run: `LLM_*` in `server/.env`. Currently Gemini through its
  OpenAI-compatible API (`LLM_PROVIDER=openai`); the company Ollama (10.0.1.24, VPN) is kept commented out.
  `--fake` / `LLM_PROVIDER=fake` runs everything offline with stand-in answers (not analysis).
- All run files go to `data/runs/<run_id>/` (`evidence_bundle.paths`), caches to `data/cache/`.
- Tests: `pytest -m "not live and not llm"` offline; `tests/test_orchestrator.py` covers the workflow.
