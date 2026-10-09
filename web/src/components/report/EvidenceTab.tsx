// Evidence tab: every evidence item of the report, filterable by kind, source,
// module and stance. Stance is derived from the claims that cite the item
// (an item can support one claim and contradict another). Conflicts and
// contradicting evidence are highlighted, as the contract notes ask.

import { ExternalLink, Search, TriangleAlert } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { Evidence, EvidenceKind, Report } from '../../types'

type StanceFilter = 'all' | 'supports' | 'contradicts' | 'uncited'

interface Row {
  evidence: Evidence
  supports: number
  contradicts: number
  /** Titles of the claims citing this item (for the tooltip). */
  claims: string[]
}

const KIND_LABEL: Record<EvidenceKind, string> = {
  record: 'Record',
  literature: 'Literature',
  web: 'Web',
  conflict: 'Conflict',
}

export default function EvidenceTab({ report }: { report: Report }) {
  const rows = useMemo<Row[]>(() => {
    const byId = new Map<string, Row>(
      report.evidence.map((e) => [e.id, { evidence: e, supports: 0, contradicts: 0, claims: [] }]),
    )
    for (const s of report.sections)
      for (const c of s.claims)
        for (const ref of c.evidence) {
          const row = byId.get(ref.evidence_id)
          if (!row) continue
          if (ref.stance === 'supports') row.supports++
          else row.contradicts++
          row.claims.push(c.text)
        }
    // Conflicts first, then cited items, then the rest; newest retrieval order is meaningless, keep input order.
    const rank = (r: Row) => (r.evidence.kind === 'conflict' ? 0 : r.supports + r.contradicts > 0 ? 1 : 2)
    return [...byId.values()].sort((a, b) => rank(a) - rank(b))
  }, [report])

  const sources = useMemo(() => [...new Set(report.evidence.map((e) => e.source))].sort(), [report.evidence])
  const modules = useMemo(() => [...new Set(report.evidence.flatMap((e) => e.modules))].sort(), [report.evidence])
  const kinds = useMemo(() => [...new Set(report.evidence.map((e) => e.kind))], [report.evidence])

  const [kind, setKind] = useState<'all' | EvidenceKind>('all')
  const [source, setSource] = useState('all')
  const [module, setModule] = useState('all')
  const [stance, setStance] = useState<StanceFilter>('all')
  const [query, setQuery] = useState('')

  const visible = rows.filter(({ evidence: e, supports, contradicts }) => {
    if (kind !== 'all' && e.kind !== kind) return false
    if (source !== 'all' && e.source !== source) return false
    if (module !== 'all' && !e.modules.includes(module)) return false
    if (stance === 'supports' && supports === 0) return false
    if (stance === 'contradicts' && contradicts === 0) return false
    if (stance === 'uncited' && supports + contradicts > 0) return false
    const q = query.trim().toLowerCase()
    return !q || `${e.title} ${e.snippet} ${e.source}`.toLowerCase().includes(q)
  })

  const cited = rows.filter((r) => r.supports + r.contradicts > 0).length
  const conflicts = rows.filter((r) => r.evidence.kind === 'conflict').length

  return (
    <div className="space-y-5">
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Evidence items" value={rows.length} />
        <Stat label="Cited by claims" value={cited} />
        <Stat label="Conflicts" value={conflicts} tone={conflicts ? 'text-amber-700' : undefined} />
        <Stat label="Sources" value={sources.length} />
      </dl>

      <div className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-xs">
        <label className="relative min-w-48 flex-1">
          <span className="sr-only">Search</span>
          <Search className="pointer-events-none absolute top-2.5 left-2.5 h-4 w-4 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search title or snippet"
            className="block w-full rounded-md border border-slate-300 py-2 pr-3 pl-8 text-sm focus:border-slate-500 focus:ring-2 focus:ring-slate-200 focus:outline-none"
          />
        </label>
        <Select label="Kind" value={kind} onChange={(v) => setKind(v as 'all' | EvidenceKind)}
          options={kinds.map((k) => [k, KIND_LABEL[k]])} />
        <Select label="Source" value={source} onChange={setSource} options={sources.map((s) => [s, s])} />
        <Select label="Module" value={module} onChange={setModule} options={modules.map((m) => [m, m])} />
        <Select
          label="Stance"
          value={stance}
          onChange={(v) => setStance(v as StanceFilter)}
          options={[
            ['supports', 'Supports a claim'],
            ['contradicts', 'Contradicts a claim'],
            ['uncited', 'Not cited'],
          ]}
        />
      </div>

      <p className="text-xs text-slate-500">
        Showing {visible.length} of {rows.length}
      </p>

      <ul className="space-y-2">
        {visible.map((row) => (
          <EvidenceRow key={row.evidence.id} row={row} />
        ))}
        {visible.length === 0 && (
          <li className="rounded-lg border border-dashed border-slate-300 px-4 py-8 text-center text-sm text-slate-500">
            No evidence matches the filters.
          </li>
        )}
      </ul>
    </div>
  )
}

function EvidenceRow({ row }: { row: Row }) {
  const { evidence: e, supports, contradicts, claims } = row
  const conflict = e.kind === 'conflict'
  const tone = conflict
    ? 'border-amber-300 bg-amber-50/50'
    : contradicts > 0
      ? 'border-rose-200 bg-rose-50/40'
      : 'border-slate-200 bg-white'
  return (
    <li className={`rounded-lg border px-4 py-3 shadow-xs ${tone}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
        <span className="font-semibold tracking-wide text-slate-600 uppercase">{e.source}</span>
        <span className="text-slate-300">·</span>
        <span className="text-slate-500">{KIND_LABEL[e.kind]}</span>
        {conflict && (
          <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-px font-medium text-amber-800">
            <TriangleAlert className="h-3 w-3" />
            Conflicting sources{e.related_evidence_ids?.length ? ` · ${e.related_evidence_ids.length} related` : ''}
          </span>
        )}
        {e.modules.map((m) => (
          <span key={m} className="rounded bg-slate-100 px-1.5 py-px text-slate-600">
            {m}
          </span>
        ))}
        <span className="ml-auto flex gap-2 font-medium" title={claims.slice(0, 5).join('\n')}>
          {supports > 0 && <span className="text-emerald-700">supports {supports}</span>}
          {contradicts > 0 && <span className="text-rose-700">contradicts {contradicts}</span>}
          {supports + contradicts === 0 && <span className="font-normal text-slate-400">not cited</span>}
        </span>
      </div>
      <a
        href={e.url}
        target="_blank"
        rel="noopener noreferrer"
        className="group mt-1 inline-flex items-start gap-1 text-sm font-medium text-slate-900 hover:underline"
      >
        {e.title}
        <ExternalLink className="mt-1 h-3 w-3 shrink-0 text-slate-400 group-hover:text-slate-700" />
      </a>
      {e.snippet && <p className="mt-1 text-xs leading-relaxed text-slate-600">{e.snippet}</p>}
    </li>
  )
}

function Select(props: { label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <label className="block">
      <span className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">{props.label}</span>
      <select
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        className="mt-1 block rounded-md border border-slate-300 bg-white px-2.5 py-2 text-sm focus:border-slate-500 focus:ring-2 focus:ring-slate-200 focus:outline-none"
      >
        <option value="all">All</option>
        {props.options.map(([v, label]) => (
          <option key={v} value={v}>
            {label}
          </option>
        ))}
      </select>
    </label>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-xs">
      <dt className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">{label}</dt>
      <dd className={`mt-0.5 text-lg font-semibold tabular-nums ${tone ?? 'text-slate-900'}`}>{value}</dd>
    </div>
  )
}
