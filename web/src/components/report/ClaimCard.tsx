import { AnimatePresence, motion } from 'framer-motion'
import { ChevronDown, ExternalLink, FileText, Sparkles, ThumbsDown, ThumbsUp, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import type { Claim, Evidence, Stance } from '../../types'
import ConfidenceBar from './ConfidenceBar'

export default function ClaimCard(props: {
  claim: Claim
  /** DOM id, so risks can scroll to this card. */
  domId: string
  evidenceById: Map<string, Evidence>
  /** Non-zero to play the "linked from a risk" highlight; a new value replays it. */
  highlight: number
}) {
  const { claim, domId, evidenceById, highlight } = props
  const [open, setOpen] = useState(false)
  const supports = claim.evidence.filter((e) => e.stance === 'supports').length
  const contradicts = claim.evidence.length - supports
  const inference = claim.kind === 'inference'

  return (
    <li
      id={domId}
      className={`relative scroll-mt-24 rounded-lg border bg-white shadow-xs ${
        inference ? 'border-dashed border-violet-300' : 'border-slate-200'
      }`}
    >
      {highlight > 0 && (
        <motion.span
          key={highlight}
          aria-hidden
          className="pointer-events-none absolute -inset-px rounded-lg bg-sky-100/60 ring-2 ring-sky-400"
          initial={{ opacity: 1 }}
          animate={{ opacity: 0 }}
          transition={{ duration: 1.6, delay: 0.5, ease: 'easeOut' }}
        />
      )}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        disabled={claim.evidence.length === 0}
        className="relative block w-full rounded-lg px-4 py-3.5 text-left focus-visible:ring-2 focus-visible:ring-slate-300 focus-visible:outline-none enabled:hover:bg-slate-50/60"
      >
        <div className="flex items-start gap-3">
          <p className="min-w-0 flex-1 text-sm leading-relaxed text-slate-800">{claim.text}</p>
          <KindBadge inference={inference} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
          <div className="w-40">
            <ConfidenceBar value={claim.confidence} tone={inference ? 'bg-violet-500' : 'bg-slate-700'} />
          </div>
          <span className="flex items-center gap-3 text-xs text-slate-500">
            <span className="flex items-center gap-1" title="Supporting evidence">
              <ThumbsUp className="h-3.5 w-3.5 text-emerald-600" />
              <span className="tabular-nums">{supports}</span> supporting
            </span>
            <span
              className={`flex items-center gap-1 ${contradicts ? 'text-rose-700' : ''}`}
              title="Contradicting evidence"
            >
              <ThumbsDown className={`h-3.5 w-3.5 ${contradicts ? 'text-rose-600' : 'text-slate-400'}`} />
              <span className="tabular-nums">{contradicts}</span> contradicting
            </span>
          </span>
          {claim.evidence.length > 0 && (
            <span className="ml-auto flex items-center gap-1 text-xs font-medium text-slate-500">
              {open ? 'Hide' : 'Show'} evidence
              <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.2 }} className="flex">
                <ChevronDown className="h-3.5 w-3.5" />
              </motion.span>
            </span>
          )}
        </div>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: 'easeOut' }}
            className="overflow-hidden"
          >
            <ul className="space-y-2 border-t border-slate-100 px-4 pt-3 pb-4">
              {claim.evidence.map((ref) => {
                const ev = evidenceById.get(ref.evidence_id)
                return ev ? (
                  <EvidenceItem key={ref.evidence_id} evidence={ev} stance={ref.stance} />
                ) : (
                  <li key={ref.evidence_id} className="font-mono text-xs text-slate-400">
                    Missing evidence: {ref.evidence_id}
                  </li>
                )
              })}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  )
}

function KindBadge({ inference }: { inference: boolean }) {
  return inference ? (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-violet-50 px-1.5 py-0.5 text-[11px] font-medium text-violet-700 ring-1 ring-violet-200 ring-inset">
      <Sparkles className="h-3 w-3" />
      AI inference
    </span>
  ) : (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700 ring-1 ring-slate-200 ring-inset">
      <FileText className="h-3 w-3" />
      Source fact
    </span>
  )
}

function EvidenceItem({ evidence, stance }: { evidence: Evidence; stance: Stance }) {
  const supports = stance === 'supports'
  return (
    <li
      className={`rounded-md border-l-[3px] py-2 pr-3 pl-3 ${
        supports ? 'border-emerald-500 bg-emerald-50/40' : 'border-rose-500 bg-rose-50/50'
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
        <span className={`font-semibold tracking-wide uppercase ${supports ? 'text-emerald-700' : 'text-rose-700'}`}>
          {supports ? 'Supports' : 'Contradicts'}
        </span>
        <span className="text-slate-300">·</span>
        <span className="font-medium text-slate-600">{evidence.source}</span>
        {evidence.kind === 'conflict' && (
          <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-px font-medium text-amber-800">
            <TriangleAlert className="h-3 w-3" />
            Conflicting sources
          </span>
        )}
      </div>
      <a
        href={evidence.url}
        target="_blank"
        rel="noopener noreferrer"
        className="group mt-1 inline-flex items-start gap-1 text-sm font-medium text-slate-900 hover:underline"
      >
        {evidence.title}
        <ExternalLink className="mt-1 h-3 w-3 shrink-0 text-slate-400 group-hover:text-slate-700" />
      </a>
      <p className="mt-1 text-xs leading-relaxed text-slate-600">{evidence.snippet}</p>
    </li>
  )
}
