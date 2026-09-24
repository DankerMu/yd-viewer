export const DISCHARGE_LEGEND = [
  { label: '<1 m³/s', color: '#7FB8DC' },
  { label: '1–10 m³/s', color: '#4292C6' },
  { label: '10–100 m³/s', color: '#2171B5' },
  { label: '100–1000 m³/s', color: '#08519C' },
  { label: '≥1000 m³/s', color: '#CB181D' },
] as const

/** Missing-value swatch: not a sixth band, shown after the five bands. */
export const MISSING_LEGEND = { label: '无径流数据', color: '#94ADC7' } as const

export function dischargeColor(value: number | null): string {
  if (value === null) return '#94ADC7'
  if (value >= 1000) return '#CB181D'
  if (value >= 100) return '#08519C'
  if (value >= 10) return '#2171B5'
  if (value >= 1) return '#4292C6'
  return '#7FB8DC'
}
