import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { availableSources, SourceChips, sourceSubtitle } from './RiverCurveWindow'

describe('availableSources / sourceSubtitle', () => {
  it('joins both sources in gfs, ifs order', () => {
    const sources = availableSources({ ifs: [1, null], gfs: [2, null] })
    expect(sources).toEqual(['gfs', 'ifs'])
    expect(sourceSubtitle(sources)).toBe('河段 q_down 流量预报 · GFS+IFS')
  })

  it('shows a single available source', () => {
    expect(sourceSubtitle(availableSources({ gfs: [1] }))).toBe('河段 q_down 流量预报 · GFS')
    expect(sourceSubtitle(availableSources({ ifs: [null, 1] }))).toBe('河段 q_down 流量预报 · IFS')
  })

  it('treats empty arrays as absent and drops the dot without sources', () => {
    expect(availableSources({ gfs: [], ifs: [1] })).toEqual(['ifs'])
    expect(availableSources({ gfs: [], ifs: [] })).toEqual([])
    expect(sourceSubtitle([])).toBe('河段 q_down 流量预报')
  })
})

function chips(html: string) {
  const spans = html.match(/<span class="inline-flex[^"]*">[\s\S]*?<\/span>(GFS|IFS)<\/span>/g) ?? []
  const byLabel = new Map(spans.map((span) => [span.endsWith('GFS</span>') ? 'GFS' : 'IFS', span]))
  return { gfs: byLabel.get('GFS') ?? '', ifs: byLabel.get('IFS') ?? '', count: spans.length }
}

describe('SourceChips', () => {
  it('greys and strikes through the missing IFS chip', () => {
    const html = renderToStaticMarkup(<SourceChips sources={['gfs']} />)
    const { gfs, ifs, count } = chips(html)
    expect(count).toBe(2)
    expect(ifs).toContain('line-through')
    expect(ifs).toContain('text-slate-500')
    expect(gfs).toContain('text-slate-200')
    expect(gfs).not.toContain('line-through')
    expect(gfs).toContain('background-color:#22d3ee')
    expect(ifs).toContain('background-color:#34d399')
    expect(html).toContain('滚轮缩放时间轴')
  })

  it('strikes nothing when both sources are present', () => {
    const html = renderToStaticMarkup(<SourceChips sources={['gfs', 'ifs']} />)
    const { gfs, ifs, count } = chips(html)
    expect(count).toBe(2)
    expect(gfs).not.toContain('line-through')
    expect(ifs).not.toContain('line-through')
  })
})
