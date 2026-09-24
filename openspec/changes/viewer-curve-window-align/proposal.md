## Why
After #338 (PR #341) the page chrome matches the NWM homepage, but the river curve window still uses yd's bare header (「河段 N」 + inline 「起报（北京时间）」 select + `×`) and a chart without wheel zoom. design §7 (PR #337, user ruling 2026-09-24) requires the NWM `M11RiverForecastPanel` look and the `ForecastChart` `zoomable` branch. The NWM sources are unchanged from pin `4f8d98263` to `c9f363b38`. Issue #339.

## What Changes
- `RiverCurveWindow.tsx`: NWM panel header (Waves icon tile, 「河段 <reach_id>」, subtitle 「河段 q_down 流量预报 · <available sources joined by +>」, lucide `X` close button), then a cycle bar row (「起报」 label + existing native `<select>` styled like NWM's trigger, Beijing-time labels, trailing hint 「GFS + IFS 同步切换」), then a source chip row (GFS `#22d3ee`, IFS `#34d399`; a source absent from `series` is greyed and struck through; trailing hint 「滚轮缩放时间轴」), then the chart. Loading/404/error states and cycle semantics are unchanged.
- `ForecastChart.tsx`: NWM compact look — no ECharts legend (the chip row replaces it), NWM compact grid, y-axis name at the axis end; add `dataZoom: [{ type: 'inside', zoomOnMouseWheel: true, moveOnMouseMove: false, moveOnMouseWheel: false, filterMode: 'none' }]`.
- `echartsCore.ts`: register `DataZoomComponent`, drop the unused `LegendComponent`.
- `SNAPSHOT.md`: register the `M11RiverForecastPanel` header/cycle-bar/chip-row snippets, the `M11IssueTimeSelect` trigger styling (native select kept, Radix `Select` not copied), and the `zoomable` branch.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `viewer-frontend`: 交互与曲线窗 (panel chrome + wheel zoom).

## Impact
`viewer/frontend/src/components/map/RiverCurveWindow.tsx`, `src/components/charts/{ForecastChart.tsx,echartsCore.ts}`, new tests, `SNAPSHOT.md`. No API, backend or dependency change (`lucide-react` already added by #338). node-27 redeploy is a separate authorized step.

Issue type: feature (UI alignment)
Fixture level: compact
Upstream suggested level: compact (agree)
Blast radius: the curve window; a mistake can drop a source curve, mislabel sources, break the null-gap semantics, or let wheel/drag gestures fight each other.
Level note: compact despite the legacy pack because the change stays inside one component and its chart; no API, file format, shared state or external consumer is touched.
Selected risk packs: Legacy compatibility (null gaps, tooltip never shows 0, cycle switch only refetches the curve, drag ignores controls, loading/404/error copy).
Evidence floor: in `viewer/frontend`: `corepack pnpm typecheck`, `test`, `build`; dist greps; `openspec validate viewer-curve-window-align --strict --no-interactive`; local click screenshot beside the NWM panel.
design.md omitted (compact fixture).
