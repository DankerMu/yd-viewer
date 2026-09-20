export type LngLatBounds = [[number, number], [number, number]]

export type BoundaryGeometry =
  | { type: 'Polygon'; coordinates: number[][][] }
  | { type: 'MultiPolygon'; coordinates: number[][][][] }

export type BoundaryFeature = {
  type: 'Feature'
  geometry: BoundaryGeometry
  properties?: Record<string, unknown> | null
}

export function boundaryBbox(feature: BoundaryFeature): LngLatBounds {
  let minLon = Infinity
  let minLat = Infinity
  let maxLon = -Infinity
  let maxLat = -Infinity

  const geometry = feature.geometry
  if (geometry.type === 'Polygon') {
    visitPolygon(geometry.coordinates)
  } else {
    const polygons = geometry.coordinates
    for (let i = 0; i < polygons.length; i += 1) {
      visitPolygon(polygons[i])
    }
  }

  return [
    [minLon, minLat],
    [maxLon, maxLat],
  ]

  function visitPolygon(rings: number[][][]) {
    for (let i = 0; i < rings.length; i += 1) {
      const ring = rings[i]
      for (let j = 0; j < ring.length; j += 1) {
        const lon = ring[j][0]
        const lat = ring[j][1]
        if (lon < minLon) minLon = lon
        if (lat < minLat) minLat = lat
        if (lon > maxLon) maxLon = lon
        if (lat > maxLat) maxLat = lat
      }
    }
  }
}
