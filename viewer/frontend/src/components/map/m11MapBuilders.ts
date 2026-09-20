import type { FeatureCollection } from 'geojson'
import type {
  FilterSpecification,
  GeoJSONSourceSpecification,
  LineLayerSpecification,
} from 'maplibre-gl'

import { dischargeColor } from '../../lib/color'

export const REACH_ID_PROPERTY = 'reach_id'

export const M11_RIVER_SOURCE_ID = 'm11-rivers'
export const M11_RIVER_CASING_LAYER_ID = 'm11-rivers-casing'
export const M11_RIVER_LINE_LAYER_ID = 'm11-rivers-line'
export const M11_RIVER_HOVER_HALO_LAYER_ID = 'm11-rivers-hover-halo'
export const M11_RIVER_HOVER_LINE_LAYER_ID = 'm11-rivers-hover-line'
export const M11_RIVER_SELECTED_HALO_LAYER_ID = 'm11-rivers-selected-halo'
export const M11_RIVER_SELECTED_LINE_LAYER_ID = 'm11-rivers-selected-line'
export const M11_RIVER_HIT_LAYER_ID = 'm11-rivers-hit'

export const M11_BOUNDARY_SOURCE_ID = 'm11-boundary'
export const M11_BOUNDARY_FILL_LAYER_ID = 'm11-boundary-fill'
export const M11_BOUNDARY_OUTLINE_LAYER_ID = 'm11-boundary-outline'

export const M11_ROUND_LINE_LAYOUT = { 'line-cap': 'round' as const, 'line-join': 'round' as const }

export const M11_RIVER_LAYER_IDS = [
  M11_RIVER_CASING_LAYER_ID,
  M11_RIVER_LINE_LAYER_ID,
  M11_RIVER_HOVER_HALO_LAYER_ID,
  M11_RIVER_HOVER_LINE_LAYER_ID,
  M11_RIVER_SELECTED_HALO_LAYER_ID,
  M11_RIVER_SELECTED_LINE_LAYER_ID,
  M11_RIVER_HIT_LAYER_ID,
] as const

export const M11_BOUNDARY_LAYER_IDS = [M11_BOUNDARY_FILL_LAYER_ID, M11_BOUNDARY_OUTLINE_LAYER_ID] as const

export type M11LineLayerProps = Extract<LineLayerSpecification, { type: 'line' }>

const MISSING_COLOR = dischargeColor(null)

const M11_OVERLAY_HIT_PAINT: M11LineLayerProps['paint'] = {
  'line-color': '#000000',
  'line-opacity': 0,
  'line-width': 16,
}

export function reachIdFromUnknown(value: unknown): number | null {
  if (typeof value === 'number' && Number.isInteger(value)) return value
  if (typeof value === 'string' && /^-?\d+$/.test(value)) return Number(value)
  return null
}

export function reachFilter(reachId?: number | null): FilterSpecification {
  return ['==', ['get', REACH_ID_PROPERTY], reachId ?? -1] as FilterSpecification
}

export function riverSourceSpec(data: FeatureCollection): GeoJSONSourceSpecification {
  return {
    type: 'geojson',
    data,
    promoteId: REACH_ID_PROPERTY,
  }
}

export function boundarySourceSpec(data: FeatureCollection): GeoJSONSourceSpecification {
  return {
    type: 'geojson',
    data,
  }
}

export function riverLinePaint(): NonNullable<M11LineLayerProps['paint']> {
  return {
    'line-color': ['coalesce', ['feature-state', 'color'], ['get', 'color'], MISSING_COLOR],
    'line-width': ['interpolate', ['linear'], ['zoom'], 5, 1.2, 8, 2, 12, 3.2],
    'line-opacity': 0.95,
  }
}

export function riverCasingPaint(): NonNullable<M11LineLayerProps['paint']> {
  return {
    'line-color': '#FFFFFF',
    'line-opacity': 0.85,
    'line-width': ['interpolate', ['linear'], ['zoom'], 5, 2.2, 8, 3.4, 12, 5],
  }
}

export function riverLayerSpecs(): M11LineLayerProps[] {
  return [
    {
      id: M11_RIVER_CASING_LAYER_ID,
      type: 'line',
      source: M11_RIVER_SOURCE_ID,
      layout: M11_ROUND_LINE_LAYOUT,
      paint: riverCasingPaint(),
    },
    {
      id: M11_RIVER_LINE_LAYER_ID,
      type: 'line',
      source: M11_RIVER_SOURCE_ID,
      layout: M11_ROUND_LINE_LAYOUT,
      paint: riverLinePaint(),
    },
    {
      id: M11_RIVER_HOVER_HALO_LAYER_ID,
      type: 'line',
      source: M11_RIVER_SOURCE_ID,
      layout: M11_ROUND_LINE_LAYOUT,
      filter: reachFilter(null),
      paint: {
        'line-color': '#FFFFFF',
        'line-width': 8,
        'line-opacity': 0.55,
      },
    },
    {
      id: M11_RIVER_HOVER_LINE_LAYER_ID,
      type: 'line',
      source: M11_RIVER_SOURCE_ID,
      layout: M11_ROUND_LINE_LAYOUT,
      filter: reachFilter(null),
      paint: {
        'line-color': '#22d3ee',
        'line-width': 4.5,
        'line-opacity': 1,
      },
    },
    {
      id: M11_RIVER_SELECTED_HALO_LAYER_ID,
      type: 'line',
      source: M11_RIVER_SOURCE_ID,
      layout: M11_ROUND_LINE_LAYOUT,
      filter: reachFilter(null),
      paint: {
        'line-color': '#FFFFFF',
        'line-width': 10,
        'line-opacity': 0.78,
      },
    },
    {
      id: M11_RIVER_SELECTED_LINE_LAYER_ID,
      type: 'line',
      source: M11_RIVER_SOURCE_ID,
      layout: M11_ROUND_LINE_LAYOUT,
      filter: reachFilter(null),
      paint: {
        'line-color': '#F97316',
        'line-width': 6,
        'line-opacity': 1,
      },
    },
    {
      id: M11_RIVER_HIT_LAYER_ID,
      type: 'line',
      source: M11_RIVER_SOURCE_ID,
      layout: M11_ROUND_LINE_LAYOUT,
      paint: M11_OVERLAY_HIT_PAINT,
    },
  ]
}

export function boundaryLayerSpecs(): Array<{
  id: string
  type: 'fill' | 'line'
  source: string
  paint: Record<string, unknown>
}> {
  return [
    {
      id: M11_BOUNDARY_FILL_LAYER_ID,
      type: 'fill',
      source: M11_BOUNDARY_SOURCE_ID,
      paint: {
        'fill-color': '#1E88E5',
        'fill-opacity': 0.12,
      },
    },
    {
      id: M11_BOUNDARY_OUTLINE_LAYER_ID,
      type: 'line',
      source: M11_BOUNDARY_SOURCE_ID,
      paint: {
        'line-color': '#0F3460',
        'line-width': 1.4,
        'line-opacity': 0.86,
      },
    },
  ]
}

