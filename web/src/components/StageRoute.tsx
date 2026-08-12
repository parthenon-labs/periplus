import type { RunCounts, RunStatus, Stage, StageProgress } from '../api/contracts'
import { STAGE_DESCRIPTION, STAGE_LABEL, STAGE_ORDER } from '../lib/stage'
import { AlertIcon, CheckIcon } from './icons'

function markerState(status: RunStatus | null | undefined): 'done' | 'active' | 'failed' | 'pending' {
  if (status === 'succeeded') return 'done'
  if (status === 'running') return 'active'
  if (status === 'failed' || status === 'cancelled') return 'failed'
  return 'pending'
}

function Marker({ state }: { state: ReturnType<typeof markerState> }) {
  if (state === 'done') {
    return (
      <span className="flex size-7 shrink-0 items-center justify-center rounded-full border border-bronze-deep bg-bronze-deep text-paper">
        <CheckIcon className="size-4" />
      </span>
    )
  }
  if (state === 'failed') {
    return (
      <span className="flex size-7 shrink-0 items-center justify-center rounded-full border border-contradicted bg-contradicted-tint text-contradicted">
        <AlertIcon className="size-4" />
      </span>
    )
  }
  if (state === 'active') {
    return (
      <span className="relative flex size-7 shrink-0 items-center justify-center">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-bronze-soft opacity-60" />
        <span className="relative size-3.5 rounded-full bg-bronze-deep" />
      </span>
    )
  }
  return <span className="flex size-7 shrink-0 items-center justify-center rounded-full border border-line-strong bg-paper" />
}

export function StageRoute({
  stages,
  currentStage,
  counts,
}: {
  stages: StageProgress[]
  currentStage: Stage | null | undefined
  counts: RunCounts
}) {
  const byStage = new Map(stages.map((s) => [s.stage, s]))

  return (
    <div className="flex flex-col gap-8">
      <ol className="flex flex-col gap-6 sm:flex-row sm:items-start sm:gap-0">
        {STAGE_ORDER.map((stage, i) => {
          const progress = byStage.get(stage)
          const state = markerState(progress?.status)
          const isCurrent = stage === currentStage
          return (
            <li key={stage} className="relative flex flex-1 gap-3 sm:flex-col sm:items-center sm:gap-3 sm:text-center">
              {i < STAGE_ORDER.length - 1 ? (
                <span
                  aria-hidden
                  className="absolute left-3.5 top-9 h-[calc(100%+0.5rem)] w-px border-l border-dashed border-line-strong sm:left-1/2 sm:top-3.5 sm:h-px sm:w-full sm:border-l-0 sm:border-t"
                />
              ) : null}
              <Marker state={state} />
              <div className="flex flex-col gap-0.5 pb-2 sm:pb-0">
                <span className={`text-sm font-medium ${isCurrent ? 'text-bronze-deep' : 'text-ink'}`}>
                  {STAGE_LABEL[stage]}
                </span>
                {isCurrent && state === 'active' ? (
                  <span className="max-w-[16rem] text-xs text-ink-soft sm:mx-auto">{STAGE_DESCRIPTION[stage]}</span>
                ) : null}
                {progress?.error ? <span className="text-xs text-contradicted">{progress.error}</span> : null}
              </div>
            </li>
          )
        })}
      </ol>

      <dl className="chart-tick grid grid-cols-2 gap-x-6 gap-y-2 border-t border-line pt-4 sm:grid-cols-5">
        <CountStat label="Evidence" value={counts.evidence} />
        <CountStat label="Claims" value={counts.claims} />
        <CountStat label="Verified" value={counts.verified_claims} />
        <CountStat label="Itinerary items" value={counts.itinerary_items} />
        <CountStat label="Content pieces" value={counts.content_pieces} />
      </dl>
    </div>
  )
}

function CountStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="uppercase text-ink-faint">{label}</dt>
      <dd className="tabular font-sans text-sm font-medium text-ink">{value}</dd>
    </div>
  )
}
