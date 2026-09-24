import { Droplets, Layers, Map as MapIcon, Mountain, Satellite, type LucideIcon } from 'lucide-react'

import type { BasemapKey } from '../../lib/basemaps'
import { formatBeijingTime } from '../../lib/time'

const GLASS_PANEL =
  'rounded-lg border border-white/40 bg-white/70 shadow-lg backdrop-blur-md supports-[backdrop-filter]:bg-white/55'

const BASEMAP_OPTION: Record<BasemapKey, { label: string; icon: LucideIcon }> = {
  vector: { label: '矢量', icon: MapIcon },
  satellite: { label: '卫星', icon: Satellite },
  terrain: { label: '地形', icon: Mountain },
}

/** Left-upper layer card: the single, always-selected discharge layer plus the issue time. */
export function M11FloatingLayerCard({ cycle }: { cycle: string | null }) {
  return (
    <section
      className={`absolute left-4 top-4 z-[120] w-max max-w-52 p-2 ${GLASS_PANEL}`}
      aria-label="地图图层"
    >
      <div className="flex items-center gap-2 px-1 pb-2 text-xs font-semibold text-neutral-900">
        <Layers className="h-4 w-4 text-primary-600" aria-hidden="true" />
        水文
      </div>
      <div
        className="flex w-full items-center gap-2 rounded-md border px-2 py-2 text-left border-primary-600 bg-primary-600/15 text-primary-700"
        aria-current="true"
      >
        <Droplets className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="min-w-0">
          <span className="block text-sm font-medium leading-tight">流量</span>
          <span className="block truncate text-xs text-neutral-600">q_down / m³/s</span>
        </span>
      </div>
      <div className="mt-2 border-t border-white/50 px-1 pt-2 text-xs text-neutral-700">
        {cycle === null ? '暂无数据' : `起报 ${formatBeijingTime(cycle)} 北京时间`}
      </div>
    </section>
  )
}

export function M11FloatingBasemapSwitcher({
  choices,
  basemap,
  onChange,
}: {
  choices: BasemapKey[]
  basemap: BasemapKey | null
  onChange?: (key: BasemapKey) => void
}) {
  if (choices.length === 0) return null
  return (
    <div
      className={`absolute right-16 top-4 z-[120] flex items-center gap-0.5 p-1 ${GLASS_PANEL}`}
      role="group"
      aria-label="底图切换"
    >
      {choices.map((key) => {
        const { label, icon: Icon } = BASEMAP_OPTION[key]
        const selected = basemap === key
        return (
          <button
            key={key}
            type="button"
            className={`flex h-8 cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors ${
              selected ? 'bg-primary-600 text-white shadow-sm' : 'text-neutral-700 hover:bg-white/70'
            }`}
            aria-pressed={selected}
            aria-label={`${label}底图`}
            onClick={() => onChange?.(key)}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
            {label}
          </button>
        )
      })}
    </div>
  )
}
