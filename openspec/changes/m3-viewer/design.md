## Context

M2 producer 已归档，`YD_ROOT` 契约（[products-contract.md](../../../docs/products-contract.md)）稳定：viewer 只读 `input/viewer/{rivers,boundary}.geojson` 与 `output/<cycle>/<source>/{yd.rivqdown.dat,DONE}`。`viewer/` 当前只有包名骨架；`viewer/frontend/` 不存在；ci.yml 有 `viewer-backend` job，无前端 job。前端组件来源为本机 NWM checkout `4f8d98263`（2026-09-19）：M11 组件全用 Tailwind class，ECharts 走 `echartsCore.ts` tree-shaking，底图为天地图 vec/img/ter + cva/cia/cta 双层栅格样式，默认 key 硬编码在 `m11MapRuntime.tsx:50`（不得复制）。

约束：简化项目、不过度设计；Python 一律 `uv`，前端 `corepack pnpm`；docs-first；部署与 live receipt 归 M5；应急 yd-NWM 副本当前占用公网 `/yd/`（agent-ops §14.6）。

Stage 1 grill 已拍板分支（用户）：D1 无 DONE 标记、启动自检；D6 entrypoint 生成 `basemaps.json`；D7 5 档色带；Q2 整读 + stdlib；Q3 坏 DAT 排除 source；Q4 CI 合成几何；Q5 Tailwind 原样复制；Q6 前端只测纯函数；Q7 容器归 M3；Q10 曲线响应形状；Q11 reach 权威 = 几何；Q8/Q12 打包细节；Stage 3 复审后追加 R1（用户）：两层校验——枚举期只读头 + stat，取数时才校分钟列。开放项：D10 `/yd/` 归属延 M5。

## Goals / Non-Goals

**Goals:**

- design §10 M3 五项：v2 DAT 解析与换算、`DONE` 契约枚举与 7 天窗、四个 API、m11 最小 UI 快照、前端构建门禁。
- 本地门禁全绿：`uv run pytest` + ruff、`tsc --noEmit` + `vitest run` + `pnpm build`、`docker build`。
- 每个交付物都可用合成 `YD_ROOT` 树本地验证；不依赖 node-22 真产物。

**Non-Goals:**

- design §2「明确不做」全部：PostgreSQL/Redis/ingest/消息队列/MVT；`meta.json`/`status.json`/degraded/运维页；气象代站、面雨量、单元变量图层；viewer 内 shapefile/GDAL 转换；NWM 登录/RBAC/全局 store/多流域/`/ops`；SHUD v1 兼容与残行修复。
- 缓存层（内存或磁盘）、抽象数据源接口、插件化底图、多语言、主题切换、响应式移动布局、可配置色带。
- `input/viewer/DONE` 标记或任何 producer 侧改动；`prepare` 半提交恢复程序归 agent-ops 文档（本 change 的 docs 任务只登记判据，不写恢复流程）。
- node-27 部署、端口、Nginx `/yd/`、真实天地图 URL、镜像装载与浏览器 receipt（M5）。
- 前缀归属：`/yd/` 由应急副本还是主线持有，M5 前另行裁决；本 change 只保证任意前缀可挂。
- DOM 级组件测试、Playwright、视觉回归。

## Not yet specified

- 无。范围内可见的工作已全部切入 specs/tasks；`/yd/` 归属是范围外部署决策（Non-Goals），M5 承接。

## Decisions

1. **DAT 读取分两层，均只用 stdlib `array`/`struct`，不加 numpy**（用户 Q2 + R1）。
   - **结构层**（catalog 枚举期，每候选一次）：有界读前 `1024 + 8*(2+nc)` 字节得到 `nc` 与列编号表，`stat` 文件大小反推数据区行数；校验行数 = 168 且列编号集合 = 几何 `reach_id` 集合。每候选约 33 KB。
   - **数据层**（map/latest 与曲线取数时，每请求一个或两个文件）：整读 5.4 MB，校验第 0 列逐值 = `i*60`，再取行/列并换算。
   备选「每候选整读全校验」代码最少但 7 天窗约 30 候选 × 5.4 MB ≈ 150 MB/请求（Review 1 发现，用户改选两层）；numpy 更快但多一个依赖。
2. **不合规 DAT → 该 source 不可用 + WARNING**（用户 Q3 + R1）。`DONE` 仍是唯一完成判据（契约 §4.1）。结构层失败：catalog 排除该 source（不出现在 cycles）。数据层失败：该 source 对本请求不可用——`map/latest` 回落到同 cycle 的另一 source，再回落到更早 cycle；曲线响应省略该 source 键，全部失败则 404。两层都记 WARNING（路径 + 原因），MUST NOT 传播为 5xx。代价：只有分钟列坏的文件会先出现在 cycles 列表里再在取数时被拒，用户已接受。备选 500 会把 producer 缺陷变成页面故障。
3. **reach 权威 = `rivers.geojson` 的 `reach_id` 集合，不设 `YD_REACH_COUNT`**（用户 Q11）。启动读几何一次得到集合；DAT 头部列编号表建 `reach_id → 列下标` 映射（不假设位置 = 编号）。少一个配置项，几何与 DAT 互证。
4. **几何就绪判据在 viewer 侧**（用户 D1）：启动时两份 GeoJSON 必须可解析、`rivers` 为 FeatureCollection 且每个 Feature 有整数 `properties.reach_id` 且无重复、`boundary` 为单 Feature Polygon/MultiPolygon；任一失败 → 进程启动失败（fail closed，容器 health 自然失败）。不加 `input/viewer/DONE`：M2 已归档，加第五终名要重开 producer 与契约 §9。
5. **配置只走 env**：`YD_VIEWER_INPUT_DIR`、`YD_VIEWER_OUTPUT_DIR` 为运维可配（compose `env_file`）；`YD_VIEWER_STATIC_DIR` 由 Dockerfile `ENV` 固定为镜像内前端 dist 路径，MUST NOT 出现在 `env.example`/compose 可覆盖变量里（本地开发才手动设置）；`YD_VIEWER_PORT` 只是 compose 的宿主端口变量（`127.0.0.1:${YD_VIEWER_PORT}:8000`），容器内 uvicorn 固定监听 8000。三个目录缺失/非目录/不可读即启动失败并打印变量名与路径。entrypoint 与 uvicorn 同一非 root 用户，`STATIC_DIR` 对该用户可写（写 `basemaps.json` 的是镜像内文件系统，不是只读挂载）。不用 pydantic-settings。
6. **错误模型 = FastAPI 默认 `{"detail": "..."}`**（用户 Q8）：空态 `cycles` 返回 `[]`；`map/latest` 与曲线在无可用 cycle 时 404；cycle 非 `^\d{10}$` 或小时 ∉ {00,12}、`reach_id` 不在集合时 400（路径参数不合法归 422 由 FastAPI 自动给出，spec 以「4xx 且不为 5xx」钉死）；`output/` 不可枚举 503。
7. **health**（用户 Q8/Q12）：`200 {"status":"ok","latest_cycle":"YYYYMMDDHH"|null}` 当且仅当 `output/` 可枚举（几何自检在启动期，失败进程不存在）；`output/` 不可枚举 → 503。
8. **曲线响应** `{"cycle","reach_id","lead_hours":[0..167],"series":{"gfs":[168],"ifs":[168]}}`（用户 Q10）：缺源省略键；时间由前端按 `UTC(cycle)+lead` 计算。`map/latest` 按 design §6.1 示例：`{"cycle","source","valid_time","values":[按几何 reach_id 升序]}`。
9. **几何静态路径** `/geometry/rivers.geojson`、`/geometry/boundary.geojson`（用户 Q8）：FastAPI `StaticFiles` 直接挂 `YD_VIEWER_INPUT_DIR`，不复制；ETag 由 StaticFiles 提供。
10. **SPA 托管**：`YD_VIEWER_STATIC_DIR` 挂到 `/`，`/api/*` 与 `/geometry/*` 先于它注册；不做 history fallback（单页无路由）。前端 `base: './'` + 相对 fetch，根路径与 `/yd/` 剥前缀共用同一构建物（design §6.1）。
11. **底图运行时注入**（用户 D6/Q9）：容器 entrypoint 读 `YD_BASEMAP_{VECTOR,SATELLITE,TERRAIN}_URL`（必填三选任意）与 `YD_BASEMAP_{VECTOR,SATELLITE,TERRAIN}_ANNOTATION_URL`（可选），写 `<static>/basemaps.json`：`{"vector":{"tiles":[url],"annotation":[url]|null},...}`，缺底图 env 则该键缺席；前端启动 fetch `./basemaps.json`，缺席键不出现在切换按钮，全部缺席用空样式（纯色背景）。不复制 NWM key；不做 `/api/config`。
12. **色带 5 档，阈值取 ≥（沿用 NWM `m11DischargeColor` 的比较方向）**（用户 D7）：`v ≥ 1000 → #CB181D`；`100 ≤ v < 1000 → #08519C`；`10 ≤ v < 100 → #2171B5`；`1 ≤ v < 10 → #4292C6`；`v < 1 → #7FB8DC`；`null → #94ADC7`。图例标签 `<1`、`1–10`、`10–100`、`100–1000`、`≥1000`（副本 patch 6 口径，去掉 NWM 的 1000–10000 档）。
13. **地图基础交互**：缩放控件、比例尺、初始视野 fit 到 `boundary` 包围盒（design §7 「地图缩放和比例尺」「全屏地图」），无其它相机逻辑。
14. **前端栈与复制来源**：Vite 6 / React 18.3 / TS 5.9 / MapLibre 4.7 / ECharts 6 / echarts-for-react 3 / Tailwind（版本随 NWM）/ pnpm 10.11；来源 NWM `4f8d98263`，复制清单与删减记入 `viewer/frontend/SNAPSHOT.md`。不装 zustand/react-router/react-query；状态用 `useState`。`overviewDataContracts.ts` 只取色带/图例部分并拆到 <1000 行（large-file-guard 不新增豁免）。
15. **前端测试只测纯函数**（用户 Q6）：色带映射、相对 URL 拼接、北京时间格式化、`basemaps.json` 解析、cycles → 下拉项、boundary 包围盒六个模块；MapLibre/ECharts 组件不做 DOM 测试。docs/design.md §9.1「前端」行的「组件测试」由 0.2 改为「纯函数测试」。
16. **时间**（用户 Q8/Q12）：API 一律 UTC `Z`；页面所有时间按 `Asia/Shanghai` 显示并标「北京时间」；页头显示最新可用 cycle 的起报时间，标「起报」；曲线 x 轴为 `UTC(cycle)+lead` 的北京时间。
17. **容器归 M3**（用户 Q7）：多阶段 Dockerfile（node 22 pnpm build → python 3.12 uv sync → 非 root uvicorn）、`compose.example.yml`（两个 `:ro` 挂载、`127.0.0.1:<port>`、env_file）；`docker build` 为本地门禁，部署归 M5。
18. **docs-first 顺序**：任务组 0 的 docs 修订必须先合并（契约 §9、CLAUDE.md），任何代码 issue `Depends on` 它。
19. **合成 `YD_ROOT` 生成器放 `viewer/tests/`**，不依赖 producer 包（复制 v2 写出约 30 行）；可参数化行数、列编号、第 0 列、`st` 头、DONE 为 symlink；前端 dev 用同一生成器产物经后端代理。
20. **12Z 锚**：绝对时间只由 `UTC(cycle)+相对分钟` 计算，v2 `st` 日期头只解析不使用（契约 §5.2 MUST NOT）；合成 fixture 故意写错 `st` 以保证判别力。
21. **compose 命名**：project/container/network/image 均带 `yd-` 前缀（agent-ops §9.2），示例文件即示范。

## Sketch seams under test

从高到低，每 seam 一行理由：

1. `FastAPI` app 经 `httpx`/`TestClient` 对合成 `YD_ROOT` 树——四个端点、错误码、静态几何与 SPA 托管的唯一验证路径，覆盖 catalog + dat + geometry 的集成。
2. `dat.read_header(path, reach_ids) -> DatHeader`（有界读 + stat）与 `dat.read_dat(path, reach_ids) -> DatFile`（整读）两个文件级纯函数——结构层与数据层校验是时间轴正确性的根，坏文件矩阵在此钉死。
3. `catalog.list_cycles(output_dir, reach_ids) -> list[CycleEntry]`（tmp 目录树）——窗口、排序、排除规则独立于 HTTP 层。
4. `geometry.load_geometry(input_dir) -> Geometry`（tmp 文件）——启动自检的失败矩阵。
5. 前端纯函数模块（`lib/color.ts`、`lib/api.ts`、`lib/time.ts`、`lib/basemaps.ts`、`lib/cycles.ts`、`lib/bbox.ts`）经 vitest——唯一有判别力的前端自动化边界。
6. `viewer/entrypoint.sh`（shell 单测：以非 root 用户、给定 env 断言生成的 JSON）——D6 唯一可验证处。

## Risks / Trade-offs

- [枚举期每候选约 33 KB × ≤30 候选，取数期每请求 1–2 × 5.4 MB 整读，无缓存] → 低流量页面可接受；若 M5 实测响应 >1s 再议，不预先优化。
- [分钟列坏的文件会先列入 cycles 再在取数时被拒] → 用户已接受；取数失败回落 + WARNING，页面不 5xx。
- [启动自检失败即进程退出，容器反复重启] → 这是 fail closed 的预期行为；compose `restart` 策略与日志在 M5 receipt 里核；错误信息必须打印精确路径与原因。
- [坏 DAT 静默排除可能掩盖 producer 缺陷] → WARNING 日志带路径与原因；#109 已在 producer 侧加了同样校验，双保险。
- [Tailwind 引入构建配置] → 只用 NWM 已有的 class 子集，不加插件；不做主题。
- [NWM 组件复制后独立维护] → `SNAPSHOT.md` 记来源 SHA 与删减，不追踪上游。
- [真实几何 CI 拿不到] → 合成几何覆盖全部判据；真实几何本地手工核一次记入 PR。

## Migration Plan

无迁移：`viewer/` 此前无业务代码。回滚 = 不部署（M5 之前无线上实例）。

## Open Questions

- 无范围内未决项。`/yd/` 归属（D10）为 M5 部署决策，见 Non-Goals。
