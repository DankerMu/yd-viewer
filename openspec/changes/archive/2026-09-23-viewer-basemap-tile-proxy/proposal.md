## Why
On 2026-09-22 the mainline viewer went live on node-27 with the browser fetching tianditu tiles directly using the NWM key; tianditu throttled the shared key (`429`/`403 code 302010 该tk已限流`) and the vector basemap broke. The user decided (Q1–Q4, 2026-09-22) that yd reuses the NWM key but serves tiles through its own same-origin reverse proxy mirroring NWM `apps/api/routes/basemap.py` (server-side key, file cache, failures never cached, per-layer cooldown). Docs merged first (PR #329): design §6.1 endpoint row, §7 basemaps rules (`YD_TIANDITU_KEY` precedence, relative tile paths resolved by the frontend), agent-ops §9.2 env list + named cache volume, §16.2. Issue #330.

## What Changes
- Backend: new `viewer/src/yd_viewer/basemap.py` with `GET /api/basemap/tianditu/{layer}/{z}/{x}/{y}`; `settings.py` gains optional `YD_TIANDITU_KEY` and `YD_BASEMAP_CACHE_DIR` (default `/cache`); `app.py` registers the route only when the key is set (otherwise the path stays a 404 like any unknown `/api/*`).
- Entrypoint: when `YD_TIANDITU_KEY` is set, `basemaps.json` carries six relative paths `api/basemap/tianditu/<layer>/{z}/{x}/{y}` and the six URL envs are ignored; unset → unchanged behavior.
- Frontend: `lib/basemaps.ts` gains a template-safe `resolveTileUrl(pageDir, url)` (string concatenation, never `new URL()`, which would percent-encode `{z}/{x}/{y}`) applied to every tile URL before building the MapLibre style; absolute URLs are unchanged.
- Container: `compose.example.yml` adds named volume `yd-basemap-cache:/cache`; `env.example` lists the two new keys (empty); `Dockerfile` creates `/cache` owned by uid 10001.
- Tests: backend (TestClient, `urllib.request.urlopen` monkeypatched), entrypoint shell test, frontend basemaps test, compose/env assertions.

## Capabilities
### New Capabilities
None (new requirement under existing `viewer-api`).
### Modified Capabilities
- `viewer-api`: ADDED requirement 天地图瓦片反代.
- `viewer-config-health`: 配置只来自环境变量 — two optional envs.
- `viewer-container`: entrypoint 生成 basemaps.json (proxy mode), compose 示例与 env 清单 (cache volume, new keys).
- `viewer-frontend`: 底图切换 — relative tile URLs resolved before MapLibre.

## Impact
`viewer/src/yd_viewer/{basemap.py,settings.py,app.py}`, `viewer/entrypoint.sh`, `viewer/frontend/src/lib/basemaps.ts` and the MapPage call site, `viewer/compose.example.yml`, `viewer/env.example`, `viewer/Dockerfile`, tests. No new Python or npm dependencies (stdlib `urllib`). node-27 redeploy (new `.env` keys, compose volume) is a separate authorized site step.

Issue type: feature (shared entrypoint + file IO + upstream HTTP)
Fixture level: expanded
Upstream suggested level: expanded (agree)
Blast radius: a wrong cache path could write outside `/cache` (layer/z/x/y are validated integers and a whitelisted literal, so no traversal); a cached error body would poison every visitor for a week (never cache non-200/non-image); a leaked key would appear in logs/errors (key never logged, never in responses); a broken `basemaps.json` would remove all basemaps (entrypoint test covers both modes).
Selected risk packs: File IO / path safety (cache tree under `YD_BASEMAP_CACHE_DIR` only; tmp + `os.replace`; parent dirs created lazily); Error handling (upstream non-200/timeout/non-image → 502/503 + `no-store`, nothing written; 429 → per-layer 60 s cooldown → 503 without upstream call); Security (layer whitelist, integer bounds, no Referer/Cookie forwarding, key absent from logs and bodies); Config (two optional envs, precedence rule, defaults); Legacy compatibility (no key → identical old behavior: six URL envs, direct tiles, route absent). Not selected: concurrency beyond tmp+rename (single-process uvicorn), migrations, money.
Evidence floor: `cd viewer && uv run pytest -q` green with the new tests (proxy cases red against pre-change); `uv run ruff check . && uv run ruff format --check .`; `bash viewer/tests/test_entrypoint.sh` (both modes); `cd viewer/frontend && corepack pnpm test && corepack pnpm typecheck && corepack pnpm build`; built `dist/` free of `tianditu.gov.cn` and query-string `[?&]tk=`; `openspec validate viewer-basemap-tile-proxy --strict --no-interactive`.
