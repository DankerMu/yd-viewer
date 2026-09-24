import { describe, expect, it } from 'vitest'

import { M11_ATTRIBUTION_CONTROL } from './m11MapRuntime'

describe('map attribution control', () => {
  it('is expanded and keeps the MapLibre credit next to basemap attributions', () => {
    expect(M11_ATTRIBUTION_CONTROL.compact).toBe(false)
    expect(M11_ATTRIBUTION_CONTROL.customAttribution).toContain('https://maplibre.org/')
    expect(M11_ATTRIBUTION_CONTROL.customAttribution).toContain('MapLibre')
  })
})
