import { useMemo, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useRunResult } from '../api/queries'
import { AppShell } from '../components/AppShell'
import type { ContentPiece, Illustration } from '../api/contracts'
import { archiveNumber } from '../lib/archive'
import { formatDateRange } from '../lib/format'

// Named for the ContentPiece.kind values Chronicler writes — see periplus/models.py.
// "article" leads the page; everything else follows in this fixed, deliberate order
// rather than whatever order the pipeline happened to produce them in.
const PIECE_ORDER = ['article', 'itinerary_doc', 'thread', 'captions', 'checklist']
const PIECE_LABEL: Record<string, string> = {
  article: 'Article',
  itinerary_doc: 'Itinerary log',
  thread: 'Thread',
  captions: 'Captions',
  checklist: 'Checklist',
}

function sortPieces(pieces: ContentPiece[]): ContentPiece[] {
  return [...pieces].sort((a, b) => {
    const ai = PIECE_ORDER.indexOf(a.kind)
    const bi = PIECE_ORDER.indexOf(b.kind)
    return (ai === -1 ? PIECE_ORDER.length : ai) - (bi === -1 ? PIECE_ORDER.length : bi)
  })
}

function imageSrc(image: Illustration): string {
  return `data:${image.mime_type ?? 'image/png'};base64,${image.data_base64}`
}

function DemoIllustration({ subject, className }: { subject: string; className: string }) {
  return (
    <div
      role="img"
      aria-label={subject}
      className={`${className} flex items-end bg-[radial-gradient(circle_at_72%_20%,rgba(178,140,79,0.38),transparent_27%),linear-gradient(145deg,#24312c_0%,#526a5e_42%,#d8c39d_100%)] p-6`}
    >
      <span className="font-serif text-2xl text-paper drop-shadow-sm">{subject}</span>
    </div>
  )
}

// A single image sits full-width. Two or more would otherwise wrap onto a second
// row in a grid — instead they scroll horizontally as a swipeable strip, so the
// reader drags/swipes sideways through them rather than the layout stacking rows.
function ImageStrip({ images, aspect = 'aspect-[16/9]' }: { images: Illustration[]; aspect?: string }) {
  if (images.length === 1) {
    const image = images[0]
    return (
      <figure className="overflow-hidden rounded-xl border border-line bg-paper-sunken">
        {import.meta.env.MODE === 'demo' ? (
          <DemoIllustration subject={image.subject} className={`${aspect} w-full`} />
        ) : (
          <img src={imageSrc(image)} alt={image.subject} className={`${aspect} w-full object-cover`} />
        )}
        <figcaption className="px-3 py-2 text-xs text-ink-faint">{image.subject}</figcaption>
      </figure>
    )
  }

  return (
    <div className="flex snap-x snap-mandatory gap-3 overflow-x-auto scroll-smooth pb-1">
      {images.map((image) => (
        <figure
          key={image.id}
          className="w-[78%] shrink-0 snap-start overflow-hidden rounded-xl border border-line bg-paper-sunken sm:w-[46%] md:w-[32%]"
        >
          {import.meta.env.MODE === 'demo' ? (
            <DemoIllustration subject={image.subject} className={`${aspect} w-full`} />
          ) : (
            <img src={imageSrc(image)} alt={image.subject} className={`${aspect} w-full object-cover`} />
          )}
          <figcaption className="px-3 py-2 text-xs text-ink-faint">{image.subject}</figcaption>
        </figure>
      ))}
    </div>
  )
}

// Splits a piece's body into paragraphs and threads its images between them, spread
// evenly by position, so a piece with images reads as text-image-text rather than a
// picture block dumped above an unbroken wall of copy.
function interleavePieceContent(body: string, images: Illustration[]) {
  const paragraphs = body
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean)
  if (paragraphs.length === 0) paragraphs.push(body)

  const blocks: ReactNode[] = []

  if (images.length === 0) {
    paragraphs.forEach((para, i) => {
      blocks.push(
        <p key={`p-${i}`} className="whitespace-pre-line">
          {para}
        </p>,
      )
    })
    return blocks
  }

  // Slot i lands after paragraph position[i] (1-indexed), spread evenly across the
  // available gaps and never before the opening paragraph.
  const positions = images.map((_, i) =>
    Math.min(paragraphs.length, Math.max(1, Math.round(((i + 1) * paragraphs.length) / (images.length + 1)))),
  )

  let imgIdx = 0
  paragraphs.forEach((para, pIdx) => {
    blocks.push(
      <p key={`p-${pIdx}`} className="whitespace-pre-line">
        {para}
      </p>,
    )
    const group: Illustration[] = []
    while (imgIdx < images.length && positions[imgIdx] === pIdx + 1) {
      group.push(images[imgIdx])
      imgIdx++
    }
    if (group.length > 0) blocks.push(<ImageStrip key={`img-${pIdx}`} images={group} />)
  })

  return blocks
}

export function ArticlePage() {
  const { runId } = useParams<{ runId: string }>()
  const { data: result, isLoading, isError, error } = useRunResult(runId ?? '', Boolean(runId))

  const run = result?.run
  const brief = run?.brief
  const destination = run?.itinerary?.destination
  const pieces = useMemo(() => sortPieces(run?.content?.pieces ?? []), [run?.content?.pieces])
  const images = useMemo(() => run?.illustrated?.images ?? [], [run?.illustrated?.images])
  const caveats = run?.illustrated?.caveats ?? []

  // One representative image per claim it illustrates, so a piece that cites the claim
  // can show the picture that goes with it — the same binding Illustrator itself made,
  // just read back out rather than re-derived.
  const imageByClaimId = useMemo(() => {
    const map = new Map<string, Illustration>()
    for (const image of images) {
      for (const claimId of image.claim_ids ?? []) {
        if (!map.has(claimId)) map.set(claimId, image)
      }
    }
    return map
  }, [images])

  const usedImageIds = new Set<string>()

  return (
    <AppShell>
      <div className="mx-auto max-w-3xl px-5 py-10 sm:px-8 sm:py-14">
        {isLoading ? (
          <div className="flex flex-col gap-6">
            <div className="h-9 w-2/3 animate-pulse rounded bg-paper-sunken" />
            <div className="h-64 animate-pulse rounded-2xl bg-paper-sunken" />
          </div>
        ) : isError || !run || pieces.length === 0 ? (
          <div className="flex flex-col gap-4">
            <h1 className="font-serif text-2xl text-ink">No illustrated content yet</h1>
            <p className="text-sm text-ink-soft">
              {error instanceof Error
                ? error.message
                : 'This run has not produced any Chronicler content — check its progress page for status.'}
            </p>
            {runId ? (
              <Link to={`/runs/${runId}/result`} className="text-sm text-bronze-deep hover:underline">
                ← Back to the itinerary
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
                {runId ? (
                  <Link to={`/runs/${runId}/result`} className="text-xs text-bronze-deep hover:underline">
                    ← Itinerary
                  </Link>
                ) : null}
                <h1 className="mt-1 font-serif text-3xl text-ink sm:text-4xl">
                  {destination ?? 'Illustrated log'}
                </h1>
                {brief ? <p className="mt-1 text-sm text-ink-soft">{formatDateRange(brief.start_date, brief.end_date)}</p> : null}
              </div>
              <span className="chart-tick tabular">No. {archiveNumber(result?.id ?? runId ?? '')}</span>
            </header>

            <div className="flex flex-col gap-12">
              {pieces.map((piece, index) => {
                // A piece can cite several claim ids that resolve to the same subject's
                // one representative image (Illustrator dedupes per subject, not per
                // claim) — dedupe here too, or that image renders twice back-to-back.
                const seenImageIds = new Set<string>()
                const piecesImages = (piece.claim_ids ?? [])
                  .map((id) => imageByClaimId.get(id))
                  .filter((image): image is Illustration => {
                    if (!image || usedImageIds.has(image.id!) || seenImageIds.has(image.id!)) return false
                    seenImageIds.add(image.id!)
                    return true
                  })
                for (const image of piecesImages) usedImageIds.add(image.id!)

                return (
                  <article key={index} className="flex flex-col gap-4">
                    <div className="flex items-baseline gap-2">
                      <h2 className="chart-tick uppercase text-bronze-deep">{PIECE_LABEL[piece.kind] ?? piece.kind}</h2>
                      {piece.word_count ? <span className="chart-tick text-ink-faint">{piece.word_count} words</span> : null}
                    </div>
                    {piece.title ? <h3 className="font-serif text-2xl text-ink">{piece.title}</h3> : null}

                    <div className="flex flex-col gap-5 font-serif text-base leading-relaxed text-ink">
                      {interleavePieceContent(piece.body, piecesImages)}
                    </div>
                  </article>
                )
              })}

              {images.some((image) => !usedImageIds.has(image.id!)) ? (
                <div className="flex flex-col gap-3 border-t border-line pt-8">
                  <h2 className="chart-tick uppercase text-bronze-deep">More from Illustrator</h2>
                  <ImageStrip
                    images={images.filter((image) => !usedImageIds.has(image.id!))}
                    aspect="aspect-square"
                  />
                </div>
              ) : null}

              {caveats.length > 0 ? (
                <div className="rounded-xl border border-contradicted/30 bg-contradicted-tint/60 px-4 py-3.5">
                  <h4 className="mb-2 text-sm font-medium text-contradicted">Illustrator caveats</h4>
                  <ul className="flex flex-col gap-1.5 text-sm text-ink">
                    {caveats.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  )
}
