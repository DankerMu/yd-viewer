## 1. Settings and route
- [x] 1.1 `viewer/src/yd_viewer/settings.py`: optional `tianditu_key: str | None` (env `YD_TIANDITU_KEY`, empty ⇒ None) and `basemap_cache_dir: Path` (env `YD_BASEMAP_CACHE_DIR`, default `/cache`; not validated at startup — created lazily on first write, so unset/absent dir does not break the four existing endpoints).
- [x] 1.2 `viewer/src/yd_viewer/basemap.py`: `register(app, settings)` adding `GET /api/basemap/tianditu/{layer}/{z}/{x}/{y}` only when the key is set; validation, upstream fetch (`urllib.request`, `run_in_threadpool`, 10 s, fixed UA, no Referer/Cookie), signature check, cache write (tmp + `os.replace`), hit/miss headers, `no-store` failures, per-layer 60 s cooldown after 429 (module-level dict keyed by layer, `time.monotonic`). Content-Type `image/png` or `image/jpeg` by signature. Key never in logs/errors.
- [x] 1.3 `viewer/src/yd_viewer/app.py`: call `basemap.register(app, settings)` before `app.mount("/api", ...)`.

## 2. Entrypoint, frontend, container
- [x] 2.1 `viewer/entrypoint.sh`: if `YD_TIANDITU_KEY` is non-empty write the six relative proxy paths (vector=vec/cva, satellite=img/cia, terrain=ter/cta) and ignore the URL envs; else unchanged. Still no URL/key in stdout/stderr.
- [x] 2.2 `viewer/frontend/src/lib/basemaps.ts`: add `resolveTileUrl(pageDir, url)` — absolute (`/^[a-z][a-z0-9+.-]*:/i`) → unchanged; else `pageDir + url` by string concatenation (`pageDir` = page URL through its last `/`); the template itself MUST NOT pass through `new URL()`/`resolveUrl` (they percent-encode `{z}/{x}/{y}`); computing `pageDir` via `new URL(".", document.baseURI)` as MapPage already does is fine; the style builder maps `tiles`/`annotation` through it; update the MapPage call site to pass the page directory.
- [x] 2.3 `viewer/compose.example.yml`: `yd-basemap-cache:/cache` named volume (top-level `volumes:` with `name: yd-basemap-cache`); `viewer/env.example`: `YD_TIANDITU_KEY=` and `YD_BASEMAP_CACHE_DIR=/cache`; `viewer/Dockerfile`: `mkdir /cache && chown 10001:10001 /cache`.

## 3. Tests and evidence
- [x] 3.1 `viewer/tests/test_basemap.py` per design "Required evidence" (TestClient, monkeypatched `urlopen`, `tmp_path` cache, monkeypatched monotonic; assert the upstream URL contains `T=vec_w&x=0&y=0&l=1&tk=` and the UA header, and no `Referer`); no-key 404 non-HTML; key absent from bodies and `caplog`.
- [x] 3.2 `viewer/tests/test_entrypoint.sh`: proxy-mode case (key set + URL envs set → exact relative JSON; key not in output) alongside existing cases.
- [x] 3.3 NEW `viewer/tests/test_container_contract.py` (no such test exists today): parse `viewer/compose.example.yml` (PyYAML is not a dependency — assert on text lines) → exactly two bind volumes ending with `:ro`, exactly one `yd-basemap-cache:/cache`, top-level named volume `yd-basemap-cache`; `viewer/env.example` → the 11 keys present, `YD_VIEWER_STATIC_DIR` absent; `viewer/Dockerfile` contains `/cache` creation with `10001`.
- [x] 3.4 `viewer/frontend/src/lib/basemaps.test.ts`: `api/basemap/tianditu/vec/{z}/{x}/{y}` with pageDir `https://h/yd/` → exactly `https://h/yd/api/basemap/tianditu/vec/{z}/{x}/{y}` (braces literal, not `%7B`); absolute `https://t0.example/vec/{z}/{x}/{y}` unchanged byte-for-byte.
- [x] 3.5 `cd viewer && uv run pytest -q`; `uv run ruff check . && uv run ruff format --check .`; `bash viewer/tests/test_entrypoint.sh`; `cd viewer/frontend && corepack pnpm test && corepack pnpm typecheck && corepack pnpm build`; `grep -rlE "tianditu\.gov\.cn|[?&]tk=" viewer/frontend/dist` empty (query-string form; a minified identifier `tk=` is not a hit, design §7); `openspec validate viewer-basemap-tile-proxy --strict --no-interactive`; red→green recorded for 3.1.
Suggested fixture level: expanded
Minimal mergeable slice: atomic - route + entrypoint mode + frontend resolve + compose volume ship together (any subset leaves the live page without basemaps).

## Risk evidence
- File IO / path safety: cache path built only from a whitelisted literal and validated ints; tmp + `os.replace`; nothing written on failure (3.1).
- Error handling: 502/503 + `no-store` on every failure, cooldown isolation per layer (3.1).
- Security: no Referer/Cookie forwarding; key never in logs/bodies (3.1); build artifact key-free (3.5).
- Config: precedence rule in entrypoint (3.2); defaults (1.1).
- Legacy compatibility: no key ⇒ old JSON, route 404 non-HTML (3.1, 3.2).
