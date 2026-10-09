// Single data-access layer. Every component reads data through these functions.
// Reports come from the Python backend (server/app, proxied at /api by Vite);
// the two hand-written fixtures (run_a, run_b) still resolve locally so the UI
// works without a backend.

import runA from './fixtures/run_a.json'
import runB from './fixtures/run_b.json'
import type { Report, ReportInput } from './types'

const API = '/api'
const fixtures: Report[] = [runA as Report, runB as Report]

function findFixture(id: string): Report | undefined {
  return fixtures.find((r) => r.id === id)
}

export async function getReport(id: string): Promise<Report> {
  const fixture = findFixture(id)
  if (fixture) return structuredClone(fixture)
  const res = await fetch(`${API}/runs/${encodeURIComponent(id)}/report`)
  if (!res.ok) throw new Error(`Report not found: ${id}`)
  return (await res.json()) as Report
}

/** The earlier run with the same input, or null if `id` is the first run. */
export async function getPreviousRun(id: string): Promise<Report | null> {
  const report = await getReport(id)
  if (!report.previous_run_id) return null
  return getReport(report.previous_run_id).catch(() => null)
}

// ---------------------------------------------------------------- live runs

export interface RunOptions {
  /** ISO date: analyse the evidence as it was known on this day (demo of changing evidence). */
  evidence_cutoff?: string
  /** live = public APIs via the researcher; fixture = bundled demo data. Default: backend setting. */
  source?: 'live' | 'fixture'
  /** Also render the PDF report with the writer. */
  pdf?: boolean
}

/** One progress event of a backend run (server/app/server.py). */
export interface RunEvent {
  seq: number
  step: string
  status: 'started' | 'progress' | 'done' | 'error'
  payload: Record<string, unknown>
}

export interface BackendHealth {
  ok: boolean
  llm_provider: string
  llm_model: string | null
  research: string
  pdf: boolean
}

/** Backend status, or null when no backend is running. */
export async function getBackendHealth(): Promise<BackendHealth | null> {
  try {
    const res = await fetch(`${API}/health`, { signal: AbortSignal.timeout(1500) })
    return res.ok ? ((await res.json()) as BackendHealth) : null
  } catch {
    return null
  }
}

export async function startRun(input: ReportInput, options: RunOptions = {}): Promise<string> {
  const res = await fetch(`${API}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input, options }),
  })
  if (!res.ok) throw new Error(`Could not start the run (${res.status}): ${await res.text()}`)
  return ((await res.json()) as { run_id: string }).run_id
}

/** A backend run: what was asked and, once finished, the outcome. */
export interface RunInfo {
  run_id: string
  done: boolean
  error: string | null
  input: ReportInput
  options: RunOptions
  result: {
    recommendation: Report['recommendation']
    confidence: number
    summary: string
    previous_run_id: string | null
  } | null
}

export async function getRun(id: string): Promise<RunInfo> {
  const res = await fetch(`${API}/runs/${encodeURIComponent(id)}`)
  if (!res.ok) throw new Error(`Run not found: ${id}`)
  const info = (await res.json()) as RunInfo & { options: Record<string, unknown> }
  const options: RunOptions = {}
  if (typeof info.options.evidence_cutoff === 'string') options.evidence_cutoff = info.options.evidence_cutoff
  if (info.options.source === 'live' || info.options.source === 'fixture') options.source = info.options.source
  if (typeof info.options.pdf === 'boolean') options.pdf = info.options.pdf
  return { ...info, options }
}

/** Streams progress events; `onEnd` fires once with an error message or null. Returns an unsubscribe function. */
export function watchRun(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onEnd: (error: string | null) => void,
): () => void {
  const source = new EventSource(`${API}/runs/${encodeURIComponent(runId)}/events`)
  let ended = false
  const end = (error: string | null) => {
    if (ended) return
    ended = true
    source.close()
    onEnd(error)
  }
  source.onmessage = (msg) => onEvent(JSON.parse(msg.data) as RunEvent)
  source.addEventListener('end', (msg) => end((JSON.parse((msg as MessageEvent).data) as { error: string | null }).error))
  source.onerror = () => end('Lost connection to the backend')
  return () => {
    ended = true
    source.close()
  }
}

export type DownloadFormat = 'md' | 'pdf' | 'zip'

/** True for reports served by the backend (fixture reports have no downloadable files). */
export function hasDownloads(id: string): boolean {
  return !findFixture(id)
}

/** Download URL of a run's report: Markdown, PDF (generated on first request if missing) or the whole run as zip. */
export function downloadUrl(id: string, format: DownloadFormat): string {
  const file = format === 'zip' ? 'files.zip' : `report.${format}`
  return `${API}/runs/${encodeURIComponent(id)}/${file}`
}

export function reportPdfUrl(id: string): string {
  return downloadUrl(id, 'pdf')
}
