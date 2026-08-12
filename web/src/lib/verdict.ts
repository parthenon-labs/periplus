import type { Verdict } from '../api/contracts'

export const VERDICT_LABEL: Record<Verdict, string> = {
  supported: 'Supported',
  partial: 'Partially supported',
  contradicted: 'Contradicted',
  unsupported: 'Unsupported',
  no_evidence: 'No evidence found',
  stale: 'Stale',
}

export type VerdictTone = 'supported' | 'contradicted' | 'neutral'

/**
 * Colour discipline: green is reserved for "supported" and vermilion for
 * "contradicted" / failure. Every other verdict renders in the neutral
 * ink/bronze palette so the two signal colours stay meaningful.
 */
export function verdictTone(verdict: Verdict): VerdictTone {
  if (verdict === 'supported') return 'supported'
  if (verdict === 'contradicted') return 'contradicted'
  return 'neutral'
}
