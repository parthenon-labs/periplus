const LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ' // omits I/O to avoid look-alikes

/**
 * Deterministically renders an existing id as a chart-archive-style tag,
 * e.g. "brief-lisbon" -> "PL-2947". This is a cosmetic re-encoding of a
 * real identifier for the "sailing log" motif, never a fabricated value:
 * the same id always produces the same tag, and no meaning beyond identity
 * is implied.
 */
export function archiveNumber(id: string | null | undefined, prefixLength = 2): string {
  if (!id) return '—'
  let hash = 0
  for (let i = 0; i < id.length; i += 1) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0
  }
  const letters = Array.from({ length: prefixLength }, (_, i) => {
    const shifted = hash >>> (i * 5)
    return LETTERS[shifted % LETTERS.length]
  }).join('')
  const digits = String(hash % 10000).padStart(4, '0')
  return `${letters}-${digits}`
}
