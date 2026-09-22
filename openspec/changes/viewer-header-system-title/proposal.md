## Why
During the M5 browser review of `https://test.nwm.ac.cn/yd/` (2026-09-22) the user decided the page needs a visible system title: 「永登流域水文模拟系统」. Docs are merged first (PR #324): `docs/design.md` §7 页头条目 now says the header shows the system title, the 起报 line and the 「流量 (m³/s)」 line, and the document `<title>` is the same string. Issue #325.

## What Changes
- `viewer/frontend/src/components/Header.tsx`: add one title line 「永登流域水文模拟系统」 as the first line of the existing floating header (`role="banner"`); the two existing lines (「流量 (m³/s)」, 「起报 <time> 北京时间」 / 「暂无数据」) are unchanged in text and order.
- `viewer/frontend/index.html`: `<title>` becomes 「永登流域水文模拟系统」 (was `yd`).
- Tests: `viewer/frontend/src/components/Header.test.tsx` using `react-dom/server` `renderToStaticMarkup` (node environment, no new dependencies): renders the title, keeps 「流量 (m³/s)」, shows 「暂无数据」 for `cycle=null`, and shows `2026-08-27 20:00` for cycle `2026082712`.
- Nothing else: no map/curve/basemap logic, no backend, no new packages, no Tailwind config.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `viewer-frontend`: 页头 — carries the system title.

## Impact
`viewer/frontend/src/components/Header.tsx`, `viewer/frontend/index.html`, new `Header.test.tsx`. Build output stays relative-path and key-free. The node-27 image must be rebuilt and redeployed afterwards (M5 receipt), which is a separate authorized site step, not part of this change.

Issue type: feature (small UI text)
Fixture level: compact
Upstream suggested level: compact (agree)
Blast radius: one component's markup and the document title; a wrong string or a removed line is visible on the live page immediately.
Selected risk packs: Legacy compatibility (existing header lines and the `暂无数据` empty state must stay byte-identical). Others not selected: no API/config/schema/file IO change.
Evidence floor: `corepack pnpm --dir viewer/frontend test` green with the new file (title assertion red against pre-change source); `corepack pnpm --dir viewer/frontend build`; `corepack pnpm --dir viewer/frontend typecheck`; `dist/index.html` carries the new `<title>`; built `dist/` contains no `tianditu.gov.cn`/`tk=` and no `/`-rooted asset refs; `openspec validate viewer-header-system-title --strict --no-interactive`.
design.md omitted (compact fixture).
