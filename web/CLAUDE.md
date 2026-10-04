# Unobio — web

Frontend for **Unobio**, an AI biotech investment underwriting tool. Given an
indication + mechanism (and optionally modality, stage, biomarkers, route), the
system produces an underwriting report: a recommendation (`invest` /
`conditional` / `do_not_invest`) with confidence, evidence-linked claims, risks,
unknowns, diligence questions, a capital-to-milestone estimate, and agent traces.
A rerun of the same input links to its predecessor so the UI can show what changed.

This package is **frontend only**. A Python backend will exist later; until then
all data comes from local JSON fixtures.

## Stack

- Vite + React 19 + TypeScript
- Tailwind CSS v4 (via `@tailwindcss/vite`, imported in `src/index.css`)
- React Router (`react-router`)
- Lint: oxlint

## Commands

- `npm run dev` — dev server
- `npm run build` — type-check (`tsc -b`) + production build
- `npm run lint` — oxlint

## Layout

- `src/types.ts` — domain types; the contract with the future backend
- `src/api.ts` — the only data-access layer
- `src/fixtures/*.json` — mock reports (`run_a` → `run_b` rerun of the same input)
- `src/pages/` — route components (one per route below)
- `src/App.tsx` — router

## Pages

The app has exactly **two routes**. Do not add others (no runs list, no
standalone compare/evidence/traces pages) — new views go in as tabs on the
report page.

### `/` — New analysis

- Input form for `ReportInput` (indication, mechanism required; modality,
  stage, biomarkers, route optional).
- Presets that prefill the form (e.g. Crohn's disease + IL-17 inhibition).
- Submit plays a **simulated** live agent log (client-side timers, no network),
  then navigates to `/runs/:id` of a fixture report.

### `/runs/:id` — Report

Loads with `getReport(id)` and `getPreviousRun(id)`. Tabs:

1. **Report** — verdict (recommendation + confidence + summary), sections with
   claims (source_fact vs inference, confidence) and expandable evidence per
   claim showing stance, then risks, unknowns, diligence questions, capital.
2. **Evidence** — table of all `report.evidence` with filters (kind, source,
   module, stance); `kind: "conflict"` rows and contradicting evidence are
   visually highlighted.
3. **What changed** — diff against the previous run of the same input
   (recommendation, confidence, claims, evidence, risks, unknowns, capital).
   **Rendered only when `getPreviousRun` returns a report**; otherwise the tab
   is hidden. The diff is computed client-side from the two `Report`s (pure
   function, matching items by `id`).
4. **Traces** — table of `report.traces`: agent, step, tool, tokens, latency,
   cost, with totals.

## Rules

1. **All data access goes through `src/api.ts`.** Components never import
   fixtures directly. `api.ts` exposes async functions that currently resolve
   fixtures and will later be replaced by `fetch` calls with identical
   signatures:
   - `getReport(id): Promise<Report>` — throws if not found
   - `getPreviousRun(id): Promise<Report | null>` — earlier run with the same
     input (via `previous_run_id`), or `null`
2. **Types live in `src/types.ts` and are the contract with the future Python
   backend.** Keep field names snake_case to mirror the backend JSON. Changing
   a type is a contract change — update fixtures to match. UI-only derived
   types (e.g. diff results) live next to the code that computes them, not in
   `types.ts`.
3. **No backend code, no external services.** No servers, no API calls, no
   third-party SDKs/analytics, no runtime network requests. Fixture URLs are
   display-only links.
4. Fixtures carry `"is_mock": true`; the UI should make mock data visible as such.
