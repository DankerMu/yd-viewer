# M11 snapshot provenance

Source commit: `4f8d982637f67956acb788b813006e12c1c93174`

Copied from NWM `apps/frontend` at that pin, then trimmed for yd-viewer. Files are independently maintained after this snapshot; later NWM changes are not tracked.

2026-09-24 NWM look alignment (issue #338, design §7): the additional sources below (`SiteHeader.tsx`, `M11FloatingControls.tsx` layer switcher/legend/basemap icons, `index.css` primary tokens, `assets/brand/*`) are unchanged between `4f8d98263` and `c9f363b38` (checked with `git show c9f363b38:<path>`), so the source pin stays `4f8d98263`.

2026-09-24 curve window alignment (issue #339, design §7): `M11RiverForecastPanel.tsx`, `M11PopupChrome.tsx`, `ForecastChart.tsx` and `echartsCore.ts` are likewise unchanged between `4f8d98263` and `c9f363b38`; the pin stays `4f8d98263`.

## Source → target

| Source (NWM pin) | Target |
|---|---|
| `apps/frontend/src/components/map/M11DraggableCurveWindow.tsx` | `src/components/map/M11DraggableCurveWindow.tsx` |
| `apps/frontend/src/components/charts/ForecastChart.tsx` | `src/components/charts/ForecastChart.tsx` |
| `apps/frontend/src/components/charts/echartsCore.ts` | `src/components/charts/echartsCore.ts` |
| `apps/frontend/src/components/map/m11MapRuntime.tsx` | `src/components/map/m11MapRuntime.tsx` |
| `apps/frontend/src/components/map/m11MapBuilders.ts` | `src/components/map/m11MapBuilders.ts` |
| `apps/frontend/src/components/map/m11MapInteractions.ts` | `src/components/map/m11MapInteractions.ts` |
| `apps/frontend/src/components/map/m11MapPrimitives.tsx` | `src/components/map/m11MapPrimitives.tsx` |
| `apps/frontend/src/components/map/M11FloatingControls.tsx` | `src/components/map/M11FloatingControls.tsx` |
| `apps/frontend/src/lib/m11/overviewDataContracts.ts` | `src/components/map/overviewDataContracts.tsx` |
| `apps/frontend/src/components/layout/SiteHeader.tsx` | `src/components/Header.tsx` |
| `apps/frontend/src/assets/brand/logo.png` (720×720, 389598 bytes) | `src/assets/brand/logo.png` — resized to 96×96 with `sips -Z 96` (15847 bytes) so the binary stays under the large-file guard line count (original counts 1602 LF bytes) |
| `apps/frontend/src/assets/brand/sponsors.png` (1013×170, 133287 bytes) | `src/assets/brand/sponsors.png` — original bytes (git blob `12d3c43`) |

## Extra origin snippets

| Source (NWM pin) | Used as |
|---|---|
| `apps/frontend/src/components/map/M11PopupChrome.tsx` lines 16–17 | Inline `M11_POPUP_GLASS` class string in `M11DraggableCurveWindow.tsx`. File itself is not copied (it imports unrelated popup/network UI). |
| `apps/frontend/src/components/map/M11FloatingControls.tsx` `GLASS_PANEL` | Same glass panel class on layer card, basemap switcher and discharge legend. |
| `apps/frontend/src/components/map/M11FloatingControls.tsx` `M11FloatingLayerSwitcher`, `LayerGroupTitle`, `layerRowClassName` (selected branch) | `M11FloatingLayerCard` in `src/components/map/M11FloatingControls.tsx`. |
| `apps/frontend/src/components/map/M11FloatingControls.tsx` `m11FloatingBasemapOptions` icons and selected/unselected button classes | `M11FloatingBasemapSwitcher` in `src/components/map/M11FloatingControls.tsx`. |
| `apps/frontend/src/components/map/M11FloatingControls.tsx` `M11FloatingLegend` title row and entry rows | `M11DischargeLegend` in `src/components/map/overviewDataContracts.tsx`. |
| `apps/frontend/src/index.css` `@theme` `--color-primary-{900,800,700,600,500,100,50}` | `src/index.css` `@theme` (only these seven tokens; the rest of NWM's theme and `:root` variables are not copied). |
| `apps/frontend/package.json` `lucide-react` `^1.14.0` | Same range in `package.json` (lockfile resolves 1.48.0). |
| `apps/frontend/src/components/map/M11RiverForecastPanel.tsx` lines 226–251 (header) | `RiverCurveWindow.tsx` header: `Waves` icon tile, title 「河段 <reach_id>」, subtitle from `sourceSubtitle` (actual sources instead of the fixed 「· GFS+IFS」), `X` close button 「关闭面板」 (plus `data-m11-window-no-drag`). The `displayName.meta` line is not copied (it would repeat the reach id). |
| `apps/frontend/src/components/map/M11RiverForecastPanel.tsx` lines 262–272 (cycle bar) | `RiverCurveWindow.tsx` 「起报」 row with trailing 「GFS + IFS 同步切换」; the control is yd's existing native `<select>`. |
| `apps/frontend/src/components/map/M11PopupChrome.tsx` `M11IssueTimeSelect` `SelectTrigger` classes (lines 140–141) | Class string on the native `<select>` (NWM `ui/select` base tokens such as `rounded-[var(--radius-md)]`/`focus:ring-primary-500` translated to `rounded-md`/`focus:ring-cyan-400`). Radix `Select`/`SelectContent`/`SelectItem` are not copied. |
| `apps/frontend/src/components/map/M11RiverForecastPanel.tsx` lines 280–293 (chip row + body) | `SourceChips` in `RiverCurveWindow.tsx` (swatch colors, present/absent classes, trailing 「滚轮缩放时间轴」) and the body container classes. A source is absent when `series` lacks a non-empty array for it. |

## Per-file kept behavior and six prohibited categories

### `M11DraggableCurveWindow.tsx`

Kept: river-only draggable shell; `header`/`children` props; Tailwind glass chrome; pointer-id identity; ignore interactive controls (`button`/`input`/`select` and `[data-m11-window-no-drag]`); clamp inside parent; stop/cancel **and** unmount listener cleanup.

- store: removed/absent (`kind`/`active` dual-window state and `onActivate` dropped)
- routing: absent
- generated client: absent
- station UI: removed (`kind === 'station'` placement and dual-window z-index)
- precipitation: absent
- authorization: absent

### `ForecastChart.tsx`

Kept: ECharts line chart; required `data: CurveResponse | null`; `compact` look (no ECharts legend — the curve window chip row replaces it; grid `left: 48, right: 16, bottom: 28`; y-axis name at the axis end) and the `zoomable` branch verbatim (`dataZoom: [{ type: 'inside', zoomOnMouseWheel: true, moveOnMouseMove: false, moveOnMouseWheel: false, filterMode: 'none' }]`), both applied unconditionally (no `variant`/`zoomable` props); the option is built by exported `buildForecastOption(data)`; all supplied values (168 when present, including JSON `null` gaps); shared axes; GFS/IFS identifiable (solid cyan / dashed green); Beijing x labels via `lib/time`; tooltip uses the category axis label as-is (no second UTC conversion) and does not coerce `null` to `0`; `connectNulls: false` so missing points leave a gap instead of bridging or zero-fill; fixed `m³/s`; `notMerge` so a dual-source option replaced by one source drops the old series.

- store: removed (`@/stores/forecast`)
- routing: absent
- generated client: absent
- station UI: absent
- precipitation: absent
- authorization: absent

Also removed: IFS 144h truncation/marker, point budget/degraded, analysis divider, light/dark themes, extra chart kinds.
Removed unused adapter: optional `{cycle, leadHours, series}` props and `ForecastChartSeries`.
Deliberate spacing difference: NWM compact uses `grid.top: 16` with `nameGap: 32`, which clips the y-axis name in the curve window; yd uses `grid.top: 28` and `nameGap: 12`.

### `echartsCore.ts`

Kept: tree-shaken ECharts registrations actually used by `ForecastChart` (LineChart, DataZoom, Grid, Tooltip, CanvasRenderer).

- store: absent
- routing: absent
- generated client: absent
- station UI: absent
- precipitation: absent
- authorization: absent

Removed unused Bar/Pie/Legend/MarkLine/Title registrations (DataZoom added and Legend dropped in #339).

### `m11MapRuntime.tsx`

Kept: native MapLibre 4.7 map create (`createM11Map` / `useM11Map`); NavigationControl with compass (`visualizePitch: true`) + ScaleControl; expanded attribution (`compact: false`; raster sources from `lib/basemaps` carry `© 天地图`); **fixed initial fit only** from `lib/bbox` bounds via `M11MapCameraFit`; `setM11MapStyle` from `lib/basemaps` without refitting camera (full rebuild `map.setStyle(style, {diff:false})` so promised `style.load` always fires); `onStyleReady` so consumers can re-register overlays after style rebuild (initialized-style via public `getStyle()`, not tile completion: subscribe `style.load` and invoke immediately if `getStyle()` is defined).

- store: removed
- routing: absent
- generated client: absent
- station UI: absent
- precipitation: absent
- authorization: absent

Credential-bearing upstream line 50 and `tiandituTiles`/`tiandituStyle` (lines 209+) were not copied. Fly-to and remembered camera were removed.
Removed unused aliases/wrappers: `M11Map` type re-export, `maplibregl` default re-export, `bboxToMapFit`.

### `m11MapBuilders.ts`

Kept: GeoJSON source/layer specs for rivers and boundary (`riverSourceSpec`/`boundarySourceSpec`/`riverLayerSpecs`/`boundaryLayerSpecs`); integer `reach_id` identity (`promoteId` + filter); hover/selected/hit-area layer ids; casing + main line paint.

- store: removed (`overviewData` / queryState)
- routing: absent
- generated client: removed (`@/api/types`, MVT metadata)
- station UI: absent
- precipitation: absent
- authorization: absent

Removed MVT tile URL builders, national overlay, budget geometry, `feature_id`/`segment_id` string filters, six-band paint.
Removed unused wrappers: `M11RegisteredOverlay`, `buildRiverOverlay`, `m11RegisteredOverlayHitLayerId`.

### `m11MapInteractions.ts`

Kept: native MapLibre hover/click on the river hit layer; integer `reach_id` extraction; cursor reset; listener disposal (listeners are always removed even after owner.remove; canvas cursor reset is safe because the canvas object remains). `queryReachFeature` returns null if public `getStyle()` is undefined after owner.remove / before style initialization, or if the hit layer is missing during style replacement (presence check only; no catch around `queryRenderedFeatures`).

- store: absent
- routing: absent
- generated client: absent
- station UI: removed (cluster expand / station click / flyTo)
- precipitation: absent
- authorization: absent

### `m11MapPrimitives.tsx`

Kept: native `addSource`/`addLayer`; river casing/main/hit/hover/selected layers, boundary fill/outline, feature-state colors via `lib/color`, and safe unregister helpers using public `getStyle()`. Task 5.4's `MapPage` is the single overlay lifecycle owner: shared `onStyleReady` registers boundary then rivers, replays colors, and restores current hover/selection. Highlight-only changes do not recreate sources. The unused `M11OverlayPrimitive`/`M11BoundaryPrimitive` React wrappers were removed when the page chose that ordered registration path; native helper behavior and provenance remain.

- store: absent
- routing: absent
- generated client: absent
- station UI: removed (cluster primitive, station filters)
- precipitation: removed (`M11PrecipOverlayPrimitive`)
- authorization: absent

Removed national-river MVT, basin labels, `feature_id` promoteId.

### `Header.tsx` (from `SiteHeader.tsx`)

Kept: 84 px `<header>` with `primary-900→800→700` gradient; round logo, title, uppercase subtitle, sponsors strip (`lg`+). Text changed to 「永登流域水文模拟系统」 / 「Yongdeng Basin Hydrological Modeling」, logo alt to 「永登流域水文模拟系统徽标」. No props, no issue time or status. Imports use relative paths (no `@/` alias).

- store: absent
- routing: absent
- generated client: absent
- station UI: absent
- precipitation: absent
- authorization: absent

### `M11FloatingControls.tsx`

Kept: `M11FloatingBasemapSwitcher` — right-upper glass basemap segmented control with NWM `Map`/`Satellite`/`Mountain` icons and `bg-primary-600` selected state; controlled `choices`/`basemap`/`onChange` from `lib/basemaps` keys (only configured keys); empty `choices` renders nothing. `M11FloatingLayerCard({ cycle })` — NWM `M11FloatingLayerSwitcher` shell with only the 「水文」 group and the single discharge row, rendered as a non-button `div` (`aria-current="true"`) in the selected row classes; footer line 「起报 <北京时间> 北京时间」 or 「暂无数据」.

Also removed: 「气象」 group (precip toggle, met-station toggle) and their disabled placeholders; `precipAvailability`; `m11FloatingLayerOptions`; `cn`/tailwind-merge helper (template strings instead); `M11MapInfoCard`, `M11FloatingNotice`, `resolveM11FloatingLegend`.

- store: removed (`M11QueryPatch`)
- routing: removed (`react-router-dom` ops link)
- generated client: removed (`components['schemas']`)
- station UI: removed (met-station toggle)
- precipitation: removed (precip switcher + precip legend)
- authorization: removed (`M11OpsLink`)

Legend, ops link, and notice cards were not copied into this file (discharge legend lives in `overviewDataContracts.tsx`).
Removed unused alias: `M11FloatingControls` (same props, only forwarded to the switcher).

### `overviewDataContracts.tsx`

Kept: `M11DischargeLegend` in NWM `M11FloatingLegend` chrome — `Layers` icon + 「径流量图例」 title, `DISCHARGE_LEGEND` five bands (labels carry ` m³/s`) then `MISSING_LEGEND` 「无径流数据」 `#94ADC7` from `lib/color`; glass panel; swatch + label rows. Positioned `bottom-10 right-4` (NWM's `bottom-[7.5rem]` only clears its bottom control bar, which yd does not have; `bottom-10` still clears the attribution strip). No `title` prop. Not an alias-only wrapper.

- store: removed (layer catalog / freshness / aggregation)
- routing: absent
- generated client: removed (`components['schemas']` basin/run/layer types)
- station UI: absent
- precipitation: removed
- authorization: absent

Removed six-band NWM legend (`1000–10000` / `>10000`), precip legend section, layer-catalog legend lookup, geometry budgets, and OpenAPI aliases. Color thresholds are not duplicated here; `lib/color.ts` is the single rule.
Removed unused exports: `DischargeLegendEntry`, `GLASS_PANEL` (class remains file-private).

### `RiverCurveWindow.tsx` (panel snippets from `M11RiverForecastPanel.tsx`)

Kept: header, cycle bar and chip row chrome listed in "Extra origin snippets"; yd's own fetch/state machine (`/api/cycles`, `/api/cycles/<cycle>/reaches/<id>`, 加载中/暂无数据/加载失败), Beijing-time option labels.

Removed: hydroMet loading chain (`loadHydroMetRiverForecast`, product bootstrap, per-source validation), `formatIssueTime` UTC labels, partial-failure reasons list, dual-window `active`/`onActivate`, delayed loading copy, segment display-name meta.

- store: removed (`@/stores/forecast`)
- routing: absent
- generated client: removed (hydroMet product/segment identity types)
- station UI: absent
- precipitation: absent
- authorization: absent

## Downstream APIs for #259 / #260

#259 map page:

- `useM11Map` (`M11MapCameraFit` for initial fit), `setM11MapStyle`, `onStyleReady`
- `registerRiverOverlay`, `registerBoundaryOverlay` and their unregister helpers; `applyReachColors` after registration, then `setRiverHover` / `setRiverSelected` (owned by `MapPage`)
- `attachRiverInteractions`
- `M11FloatingBasemapSwitcher`
- `M11FloatingLayerCard`
- `M11DischargeLegend`

#260 curve window:

- `M11DraggableCurveWindow` (`header`/`children`)
- `ForecastChart({ data })` with required `data: CurveResponse | null`
