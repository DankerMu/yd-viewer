# M3 viewer（m3-viewer）

## Why

[docs/design.md](../../../docs/design.md) §10 里程碑 M3 要求交付 node-27 yd-viewer 的全部本地可验证代码：只读消费 `YD_ROOT` 的 `input/viewer` 与 `output`，以单容器 FastAPI + 构建后 React 提供四个 API、同源几何静态文件与 m11 最小 UI。M2 已归档（producer 全部落地），M3 与 M4 互不依赖，是 M5 node-27 真闭环的前置。

本项目是**简化项目**：只做 design §2 列出的能力，§2「明确不做」一律不做，不建缓存层、抽象框架或预留扩展点。

## What Changes

- 在 `viewer/` uv 项目内实现 FastAPI 后端：env 配置、启动几何自检、SHUD v2 DAT 两层读取（枚举期只读头 + stat 校结构；取数时整读校分钟列）、`DONE` 目录枚举与 7 天窗、`GET /api/cycles`、`GET /api/map/latest`、`GET /api/cycles/{cycle}/reaches/{reach_id}`、`GET /api/health`、`/geometry/*` 静态几何、SPA 构建物托管。
- 新建 `viewer/frontend/`（Vite + React 18 + TypeScript + MapLibre GL 4 + ECharts 6 + Tailwind，`corepack pnpm`）：全屏地图按最新 cycle 流量着色、5 档色带与 colorbar、三底图切换（运行时 `basemaps.json`）、hover/selected、可拖拽曲线窗（起报下拉、GFS/IFS 同轴、168 点、北京时间）、页头最新起报时间；从 NWM `4f8d98263` 复制并精简 M11 组件，记录来源。
- 新建单镜像 Dockerfile（多阶段：pnpm build → uv 安装 → 非 root uvicorn）、entrypoint 按 6 个 URL 模板 env 生成 `basemaps.json`、compose 示例（两个 `:ro` 挂载、`127.0.0.1` 回环端口）；CI 新增 `viewer-frontend` job。
- docs-first：先修订 products-contract §5.2/§6（viewer 侧校验与几何完整性判据）、design §6.1/§7/§11（错误模型、曲线响应形状、health body、几何静态路径、底图注入机制、色带分档、`/yd/` 归属待定）并合并，再动码。

无 BREAKING：`viewer/` 此前只有包名骨架；producer 与产物契约不变（viewer 侧新增校验不改变 producer 义务）。

## Capabilities

### New Capabilities

- `viewer-config-health`: env 配置（输入、输出、静态三个目录）、启动几何自检（两份 GeoJSON、`reach_id` 集合）、`GET /api/health` 语义。
- `viewer-dat-reader`: SHUD v2 `yd.rivqdown.dat` 两层读取——结构层有界读头 + stat（`nc`、列编号表、行数 = 168、列编号集合 = 几何 `reach_id` 集合）；数据层整读并校验第 0 列 `0..10020`、m³/day → m³/s 换算、按 lead 取行 / 按 reach 取列。
- `viewer-catalog`: `output/<cycle>/<source>/DONE` 枚举、cycle/source 命名校验、以最新 `DONE` cycle 为锚的 7 天窗、不合规 DAT 的 source 排除（WARNING 日志）。
- `viewer-api`: 四个端点的请求/响应形状、GFS 优先规则、错误码（空态 404、越界 400、挂载不可读 503、FastAPI 默认 `detail`）、`/geometry/*` 静态几何、SPA 托管与相对路径部署。
- `viewer-frontend`: 地图着色、色带、colorbar、底图切换、hover/selected、曲线窗、页头、相对 base、纯函数测试门禁、M11 快照来源登记。
- `viewer-container`: Dockerfile、entrypoint 生成 `basemaps.json`、compose 示例、CI `viewer-frontend` job 与本地 `docker build` 门禁。

### Modified Capabilities

（无——现有 `openspec/specs/` 全部为 producer 能力，viewer 侧无既有 spec。）

## Impact

- 代码：全部落在 `viewer/`（Python 包 `yd_viewer`、`viewer/frontend/`、`viewer/Dockerfile`、`viewer/compose.example.yml`）与 `.github/workflows/ci.yml`（新增一个 job）。`producer/` 零改动。
- 依赖：后端只加 `fastapi`、`uvicorn[standard]`（dev 加 `httpx`）；不加 numpy、pydantic-settings、缓存或 ORM。前端依赖对齐 NWM 版本；不装 zustand、react-router、react-query。
- 文档：products-contract §5.2/§6、design §6.1/§7/§11 先改后码（契约 §9 变更规则：viewer 侧判据属新增条款，不改变 producer 义务与目录布局）。
- 外部系统：不连接 NWM DB/display API/scheduler；不复制 NWM 天地图 key；部署（镜像装载、端口、Nginx）归 M5。
