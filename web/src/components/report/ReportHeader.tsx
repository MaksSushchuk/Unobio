import { AnimatePresence, motion } from 'framer-motion'
import { ChevronRight, CircleCheck, CircleX, FlaskConical, TriangleAlert, Wallet, type LucideIcon } from 'lucide-react'
import { useState } from 'react'
import { rise, stagger } from '../../lib/motion'
import type { CapitalEstimate, Recommendation, Report } from '../../types'
import ConfidenceBar from './ConfidenceBar'

const VERDICT: Record<Recommendation, { label: string; icon: LucideIcon; badge: string; bar: string }> = {
  invest: {
    label: 'Invest',
    icon: CircleCheck,
    badge: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
    bar: 'bg-emerald-500',
  },
  conditional: {
    label: 'Conditional',
    icon: TriangleAlert,
    badge: 'bg-amber-50 text-amber-800 ring-amber-200',
    bar: 'bg-amber-500',
  },
  do_not_invest: {
    label: 'Do not invest',
    icon: CircleX,
    badge: 'bg-rose-50 text-rose-800 ring-rose-200',
    bar: 'bg-rose-500',
  },
}

export default function ReportHeader({ report }: { report: Report }) {
  const { input } = report
  const verdict = VERDICT[report.recommendation]
  const VerdictIcon = verdict.icon

  const chips: [string, string][] = []
  if (input.modality) chips.push(['Modality', input.modality])
  if (input.stage) chips.push(['Stage', input.stage])
  if (input.biomarkers?.length) chips.push(['Biomarkers', input.biomarkers.join(', ')])
  if (input.route) chips.push(['Route', input.route])

  return (
    <motion.header variants={stagger} initial="hidden" animate="show" className="space-y-6">
      {report.is_mock && (
        <motion.p
          variants={rise}
          className="flex w-fit items-center gap-1.5 rounded-full bg-amber-50/80 px-2.5 py-1 text-xs text-amber-800 ring-1 ring-amber-200/70 ring-inset"
        >
          <FlaskConical className="h-3.5 w-3.5" />
          <span className="font-medium">Mock data</span>
          <span className="text-amber-700/80">· fixture content, sources not verified</span>
        </motion.p>
      )}

      <motion.div variants={rise}>
        <h1 className="text-2xl font-semibold tracking-tight">
          {input.mechanism} <span className="text-slate-400">·</span> {input.indication}
        </h1>
        {chips.length > 0 && (
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {chips.map(([label, value]) => (
              <li
                key={label}
                className="rounded-md bg-white px-2 py-0.5 text-xs text-slate-700 ring-1 ring-slate-200 ring-inset"
              >
                <span className="text-slate-400">{label}</span> {value}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 font-mono text-xs text-slate-400">
          {report.id} · {new Date(report.created_at).toLocaleString()}
        </p>
      </motion.div>

      <motion.div variants={rise} className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-xs font-medium tracking-wide text-slate-500 uppercase">Recommendation</h2>
          <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-4">
            <motion.span
              initial={{ opacity: 0, scale: 0.85 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ type: 'spring', stiffness: 260, damping: 18, delay: 0.25 }}
              className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-lg font-semibold ring-1 ring-inset ${verdict.badge}`}
            >
              <VerdictIcon className="h-5 w-5" />
              {verdict.label}
            </motion.span>
            <div className="w-full max-w-56 flex-1">
              <ConfidenceBar value={report.confidence} size="md" tone={verdict.bar} delay={0.35} />
            </div>
          </div>
          <p className="mt-4 text-sm leading-relaxed text-slate-700">{report.summary}</p>
        </section>

        <CapitalCard capital={report.capital} />
      </motion.div>
    </motion.header>
  )
}

const usd = (n: number) =>
  n >= 1e9 ? `$${+(n / 1e9).toFixed(1)}B` : n >= 1e6 ? `$${+(n / 1e6).toFixed(1)}M` : `$${Math.round(n / 1e3)}K`

function CapitalCard({ capital }: { capital: CapitalEstimate }) {
  const [open, setOpen] = useState(false)
  // Axis runs from 0 to a bit past the high estimate so the range sits inside the track.
  const max = capital.usd_high * 1.15
  const pos = (n: number) => `${(n / max) * 100}%`

  return (
    <section className="flex flex-col rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-slate-500 uppercase">
        <Wallet className="h-3.5 w-3.5" />
        Capital to milestone
      </h2>
      <p className="mt-3 text-sm font-medium text-slate-900">{capital.milestone}</p>
      <p className="mt-1 text-xs text-slate-500">
        {capital.months_low}–{capital.months_high} months
      </p>

      <div className="mt-5">
        <div className="relative h-2 rounded-full bg-slate-100">
          <motion.div
            className="absolute inset-y-0 origin-left rounded-full bg-slate-300"
            style={{ left: pos(capital.usd_low), width: pos(capital.usd_high - capital.usd_low) }}
            initial={{ scaleX: 0 }}
            animate={{ scaleX: 1 }}
            transition={{ duration: 0.6, delay: 0.4, ease: 'easeOut' }}
          />
          <motion.div
            aria-hidden
            className="absolute top-1/2 h-4 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-900 ring-2 ring-white"
            style={{ left: pos(capital.usd_base) }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.3, delay: 0.9 }}
          />
        </div>
        <dl className="relative mt-2 h-9 text-xs">
          {(
            [
              ['Low', capital.usd_low, 'text-slate-500'],
              ['Base', capital.usd_base, 'font-semibold text-slate-900'],
              ['High', capital.usd_high, 'text-slate-500'],
            ] as const
          ).map(([label, value, cls]) => (
            <div key={label} className="absolute -translate-x-1/2 text-center" style={{ left: pos(value) }}>
              <dt className="text-[10px] tracking-wide text-slate-400 uppercase">{label}</dt>
              <dd className={`tabular-nums ${cls}`}>{usd(value)}</dd>
            </div>
          ))}
        </dl>
      </div>

      {capital.assumptions.length > 0 && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="flex items-center gap-1.5 text-xs font-medium text-slate-600 hover:text-slate-900"
          >
            <motion.span animate={{ rotate: open ? 90 : 0 }} transition={{ duration: 0.2 }} className="flex">
              <ChevronRight className="h-3.5 w-3.5" />
            </motion.span>
            {capital.assumptions.length} assumptions
          </button>
          <AnimatePresence initial={false}>
            {open && (
              <motion.ul
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.25, ease: 'easeOut' }}
                className="overflow-hidden"
              >
                {capital.assumptions.map((a) => (
                  <li key={a} className="mt-2 flex gap-2 text-xs leading-relaxed text-slate-600">
                    <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-slate-400" />
                    {a}
                  </li>
                ))}
              </motion.ul>
            )}
          </AnimatePresence>
        </div>
      )}
    </section>
  )
}
