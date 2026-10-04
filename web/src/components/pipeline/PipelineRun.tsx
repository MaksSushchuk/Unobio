import { AnimatePresence, motion } from 'framer-motion'
import { ChevronRight, CircleCheck, X } from 'lucide-react'
import { useEffect, useEffectEvent, useMemo, useRef, useState, type ReactNode } from 'react'
import { PIPELINE, READY_HOLD_MS } from '../../pipeline.config'
import type { ReportInput } from '../../types'
import Counter from '../Counter'
import PipelineDiagram from './PipelineDiagram'
import { compileTimeline, deriveState, type LogEvent } from './timeline'

const TICK_MS = 100

export default function PipelineRun({
  input,
  onComplete,
  onCancel,
}: {
  input: ReportInput
  onComplete: () => void
  onCancel: () => void
}) {
  const timeline = useMemo(() => compileTimeline(PIPELINE, input), [input])
  const [startedAt] = useState(() => Date.now())
  const [elapsed, setElapsed] = useState(0)
  const [showLog, setShowLog] = useState(false)
  const complete = useEffectEvent(onComplete)

  // One clock drives everything; state is derived from `elapsed`.
  useEffect(() => {
    const t0 = performance.now()
    const id = setInterval(() => {
      const e = performance.now() - t0
      setElapsed(e)
      if (e >= timeline.end + READY_HOLD_MS) {
        clearInterval(id)
        complete()
      }
    }, TICK_MS)
    return () => clearInterval(id)
  }, [timeline])

  const state = deriveState(timeline, elapsed)
  const sourceCount = state.sources.size

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <motion.h1
              key={state.done ? 'ready' : 'running'}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex items-center gap-2.5 text-2xl font-semibold tracking-tight"
            >
              {state.done ? (
                <CircleCheck className="h-6 w-6 text-emerald-600" />
              ) : (
                <span className="relative flex h-2.5 w-2.5">
                  <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 motion-safe:animate-ping" />
                  <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
                </span>
              )}
              {state.done ? 'Report ready' : 'Running underwriting'}
            </motion.h1>
            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200 ring-inset">
              Simulated
            </span>
          </div>
          <p className="mt-1 truncate text-sm text-slate-500">
            {input.mechanism} · {input.indication}
            {state.done && <span className="text-slate-400"> — opening report…</span>}
          </p>
        </div>
        {!state.done && (
          <button
            type="button"
            onClick={onCancel}
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 shadow-xs hover:bg-slate-50 hover:text-slate-900"
          >
            <X className="h-3.5 w-3.5" />
            Cancel
          </button>
        )}
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="relative px-4 py-6 sm:px-6">
          <PipelineDiagram pipeline={PIPELINE} state={state} elapsed={elapsed} />
          <AnimatePresence>
            {state.done && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="absolute inset-0 flex items-center justify-center bg-white/70 backdrop-blur-[1px]"
              >
                <motion.div
                  initial={{ scale: 0.9, y: 8 }}
                  animate={{ scale: 1, y: 0 }}
                  transition={{ type: 'spring', stiffness: 300, damping: 22 }}
                  className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-5 py-3 shadow-lg"
                >
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-emerald-50 text-emerald-600">
                    <CircleCheck className="h-5 w-5" />
                  </span>
                  <span>
                    <span className="block text-sm font-medium text-slate-900">Report ready</span>
                    <span className="block text-xs text-slate-500">
                      {state.evidence} evidence items from {sourceCount} sources
                    </span>
                  </span>
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <dl className="grid grid-cols-2 divide-slate-100 border-t border-slate-100 bg-slate-50/70 sm:grid-cols-4 sm:divide-x">
          <Stat label="Evidence items">
            <Counter value={state.evidence} format={(n) => Math.round(n).toString()} />
          </Stat>
          <Stat label="Sources">
            <Counter value={sourceCount} format={(n) => Math.round(n).toString()} />
          </Stat>
          <Stat label="Elapsed">{(Math.min(elapsed, timeline.end) / 1000).toFixed(1)}s</Stat>
          <Stat label="Cost (USD)">
            <Counter value={state.cost_usd} format={(n) => `$${n.toFixed(3)}`} />
          </Stat>
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
          <span className="font-normal text-slate-400">({state.logs.length})</span>
        </button>
        <AnimatePresence initial={false}>
          {showLog && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.25, ease: 'easeOut' }}
              className="overflow-hidden"
            >
              <RunLog logs={state.logs} startedAt={startedAt} />
            </motion.div>
          )}
        </AnimatePresence>
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

function formatTime(ms: number) {
  return new Date(ms).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function RunLog({ logs, startedAt }: { logs: LogEvent[]; startedAt: number }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [logs.length])

  return (
    <div
      ref={ref}
      className="mt-3 max-h-72 overflow-y-auto rounded-lg border border-slate-200 bg-white px-4 py-3 font-mono text-[12.5px] leading-6"
    >
      {logs.map((e, i) => (
        <div key={i} className="grid grid-cols-[auto_1fr] gap-x-4 py-0.5 sm:grid-cols-[auto_8rem_1fr] sm:py-0">
          <span className="text-slate-400 tabular-nums">{formatTime(startedAt + e.at)}</span>
          <span className="truncate text-slate-500">{e.agent}</span>
          <span className="col-span-2 text-slate-800 sm:col-span-1">{e.message}</span>
        </div>
      ))}
    </div>
  )
}
