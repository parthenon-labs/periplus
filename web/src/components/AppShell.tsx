import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'
import { formatLogDate } from '../lib/format'
import { CompassIcon } from './icons'

// Matches `succeededRunSummary.id` / `succeededResult.id` in src/test/fixtures.ts — the
// real captured Tokyo run those demo-mode fixtures were built from. Kept as a literal
// (not imported from fixtures.ts) so a non-demo production build never pulls in that
// file's ~1.7MB of embedded fixture data.
const DEMO_SAMPLE_RUN_ID = 'e31c4f3f6b8a40fa806432c9acc6d579'

export function AppShell({ children }: { children: ReactNode }) {
  const isDemo = import.meta.env.MODE === 'demo'

  return (
    <div className="min-h-svh bg-paper">
      <header className="border-b border-line bg-paper-raised/40">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4 sm:px-8">
          <Link to="/" className="flex items-center gap-2.5 text-ink">
            <CompassIcon className="size-6 text-bronze-deep" />
            <span className="font-serif text-xl tracking-tight">Periplus</span>
          </Link>
          <div className="flex items-center gap-4">
            {isDemo ? (
              <>
                <Link
                  to={`/runs/${DEMO_SAMPLE_RUN_ID}/result`}
                  className="text-xs font-medium uppercase tracking-[0.14em] text-bronze-deep hover:underline"
                >
                  Sample journey
                </Link>
                <span
                  title="This is a real, previously captured pipeline run frozen for the static demo — not a live backend."
                  className="rounded-full border border-bronze-deep/30 bg-bronze-pale px-2.5 py-1 text-[0.65rem] font-semibold uppercase tracking-[0.14em] text-bronze-deep"
                >
                  Example run · not live
                </span>
              </>
            ) : (
              <span className="chart-tick hidden tabular sm:inline">Log · {formatLogDate(new Date())}</span>
            )}
          </div>
        </div>
      </header>
      <main>{children}</main>
    </div>
  )
}
