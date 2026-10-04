// Single data-access layer. Every component reads data through these functions.
// Today they resolve local fixtures; later each body becomes a fetch() to the
// Python backend with the same signature and return type.

import runA from './fixtures/run_a.json'
import runB from './fixtures/run_b.json'
import type { Report } from './types'

const reports: Report[] = [runA as Report, runB as Report]

function findReport(id: string): Report | undefined {
  return reports.find((r) => r.id === id)
}

export async function getReport(id: string): Promise<Report> {
  const report = findReport(id)
  if (!report) throw new Error(`Report not found: ${id}`)
  return structuredClone(report)
}

/** The earlier run with the same input, or null if `id` is the first run. */
export async function getPreviousRun(id: string): Promise<Report | null> {
  const report = await getReport(id)
  if (!report.previous_run_id) return null
  return getReport(report.previous_run_id)
}
