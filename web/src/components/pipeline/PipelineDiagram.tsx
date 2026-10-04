import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Check, Pause } from 'lucide-react'
import { useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import type { PipelineAgent, PipelineStage } from '../../pipeline.config'
import Counter from '../Counter'
import { computeLayout, NODE_H } from './layout'
import type { AgentStatus, RunState } from './timeline'

const formatInt = (n: number) => Math.round(n).toLocaleString()

export default function PipelineDiagram({
  pipeline,
  state,
  elapsed,
}: {
  pipeline: PipelineStage[]
  state: RunState
  elapsed: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const el = ref.current!
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const layout = useMemo(() => (width ? computeLayout(pipeline, width) : null), [pipeline, width])
  const agents = useMemo(() => new Map(pipeline.flatMap((s) => s.agents).map((a) => [a.id, a])), [pipeline])
  const sources = useMemo(() => new Map(pipeline.flatMap((s) => s.sources ?? []).map((s) => [s.id, s])), [pipeline])

  return (
    <div ref={ref} className="relative w-full" style={{ height: layout?.height ?? 320 }}>
      {layout && (
        <>
          <svg className="absolute inset-0 overflow-visible" width={width} height={layout.height} aria-hidden>
            {layout.edges.map((e) => (
              <Edge key={`${e.from}-${e.to}`} d={e.d} status={edgeStatus(state.agent(e.from), state.agent(e.to))} />
            ))}
            <AnimatePresence>
              {layout.loops
                .filter((l) => state.activeLoops.some((a) => a.from_stage === l.from_stage))
                .map((l) => (
                  <LoopArrow key={l.from_stage} d={l.d} at={l.arrowAt} dir={l.arrowDir} />
                ))}
            </AnimatePresence>
          </svg>

          <AnimatePresence>
            {layout.loops
              .filter((l) => state.activeLoops.some((a) => a.from_stage === l.from_stage))
              .map((l) => (
                <motion.div
                  key={l.from_stage}
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1, transition: { delay: 0.35 } }}
                  exit={{ opacity: 0 }}
                  className={`absolute z-10 -translate-x-1/2 -translate-y-1/2 rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap text-amber-800 shadow-sm ${
                    layout.vertical ? '[writing-mode:vertical-rl] px-0.5 py-2' : ''
                  }`}
                  style={{ left: l.labelAt.x, top: l.labelAt.y }}
                >
                  ↺ {l.label}
                </motion.div>
              ))}
          </AnimatePresence>

          {layout.nodes.map((n) => (
            <Node
              key={n.id}
              agent={agents.get(n.id)!}
              status={state.agent(n.id)}
              style={{ left: n.x - n.w / 2, top: n.y - NODE_H / 2, width: n.w, height: NODE_H }}
            />
          ))}

          {layout.chips.map((c) => {
            const src = sources.get(c.source_id)!
            const s = state.sources.get(c.source_id)
            return (
              <AnimatePresence key={c.source_id}>
                {s && (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.85, y: 6 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    transition={{ type: 'spring', stiffness: 400, damping: 24 }}
                    className="absolute flex h-[26px] items-center gap-1.5 overflow-hidden rounded-md border border-slate-200 bg-white px-2 text-[11.5px] shadow-xs"
                    style={{ left: c.x, top: c.y, width: c.w }}
                  >
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
                    <span className="truncate text-slate-700">{src.name}</span>
                    <span className="ml-auto shrink-0 font-mono text-slate-500 tabular-nums">
                      <Counter value={s.records} format={formatInt} />
                    </span>
                    <AnimatePresence>
                      {s.follow_up_at !== null && elapsed - s.follow_up_at < 1400 && (
                        <motion.span
                          key={s.follow_up_at}
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
                          exit={{ opacity: 0 }}
                          className="absolute inset-y-0 right-0 flex items-center bg-gradient-to-l from-amber-50 from-60% to-transparent pr-2 pl-6 font-mono font-medium text-amber-800"
                        >
                          +{s.last_follow_up_records}
                        </motion.span>
                      )}
                    </AnimatePresence>
                  </motion.div>
                )}
              </AnimatePresence>
            )
          })}
        </>
      )}
    </div>
  )
}

type EdgeStatus = 'idle' | 'flowing' | 'done'

function edgeStatus(from: AgentStatus, to: AgentStatus): EdgeStatus {
  if (from === 'pending' || to === 'pending') return 'idle'
  if (from === 'done' && to === 'active') return 'flowing'
  return 'done'
}

function Edge({ d, status }: { d: string; status: EdgeStatus }) {
  const reduce = useReducedMotion()
  return (
    <>
      <path d={d} fill="none" strokeWidth={1.5} className="stroke-slate-200" />
      {status === 'done' && <path d={d} fill="none" strokeWidth={1.5} className="stroke-slate-400" />}
      {status === 'flowing' && (
        <motion.path
          d={d}
          fill="none"
          strokeWidth={1.75}
          strokeDasharray="4 6"
          className="stroke-slate-600"
          animate={reduce ? undefined : { strokeDashoffset: [0, -20] }}
          transition={{ duration: 0.8, ease: 'linear', repeat: Infinity }}
        />
      )}
    </>
  )
}

function LoopArrow({ d, at, dir }: { d: string; at: { x: number; y: number }; dir: 'down' | 'left' }) {
  const reduce = useReducedMotion()
  const head =
    dir === 'down'
      ? `M ${at.x - 5} ${at.y - 7} L ${at.x} ${at.y} L ${at.x + 5} ${at.y - 7}`
      : `M ${at.x + 7} ${at.y - 5} L ${at.x} ${at.y} L ${at.x + 7} ${at.y + 5}`
  return (
    <motion.g initial={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.4 } }}>
      <motion.path
        d={d}
        fill="none"
        strokeWidth={1.75}
        strokeLinecap="round"
        className="stroke-amber-500"
        initial={{ pathLength: reduce ? 1 : 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.5, ease: 'easeInOut' }}
      />
      <motion.path
        d={head}
        fill="none"
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="stroke-amber-500"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: reduce ? 0 : 0.45, duration: 0.15 }}
      />
    </motion.g>
  )
}

const STATUS_LABEL: Record<AgentStatus, string> = {
  pending: 'Queued',
  active: 'Running',
  waiting: 'Awaiting follow-up',
  done: 'Done',
}

function Node({ agent, status, style }: { agent: PipelineAgent; status: AgentStatus; style: CSSProperties }) {
  const reduce = useReducedMotion()
  const Icon = agent.icon
  const active = status === 'active'

  return (
    <div className="absolute" style={style}>
      {active && !reduce && (
        <motion.span
          className="absolute -inset-[3px] rounded-[10px] border border-slate-400"
          initial={{ opacity: 0.7, scale: 1 }}
          animate={{ opacity: 0, scale: 1.06 }}
          transition={{ duration: 1.3, repeat: Infinity, ease: 'easeOut' }}
        />
      )}
      <div
        className={`relative flex h-full items-center gap-2.5 rounded-lg border bg-white px-2.5 transition-[border-color,box-shadow] duration-300 ${
          active
            ? 'border-slate-800 shadow-md ring-1 ring-slate-800'
            : status === 'waiting'
              ? 'border-amber-300 shadow-sm'
              : status === 'done'
                ? 'border-slate-300 shadow-xs'
                : 'border-slate-200'
        }`}
      >
        <span
          className={`relative flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition-colors duration-300 ${
            active
              ? 'bg-slate-900 text-white'
              : status === 'done'
                ? 'bg-emerald-50 text-emerald-700'
                : status === 'waiting'
                  ? 'bg-amber-50 text-amber-700'
                  : 'bg-slate-100 text-slate-400'
          }`}
        >
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.span
              key={status === 'done' ? 'done' : status === 'waiting' ? 'waiting' : 'icon'}
              initial={{ opacity: 0, scale: 0.5 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.5 }}
              transition={{ type: 'spring', stiffness: 500, damping: 26 }}
              className="flex"
            >
              {status === 'done' ? (
                <Check className="h-4 w-4" strokeWidth={2.5} />
              ) : status === 'waiting' ? (
                <Pause className="h-4 w-4" />
              ) : (
                <Icon className="h-4 w-4" />
              )}
            </motion.span>
          </AnimatePresence>
        </span>
        <span className="min-w-0">
          <span className={`block truncate text-[13px] font-medium ${status === 'pending' ? 'text-slate-500' : 'text-slate-900'}`}>
            {agent.name}
          </span>
          <span className="block truncate text-[11px] text-slate-500">
            {active ? <span className="text-slate-700">{STATUS_LABEL.active}…</span> : status === 'pending' ? agent.role : STATUS_LABEL[status]}
          </span>
        </span>
      </div>
    </div>
  )
}
