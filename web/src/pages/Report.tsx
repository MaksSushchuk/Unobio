import { useEffect, useState } from 'react'
import { useParams } from 'react-router'
import { getReport } from '../api'
import type { Recommendation, Report } from '../types'

const RECOMMENDATION_LABEL: Record<Recommendation, string> = {
  invest: 'Invest',
  conditional: 'Conditional',
  do_not_invest: 'Do not invest',
}

const RECOMMENDATION_STYLE: Record<Recommendation, string> = {
  invest: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  conditional: 'bg-amber-50 text-amber-800 ring-amber-200',
  do_not_invest: 'bg-rose-50 text-rose-800 ring-rose-200',
}

// Placeholder — tabs (Report / Evidence / What changed / Traces) come next.
export default function ReportPage() {
  const { id = '' } = useParams()
  // Tagged with the id it was loaded for, so a stale result reads as "loading".
  const [loaded, setLoaded] = useState<{ id: string; report?: Report; error?: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    getReport(id)
      .then((report) => !cancelled && setLoaded({ id, report }))
      .catch((e: Error) => !cancelled && setLoaded({ id, error: e.message }))
    return () => {
      cancelled = true
    }
  }, [id])

  if (loaded?.id !== id) return <p className="text-sm text-slate-500">Loading…</p>
  if (!loaded.report) return <p className="text-sm text-rose-700">{loaded.error}</p>
  const { report } = loaded

  const { input } = report
  const rows: [string, string | undefined][] = [
    ['Indication', input.indication],
    ['Mechanism', input.mechanism],
    ['Modality', input.modality],
    ['Stage', input.stage],
    ['Biomarkers', input.biomarkers?.join(', ')],
    ['Route', input.route],
  ]

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">
          {input.mechanism} · {input.indication}
        </h1>
        {report.is_mock && (
          <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200 ring-inset">
            Mock data
          </span>
        )}
      </div>
      <p className="font-mono text-xs text-slate-400">
        {report.id} · {new Date(report.created_at).toLocaleString()}
      </p>

      <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-xs font-medium uppercase tracking-wide text-slate-500">Recommendation</h2>
        <div className="mt-3 flex items-center gap-4">
          <span
            className={`rounded-md px-3 py-1 text-sm font-semibold ring-1 ring-inset ${RECOMMENDATION_STYLE[report.recommendation]}`}
          >
            {RECOMMENDATION_LABEL[report.recommendation]}
          </span>
          <span className="text-sm text-slate-600">
            Confidence <span className="font-medium text-slate-900 tabular-nums">{Math.round(report.confidence * 100)}%</span>
          </span>
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-xs font-medium uppercase tracking-wide text-slate-500">Input</h2>
        <dl className="mt-3 grid gap-x-8 gap-y-3 sm:grid-cols-2">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-slate-500">{label}</dt>
              <dd className="text-sm text-slate-900">{value ?? <span className="text-slate-400">—</span>}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  )
}
