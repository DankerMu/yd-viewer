## Why

node-27 上 yd 与 NWM 各自缓存同一批天地图瓦片（同 key、同上游、同 `<layer>/<z>/<x>/<y>` 布局），两份缓存都只增不减、无上限，yd 那份还在根分区的 docker 命名卷里。用户裁决（2026-09-25）：两边共用 `/home` 下 NWM 的缓存目录，冷瓦片（mtime 30 天未刷新）由 NWM 每日清理（DankerMu/SHUD-NWM#2627），yd 不清理，只在命中时刷新 mtime。docs 已先行（design §6.1、agent-ops §9.2/§16.2，PR #353）。Closes #354。

**硬约束：简化项目，不过度设计。**

```text
Issue type: feature
Fixture level: expanded
Upstream suggested level: compact (override: 触及共享 entrypoint、与 NWM 共享的持久化目录上的文件 IO、权限（umask/group_add）、生产 compose 配置与既有反代行为兼容，均为 expanded 触发项)
Blast radius: 权限错 → NWM 无法清理或写入 yd 建的目录、yd 命中后反复回源；刷新错 → 热瓦片被 NWM 当冷瓦片删除或每次命中都写盘；反代既有行为回归 → 底图白屏
Selected risk packs: Config / project setup; File IO / path safety / overwrite; Auth / permissions / secrets; Concurrency / shared state / ordering; Error handling; Legacy compatibility
Evidence floor: viewer ruff + pytest + test_entrypoint.sh 全绿；openspec validate --strict；容器内 PID 1 Umask 0002 与非属主经组权限刷新 mtime 的实测；未命中 775/664 以 umask 间接证明，现场直接证据归 node-27 部署 receipt
```

## What Changes

- 反代命中时，若缓存文件 mtime 早于当前 24 h 以上，`os.utime(path)` 刷新；`OSError` 吞掉，响应不变。
- `viewer/entrypoint.sh` 在 `set -eu` 之后、任何写盘之前执行 `umask 002`，使新建缓存目录 775、文件 664（共享目录组可写）。
- `viewer/compose.example.yml`：缓存由命名卷 `yd-basemap-cache` 改为可写 bind（占位路径）并加 `group_add`（占位 gid）；删除顶层 `volumes:`。
- 合同测试随之更新。

不做：清理/配额/监控、bbox 或 z 上限、新 env、Dockerfile 改动、node-27 部署（另行授权）、NWM 代码。

## Capabilities

### Modified Capabilities
- `viewer-api`：天地图瓦片反代——命中刷新 mtime。
- `viewer-container`：entrypoint `umask 002`；compose 缓存挂载形状。

## Impact

- `viewer/src/yd_viewer/basemap.py`、`viewer/entrypoint.sh`、`viewer/compose.example.yml`、`viewer/tests/`（basemap 与容器合同测试）。
- 设计要点、不变量与兄弟面见 design.md；risk pack 逐项取舍见 tasks.md。
- Must preserve：反代现有全部场景（未命中写入、失败不缓存、限流冷却、参数校验、无 key 404、key 不进日志）；basemaps.json 生成与 URL 不进日志；两条 `:ro` 挂载、端口映射、`yd-` 前缀；镜像预建 `/cache` 属运行用户。
- Required evidence：见 tasks.md §4（每条给出输入与预期输出）。
