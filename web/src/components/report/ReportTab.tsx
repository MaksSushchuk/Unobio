import { motion, useReducedMotion } from 'framer-motion'
import {
  CircleHelp,
  Factory,
  FlaskConical,
  Lock,
  MessagesSquare,
  ShieldAlert,
  Stethoscope,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { rise, stagger } from '../../lib/motion'
import type { Claim, DiligenceRequirement, Report, Severity } from '../../types'
import ClaimCard from './ClaimCard'

const SEVERITY_ORDER: Severity[] = ['high', 'medium', 'low']

const SEVERITY_STYLE: Record<Severity, { label: string; dot: string; text: string }> = {
  high: { label: 'High severity', dot: 'bg-rose-500', text: 'text-rose-700' },
  medium: { label: 'Medium severity', dot: 'bg-amber-500', text: 'text-amber-700' },
  low: { label: 'Low severity', dot: 'bg-slate-400', text: 'text-slate-600' },
}

const REQUIREMENT: Record<DiligenceRequirement, { label: string; icon: LucideIcon }> = {
  proprietary_data: { label: 'Proprietary data', icon: Lock },
  patient_data: { label: 'Patient data', icon: Stethoscope },
  kol: { label: 'KOL input', icon: MessagesSquare },
  experiment: { label: 'Experiment', icon: FlaskConical },
  cmc_ip: { label: 'CMC / IP', icon: Factory },
}

const RISKS_ID = 'risks'
const UNKNOWNS_ID = 'unknowns'
const DILIGENCE_ID = 'diligence'

const claimDomId = (claimId: string) => `claim-${claimId}`

export default function ReportTab({ report }: { report: Report }) {
  const reduce = useReducedMotion()
  const evidenceById = useMemo(() => new Map(report.evidence.map((e) => [e.id, e])), [report.evidence])
  const claimById = useMemo(() => {
    const m = new Map<string, Claim>()
    for (const s of report.sections) for (const c of s.claims) m.set(c.id, c)
    return m
  }, [report.sections])

  // Bumped per click so re-clicking the same claim replays its highlight.
  const [highlight, setHighlight] = useState<{ claimId: string; n: number } | null>(null)

  const toc = [
    ...report.sections.map((s) => ({ id: s.id, title: s.title, count: s.claims.length })),
    { id: RISKS_ID, title: 'Risks', count: report.risks.length },
    { id: UNKNOWNS_ID, title: 'Critical unknowns', count: report.unknowns.length },
    { id: DILIGENCE_ID, title: 'Diligence questions', count: report.diligence_questions.length },
  ]
  const activeId = useActiveSection(toc.map((t) => t.id))

  function scrollTo(domId: string, block: ScrollLogicalPosition) {
    document.getElementById(domId)?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block })
  }

  function goToClaim(claimId: string) {
    scrollTo(claimDomId(claimId), 'center')
    setHighlight((h) => ({ claimId, n: (h?.n ?? 0) + 1 }))
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[13rem_1fr]">
      <nav aria-label="Report contents" className="hidden lg:block">
        <div className="sticky top-6">
          <h2 className="mb-2 text-xs font-medium tracking-wide text-slate-500 uppercase">Contents</h2>
          <ul className="space-y-0.5 border-l border-slate-200">
            {toc.map((t) => {
              const active = t.id === activeId
              return (
                <li key={t.id} className="relative">
                  {active && (
                    <motion.span
                      layoutId="toc-active"
                      className="absolute inset-y-0 -left-px w-0.5 bg-slate-900"
                      transition={{ type: 'spring', stiffness: 400, damping: 35 }}
                    />
                  )}
                  <a
                    href={`#${t.id}`}
                    onClick={(e) => {
                      e.preventDefault()
                      scrollTo(t.id, 'start')
                    }}
                    className={`flex items-baseline justify-between gap-2 py-1.5 pr-1 pl-3 text-[13px] leading-snug transition-colors ${
                      active ? 'font-medium text-slate-900' : 'text-slate-500 hover:text-slate-900'
                    }`}
                  >
                    <span>{t.title}</span>
                    <span className="shrink-0 text-xs text-slate-400 tabular-nums">{t.count}</span>
                  </a>
                </li>
              )
            })}
          </ul>
        </div>
      </nav>

      <motion.div variants={stagger} initial="hidden" animate="show" className="min-w-0 space-y-10">
        {report.sections.map((section) => (
          <motion.section key={section.id} id={section.id} variants={rise} className="scroll-mt-6">
            <h2 className="text-lg font-semibold tracking-tight">{section.title}</h2>
            {section.summary && (
              <p className="mt-1.5 border-l-2 border-slate-300 pl-3 text-sm leading-relaxed text-slate-600">
                {section.summary}
              </p>
            )}
            <ul className="mt-4 space-y-3">
              {section.claims.map((claim) => (
                <ClaimCard
                  key={claim.id}
                  claim={claim}
                  domId={claimDomId(claim.id)}
                  evidenceById={evidenceById}
                  highlight={highlight?.claimId === claim.id ? highlight.n : 0}
                />
              ))}
            </ul>
          </motion.section>
        ))}

        <motion.section id={RISKS_ID} variants={rise} className="scroll-mt-6">
          <SectionHeading icon={ShieldAlert} title="Risks" />
          <div className="mt-4 space-y-6">
            {SEVERITY_ORDER.map((severity) => {
              const risks = report.risks.filter((r) => r.severity === severity)
              if (!risks.length) return null
              const style = SEVERITY_STYLE[severity]
              return (
                <div key={severity}>
                  <h3 className={`flex items-center gap-2 text-xs font-medium tracking-wide uppercase ${style.text}`}>
                    <span className={`h-2 w-2 rounded-full ${style.dot}`} />
                    {style.label}
                    <span className="text-slate-400 tabular-nums">{risks.length}</span>
                  </h3>
                  <ul className="mt-2 space-y-3">
                    {risks.map((risk) => (
                      <li key={risk.id} className="rounded-lg border border-slate-200 bg-white px-4 py-3.5 shadow-xs">
                        <p className="text-sm font-medium text-slate-900">{risk.title}</p>
                        <p className="mt-1 text-sm leading-relaxed text-slate-600">{risk.description}</p>
                        {risk.claim_ids.length > 0 && (
                          <div className="mt-3 flex flex-wrap gap-1.5">
                            {risk.claim_ids.map((id) => {
                              const claim = claimById.get(id)
                              if (!claim) return null
                              return (
                                <button
                                  key={id}
                                  type="button"
                                  onClick={() => goToClaim(id)}
                                  title={claim.text}
                                  className="max-w-full truncate rounded-md bg-slate-50 px-2 py-1 text-left text-xs text-slate-600 ring-1 ring-slate-200 transition-colors ring-inset hover:bg-slate-900 hover:text-white hover:ring-slate-900 sm:max-w-xs"
                                >
                                  ↳ {claim.text}
                                </button>
                              )
                            })}
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )
            })}
          </div>
        </motion.section>

        <motion.section id={UNKNOWNS_ID} variants={rise} className="scroll-mt-6">
          <SectionHeading icon={CircleHelp} title="Critical unknowns" />
          <ul className="mt-4 divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white shadow-xs">
            {report.unknowns.map((u) => (
              <li key={u} className="flex gap-3 px-4 py-3 text-sm leading-relaxed text-slate-700">
                <CircleHelp className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
                {u}
              </li>
            ))}
          </ul>
        </motion.section>

        <motion.section id={DILIGENCE_ID} variants={rise} className="scroll-mt-6">
          <SectionHeading icon={MessagesSquare} title="Diligence questions" />
          <ol className="mt-4 space-y-3">
            {report.diligence_questions.map((q, i) => {
              const req = REQUIREMENT[q.requires]
              const ReqIcon = req.icon
              return (
                <li key={q.question} className="flex gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3.5 shadow-xs">
                  <span className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-600 tabular-nums">
                    {i + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <p className="min-w-0 flex-1 text-sm font-medium text-slate-900">{q.question}</p>
                      <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-medium text-sky-800 ring-1 ring-sky-200 ring-inset">
                        <ReqIcon className="h-3 w-3" />
                        Requires: {req.label}
                      </span>
                    </div>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">{q.rationale}</p>
                  </div>
                </li>
              )
            })}
          </ol>
        </motion.section>
      </motion.div>
    </div>
  )
}

function SectionHeading({ icon: Icon, title }: { icon: LucideIcon; title: string }) {
  return (
    <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
      <Icon className="h-4.5 w-4.5 text-slate-400" />
      {title}
    </h2>
  )
}

/** Id of the last section whose top has scrolled past the upper third of the viewport. */
function useActiveSection(ids: string[]) {
  const [active, setActive] = useState(ids[0])
  const key = ids.join('|')

  useEffect(() => {
    const list = key.split('|')
    function update() {
      const line = window.innerHeight / 3
      let current = list[0]
      for (const id of list) {
        const el = document.getElementById(id)
        if (el && el.getBoundingClientRect().top <= line) current = id
      }
      // At the very bottom the last short sections can never cross the line.
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) {
        current = list[list.length - 1]
      }
      setActive(current)
    }
    update()
    window.addEventListener('scroll', update, { passive: true })
    window.addEventListener('resize', update)
    return () => {
      window.removeEventListener('scroll', update)
      window.removeEventListener('resize', update)
    }
  }, [key])

  return active
}
