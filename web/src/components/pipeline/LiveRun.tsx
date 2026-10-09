// Real backend run: shows the progress events streamed by server/app (SSE)
// and opens the report when the pipeline finishes. The simulated PipelineRun
// is still used when no backend is running.

import { motion } from 'framer-motion'
import {
  ArrowLeft,
  ArrowRight,
  ChevronRight,
  GitCompareArrows,
  CircleAlert,
  CircleCheck,
  Coins,
  Dna,
  FileText,
  Gavel,
  Landmark,
  Layers,
  ListChecks,
  PenLine,
  ScanSearch,
  Stethoscope,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import Downloads from '../report/Downloads'
import { getRun, watchRun, type RunEvent, type RunInfo, type RunOptions } from '../../api'
import type { PipelineStage } from '../../pipeline.config'
import type { ReportInput } from '../../types'
import PipelineDiagram from './PipelineDiagram'
import type { AgentStatus, RunState, SourceState } from './timeline'

type StepStatus = 'pending' | 'active' | 'done' | 'error'

interface StepDef {
  id: string
  label: string
  role: string
  icon: LucideIcon
}

const STAGES: { label: string; steps: StepDef[] }[] = [
  { label: 'Research', steps: [{ id: 'research', label: 'Researcher', role: 'Public evidence', icon: ScanSearch }] },
  { label: 'Context', steps: [{ id: 'context', label: 'Context', role: 'Routing & budget', icon: Layers }] },
  {
    label: 'Analysts',
    steps: [
      { id: 'lens:science', label: 'Science', role: 'Target & translation', icon: Dna },
      { id: 'lens:clinical', label: 'Clinical', role: 'Trials & plan', icon: Stethoscope },
      { id: 'lens:market', label: 'Market', role: 'Landscape & demand', icon: Landmark },
      { id: 'lens:investment', label: 'Investment', role: 'Stage & value', icon: TrendingUp },
    ],
  },
  {
    label: 'Decision',
    steps: [
      { id: 'verdict', label: 'Verdict', role: 'Rules', icon: Gavel },
      { id: 'finance', label: 'Finance', role: 'Capital & rNPV', icon: Coins },
      { id: 'findings', label: 'Findings', role: 'Risks & questions', icon: ListChecks },
    ],
  },
  { label: 'Report', steps: [{ id: 'report', label: 'Report', role: 'Report JSON', icon: FileText }] },
]

const ALL_DONE: Record<string, StepStatus> = Object.fromEntries(
  STAGES.flatMap((st) => st.steps).map((step) => [step.id, 'done' as StepStatus]),
)

const VERDICT_STYLE: Record<string, { label: string; tone: string }> = {
  invest: { label: 'Invest', tone: 'bg-emerald-50 text-emerald-800 ring-emerald-200' },
  conditional: { label: 'Conditional', tone: 'bg-amber-50 text-amber-800 ring-amber-200' },
  do_not_invest: { label: 'Do not invest', tone: 'bg-rose-50 text-rose-800 ring-rose-200' },
}

const WRITER_STEP: StepDef = { id: 'writer', label: 'Writer', role: 'Narrative & PDF', icon: PenLine }

// Display names for the researcher's sources (keys of the research "done" event's `sources`).
const SOURCE_LABEL: Record<string, string> = {
  clinicaltrials: 'ClinicalTrials.gov',
  opentargets: 'Open Targets',
  chembl: 'ChEMBL',
  pubmed: 'PubMed',
  openfda: 'openFDA',
}

/** STAGES as the shared PipelineDiagram config. Durations/costs/logs are unused for a live run. */
function toPipeline(stages: { label: string; steps: StepDef[] }[], sources: Record<string, number>): PipelineStage[] {
  const names = Object.keys(sources)
  return stages.map((stage) => ({
    id: stage.label.toLowerCase(),
    label: stage.label,
    agents: stage.steps.map((step) => ({
      id: step.id,
      name: step.label,
      role: step.role,
      icon: step.icon,
      duration_ms: 0,
      cost_usd: 0,
      log: () => '',
    })),
    sources:
      stage.steps.some((st) => st.id === 'research') && names.length
        ? names.map((n) => ({ id: n, name: SOURCE_LABEL[n] ?? n, records: sources[n], evidence: sources[n] }))
        : undefined,
  }))
}

/**
 * Real step statuses -> the diagram's RunState. The diagram has no "error" state: a failed step is shown
 * as not completed (pending, so the arrow into it stays idle) and listed in the notes under the diagram.
 */
function toRunState(status: Record<string, StepStatus>, sources: Record<string, number>, evidence: number, done: boolean): RunState {
  const agent = (id: string): AgentStatus => {
    const s = status[id] ?? 'pending'
    return s === 'error' ? 'pending' : s
  }
  const srcState = new Map<string, SourceState>(
    Object.entries(sources).map(([k, n]) => [k, { records: n, evidence: n, follow_up_at: null, last_follow_up_records: 0 }]),
  )
  return { agent, sources: srcState, evidence, cost_usd: 0, logs: [], activeLoops: [], done }
}

interface StepNote {
  step: string
  tone: 'error' | 'skipped'
  text: string
}

interface LogLine {
  at: number
  step: string
  text: string
}

function describe(e: RunEvent): string {
  const p = e.payload
  const parts = Object.entries(p)
    .filter(([k, v]) => v !== null && v !== '' && k !== 'trace' && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
  return `${e.status}${parts.length ? ' · ' + parts.join(', ') : ''}`
}

export default function LiveRun({
  runId,
  input,
  options,
  onOpenReport,
  onNew,
}: {
  /** Started by the caller (once per submit, so React StrictMode cannot start it twice). */
  runId: string
  input: ReportInput
  options: RunOptions
  /** Open the report page; `tab` e.g. "changes" for the comparison with the previous run. */
  onOpenReport: (runId: string, tab?: string) => void
  /** Back to an empty form (a running run keeps going on the server). */
  onNew: () => void
}) {
  const [status, setStatus] = useState<Record<string, StepStatus>>({})
  const [stats, setStats] = useState({ evidence: 0, conflicts: 0, analysts: 0 })
  const [log, setLog] = useState<LogLine[]>([])
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [showLog, setShowLog] = useState(true)
  const [startedAt] = useState(() => Date.now())
  const [now, setNow] = useState(() => Date.now())
  const [result, setResult] = useState<RunInfo['result']>(null)
  const [sources, setSources] = useState<Record<string, number>>({})
  const [notes, setNotes] = useState<StepNote[]>([])

  useEffect(() => {
    const unsubscribe = watchRun(
      runId,
      (e) => {
        setStatus((s) => ({ ...s, [e.step]: e.status === 'error' ? 'error' : e.status === 'done' ? 'done' : 'active' }))
        setLog((l) => [...l, { at: Date.now(), step: e.step, text: describe(e) }])
        if (e.step === 'research' && e.status === 'done') {
          setStats((s) => ({ ...s, evidence: Number(e.payload.evidence ?? 0), conflicts: Number(e.payload.conflicts ?? 0) }))
          const src = e.payload.sources
          if (src && typeof src === 'object') {
            setSources(Object.fromEntries(Object.entries(src as Record<string, unknown>).map(([k, v]) => [k, Number(v) || 0])))
          }
        }
        const errs = Array.isArray(e.payload.errors) ? (e.payload.errors as unknown[]).map(String) : []
        const warns = Array.isArray(e.payload.warnings) ? (e.payload.warnings as unknown[]).map(String) : []
        if (e.status === 'error') {
          setNotes((n) => [...n.filter((x) => x.step !== e.step), { step: e.step, tone: 'error', text: errs[0] ?? warns[0] ?? 'failed' }])
        } else if (e.status === 'done' && e.payload.status === 'skipped') {
          setNotes((n) => [...n.filter((x) => x.step !== e.step), { step: e.step, tone: 'skipped', text: errs[0] ?? 'skipped' }])
        }
        if (e.step.startsWith('lens:') && e.status === 'done') {
          setStats((s) => ({ ...s, analysts: s.analysts + 1 }))
        }
      },
      (err) => {
        if (err) {
          setError(err)
          return
        }
        setDone(true)
        // The page stays here: show the outcome and let the user open the report.
        getRun(runId)
          .then((info) => {
            setResult(info.result)
            if (info.error && !info.result) setError(info.error)
            // A run finished in an earlier server process has no event history: mark every step done.
            if (info.result) setStatus((s) => (Object.keys(s).length ? s : ALL_DONE))
          })
          .catch(() => undefined)
      },
    )
    return unsubscribe
  }, [runId])

  useEffect(() => {
    if (done || error) return
    const t = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(t)
  }, [done, error])

  // The writer runs between the decision and the report. Unset `pdf` means the server default (on), so the
  // step is shown unless it was turned off, and always once the server reports it.
  const showWriter = options.pdf !== false || status.writer !== undefined
  const stages = useMemo(
    () => (showWriter ? [...STAGES.slice(0, -1), { label: 'Writer', steps: [WRITER_STEP] }, ...STAGES.slice(-1)] : STAGES),
    [showWriter],
  )
  const pipeline = useMemo(() => toPipeline(stages, sources), [stages, sources])
  const runState = useMemo(() => toRunState(status, sources, stats.evidence, done), [status, sources, stats.evidence, done])
  const stepLabel = (id: string) => stages.flatMap((st) => st.steps).find((st) => st.id === id)?.label ?? id
  const elapsed = ((done || error ? (log.at(-1)?.at ?? now) : now) - startedAt) / 1000

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="flex items-center gap-2.5 text-2xl font-semibold tracking-tight">
              {error ? (
                <CircleAlert className="h-6 w-6 text-rose-600" />
              ) : done ? (
                <CircleCheck className="h-6 w-6 text-emerald-600" />
              ) : (
                <span className="relative flex h-2.5 w-2.5">
                  <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 motion-safe:animate-ping" />
                  <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
                </span>
              )}
              {error ? 'Run failed' : done ? 'Report ready' : 'Running underwriting'}
            </h1>
            <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-emerald-200 ring-inset">
              Live
            </span>
            {options.evidence_cutoff && (
              <span className="rounded bg-sky-50 px-1.5 py-0.5 text-[11px] font-medium text-sky-700 ring-1 ring-sky-200 ring-inset">
                Evidence as of {options.evidence_cutoff}
              </span>
            )}
          </div>
          <p className="mt-1 truncate text-sm text-slate-500">
            {input.mechanism} · {input.indication}
            {runId && <span className="font-mono text-xs text-slate-400"> · {runId}</span>}
          </p>
        </div>
        <button
          type="button"
          onClick={onNew}
          title={done || error ? undefined : 'The run keeps going on the server; reopen it later from the URL'}
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 shadow-xs hover:bg-slate-50 hover:text-slate-900"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          New analysis
        </button>
      </div>

      {error && (
        <p className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">{error}</p>
      )}

      {result && (
        <motion.section
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex flex-wrap items-start justify-between gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
        >
          <div className="min-w-0 flex-1 basis-96">
            <div className="flex items-center gap-3">
              <span
                className={`rounded-md px-2.5 py-1 text-sm font-semibold ring-1 ring-inset ${VERDICT_STYLE[result.recommendation]?.tone ?? ''}`}
              >
                {VERDICT_STYLE[result.recommendation]?.label ?? result.recommendation}
              </span>
              <span className="text-sm text-slate-500 tabular-nums">confidence {Math.round(result.confidence * 100)}%</span>
            </div>
            <p className="mt-2 text-sm leading-6 text-slate-700">{result.summary}</p>
            <Downloads id={runId} className="mt-3" />
          </div>
          <div className="flex shrink-0 flex-col gap-2">
            <button
              type="button"
              onClick={() => onOpenReport(runId)}
              className="inline-flex items-center justify-center gap-1.5 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-800"
            >
              Open report
              <ArrowRight className="h-4 w-4" />
            </button>
            {result.previous_run_id && (
              <button
                type="button"
                onClick={() => onOpenReport(runId, 'changes')}
                className="inline-flex items-center justify-center gap-1.5 rounded-md border border-slate-200 bg-white px-4 py-2 text-sm text-slate-700 shadow-xs hover:bg-slate-50"
              >
                <GitCompareArrows className="h-4 w-4" />
                What changed
              </button>
            )}
          </div>
        </motion.section>
      )}

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="px-4 py-6 sm:px-6">
          <PipelineDiagram pipeline={pipeline} state={runState} elapsed={now - startedAt} />
        </div>
        {notes.length > 0 && (
          <ul className="space-y-1.5 border-t border-slate-100 px-4 py-3 sm:px-6">
            {notes.map((n) => (
              <li
                key={n.step}
                className={`flex items-start gap-2 rounded-md border px-3 py-1.5 text-xs ${
                  n.tone === 'error' ? 'border-rose-200 bg-rose-50 text-rose-800' : 'border-amber-200 bg-amber-50 text-amber-800'
                }`}
              >
                <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span className="min-w-0 break-words">
                  <b className="font-medium">{stepLabel(n.step)}</b> {n.tone === 'error' ? 'failed' : 'skipped'}: {n.text}
                </span>
              </li>
            ))}
          </ul>
        )}
        <dl className="grid grid-cols-2 divide-slate-100 border-t border-slate-100 bg-slate-50/70 sm:grid-cols-4 sm:divide-x">
          <Stat label="Evidence items">{stats.evidence}</Stat>
          <Stat label="Conflicts">{stats.conflicts}</Stat>
          <Stat label="Analysts done">{stats.analysts}/4</Stat>
          <Stat label="Elapsed">{elapsed.toFixed(1)}s</Stat>
        </dl>
      </div>

      <section>
        <button
          type="button"
          onClick={() => setShowLog((s) => !s)}
          aria-expanded={showLog}
          className="flex items-center gap-1.5 text-sm font-medium text-slate-600 hover:text-slate-900"
        >
          <motion.span animate={{ rotate: showLog ? 90 : 0 }} className="flex">
            <ChevronRight className="h-4 w-4" />
          </motion.span>
          {showLog ? 'Hide log' : 'Show log'}
          <span className="font-normal text-slate-400">({log.length})</span>
        </button>
        {showLog && <RunLog lines={log} />}
      </section>
    </div>
  )
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="px-5 py-3">
      <dt className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold text-slate-900 tabular-nums">{children}</dd>
    </div>
  )
}

function RunLog({ lines }: { lines: LogLine[] }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines.length])
  return (
    <div
      ref={ref}
      className="mt-3 max-h-72 overflow-y-auto rounded-lg border border-slate-200 bg-white px-4 py-3 font-mono text-[12.5px] leading-6"
    >
      {lines.map((l, i) => (
        <div key={i} className="grid grid-cols-[auto_1fr] gap-x-4 py-0.5 sm:grid-cols-[auto_9rem_1fr] sm:py-0">
          <span className="text-slate-400 tabular-nums">
            {new Date(l.at).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </span>
          <span className="truncate text-slate-500">{l.step}</span>
          <span className="col-span-2 break-words text-slate-800 sm:col-span-1">{l.text}</span>
        </div>
      ))}
    </div>
  )
}
