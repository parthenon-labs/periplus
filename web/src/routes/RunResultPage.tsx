import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useRunResult } from '../api/queries'
import { AppShell } from '../components/AppShell'
import { DayTimeline } from '../components/DayTimeline'
import { EvidenceDrawer } from '../components/EvidenceDrawer'
import { PlaceCredibilityPanel } from '../components/PlaceCredibilityPanel'
import type { ItineraryItem } from '../api/contracts'
import { archiveNumber } from '../lib/archive'
import { formatDateRange } from '../lib/format'

export function RunResultPage() {
  const { runId } = useParams<{ runId: string }>()
  const { data: result, isLoading, isError, error } = useRunResult(runId ?? '', Boolean(runId))
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null)

  const itinerary = result?.run?.itinerary
  const brief = result?.run?.brief

  const claimsById = useMemo(() => new Map((itinerary?.claims ?? []).map((c) => [c.id!, c])), [itinerary])
  const evidenceById = useMemo(() => new Map((itinerary?.evidence ?? []).map((e) => [e.id!, e])), [itinerary])
  const placesById = useMemo(() => new Map((itinerary?.places ?? []).map((p) => [p.id!, p])), [itinerary])

  const selectedItem: ItineraryItem | null = useMemo(() => {
    if (!selectedItemId || !itinerary) return null
    for (const day of itinerary.days ?? []) {
      const found = day.items?.find((i) => i.id === selectedItemId)
      if (found) return found
    }
    return null
  }, [selectedItemId, itinerary])

  const selectedClaims = useMemo(
    () => (selectedItem?.claim_ids ?? []).map((id) => claimsById.get(id)).filter((c): c is NonNullable<typeof c> => Boolean(c)),
    [selectedItem, claimsById],
  )
  const selectedPlaceName = selectedItem?.place_id ? placesById.get(selectedItem.place_id)?.name : undefined

  const contentPiece = result?.run?.content?.pieces?.[0]
  const hasIllustratedContent = Boolean(result?.run?.content?.pieces?.length)

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-5 py-10 sm:px-8 sm:py-14">
        {isLoading ? (
          <div className="flex flex-col gap-6">
            <div className="h-9 w-2/3 animate-pulse rounded bg-paper-sunken" />
            <div className="h-64 animate-pulse rounded-2xl bg-paper-sunken" />
          </div>
        ) : isError || !result?.run || !itinerary ? (
          <div className="flex flex-col gap-4">
            <h1 className="font-serif text-2xl text-ink">No itinerary yet</h1>
            <p className="text-sm text-ink-soft">
              {error instanceof Error
                ? error.message
                : 'This run has not produced an itinerary. Check its progress page for status.'}
            </p>
            {runId ? (
              <Link to={`/runs/${runId}`} className="text-sm text-bronze-deep hover:underline">
                ← View run progress
              </Link>
            ) : (
              <Link to="/" className="text-sm text-bronze-deep hover:underline">
                ← Chart a new trip
              </Link>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-10">
            <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <div>
                <h1 className="font-serif text-3xl text-ink sm:text-4xl">{itinerary.destination}</h1>
                {brief ? <p className="mt-1 text-sm text-ink-soft">{formatDateRange(brief.start_date, brief.end_date)}</p> : null}
              </div>
              <div className="flex items-center gap-4">
                {hasIllustratedContent ? (
                  <Link
                    to={`/runs/${runId}/article`}
                    className="rounded-full border border-bronze-deep px-4 py-1.5 text-sm font-medium text-bronze-deep transition hover:bg-bronze-deep hover:text-paper"
                  >
                    Read the illustrated article →
                  </Link>
                ) : null}
                <span className="chart-tick tabular">No. {archiveNumber(result.id)}</span>
              </div>
            </header>

            <div className="grid grid-cols-1 gap-10 lg:grid-cols-[1fr_22rem]">
              <DayTimeline
                days={itinerary.days ?? []}
                placesById={placesById}
                claimsById={claimsById}
                selectedItemId={selectedItemId}
                onSelectItem={(item) => setSelectedItemId(item.id ?? null)}
              />
              <aside className="flex flex-col gap-8">
                <PlaceCredibilityPanel places={itinerary.places ?? []} claimsById={claimsById} caveats={itinerary.caveats ?? []} />
                {contentPiece ? (
                  <div className="rounded-xl border border-line bg-paper px-4 py-4">
                    <h4 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-ink-faint">Field notes</h4>
                    {contentPiece.title ? <p className="font-serif text-base text-ink">{contentPiece.title}</p> : null}
                    <p className="mt-1 text-sm text-ink-soft">{contentPiece.body}</p>
                  </div>
                ) : null}
              </aside>
            </div>
          </div>
        )}
      </div>

      <EvidenceDrawer
        open={Boolean(selectedItemId)}
        onOpenChange={(open) => {
          if (!open) setSelectedItemId(null)
        }}
        item={selectedItem}
        placeName={selectedPlaceName}
        claims={selectedClaims}
        evidenceById={evidenceById}
      />
    </AppShell>
  )
}
