# M2 Producer 基础（m2-producer-core）

## Why

[docs/design.md](../../../docs/design.md) §10 里程碑 M2 要求交付 node-22 producer 的全部本地可验证代码；这是 M4 node-22 真计算的前置（agent-ops §8.1：CLI 未实现并通过本地测试前，禁止手工拼出等价生产流程）。业务规则已由 [docs/compute-loop-design.md](../../../docs/compute-loop-design.md) 定稿，本 change 把它落为可实现、可审核的 spec 与任务。

## What Changes

- 在 `producer/` uv 项目内实现 `yd-producer` CLI 三入口（`prepare`/`init`/`run`）与 `config.toml`/`local.toml` 双层配置装载；
- 从 NWM 公开仓（DankerMu/SHUD-NWM，pin `8ae9b8f29c8b72c574e8cbd95f2994160bd42832`）精简快照 DB-free canonical converter、file-backend direct-grid forcing、object-store/path 基础函数、IFS/GFS source 与 raw manifest 数据结构，连同其最小测试，逐模块记录来源 commit；
- 实现 `cfg.ic` 原生分段解析、重戳、负残差处理与结构检查；
- 实现 T+12 checkpoint tracker 与 12 小时漏采补跑；
- 实现 IFS/GFS raw 完整性扫描与本轮临时 raw manifest 生成；
- 实现 `run` 控制器：严格前沿推进、非阻塞 flock、Slurm 提交封装、NFS 提交顺序与崩溃恢复、14 天保留清理；
- 实现 `prepare` 的变体生成编排（NWM mapping-builder 薄外壳，fail-closed）与 `rivers.geojson`/`boundary.geojson` 生成；
- 实现 `init` 首态建链（已有状态或 `DONE` 时拒绝）。

无 BREAKING（首个功能 change，此前仓库无业务代码）。

## Capabilities

### New Capabilities

- `cli-config`: `yd-producer` 三入口 CLI 骨架、`config.toml`（版本化业务规则）与 `local.toml`（现场值）装载与校验、NWM 解释器薄外壳 fail-closed 约束。
- `raw-scan`: IFS/GFS 00Z/12Z 0–168h raw 完整性扫描（文件模式、变量、GFS f000 特例）、只读复制到 work、生成本轮临时 `raw-manifest.json`。
- `forcing-chain`: NWM 快照的 DB-free canonical converter 与 source-specific direct-grid forcing 生产（binding 权重 1），work 内临时 registry/manifest 生成，SHUD 输入组装。
- `state-tools`: `cfg.ic` 原生分段（mesh/river/lake）解析、重戳到目标 cycle、负残差归零与域均修正阈值检查、结构检查。
- `checkpoint-tracker`: SHUD 运行期轮询 `cfg.ic.update` 捕获 T+12（relative 720 分钟）checkpoint；漏采时 `END=0.5` 确定性补跑；补跑失败判整轮失败。
- `prepare-variants`: 一次性生成 `yd_gfs`/`yd_ifs` 两个 direct-grid 模型变体与两个 EPSG:4326 GeoJSON；任一变体已存在即拒绝，不提供覆盖。
- `init-bootstrap`: 全新根上从率定末态建立两条 source 状态链（7 天扫描窗、重戳到首轮 T）；已有任一状态或 `DONE` 即拒绝。
- `run-controller`: 日常循环控制器——严格前沿（`DONE` 定位 D → T=D+12h）、未提交残留清理重跑、非阻塞 flock、每源最多一个 Slurm 作业、双源并行互不阻塞、NFS 提交顺序（DAT → 状态 → `DONE` 最后写）、失败日志回收、14 天保留清理（`realpath` 圈定）。

### Modified Capabilities

（无——`openspec/specs/` 为空，本 change 全部为新增。）

## Impact

- 代码：全部落在 `producer/`（包 `yd_producer`）；`viewer/` 不受影响。
- 依赖：canonical/forcing 快照模块引入 numpy/xarray/cfgrib 等运行依赖，随对应 issue 加入 `producer/pyproject.toml`；骨架当前 `dependencies = []` 为刻意留空。
- 外部系统：仅代码引用 NWM 公开仓 pin commit；不连接 NWM 数据库/scheduler/display API（agent-ops §2.2 禁区）。
- 不改变 `YD_ROOT` 产物契约（[docs/products-contract.md](../../../docs/products-contract.md) 保持 v1）。

## Non-goals

- 不在本 change 内实际执行 `prepare`/`init`/`run` 于 node-22（M4 oracle）；不安装 cron；不真实提交 Slurm；不运行 SHUD 二进制。
- 不含 viewer 后端/前端（M3）与 node-27 部署（M5）。
- 工程骨架、CI、仓库 public 已作为流水线前置基建完成（commit `3f18de8`），不属于本 change。
- 不实现 compute-loop-design §2 列明的排除项：旧状态降级、跨轮重戳、冷启动、degraded、状态 registry、血缘 JSON、失败计数、指数退避、下载兜底、常驻服务、自写 watchdog、自动 `scancel`。
