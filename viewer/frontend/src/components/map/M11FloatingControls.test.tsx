import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { M11FloatingBasemapSwitcher, M11FloatingLayerCard } from './M11FloatingControls'

describe('M11FloatingLayerCard', () => {
  it('shows the discharge layer and the Beijing issue time of the cycle', () => {
    const html = renderToStaticMarkup(<M11FloatingLayerCard cycle="2026082712" />)
    expect(html).toContain('起报 2026-08-27 20:00 北京时间')
    expect(html).toContain('q_down / m³/s')
    expect(html).toContain('流量')
    expect(html).toContain('水文')
  })

  it('shows 暂无数据 without a cycle and has no buttons, 气象 or 代站', () => {
    const html = renderToStaticMarkup(<M11FloatingLayerCard cycle={null} />)
    expect(html).toContain('暂无数据')
    expect(html).not.toContain('起报')
    for (const markup of [html, renderToStaticMarkup(<M11FloatingLayerCard cycle="2026082712" />)]) {
      expect(markup).not.toContain('<button')
      expect(markup).not.toContain('气象')
      expect(markup).not.toContain('代站')
    }
  })
})

describe('M11FloatingBasemapSwitcher', () => {
  it('lists only the configured basemaps, each with an icon, vector selected', () => {
    const html = renderToStaticMarkup(
      <M11FloatingBasemapSwitcher choices={['vector', 'satellite']} basemap="vector" />,
    )
    const buttons = html.match(/<button[\s\S]*?<\/button>/g) ?? []
    expect(buttons).toHaveLength(2)
    expect(buttons[0]).toContain('aria-pressed="true"')
    expect(buttons[0]).toContain('bg-primary-600')
    expect(buttons[0]).toContain('矢量')
    expect(buttons[1]).toContain('aria-pressed="false"')
    expect(buttons[1]).not.toContain('bg-primary-600')
    expect(buttons[1]).toContain('卫星')
    for (const button of buttons) expect(button).toContain('<svg')
    expect(html).not.toContain('地形')
  })

  it('renders nothing when no basemap is configured', () => {
    expect(renderToStaticMarkup(<M11FloatingBasemapSwitcher choices={[]} basemap={null} />)).toBe('')
  })
})
