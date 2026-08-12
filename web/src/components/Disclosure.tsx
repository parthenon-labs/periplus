import { useId, useState, type ReactNode } from 'react'
import { ChevronDownIcon } from './icons'

export function Disclosure({
  title,
  subtitle,
  defaultOpen = false,
  children,
}: {
  title: string
  subtitle?: string
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  const bodyId = useId()

  return (
    <div className="border-b border-line last:border-b-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-4 py-4 text-left"
      >
        <span className="flex flex-col gap-0.5">
          <span className="font-serif text-lg text-ink">{title}</span>
          {subtitle ? <span className="text-sm text-ink-soft">{subtitle}</span> : null}
        </span>
        <ChevronDownIcon
          className={`size-5 shrink-0 text-bronze transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
        />
      </button>
      <div className="disclosure-body" data-open={open}>
        <div>
          <div id={bodyId} className="pb-6">
            {children}
          </div>
        </div>
      </div>
    </div>
  )
}
