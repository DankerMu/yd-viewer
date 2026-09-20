import type { BasemapKey } from '../../lib/basemaps'

const GLASS_PANEL =
  'rounded-lg border border-white/40 bg-white/70 shadow-lg backdrop-blur-md supports-[backdrop-filter]:bg-white/55'

const BASEMAP_LABEL: Record<BasemapKey, string> = {
  vector: '矢量',
  satellite: '卫星',
  terrain: '地形',
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
        const selected = basemap === key
        return (
          <button
            key={key}
            type="button"
            className={
              selected
                ? 'flex h-8 cursor-pointer items-center gap-1.5 rounded-md bg-sky-700 px-2.5 text-xs font-medium text-white shadow-sm'
                : 'flex h-8 cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-neutral-700 hover:bg-white/70'
            }
            aria-pressed={selected}
            aria-label={`${BASEMAP_LABEL[key]}底图`}
            onClick={() => onChange?.(key)}
          >
            {BASEMAP_LABEL[key]}
          </button>
        )
      })}
    </div>
  )
}

