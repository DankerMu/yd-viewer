export const DISCHARGE_LEGEND = [
  { label: '<1', color: '#7FB8DC' },
  { label: '1–10', color: '#4292C6' },
  { label: '10–100', color: '#2171B5' },
  { label: '100–1000', color: '#08519C' },
  { label: '≥1000', color: '#CB181D' },
] as const

export function dischargeColor(value: number | null): string {
  if (value === null) return '#94ADC7'
  if (value >= 1000) return '#CB181D'
  if (value >= 100) return '#08519C'
  if (value >= 10) return '#2171B5'
  if (value >= 1) return '#4292C6'
  return '#7FB8DC'
}
