const DATE_FMT = new Intl.DateTimeFormat('en', {
  day: 'numeric',
  month: 'short',
})

const DATE_FMT_WITH_YEAR = new Intl.DateTimeFormat('en', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

const DAY_FMT = new Intl.DateTimeFormat('en', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
})

function parseDateOnly(iso: string): Date {
  // Dates from the API are date-only (YYYY-MM-DD). Parsing as UTC avoids
  // local-timezone drift shifting the day by one.
  return new Date(`${iso}T00:00:00Z`)
}

export function formatDateRange(startIso: string, endIso: string): string {
  const start = parseDateOnly(startIso)
  const end = parseDateOnly(endIso)
  const sameYear = start.getUTCFullYear() === end.getUTCFullYear()
  const startLabel = DATE_FMT.format(start)
  const endLabel = sameYear ? DATE_FMT_WITH_YEAR.format(end) : DATE_FMT_WITH_YEAR.format(end)
  return `${startLabel} – ${endLabel}`
}

export function formatDay(iso: string): string {
  return DAY_FMT.format(parseDateOnly(iso))
}

export function formatNights(startIso: string, endIso: string): number | null {
  if (!startIso || !endIso) return null
  const start = parseDateOnly(startIso)
  const end = parseDateOnly(endIso)
  const ms = end.getTime() - start.getTime()
  if (Number.isNaN(ms) || ms < 0) return null
  return Math.round(ms / (1000 * 60 * 60 * 24))
}

// ItineraryItem.start/end are Pydantic `time` values, serialized as ISO 8601
// "HH:MM:SS" — always with seconds, even though the app only ever schedules on the
// minute. Drop a trailing ":00" seconds component so "09:30:00" reads as "09:30";
// anything else (already-short strings, non-zero seconds) passes through untouched.
function trimSeconds(value: string): string {
  return value.replace(/^(\d{1,2}:\d{2}):00$/, '$1')
}

export function formatTimeRange(start?: string | null, end?: string | null): string {
  if (start && end) return `${trimSeconds(start)}–${trimSeconds(end)}`
  if (start) return trimSeconds(start)
  return 'Time to be confirmed'
}

export function formatMoney(amount: number | null | undefined, currency: string): string | null {
  if (amount === null || amount === undefined) return null
  try {
    return new Intl.NumberFormat('en', {
      style: 'currency',
      currency,
      maximumFractionDigits: 0,
    }).format(amount)
  } catch {
    return `${amount} ${currency}`
  }
}

export function formatDuration(minutes: number | null | undefined): string | null {
  if (minutes === null || minutes === undefined) return null
  if (minutes < 60) return `${minutes} min`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m === 0 ? `${h}h` : `${h}h ${m}m`
}

export function formatConfidence(confidence: number): string {
  return `${Math.round(confidence * 100)}%`
}

export function formatPublished(dateLike?: string | null): string | null {
  if (!dateLike) return null
  const d = new Date(dateLike)
  if (Number.isNaN(d.getTime())) return dateLike
  return new Intl.DateTimeFormat('en', { day: 'numeric', month: 'short', year: 'numeric' }).format(d)
}

export function formatLogDate(date: Date): string {
  return new Intl.DateTimeFormat('en', { day: 'numeric', month: 'short', year: 'numeric' }).format(date)
}
