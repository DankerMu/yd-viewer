## Why

#132 / PR #197 的生产 worker 没有 stock SHUD 所需的完整输入：#171/#177 只交接五文件，assembly 使用平铺目录和非原生 `yd.para`。同时真实 prepare builder 仍是显式失败分支。#202 补齐这两段业务代码，不能把它们留给 M4 现场验证。

用户已要求按一个 yd 小流域简化：固定文件、IFS/GFS 两个变体、复用 NWM 算法；不建设通用模型包/registry/审批平台，不扩张既有防御层。

## What Changes

- **BREAKING**：完整 yd 变体取代 five-only carrier；原有 direct-grid handoff 升级一版，保留既有身份字段与 opaque binding，固定携带 native 文件，不增加可配置资产角色/任意目标路径。旧变体重新 prepare，不自动升级。
- staged-input 继续沿用既有 work-local 搬运、checksum 与 owner，不另建缓存、registry 或多层验证；文件集合改为固定 native 清单。
- `assemble_staged` 物化 `model/input/yd/`，用当前 T 状态、原生 `yd.cfg.para` 和本轮 forcing 替换对应输入；运行 cwd 仍为 `model/`。保持 #197 的显式 `-o`、DAT 与 checkpoint 归属。
- prepare 在固定 NWM 解释器中运行 yd 自己的薄 driver，只复用 NWM 的文件网格读取、几何/mapping、sp.att 重写与 binding 库。不接 NWM DB，不照搬其平台审批/evidence/rollback 生命周期。
- baseline 参数明确指向包含 `yd.*` 与 `gis/` 的模型目录；GIS 使用实际 `river.shp`/`domain.shp`，不用合成 `rivers.shp`。
- 业务实现全部归 M2；M4 只负责真实环境运行与 22/27 receipt。既有清理、发布、flock、安全 helper 的独立合同不在本次重构。

## Capabilities

### New Capabilities

- `yd-native-input`: 单流域固定 native 变体从 prepare handoff、work-local staging 到 stock SHUD 输入目录的交接。
- `yd-native-builder`: 使用固定 NWM 环境和已有 DB-free mapping 库的真实 prepare driver。

### Modified Capabilities

无已归档 canonical capability 被改名。本 change 明确取代 active `m2-producer-core` D19/D20 的 five-only/flat-layout 细节及 prepare builder 留给 M4 的旧归属；旧 issue fixture 只作其历史提交的验收依据。既有 state、清理和 DONE 合同不变。

## Impact

- runtime 切片：`prepare_handoff.py`、`prepare.py` 的变体文件合同、`staged_inputs.py`、`assemble.py` / `_assemble_io.py`，以及 `tracker/checkpoint_tracker.py` 的 native RunDirectory 接受/恢复模式调用点和相关 fixtures。#132 的 worker/receipt 只消费已迁移的 tracker，不加新 controller API，不扩大其六文件边界。
- builder 切片：`prepare.default_builder`、`nwm.invoke_mapping_builder`、一个 prepare-only driver、必要的版本化配置与 CLI 接线。顺带完成 #45 在真实调用处的既有解释器/env 义务，不增设新配置平台。
- docs：`products-contract.md`、`compute-loop-design.md`、`agent-ops.md`、active M2 design/tasks/specs 的优先级与阶段归属说明。
- 不修改 NWM checkout、环境或服务；不提交真实模型/节点配置。#202 作为父项按真实模块依赖拆分，全部完成前 #132 不合并。
