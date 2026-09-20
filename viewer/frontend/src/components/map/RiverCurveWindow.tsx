import { useEffect, useState } from 'react'

import {
  curveUrl,
  cyclesUrl,
  type CurveResponse,
  type CycleEntry,
} from '../../lib/api'
import { cycleOptions, type CycleOption } from '../../lib/cycles'
import { formatBeijingTime } from '../../lib/time'
import { ForecastChart } from '../charts/ForecastChart'
import { M11DraggableCurveWindow } from './M11DraggableCurveWindow'

const GENERIC_LOAD_ERROR = '加载失败'
const GENERIC_NO_DATA = '暂无数据'
const GENERIC_LOADING = '加载中'

type CyclesState =
  | { status: 'pending' }
  | { status: 'ready'; options: CycleOption[] }
  | { status: 'error' }

type CurveState =
  | { status: 'pending'; cycle: string; reachId: number }
  | { status: 'ready'; cycle: string; reachId: number; data: CurveResponse }
  | { status: 'absent'; cycle: string; reachId: number }
  | { status: 'error'; cycle: string; reachId: number }

export function RiverCurveWindow({
  reachId,
  mapCycle,
  onClose,
}: {
  reachId: number | null
  mapCycle: string | null
  onClose: () => void
}) {
  if (reachId === null || mapCycle === null) return null
  return (
    <RiverCurveSession
      key={reachId}
      reachId={reachId}
      mapCycle={mapCycle}
      onClose={onClose}
    />
  )
}

function RiverCurveSession({
  reachId,
  mapCycle,
  onClose,
}: {
  reachId: number
  mapCycle: string
  onClose: () => void
}) {
  const [selectedCycle, setSelectedCycle] = useState(mapCycle)
  const [cycles, setCycles] = useState<CyclesState>({ status: 'pending' })
  const [curve, setCurve] = useState<CurveState>({
    status: 'pending',
    cycle: mapCycle,
    reachId,
  })

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    const pageDir = new URL('.', document.baseURI).href
    const { signal } = controller

    void loadCycles()

    return () => {
      active = false
      controller.abort()
    }

    async function loadCycles() {
      try {
        const result = await getJson(cyclesUrl(pageDir), signal)
        if (!active) return
        const entries = result.ok ? asCycleEntries(result.body) : null
        if (!entries) {
          setCycles({ status: 'error' })
          return
        }
        setCycles({ status: 'ready', options: cycleOptions(entries) })
      } catch (error) {
        if (!active || (error instanceof Error && error.name === 'AbortError')) return
        setCycles({ status: 'error' })
      }
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    const pageDir = new URL('.', document.baseURI).href
    const requestCycle = selectedCycle
    const requestReachId = reachId
    const { signal } = controller

    setCurve({ status: 'pending', cycle: requestCycle, reachId: requestReachId })
    void loadCurve()

    return () => {
      active = false
      controller.abort()
    }

    async function loadCurve() {
      try {
        const result = await getJson(curveUrl(pageDir, requestCycle, requestReachId), signal)
        if (!active) return
        if (result.status === 404) {
          setCurve({ status: 'absent', cycle: requestCycle, reachId: requestReachId })
          return
        }
        const data = result.ok ? asCurveResponse(result.body, requestCycle, requestReachId) : null
        if (!data) {
          setCurve({ status: 'error', cycle: requestCycle, reachId: requestReachId })
          return
        }
        setCurve({ status: 'ready', cycle: requestCycle, reachId: requestReachId, data })
      } catch (error) {
        if (!active || (error instanceof Error && error.name === 'AbortError')) return
        setCurve({ status: 'error', cycle: requestCycle, reachId: requestReachId })
      }
    }
  }, [reachId, selectedCycle])

  const catalogOptions = cycles.status === 'ready' ? cycles.options : null
  const options = selectorOptions(mapCycle, catalogOptions)
  const curveMatches = curve.cycle === selectedCycle && curve.reachId === reachId
  const chartData = curveMatches && curve.status === 'ready' ? curve.data : null

  let statusMessage: string | null = null
  if (!curveMatches || curve.status === 'pending') statusMessage = GENERIC_LOADING
  else if (curve.status === 'absent') statusMessage = GENERIC_NO_DATA
  else if (curve.status === 'error') statusMessage = GENERIC_LOAD_ERROR

  return (
    <M11DraggableCurveWindow
      header={
        <div className="flex items-center gap-2 px-3 py-2 text-slate-100">
          <span className="min-w-0 truncate text-sm font-medium text-slate-100">
            河段 {reachId}
          </span>
          <label className="ml-auto flex min-w-0 items-center gap-1.5 text-xs text-slate-300">
            <span className="shrink-0 text-xs text-slate-300">起报（北京时间）</span>
            <select
              className="max-w-44 truncate rounded-md border border-white/20 bg-slate-900 px-2 py-1 text-xs text-slate-100"
              value={selectedCycle}
              aria-label="起报（北京时间）"
              data-m11-window-no-drag=""
              onChange={(event) => setSelectedCycle(event.target.value)}
            >
              {options.map((option) => (
                <option
                  key={option.cycle}
                  value={option.cycle}
                  className="bg-slate-900 text-slate-100"
                >
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-sm text-slate-300 hover:bg-white/10 hover:text-slate-100"
            aria-label="关闭"
            data-m11-window-no-drag=""
            onClick={onClose}
          >
            ×
          </button>
        </div>
      }
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-3 pb-3">
        {cycles.status === 'error' ? (
          <div className="shrink-0 pb-2 text-sm text-slate-400" role="alert">
            {GENERIC_LOAD_ERROR}
          </div>
        ) : null}
        {statusMessage !== null ? (
          <div
            className="grid h-full min-h-0 flex-1 place-items-center text-sm text-slate-400"
            role={curveMatches && curve.status === 'pending' ? 'status' : 'alert'}
          >
            {statusMessage}
          </div>
        ) : (
          <div className="h-full min-h-0 w-full flex-1">
            <ForecastChart data={chartData} />
          </div>
        )}
      </div>
    </M11DraggableCurveWindow>
  )
}

function selectorOptions(mapCycle: string, catalog: CycleOption[] | null): CycleOption[] {
  const mapOption: CycleOption = { cycle: mapCycle, label: formatBeijingTime(mapCycle) }
  if (catalog === null) return [mapOption]
  if (catalog.some((option) => option.cycle === mapCycle)) return catalog
  return [mapOption, ...catalog]
}

function asCycleEntries(body: unknown): CycleEntry[] | null {
  if (!Array.isArray(body)) return null
  const entries: CycleEntry[] = []
  for (let i = 0; i < body.length; i += 1) {
    const item = body[i]
    if (item === null || typeof item !== 'object') return null
    const entry = item as CycleEntry
    if (typeof entry.cycle !== 'string' || !Array.isArray(entry.sources)) return null
    entries.push(entry)
  }
  return entries
}

function asCurveResponse(body: unknown, cycle: string, reachId: number): CurveResponse | null {
  if (body === null || typeof body !== 'object') return null
  const curve = body as CurveResponse
  if (curve.cycle !== cycle || curve.reach_id !== reachId) return null
  if (!Array.isArray(curve.lead_hours)) return null
  if (curve.series === null || typeof curve.series !== 'object') return null
  return curve
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
