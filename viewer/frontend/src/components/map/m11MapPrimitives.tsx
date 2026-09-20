import type { FeatureCollection } from 'geojson'
import type { GeoJSONSource, LayerSpecification, Map as MapLibreMap } from 'maplibre-gl'

import { dischargeColor } from '../../lib/color'
import {
  M11_BOUNDARY_LAYER_IDS,
  M11_BOUNDARY_SOURCE_ID,
  M11_RIVER_HOVER_HALO_LAYER_ID,
  M11_RIVER_HOVER_LINE_LAYER_ID,
  M11_RIVER_LAYER_IDS,
  M11_RIVER_SELECTED_HALO_LAYER_ID,
  M11_RIVER_SELECTED_LINE_LAYER_ID,
  M11_RIVER_SOURCE_ID,
  boundaryLayerSpecs,
  boundarySourceSpec,
  reachFilter,
  riverLayerSpecs,
  riverSourceSpec,
} from './m11MapBuilders'

export function registerRiverOverlay(map: MapLibreMap, data: FeatureCollection): void {
  if (map.getStyle() == null) return
  const existing = map.getSource(M11_RIVER_SOURCE_ID)
  if (existing && existing.type === 'geojson') {
    ;(existing as GeoJSONSource).setData(data)
  } else {
    removeLayers(map, M11_RIVER_LAYER_IDS)
    if (map.getSource(M11_RIVER_SOURCE_ID)) map.removeSource(M11_RIVER_SOURCE_ID)
    map.addSource(M11_RIVER_SOURCE_ID, riverSourceSpec(data))
  }
  addLayersIfMissing(map, riverLayerSpecs())
}

export function unregisterRiverOverlay(map: MapLibreMap): void {
  if (map.getStyle() == null) return
  removeLayers(map, M11_RIVER_LAYER_IDS)
  if (map.getSource(M11_RIVER_SOURCE_ID)) map.removeSource(M11_RIVER_SOURCE_ID)
}

export function registerBoundaryOverlay(map: MapLibreMap, data: FeatureCollection): void {
  if (map.getStyle() == null) return
  const existing = map.getSource(M11_BOUNDARY_SOURCE_ID)
  if (existing && existing.type === 'geojson') {
    ;(existing as GeoJSONSource).setData(data)
  } else {
    removeLayers(map, M11_BOUNDARY_LAYER_IDS)
    if (map.getSource(M11_BOUNDARY_SOURCE_ID)) map.removeSource(M11_BOUNDARY_SOURCE_ID)
    map.addSource(M11_BOUNDARY_SOURCE_ID, boundarySourceSpec(data))
  }
  addLayersIfMissing(map, boundaryLayerSpecs() as LayerSpecification[])
}

export function unregisterBoundaryOverlay(map: MapLibreMap): void {
  if (map.getStyle() == null) return
  removeLayers(map, M11_BOUNDARY_LAYER_IDS)
  if (map.getSource(M11_BOUNDARY_SOURCE_ID)) map.removeSource(M11_BOUNDARY_SOURCE_ID)
}

export function setRiverHover(map: MapLibreMap, reachId: number | null): void {
  if (map.getStyle() == null) return
  setReachFilter(map, M11_RIVER_HOVER_HALO_LAYER_ID, reachId)
  setReachFilter(map, M11_RIVER_HOVER_LINE_LAYER_ID, reachId)
}

export function setRiverSelected(map: MapLibreMap, reachId: number | null): void {
  if (map.getStyle() == null) return
  setReachFilter(map, M11_RIVER_SELECTED_HALO_LAYER_ID, reachId)
  setReachFilter(map, M11_RIVER_SELECTED_LINE_LAYER_ID, reachId)
}

export function applyReachColors(
  map: MapLibreMap,
  entries: Iterable<{ reachId: number; value: number | null }>,
  sourceId: string = M11_RIVER_SOURCE_ID,
): void {
  if (map.getStyle() == null) return
  if (!map.getSource(sourceId)) return
  for (const entry of entries) {
    map.setFeatureState(
      { source: sourceId, id: entry.reachId },
      { color: dischargeColor(entry.value), value: entry.value },
    )
  }
}

function addLayersIfMissing(map: MapLibreMap, layers: LayerSpecification[]): void {
  for (const layer of layers) {
    if (!map.getLayer(layer.id)) map.addLayer(layer)
  }
}

function removeLayers(map: MapLibreMap, ids: readonly string[]): void {
  for (let i = ids.length - 1; i >= 0; i -= 1) {
    const id = ids[i]
    if (map.getLayer(id)) map.removeLayer(id)
  }
}

function setReachFilter(map: MapLibreMap, layerId: string, reachId: number | null): void {
  if (!map.getLayer(layerId)) return
  map.setFilter(layerId, reachFilter(reachId))
}
