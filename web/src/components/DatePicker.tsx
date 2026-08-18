import * as Popover from '@radix-ui/react-popover'
import { gsap } from 'gsap'
import { useEffect, useRef, useState } from 'react'
import { CalendarIcon, ChevronLeftIcon, ChevronRightIcon } from './icons'

// Native `<input type="date">` renders a browser-chrome calendar icon and a
// locale-dependent numeral entry field that breaks the ivory-paper/serif
// world on every platform differently. This is a small in-world replacement:
// a log-style trigger button plus a bounded month grid, built on Radix
// Popover for focus containment, outside-click, and Escape handling.

const DISPLAY_FMT = new Intl.DateTimeFormat('en', { day: 'numeric', month: 'short', year: 'numeric' })
const MONTH_FMT = new Intl.DateTimeFormat('en', { month: 'long', year: 'numeric' })
const WEEKDAY_LABELS = ['M', 'T', 'W', 'T', 'F', 'S', 'S']
const prefersReducedMotion =
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

function parseISO(iso: string): Date | null {
  if (!iso) return null
  const d = new Date(`${iso}T00:00:00`)
  return Number.isNaN(d.getTime()) ? null : d
}

function toISO(date: Date): string {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function sameDay(a: Date | null, b: Date | null): boolean {
  return !!a && !!b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

function buildMonthGrid(monthDate: Date): (Date | null)[] {
  const year = monthDate.getFullYear()
  const month = monthDate.getMonth()
  const firstWeekday = (new Date(year, month, 1).getDay() + 6) % 7 // Monday-first
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const cells: (Date | null)[] = Array.from({ length: firstWeekday }, () => null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(new Date(year, month, d))
  return cells
}

export function DatePicker({
  id,
  label,
  value,
  onChange,
  minIso,
  placeholder = 'Select date',
  className,
  disabled = false,
}: {
  id?: string
  label: string
  value: string
  onChange: (iso: string) => void
  minIso?: string
  placeholder?: string
  className?: string
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const selected = parseISO(value)
  const min = minIso ? parseISO(minIso) : null
  const [viewMonth, setViewMonth] = useState(() => startOfDay(selected ?? min ?? new Date()))
  const [focusIso, setFocusIso] = useState<string | null>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const cellRefs = useRef(new Map<string, HTMLButtonElement>())
  const today = startOfDay(new Date())

  useEffect(() => {
    if (!focusIso) return
    cellRefs.current.get(focusIso)?.focus()
  }, [focusIso, viewMonth])

  useEffect(() => {
    if (!open || !contentRef.current) return
    if (prefersReducedMotion) return
    gsap.killTweensOf(contentRef.current)
    gsap.fromTo(
      contentRef.current,
      { opacity: 0, y: -6, scale: 0.97 },
      { opacity: 1, y: 0, scale: 1, duration: 0.18, ease: 'power2.out' },
    )
  }, [open])

  const handleOpenChange = (next: boolean) => {
    if (next && disabled) return
    if (next) {
      setViewMonth(startOfDay(selected ?? min ?? new Date()))
      setFocusIso(toISO(selected ?? min ?? new Date()))
    }
    setOpen(next)
  }

  const select = (date: Date) => {
    onChange(toISO(date))
    setOpen(false)
  }

  const shiftMonth = (delta: number) => setViewMonth((m) => new Date(m.getFullYear(), m.getMonth() + delta, 1))

  const moveFocus = (from: Date, deltaDays: number) => {
    const next = new Date(from)
    next.setDate(next.getDate() + deltaDays)
    if (min && next < min) return
    if (next.getMonth() !== viewMonth.getMonth() || next.getFullYear() !== viewMonth.getFullYear()) {
      setViewMonth(new Date(next.getFullYear(), next.getMonth(), 1))
    }
    setFocusIso(toISO(next))
  }

  const cells = buildMonthGrid(viewMonth)

  return (
    <Popover.Root open={open} onOpenChange={handleOpenChange}>
      <Popover.Trigger asChild>
        <button
          id={id}
          type="button"
          disabled={disabled}
          className={`tabular flex items-center gap-2 rounded-lg border border-line-strong bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-bronze-deep disabled:cursor-not-allowed disabled:opacity-70 ${className ?? ''}`}
        >
          <CalendarIcon className="size-4 shrink-0 text-bronze" />
          <span className={selected ? 'text-ink' : 'text-ink-faint'}>
            {selected ? DISPLAY_FMT.format(selected) : placeholder}
          </span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          ref={contentRef}
          align="start"
          sideOffset={8}
          className="z-50 w-72 rounded-xl border border-line-strong bg-paper p-3 shadow-panel outline-none"
          aria-label={label}
        >
          <div className="mb-2 flex items-center justify-between">
            <button
              type="button"
              aria-label="Previous month"
              onClick={() => shiftMonth(-1)}
              className="flex size-7 items-center justify-center rounded-full text-ink-soft hover:bg-paper-sunken"
            >
              <ChevronLeftIcon className="size-4" />
            </button>
            <span className="font-serif text-sm text-ink">{MONTH_FMT.format(viewMonth)}</span>
            <button
              type="button"
              aria-label="Next month"
              onClick={() => shiftMonth(1)}
              className="flex size-7 items-center justify-center rounded-full text-ink-soft hover:bg-paper-sunken"
            >
              <ChevronRightIcon className="size-4" />
            </button>
          </div>

          <div className="grid grid-cols-7 gap-y-1">
            {WEEKDAY_LABELS.map((w, i) => (
              <span key={i} className="chart-tick py-1 text-center uppercase">
                {w}
              </span>
            ))}
            {cells.map((date, i) => {
              if (!date) return <span key={`blank-${i}`} />
              const iso = toISO(date)
              const disabled = !!min && date < min
              const isSelected = sameDay(date, selected)
              const isToday = sameDay(date, today)
              return (
                <button
                  key={iso}
                  ref={(el) => {
                    if (el) cellRefs.current.set(iso, el)
                    else cellRefs.current.delete(iso)
                  }}
                  type="button"
                  disabled={disabled}
                  tabIndex={focusIso === iso || (!focusIso && isSelected) ? 0 : -1}
                  aria-current={isToday ? 'date' : undefined}
                  aria-pressed={isSelected}
                  aria-label={new Intl.DateTimeFormat('en', {
                    weekday: 'long',
                    day: 'numeric',
                    month: 'long',
                    year: 'numeric',
                  }).format(date)}
                  onFocus={() => setFocusIso(iso)}
                  onClick={() => select(date)}
                  onKeyDown={(e) => {
                    const deltas: Record<string, number> = {
                      ArrowLeft: -1,
                      ArrowRight: 1,
                      ArrowUp: -7,
                      ArrowDown: 7,
                    }
                    if (e.key in deltas) {
                      e.preventDefault()
                      moveFocus(date, deltas[e.key])
                    } else if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      if (!disabled) select(date)
                    }
                  }}
                  className={`tabular flex size-8 items-center justify-center justify-self-center rounded-full text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-bronze disabled:cursor-not-allowed disabled:text-ink-faint/50 ${
                    isSelected
                      ? 'bg-bronze-deep text-paper'
                      : isToday
                        ? 'border border-bronze text-ink'
                        : 'text-ink hover:bg-paper-sunken'
                  }`}
                >
                  {date.getDate()}
                </button>
              )
            })}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
