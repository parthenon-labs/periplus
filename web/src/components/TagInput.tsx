import { useState, type KeyboardEvent } from 'react'
import { CloseIcon } from './icons'

export function TagInput({
  label,
  hint,
  values,
  onChange,
  placeholder,
}: {
  label: string
  hint?: string
  values: string[]
  onChange: (next: string[]) => void
  placeholder?: string
}) {
  const [draft, setDraft] = useState('')

  const commit = () => {
    const trimmed = draft.trim()
    if (trimmed && !values.includes(trimmed)) {
      onChange([...values, trimmed])
    }
    setDraft('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      commit()
    } else if (e.key === 'Backspace' && draft === '' && values.length > 0) {
      onChange(values.slice(0, -1))
    }
  }

  return (
    <label className="flex flex-col gap-2">
      <span className="text-sm font-medium text-ink">{label}</span>
      {hint ? <span className="-mt-1 text-xs text-ink-faint">{hint}</span> : null}
      <span className="flex flex-wrap items-center gap-1.5 rounded-lg border border-line bg-paper px-2.5 py-2 focus-within:border-bronze">
        {values.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 rounded-full bg-bronze-tint px-2.5 py-1 text-xs text-bronze-deep"
          >
            {tag}
            <button
              type="button"
              onClick={() => onChange(values.filter((t) => t !== tag))}
              aria-label={`Remove ${tag}`}
              className="rounded-full text-bronze-deep/70 hover:text-bronze-deep"
            >
              <CloseIcon className="size-3" />
            </button>
          </span>
        ))}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onBlur={commit}
          placeholder={values.length === 0 ? placeholder : undefined}
          className="min-w-[8ch] flex-1 bg-transparent py-1 text-sm outline-none placeholder:text-ink-faint"
        />
      </span>
    </label>
  )
}
