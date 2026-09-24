import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Header } from './Header'

describe('Header brand bar', () => {
  it('is a <header> with the system title and the English subtitle', () => {
    const html = renderToStaticMarkup(<Header />)
    expect(html).toContain('<header')
    expect(html).toContain('永登流域水文模拟系统')
    expect(html).toContain('Yongdeng Basin Hydrological Modeling')
  })

  it('shows the logo and the sponsors strip', () => {
    const html = renderToStaticMarkup(<Header />)
    expect(html).toMatch(/<img[^>]*alt="永登流域水文模拟系统徽标"/)
    expect(html).toMatch(/<img[^>]*alt="合作单位"/)
  })

  it('carries no issue time or other status', () => {
    const html = renderToStaticMarkup(<Header />)
    expect(html).not.toContain('起报')
  })
})
