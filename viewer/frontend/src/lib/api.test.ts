import { describe, expect, it } from 'vitest'

import { curveUrl, cyclesUrl, mapLatestUrl, resolveUrl } from './api'

const PREFIX_PAGE = 'https://h/yd/'
const ROOT_PAGE = 'https://h/'

describe('relative API URLs', () => {
  it('keeps the /yd/ prefix on the cycles request', () => {
    expect(cyclesUrl(PREFIX_PAGE)).toBe('https://h/yd/api/cycles')
    expect(resolveUrl(PREFIX_PAGE, 'api/cycles')).toBe('https://h/yd/api/cycles')
  })

  it('keeps /api/cycles on a root deployment', () => {
    expect(cyclesUrl(ROOT_PAGE)).toBe('https://h/api/cycles')
  })

  it('keeps the /yd/ prefix on map/latest and the curve path', () => {
    expect(mapLatestUrl(PREFIX_PAGE)).toBe('https://h/yd/api/map/latest')
    expect(curveUrl(PREFIX_PAGE, '2026082700', 1)).toBe(
      'https://h/yd/api/cycles/2026082700/reaches/1',
    )
  })

  it('keeps geometry and basemaps.json under the deployment prefix', () => {
    expect(resolveUrl(PREFIX_PAGE, 'geometry/rivers.geojson')).toBe(
      'https://h/yd/geometry/rivers.geojson',
    )
    expect(resolveUrl(PREFIX_PAGE, './basemaps.json')).toBe(
      'https://h/yd/basemaps.json',
    )
  })
})
