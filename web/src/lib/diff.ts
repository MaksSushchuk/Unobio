// Client-side comparison of two runs of the same input. The backend only
// returns Reports; everything here is derived on the frontend, so these types
// are UI-only and deliberately not part of src/types.ts.

import type { CapitalEstimate, Claim, Evidence, Recommendation, Report, Risk } from '../types'

export interface ChangedItem<T> {
  a: T
  b: T
  /** Fields whose value differs between a and b. */
  fields: (keyof T)[]
}

export interface ListDiff<T> {
  added: T[]
  removed: T[]
  changed: ChangedItem<T>[]
  unchanged: T[]
}

/** A claim with the section it belongs to; claims are matched within a section. */
export interface SectionClaim {
  section_id: string
  section_title: string
  claim: Claim
}

export interface ClaimChange extends ChangedItem<SectionClaim> {
  /** Text similarity used to pair the claims, 0–1 (1 = identical after normalization). */
  similarity: number
  /** Claim fields that differ; `text` is listed when the wording changed at all. */
  claim_fields: (keyof Claim)[]
}

export interface RunComparison {
  /** Earlier run. */
  a: Report
  /** Later run. */
  b: Report
  recommendation: { a: Recommendation; b: Recommendation; changed: boolean }
  confidence: { a: number; b: number; delta: number }
  claims: Omit<ListDiff<SectionClaim>, 'changed'> & { changed: ClaimChange[] }
  evidence: ListDiff<Evidence>
  risks: ListDiff<Risk>
  /** Unknowns are plain strings and compared by normalized text, so they are only added/removed. */
  unknowns: ListDiff<string>
  capital: { a: CapitalEstimate; b: CapitalEstimate; fields: (keyof CapitalEstimate)[] }
}

/**
 * Minimum similarity for two claims in the same section to count as the same
 * claim (reworded) rather than one removed and one added.
 */
export const CLAIM_MATCH_THRESHOLD = 0.35

export function diffReports(prev: Report, curr: Report): RunComparison {
  return {
    a: prev,
    b: curr,
    recommendation: { a: prev.recommendation, b: curr.recommendation, changed: prev.recommendation !== curr.recommendation },
    confidence: { a: prev.confidence, b: curr.confidence, delta: curr.confidence - prev.confidence },
    claims: diffClaims(prev, curr),
    // retrieved_at differs on every run, so it never counts as a change.
    evidence: diffById(prev.evidence, curr.evidence, [
      'source',
      'url',
      'title',
      'snippet',
      'kind',
      'modules',
      'related_evidence_ids',
    ]),
    // claim_ids point at claim ids, which are not stable across runs.
    risks: diffById(prev.risks, curr.risks, ['title', 'severity', 'description']),
    unknowns: diffStrings(prev.unknowns, curr.unknowns),
    capital: { a: prev.capital, b: curr.capital, fields: changedFields(prev.capital, curr.capital, CAPITAL_FIELDS) },
  }
}

const CAPITAL_FIELDS: (keyof CapitalEstimate)[] = [
  'milestone',
  'months_low',
  'months_high',
  'usd_low',
  'usd_base',
  'usd_high',
  'assumptions',
]

// Claim ids are not stable across LLM runs, so claims are paired by section id
// and text similarity. Pairs are assigned greedily, best match first.
function diffClaims(prev: Report, curr: Report): RunComparison['claims'] {
  const result: RunComparison['claims'] = { added: [], removed: [], changed: [], unchanged: [] }
  const flatten = (r: Report) =>
    r.sections.flatMap((s) => s.claims.map((claim) => ({ section_id: s.id, section_title: s.title, claim })))
  const aClaims = flatten(prev)
  const bClaims = flatten(curr)
  const aTokens = aClaims.map((c) => tokens(c.claim.text))
  const bTokens = bClaims.map((c) => tokens(c.claim.text))

  const candidates: { i: number; j: number; sim: number }[] = []
  aClaims.forEach((a, i) => {
    bClaims.forEach((b, j) => {
      if (a.section_id !== b.section_id) return
      const sim = similarity(aTokens[i], bTokens[j])
      if (sim >= CLAIM_MATCH_THRESHOLD) candidates.push({ i, j, sim })
    })
  })
  candidates.sort((x, y) => y.sim - x.sim)

  const matchedA = new Set<number>()
  const matchedB = new Set<number>()
  for (const { i, j, sim } of candidates) {
    if (matchedA.has(i) || matchedB.has(j)) continue
    matchedA.add(i)
    matchedB.add(j)
    const a = aClaims[i]
    const b = bClaims[j]
    const claim_fields: (keyof Claim)[] = []
    if (normalize(a.claim.text) !== normalize(b.claim.text)) claim_fields.push('text')
    claim_fields.push(...changedFields(a.claim, b.claim, ['kind', 'confidence']))
    if (!sameEvidenceRefs(a.claim, b.claim)) claim_fields.push('evidence')
    if (claim_fields.length) {
      const fields: (keyof SectionClaim)[] = a.section_title !== b.section_title ? ['section_title', 'claim'] : ['claim']
      result.changed.push({ a, b, fields, similarity: sim, claim_fields })
    } else {
      result.unchanged.push(b)
    }
  }

  result.removed = aClaims.filter((_, i) => !matchedA.has(i))
  result.added = bClaims.filter((_, j) => !matchedB.has(j))
  return result
}

function sameEvidenceRefs(a: Claim, b: Claim): boolean {
  const key = (c: Claim) =>
    c.evidence
      .map((e) => `${e.evidence_id}:${e.stance}`)
      .sort()
      .join('|')
  return key(a) === key(b)
}

function diffById<T extends { id: string }>(prev: T[], curr: T[], fields: (keyof T)[]): ListDiff<T> {
  const prevById = new Map(prev.map((x) => [x.id, x]))
  const currIds = new Set(curr.map((x) => x.id))
  const result: ListDiff<T> = { added: [], removed: prev.filter((x) => !currIds.has(x.id)), changed: [], unchanged: [] }
  for (const b of curr) {
    const a = prevById.get(b.id)
    if (!a) {
      result.added.push(b)
      continue
    }
    const changed = changedFields(a, b, fields)
    if (changed.length) result.changed.push({ a, b, fields: changed })
    else result.unchanged.push(b)
  }
  return result
}

function diffStrings(prev: string[], curr: string[]): ListDiff<string> {
  const prevKeys = new Set(prev.map(normalize))
  const currKeys = new Set(curr.map(normalize))
  return {
    added: curr.filter((s) => !prevKeys.has(normalize(s))),
    removed: prev.filter((s) => !currKeys.has(normalize(s))),
    changed: [],
    unchanged: curr.filter((s) => prevKeys.has(normalize(s))),
  }
}

function changedFields<T>(a: T, b: T, fields: (keyof T)[]): (keyof T)[] {
  return fields.filter((f) => JSON.stringify(a[f]) !== JSON.stringify(b[f]))
}

// --- text similarity -------------------------------------------------------

const STOPWORDS = new Set(
  'a an and are as at be but by for from has have in is it its of on or that the this to was were which while with would could should'.split(
    ' ',
  ),
)

/** Lowercase, strip diacritics and punctuation, collapse whitespace. */
export function normalize(text: string): string {
  return text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
}

function tokens(text: string): Set<string> {
  return new Set(
    normalize(text)
      .split(' ')
      .filter((t) => t && !STOPWORDS.has(t))
      // Crude plural folding so "inhibitor"/"inhibitors" match.
      .map((t) => (t.length > 3 && t.endsWith('s') ? t.slice(0, -1) : t)),
  )
}

/** Dice coefficient over token sets, 0–1. */
function similarity(a: Set<string>, b: Set<string>): number {
  if (!a.size && !b.size) return 1
  let shared = 0
  for (const t of a) if (b.has(t)) shared++
  return (2 * shared) / (a.size + b.size)
}
