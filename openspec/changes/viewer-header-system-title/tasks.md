## 1. Header title
- [x] 1.1 `viewer/frontend/src/components/Header.tsx`: add `<div>永登流域水文模拟系统</div>` as the first child of the banner (slightly stronger weight is fine, e.g. `font-medium`; keep the existing container classes); keep 「流量 (m³/s)」 and the 起报 line exactly as they are.
- [x] 1.2 `viewer/frontend/index.html`: `<title>永登流域水文模拟系统</title>`.
- [x] 1.3 `viewer/frontend/src/components/Header.test.tsx` (vitest, node env, `react-dom/server` `renderToStaticMarkup`, no new deps): (a) markup contains `永登流域水文模拟系统` and `流量 (m³/s)`; (b) `cycle=null` → contains `暂无数据`; (c) `cycle="2026082712"` → contains `起报 2026-08-27 20:00 北京时间`; (d) ordering: `const i = html.indexOf(TITLE), j = html.indexOf("流量 (m³/s)"); expect(i).toBeGreaterThanOrEqual(0); expect(i).toBeLessThan(j)` (must not be vacuously true). Use `renderToStaticMarkup`, not `renderToString` (the latter inserts `<!-- -->` between adjacent text nodes and breaks the contiguous 起报 assertion).
- [x] 1.4 `corepack pnpm --dir viewer/frontend test`; `corepack pnpm --dir viewer/frontend typecheck`; `corepack pnpm --dir viewer/frontend build`; grep built `dist/` for `tianditu.gov.cn`, `tk=` and `src="/`/`href="/` (all absent) and `dist/index.html` for `<title>永登流域水文模拟系统</title>` (present); `openspec validate viewer-header-system-title --strict --no-interactive`; red→green recorded for (a).
Suggested fixture level: compact
Minimal mergeable slice: atomic - one component line, one html title, one test file.

## Risk evidence
- Legacy compatibility: existing two header lines and empty state unchanged (1.3 b/c); no other component touched.
- Non-goals: no 气象代站 layer or popup layout change (design §2 明确不做); no backend or config change.
