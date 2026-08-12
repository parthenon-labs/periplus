import type { Claim, ItineraryDay, ItineraryItem, Place } from '../api/contracts'
import { formatDay, formatDuration, formatTimeRange } from '../lib/format'
import { AnchorRouteIcon, TransferIcon } from './icons'
import { VerdictDot } from './VerdictBadge'

function itemRollupDot(item: ItineraryItem, claimsById: Map<string, Claim>) {
  const claims = (item.claim_ids ?? []).map((id) => claimsById.get(id)).filter((c): c is Claim => Boolean(c?.check))
  if (claims.length === 0) return null
  const hasContradicted = claims.some((c) => c.check!.verdict === 'contradicted')
  const allSupported = claims.every((c) => c.check!.verdict === 'supported')
  const verdict = hasContradicted ? 'contradicted' : allSupported ? 'supported' : 'partial'
  return <VerdictDot verdict={verdict} title={`${claims.length} claim${claims.length === 1 ? '' : 's'} checked`} />
}

export function DayTimeline({
  days,
  placesById,
  claimsById,
  selectedItemId,
  onSelectItem,
}: {
  days: ItineraryDay[]
  placesById: Map<string, Place>
  claimsById: Map<string, Claim>
  selectedItemId: string | null
  onSelectItem: (item: ItineraryItem) => void
}) {
  if (days.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-line-strong px-4 py-8 text-center text-sm text-ink-soft">
        No itinerary days were produced for this run.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-10">
      {days.map((day) => (
        <section key={day.day}>
          <header className="mb-4 flex items-baseline gap-3">
            <h3 className="font-serif text-xl text-ink">
              Day {day.day} <span className="text-ink-faint">·</span> {formatDay(day.date)}
            </h3>
            {day.theme ? <span className="text-sm text-bronze-deep">{day.theme}</span> : null}
          </header>

          <ol className="flex flex-col gap-1 border-l border-line pl-5">
            {(day.items ?? []).map((item) => {
              const place = item.place_id ? placesById.get(item.place_id) : undefined
              const claimCount = item.claim_ids?.length ?? 0
              const selected = item.id === selectedItemId
              return (
                <li key={item.id ?? item.title} className="relative -ml-[1.4rem] pb-6 pl-[1.4rem]">
                  <span className="absolute left-0 top-1.5 size-2.5 -translate-x-1/2 rounded-full border-2 border-bronze bg-paper" />

                  {item.transfer_in ? (
                    <p className="mb-2 flex items-center gap-1.5 text-xs text-ink-faint">
                      <TransferIcon className="size-3.5" />
                      {item.transfer_in.mode}, {formatDuration(item.transfer_in.minutes)}
                      {item.transfer_in.detail ? ` — ${item.transfer_in.detail}` : ''}
                    </p>
                  ) : null}

                  <button
                    type="button"
                    onClick={() => onSelectItem(item)}
                    aria-pressed={selected}
                    className={`w-full rounded-xl border px-4 py-3.5 text-left transition-colors ${
                      selected ? 'border-bronze-deep bg-bronze-tint/60' : 'border-line bg-paper hover:border-bronze'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex flex-col gap-1">
                        <span className="tabular text-xs font-medium text-bronze-deep">
                          {formatTimeRange(item.start, item.end)}
                        </span>
                        <span className="font-serif text-base text-ink">{item.title}</span>
                        {place ? (
                          <span className="flex items-center gap-1 text-xs text-ink-soft">
                            <AnchorRouteIcon className="size-3.5" />
                            {place.name}
                          </span>
                        ) : null}
                        {item.notes ? <span className="text-xs text-ink-soft">{item.notes}</span> : null}
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1.5">
                        {itemRollupDot(item, claimsById)}
                        {claimCount > 0 ? (
                          <span className="chart-tick tabular whitespace-nowrap">
                            {claimCount} claim{claimCount === 1 ? '' : 's'}
                          </span>
                        ) : null}
                        {item.booking_required ? (
                          <span className="rounded-full bg-paper-sunken px-2 py-0.5 text-[11px] text-ink-soft">
                            Booking required
                          </span>
                        ) : null}
                      </div>
                    </div>
                  </button>
                </li>
              )
            })}
          </ol>
        </section>
      ))}
    </div>
  )
}
