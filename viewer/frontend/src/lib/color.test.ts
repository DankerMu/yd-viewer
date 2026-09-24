import { describe, expect, it } from 'vitest'

import { DISCHARGE_LEGEND, MISSING_LEGEND, dischargeColor } from './color'

describe('discharge color bands', () => {
  it('maps the eleven specified values onto spec colors', () => {
    const values = [0.5, 1, 9.99, 10, 99.9, 100, 500, 999.9, 1000, 5000, null]
    const colors = [
      '#7FB8DC',
      '#4292C6',
      '#4292C6',
      '#2171B5',
      '#2171B5',
      '#08519C',
      '#08519C',
      '#08519C',
      '#CB181D',
      '#CB181D',
      '#94ADC7',
    ]
    expect(values.map((value) => dischargeColor(value))).toEqual(colors)
  })

  it('uses the five-band legend labels and matching colors', () => {
    expect(DISCHARGE_LEGEND).toEqual([
      { label: '<1 m³/s', color: '#7FB8DC' },
      { label: '1–10 m³/s', color: '#4292C6' },
      { label: '10–100 m³/s', color: '#2171B5' },
      { label: '100–1000 m³/s', color: '#08519C' },
      { label: '≥1000 m³/s', color: '#CB181D' },
    ])
  })

  it('describes the missing-value color separately from the five bands', () => {
    expect(MISSING_LEGEND).toEqual({ label: '无径流数据', color: '#94ADC7' })
    expect(MISSING_LEGEND.color).toBe(dischargeColor(null))
  })
})
