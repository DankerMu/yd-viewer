# M11 snapshot provenance

Source commit: `4f8d982637f67956acb788b813006e12c1c93174`

Copied from NWM `apps/frontend` at that pin, then trimmed for yd-viewer. Files are independently maintained after this snapshot; later NWM changes are not tracked.

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

## Extra origin snippets

| Source (NWM pin) | Used as |
|---|---|
| `apps/frontend/src/components/map/M11PopupChrome.tsx` lines 16–17 | Inline `M11_POPUP_GLASS` class string in `M11DraggableCurveWindow.tsx`. File itself is not copied (it imports unrelated popup/network UI). |
| `apps/frontend/src/components/map/M11FloatingControls.tsx` `GLASS_PANEL` | Same glass panel class on basemap switcher and discharge legend. |

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

Kept: ECharts line chart; required `data: CurveResponse | null`; all supplied values (168 when present, including JSON `null` gaps); shared axes; GFS/IFS identifiable (solid cyan / dashed green); Beijing x labels via `lib/time`; tooltip uses the category axis label as-is (no second UTC conversion) and does not coerce `null` to `0`; `connectNulls: false` so missing points leave a gap instead of bridging or zero-fill; fixed `m³/s`; `notMerge` so a dual-source option replaced by one source drops the old series.

- store: removed (`@/stores/forecast`)
- routing: absent
- generated client: absent
- station UI: absent
- precipitation: absent
- authorization: absent

Also removed: IFS 144h truncation/marker, point budget/degraded, analysis divider, light/dark themes, extra chart kinds.
Removed unused adapter: optional `{cycle, leadHours, series}` props and `ForecastChartSeries`.

### `echartsCore.ts`

Kept: tree-shaken ECharts registrations actually used by `ForecastChart` (LineChart, Grid, Legend, Tooltip, CanvasRenderer).

- store: absent
- routing: absent
- generated client: absent
- station UI: absent
- precipitation: absent
- authorization: absent

Removed unused Bar/Pie/DataZoom/MarkLine/Title registrations.

### `m11MapRuntime.tsx`

Kept: native MapLibre 4.7 map create (`createM11Map` / `useM11Map`); NavigationControl + ScaleControl; **fixed initial fit only** from `lib/bbox` bounds via `M11MapCameraFit`; `setM11MapStyle` from `lib/basemaps` without refitting camera (full rebuild `map.setStyle(style, {diff:false})` so promised `style.load` always fires); `onStyleReady` so consumers can re-register overlays after style rebuild (initialized-style via public `getStyle()`, not tile completion: subscribe `style.load` and invoke immediately if `getStyle()` is defined).

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

### `M11FloatingControls.tsx`

Kept: `M11FloatingBasemapSwitcher` — right-upper glass basemap segmented control; controlled `choices`/`basemap`/`onChange` from `lib/basemaps` keys; empty `choices` renders nothing.

- store: removed (`M11QueryPatch`)
- routing: removed (`react-router-dom` ops link)
- generated client: removed (`components['schemas']`)
- station UI: removed (met-station toggle)
- precipitation: removed (precip switcher + precip legend)
- authorization: removed (`M11OpsLink`)

Layer switcher, legend, ops link, and notice cards were not copied into this file (discharge legend lives in `overviewDataContracts.tsx`).
Removed unused alias: `M11FloatingControls` (same props, only forwarded to the switcher).

### `overviewDataContracts.tsx`

Kept: `M11DischargeLegend` using `DISCHARGE_LEGEND` from `lib/color` (five bands + `m³/s` title); glass panel; swatch + label rows. Not an alias-only wrapper.

- store: removed (layer catalog / freshness / aggregation)
- routing: absent
- generated client: removed (`components['schemas']` basin/run/layer types)
- station UI: absent
- precipitation: removed
- authorization: absent

Removed six-band NWM legend (`1000–10000` / `>10000`), geometry budgets, and OpenAPI aliases. Color thresholds are not duplicated here; `lib/color.ts` is the single rule.
Removed unused exports: `DischargeLegendEntry`, `GLASS_PANEL` (class remains file-private).

## Downstream APIs for #259 / #260

#259 map page:

- `useM11Map` (`M11MapCameraFit` for initial fit), `setM11MapStyle`, `onStyleReady`
- `registerRiverOverlay`, `registerBoundaryOverlay` and their unregister helpers; `applyReachColors` after registration, then `setRiverHover` / `setRiverSelected` (owned by `MapPage`)
- `attachRiverInteractions`
- `M11FloatingBasemapSwitcher`
- `M11DischargeLegend`

#260 curve window:

- `M11DraggableCurveWindow` (`header`/`children`)
- `ForecastChart({ data })` with required `data: CurveResponse | null`
