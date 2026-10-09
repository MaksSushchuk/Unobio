// Traces tab: every recorded step and LLM call of the run (researcher sources,
// analysts' attempts, verdict/finance/findings code steps, writer calls) with
// tokens, latency and cost, a per-agent summary and totals ("cost per run").

import { useMemo, useState, type ReactNode } from 'react'
import type { Report, Trace } from '../../types'

interface AgentSummary {
  agent: string
  steps: number
  input_tokens: number
  output_tokens: number
  latency_s: number
  cost_usd: number
}

const fmtInt = (n: number) => n.toLocaleString()
const fmtSec = (s: number) => (s >= 60 ? `${(s / 60).toFixed(1)} min` : `${s.toFixed(s < 10 ? 2 : 1)} s`)
const fmtUsd = (x: number) => (x === 0 ? '$0' : x < 0.01 ? `$${x.toFixed(4)}` : `$${x.toFixed(3)}`)

function agentLabel(agent: string) {
  return agent.startsWith('lens:') ? `analyst · ${agent.slice(5)}` : agent
}

export default function TracesTab({ report }: { report: Report }) {
  const traces = report.traces
  const [agentFilter, setAgentFilter] = useState('all')

  const summary = useMemo<AgentSummary[]>(() => {
    const m = new Map<string, AgentSummary>()
    for (const t of traces) {
      const s = m.get(t.agent) ?? { agent: t.agent, steps: 0, input_tokens: 0, output_tokens: 0, latency_s: 0, cost_usd: 0 }
      s.steps++
      s.input_tokens += t.input_tokens
      s.output_tokens += t.output_tokens
      s.latency_s += t.latency_s
      s.cost_usd += t.cost_usd
      m.set(t.agent, s)
    }
    return [...m.values()]
  }, [traces])

  const total = summary.reduce(
    (acc, s) => ({
      steps: acc.steps + s.steps,
      input_tokens: acc.input_tokens + s.input_tokens,
      output_tokens: acc.output_tokens + s.output_tokens,
      latency_s: acc.latency_s + s.latency_s,
      cost_usd: acc.cost_usd + s.cost_usd,
    }),
    { steps: 0, input_tokens: 0, output_tokens: 0, latency_s: 0, cost_usd: 0 },
  )
  const llmCalls = traces.filter((t) => t.input_tokens + t.output_tokens > 0).length
  const rows: Trace[] = agentFilter === 'all' ? traces : traces.filter((t) => t.agent === agentFilter)

  if (traces.length === 0)
    return (
      <p className="rounded-lg border border-dashed border-slate-300 px-4 py-8 text-center text-sm text-slate-500">
        This report has no traces.
      </p>
    )

  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Cost per run" value={fmtUsd(total.cost_usd)} />
        <Stat label="LLM calls" value={fmtInt(llmCalls)} />
        <Stat label="Tokens in / out" value={`${fmtInt(total.input_tokens)} / ${fmtInt(total.output_tokens)}`} />
        <Stat label="Step time (sum)" value={fmtSec(total.latency_s)} hint="Parallel analysts overlap, so wall time is shorter" />
      </dl>

      <section>
        <h3 className="mb-2 text-xs font-medium tracking-wide text-slate-500 uppercase">By agent</h3>
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-xs">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-[11px] tracking-wide text-slate-500 uppercase">
              <tr>
                <Th>Agent</Th>
                <Th right>Steps</Th>
                <Th right>Tokens in</Th>
                <Th right>Tokens out</Th>
                <Th right>Time</Th>
                <Th right>Cost</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {summary.map((s) => (
                <tr
                  key={s.agent}
                  onClick={() => setAgentFilter((f) => (f === s.agent ? 'all' : s.agent))}
                  className={`cursor-pointer hover:bg-slate-50 ${agentFilter === s.agent ? 'bg-sky-50/60' : ''}`}
                >
                  <Td>{agentLabel(s.agent)}</Td>
                  <Td right>{s.steps}</Td>
                  <Td right>{fmtInt(s.input_tokens)}</Td>
                  <Td right>{fmtInt(s.output_tokens)}</Td>
                  <Td right>{fmtSec(s.latency_s)}</Td>
                  <Td right>{fmtUsd(s.cost_usd)}</Td>
                </tr>
              ))}
              <tr className="bg-slate-50 font-semibold">
                <Td>Total</Td>
                <Td right>{total.steps}</Td>
                <Td right>{fmtInt(total.input_tokens)}</Td>
                <Td right>{fmtInt(total.output_tokens)}</Td>
                <Td right>{fmtSec(total.latency_s)}</Td>
                <Td right>{fmtUsd(total.cost_usd)}</Td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-medium tracking-wide text-slate-500 uppercase">
            Steps {agentFilter !== 'all' && <span className="normal-case">· {agentLabel(agentFilter)}</span>}
          </h3>
          {agentFilter !== 'all' && (
            <button type="button" onClick={() => setAgentFilter('all')} className="text-xs text-slate-500 hover:text-slate-900">
              Show all
            </button>
          )}
        </div>
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-xs">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-[11px] tracking-wide text-slate-500 uppercase">
              <tr>
                <Th>#</Th>
                <Th>Agent</Th>
                <Th>Step</Th>
                <Th>Tool / model</Th>
                <Th right>Tokens in</Th>
                <Th right>Tokens out</Th>
                <Th right>Time</Th>
                <Th right>Cost</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((t, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <Td muted>{i + 1}</Td>
                    <Td>{agentLabel(t.agent)}</Td>
                    <Td>{t.step}</Td>
                    <Td muted>{t.tool ?? '—'}</Td>
                    <Td right>{t.input_tokens ? fmtInt(t.input_tokens) : '—'}</Td>
                    <Td right>{t.output_tokens ? fmtInt(t.output_tokens) : '—'}</Td>
                    <Td right>{fmtSec(t.latency_s)}</Td>
                    <Td right>{fmtUsd(t.cost_usd)}</Td>
                  </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

function Th({ children, right }: { children: ReactNode; right?: boolean }) {
  return <th className={`px-3 py-2 font-medium ${right ? 'text-right' : ''}`}>{children}</th>
}

function Td({ children, right, muted }: { children: ReactNode; right?: boolean; muted?: boolean }) {
  return (
    <td className={`px-3 py-2 whitespace-nowrap ${right ? 'text-right tabular-nums' : ''} ${muted ? 'text-slate-400' : 'text-slate-700'}`}>
      {children}
    </td>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-xs" title={hint}>
      <dt className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold text-slate-900 tabular-nums">{value}</dd>
    </div>
  )
}
