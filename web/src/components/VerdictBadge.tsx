import type { Verdict } from '../api/contracts'
import { VERDICT_LABEL, verdictTone } from '../lib/verdict'
import { AlertIcon, CheckIcon, ClockIcon } from './icons'

const TONE_CLASSES: Record<string, string> = {
  supported: 'text-supported bg-supported-tint',
  contradicted: 'text-contradicted bg-contradicted-tint',
  neutral: 'text-ink-soft bg-paper-sunken',
}

export function VerdictBadge({ verdict, className = '' }: { verdict: Verdict; className?: string }) {
  const tone = verdictTone(verdict)
  const Icon = tone === 'supported' ? CheckIcon : tone === 'contradicted' ? AlertIcon : ClockIcon
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${TONE_CLASSES[tone]} ${className}`}
    >
      <Icon className="size-3.5 shrink-0" />
      {VERDICT_LABEL[verdict]}
    </span>
  )
}

/** A minimal coloured dot for dense contexts (timeline rows, place chips). */
export function VerdictDot({ verdict, title }: { verdict: Verdict; title?: string }) {
  const tone = verdictTone(verdict)
  const color =
    tone === 'supported' ? 'bg-supported' : tone === 'contradicted' ? 'bg-contradicted' : 'bg-ink-faint'
  return <span title={title ?? VERDICT_LABEL[verdict]} className={`inline-block size-2 rounded-full ${color}`} />
}
