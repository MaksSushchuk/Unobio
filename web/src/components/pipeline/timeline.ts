// Compiles PIPELINE into a flat timeline of timed events, and derives the
// visible run state for any elapsed time. Keeping the state a pure function of
// `elapsed` means the UI only needs a ticking clock.

import type { PipelineStage } from '../../pipeline.config'
import type { ReportInput } from '../../types'

export type AgentStatus = 'pending' | 'active' | 'waiting' | 'done'

interface Segment {
  agent_id: string
  start: number
  end: number
  cost_usd: number
  follow_up: boolean
}

interface SourceHit {
  at: number
  source_id: string
  records: number
  evidence: number
  follow_up: boolean
}

export interface LogEvent {
  at: number
  agent: string
  message: string
}

interface Loop {
  from_stage: string
  to_stage: string
  label: string
  start: number
  end: number
}

export interface Timeline {
  segments: Segment[]
  hits: SourceHit[]
  logs: LogEvent[]
  loops: Loop[]
  /** When the last agent finishes. */
  end: number
}

export function compileTimeline(pipeline: PipelineStage[], input: ReportInput): Timeline {
  const segments: Segment[] = []
  const hits: SourceHit[] = []
  const logs: LogEvent[] = [{ at: 0, agent: 'orchestrator', message: 'Run started' }]
  const loops: Loop[] = []
  const sourceName = new Map(pipeline.flatMap((s) => s.sources ?? []).map((s) => [s.id, s.name]))
  let t = 0

  for (const stage of pipeline) {
    const stageDuration = Math.max(...stage.agents.map((a) => a.duration_ms))
    const fu = stage.follow_up
    // A follow-up pauses this stage's agents halfway and re-runs the target stage.
    const pauseAt = fu ? stageDuration / 2 : Infinity
    const pause = fu ? fu.duration_ms : 0

    for (const agent of stage.agents) {
      logs.push({ at: t, agent: agent.name, message: agent.log(input) })
      if (agent.duration_ms <= pauseAt) {
        segments.push({ agent_id: agent.id, start: t, end: t + agent.duration_ms, cost_usd: agent.cost_usd, follow_up: false })
      } else {
        const share = pauseAt / agent.duration_ms
        segments.push({ agent_id: agent.id, start: t, end: t + pauseAt, cost_usd: agent.cost_usd * share, follow_up: false })
        segments.push({
          agent_id: agent.id,
          start: t + pauseAt + pause,
          end: t + agent.duration_ms + pause,
          cost_usd: agent.cost_usd * (1 - share),
          follow_up: false,
        })
      }
    }

    stage.sources?.forEach((src, i) => {
      const at = t + ((i + 0.6) / stage.sources!.length) * stageDuration * 0.9
      hits.push({ at, source_id: src.id, records: src.records, evidence: src.evidence, follow_up: false })
      logs.push({ at, agent: stage.agents[0].name, message: `${src.name}: ${src.records} records, ${src.evidence} kept as evidence` })
    })

    if (fu) {
      const target = pipeline.find((s) => s.id === fu.target_stage)
      if (!target) throw new Error(`follow_up.target_stage not found: ${fu.target_stage}`)
      const start = t + pauseAt
      loops.push({ from_stage: stage.id, to_stage: target.id, label: fu.label, start, end: start + pause })
      logs.push({ at: start, agent: stage.agents[0].name, message: `Requesting ${fu.label}` })
      logs.push({ at: start + 300, agent: target.agents[0].name, message: fu.log })
      // Target agents re-run after the arrow has drawn in.
      const runStart = start + 500
      for (const agent of target.agents) {
        segments.push({ agent_id: agent.id, start: runStart, end: start + pause, cost_usd: fu.cost_usd / target.agents.length, follow_up: true })
      }
      fu.sources.forEach((src, i) => {
        const at = runStart + ((i + 0.6) / fu.sources.length) * (pause - 500) * 0.85
        hits.push({ at, source_id: src.source_id, records: src.records, evidence: src.evidence, follow_up: true })
        logs.push({ at, agent: target.agents[0].name, message: `${sourceName.get(src.source_id)}: +${src.records} records, +${src.evidence} evidence` })
      })
    }

    t += stageDuration + pause
  }

  logs.push({ at: t, agent: 'orchestrator', message: 'Report ready' })
  logs.sort((a, b) => a.at - b.at)
  return { segments, hits, logs, loops, end: t }
}

export interface SourceState {
  records: number
  evidence: number
  /** Time of the latest follow-up hit, used to flash the chip. */
  follow_up_at: number | null
  last_follow_up_records: number
}

export interface RunState {
  agent: (id: string) => AgentStatus
  sources: Map<string, SourceState>
  evidence: number
  cost_usd: number
  logs: LogEvent[]
  activeLoops: Loop[]
  done: boolean
}

export function deriveState(tl: Timeline, elapsed: number): RunState {
  const agent = (id: string): AgentStatus => {
    const segs = tl.segments.filter((s) => s.agent_id === id)
    if (segs.some((s) => s.start <= elapsed && elapsed < s.end)) return 'active'
    if (segs.every((s) => s.end <= elapsed)) return 'done'
    if (segs.every((s) => s.start > elapsed)) return 'pending'
    // Between segments: a later follow-up run means this agent already finished
    // its own work; otherwise it paused itself and is waiting on a follow-up.
    const next = segs.find((s) => s.start > elapsed)!
    return next.follow_up ? 'done' : 'waiting'
  }

  const sources = new Map<string, SourceState>()
  let evidence = 0
  for (const h of tl.hits) {
    if (h.at > elapsed) continue
    const s = sources.get(h.source_id) ?? { records: 0, evidence: 0, follow_up_at: null, last_follow_up_records: 0 }
    s.records += h.records
    s.evidence += h.evidence
    if (h.follow_up) {
      s.follow_up_at = h.at
      s.last_follow_up_records = h.records
    }
    sources.set(h.source_id, s)
    evidence += h.evidence
  }

  let cost_usd = 0
  for (const s of tl.segments) {
    const p = Math.min(1, Math.max(0, (elapsed - s.start) / (s.end - s.start)))
    cost_usd += s.cost_usd * p
  }

  return {
    agent,
    sources,
    evidence,
    cost_usd,
    logs: tl.logs.filter((l) => l.at <= elapsed),
    activeLoops: tl.loops.filter((l) => l.start <= elapsed && elapsed < l.end),
    done: elapsed >= tl.end,
  }
}
