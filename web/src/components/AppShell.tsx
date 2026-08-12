import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'
import { formatLogDate } from '../lib/format'
import { CompassIcon } from './icons'

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-svh bg-paper">
      <header className="border-b border-line bg-paper-raised/40">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4 sm:px-8">
          <Link to="/" className="flex items-center gap-2.5 text-ink">
            <CompassIcon className="size-6 text-bronze-deep" />
            <span className="font-serif text-xl tracking-tight">Periplus</span>
          </Link>
          <span className="chart-tick hidden tabular sm:inline">Log · {formatLogDate(new Date())}</span>
        </div>
      </header>
      <main>{children}</main>
    </div>
  )
}
