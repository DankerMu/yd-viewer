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

export function parseBasemaps(
  payload: BasemapsConfig | null | undefined,
  status?: number,
): ParsedBasemaps {
  if (status === 404 || payload == null) return EMPTY

  const choices: BasemapKey[] = []
  const styles: Partial<Record<BasemapKey, MapStyle>> = {}
  for (let i = 0; i < BASEMAP_KEYS.length; i += 1) {
    const key = BASEMAP_KEYS[i]
    const entry = payload[key]
    if (entry === undefined) continue
    choices.push(key)
    styles[key] = rasterStyle(key, entry)
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

function rasterStyle(key: BasemapKey, entry: BasemapEntry): MapStyle {
  const sources: Record<string, RasterSource> = {
    [`${key}-base`]: { type: 'raster', tiles: entry.tiles, tileSize: 256 },
  }
  const layers: RasterLayer[] = [
    { id: `${key}-base`, type: 'raster', source: `${key}-base` },
  ]
  if (entry.annotation !== null) {
    sources[`${key}-anno`] = {
      type: 'raster',
      tiles: entry.annotation,
      tileSize: 256,
    }
    layers.push({ id: `${key}-anno`, type: 'raster', source: `${key}-anno` })
  }
  return { version: 8, sources, layers }
}
