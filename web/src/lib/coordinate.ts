function toDegreesMinutes(value: number): { degrees: number; minutes: string } {
  const abs = Math.abs(value)
  const degrees = Math.floor(abs)
  const minutes = ((abs - degrees) * 60).toFixed(1)
  return { degrees, minutes }
}

/** Renders a decimal lat/lon pair as a chart-style coordinate, e.g. "38°41.9'N 9°12.4'W". */
export function toDMS(lat: number, lon: number): string {
  const latPart = toDegreesMinutes(lat)
  const lonPart = toDegreesMinutes(lon)
  const ns = lat >= 0 ? 'N' : 'S'
  const ew = lon >= 0 ? 'E' : 'W'
  return `${latPart.degrees}°${latPart.minutes}'${ns} ${lonPart.degrees}°${lonPart.minutes}'${ew}`
}
