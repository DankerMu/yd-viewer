## 1. 固定 native 输入的运行时格式切换

- [ ] 1.1 在既有 prepare handoff 中实现 D1/D2 的固定 14 文件 v2 合同；迁移 `yd.para` 到 `yd.cfg.para` 及 prepare/fixture 消费者，保留顶层 calibrated state 与原有 direct-grid/identity 字段；five-only 明确要求重建，不增加通用角色/路径系统。
- [ ] 1.2 将既有 staged 搬运/file-checksum map 切换为该固定完整变体与 cycle state；保持 WorkClaim、既有 no-follow/cleanup 代码，不新增第二层 snapshot/验证框架。
- [ ] 1.3 `assemble_staged` 复用共享 kernel 写入 `model/input/yd`；按实际 RunDirectory 字段输出当前 state、原生参数与 forcing index，CSV 仍在 model 根且 index path=`.`；迁移所有本切片调用者及已有 tests，保持独立 legacy external-root assembly 合同。
- [ ] 1.4 给共享参数 writer 增加明确的 native 输出模式，正确读取空白分隔的 cfg.para 并输出 stock `%s %lf` 可读的六项参数；保留 legacy 模板模式，主运行/恢复均使用 native 模式，不只改文件名。
- [ ] 1.5 用同一 staged→native assembly 路径证明完整文件、T 初态覆盖、forcing CSV 可按 stock reader 规则打开、source 断开后仍可运行；沿用已有真实失败测试验证缺文件/旧包不进入 worker，运行 project-profile 门禁。

Depends on #171
Depends on #177
Suggested fixture level: expanded - 已有文件格式与 native 路径跨 prepared/staged/assembly 消费者切换；主要风险是计算输入与状态路径，不新增安全平台。
Minimal mergeable slice: atomic - 这一固定格式同时被 prepare、staged loader 和 assembly 消费；拆成逐文件 PR 会让生产消费者暂时拒绝/漏搬同一完整变体。一个 staged→assembly 验收路径覆盖整条格式边界。
Width exception: merged-tasks - 1.1–1.5 同属一个完整 native 输入格式切换，无法交付半套读写格式；shared helper 和 legacy assembly 不重构。

## 2. 真实 prepare-only builder driver

- [ ] 2.1 实现随 yd 分发的薄 driver：从固定 NWM grid 文件调用 DB-free reader/snapshot preparation，复用 mapping/index、sp.att 重写、Z sampler 和 binding emitter，输出组 1 的两个完整 source 变体；不调用 resolution-only CLI，不构造 NWM 平台审批/QA/UUID/evidence 记录。
- [ ] 2.2 将 `prepare.default_builder` 接入该真实 driver，`invoke_mapping_builder` 使用固定 NWM interpreter 和 yd driver 绝对脚本路径；执行 #45 的现有 exec/env 义务，移除 obsolete `nwm_mapping_builder_module`/builder-unavailable 代码和调用者。
- [ ] 2.3 baseline GIS 改用模型目录的 `gis/river.shp`/`domain.shp` 与 sidecar；沿用现有 GeoJSON writer 和四终名事务；仅在 prepare 检查当前 yd 不支持的非零 BC/SS/LAKE，不引入条件资产解释器。
- [ ] 2.4 从真实 CLI prepare 路径证明两个 source 的真实 binding/重写/native 文件/GeoJSON 和一个真实 mapping 失败不发布；本地对固定 NWM 库执行 smoke，禁止用 shell 假成功代替 driver。运行 project-profile 门禁。

Depends on: group 1
Refs #45
Suggested fixture level: expanded - 一次性外部解释器调用、mapping 科学输入和 prepare 事务；不覆盖日常 Slurm/controller 生命周期。
Minimal mergeable slice: atomic - driver、入口与 native/GIS 输出组成一个 CLI prepare 用户路径；单独交付无人调用的脚本或仍抛 unavailable 的入口不解决缺口。
Width exception: merged-tasks - 2.1–2.4 都只验证 prepare 这一路径；exec/config/GIS 改动是这个入口的直接依赖，不引入独立产品功能。

## 既有 issue 路由（非新任务组）

既有 #132：吸收两个前置并迁移 worker/receipt/recovery 中的实际 RunDirectory 路径；不新增 controller/JobRecord API，继续原 review ledger 完成审核及 CI，不重置轮次。本 change 不重复创建 #132。

既有 M4：在明确授权后使用真实 yd baseline/binary，取得两 source 的 00Z/12Z、7 日输出、T+12/下一轮与 NFS receipt。本地 driver/native 结构证明不能替代它；此路由不作为组 1/2 的本地实现 checkbox，也不把现场部署塞入子 PR。

组 1/2 完成即解除 #132 的 native 业务代码前置；#202 的实现子项与 M4 receipt 分栏记录。M4 需消费集成后的 #132，不反过来成为本地 #132 的代码合并依赖，避免循环依赖。

#202 为父项，组 1 → 组 2 → #132 是实际依赖；两个子项必须各自标明单一路径、文件范围、上述 fixture level 与 atomic 理由。原 #171/#177 为已完成依赖，不重新打开。Native/business adapter 必须全部完成才可销 #202/#132；M4 不再承担缺失代码。

禁止把新设计扩成通用包管理、动态 registry、可配置资产角色、审批链、旧格式兼容或多层 checksum 框架。现有合并后的 cleanup/publish/work-identity 保障刻意不动。真实模型/配置不入 Git，现场操作不由本地测试授权。
