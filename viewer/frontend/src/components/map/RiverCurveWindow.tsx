import { useEffect, useState } from 'react'
import { Waves, X } from 'lucide-react'

import {
  curveUrl,
  cyclesUrl,
  type CurveResponse,
  type CycleEntry,
  type ForecastSource,
} from '../../lib/api'
import { cycleOptions, type CycleOption } from '../../lib/cycles'
import { formatBeijingTime } from '../../lib/time'
import { ForecastChart } from '../charts/ForecastChart'
import { M11DraggableCurveWindow } from './M11DraggableCurveWindow'

const GENERIC_LOAD_ERROR = '加载失败'
const GENERIC_NO_DATA = '暂无数据'
const GENERIC_LOADING = '加载中'
const SUBTITLE_BASE = '河段 q_down 流量预报'

const SOURCE_ORDER: ForecastSource[] = ['gfs', 'ifs']
const SOURCE_LABEL: Record<ForecastSource, string> = { gfs: 'GFS', ifs: 'IFS' }
const SOURCE_COLOR: Record<ForecastSource, string> = { gfs: '#22d3ee', ifs: '#34d399' }

const ISSUE_TIME_SELECT =
  'h-7 min-w-0 max-w-[12rem] cursor-pointer rounded-md border border-white/15 bg-white/10 px-2 py-0 font-mono text-[11px] text-slate-100 shadow-none [color-scheme:dark] hover:border-cyan-400/50 focus:border-cyan-400 focus:outline-none focus:ring-2 focus:ring-cyan-400 disabled:cursor-not-allowed disabled:opacity-50'

/** Sources whose curve array is non-empty, in fixed gfs, ifs order. */
export function availableSources(series: CurveResponse['series']): ForecastSource[] {
  return SOURCE_ORDER.filter((source) => (series[source]?.length ?? 0) > 0)
}

export function sourceSubtitle(sources: ForecastSource[]): string {
  if (sources.length === 0) return SUBTITLE_BASE
  return `${SUBTITLE_BASE} · ${sources.map((source) => SOURCE_LABEL[source]).join('+')}`
}

export function SourceChips({ sources }: { sources: ForecastSource[] }) {
  return (
    <div className="flex shrink-0 items-center gap-3 px-1 pb-1.5">
      {SOURCE_ORDER.map((source) => (
        <span
          key={source}
          className={`inline-flex items-center gap-1.5 text-[11px] ${
            sources.includes(source) ? 'text-slate-200' : 'text-slate-500 line-through'
          }`}
        >
          <span
            className="h-2 w-3.5 rounded-sm"
            style={{ backgroundColor: SOURCE_COLOR[source] }}
            aria-hidden="true"
          />
          {SOURCE_LABEL[source]}
        </span>
      ))}
      <span className="ml-auto text-[10px] text-slate-500">滚轮缩放时间轴</span>
    </div>
  )
}

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

  const sources = chartData ? availableSources(chartData.series) : []

  return (
    <M11DraggableCurveWindow
      header={
        <header className="flex shrink-0 items-start justify-between gap-2.5 border-b border-white/10 px-4 py-3">
          <div className="flex min-w-0 items-start gap-2.5">
            <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-cyan-400/10 text-cyan-300 ring-1 ring-inset ring-cyan-400/30">
              <Waves className="h-4 w-4" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold leading-tight text-slate-50">
                河段 {reachId}
              </div>
              <div className="mt-0.5 text-[11px] uppercase tracking-[0.14em] text-cyan-300/80">
                {sourceSubtitle(sources)}
              </div>
            </div>
          </div>
          <button
            type="button"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-white/10 hover:text-slate-100"
            aria-label="关闭面板"
            data-m11-window-no-drag=""
            onClick={onClose}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>
      }
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-white/10 px-4 py-2 text-[11px] text-slate-400">
        <span className="shrink-0 uppercase tracking-wide">起报</span>
        <select
          className={ISSUE_TIME_SELECT}
          value={selectedCycle}
          aria-label="起报时间选择"
          data-m11-window-no-drag=""
          onChange={(event) => setSelectedCycle(event.target.value)}
        >
          {options.map((option) => (
            <option key={option.cycle} value={option.cycle} className="bg-slate-900 text-slate-100">
              {option.label}
            </option>
          ))}
        </select>
        <span className="ml-auto text-[10px] text-slate-500">GFS + IFS 同步切换</span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col px-3 pb-2 pt-2.5">
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
          <>
            <SourceChips sources={sources} />
            <div className="h-full min-h-0 w-full flex-1">
              <ForecastChart data={chartData} />
            </div>
          </>
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
