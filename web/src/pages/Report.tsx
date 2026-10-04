import { AnimatePresence, motion } from 'framer-motion'
import { Activity, ArrowLeft, BookOpen, Construction, GitCompareArrows, Library, SearchX, type LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { getPreviousRun, getReport } from '../api'
import ReportHeader from '../components/report/ReportHeader'
import ReportTab from '../components/report/ReportTab'
import type { Report } from '../types'

type TabId = 'report' | 'evidence' | 'changes' | 'traces'

const TABS: { id: TabId; label: string; icon: LucideIcon }[] = [
  { id: 'report', label: 'Report', icon: BookOpen },
  { id: 'evidence', label: 'Evidence', icon: Library },
  { id: 'changes', label: 'What changed', icon: GitCompareArrows },
  { id: 'traces', label: 'Traces', icon: Activity },
]

interface Loaded {
  id: string
  report?: Report
  previous?: Report | null
}

export default function ReportPage() {
  const { id = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  // Tagged with the id it was loaded for, so a stale result reads as "loading".
  const [loaded, setLoaded] = useState<Loaded | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([getReport(id), getPreviousRun(id)])
      .then(([report, previous]) => !cancelled && setLoaded({ id, report, previous }))
      .catch(() => !cancelled && setLoaded({ id }))
    return () => {
      cancelled = true
    }
  }, [id])

  if (loaded?.id !== id) return <p className="text-sm text-slate-500">Loading…</p>
  if (!loaded.report) return <NotFound id={id} />
  const { report, previous } = loaded

  const tabs = TABS.filter((t) => t.id !== 'changes' || previous)
  const requested = searchParams.get('tab')
  const tab: TabId = tabs.find((t) => t.id === requested)?.id ?? 'report'

  function selectTab(next: TabId) {
    setSearchParams(
      (params) => {
        if (next === 'report') params.delete('tab')
        else params.set('tab', next)
        return params
      },
      { preventScrollReset: true },
    )
  }

  return (
    <div className="space-y-8">
      <ReportHeader report={report} />

      <div>
        <div role="tablist" aria-label="Report views" className="flex gap-6 overflow-x-auto border-b border-slate-200">
          {tabs.map((t) => {
            const Icon = t.icon
            const active = t.id === tab
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                id={`tab-${t.id}`}
                aria-selected={active}
                aria-controls={`panel-${t.id}`}
                onClick={() => selectTab(t.id)}
                className={`relative flex shrink-0 items-center gap-1.5 pt-1 pb-3 text-sm font-medium transition-colors ${
                  active ? 'text-slate-900' : 'text-slate-500 hover:text-slate-900'
                }`}
              >
                <Icon className="h-4 w-4" />
                {t.label}
                {active && (
                  <motion.span
                    layoutId="report-tab-underline"
                    className="absolute inset-x-0 -bottom-px h-0.5 bg-slate-900"
                    transition={{ type: 'spring', stiffness: 500, damping: 40 }}
                  />
                )}
              </button>
            )
          })}
        </div>

        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={tab}
            role="tabpanel"
            id={`panel-${tab}`}
            aria-labelledby={`tab-${tab}`}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            className="pt-8"
          >
            {tab === 'report' ? (
              <ReportTab report={report} />
            ) : (
              <ComingSoon label={TABS.find((t) => t.id === tab)!.label} />
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}

function ComingSoon({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-300 bg-white/60 px-6 py-16 text-center">
      <Construction className="h-6 w-6 text-slate-400" />
      <p className="mt-3 text-sm font-medium text-slate-900">{label} — coming soon</p>
      <p className="mt-1 text-sm text-slate-500">This view isn’t built yet.</p>
    </div>
  )
}

function NotFound({ id }: { id: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="mx-auto mt-10 flex max-w-md flex-col items-center rounded-xl border border-slate-200 bg-white px-8 py-12 text-center shadow-sm"
    >
      <span className="flex h-11 w-11 items-center justify-center rounded-full bg-slate-100 text-slate-500">
        <SearchX className="h-5 w-5" />
      </span>
      <h1 className="mt-4 text-lg font-semibold tracking-tight">Run not found</h1>
      <p className="mt-1 text-sm text-slate-500">
        There’s no analysis with id <span className="font-mono text-slate-700">{id}</span>. It may have been removed,
        or the link is mistyped.
      </p>
      <Link
        to="/"
        className="mt-6 inline-flex items-center gap-1.5 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-slate-800"
      >
        <ArrowLeft className="h-4 w-4" />
        New analysis
      </Link>
    </motion.div>
  )
}
