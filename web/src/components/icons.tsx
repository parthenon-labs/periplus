// Hand-authored line icon set, one stroke weight, used consistently across
// the app instead of an icon font, emoji, or a mismatched external set.
import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

const base = {
  viewBox: '0 0 20 20',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

export function CompassIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="10" cy="10" r="7.4" />
      <path d="M12.6 7.4 11 11l-3.6 1.6L9 9.1z" strokeLinejoin="round" />
      <circle cx="10" cy="10" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function ChevronDownIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="m5.5 7.5 4.5 5 4.5-5" />
    </svg>
  )
}

export function ChevronRightIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="m7.5 5.5 5 4.5-5 4.5" />
    </svg>
  )
}

export function ExternalLinkIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M8.3 5.5H5.6a1.1 1.1 0 0 0-1.1 1.1v7.8a1.1 1.1 0 0 0 1.1 1.1h7.8a1.1 1.1 0 0 0 1.1-1.1v-2.7" />
      <path d="M11.5 4.5h4v4" />
      <path d="M15.3 4.7 9.6 10.4" />
    </svg>
  )
}

export function CheckIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="m5 10.3 3.2 3.2L15 6.7" />
    </svg>
  )
}

export function AlertIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M10 3.5 17 15.5H3z" strokeLinejoin="round" />
      <path d="M10 8.2v3.4" />
      <circle cx="10" cy="13.6" r="0.55" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function ClockIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="10" cy="10" r="7" />
      <path d="M10 6v4.3l3 2" />
    </svg>
  )
}

export function PlusIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M10 5v10M5 10h10" />
    </svg>
  )
}

export function MinusIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M5 10h10" />
    </svg>
  )
}

export function CloseIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="m5.5 5.5 9 9M14.5 5.5l-9 9" />
    </svg>
  )
}

export function AnchorRouteIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="10" cy="4.6" r="1.5" />
      <path d="M10 6.1V16" />
      <path d="M5.5 12a4.5 4.5 0 0 0 9 0" />
      <path d="M4 12h1.5M14.5 12H16" />
    </svg>
  )
}

export function TransferIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M4 7.5h9.5M4 7.5l3-3M4 7.5l3 3" />
      <path d="M16 12.5h-9.5M16 12.5l-3-3M16 12.5l-3 3" />
    </svg>
  )
}

export function ChevronLeftIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="m12.5 5.5-5 4.5 5 4.5" />
    </svg>
  )
}

export function CalendarIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <rect x="3.5" y="4.5" width="13" height="12" rx="1.6" />
      <path d="M3.5 8.3h13" />
      <path d="M7 3v3M13 3v3" />
    </svg>
  )
}

export function PeopleIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="7.3" cy="7" r="2.3" />
      <path d="M2.8 16c.4-2.7 2.2-4.2 4.5-4.2s4.1 1.5 4.5 4.2" />
      <circle cx="14" cy="6.4" r="1.8" />
      <path d="M13 11.9c1.9.2 3.2 1.6 3.6 3.9" />
    </svg>
  )
}

export function CoinIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="10" cy="10" r="7" />
      <path d="M10 6.3v7.4" />
      <path d="M12.2 8c-.3-.9-1-1.4-2.2-1.4-1.3 0-2.2.6-2.2 1.6 0 2.3 4.4.9 4.4 3.2 0 1-.9 1.6-2.2 1.6-1.2 0-1.9-.5-2.2-1.4" />
    </svg>
  )
}

export function StarIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M10 3.2 12 8l5.2.5-4 3.4 1.2 5.1L10 14.2l-4.4 2.8 1.2-5.1-4-3.4L8 8z" strokeLinejoin="round" />
    </svg>
  )
}

export function PenIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M12.6 3.9 16.1 7.4 7 16.5l-3.9.9.9-3.9z" strokeLinejoin="round" />
      <path d="M11 5.5 14.5 9" />
    </svg>
  )
}
