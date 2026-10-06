# Writer agent

Turns the analysts' and Skeptic's JSON output into an A4 underwriting report (PDF),
following the layout of the report screen in the UI.

```
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/python -m writer_agent run.json -o report.pdf       # prints the WriterResult as JSON
.venv/bin/python scripts/render_sample.py -o sample.pdf        # offline sample from the fixture (canned model replies)
.venv/bin/python scripts/render_sample.py --live -o sample.pdf # same, but calls the real model
.venv/bin/python -m pytest                                     # tests use a stub model, no network
```

## Interface

```python
from writer_agent import WriterAgent, WriterConfig

result = WriterAgent(WriterConfig()).run("run.json", "out/report.pdf")   # dict or path
result.status        # "ok" | "degraded" (a model step failed, PDF still produced) | "error"
result.pdf_path, result.page_count, result.recommendation, result.confidence,
result.sections, result.warnings, result.message
```

`run()` never raises. Input that cannot produce any report (missing
`thesis.indication`/`thesis.mechanism`, unreadable JSON) returns `status="error"`.

## Model

Self-hosted model behind an Ollama-compatible API (`POST /api/chat`, `format: "json"`),
called with the standard library only (`writer_agent/llm.py`). Configuration
(`writer_agent/config.py`) is read from the environment:

| Variable | Default |
|---|---|
| `WRITER_LLM_URL` | `http://10.0.1.24:11434` |
| `WRITER_LLM_MODEL` | `llama3.1:8b` (**set this to the deployed model**) |
| `WRITER_LLM_TIMEOUT` | `180` s |
| `WRITER_LLM_NUM_CTX` | `8192` |
| `WRITER_MAX_PROMPT_CHARS` | `20000` (payload size per call; larger input is truncated for the model only) |

Model calls: one per section (analysts + Skeptic) for takeaway and synthesis,
plus one final call for the rationale, risks, critical unknowns, diligence questions
and (only when no upstream verdict exists) the verdict. Replies are parsed
defensively (code fences, surrounding prose) and retried once if invalid.

## What is copied and what is written

| Copied verbatim from input | Written by the model |
|---|---|
| claim text, confidence, claim type, evidence counts, sources | section takeaway + synthesis |
| verdict + confidence (when upstream supplies them) | recommendation rationale |
| capital to milestone (all figures and assumptions) | risks, critical unknowns, diligence questions |
| data panels (appendix) | verdict, only when upstream supplies none |

After generation every number in model-written text is checked against the input
JSON (`writer_agent/numbers.py`); unmatched numbers are returned as warnings and
listed in the PDF's "Report QA notes". Claim ids the model cites that do not
exist in the input are dropped with a warning.

## Degradation

- Missing analyst / analyst with no claims / no Skeptic → warning; report built from the rest.
- Section model call fails → that section's claims printed without narrative; status `degraded`.
- Final synthesis fails → PDF still produced without rationale/risks/unknowns/questions; status `degraded`.
- Input too large for the model → model payload truncated (excerpts first, then lowest-confidence claims; claims with contradicting evidence are kept), warning. The PDF always prints all claims.

## Input

See [docs/INPUT_SCHEMA.md](docs/INPUT_SCHEMA.md). The contract is **provisional**: the real
analyst / Skeptic output schema was not available, so all field names live in
`writer_agent/schema.py` and can be remapped there.
