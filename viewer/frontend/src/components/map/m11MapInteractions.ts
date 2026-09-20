import type { Map as MapLibreMap, MapGeoJSONFeature, MapMouseEvent, PointLike } from 'maplibre-gl'

import { M11_RIVER_HIT_LAYER_ID, reachIdFromUnknown } from './m11MapBuilders'

export interface M11MapOverlayInteraction {
  reachId: number
  event: MapMouseEvent
  feature: MapGeoJSONFeature
}

export function mapFeatureReachId(feature: MapGeoJSONFeature | undefined | null): number | null {
  if (!feature) return null
  const fromProperties = reachIdFromUnknown(feature.properties?.reach_id)
  if (fromProperties !== null) return fromProperties
  return reachIdFromUnknown(feature.id)
}

export function queryReachFeature(
  map: MapLibreMap,
  point: PointLike,
  layerId: string = M11_RIVER_HIT_LAYER_ID,
): MapGeoJSONFeature | null {
  if (!map.getLayer(layerId)) return null
  const features = map.queryRenderedFeatures(point, { layers: [layerId] })
  return features[0] ?? null
}

export function attachRiverInteractions(
  map: MapLibreMap,
  options: {
    hitLayerId?: string
    onHover?: (interaction: M11MapOverlayInteraction | null) => void
    onClick?: (interaction: M11MapOverlayInteraction) => void
  } = {},
): () => void {
  const hitLayerId = options.hitLayerId ?? M11_RIVER_HIT_LAYER_ID
  let lastReachId: number | null | undefined

  const emitHover = (interaction: M11MapOverlayInteraction | null) => {
    const reachId = interaction?.reachId ?? null
    if (reachId === lastReachId) return
    lastReachId = reachId
    options.onHover?.(interaction)
  }

  const onMove = (event: MapMouseEvent) => {
    const feature = queryReachFeature(map, event.point, hitLayerId)
    const reachId = mapFeatureReachId(feature)
    if (feature && reachId !== null) {
      map.getCanvas().style.cursor = 'pointer'
      emitHover({ reachId, event, feature })
      return
    }
    map.getCanvas().style.cursor = ''
    emitHover(null)
  }

  const onLeave = () => {
    map.getCanvas().style.cursor = ''
    emitHover(null)
  }

  const onClick = (event: MapMouseEvent) => {
    const feature = queryReachFeature(map, event.point, hitLayerId)
    const reachId = mapFeatureReachId(feature)
    if (!feature || reachId === null) return
    options.onClick?.({ reachId, event, feature })
  }

  map.on('mousemove', onMove)
  map.on('mouseout', onLeave)
  map.on('click', onClick)

  return () => {
    map.off('mousemove', onMove)
    map.off('mouseout', onLeave)
    map.off('click', onClick)
    map.getCanvas().style.cursor = ''
  }
}
