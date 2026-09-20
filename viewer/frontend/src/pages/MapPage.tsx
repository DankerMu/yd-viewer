import { useEffect, useMemo, useRef, useState } from 'react'
import type { Feature, FeatureCollection } from 'geojson'

import { mapLatestUrl, resolveUrl, type MapLatestResponse } from '../lib/api'
import {
  emptyMapStyle,
  parseBasemaps,
  type BasemapKey,
  type BasemapsConfig,
  type MapStyle,
  type ParsedBasemaps,
} from '../lib/basemaps'
import { boundaryBbox, type BoundaryFeature } from '../lib/bbox'
import { M11FloatingBasemapSwitcher } from '../components/map/M11FloatingControls'
import { attachRiverInteractions } from '../components/map/m11MapInteractions'
import { reachIdFromUnknown } from '../components/map/m11MapBuilders'
import {
  applyReachColors,
  registerBoundaryOverlay,
  registerRiverOverlay,
  setRiverHover,
  setRiverSelected,
  unregisterBoundaryOverlay,
  unregisterRiverOverlay,
} from '../components/map/m11MapPrimitives'
import { onStyleReady, useM11Map } from '../components/map/m11MapRuntime'
import { M11DischargeLegend } from '../components/map/overviewDataContracts'

export type MapPageProps = {
  onReachSelect?: (reachId: number) => void
  onLatestChange?: (latest: MapLatestResponse | null) => void
}

type ReachColor = { reachId: number; value: number | null }

type LatestState =
  | { status: 'pending' }
  | { status: 'ready'; data: MapLatestResponse }
  | { status: 'absent' }
  | { status: 'error' }

const EMPTY_BASEMAPS = parseBasemaps(null, 404)
const GENERIC_LOAD_ERROR = '加载失败'

export function MapPage({ onReachSelect, onLatestChange }: MapPageProps) {
  const onReachSelectRef = useRef(onReachSelect)
  const onLatestChangeRef = useRef(onLatestChange)
  onReachSelectRef.current = onReachSelect
  onLatestChangeRef.current = onLatestChange

  const [rivers, setRivers] = useState<FeatureCollection | null>(null)
  const [boundary, setBoundary] = useState<BoundaryFeature | null>(null)
  const [geometryFailed, setGeometryFailed] = useState(false)
  const [latest, setLatest] = useState<LatestState>({ status: 'pending' })
  const [parsedBasemaps, setParsedBasemaps] = useState<ParsedBasemaps>(EMPTY_BASEMAPS)
  const [basemap, setBasemap] = useState<BasemapKey | null>(null)
  const [failed, setFailed] = useState(false)
  const [hoverReachId, setHoverReachId] = useState<number | null>(null)
  const [selectedReachId, setSelectedReachId] = useState<number | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    const pageDir = new URL('.', document.baseURI).href
    const { signal } = controller

    void loadRivers()
    void loadBoundary()
    void loadLatest()
    void loadBasemaps()

    return () => {
      active = false
      controller.abort()
    }

    async function loadRivers() {
      try {
        const result = await getJson(resolveUrl(pageDir, 'geometry/rivers.geojson'), signal)
        if (!active) return
        const collection = result.ok ? asRiverCollection(result.body) : null
        if (!collection) {
          setGeometryFailed(true)
          return
        }
        setRivers(collection)
      } catch (error) {
        if (!active || isAbortError(error)) return
        setGeometryFailed(true)
      }
    }

    async function loadBoundary() {
      try {
        const result = await getJson(resolveUrl(pageDir, 'geometry/boundary.geojson'), signal)
        if (!active) return
        const feature = result.ok ? asBoundaryFeature(result.body) : null
        if (!feature) {
          setGeometryFailed(true)
          return
        }
        setBoundary(feature)
      } catch (error) {
        if (!active || isAbortError(error)) return
        setGeometryFailed(true)
      }
    }

    async function loadLatest() {
      try {
        const result = await getJson(mapLatestUrl(pageDir), signal)
        if (!active) return
        if (result.status === 404) {
          setLatest({ status: 'absent' })
          onLatestChangeRef.current?.(null)
          return
        }
        const data = result.ok ? asMapLatest(result.body) : null
        if (!data) {
          setLatest({ status: 'error' })
          setFailed(true)
          return
        }
        setLatest({ status: 'ready', data })
        onLatestChangeRef.current?.(data)
      } catch (error) {
        if (!active || isAbortError(error)) return
        setLatest({ status: 'error' })
        setFailed(true)
      }
    }

    async function loadBasemaps() {
      try {
        const result = await getJson(resolveUrl(pageDir, 'basemaps.json'), signal)
        if (!active) return
        if (result.status === 404) {
          setParsedBasemaps(EMPTY_BASEMAPS)
          setBasemap(null)
          return
        }
        if (!result.ok) {
          setFailed(true)
          return
        }
        const parsed = parseBasemaps(
          result.body !== null && typeof result.body === 'object'
            ? (result.body as BasemapsConfig)
            : {},
        )
        setParsedBasemaps(parsed)
        setBasemap(parsed.defaultKey)
      } catch (error) {
        if (!active || isAbortError(error)) return
        setFailed(true)
      }
    }
  }, [])

  const reachIds = useMemo(() => (rivers ? sortedReachIds(rivers) : []), [rivers])
  const colorEntries = useMemo<ReachColor[]>(() => {
    const values = latest.status === 'ready' ? latest.data.values : undefined
    return reachIds.map((reachId, index) => {
      const raw = values?.[index]
      return {
        reachId,
        value: typeof raw === 'number' && Number.isFinite(raw) ? raw : null,
      }
    })
  }, [latest, reachIds])

  const mapStyle: MapStyle =
    (basemap ? parsedBasemaps.styles[basemap] : undefined) ?? parsedBasemaps.selectedStyle ?? emptyMapStyle

  const handleSelectReach = (reachId: number) => {
    setSelectedReachId(reachId)
    onReachSelectRef.current?.(reachId)
  }

  const geometryReady = rivers !== null && boundary !== null && !geometryFailed

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-neutral-200">
      {geometryReady ? (
        <>
          <GeometryReadyMap
            rivers={rivers}
            boundary={boundary}
            style={mapStyle}
            colorEntries={colorEntries}
            hoverReachId={hoverReachId}
            selectedReachId={selectedReachId}
            onHoverReach={setHoverReachId}
            onSelectReach={handleSelectReach}
          />
          <M11FloatingBasemapSwitcher
            choices={parsedBasemaps.choices}
            basemap={basemap}
            onChange={setBasemap}
          />
          <M11DischargeLegend />
          {failed ? (
            <div
              className="absolute left-4 top-4 z-[130] rounded-lg border border-white/40 bg-white/80 px-3 py-2 text-sm text-neutral-800 shadow-lg"
              role="alert"
            >
              {GENERIC_LOAD_ERROR}
            </div>
          ) : null}
        </>
      ) : geometryFailed ? (
        <div className="flex h-full items-center justify-center text-sm text-neutral-700" role="alert">
          {GENERIC_LOAD_ERROR}
        </div>
      ) : null}
    </div>
  )
}

function GeometryReadyMap({
  rivers,
  boundary,
  style,
  colorEntries,
  hoverReachId,
  selectedReachId,
  onHoverReach,
  onSelectReach,
}: {
  rivers: FeatureCollection
  boundary: BoundaryFeature
  style: MapStyle
  colorEntries: ReachColor[]
  hoverReachId: number | null
  selectedReachId: number | null
  onHoverReach: (reachId: number | null) => void
  onSelectReach: (reachId: number) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const colorRef = useRef(colorEntries)
  const hoverRef = useRef(hoverReachId)
  const selectedRef = useRef(selectedReachId)
  const onHoverRef = useRef(onHoverReach)
  const onSelectRef = useRef(onSelectReach)
  colorRef.current = colorEntries
  hoverRef.current = hoverReachId
  selectedRef.current = selectedReachId
  onHoverRef.current = onHoverReach
  onSelectRef.current = onSelectReach

  const fitTo = useMemo(
    () => ({ bounds: boundaryBbox(boundary), padding: 36 }),
    [boundary],
  )
  const boundarySource = useMemo(
    (): FeatureCollection => ({
      type: 'FeatureCollection',
      features: [boundary as Feature],
    }),
    [boundary],
  )
  const map = useM11Map(containerRef, { style, fitTo })

  useEffect(() => {
    if (!map) return
    const apply = () => {
      registerBoundaryOverlay(map, boundarySource)
      registerRiverOverlay(map, rivers)
      applyReachColors(map, colorRef.current)
      setRiverHover(map, hoverRef.current)
      setRiverSelected(map, selectedRef.current)
    }
    const stop = onStyleReady(map, apply)
    return () => {
      stop()
      unregisterRiverOverlay(map)
      unregisterBoundaryOverlay(map)
    }
  }, [map, rivers, boundarySource])

  useEffect(() => {
    if (!map) return
    applyReachColors(map, colorEntries)
  }, [map, colorEntries])

  useEffect(() => {
    if (!map) return
    setRiverHover(map, hoverReachId)
  }, [map, hoverReachId])

  useEffect(() => {
    if (!map) return
    setRiverSelected(map, selectedReachId)
  }, [map, selectedReachId])

  useEffect(() => {
    if (!map) return
    return attachRiverInteractions(map, {
      onHover: (interaction) => {
        onHoverRef.current(interaction?.reachId ?? null)
      },
      onClick: (interaction) => {
        onSelectRef.current(interaction.reachId)
      },
    })
  }, [map])

  return <div ref={containerRef} className="h-full w-full" />
}

function sortedReachIds(rivers: FeatureCollection): number[] {
  const ids: number[] = []
  for (let i = 0; i < rivers.features.length; i += 1) {
    const reachId = reachIdFromUnknown(rivers.features[i]?.properties?.reach_id)
    if (reachId !== null) ids.push(reachId)
  }
  ids.sort((a, b) => a - b)
  return ids
}

function asRiverCollection(body: unknown): FeatureCollection | null {
  if (body === null || typeof body !== 'object') return null
  const collection = body as FeatureCollection
  if (collection.type !== 'FeatureCollection' || !Array.isArray(collection.features)) {
    return null
  }
  return collection
}

function asBoundaryFeature(body: unknown): BoundaryFeature | null {
  if (body === null || typeof body !== 'object') return null
  const feature = body as BoundaryFeature
  if (feature.type !== 'Feature') return null
  const geometry = feature.geometry
  if (geometry === null || typeof geometry !== 'object') return null
  if (geometry.type !== 'Polygon' && geometry.type !== 'MultiPolygon') return null
  return feature
}

function asMapLatest(body: unknown): MapLatestResponse | null {
  if (body === null || typeof body !== 'object') return null
  const latest = body as MapLatestResponse
  if (typeof latest.cycle !== 'string' || typeof latest.valid_time !== 'string') return null
  if (latest.source !== 'gfs' && latest.source !== 'ifs') return null
  if (!Array.isArray(latest.values)) return null
  return latest
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

async function getJson(
  url: string,
  signal: AbortSignal,
): Promise<{ ok: true; status: number; body: unknown } | { ok: false; status: number }> {
  const response = await fetch(url, { signal })
  if (response.status === 404) return { ok: false, status: 404 }
  if (!response.ok) return { ok: false, status: response.status }
  return { ok: true, status: response.status, body: await response.json() }
}
