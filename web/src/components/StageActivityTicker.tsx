// A purely decorative "still working" signal for the two stages that process many
// small items one at a time (Explorer's source documents, Auditor's claim batches).
// It never claims to name a specific source or claim — nothing here is live data, only
// the numeric counter next to it (StageRoute reads that from StageProgress) is. Kept
// separate from that counter on purpose: motion says "not stuck", the number says
// "how far".
const PHRASES: Record<'research' | 'verify', readonly string[]> = {
  research: [
    'Searching the open web…',
    'Reading a source page…',
    'Checking for a publish date…',
    'Binding a quote to its source…',
    'Drafting a candidate claim…',
  ],
  verify: [
    'Re-checking a claim against its evidence…',
    'Comparing the quote to the claim…',
    'Applying the freshness window…',
    'Recording a verdict…',
  ],
}

export function StageActivityTicker({ stage }: { stage: 'research' | 'verify' }) {
  const phrases = PHRASES[stage]
  return (
    <div className="stage-ticker h-[4.5rem] w-full max-w-[16rem]" aria-hidden="true">
      <div className="stage-ticker-track flex flex-col">
        {[...phrases, ...phrases].map((phrase, i) => (
          <span
            key={i}
            className="flex h-6 items-center justify-center overflow-hidden text-ellipsis whitespace-nowrap px-2 text-xs text-ink-faint"
          >
            {phrase}
          </span>
        ))}
      </div>
    </div>
  )
}
