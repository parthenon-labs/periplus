import type { Claim, Place, Verdict } from '../api/contracts'
import { toDMS } from '../lib/coordinate'
import { formatDuration } from '../lib/format'
import { VERDICT_LABEL } from '../lib/verdict'
import { VerdictDot } from './VerdictBadge'

function tallyVerdicts(place: Place, claimsById: Map<string, Claim>) {
  const counts = new Map<Verdict, number>()
  for (const id of place.claim_ids ?? []) {
    const claim = claimsById.get(id)
    const verdict = claim?.check?.verdict
    if (!verdict) continue
    counts.set(verdict, (counts.get(verdict) ?? 0) + 1)
  }
  return counts
}

export function PlaceCredibilityPanel({
  places,
  claimsById,
  caveats,
}: {
  places: Place[]
  claimsById: Map<string, Claim>
  caveats: string[]
}) {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h3 className="mb-3 font-serif text-lg text-ink">Places &amp; evidence</h3>
        {places.length === 0 ? (
          <p className="text-sm text-ink-soft">No places were recorded for this run.</p>
        ) : (
          <ul className="flex flex-col gap-4">
            {places.map((place) => {
              const tally = tallyVerdicts(place, claimsById)
              return (
                <li key={place.id ?? place.name} className="rounded-xl border border-line bg-paper px-4 py-3.5">
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-serif text-base text-ink">{place.name}</span>
                    <span className="chart-tick shrink-0 uppercase">{place.kind}</span>
                  </div>
                  {place.summary ? <p className="mt-1 text-sm text-ink-soft">{place.summary}</p> : null}
                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-faint">
                    {place.point ? <span className="chart-tick">{toDMS(place.point.lat, place.point.lon)}</span> : null}
                    {place.typical_duration_minutes ? <span>{formatDuration(place.typical_duration_minutes)} visit</span> : null}
                  </div>
                  {tally.size > 0 ? (
                    <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
                      {[...tally.entries()].map(([verdict, count]) => (
                        <li key={verdict} className="flex items-center gap-1.5 text-xs text-ink-soft">
                          <VerdictDot verdict={verdict} />
                          {count} {VERDICT_LABEL[verdict]}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {caveats.length > 0 ? (
        <div className="rounded-xl border border-contradicted/30 bg-contradicted-tint/60 px-4 py-3.5">
          <h4 className="mb-2 text-sm font-medium text-contradicted">Caveats to re-check</h4>
          <ul className="flex flex-col gap-1.5 text-sm text-ink">
            {caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
