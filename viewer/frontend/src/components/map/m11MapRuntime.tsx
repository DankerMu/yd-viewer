import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { Map as MapLibreMap, NavigationControl, ScaleControl } from 'maplibre-gl'
import type { StyleSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import { emptyMapStyle, type MapStyle } from '../../lib/basemaps'
import type { LngLatBounds } from '../../lib/bbox'

export interface M11MapCameraFit {
  bounds: LngLatBounds
  padding?: number
}

export function createM11Map(
  container: HTMLElement,
  options: {
    style?: MapStyle
    fitTo?: M11MapCameraFit | null
  } = {},
): MapLibreMap {
  const style = (options.style ?? emptyMapStyle) as StyleSpecification
  const fitTo = options.fitTo
  const map = new MapLibreMap({
    container,
    style,
    attributionControl: { compact: true },
    ...(fitTo
      ? {
          bounds: fitTo.bounds,
          fitBoundsOptions: { padding: fitTo.padding ?? 36, duration: 0 },
        }
      : { center: [0, 0] as [number, number], zoom: 1 }),
  })
  map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
  map.addControl(new ScaleControl({ maxWidth: 120 }), 'bottom-left')
  return map
}

export function setM11MapStyle(map: MapLibreMap, style: MapStyle): void {
  map.setStyle(style as StyleSpecification)
}

export function onStyleReady(map: MapLibreMap, callback: () => void): () => void {
  if (map.isStyleLoaded()) callback()
  map.on('style.load', callback)
  return () => {
    map.off('style.load', callback)
  }
}

export function useM11Map(
  containerRef: RefObject<HTMLElement | null>,
  options: {
    style: MapStyle
    fitTo?: M11MapCameraFit | null
  },
): MapLibreMap | null {
  const [map, setMap] = useState<MapLibreMap | null>(null)
  const fitToRef = useRef(options.fitTo)
  const styleRef = useRef(options.style)

  useLayoutEffect(() => {
    const container = containerRef.current
    if (!container) return
    const instance = createM11Map(container, {
      style: styleRef.current,
      fitTo: fitToRef.current,
    })
    setMap(instance)
    return () => {
      instance.remove()
    }
  }, [containerRef])

  useEffect(() => {
    if (!map || styleRef.current === options.style) return
    styleRef.current = options.style
    setM11MapStyle(map, options.style)
  }, [map, options.style])

  return map
}

