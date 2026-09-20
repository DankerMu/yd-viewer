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
  value?: number | string | Array<number | string>
}

function tooltipValue(param: TooltipParam) {
  if (Array.isArray(param.value)) return Number(param.value[1])
  return Number(param.value)
}

function tooltipFormatter(params: TooltipParam | TooltipParam[]) {
  const items = Array.isArray(params) ? params : [params]
  const axisValue = items[0]?.axisValue
  const timeLabel = axisValue == null ? '' : String(axisValue)
  const lines = [`时间: ${timeLabel}`]
  items.forEach((param) => {
    const value = tooltipValue(param)
    if (!Number.isFinite(value)) return
    lines.push(`${param.marker ?? ''}${param.seriesName ?? 'series'}: ${value.toFixed(2)} m³/s`)
  })
  return lines.join('\n')
}

export function ForecastChart({ data }: ForecastChartProps) {
  const packed = useMemo(() => {
    if (!data) return null
    const series: Array<{ source: ForecastSource; values: number[] }> = []
    for (const source of SOURCE_ORDER) {
      const values = data.series[source]
      if (values && values.length > 0) series.push({ source, values })
    }
    return { cycle: data.cycle, leadHours: data.lead_hours, series }
  }, [data])

  const labels = useMemo(() => {
    if (!packed) return []
    return packed.leadHours.map((lead) => formatBeijingTime(packed.cycle, lead))
  }, [packed])

  const option = useMemo(() => {
    if (!packed) return null
    const axisColor = '#94a3b8'
    return {
      color: packed.series.map((entry) => SOURCE_COLOR[entry.source]),
      legend: { top: 0, left: 0, itemWidth: 18, itemHeight: 8, textStyle: { color: axisColor } },
      grid: { left: 52, right: 16, top: 28, bottom: 28 },
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
        nameGap: 32,
        scale: true,
        axisLabel: { color: axisColor },
        nameTextStyle: { color: axisColor },
        splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.14)' } },
      },
      series: packed.series.map((entry) => ({
        type: 'line',
        name: SOURCE_LABEL[entry.source],
        id: entry.source,
        smooth: true,
        showSymbol: false,
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
  }, [labels, packed])

  if (!packed || packed.series.length === 0 || option === null) {
    return (
      <div className="grid min-h-72 place-items-center rounded-lg border border-dashed border-white/15 p-4 text-center text-sm text-slate-400">
        暂无预报数据
      </div>
    )
  }

  return (
    <ReactEChartsCore
      echarts={echarts}
      option={option}
      notMerge
      lazyUpdate
      style={{ height: '100%', width: '100%', minHeight: 216 }}
    />
  )
}
