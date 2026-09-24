import { useMemo } from 'react'
import ReactEChartsCore from 'echarts-for-react/lib/core'

import type { CurveResponse, ForecastSource } from '../../lib/api'
import { formatBeijingTime } from '../../lib/time'
import { echarts } from './echartsCore'

const SOURCE_COLOR: Record<ForecastSource, string> = {
  gfs: '#22d3ee',
  ifs: '#34d399',
}

const SOURCE_LABEL: Record<ForecastSource, string> = {
  gfs: 'GFS',
  ifs: 'IFS',
}

const SOURCE_LINE: Record<ForecastSource, 'solid' | 'dashed'> = {
  gfs: 'solid',
  ifs: 'dashed',
}

const SOURCE_ORDER: ForecastSource[] = ['gfs', 'ifs']

export interface ForecastChartProps {
  data: CurveResponse | null
}

function hexToRgba(hex: string, alpha: number): string {
  const match = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim())
  if (!match) return `rgba(34, 211, 238, ${alpha})`
  let digits = match[1]
  if (digits.length === 3) digits = digits.split('').map((c) => c + c).join('')
  const value = Number.parseInt(digits, 16)
  return `rgba(${(value >> 16) & 0xff}, ${(value >> 8) & 0xff}, ${value & 0xff}, ${alpha})`
}

interface TooltipParam {
  axisValue?: string | number
  marker?: string
  seriesName?: string
  value?: number | string | null | Array<number | string | null>
}

function tooltipValue(param: TooltipParam): number | null {
  const raw = Array.isArray(param.value) ? param.value[1] : param.value
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return null
  return raw
}

function tooltipFormatter(params: TooltipParam | TooltipParam[]) {
  const items = Array.isArray(params) ? params : [params]
  const axisValue = items[0]?.axisValue
  const timeLabel = axisValue == null ? '' : String(axisValue)
  const lines = [`时间: ${timeLabel}`]
  items.forEach((param) => {
    const value = tooltipValue(param)
    if (value === null) return
    lines.push(`${param.marker ?? ''}${param.seriesName ?? 'series'}: ${value.toFixed(2)} m³/s`)
  })
  return lines.join('\n')
}

export function buildForecastOption(data: CurveResponse) {
  const packed: Array<{ source: ForecastSource; values: Array<number | null> }> = []
  for (const source of SOURCE_ORDER) {
    const values = data.series[source]
    if (values && values.length > 0) packed.push({ source, values })
  }
  const labels = data.lead_hours.map((lead) => formatBeijingTime(data.cycle, lead))
  const axisColor = '#94a3b8'
  return {
    color: packed.map((entry) => SOURCE_COLOR[entry.source]),
    grid: { left: 48, right: 16, top: 28, bottom: 28 },
    // Wheel zooms the time axis around the cursor; filterMode none keeps curves continuous.
    dataZoom: [
      {
        type: 'inside',
        zoomOnMouseWheel: true,
        moveOnMouseMove: false,
        moveOnMouseWheel: false,
        filterMode: 'none',
      },
    ],
    tooltip: {
      trigger: 'axis',
      renderMode: 'richText',
      formatter: tooltipFormatter,
      backgroundColor: 'rgba(8, 14, 32, 0.92)',
      borderColor: 'rgba(34, 211, 238, 0.35)',
      textStyle: { color: '#e2e8f0' },
    },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { color: axisColor, fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.25)' } },
    },
    yAxis: {
      type: 'value',
      name: '流量 (m³/s)',
      nameGap: 12,
      scale: true,
      axisLabel: { color: axisColor },
      nameTextStyle: { color: axisColor },
      splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.14)' } },
    },
    series: packed.map((entry) => ({
      type: 'line',
      name: SOURCE_LABEL[entry.source],
      id: entry.source,
      smooth: true,
      showSymbol: false,
      connectNulls: false,
      data: entry.values,
      lineStyle: {
        width: 2.5,
        color: SOURCE_COLOR[entry.source],
        type: SOURCE_LINE[entry.source],
        shadowBlur: 12,
        shadowColor: hexToRgba(SOURCE_COLOR[entry.source], 0.45),
        shadowOffsetY: 3,
      },
      itemStyle: { color: SOURCE_COLOR[entry.source] },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: hexToRgba(SOURCE_COLOR[entry.source], 0.28) },
            { offset: 1, color: hexToRgba(SOURCE_COLOR[entry.source], 0.02) },
          ],
        },
      },
    })),
  }
}

export function ForecastChart({ data }: ForecastChartProps) {
  const option = useMemo(() => (data ? buildForecastOption(data) : null), [data])

  if (option === null || option.series.length === 0) {
    return (
      <div className="grid h-full min-h-0 place-items-center rounded-lg border border-dashed border-white/15 p-4 text-center text-sm text-slate-400">
        暂无预报数据
      </div>
    )
  }

  return (
    <div className="h-full min-h-0 w-full">
      <ReactEChartsCore
        echarts={echarts}
        option={option}
        notMerge
        lazyUpdate
        style={{ height: '100%', width: '100%', minHeight: 0 }}
      />
    </div>
  )
}
