import { describe, expect, it } from 'vitest'

import type { CurveResponse } from '../../lib/api'
import { buildForecastOption } from './ForecastChart'

const CURVE: CurveResponse = {
  cycle: '2026082700',
  reach_id: 7,
  lead_hours: [1, 2, 3],
  series: { gfs: [1.5, null, 2], ifs: [null, 0.5, 1] },
}

describe('buildForecastOption', () => {
  it('enables exactly one inside dataZoom and draws no ECharts legend', () => {
    const option = buildForecastOption(CURVE)
    expect(option.dataZoom).toEqual([
      {
        type: 'inside',
        zoomOnMouseWheel: true,
        moveOnMouseMove: false,
        moveOnMouseWheel: false,
        filterMode: 'none',
      },
    ])
    expect(option).not.toHaveProperty('legend')
  })

  it('keeps null gaps: connectNulls false and null values untouched', () => {
    const option = buildForecastOption(CURVE)
    expect(option.series.map((entry) => entry.id)).toEqual(['gfs', 'ifs'])
    for (const entry of option.series) expect(entry.connectNulls).toBe(false)
    expect(option.series[0].data).toEqual([1.5, null, 2])
    expect(option.series[1].data).toEqual([null, 0.5, 1])
    expect(option.xAxis.data).toEqual(['2026-08-27 09:00', '2026-08-27 10:00', '2026-08-27 11:00'])
  })

  it('tooltip skips null points and never prints 0 for them', () => {
    const option = buildForecastOption(CURVE)
    const text = option.tooltip.formatter([
      { axisValue: '2026-08-27 09:00', seriesName: 'GFS', value: null },
      { axisValue: '2026-08-27 09:00', seriesName: 'IFS', value: 1.5 },
    ])
    expect(text.split('\n')).toEqual(['时间: 2026-08-27 09:00', 'IFS: 1.50 m³/s'])
    expect(text).not.toContain('0.00')
  })
})
