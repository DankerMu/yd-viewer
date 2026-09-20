# M3 viewer — 任务分解

任务组按依赖排序（"依赖"行给出真实前置，未列即可与前面各组并行）；后端落 `viewer/src/yd_viewer` 与 `viewer/tests`，前端落 `viewer/frontend`，容器落 `viewer/Dockerfile`、`viewer/entrypoint.sh`、`viewer/compose.example.yml`、`viewer/env.example`、`.github/workflows/ci.yml`。本地门禁是唯一门禁（design §9.1）；部署与 live receipt 归 M5。

**每个任务共同约束**：简化项目，不过度设计——无缓存层、无抽象接口、无预留扩展点；只做 design §2 列出的能力，§2「明确不做」一律不做。

## 0. docs-first：契约与设计文档修订

- [ ] 0.1 products-contract §5.2 增「viewer 消费侧两层校验」条款（结构层：168 行、列编号集合 = 几何 `reach_id` 集合，枚举期只读头 + stat；数据层：第 0 列 `0..10020`，取数时校验；不合规 source 视为不可用，`DONE` 仍是唯一完成判据）；§6 增「几何完整性由 viewer 启动自检判定，不设 `input/viewer/DONE`」并写明自检项（FeatureCollection、整数 `reach_id` 无重复、boundary 单 Feature Polygon/MultiPolygon）
- [ ] 0.2 design §6.1 补：错误码与 FastAPI 默认 `detail`、`map/latest` 回落顺序与 `values` 排序、曲线响应形状、health body、`/geometry/*` 静态路径、SPA 托管与 `/api` 优先；§7 补：5 档色带 ≥ 阈值与标签、`basemaps.json` 运行时注入（6 个 env）、缩放/比例尺/初始视野、Tailwind 与来源登记；§6 「无写路径」澄清为「不写只读挂载；entrypoint 写镜像内 `basemaps.json`」；§9.1 表增「viewer 启动自检」行，「前端」行的「组件测试」改为「纯函数测试」（用户 Q6）；§11 待现场确定项增「主线 viewer 与应急副本的 `/yd/` 归属」
- [ ] 0.3 agent-ops §9.2 补 viewer 容器 env 清单（`YD_VIEWER_{INPUT,OUTPUT}_DIR`、`YD_VIEWER_PORT`、六个 `YD_BASEMAP_*`；`YD_VIEWER_STATIC_DIR` 由镜像固定不可覆盖）、compose 各名 `yd-` 前缀示例与「`basemaps.json` 由 entrypoint 生成、URL 不进日志」

依赖：无
§9.1 归属：无直接行（契约前置）
Suggested fixture level: none - 纯文档修订，无代码与测试
Minimal mergeable slice: atomic - 三份文档的改动互相引用（契约条款 ↔ design 端点表 ↔ ops env 清单），分开合并会出现引用悬空

## 1. viewer-config-health：配置与几何自检

- [ ] 1.1 `viewer/pyproject.toml` 加 `fastapi`、`uvicorn[standard]`，dev 加 `httpx`；建包布局 `settings.py` / `geometry.py` / `dat.py` / `catalog.py` / `app.py`（空模块）；删除 smoke 测试
- [ ] 1.2 `settings.py`：从 `YD_VIEWER_INPUT_DIR` / `YD_VIEWER_OUTPUT_DIR` / `YD_VIEWER_STATIC_DIR` 装载，缺失/非目录/不可读即抛带变量名与路径的错误（spec viewer-config-health）
- [ ] 1.3 `geometry.py`：启动读取两份 GeoJSON 并按 spec「启动几何自检」的九条文件级 scenario 校验（第十条「请求期不重读」由 4.2 的 HTTP 测试断言）（FeatureCollection、整数 `reach_id` 存在/类型/无重复、boundary 单 Feature、Polygon 与 MultiPolygon 都接受、其它类型拒绝）；返回权威 `reach_id` 集合；失败矩阵测试

依赖：0
§9.1 归属：viewer 启动自检（0.2 新增行）
Suggested fixture level: compact - tmp 目录 + 内联小 GeoJSON 即可覆盖全部场景
Minimal mergeable slice: 1.1–1.2（依赖与 settings）——纯函数加测试，可独立合并保绿；1.3 为后继

## 2. viewer-dat-reader：v2 DAT 两层读取

- [ ] 2.1 `viewer/tests/synthetic.py`：合成 `YD_ROOT` 生成器——写 v2 DAT（1024 字节头 + `st` + `nc` + 列编号 + 数据区；参数化行数/列编号/第 0 列/`st`/单元格 NaN）、写 `DONE`（可选写成 symlink）、写小 GeoJSON（Polygon 或 MultiPolygon）；不依赖 producer 包
- [ ] 2.2 `dat.py` 结构层 `read_header(path, reach_ids)`：有界读 `1024+8*(2+nc)` 字节 + `stat` 推算行数；校验整除、168 行、列编号无重复且集合相等；`st` 只解析不用；`DatError` 含路径与差异（#245 已实现基础；#246 用户裁决补重复编号拒绝及回归）
- [ ] 2.3 `dat.py` 数据层 `read_dat(path, reach_ids)`：先结构层，再整读 + `array('d')` 解析数据区；第 0 列逐值校验（期望由契约常量独立推导，含 NaN 拒绝）；独立 golden 轴证明 validator 单独/与 writer 共同偏移 +60 的变异会失败，数学等价改写不要求变红
- [ ] 2.4 `dat.py` 取值：`row(lead)` 按权威集合升序、`column(reach_id)` 168 值，均已 /86400

依赖：0（2.1–2.2 可与 1 并行；2.2 的权威集合参数为 `set[int]`，不依赖 1.3 的类型）
§9.1 归属：v2 DAT 解析
Suggested fixture level: compact - 合成字节串即可覆盖全部坏文件矩阵
Minimal mergeable slice: 2.1–2.2（生成器 + 结构层）——不含整读与换算，可独立合并保绿；2.3–2.4 为后继

## 3. viewer-catalog：DONE 枚举与 7 天窗

- [ ] 3.1 `catalog.py` 枚举：`output/<cycle>/<source>/DONE` 经 `lstat` 为普通文件（symlink 不算）、cycle/source 命名校验、非法条目忽略
- [ ] 3.2 结构层排除：对每个候选调用 2.2 `read_header`，失败即排除并 WARNING（路径 + 原因）；整 cycle 无可用 source 则不列；枚举期读取量有界测试
- [ ] 3.3 窗口与排序：以最新可用 cycle 为锚取 7 天、倒序、`sources` 固定 `gfs,ifs` 顺序；`latest()` 返回锚 cycle 或 `None`

依赖：3.1 需 2.1（symlink fixture）；3.2 需 2.2；3.3 需 3.1
§9.1 归属：目录契约
Suggested fixture level: compact - tmp 目录树按命名生成空 DONE 与合成 DAT
Minimal mergeable slice: 3.1（枚举与命名校验纯函数）——不含 DAT 校验与窗口，可独立合并保绿；3.2–3.3 为后继

## 4. viewer-api：应用工厂、四个端点、静态几何与 SPA

- [ ] 4.1 `app.py` 应用工厂 `create_app(settings)`：启动期调用 1.2/1.3；`GET /api/health` 用 3.3 `latest()` 填 `latest_cycle`，`output/` 不可枚举 503
- [ ] 4.2 `GET /api/cycles`：直接返回 3.3 结果；空态 `[]`；加「删除 rivers.geojson 后请求仍正常」断言（几何只在启动期读）
- [ ] 4.3 `GET /api/map/latest`：按 catalog 顺序取候选（cycle 倒序、gfs 先）调用 2.3/2.4，失败 WARNING 并下一候选；`valid_time` UTC Z；`values` 按 reach_id 升序；无候选或全失败 404
- [ ] 4.4 `GET /api/cycles/{cycle}/reaches/{reach_id}`：每个可用 source 调用 2.3/2.4，失败省略键；`series` 空 404；cycle 不可用 404；格式/越界 4xx
- [ ] 4.5 `output/` 不可枚举时四端点 503；错误体为 FastAPI 默认 `detail`（不自定义 handler）；四端点各一条 503 测试（在 4.2–4.4 合并后补齐）
- [ ] 4.6 `/geometry/*` 以 StaticFiles 直挂 `YD_VIEWER_INPUT_DIR`；`YD_VIEWER_STATIC_DIR` 挂 `/`；`/api/*`、`/geometry/*` 先注册；未知 `/api/*` 404 非 HTML

依赖：4.1 需 1、3.2、3.3；4.2 需 4.1；4.3–4.4 需 4.1、2.4；4.5 需 4.2、4.3、4.4；4.6 需 4.1
§9.1 归属：API
Suggested fixture level: compact - TestClient + 合成 YD_ROOT 树即可
Minimal mergeable slice: 4.1（应用工厂 + health）——只依赖 settings/geometry/catalog 已合并部分，可独立合并保绿；4.2 只需 catalog，4.3/4.4 另需 2.4；4.6 只需 4.1；4.5 最后（需三端点齐）

## 5. viewer-frontend：脚手架、纯函数、M11 快照与页面

- [x] 5.1 脚手架：`viewer/frontend/` Vite 6 + React 18.3 + TS 5.9 + Tailwind + vitest，`packageManager: pnpm@10.11.0`，`base: './'`，`build.outDir` 默认 `dist`；`typecheck`/`test`/`build` 脚本，vitest 配 `passWithNoTests: true`（5.2 前无测试文件也保绿）；空页面可构建
- [x] 5.2 纯函数模块与 vitest：`lib/api.ts`（相对 URL 拼接与三种响应类型）、`lib/time.ts`（cycle/lead → 北京时间文案）、`lib/color.ts`（≥ 阈值 5 档 + 图例标签，含边界值测试）、`lib/basemaps.ts`（解析 `basemaps.json` → MapLibre 样式；404/`{}`/缺键 → 空样式）、`lib/cycles.ts`（cycles → 下拉项）、`lib/bbox.ts`（boundary GeoJSON → 包围盒）
- [x] 5.3 M11 快照：从 NWM `4f8d98263` 复制 `M11DraggableCurveWindow`、`ForecastChart` + `echartsCore`、`m11MapRuntime`（去 key，改读 5.2 `lib/basemaps.ts` 的样式）、`m11MapBuilders`、`m11MapInteractions`、`m11MapPrimitives`、`M11FloatingControls`（只留底图切换）、`overviewDataContracts` 的色带/图例子集；逐文件删除 store、路由、OpenAPI client、代站弹窗、降水叠加、RBAC 六类内容并在 `SNAPSHOT.md` 逐文件登记；每文件 ≤1000 行
- [x] 5.4 地图页：全屏 MapLibre、加载几何、按 `map/latest` 着色、右下 colorbar、右上底图按钮、缩放控件与比例尺、初始视野 fit 到 5.2 `bbox`、hover/selected 高亮
- [x] 5.5 曲线窗：点击河段打开可拖拽窗，起报下拉（默认地图 cycle）、GFS/IFS 同轴 168 点、x 轴北京时间；切换只重取曲线
- [x] 5.6 页头：最新起报时间（北京时间，标「起报」）与「流量 (m³/s)」；无数据显示「暂无数据」；`App.tsx` 装配
- [ ] 5.7 本地开发：`vite.config` 代理 `/api`、`/geometry`、`/basemaps.json` 到本地后端；README 一条命令用 2.1 生成器起全栈

依赖：5.1 需 0；5.2 需 5.1；5.3 需 5.1、5.2（`m11MapRuntime` 读 `lib/basemaps.ts`）；5.4–5.6 需 5.2、5.3（响应形状按 spec，可用 mock JSON 开发，不需后端合并）；5.7 需 2.1 与组 4 实际可运行（不可用 mock 顶替）
§9.1 归属：前端
Suggested fixture level: compact - 纯函数 vitest + 构建门禁；不做 DOM 组件测试（用户 Q6）
Minimal mergeable slice: 5.1（脚手架 + 空页面可构建）——不含任何业务代码，可独立合并保绿；5.2 → 5.3 → {5.4, 5.5, 5.6} 串行，5.7 最后

## 6. viewer-container：镜像、entrypoint、compose、CI

- [x] 6.1 `viewer/entrypoint.sh` + `viewer/Dockerfile`（同一 PR）：entrypoint 按 6 个 env 生成 `$YD_VIEWER_STATIC_DIR/basemaps.json`（缺键缺席、全缺 `{}`、URL 不进日志）后 `exec uvicorn --host 0.0.0.0 --port 8000`（容器内端口固定 8000，宿主端口由 compose `127.0.0.1:${YD_VIEWER_PORT}:8000` 映射），shell 单测以非 root 用户给定 env 断言 JSON 与日志不含 URL；Dockerfile 多阶段（Node 22 pnpm build → Python 3.12 `uv sync --frozen --no-dev` → `ENV YD_VIEWER_STATIC_DIR=<镜像内固定路径>`，该目录属运行用户 → 非 root 执行 `entrypoint.sh`）；`docker build` 通过；镜像内 `USER` 非 root 且静态目录可写含 `index.html`
- [ ] 6.2 `viewer/compose.example.yml`：两个 `:ro` 挂载、`127.0.0.1:${YD_VIEWER_PORT}:8000`、`env_file`、project/service/container/network/image 全 `yd-` 前缀；`viewer/env.example` 列出 `YD_VIEWER_{INPUT,OUTPUT}_DIR`、`YD_VIEWER_PORT`、六个 `YD_BASEMAP_*`，不含 `YD_VIEWER_STATIC_DIR`
- [x] 6.3 ci.yml 新增 `viewer-frontend` job（install --frozen-lockfile、typecheck、test、build）；现有 job 不变

依赖：6.3 需 5.1；6.1 需 5.1 与 1.1；6.2 需 6.1
§9.1 归属：前端构建门禁（6.3）；其余为 M5 镜像前置
Suggested fixture level: compact - `docker build` 与 entrypoint shell 单测
Minimal mergeable slice: 6.3（CI job）——只改 ci.yml，5.1 合并后可独立保绿；6.1 为后继（entrypoint 与 Dockerfile 原子：Dockerfile 的运行阶段执行 entrypoint，单独合并任一方都不可启动或 build 失败）；6.2 `Depends on` 6.1

## Issue-workflow risk evidence

Only the current issue's task(s) are implementation scope. The shared fixture and
its evidence metadata are orchestration artifacts, not permission to implement
later tasks. Each issue receives its own fixture review and code review.

- Config / project setup — selected: #256 frozen install with pnpm 10.11.0, typecheck, test (no tests yet succeeds), build produce `dist/index.html`.
- Release / packaging / dependency compatibility — selected: #256 uses Vite 6, React 18.3, TS 5.9 and upstream Tailwind; install uses committed lockfile. Container changes additionally require task 6.1 evidence.
- Public API / CLI / script entry — selected: #256 preview serves the empty React page and its relative assets at root and a stripped prefix; no API calls are added until their task.
- Auth / permissions / secrets — selected: #256 source/env and built text contain no `tianditu` or `tk=`; runtime injection only belongs to 6.1.
- Schema / columns / units / field names — selected for 5.2 onward, explicitly non-goal for #256's empty page; pure-function scenarios in viewer-frontend cover these contracts.
- Documentation / migration notes — selected: docs/design.md §7 and §9.1 remain authoritative; snapshot provenance only belongs to 5.3 and README to 5.7.
- File IO / path safety / overwrite — not selected for #256: normal Vite output only, no custom reader/writer or publishing.
- Concurrency / shared state / ordering — not selected for #256: empty page has no asynchronous application state; 5.4–5.6 must preserve map/curve separation.
- Resource limits / large input / discovery — not selected for #256: no data loading.
- Legacy compatibility / examples — not selected for #256: new frontend with no existing consumers; relative asset compatibility is covered above.
- Error handling / rollback / partial outputs — not selected for #256: no runtime data paths; tool failures must exit nonzero.

#256 required evidence: `corepack pnpm --version` → `10.11.0`;
`corepack pnpm install --frozen-lockfile && corepack pnpm typecheck && corepack pnpm test && corepack pnpm build`
→ exit 0, including an empty test suite. HTTP preview of `/` and `/yd/` through
prefix stripping → 200 HTML and referenced JS/CSS. Inspect generated resource
URLs → relative; scan generated text → zero `tianditu`/`tk=` matches.
No permanent tests are required before pure-function task 5.2.

#256 用户裁决：仅允许在 `.large-file-guard.json` 增加
`viewer/frontend/pnpm-lock.yaml` 的生成文件豁免；不提高 1000 行阈值，
不豁免源文件、不改 CI job。先提交并 push docs/spec，再修改 guard 配置。
验证：干净提交副本运行 guard → exit 0；加入 tracked 1001 行前端源文件
→ exit 1；frozen install → exit 0。PR 边界扩展仅限本项及必要文档。

## #263 fixture — task 6.3 only

Expanded (CI config and shared verification entrypoint). Selected packs:
Config / project setup; Public API / CLI / script entry; Release / packaging /
dependency compatibility. Evidence: Node 22 CI checks out the repository,
enters `viewer/frontend`, then frozen install → typecheck → test → build, all
exit 0. A failed command must fail the job, not be ignored. Parse workflow
to prove original producer/viewer-backend/openspec/stage-pipeline-log jobs
are unchanged, then run the actual new GitHub Actions job on the PR.
Secrets/auth not selected: no credentials or secret configuration added.
File IO/schema/concurrency/resource/legacy/error-rollback/docs-migration
not selected: only ordinary CI steps; no runtime/API/publish change.
Must preserve: existing job definitions and their success/failure semantics,
pnpm 10.11.0 from packageManager, frozen lockfile, empty-suite success.
Governing invariant: every CI frontend check runs against this checkout in the
frontend directory using the declared package manager, and failures block CI.
Sibling surfaces: ci.yml, package scripts, packageManager and pnpm-lock.yaml.
Non-goals: Docker/compose, caches, deployments, runtime source edits.
Preserve workflow-level `on:` (`push` to `master`, `pull_request`) and the
`stage-pipeline-log` job's `if:` verbatim. Downstream consumers: all later
frontend PRs (5.2+) are gated here; task 6.1's Node stage uses compatible
frontend commands but remains out of implementation scope.
Explicit #263 oracle: base/head ci.yml → unchanged original job definitions,
triggers and conditional; new `viewer-frontend` job → Node 22 with pnpm 10.11.0
in `viewer/frontend`, `corepack pnpm install --frozen-lockfile` followed by
`corepack pnpm typecheck`, `corepack pnpm test`, `corepack pnpm build` in order;
no continue-on-error or shell suppression → failed step fails the job.
PR CI run → named `viewer-frontend` check succeeds with each command exit 0.

## #257 fixture — task 5.2 only

Expanded (exported response types, runtime JSON parser and shared UI helpers).
Scope: six `src/lib/` modules and their pure-function tests, no new dependencies,
components, fetch side effects, DOM or MapLibre runtime. Downstream consumers:
5.3 snapshot adapters, 5.4 map/colorbar/basemap controls, 5.5 curves/cycle menu,
5.6 header; backend response shapes and 6.1 basemaps writer remain the oracle.
Governing invariant: URLs retain deployment prefix, values/labels share the fixed
five bands, and UTC cycle plus lead yields host-timezone-independent Beijing time.
Preserve 5.1 package/toolchain, relative key-free artifacts and 6.3 CI.
Sibling surfaces: API response types, color function/legend, time/cycle options,
basemap selection/style, Polygon/MultiPolygon traversal.

Selected risks and evidence:
- Public API / CLI / script entry; Schema / columns / units / field names:
  vitest imports real modules; `/yd/` + cycles → `https://h/yd/api/cycles`;
  root deployment retains `/api/cycles`; types match docs/design.md §6.1.
  All eleven color inputs in viewer-frontend spec yield their exact colors;
  threshold and legend agreement tested without configurable themes.
  `2026082712` → `2026-08-27 20:00`; `2026082700` +5 →
  `2026-08-27 13:00`; rollover tested; cycles preserve API order.
- Config / project setup; Error handling / rollback / partial outputs:
  parse vector/satellite → exactly two choices, vector first/default even if
  input order differs; base plus optional annotation raster layers;
  absent/404 payload, `{}`, unknown-only keys → zero choices and empty style.
  No network layer is introduced: later caller maps 404 to absent input.
- Resource limits / large input / discovery: bbox traverses all Polygon and
  MultiPolygon coordinates without spread-argument limits; both example shapes
  yield `[[100,30],[101,31]]`. Geometry validity is backend-owned.
- Documentation / migration notes: existing docs/spec are sufficient; no README
  or snapshot provenance until their issues. Named exported helpers feed later UI.
- Auth/secrets, file IO, concurrency, legacy, release/dependency packs not selected:
  no credentials, IO, shared state, migration or dependency changes.

Required proof: focused vitest cases above fail against the pre-implementation
module tree (missing new APIs is the expected red), then pass with implementation;
all six modules covered by observable boundary scenarios. Parent runs frozen
install/typecheck/test/build; throwaway smoke executes exported helpers without
DOM. No generic framework, validation layer, i18n or theme work.
Explicit sibling cases: cycles `2026082712`, `2026082700` → labels
`2026-08-27 20:00`, `2026-08-27 08:00` in input order; cycle `2026082712`
lead 5 → `2026-08-28 01:00` (Beijing rollover).
Recognized basemap keys are exactly vector/satellite/terrain with that priority;
satellite-only defaults to satellite, terrain-only defaults to terrain.
`annotation: null` from task 6.1 remains a valid choice with only its base layer.

## #258 fixture — task 5.3 only

Expanded: copied UI entrypoints, event lifecycle, dependency and provenance.
User-approved scope clarification: package.json/pnpm-lock.yaml may add only
required dependencies from the documented stack; forbidden-code scan targets
frontend src, dependency declarations and dist, not explanatory docs/SNAPSHOT.
No real credentials may enter any committed file. No new guard exemption.
Pinned source: NWM `4f8d982637f67956acb788b813006e12c1c93174`.
Parent verified all nine target blobs match that pin; copying uses `git show`.

Deliver all named snapshot files from 5.3, adapted rather than placeholder
components: river-only draggable window, controlled chart with ECharts core,
map runtime/builders/interactions/primitives, basemap-only floating controls,
and discharge legend subset. Native MapLibre replaces wrapper-specific JSX
where needed; do not import the full NWM app or its dependency manifest.
Reuse lib/color/time/basemaps/cycles; no duplicate six-band/UTC rules.
Downstream: #259 consumes map helpers/controls/legend, #260 consumes draggable
window/chart, #261 assembles page. Do not mount these in App or fetch APIs here.

Governing invariant: copied UI remains useful without NWM runtime state, secrets
or forbidden subsystems, and cleanup releases listeners/resources it owns.
Preserve drag pointer identity, interactive-control no-drag, container bounds;
river identity is integer reach_id; base style changes cannot erase river overlay
once consumers reapply it. Keep chart's real line rendering, dual-source labels,
all 168 values and Beijing labels; remove upstream 144h IFS truncation/markers.
No fly-to, remembered camera, extra themes, station window or external data client.
Sibling surfaces: NWM imports, local props/types, DOM listeners/unmount, ECharts
registration, MapLibre layers/sources, package+lock, SNAPSHOT provenance.

Selected risks:
- Public API/entry + schema: typecheck all new files against useful controlled
  props and real library types; throwaway bundle imports every snapshot export.
- Release/dependency + config: frozen install/typecheck/test/build succeed with
  MapLibre4.7/ECharts6/echarts-for-react3 and only necessary typings; no new
  state/router/query framework. Existing 21 pure-function tests remain green.
- Auth/secrets: scan src/manifest/dist for prohibited key/domain/import strings
  → zero; scan committed additions for actual credential material without
  printing any upstream key. Credential-bearing upstream lines never copied.
- Concurrency/shared state + resource: read-only audit of drag listener removal
  on stop/cancel/unmount and no cross-window state; no network or shared stores.
- Legacy/provenance + documentation: SNAPSHOT lists full SHA, each of nine
  source→target mappings, retained behavior and deletion/absent disposition
  separately for store, routing, generated client, station UI, precipitation,
  authorization; every target source ≤1000 lines and guard config unchanged.
- File IO/error rollback not selected: no custom persistence/publish/fetch path.

Required evidence: parent frozen install/typecheck/test/build → exit0; temporary
consumer bundle imports all nine file exports using actual React/MapLibre/ECharts
types → build succeeds; source scans and provenance audit above. No permanent
DOM/visual tests or page visibility required by this issue. Runtime browser
interaction is verified when mounted in #259–#261, not claimed for this slice.

## #259 fixture — task 5.4 only

Expanded: first real map page with async fetch, event/state and rendering.
Scope pages/ + map components, no App/main mount, curve window or header.
MapPage owns one latest-map/geometry/basemaps load per mount, a selected reach
and controlled basemap choice; optional onReachSelect/onLatestChange callbacks
serve #260/#261 without fetching those data twice. No caches or polling.
Wait for geometry before creating the map so initial fit has boundary bounds.
Initial fit is not repeated by hover, selection or basemap changes.

Governing invariant: API values map to sorted integer reach_id, not feature
order; base-style replacement restores geometry, colors and selected identity
without refetching or changing latest cycle. Keep #258 shared readiness helper.
Sibling surfaces: relative API/geometry/config URLs, boundary Feature adapter,
map source/layer registration, style.load color replay, hover/click filters,
request abort/unmount, callbacks for future chart/header.

Selected risk packs:
- Public API/entry + schema: fetch ./geometry/rivers.geojson (FeatureCollection),
  ./geometry/boundary.geojson (single Feature), ./api/map/latest and
  ./basemaps.json under root or stripped /yd/; use docs response types. With
  shuffled reaches [3,1,2] and values [0.5,10,1000], actual colors for 1/2/3
  are #7FB8DC/#2171B5/#CB181D, and click returns the actual integer id.
- Config + error/partial outputs: vector/satellite only → exactly2 buttons and
  vector default; 404 or {} basemaps → no buttons but river drawing/highlight
  remain. Latest404 → neutral river colors, no fabricated cycle/data.
- Concurrency/shared state + resource: real Chromium smoke mounts StrictMode,
  exercises hover/click and two style switches, confirms restored colors and
  filters, then unmounts with no errors or stale asynchronous state updates.
  Fetches cancel on unmount; style registration precedes color replay.
- Release/dependency + auth/secrets: existing frozen install/typecheck/21tests/
  build pass, no new dependencies or keys; built requests retain prefix.
- Documentation: record synthetic browser evidence and limits in PR; no M5
  receipt claim or permanent DOM/visual suite.
- File IO/legacy/resource-limits not selected beyond normal4000reach rendering:
  no filesystem, persisted state, migrations, data framework or new budgets.

Required browser evidence: temporary harness mounts exported real page with
synthetic HTTP JSON, not mock MapLibre; canvas/controls/colorbar render; shuffled
ids map correctly; pointer hover/click selects; two basemap changes restore
sources/colors/selection and preserve camera; basemap404 and{} both work.
Record actual requests under /yd/ and unchanged latest fetch count on selection
and style switching. Typecheck/build plus inherited tests remain project gate.
Remove throwaway harness afterward; permanent page entry waits for #261.
Callback contract: `onLatestChange?: (latest: MapLatestResponse | null) => void`;
latest404 invokes it with null, so #261 renders 暂无数据 without another fetch.
Fit oracle: synthetic boundary Feature with bbox `[[100,30],[101,31]]` →
createM11Map initial `fitTo.bounds` is that bbox after geometry load, using its
existing36px padding. Capture resultant camera; hover/select/two style rebuilds
leave it unchanged. No fabricated cycle/source/values on404.

## #260 fixture — task 5.5 only

Expanded: async curve selection and real chart/window interaction.
Scope curve/map-window + chart components only; no MapPage/main/App/header edits.
Export a controlled RiverCurveWindow accepting selected reach_id, current map
cycle, and a close callback; null selection/map cycle means no open window.
#261 wires existing MapPage callbacks; temporary harness wires them here.
Window fetches ./api/cycles for its sole selector and ./api/cycles/{cycle}/reaches/
{reach_id} for curves; no map/latest fetch or mutation. Default selected cycle
is map cycle on a new reach/open; changing cycle affects only window requests.
Use cycleOptions/formatBeijingTime/curveUrl and existing draggable/ForecastChart.
No additional export, source/lead controls, comparison, cache, retries or store.

Governing invariant: displayed reach/cycle/curve always share one request identity;
stale/aborted results cannot replace current selection, and curve history never
changes map cycle, coloring, selected reach, camera or latest request count.
Sibling surfaces: selector options, request identity, loading/404/error state,
chart category labels/series, drag header input guards, unmount/close cleanup.
Downstream: #261 supplies map cycle and reach from existing callbacks, not a
second map/data loader. Preserve #259 map and #258 real chart/rendering contracts.

Selected packs:
- Public API/entry + schema: synthetic cycles [2026082712,2026082700] →
  dropdown [2026-08-27 20:00,2026-08-27 08:00] in that order, default12Z;
  curves for reach1 latest → GFS+IFS168points each on same axes; historical
  source omission → exactly one168point curve, no stale second source.
  2026082700 lead5 x-label → 2026-08-27 13:00. Values remain m³/s.
- Concurrency/shared state + resource: switch A→B while A's response is delayed
  → onlyB renders; new reach resets default to mapcycle and cannot show oldreach
  result. Close/unmount aborts outstanding fetch and draglisteners; select/button
  interactions do not drag window; dragging blank header changes windowposition.
- Error/partial outputs: curve404 → no old series and generic no-data UI;
  other non2xx → generic failure without producer/internal states; no fabricated
  sources/cycles. Unavailable responses must not leave previous data under newlabel.
- Config/dependency + auth/secrets: no new dependency/config; URLs resolve relative
  to document directory for root and /yd/; no credentials; existing21tests and
  frozen install/typecheck/build remain green.
- Documentation: PR records actual synthetic-browser evidence, not M5.
- FileIO/legacy/largeinput/release not selected beyond fixed2×168 chart:
  no persistence, migration, deployment or new resource framework.

Required proof: temporary real Chromium+MapLibre+ECharts harness connects
MapPage onReachSelect/onLatestChange to window. Real river click opens it;
actual ECharts option/render contains one vs two168point series and Beijing
labels; history switch leaves captured map state/camera/latestrequest count
unchanged. Exercise real drag, non-drag selector, close, delayed stale response,
curve404, StrictMode unmount. No permanent DOM test suite or App mount here.

## #261 fixture — task 5.6 only

Expanded: permanent UI entrypoint and assembly state. App plus header are the
feature scope; main.tsx must replace its empty fragment with App as the minimal
entry wiring (otherwise the requested assembly is unreachable). No other page,
chart, helper, proxy, README, dependency or config change.
App owns only selected reach and latest MapLatestResponse|null, populated by
existing MapPage callbacks; header and curve share that latest cycle. Close
sets selected reach null; re-clicking the same river must reopen the window.
Header renders 流量 (m³/s), 起报 and 北京时间; cycle2026082712 →
2026-08-27 20:00. Latest404/null → 暂无数据 with no fabricated time/source.
Header must not render source failure, stopped/degraded or internal status.

Governing invariant: one map owner and one latest fetch path; historical curve
selection cannot change header's latest cycle, map colors/camera or latest
request count. One App state flow feeds existing controlled components.
Sibling surfaces: main StrictMode, App state, MapPage onLatestChange/onReachSelect,
header formatter, RiverCurveWindow null/close/reopen and mapCycle input.

Selected risks:
- Public entry/schema/config: actual production index.html mounts App (not a
  temporary replacement); root and stripped /yd/ requests stay same-origin under
  prefix; header exact12Z→20:00 and unit/labels, latest404→暂无数据.
- Concurrency/shared state/resource: actual river click opens real curve; history
  changes chart only; close and same-reach click reopen. StrictMode dev still
  owns one live map; no new fetching in App/header.
- Error/partial output: latest404 gives no-data header and no curve with fakecycle;
  existing MapPage generic failures preserved, no internal status surfaced.
- Dependency/release/auth: frozen/typecheck/21tests/build pass; no newdeps/keys;
  dist relative resources and forbidden-key scans remain clean.
- Docs: record synthetic production browser evidence/limits in PR, not M5.
- FileIO/legacy/largeinput not selected: assembly only, no new data algorithms.

Required proof: build real App then Vite preview+Chromium with synthetic HTTP
responses; header positive/empty cases, real map+curve, history/header isolation,
close/reopen same reach, root+/yd/ paths and no browser exceptions. No permanent
DOMsuite. Previous temporary harness becomes unnecessary; verify actual entry.
Explicit preservation: no edits to MapPage/RiverCurveWindow/lib/vite/README;
header overlays fullscreen map, adds no document-flow viewport height, reuses
formatBeijingTime; existing callback/null/close semantics and21tests preserved.
Browser I/O: latest12Z → 起报/北京时间/2026-08-27 20:00/流量 (m³/s);
curve00Z selection → header still20:00, map colors/camera identical, latest
request count1 in production. latest404 → 暂无数据, no time/source/fake curve.
Close then click same reach → window reopens. Real dist at `/yd/` requests
`/yd/api/map/latest`, `/yd/geometry/{rivers,boundary}.geojson`, `/yd/basemaps.json`,
then `/yd/api/cycles` and `/yd/api/cycles/{cycle}/reaches/{id}`; root has no prefix.
Dist tianditu.gov.cn/tk= scan →0hits; browser exceptions→0; smoke resources removed.
Downstream5.7 proxy/README and6.1 image copy consume this production entry only.

## #264 fixture — task 6.1 only

Expanded: container entrypoint, filesystem output, permissions and dependency
packaging. Scope viewer/Dockerfile, viewer/entrypoint.sh, tests/test_entrypoint.sh.
No compose/env.example/healthcheck/supervisor/deployment or backend route changes.
Actual app entry is `yd_viewer.app:create_app --factory`, uvicorn fixed host
0.0.0.0 and port8000; YD_VIEWER_PORT cannot change it. Input/output directory
configuration and geometry remain existing backend startup contracts.

Governing invariant: same non-root identity writes only image-owned static
basemaps.json then execs the real viewer; no URL reaches logs and no dependency
installation/network resolution occurs at runtime. Preserve relative frontend
build, public basemap JSON shape, read-only input/output and Python3.12 backend.
Sibling surfaces: six env names, JSON serializer, staticdir ENV/ownership,
frontend parser, uvicorn factory/PID/argv, build/runner venv path and dependencies.
Downstream #265 mounts two directories readonly and maps hostport; #262 uses
packaged backend/frontend contracts. No M5 health or remote receipt claim.

Build: Node22 frozen pnpm10.11 install/build; Python3.12 matching builder/runner,
uv0.9.18 pinned, `uv sync --frozen --no-dev` (non-editable install or preserve
required source explicitly). Copy explicit manifests/source only, not whole repo,
localvenv/node_modules/env. Runtime has no Node/pnpm/uvcache/project devdeps.
Keeping uv executable is permitted for offline/no-project/no-cache stdlib JSON
execution under the repository uv-only rule; no startup sync/download.

Selected risks/evidence:
- Public CLI/config/schema: shell test runs real entrypoint with PATH uvicorn
  sentinel; vector+annotation/satellite -> exact2 keys, annotationarray/null,
  no terrain; annotation-only has no basekey; all six absent ->{} and exec exit0.
  Sentinel records factory/--factory/--host0.0.0.0/--port8000, even PORT env differs.
- FileIO/permissions/auth: tests assert effectiveuid!=0; staticdir is writable
  for image default USER and contains index.html. Quoted/backslash/newline
  synthetic URL round-trips via JSON serializer; `tk=SECRET` absent stdout/stderr.
  Static write failure -> nonzero and no uvicorn exec; no silent stale-config use.
- Release/dependency: repository-root `docker build -f viewer/Dockerfile .` ->
  success; inspect image default USER !=root, Python3.12, import actual yd_viewer,
  no node/pnpm/pytest/ruff/httpx or uv caches. Execute image entrypoint with
  non-root sentinel and synthetic env to verify packaged script too.
- Error/partial outputs: serializer/write error fails beforeexec; no runtime
  retries or secret-bearing diagnostics; shell errexit, no xtrace.
- Resource/concurrency: exec replaces shell, no supervisor/background worker;
  single process initialization only, no publish/locking framework.
- Docs/legacy: existing ops§9.2/spec contract retained; no new public knobs,
  migration, compatibility layer or remote operations.

Required proof: `bash viewer/tests/test_entrypoint.sh` non-root all cases pass;
new script's missing-implementation run fails before source is added; actual
Docker build and image-default-user checks above pass. No mocked Docker build
or fake production fallback; local packaging proof is not a deployment receipt.
Exec proof: test launches entrypoint, captures its PID, and asserts uvicorn
sentinel PID equals that entrypoint PID and effective UID remains non-root;
matching argv alone is not sufficient to distinguish exec from child spawning.
