export type BasemapKey = 'vector' | 'satellite' | 'terrain'

export type BasemapEntry = {
  tiles: string[]
  annotation: string[] | null
}

export type BasemapsConfig = Partial<Record<BasemapKey, BasemapEntry>> &
  Record<string, unknown>

export type RasterSource = {
  type: 'raster'
  tiles: string[]
  tileSize: 256
}

export type RasterLayer = {
  id: string
  type: 'raster'
  source: string
}

export type MapStyle = {
  version: 8
  sources: Record<string, RasterSource>
  layers: RasterLayer[]
}

export type ParsedBasemaps = {
  choices: BasemapKey[]
  defaultKey: BasemapKey | null
  selectedStyle: MapStyle
  styles: Partial<Record<BasemapKey, MapStyle>>
}

const BASEMAP_KEYS: BasemapKey[] = ['vector', 'satellite', 'terrain']

export const emptyMapStyle: MapStyle = {
  version: 8,
  sources: {},
  layers: [],
}

const EMPTY: ParsedBasemaps = {
  choices: [],
  defaultKey: null,
  selectedStyle: emptyMapStyle,
  styles: {},
}

const ABSOLUTE_URL = /^[a-z][a-z0-9+.-]*:/i

/**
 * Resolve a tile URL template against the page directory.
 *
 * Plain string concatenation on purpose: passing the template through `URL`
 * would percent-encode `{z}/{x}/{y}` and MapLibre would never substitute them.
 */
export function resolveTileUrl(pageDir: string, url: string): string {
  return ABSOLUTE_URL.test(url) ? url : pageDir + url
}

export function parseBasemaps(
  payload: BasemapsConfig | null | undefined,
  status?: number,
  pageDir = '',
): ParsedBasemaps {
  if (status === 404 || payload == null) return EMPTY

  const choices: BasemapKey[] = []
  const styles: Partial<Record<BasemapKey, MapStyle>> = {}
  for (let i = 0; i < BASEMAP_KEYS.length; i += 1) {
    const key = BASEMAP_KEYS[i]
    const entry = payload[key]
    if (entry === undefined) continue
    choices.push(key)
    styles[key] = rasterStyle(key, entry, pageDir)
  }
  if (choices.length === 0) return EMPTY
  const defaultKey = choices[0]
  return {
    choices,
    defaultKey,
    selectedStyle: styles[defaultKey] ?? emptyMapStyle,
    styles,
  }
}

function rasterStyle(
  key: BasemapKey,
  entry: BasemapEntry,
  pageDir: string,
): MapStyle {
  const sources: Record<string, RasterSource> = {
    [`${key}-base`]: {
      type: 'raster',
      tiles: resolveTiles(pageDir, entry.tiles),
      tileSize: 256,
    },
  }
  const layers: RasterLayer[] = [
    { id: `${key}-base`, type: 'raster', source: `${key}-base` },
  ]
  if (entry.annotation !== null) {
    sources[`${key}-anno`] = {
      type: 'raster',
      tiles: resolveTiles(pageDir, entry.annotation),
      tileSize: 256,
    }
    layers.push({ id: `${key}-anno`, type: 'raster', source: `${key}-anno` })
  }
  return { version: 8, sources, layers }
}

function resolveTiles(pageDir: string, tiles: string[]): string[] {
  return tiles.map((url) => resolveTileUrl(pageDir, url))
}
