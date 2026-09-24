## 1. Theme, dependency, assets
- [x] 1.1 `src/index.css`: after `@import "tailwindcss";` add `@theme { --color-primary-900:#0a1929; --color-primary-800:#0d2744; --color-primary-700:#0f3460; --color-primary-600:#1565c0; --color-primary-500:#1e88e5; --color-primary-100:#e3f2fd; --color-primary-50:#f5f9ff; }` (values from NWM `apps/frontend/src/index.css` at `c9f363b38`); nothing else from NWM's theme.
- [x] 1.2 `corepack pnpm --dir viewer/frontend add lucide-react@^1.14.0`; lockfile updated; `install --frozen-lockfile` passes.
- [x] 1.3 `src/assets/brand/logo.png` = NWM `apps/frontend/src/assets/brand/logo.png` resized to 96×96 (`sips -Z 96`); `src/assets/brand/sponsors.png` = NWM original bytes. Obtain via `git -C ../NWM show c9f363b38:<path>` (read-only on the NWM repo). Both pass `scripts/large_file_guard.py` with no new exclusion. Add `src/vite-env.d.ts` png typing only if Vite's client types do not already cover it.

## 2. Layout and brand bar
- [x] 2.1 `src/components/Header.tsx` becomes the NWM `SiteHeader` port: `<header>` `flex h-[84px] shrink-0 items-center justify-between gap-4 bg-gradient-to-r from-primary-900 via-primary-800 to-primary-700 px-5 shadow-md`; left: logo `h-12 w-12 rounded-full` (alt 「永登流域水文模拟系统徽标」), title `text-[28px] font-extrabold tracking-wide text-white` 「永登流域水文模拟系统」, subtitle `text-[11px] uppercase tracking-[0.25em] text-primary-100/80` 「Yongdeng Basin Hydrological Modeling」; right: sponsors `hidden h-14 object-contain lg:block` alt 「合作单位」. No props; no 起报/流量 text in the header.
- [x] 2.2 `src/App.tsx`: root `flex h-screen w-screen flex-col overflow-hidden`; `<Header />` then a `relative min-h-0 flex-1 overflow-hidden bg-[#d7e7ef]` section containing `MapPage` and `RiverCurveWindow`. `MapPage` root changes from `h-screen w-screen` to `h-full w-full` so the map fills the section; MapLibre must resize to the section (it already observes its container). The curve window's clamp uses its parent, so it stays inside the map section.
- [x] 2.3 `index.html` `<title>` unchanged (「永登流域水文模拟系统」).

## 3. Floating panels (in `src/components/map/M11FloatingControls.tsx` unless noted)
- [x] 3.1 `M11FloatingLayerCard({ cycle })`: NWM `M11FloatingLayerSwitcher` shell — `section` `absolute left-4 top-4 z-[120] w-max max-w-52 p-2` + `GLASS_PANEL`, aria-label 「地图图层」; group title row with `Layers` icon + 「水文」; one `div` (not a button, `aria-current="true"`) with NWM selected row classes `flex w-full items-center gap-2 rounded-md border px-2 py-2 text-left border-primary-600 bg-primary-600/15 text-primary-700`, `Droplets` icon, label 「流量」, description 「q_down / m³/s」; footer `mt-2 border-t border-white/50 px-1 pt-2 text-xs text-neutral-700`: 「起报 <formatBeijingTime(cycle)> 北京时间」 or 「暂无数据」 when `cycle === null`. No 气象 group, no disabled placeholders.
- [x] 3.2 `M11FloatingBasemapSwitcher`: NWM option icons (`vector`→`Map`, `satellite`→`Satellite`, `terrain`→`Mountain`, `h-3.5 w-3.5`), selected `bg-primary-600 text-white shadow-sm`, unselected unchanged, `transition-colors`; still renders only `choices`, returns null for none.
- [x] 3.3 Legend (`overviewDataContracts.tsx` `M11DischargeLegend` and `lib/color.ts`): title row `Layers` icon (`h-4 w-4 text-primary-600`) + 「径流量图例」; `DISCHARGE_LEGEND` labels become `<1 m³/s`, `1–10 m³/s`, `10–100 m³/s`, `100–1000 m³/s`, `≥1000 m³/s`; add a separate exported `MISSING_LEGEND = { label: '无径流数据', color: '#94ADC7' }` rendered as the last row; `dischargeColor` thresholds/colors untouched. Position `bottom-10 right-4` (NWM's `bottom-[7.5rem]` only clears its bottom bar, which yd does not have) — it must not overlap the attribution strip.
- [x] 3.4 `MapPage.tsx`: render the layer card with the `map/latest` cycle (null while pending/absent/error) — `MapPage` already owns `latest`; `App` no longer passes cycle to the header.
- [x] 3.5 `MapPage.tsx` load-error alert (currently `absolute left-4 top-4 z-[130]`, same corner as the layer card): move it to the NWM notice slot `absolute left-1/2 top-4 z-[130] -translate-x-1/2` (keep `role="alert"` and text 「加载失败」) so neither covers the other.

## 4. Map controls and attribution
- [x] 4.1 `m11MapRuntime.tsx`: `new NavigationControl({ visualizePitch: true })` (compass shown); `attributionControl: { compact: false }`.
- [x] 4.2 `lib/basemaps.ts`: every raster source built from `basemaps.json` (tiles and annotation) gets `attribution: '© 天地图'`; the empty style has no sources, so no 天地图 attribution without basemaps.

## 5. Tests (vitest, node env, `react-dom/server` `renderToStaticMarkup`, as in `Header.test.tsx`)
- [x] 5.1 `Header.test.tsx` rewritten: markup has `<header`, 「永登流域水文模拟系统」, 「Yongdeng Basin Hydrological Modeling」, both `<img` alts; no 「起报」 text. Title assertion red against pre-change source is not required (title existed); the subtitle assertion must be red pre-change.
- [x] 5.2 Layer card: cycle `2026082712` → contains 「起报 2026-08-27 20:00 北京时间」 and 「q_down / m³/s」; cycle `null` → 「暂无数据」; contains no `<button`, no 「气象」, no 「代站」.
- [x] 5.3 Legend: five labels in order with ` m³/s`, then 「无径流数据」 last with `#94ADC7`. `lib/color.test.ts` `'uses the five-band legend labels and matching colors'` is updated to the new ` m³/s` labels (colors in that assertion unchanged); the `dischargeColor` bin test is untouched and green.
- [x] 5.4 Basemap switcher: choices `['vector','satellite']`, basemap `vector` → exactly two buttons, first `aria-pressed="true"` with `bg-primary-600`, each has an `<svg`; `[]` → empty markup.
- [x] 5.5 `basemaps.test.ts`: parsed style raster sources carry `attribution: '© 天地图'`; empty style has none.
- [x] 5.6 Record red→green for 5.1 subtitle, 5.2, 5.3, 5.5 in the implementer report.

## 6. Registration and gates
- [x] 6.1 `SNAPSHOT.md`: rows for `components/layout/SiteHeader.tsx` → `src/components/Header.tsx`, `M11FloatingLayerSwitcher`/`M11FloatingLegend`/basemap icons snippets, `index.css` primary tokens, the two brand PNGs (source path, original size, logo resize 720→96 with command); per-file removals (气象 group, precip legend, ops link, react-router `Link`, query-state/store props, `cn`/tailwind-merge helper); pin note: sources unchanged `4f8d98263`→`c9f363b38`.
- [x] 6.2 Gates: `corepack pnpm --dir viewer/frontend install --frozen-lockfile`, `typecheck`, `test`, `build`; `uv run python scripts/large_file_guard.py` (from `producer/`: `uv run python ../scripts/large_file_guard.py`); grep `viewer/frontend/dist` for `tianditu.gov.cn` (0), `[?&]tk=` (0), `src="/` and `href="/` (0); `openspec validate viewer-nwm-chrome-align --strict --no-interactive`.
- [x] 6.3 Local visual evidence (orchestrator): serve the build with local smoke fixture, screenshot 1440×900, compare with NWM homepage screenshot; plus one screenshot with `map/latest` failing (alert visible, not under the card).

Suggested fixture level: compact
Minimal mergeable slice: atomic - the chrome pieces share the new layout, theme tokens and icon dependency; splitting leaves a half-restyled page.

## Risk evidence
- Release/packaging: 1.2 frozen install, 6.2 build + large-file guard + dist gates.
- Legacy compatibility: 「加载失败」 alert stays visible and does not overlap the layer card (3.5; checked visually by the orchestrator with a failing `map/latest` in 6.3 — `MapPage` is effect-driven and has no node-env render test); 5.3 bins unchanged, 5.4 only-present basemaps, 5.2 暂无数据, 2.2 curve window stays clamped inside the map section (visual check 6.3), 4.2 no attribution without basemaps.
- Not selected: Public API/CLI (none), Config (no env/basemaps.json change), File IO (none), Schema/units (display text only), Auth/secrets (dist key gate still enforced by 6.2), Concurrency, Resource limits, Error handling (no new fetch paths), Docs (design §7 already merged in #337).
- Non-goals: curve window chrome and dataZoom (#339); bottom control bar / lead timeline (#340); 气象 group; 6-class legend; backend/deploy.
- Fix pass 1: attribution keeps MapLibre customAttribution (C1); layer-card footer nowrap (C2).
