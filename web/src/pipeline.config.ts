// Pipeline definition for the simulated run on the New analysis page.
// The diagram, timeline, counters and log are all derived from PIPELINE, so
// adding/removing/reordering agents here is enough to change the visualization.
// Durations and costs are illustrative — nothing here is a real measurement.

import type { LucideIcon } from 'lucide-react'
import { Dna, Landmark, PenLine, ScanSearch, ShieldAlert, ShieldQuestion, Stethoscope } from 'lucide-react'
import type { ReportInput } from './types'

export interface PipelineSource {
  id: string
  name: string
  /** Raw records returned by the query. */
  records: number
  /** Records kept as evidence items. */
  evidence: number
}

export interface PipelineAgent {
  id: string
  name: string
  role: string
  icon: LucideIcon
  duration_ms: number
  cost_usd: number
  /** Log line written when the agent starts. */
  log: (input: ReportInput) => string
}

export interface PipelineFollowUp {
  /** Stage whose agents re-run. Must come earlier in PIPELINE. */
  target_stage: string
  label: string
  duration_ms: number
  cost_usd: number
  log: string
  /** Extra records fetched from the target stage's sources. */
  sources: { source_id: string; records: number; evidence: number }[]
}

export interface PipelineStage {
  id: string
  label: string
  /** More than one agent = the agents run in parallel. */
  agents: PipelineAgent[]
  /** Sources queried by this stage, revealed one by one while it runs. */
  sources?: PipelineSource[]
  /** Halfway through this stage, loop back and re-run an earlier stage. */
  follow_up?: PipelineFollowUp
}

export const PIPELINE: PipelineStage[] = [
  {
    id: 'research',
    label: 'Research',
    agents: [
      {
        id: 'researcher',
        name: 'Researcher',
        role: 'Evidence retrieval',
        icon: ScanSearch,
        duration_ms: 4500,
        cost_usd: 0.041,
        log: (i) => `Resolving subject "${i.mechanism}" in "${i.indication}"; querying sources`,
      },
    ],
    sources: [
      { id: 'ctgov', name: 'ClinicalTrials.gov', records: 38, evidence: 9 },
      { id: 'opentargets', name: 'Open Targets', records: 214, evidence: 7 },
      { id: 'pubmed', name: 'PubMed', records: 412, evidence: 16 },
      { id: 'openfda', name: 'openFDA', records: 57, evidence: 4 },
    ],
  },
  {
    id: 'analysis',
    label: 'Analysts',
    agents: [
      {
        id: 'analyst_biology',
        name: 'Biology',
        role: 'Target & genetics',
        icon: Dna,
        duration_ms: 3900,
        cost_usd: 0.094,
        log: () => 'Analyzing target biology and human genetics',
      },
      {
        id: 'analyst_clinical',
        name: 'Clinical',
        role: 'Trial precedent',
        icon: Stethoscope,
        duration_ms: 4700,
        cost_usd: 0.112,
        log: () => 'Analyzing clinical precedent and trial outcomes',
      },
      {
        id: 'analyst_safety',
        name: 'Safety',
        role: 'Class & label risks',
        icon: ShieldAlert,
        duration_ms: 4200,
        cost_usd: 0.079,
        log: () => 'Reviewing safety signals and label warnings',
      },
      {
        id: 'analyst_commercial',
        name: 'Commercial',
        role: 'Market & capital',
        icon: Landmark,
        duration_ms: 5000,
        cost_usd: 0.087,
        log: () => 'Assessing competitive landscape and capital to milestone',
      },
    ],
  },
  {
    id: 'review',
    label: 'Review',
    agents: [
      {
        id: 'skeptic',
        name: 'Skeptic',
        role: 'Challenges claims',
        icon: ShieldQuestion,
        duration_ms: 2600,
        cost_usd: 0.071,
        log: () => 'Cross-checking claims against contradicting evidence',
      },
    ],
    follow_up: {
      target_stage: 'research',
      label: 'follow-up search',
      duration_ms: 2600,
      cost_usd: 0.019,
      log: 'Follow-up: PubMed + openFDA for class safety warnings',
      sources: [
        { source_id: 'pubmed', records: 23, evidence: 3 },
        { source_id: 'openfda', records: 6, evidence: 2 },
      ],
    },
  },
  {
    id: 'write',
    label: 'Report',
    agents: [
      {
        id: 'writer',
        name: 'Writer',
        role: 'Underwriting report',
        icon: PenLine,
        duration_ms: 3000,
        cost_usd: 0.136,
        log: () => 'Writing underwriting report',
      },
    ],
  },
]

/** How long the "Report ready" state shows before navigating. */
export const READY_HOLD_MS = 1600

/** The simulated run always lands on this fixture report. */
export const RESULT_RUN_ID = 'run_b'
