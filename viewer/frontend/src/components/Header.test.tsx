import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Header } from './Header'

const TITLE = '永登流域水文模拟系统'
const DISCHARGE = '流量 (m³/s)'

describe('Header', () => {
  it('renders the system title and the discharge label', () => {
    const html = renderToStaticMarkup(<Header cycle="2026082712" />)
    expect(html).toContain(TITLE)
    expect(html).toContain(DISCHARGE)
  })

  it('shows 暂无数据 when there is no cycle', () => {
    const html = renderToStaticMarkup(<Header cycle={null} />)
    expect(html).toContain('暂无数据')
  })

  it('shows the Beijing issue time for a cycle', () => {
    const html = renderToStaticMarkup(<Header cycle="2026082712" />)
    expect(html).toContain('起报 2026-08-27 20:00 北京时间')
  })

  it('puts the system title before the discharge label', () => {
    const html = renderToStaticMarkup(<Header cycle={null} />)
    const i = html.indexOf(TITLE)
    const j = html.indexOf(DISCHARGE)
    expect(i).toBeGreaterThanOrEqual(0)
    expect(i).toBeLessThan(j)
  })
})
