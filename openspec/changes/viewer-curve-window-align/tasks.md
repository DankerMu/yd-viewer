## 1. Pure helpers (testable in node env)
- [x] 1.1 `RiverCurveWindow.tsx` exports `availableSources(series): ForecastSource[]` — keys of `series` whose array is non-empty, in `gfs`, `ifs` order — and `sourceSubtitle(sources)` → `河段 q_down 流量预报 · GFS+IFS` / `· GFS` / `· IFS`; with no sources the subtitle is `河段 q_down 流量预报` (no dot).
- [x] 1.2 `ForecastChart.tsx` exports its option builder (e.g. `buildForecastOption(packed, labels)`) so tests can inspect the ECharts option without a DOM; `ForecastChart` keeps using it.

## 2. Panel chrome (`RiverCurveWindow.tsx`)
- [x] 2.1 Header per NWM `M11RiverForecastPanel` (c9f363b38 lines 226–251): `header` `flex shrink-0 items-start justify-between gap-2.5 border-b border-white/10 px-4 py-3`; icon tile `mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-cyan-400/10 text-cyan-300 ring-1 ring-inset ring-cyan-400/30` with `Waves`; title `truncate text-sm font-semibold leading-tight text-slate-50` 「河段 {reachId}」; subtitle `mt-0.5 text-[11px] uppercase tracking-[0.14em] text-cyan-300/80` from `sourceSubtitle` (sources from the ready curve; before a curve is ready, the plain `河段 q_down 流量预报`); close button NWM classes, `X` icon, `aria-label="关闭面板"`, keeps `data-m11-window-no-drag`. No 河段 ID meta line (it would repeat the title).
- [x] 2.2 Cycle bar (NWM lines 262–272): row `flex shrink-0 items-center gap-2 border-b border-white/10 px-4 py-2 text-[11px] text-slate-400`; label 「起报」 (`shrink-0 uppercase tracking-wide`); the existing native `<select>` (same value/options/onChange/`data-m11-window-no-drag`, `aria-label` becomes 「起报时间选择」) styled like NWM's `M11IssueTimeSelect` trigger (read `SelectTrigger` usage in `M11PopupChrome.tsx` for the classes; do not copy Radix `Select`); option labels stay Beijing time (design §7), no 「UTC」; trailing `ml-auto text-[10px] text-slate-500` 「GFS + IFS 同步切换」.
- [x] 2.3 Chip row above the chart when a curve is ready (NWM lines 281–292), as a small exported component `SourceChips({ sources })`: for `gfs`, `ifs` a chip `inline-flex items-center gap-1.5 text-[11px]` with a `h-2 w-3.5 rounded-sm` swatch (`#22d3ee` / `#34d399`); present → `text-slate-200`, absent → `text-slate-500 line-through`; trailing `ml-auto text-[10px] text-slate-500` 「滚轮缩放时间轴」. Body container `flex min-h-0 flex-1 flex-col px-3 pb-2 pt-2.5`.
- [x] 2.4 Unchanged: `RiverCurveSession` state machine, 加载中/暂无数据/加载失败 copy and roles, cycles-error alert, `selectorOptions`, `asCycleEntries`, `asCurveResponse`, `getJson`, `M11DraggableCurveWindow` usage.

## 3. Chart (`ForecastChart.tsx`, `echartsCore.ts`)
- [x] 3.1 Remove the ECharts `legend` (chip row replaces it); grid `{ left: 48, right: 16, top: 28, bottom: 28 }` (NWM compact left/right/bottom; top leaves room for the axis-end name); yAxis `name: '流量 (m³/s)'`, default `nameLocation` (end), `nameGap: 12`; everything else (colors, dashed IFS, `connectNulls: false`, tooltip formatter skipping null, area fill, category x-axis with Beijing labels) unchanged.
- [x] 3.2 `dataZoom: [{ type: 'inside', zoomOnMouseWheel: true, moveOnMouseMove: false, moveOnMouseWheel: false, filterMode: 'none' }]` (NWM zoomable branch verbatim).
- [x] 3.3 `echartsCore.ts` registers `DataZoomComponent` and drops the now-unused `LegendComponent`.

## 4. Tests (vitest node env; `renderToStaticMarkup` where markup is needed)
- [x] 4.1 `availableSources`/`sourceSubtitle`: both → `GFS+IFS`; gfs only → `· GFS`; ifs only (gfs key absent) → `· IFS`; empty-array series treated as absent; none → no dot.
- [x] 4.2 `buildForecastOption`: has exactly one `dataZoom` entry `type: 'inside'`, `filterMode: 'none'`, `moveOnMouseMove: false`; no `legend`; series keep `connectNulls: false`; a `null` value stays `null` in series data.
- [x] 4.3 New test: `buildForecastOption(...).tooltip.formatter([...])` with one `null` point and one numeric point (e.g. 1.5) returns the time line plus only the numeric line (`1.50 m³/s`); the output contains no `0.00`.
- [x] 4.6 `SourceChips` via `renderToStaticMarkup`: sources `['gfs']` → the IFS chip has `line-through` and `text-slate-500`, the GFS chip has `text-slate-200` and no `line-through`; sources `['gfs','ifs']` → neither chip has `line-through`.
- [x] 4.4 `echartsCore.ts` registering `DataZoomComponent` is checked by diff and by the 5.3 wheel-zoom screenshot (no DOM in node env; no extra test).
- [x] 4.5 Record red→green for 4.1, 4.2, 4.3 and 4.6.

## 5. Registration and gates
- [x] 5.1 `SNAPSHOT.md`: update the `echartsCore.ts` entry (DataZoom added, Legend removed); record the deliberate chart spacing difference from NWM (NWM compact `grid.top: 16` + `nameGap: 32` clips the y-axis name; yd uses `top: 28`, `nameGap: 12`); rows for the `M11RiverForecastPanel` snippets (header, cycle bar, chip row), `M11IssueTimeSelect` trigger classes (Radix Select not copied), `ForecastChart` zoomable branch + compact grid, `echartsCore` DataZoom; removals: hydroMet loading chain, `formatIssueTime` UTC labels, partial-failure reasons list, dual-window `active/onActivate`.
- [x] 5.2 In `viewer/frontend`: `corepack pnpm typecheck`, `corepack pnpm test`, `corepack pnpm build`; dist greps `tianditu.gov.cn`, `[?&]tk=`, `(src|href)="/` empty; `openspec validate viewer-curve-window-align --strict --no-interactive`.
- [x] 5.3 Orchestrator: local click screenshot (live data proxied) beside the NWM panel screenshot; wheel over the chart zooms, dragging the header still moves the window.

Suggested fixture level: compact
Minimal mergeable slice: atomic - one component's chrome plus the chart option it renders.

## Risk evidence
- Legacy compatibility: 4.2 null kept and `connectNulls: false`; 4.3 tooltip; 2.4 state machine untouched (existing behavior, reviewed by diff); cycle select keeps `data-m11-window-no-drag` (2.2) so drag and select do not fight; wheel zoom checked visually in 5.3.
- Not selected: Public API (none), Config (none), File IO (none), Schema/units (display only), Auth/secrets (dist gate 5.2), Concurrency (fetch race guards unchanged), Resource limits, Error handling (copy unchanged), Release/packaging (no new dependency; `lucide-react` already present), Docs (design §7 merged in #337).
- Non-goals: bottom timeline (#340); station popup; UTC labels; Radix Select; changing `M11DraggableCurveWindow`.
