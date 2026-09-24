## Why
The user compared `https://nwm.ac.cn/yd/` with the NWM homepage (2026-09-24) and ruled that the yd viewer must look like NWM, frontend appearance only. The gap comes from yd's own trim of the M11 snapshot: NWM `SiteHeader`, `M11FloatingLayerSwitcher`, `M11FloatingBasemapSwitcher` icons, `M11FloatingLegend` chrome, the compass and the expanded attribution all existed at pin `4f8d98263` and are unchanged at `c9f363b38`. Docs first: `docs/design.md` §7 (PR #337). Issue #338.

## What Changes
- Theme: copy NWM's seven `--color-primary-*` tokens into `src/index.css` `@theme`; add `lucide-react` (NWM `^1.14.0`).
- Page layout: vertical flex — 84 px brand bar, then a `flex-1` map section (background `#d7e7ef`) that holds the map, all floating panels and the curve window.
- Brand bar (replaces the floating `Header`): NWM `SiteHeader` markup with NWM logo (resized 720→96 px) + 「永登流域水文模拟系统」 + 「Yongdeng Basin Hydrological Modeling」, sponsors strip (`lg`+).
- Top-left layer card: NWM switcher look, one always-selected non-button row 「流量 · q_down / m³/s」, footer line 「起报 <北京时间> 北京时间」 or 「暂无数据」.
- Basemap switcher: NWM icons + `primary-600` selected state; still only keys present in `basemaps.json`.
- Legend: title 「径流量图例」 with Layers icon, five labels suffixed ` m³/s`, final row 「无径流数据」 `#94ADC7`; `dischargeColor` unchanged.
- Map controls: compass with `visualizePitch`; attribution expanded; raster basemap sources carry `© 天地图`.
- `SNAPSHOT.md` registers the new sources and the logo resize.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `viewer-frontend`: 地图着色与色带 (legend chrome), 地图基础交互 (layout + controls), 底图切换 (icon look), 页头 (brand bar + layer card).

## Impact
`viewer/frontend/{package.json,pnpm-lock.yaml,SNAPSHOT.md}`, `src/index.css`, `src/App.tsx`, `src/components/Header.tsx` (+test), `src/components/map/{M11FloatingControls.tsx,overviewDataContracts.tsx,m11MapRuntime.tsx}`, `src/lib/{basemaps.ts,color.ts}`, `src/pages/MapPage.tsx`, new `src/assets/brand/{logo.png,sponsors.png}`. No backend, API, entrypoint or deploy change. node-27 redeploy is a separate authorized step.

Issue type: feature (UI alignment)
Fixture level: compact
Upstream suggested level: compact (agree)
Blast radius: the whole viewer page chrome; a layout mistake can hide the map, the curve window or the MapLibre attribution, or break the relative-path/no-key build gate.
Selected risk packs: Release / packaging / dependency compatibility (new `lucide-react`, lockfile, binary assets vs large-file guard); Legacy compatibility (basemap-only-present, color bins, curve window drag bounds, 暂无数据 empty state).
Evidence floor: `corepack pnpm --dir viewer/frontend install --frozen-lockfile`, `typecheck`, `test`, `build`; `uv run python scripts/large_file_guard.py`; dist grep gates; local 1440×900 screenshot side by side with NWM; `openspec validate viewer-nwm-chrome-align --strict --no-interactive`.
design.md omitted (compact fixture).
