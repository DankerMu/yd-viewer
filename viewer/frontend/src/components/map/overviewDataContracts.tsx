import { DISCHARGE_LEGEND } from '../../lib/color'

const GLASS_PANEL =
  'rounded-lg border border-white/40 bg-white/70 shadow-lg backdrop-blur-md supports-[backdrop-filter]:bg-white/55'

export function M11DischargeLegend({
  title = '流量 (m³/s)',
}: {
  title?: string
}) {
  const entries = DISCHARGE_LEGEND
  return (
    <section
      className={`absolute bottom-8 right-4 z-[120] w-max max-w-56 p-3 ${GLASS_PANEL}`}
      aria-label="地图图例"
    >
      <div className="pb-2 text-xs font-semibold text-neutral-900">{title}</div>
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
