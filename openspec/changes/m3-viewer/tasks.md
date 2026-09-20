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
- [ ] 2.2 `dat.py` 结构层 `read_header(path, reach_ids)`：有界读 `1024+8*(2+nc)` 字节 + `stat` 推算行数；校验整除、168 行、列编号集合；`st` 只解析不用；`DatError` 含路径与差异
- [ ] 2.3 `dat.py` 数据层 `read_dat(path, reach_ids)`：先结构层，再整读 + `array('d')` 解析数据区；第 0 列逐值校验（期望由契约常量推导，不与 2.1 写出算术同式，含 NaN 拒绝）
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
- [ ] 5.2 纯函数模块与 vitest：`lib/api.ts`（相对 URL 拼接与三种响应类型）、`lib/time.ts`（cycle/lead → 北京时间文案）、`lib/color.ts`（≥ 阈值 5 档 + 图例标签，含边界值测试）、`lib/basemaps.ts`（解析 `basemaps.json` → MapLibre 样式；404/`{}`/缺键 → 空样式）、`lib/cycles.ts`（cycles → 下拉项）、`lib/bbox.ts`（boundary GeoJSON → 包围盒）
- [ ] 5.3 M11 快照：从 NWM `4f8d98263` 复制 `M11DraggableCurveWindow`、`ForecastChart` + `echartsCore`、`m11MapRuntime`（去 key，改读 5.2 `lib/basemaps.ts` 的样式）、`m11MapBuilders`、`m11MapInteractions`、`m11MapPrimitives`、`M11FloatingControls`（只留底图切换）、`overviewDataContracts` 的色带/图例子集；逐文件删除 store、路由、OpenAPI client、代站弹窗、降水叠加、RBAC 六类内容并在 `SNAPSHOT.md` 逐文件登记；每文件 ≤1000 行
- [ ] 5.4 地图页：全屏 MapLibre、加载几何、按 `map/latest` 着色、右下 colorbar、右上底图按钮、缩放控件与比例尺、初始视野 fit 到 5.2 `bbox`、hover/selected 高亮
- [ ] 5.5 曲线窗：点击河段打开可拖拽窗，起报下拉（默认地图 cycle）、GFS/IFS 同轴 168 点、x 轴北京时间；切换只重取曲线
- [ ] 5.6 页头：最新起报时间（北京时间，标「起报」）与「流量 (m³/s)」；无数据显示「暂无数据」；`App.tsx` 装配
- [ ] 5.7 本地开发：`vite.config` 代理 `/api`、`/geometry`、`/basemaps.json` 到本地后端；README 一条命令用 2.1 生成器起全栈

依赖：5.1 需 0；5.2 需 5.1；5.3 需 5.1、5.2（`m11MapRuntime` 读 `lib/basemaps.ts`）；5.4–5.6 需 5.2、5.3（响应形状按 spec，可用 mock JSON 开发，不需后端合并）；5.7 需 2.1 与组 4 实际可运行（不可用 mock 顶替）
§9.1 归属：前端
Suggested fixture level: compact - 纯函数 vitest + 构建门禁；不做 DOM 组件测试（用户 Q6）
Minimal mergeable slice: 5.1（脚手架 + 空页面可构建）——不含任何业务代码，可独立合并保绿；5.2 → 5.3 → {5.4, 5.5, 5.6} 串行，5.7 最后

## 6. viewer-container：镜像、entrypoint、compose、CI

- [ ] 6.1 `viewer/entrypoint.sh` + `viewer/Dockerfile`（同一 PR）：entrypoint 按 6 个 env 生成 `$YD_VIEWER_STATIC_DIR/basemaps.json`（缺键缺席、全缺 `{}`、URL 不进日志）后 `exec uvicorn --host 0.0.0.0 --port 8000`（容器内端口固定 8000，宿主端口由 compose `127.0.0.1:${YD_VIEWER_PORT}:8000` 映射），shell 单测以非 root 用户给定 env 断言 JSON 与日志不含 URL；Dockerfile 多阶段（Node 22 pnpm build → Python 3.12 `uv sync --frozen --no-dev` → `ENV YD_VIEWER_STATIC_DIR=<镜像内固定路径>`，该目录属运行用户 → 非 root 执行 `entrypoint.sh`）；`docker build` 通过；镜像内 `USER` 非 root 且静态目录可写含 `index.html`
- [ ] 6.2 `viewer/compose.example.yml`：两个 `:ro` 挂载、`127.0.0.1:${YD_VIEWER_PORT}:8000`、`env_file`、project/service/container/network/image 全 `yd-` 前缀；`viewer/env.example` 列出 `YD_VIEWER_{INPUT,OUTPUT}_DIR`、`YD_VIEWER_PORT`、六个 `YD_BASEMAP_*`，不含 `YD_VIEWER_STATIC_DIR`
- [ ] 6.3 ci.yml 新增 `viewer-frontend` job（install --frozen-lockfile、typecheck、test、build）；现有 job 不变

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
