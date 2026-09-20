import { describe, expect, it } from 'vitest'

import { parseBasemaps, type MapStyle } from './basemaps'

const VECTOR_TILES = ['https://tiles.example/vector/{z}/{x}/{y}.png']
const VECTOR_ANNO = ['https://tiles.example/vector-anno/{z}/{x}/{y}.png']
const SATELLITE_TILES = ['https://tiles.example/sat/{z}/{x}/{y}.png']
const TERRAIN_TILES = ['https://tiles.example/ter/{z}/{x}/{y}.png']

function layerTileSets(style: MapStyle): string[][] {
  expect(style.version).toBe(8)
  const seen = new Set<string>()
  const tiles: string[][] = []
  for (let i = 0; i < style.layers.length; i += 1) {
    const layer = style.layers[i]
    expect(layer.id.length).toBeGreaterThan(0)
    expect(seen.has(layer.id)).toBe(false)
    seen.add(layer.id)
    expect(layer.type).toBe('raster')
    const source = style.sources[layer.source]
    if (source === undefined) {
      throw new Error(`layer ${layer.id} source is missing`)
    }
    expect(source.type).toBe('raster')
    tiles.push(source.tiles)
  }
  return tiles
}

function expectEmptyStyle(style: MapStyle) {
  expect(style.version).toBe(8)
  expect(style.layers).toEqual([])
  expect(style.sources).toEqual({})
}

describe('basemaps.json parsing', () => {
  it('lists vector then satellite and defaults to vector even when JSON order differs', () => {
    const parsed = parseBasemaps({
      satellite: { tiles: SATELLITE_TILES, annotation: null },
      other: { tiles: TERRAIN_TILES, annotation: null },
      vector: { tiles: VECTOR_TILES, annotation: VECTOR_ANNO },
    })
    expect(parsed.choices).toEqual(['vector', 'satellite'])
    expect(parsed.defaultKey).toBe('vector')
    expect(layerTileSets(parsed.selectedStyle)).toEqual([
      VECTOR_TILES,
      VECTOR_ANNO,
    ])
  })

  it('defaults a satellite-only configuration to satellite', () => {
    const parsed = parseBasemaps({
      satellite: { tiles: SATELLITE_TILES, annotation: null },
    })
    expect(parsed.choices).toEqual(['satellite'])
    expect(parsed.defaultKey).toBe('satellite')
    expect(layerTileSets(parsed.selectedStyle)).toEqual([SATELLITE_TILES])
  })

  it('defaults a terrain-only configuration to terrain', () => {
    const parsed = parseBasemaps({
      terrain: { tiles: TERRAIN_TILES, annotation: null },
    })
    expect(parsed.choices).toEqual(['terrain'])
    expect(parsed.defaultKey).toBe('terrain')
    expect(layerTileSets(parsed.selectedStyle)).toEqual([TERRAIN_TILES])
  })

  it('builds a base raster layer plus an annotation layer above it', () => {
    const parsed = parseBasemaps({
      vector: { tiles: VECTOR_TILES, annotation: VECTOR_ANNO },
    })
    const style = parsed.styles.vector
    if (style === undefined) {
      throw new Error('expected vector style')
    }
    expect(layerTileSets(style)).toEqual([VECTOR_TILES, VECTOR_ANNO])
    expect(layerTileSets(parsed.selectedStyle)).toEqual([
      VECTOR_TILES,
      VECTOR_ANNO,
    ])
  })

  it('keeps only the base raster layer when annotation is null', () => {
    const parsed = parseBasemaps({
      satellite: { tiles: SATELLITE_TILES, annotation: null },
    })
    const style = parsed.styles.satellite
    if (style === undefined) {
      throw new Error('expected satellite style')
    }
    expect(layerTileSets(style)).toEqual([SATELLITE_TILES])
  })

  it('uses zero choices and an empty style for 404, empty object, absent payload, and unknown-only keys', () => {
    const empty = [
      parseBasemaps(
        { vector: { tiles: VECTOR_TILES, annotation: null } },
        404,
      ),
      parseBasemaps({}),
      parseBasemaps(null),
      parseBasemaps(undefined),
      parseBasemaps({
        other: { tiles: VECTOR_TILES, annotation: null },
      }),
    ]
    for (const parsed of empty) {
      expect(parsed.choices).toEqual([])
      expect(parsed.defaultKey).toBe(null)
      expect(parsed.styles).toEqual({})
      expectEmptyStyle(parsed.selectedStyle)
    }
  })
})
