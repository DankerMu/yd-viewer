import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { M11DischargeLegend } from './overviewDataContracts'

describe('M11DischargeLegend', () => {
  it('lists the five bands with m³/s, then 无径流数据 last in #94ADC7', () => {
    const html = renderToStaticMarkup(<M11DischargeLegend />)
    expect(html).toContain('径流量图例')
    const labels = ['&lt;1 m³/s', '1–10 m³/s', '10–100 m³/s', '100–1000 m³/s', '≥1000 m³/s', '无径流数据']
    const positions = labels.map((label) => html.indexOf(label))
    for (const position of positions) expect(position).toBeGreaterThanOrEqual(0)
    expect([...positions].sort((a, b) => a - b)).toEqual(positions)
    const rows = html.match(/<div class="flex items-center gap-2 text-xs[\s\S]*?<\/div>/g) ?? []
    expect(rows).toHaveLength(6)
    expect(rows[5]).toContain('无径流数据')
    expect(rows[5].toLowerCase()).toContain('background-color:#94adc7')
  })
})
