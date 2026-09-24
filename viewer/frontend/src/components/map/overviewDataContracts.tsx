import { Layers } from 'lucide-react'

import { DISCHARGE_LEGEND, MISSING_LEGEND } from '../../lib/color'

const GLASS_PANEL =
  'rounded-lg border border-white/40 bg-white/70 shadow-lg backdrop-blur-md supports-[backdrop-filter]:bg-white/55'

export function M11DischargeLegend() {
  const entries = [...DISCHARGE_LEGEND, MISSING_LEGEND]
  return (
    <section
      className={`absolute bottom-10 right-4 z-[120] w-max max-w-56 p-3 ${GLASS_PANEL}`}
      aria-label="地图图例"
    >
      <div className="flex items-center gap-2 pb-2 text-xs font-semibold text-neutral-900">
        <Layers className="h-4 w-4 text-primary-600" aria-hidden="true" />
        径流量图例
      </div>
      <div className="space-y-1">
        {entries.map((entry) => (
          <div key={entry.label} className="flex items-center gap-2 text-xs text-neutral-700">
            <span className="h-3 w-7 shrink-0 rounded-sm" style={{ backgroundColor: entry.color }} aria-hidden="true" />
            <span className="min-w-0 truncate">{entry.label}</span>
          </div>
        ))}
      </div>
    </section>
  )
}
