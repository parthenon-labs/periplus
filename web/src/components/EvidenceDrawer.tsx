import * as Dialog from '@radix-ui/react-dialog'
import type { Claim, Evidence, ItineraryItem } from '../api/contracts'
import { formatConfidence, formatPublished, formatTimeRange } from '../lib/format'
import { CloseIcon, ExternalLinkIcon } from './icons'
import { VerdictBadge } from './VerdictBadge'

function EvidenceCard({ evidence, tone }: { evidence: Evidence; tone: 'supporting' | 'conflicting' }) {
  return (
    <li
      className={`rounded-lg border px-3.5 py-3 ${
        tone === 'conflicting' ? 'border-contradicted/30 bg-contradicted-tint/50' : 'border-line bg-paper'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium text-ink">{evidence.title ?? evidence.url}</span>
        <span className="chart-tick shrink-0 uppercase">{evidence.source_kind}</span>
      </div>
      <blockquote className="mt-1.5 border-l-2 border-line-strong pl-3 text-sm italic text-ink-soft">
        “{evidence.snippet}”
      </blockquote>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-faint">
        {evidence.published_at ? <span>Published {formatPublished(evidence.published_at)}</span> : null}
        <a
          href={evidence.url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-bronze-deep hover:underline"
        >
          Open source <ExternalLinkIcon className="size-3" />
        </a>
      </div>
    </li>
  )
}

function ClaimRecord({ claim, evidenceById }: { claim: Claim; evidenceById: Map<string, Evidence> }) {
  const check = claim.check
  const supporting = (check?.supporting_evidence_ids ?? [])
    .map((id) => evidenceById.get(id))
    .filter((e): e is Evidence => Boolean(e))
  const conflicting = (check?.conflicting_evidence_ids ?? [])
    .map((id) => evidenceById.get(id))
    .filter((e): e is Evidence => Boolean(e))

  return (
    <article className="border-b border-line py-5 first:pt-0 last:border-b-0">
      <p className="text-xs uppercase tracking-wide text-ink-faint">{claim.subject}</p>
      <p className="mt-1 font-serif text-base text-ink">{claim.text}</p>

      {check ? (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <VerdictBadge verdict={check.verdict} />
          <span className="chart-tick tabular">confidence {formatConfidence(check.confidence)}</span>
        </div>
      ) : (
        <p className="mt-3 text-sm text-ink-faint">Not yet verified.</p>
      )}

      {check?.reason ? <p className="mt-2 text-sm text-ink-soft">{check.reason}</p> : null}

      {supporting.length > 0 ? (
        <div className="mt-4">
          <h5 className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-faint">Supporting evidence</h5>
          <ul className="flex flex-col gap-2">
            {supporting.map((e) => (
              <EvidenceCard key={e.id} evidence={e} tone="supporting" />
            ))}
          </ul>
        </div>
      ) : null}

      {conflicting.length > 0 ? (
        <div className="mt-4">
          <h5 className="mb-2 text-xs font-medium uppercase tracking-wide text-contradicted">Conflicting evidence</h5>
          <ul className="flex flex-col gap-2">
            {conflicting.map((e) => (
              <EvidenceCard key={e.id} evidence={e} tone="conflicting" />
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  )
}

export function EvidenceDrawer({
  open,
  onOpenChange,
  item,
  placeName,
  claims,
  evidenceById,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  item: ItineraryItem | null
  placeName?: string
  claims: Claim[]
  evidenceById: Map<string, Evidence>
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="evidence-overlay fixed inset-0 z-40" />
        <Dialog.Content
          className="evidence-panel fixed inset-y-0 right-0 z-50 flex w-full flex-col overflow-hidden border-l border-line bg-paper shadow-panel outline-none sm:w-[26rem] max-sm:inset-x-0 max-sm:inset-y-auto max-sm:bottom-0 max-sm:top-auto max-sm:max-h-[85vh] max-sm:rounded-t-2xl max-sm:border-l-0 max-sm:border-t"
          aria-describedby={undefined}
        >
          <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
            <div className="flex flex-col gap-0.5">
              <Dialog.Title className="font-serif text-lg text-ink">{item?.title ?? 'Evidence'}</Dialog.Title>
              <span className="text-sm text-ink-soft">
                {placeName ? `${placeName} · ` : ''}
                {item ? formatTimeRange(item.start, item.end) : ''}
              </span>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Close evidence panel"
                className="flex size-8 shrink-0 items-center justify-center rounded-full text-ink-soft hover:bg-paper-sunken"
              >
                <CloseIcon className="size-4" />
              </button>
            </Dialog.Close>
          </header>

          <div className="flex-1 overflow-y-auto px-5">
            {claims.length === 0 ? (
              <p className="py-8 text-center text-sm text-ink-soft">No claims are attached to this stop yet.</p>
            ) : (
              claims.map((claim) => <ClaimRecord key={claim.id} claim={claim} evidenceById={evidenceById} />)
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
