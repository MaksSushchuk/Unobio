# analytics — orchestrator and analyst agents

**Input:** `evidence_bundle.json` (contract: `evidence_bundle/models.py`).
**Output:** `analysis_result.json` (contract: `analytics/schemas.py`), consumed by writer and the UI.

## Glossary

| Term | Meaning in this project |
|---|---|
| **Orchestrator** | Plain Python code. Knows the order of steps, starts agents, gives them their input, checks their output. It does not "think" |
| **Agent** | One LLM call with a role: instructions (system prompt) + its slice of evidence + an answer schema → JSON. No memory between calls |
| **Context** | Everything sent in one agent call. Limited by the model's window and paid per token, so each agent gets only what it needs |
| **Lens** | An analyst's point of view: `science`, `clinical`, `market`, `investment`. One lens = one agent |
| **Claim** | A statement by an analyst that cites evidence ids, is typed `source_fact` or `inference`, and has a confidence |
| **Validator** | Code that checks an agent's answer: format, citations point to real evidence, required fields present |

## Flow

```
evidence_bundle.json
   │
1. Load and validate the contract ............................ code
2. Build a context per lens .................................. code   ← analytics/context
3. 4 analysts in parallel (science/clinical/market/investment)  LLM
4. Validator → retry only the failing lens (≤2) .............. code
5. (later) Sceptic → re-run the challenged lenses ............ LLM
6. Verdict: kill rule + score thresholds ..................... code
7. Capital / rNPV from trial benchmarks ...................... code
8. Risks, unknowns → diligence questions ..................... code
9. Save analysis_result.json + traces
```

The LLM is used only in steps 3 and 5, each time with a narrow task and a bounded context.

## Context (step 2) — `analytics/context/`

Turns the bundle into one compact text context per lens. No LLM; deterministic.

```
bundle ─► route ─► tier ─► aggregate ─► budget ─► render ─► RunContext
```

| Module | What it does |
|---|---|
| `routing.py` | Which lens sees which evidence. Each lens owns Unobio modules; `red-flags` goes to every lens; investment also sees pipeline / competition as one-liners |
| `short_ids.py` | Global, deterministic short ids `E1…En` (and `A1…` for aggregates) for prompts; maps back to full evidence ids |
| `priority.py` | Tiers: **P0** must-see (conflicts + lens anchors, never dropped), **P1** own-module evidence (full card), **P2** shared/secondary evidence (one line), **P3** dropped by budget (ids listed) |
| `cards.py` | Text rendering: full card, one-line card, aggregate card, shared subject header |
| `builder.py` | `ContextBuilder.build(bundle) → RunContext`: fills each lens up to `budget_tokens`, downgrading full → one-line before dropping |

Inspect what each analyst would see:

```bash
python -m analytics.context data/fixtures/il17_crohn_now.json
python -m analytics.context data/fixtures/il17_crohn_now.json --budget 1200   # simulate a small model
```

Files are written to `data/runs/<bundle_run_id>/context/` (`header.md`, `<lens>.md`, `id_map.json`).

Design choices:
- **Header is shared** and identical across lenses, so providers that cache prompt prefixes charge for it once.
- **Must-see evidence is never dropped.** If it alone exceeds the budget, `p0_overflow = true` is reported instead.
- **Aggregates** (≥ 5 homogeneous items, e.g. approved therapies) are one card; citing `A1` resolves to its first
  `MAX_AGGREGATE_MEMBERS` (5) members (`lenses/mapping.py`), so a claim never cites a hundred landscape trials.
- **Nothing is hidden silently**: ids that did not fit are listed under "NOT SHOWN", so an agent can request them via `need_detail`.

## LLM layer (step 3) — `analytics/llm/`

One interface for every model: `await llm.complete(LLMRequest) -> LLMResponse` (text, tokens, latency, cost).
Agents never know which provider is behind it; switching model = editing `.env`.

```
agent ─► ResilientLLM ─► cache hit? ─► return stored answer (free, identical)
                  │
                  ├─► concurrency limit (LLM_MAX_CONCURRENCY)
                  ├─► provider: Ollama (native /api/chat) | Gemini (native generateContent) | OpenAI-compatible | FakeLLM
                  └─► retry with backoff on network errors, 429, 5xx (not on 4xx)
```

| Module | What it does |
|---|---|
| `base.py` | `LLMRequest`, `LLMResponse`, `LLMClient` protocol, `LLMError` |
| `config.py` | `LLMSettings.from_env()` — reads `LLM_*` variables (and `server/.env`); price → cost per call |
| `providers.py` | `OllamaProvider` (sets `num_ctx` per request, JSON Schema via `format`, detects silent truncation), `GeminiProvider` (`GEMINI_API_KEY` / `GEMINI_MODEL`, waits on 429 `retryDelay`), `OpenAICompatProvider` (Groq, OpenRouter, OpenAI, vLLM, ...) |
| `fake.py` | `FakeLLM` — scripted answers by request `tag`, records calls; for tests and offline work |
| `resilient.py` | `ResilientLLM` — cache (`data/cache/llm.sqlite`), concurrency limit, transport retries |
| `json_utils.py` | `extract_json` — gets the JSON object out of fenced / chatty model output |

Setup and smoke test:

```bash
cp .env.example .env          # choose provider: fake | gemini | ollama | openai
python -m analytics.llm       # checks connectivity + JSON output + token/cost accounting
```

Two kinds of retries — do not mix them up:
- **Transport retries** (here): the call itself failed (network, 429, 5xx) → same request again.
- **Validation retries** (step 4, agent runner): the call succeeded but the answer is invalid → new request that includes the validation errors.

## Agents (step 4) — `analytics/agents/` + `analytics/prompts/`

An agent is **data, not a class**: an `AgentSpec` says who the agent is and what it must return.
One generic `AgentRunner` executes every spec the same way.

```python
spec = AgentSpec(
    name="lens:science",                      # trace label + FakeLLM script key
    system_prompt=load_prompt("_common") + "\n\n" + load_prompt("lens_science"),
    output_model=ScienceAnswer,               # Pydantic model of the raw LLM answer -> JSON Schema
    model=None,                               # optional per-agent model override
    max_attempts=3,                           # 1 call + up to 2 validation retries
)
result = await AgentRunner(llm).run(spec, user_prompt, validate=check_citations)
# result.ok, result.output (typed), result.errors, result.attempts, result.traces
```

Validate-and-retry loop:

```
attempt 1:  [system, user]                                         ─► LLM ─► check
attempt 2:  [system, user, assistant(bad answer), user(error list)] ─► LLM ─► check
...         until valid or max_attempts
```

Checks, cheapest first:

| # | Check | Example error fed back to the model |
|---|---|---|
| 1 | **parse** — is there a JSON object (fences and `<think>` blocks are stripped) | `The answer is not a JSON object…` |
| 2 | **schema** — Pydantic `output_model` | `claims.0.confidence: Input should be less than or equal to 1` |
| 3 | **semantics** — `validate` callback from the caller | `claims.2 cites unknown evidence id E99` |

The runner never raises on a bad answer; it returns `AgentResult(ok=False, errors=…)` and the
orchestrator decides what to do. Every LLM call becomes an `AgentTrace` (tokens, latency, cost, error).

**Prompts are Markdown files** in `analytics/prompts/` (`_common.md` = rules shared by all analysts).
Placeholders use `$name` (`render(template, name=...)`), so JSON examples with `{}` need no escaping.

## Lenses (step 5) — `analytics/lenses/` + `analytics/prompts/lens_*.md`

Four analysts, one generic code path. Each lens = prompt file + answer model + mandatory questions (`definitions.py`).

| Lens | Prompt | Lens-specific output (→ `LensResult.params`) |
|---|---|---|
| science | `lens_science.md` | `causality`, `translation_chain` (5 links: supported / weak / missing / contradicted) |
| clinical | `lens_clinical.md` | `development_plan` (population, endpoint, comparator, biomarkers, phases, analogues) |
| market | `lens_market.md` | `market` (SoC, unmet need, addressable patients, pricing analogues, differentiation) |
| investment | `lens_investment.md` | stage, next milestone, `trials_to_milestone` ranges, peak sales low/base/high, exit options |

Common output for every lens: `score` 0–5 (rubric in each prompt; 0 = kills the thesis), `score_rationale`,
2–8 `claims` with cited ids and stance, `counter_evidence`, `unknowns` (+ the data they require), `need_detail`.

Flow of one lens:

```
context (header + cards) + questions ─► AgentRunner ─► parse ─► schema ─► semantic validation ─► retry ≤2
                                                                                │
                      need_detail? ─► send requested full cards ─► revised answer (1 round, ≤ 8 ids)
                                                                                │
                                          short ids ─► full evidence ids (A-ids expand to members) ─► LensResult
```

Semantic validation (`validation.py`):
- every cited id exists, and was **shown to this lens** (ids under "NOT SHOWN" must be requested first);
- every **KILL SIGNAL** conflict in the lens context is cited by at least one claim;
- ranges are ordered (low ≤ base ≤ high).

Prompt size per lens ≈ 1.3–1.5k tokens of instructions + schema, + ~0.2k header, + evidence (`budget_tokens`),
+ up to 2.5k tokens of answer. With Ollama `LLM_NUM_CTX=16384`, an evidence budget of 8k fits comfortably.

Run the analysts and inspect results:

```bash
python -m analytics.lenses data/fixtures/il17_crohn_now.json                  # all four lenses
python -m analytics.lenses data/fixtures/il17_crohn_now.json --lens science   # one lens
```

Outputs: `data/runs/<id>/lenses/<lens>.json` (result + traces) and `data/runs/<id>/prompts/<lens>.md`
(exactly what was sent — the first place to look when tuning prompts).
With `LLM_PROVIDER=fake` an offline stand-in (`lenses/fake.py`) produces schema-valid answers that cite the
context — it is **not analysis**, it only lets the pipeline run end-to-end without a model.

## Verdict (step 6) — `analytics/verdict/`

Deterministic rules, no LLM: same inputs → same verdict, every fired rule is logged in `Verdict.rule_trace`,
all thresholds live in `VerdictConfig`.

| Order | Rule | Effect |
|---|---|---|
| 1 | **Kill** — conflict evidence with `kill_signal` (same-target program failed in this indication) | Do Not Invest |
| 2 | **Floors** — science ≤ 1 (biology contradicted) or clinical ≤ 0 | Do Not Invest |
| 3 | **Composite** — weighted mean of available lens scores (science 0.35, clinical 0.25, market 0.20, investment 0.20) | ≥ 3.5 Invest · < 2.0 Do Not Invest · else Conditional |
| 4 | **Caps** — high-severity conflicts, failed lenses, any lens < 3, science < 3, overridden kills | Invest → Conditional |
| 5 | **Confidence** — how well-founded the verdict is: mean of coverage, agreement of lens scores, sourced claims, evidence depth (primary items and sources) and margin from the deciding threshold; floor 0.80 + 0.05 per extra independent failed program | 0.2 – 0.95 |

`decide(..., kill_overrides={ids})` lets a later step (adjudicator, step 10) mark a kill signal as not
target-related; it then no longer forces Do Not Invest but still caps the verdict at Conditional.

Tune thresholds without any LLM calls — the verdict reads the saved lens results:

```bash
python -m analytics.lenses  data/fixtures/il17_crohn_now.json   # once (LLM)
python -m analytics.verdict data/fixtures/il17_crohn_now.json   # instant, prints the rule trace
```

## Capital and rNPV (step 7) — `analytics/finance/`

Deterministic model; every number it uses is listed in `assumptions.py` and copied into the output
`assumptions`, so a reader can challenge it. Ranges (low / base / high), not point estimates.

Inputs: investment lens `params` (stage, next milestone, trials to milestone, peak sales range),
bundle `trial_benchmarks` (fallback trial sizes / durations), verdict kill flag.

**Capital to the next milestone** = IND-enabling package (if preclinical)
+ Σ trials (patients × cost per patient) + program overhead per year × duration (trials run sequentially).

**rNPV** = P(approval) × PV(commercial cash flows) − Σ P(reach phase) × PV(phase cost)

| Assumption | Value |
|---|---|
| Phase transition probabilities | BIO/Informa/QLS 2011–2020: Ph1 52%, Ph2 29%, Ph3 58%, filing 91% (preclinical 65% is an assumption) |
| Kill signal | Phase 2 success × 0.25 |
| Cost per patient | Ph1 25–70k, Ph2 30–90k, Ph3 30–80k USD |
| Overhead | $5–15M per year while trials run |
| Sales curve | 5-year linear ramp, 6 years at peak, −50%/year after loss of exclusivity |
| Margin / discount | 25/35/45% operating margin; 15/12/10% discount (low/base/high scenario) |

Low scenario = pessimistic on every input at once (low peak, high cost, low margin, high discount); high = the reverse.

```bash
python -m analytics.finance data/fixtures/il17_crohn_now.json   # reads saved lens results, no LLM
```

## Risks, unknowns, diligence (step 8) — `analytics/findings/`

Deterministic; every item links back to evidence ids (and to the claims citing them) for the
Recommendation → Claims → Evidence → Sources drill-down.

| Output | Built from | Ordering / limits |
|---|---|---|
| **Risks** | researcher conflicts (kill signals first) · one combined "low analyst scores" risk for lenses ≤ 2 · science translation-chain links that are missing / contradicted | severity, then source priority; near-duplicates merged; ≤ 10 |
| **Unknowns** | lens `unknowns` + researcher `gaps` | duplicates removed by wording similarity; ≤ 12 |
| **Diligence questions** | 1) one question naming all failed same-target programs ("target, molecule, dose or population?") · 2) high-severity risks · 3) weak / missing chain links · 4) unknowns · 5) "what would kill the program?" · 6) standard translational questions as top-up | 5–10, each with the data it `requires` |

```bash
python -m analytics.findings data/fixtures/il17_crohn_now.json   # reads saved lens results, no LLM
```

## Orchestrator (step 9) — `analytics/orchestrator.py`, `python -m analytics`

One command from evidence bundle to `analysis_result.json`:

```bash
python -m analytics data/fixtures/il17_crohn_now.json
python -m analytics data/fixtures/il17_crohn_now.json --budget 4000 --max-tokens 60000   # small model
python -m analytics data/fixtures/il17_crohn_now.json --lens science --lens clinical    # subset
```

```
bundle ─► context ─► 4 lenses (parallel, LLM) ─► verdict ─► finance ─► findings ─► analysis_result.json
            │                                                                        trace.jsonl
            └─ token guard: if one attempt per lens would exceed --max-tokens,          context/ prompts/ lenses/
               the per-lens evidence budget is shrunk (P0 evidence is never dropped)
```

In code (e.g. from the future API layer):

```python
result = await analyze(bundle, make_llm(), RunOptions(budget_tokens=6000), on_event=send_sse)
# on_event(step, status, payload): ("lens:science", "done", {"score": 2, "claims": 5, ...})
```

- A failed lens never breaks the run: it is marked `status="failed"`, reported as an `error` event,
  and the verdict caps itself (missing lens → at most Conditional, lower confidence).
- A lens whose context has no evidence is not sent to the model: `status="skipped"`, and "no public evidence
  for this lens" becomes a critical unknown (same verdict cap as a failed lens).
- `analysis_result.json` is the contract for writer (`analytics/schemas.py`); `short_ids` maps
  `E3` → evidence id so inline references in rationales can be linked.
- `trace.jsonl`: one line per LLM call and per code step; the last line (`step="total"`) has the
  run's tokens, cost and wall time — the "cost per underwriting run" deliverable.

## Implementation plan

| # | Step | Status |
|---|---|---|
| 1 | `server/` skeleton, `evidence_bundle` contract + fixtures, `analysis_result` contract | ✅ |
| 2 | **ContextBuilder**: routing, short ids, priorities, aggregates, token budget, cards | ✅ |
| 3 | LLM layer: provider-agnostic client (Ollama / OpenAI-compatible) + `FakeLLM`, cache, retries, tokens and cost | ✅ |
| 4 | Agent abstraction: `AgentSpec` + runner (prompt → call → parse → validate → retry), prompt files | ✅ |
| 5 | 4 lenses: prompts, answer schemas, validator rules, `E-id → evidence_id` mapping, detail round | ✅ |
| 6 | Verdict: kill rule, floors, composite, caps, confidence, `rule_trace` | ✅ |
| 7 | Capital and rNPV from lens params + `trial_benchmarks` (ranges + listed assumptions) | ✅ |
| 8 | Risks, unknowns, diligence questions | ✅ |
| 9 | Orchestrator: pipeline, events, token guard, traces, CLI `python -m analytics <bundle>` | ✅ |
| 10 | End-to-end integration: researcher adapter, writer, HTTP API, web live run (`server/app/`) | ✅ |
| 11 | (optional) Adjudicator for kill candidates, sceptic hook | |

After step 9 the system produces a full `analysis_result.json` on fixtures; every later step improves quality and can be stopped at any time.
