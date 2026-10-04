// Contract with the future Python backend.
// Field names are snake_case to match the backend's JSON payloads 1:1 — do not
// rename or reshape without changing the backend schema in lockstep.

export type Recommendation = 'invest' | 'conditional' | 'do_not_invest'

export type ClaimKind = 'source_fact' | 'inference'

export type Stance = 'supports' | 'contradicts'

export type EvidenceKind = 'record' | 'literature' | 'web' | 'conflict'

export type Severity = 'low' | 'medium' | 'high'

export type DiligenceRequirement =
  | 'proprietary_data'
  | 'patient_data'
  | 'kol'
  | 'experiment'
  | 'cmc_ip'

export interface ReportInput {
  indication: string
  mechanism: string
  modality?: string
  stage?: string
  biomarkers?: string[]
  route?: string
}

export interface EvidenceRef {
  evidence_id: string
  stance: Stance
}

export interface Claim {
  id: string
  text: string
  kind: ClaimKind
  /** 0–1 */
  confidence: number
  evidence: EvidenceRef[]
}

export interface Section {
  id: string
  title: string
  claims: Claim[]
}

export interface Evidence {
  id: string
  source: string
  url: string
  title: string
  snippet: string
  kind: EvidenceKind
  /** Pipeline modules that retrieved or used this evidence. */
  modules: string[]
  /** ISO 8601 */
  retrieved_at: string
}

export interface Risk {
  id: string
  title: string
  severity: Severity
  description: string
  claim_ids: string[]
}

export interface DiligenceQuestion {
  question: string
  requires: DiligenceRequirement
}

export interface CapitalEstimate {
  milestone: string
  months_low: number
  months_high: number
  usd_low: number
  usd_base: number
  usd_high: number
  assumptions: string[]
}

export interface Trace {
  agent: string
  step: string
  tool?: string
  input_tokens: number
  output_tokens: number
  latency_s: number
  cost_usd: number
}

export interface Report {
  id: string
  /** ISO 8601 */
  created_at: string
  /** True for hand-written fixtures; URLs look real but content is not verified. */
  is_mock: boolean
  /** Earlier run with the same input, or null if this is the first. Drives the "What changed" tab. */
  previous_run_id: string | null
  input: ReportInput
  recommendation: Recommendation
  /** 0–1 */
  confidence: number
  summary: string
  sections: Section[]
  risks: Risk[]
  unknowns: string[]
  diligence_questions: DiligenceQuestion[]
  capital: CapitalEstimate
  evidence: Evidence[]
  traces: Trace[]
}
