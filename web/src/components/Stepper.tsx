import { MinusIcon, PlusIcon } from './icons'

export function Stepper({
  label,
  value,
  min = 0,
  max = 99,
  onChange,
  disabled = false,
}: {
  label: string
  value: number
  min?: number
  max?: number
  onChange: (next: number) => void
  disabled?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border border-line bg-paper px-3 py-2.5">
      <span className="text-sm font-medium text-ink">{label}</span>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => onChange(Math.max(min, value - 1))}
          disabled={disabled || value <= min}
          aria-label={`Decrease ${label}`}
          className="flex size-7 items-center justify-center rounded-full border border-line-strong text-bronze-deep disabled:opacity-30"
        >
          <MinusIcon className="size-3.5" />
        </button>
        <span className="tabular w-4 text-center text-sm font-medium">{value}</span>
        <button
          type="button"
          onClick={() => onChange(Math.min(max, value + 1))}
          disabled={disabled || value >= max}
          aria-label={`Increase ${label}`}
          className="flex size-7 items-center justify-center rounded-full border border-line-strong text-bronze-deep disabled:opacity-30"
        >
          <PlusIcon className="size-3.5" />
        </button>
      </div>
    </div>
  )
}
