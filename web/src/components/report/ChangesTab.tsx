// What changed: this run against the previous run of the same input, computed
// client-side by diffReports (src/lib/diff.ts). Typical use: the same thesis
// with evidence as of an earlier date vs today — the verdict flip and the new
// evidence that caused it.

import { ArrowRight, ExternalLink, Minus, Plus, RefreshCw } from 'lucide-react'
import { useMemo, type ReactNode } from 'react'
import { Link } from 'react-router'
import { diffReports, type ListDiff } from '../../lib/diff'
import type { Evidence, Recommendation, Report } from '../../types'

const VERDICT: Record<Recommendation, { label: string; tone: string }> = {
  invest: { label: 'Invest', tone: 'bg-emerald-50 text-emerald-800 ring-emerald-200' },
  conditional: { label: 'Conditional', tone: 'bg-amber-50 text-amber-800 ring-amber-200' },
  do_not_invest: { label: 'Do not invest', tone: 'bg-rose-50 text-rose-800 ring-rose-200' },
}

const usd = (x: number) => `${x < 0 ? '-' : ''}$${(Math.abs(x) / 1e6).toFixed(1)}M`

export default function ChangesTab({ previous, report }: { previous: Report; report: Report }) {
  const d = useMemo(() => diffReports(previous, report), [previous, report])
  const asOf = (r: Report) => {
    const details = (r as Report & { details?: { evidence_cutoff?: string | null } }).details
    return details?.evidence_cutoff ? `evidence as of ${details.evidence_cutoff}` : 'evidence as of run date'
  }

  return (
    <div className="space-y-6">
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="grid items-center gap-4 sm:grid-cols-[1fr_auto_1fr]">
          <RunCard label="Previous run" r={previous} sub={asOf(previous)} link />
          <ArrowRight className="mx-auto hidden h-5 w-5 text-slate-400 sm:block" />
          <RunCard label="This run" r={report} sub={asOf(report)} />
        </div>
        <p className="mt-4 text-sm text-slate-600">
          {d.recommendation.changed ? (
            <>
              Recommendation changed from <b>{VERDICT[d.recommendation.a].label}</b> to{' '}
              <b>{VERDICT[d.recommendation.b].label}</b>
            </>
          ) : (
            <>Recommendation unchanged ({VERDICT[d.recommendation.b].label})</>
          )}
          ; confidence {Math.round(d.confidence.a * 100)}% → {Math.round(d.confidence.b * 100)}% (
          {d.confidence.delta >= 0 ? '+' : ''}
          {Math.round(d.confidence.delta * 100)} pts).
        </p>
      </section>

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Counts label="Evidence" diff={d.evidence} />
        <Counts label="Claims" diff={d.claims} />
        <Counts label="Risks" diff={d.risks} />
        <Counts label="Unknowns" diff={d.unknowns} />
      </dl>

      <Block title="New evidence" count={d.evidence.added.length}>
        <EvidenceList items={d.evidence.added} tone="add" />
      </Block>
      <Block title="Evidence no longer present" count={d.evidence.removed.length}>
        <EvidenceList items={d.evidence.removed} tone="remove" />
      </Block>

      <Block title="Risks" count={d.risks.added.length + d.risks.removed.length + d.risks.changed.length}>
        <ul className="space-y-1.5">
          {d.risks.added.map((r) => (
            <Line key={`a${r.id}`} tone="add">
              <b className="font-medium">[{r.severity}]</b> {r.title}
            </Line>
          ))}
          {d.risks.changed.map((c) => (
            <Line key={`c${c.b.id}`} tone="change">
              <b className="font-medium">[{c.a.severity} → {c.b.severity}]</b> {c.b.title}
            </Line>
          ))}
          {d.risks.removed.map((r) => (
            <Line key={`r${r.id}`} tone="remove">
              <b className="font-medium">[{r.severity}]</b> {r.title}
            </Line>
          ))}
        </ul>
      </Block>

      <Block title="Claims" count={d.claims.added.length + d.claims.removed.length + d.claims.changed.length}>
        <ul className="space-y-1.5">
          {d.claims.added.map((c, i) => (
            <Line key={`a${i}`} tone="add">
              <span className="text-slate-400">{c.section_title}:</span> {c.claim.text}
            </Line>
          ))}
          {d.claims.changed.map((c, i) => (
            <Line key={`c${i}`} tone="change">
              <span className="text-slate-400">{c.b.section_title}:</span> {c.b.claim.text}{' '}
              <span className="text-xs text-slate-500">
                ({c.claim_fields.join(', ')}
                {c.claim_fields.includes('confidence') &&
                  `: ${Math.round(c.a.claim.confidence * 100)}% → ${Math.round(c.b.claim.confidence * 100)}%`}
                )
              </span>
            </Line>
          ))}
          {d.claims.removed.map((c, i) => (
            <Line key={`r${i}`} tone="remove">
              <span className="text-slate-400">{c.section_title}:</span> {c.claim.text}
            </Line>
          ))}
        </ul>
      </Block>

      <Block title="Critical unknowns" count={d.unknowns.added.length + d.unknowns.removed.length}>
        <ul className="space-y-1.5">
          {d.unknowns.added.map((u) => (
            <Line key={`a${u}`} tone="add">{u}</Line>
          ))}
          {d.unknowns.removed.map((u) => (
            <Line key={`r${u}`} tone="remove">{u}</Line>
          ))}
        </ul>
      </Block>

      <Block title="Capital to milestone" count={d.capital.fields.length}>
        <p className="text-sm text-slate-700">
          {d.capital.a.milestone === d.capital.b.milestone ? d.capital.b.milestone : `${d.capital.a.milestone} → ${d.capital.b.milestone}`}
          : base {usd(d.capital.a.usd_base)} → {usd(d.capital.b.usd_base)}, {d.capital.a.months_low}–
          {d.capital.a.months_high} → {d.capital.b.months_low}–{d.capital.b.months_high} months
        </p>
      </Block>
    </div>
  )
}

function RunCard({ label, r, sub, link }: { label: string; r: Report; sub: string; link?: boolean }) {
  const v = VERDICT[r.recommendation]
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">{label}</p>
      <div className="mt-1 flex items-center gap-2">
        <span className={`rounded-md px-2 py-0.5 text-sm font-semibold ring-1 ring-inset ${v.tone}`}>{v.label}</span>
        <span className="text-sm text-slate-500 tabular-nums">{Math.round(r.confidence * 100)}%</span>
      </div>
      <p className="mt-1 truncate text-xs text-slate-500">
        {sub} ·{' '}
        {link ? (
          <Link to={`/runs/${r.id}`} className="font-mono hover:underline">
            {r.id}
          </Link>
        ) : (
          <span className="font-mono">{r.id}</span>
        )}
      </p>
    </div>
  )
}

function Counts<T>({ label, diff }: { label: string; diff: Pick<ListDiff<T>, 'added' | 'removed'> & { changed: unknown[] } }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-xs">
      <dt className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">{label}</dt>
      <dd className="mt-0.5 flex gap-3 text-sm font-semibold tabular-nums">
        <span className="text-emerald-700">+{diff.added.length}</span>
        <span className="text-rose-700">−{diff.removed.length}</span>
        <span className="text-sky-700">~{diff.changed.length}</span>
      </dd>
    </div>
  )
}

function Block({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  if (count === 0) return null
  return (
    <section>
      <h3 className="mb-2 text-xs font-medium tracking-wide text-slate-500 uppercase">
        {title} <span className="text-slate-400">({count})</span>
      </h3>
      {children}
    </section>
  )
}

const TONE = {
  add: { icon: Plus, cls: 'border-emerald-200 bg-emerald-50/50 text-emerald-700' },
  remove: { icon: Minus, cls: 'border-rose-200 bg-rose-50/50 text-rose-700' },
  change: { icon: RefreshCw, cls: 'border-sky-200 bg-sky-50/50 text-sky-700' },
}

function Line({ tone, children }: { tone: keyof typeof TONE; children: ReactNode }) {
  const { icon: Icon, cls } = TONE[tone]
  return (
    <li className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm ${cls}`}>
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0 text-slate-800">{children}</span>
    </li>
  )
}

function EvidenceList({ items, tone }: { items: Evidence[]; tone: 'add' | 'remove' }) {
  return (
    <ul className="space-y-1.5">
      {items.map((e) => (
        <Line key={e.id} tone={tone}>
          <span className="text-xs font-medium tracking-wide text-slate-500 uppercase">{e.source}</span>{' '}
          {e.kind === 'conflict' && <span className="rounded bg-amber-100 px-1 text-xs text-amber-800">conflict</span>}{' '}
          <a href={e.url} target="_blank" rel="noopener noreferrer" className="hover:underline">
            {e.title}
            <ExternalLink className="ml-1 inline h-3 w-3 text-slate-400" />
          </a>
        </Line>
      ))}
    </ul>
  )
}
