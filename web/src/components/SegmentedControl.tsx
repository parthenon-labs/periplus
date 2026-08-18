export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
  disabled = false,
}: {
  label: string
  value: T
  options: { value: T; label: string; hint?: string }[]
  onChange: (next: T) => void
  disabled?: boolean
}) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-sm font-medium text-ink">{label}</span>
      <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-2">
        {options.map((opt) => {
          const active = opt.value === value
          return (
            <button
              key={opt.value}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={disabled}
              onClick={() => onChange(opt.value)}
              className={`rounded-full border px-4 py-2 text-sm transition-colors disabled:cursor-not-allowed ${
                active
                  ? 'border-bronze-deep bg-bronze-deep text-paper'
                  : 'border-line-strong bg-paper text-ink-soft enabled:hover:border-bronze disabled:opacity-45'
              }`}
            >
              {opt.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
