import { useEffect } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useRunSummary } from '../api/queries'
import { AppShell } from '../components/AppShell'
import { StageRoute } from '../components/StageRoute'
import { archiveNumber } from '../lib/archive'
import { formatDateRange } from '../lib/format'

export function RunProgressPage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  const { data: summary, isLoading, isError, error } = useRunSummary(runId ?? '')

  useEffect(() => {
    if (summary?.status === 'succeeded') {
      navigate(`/runs/${summary.id}/result`, { replace: true })
    }
  }, [summary?.status, summary?.id, navigate])

  return (
    <AppShell>
      <div className="mx-auto max-w-3xl px-5 py-10 sm:px-8 sm:py-16">
        {isLoading ? (
          <div className="flex flex-col gap-6">
            <div className="h-9 w-2/3 animate-pulse rounded bg-paper-sunken" />
            <div className="h-24 animate-pulse rounded-2xl bg-paper-sunken" />
          </div>
        ) : isError || !summary ? (
          <div className="flex flex-col gap-4">
            <h1 className="font-serif text-2xl text-ink">Run not found</h1>
            <p className="text-sm text-ink-soft">
              {error instanceof Error ? error.message : 'This run id does not match anything on record.'}
            </p>
            <Link to="/" className="text-sm text-bronze-deep hover:underline">
              ← Chart a new trip
            </Link>
          </div>
        ) : (
          <div className="flex flex-col gap-8">
            <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <div>
                <h1 className="font-serif text-3xl text-ink">{summary.brief.destination}</h1>
                <p className="mt-1 text-sm text-ink-soft">{formatDateRange(summary.brief.start_date, summary.brief.end_date)}</p>
              </div>
              <span className="chart-tick tabular">No. {archiveNumber(summary.id)}</span>
            </header>

            <div className="rounded-2xl border border-line px-6 py-8 sm:px-8">
              <StageRoute stages={summary.stages} currentStage={summary.current_stage} counts={summary.counts} />
            </div>

            {summary.status === 'failed' || summary.status === 'cancelled' ? (
              <div className="rounded-xl border border-contradicted/30 bg-contradicted-tint px-5 py-4">
                <p className="text-sm font-medium text-contradicted">
                  {summary.status === 'failed' ? 'The run failed.' : 'The run was cancelled.'}
                </p>
                {summary.error ? <p className="mt-1 text-sm text-ink">{summary.error}</p> : null}
                <Link to="/" className="mt-3 inline-block text-sm text-bronze-deep hover:underline">
                  ← Chart a new trip
                </Link>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </AppShell>
  )
}
