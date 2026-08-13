import type { Stage } from '../api/contracts'

export const STAGE_ORDER: readonly Stage[] = ['research', 'verify', 'plan', 'write', 'illustrate']

export const STAGE_LABEL: Record<Stage, string> = {
  research: 'Research',
  verify: 'Verify',
  plan: 'Plan',
  write: 'Write',
  illustrate: 'Illustrate',
}

// Named for the pipeline roles documented in the API contract (Explorer,
// Auditor, Navigator, Chronicler, Illustrator) — descriptive, not invented statistics.
export const STAGE_DESCRIPTION: Record<Stage, string> = {
  research: 'The Explorer is gathering sources and drafting claims for the destination.',
  verify: 'The Auditor is checking every claim against its cited evidence.',
  plan: 'The Navigator is assembling the day-by-day route from verified claims.',
  write: 'The Chronicler is drafting the traveller-facing log.',
  illustrate: 'The Illustrator is generating grounded images for the illustrated article.',
}
