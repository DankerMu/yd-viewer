## 1. 固定 native 输入的运行时格式切换

Implementation issue: #207

- [x] 1.1 在既有 prepare handoff 中实现 D1/D2 的固定 14 文件 v2 合同；迁移 `yd.para` 到 `yd.cfg.para` 及 prepare/fixture 消费者，保留顶层 calibrated state 与原有 direct-grid/identity 字段；five-only 明确要求重建，不增加通用角色/路径系统。
- [x] 1.2 将既有 staged 搬运/file-checksum map 切换为该固定完整变体与 cycle state；保持 WorkClaim、既有 no-follow/cleanup 代码，不新增第二层 snapshot/验证框架。
- [x] 1.3 `assemble_staged` 复用共享 kernel 写入 `model/input/yd`，CSV 留 model 根、index path=`.`；同一切片迁移 `tracker/checkpoint_tracker.py` 的 native RunDirectory 私有路径检查及 native 恢复调用，并更新 `test_checkpoint_tracker.py`/`test_checkpoint_recovery.py` 等相关 fixtures。保留独立 legacy external-root assembly 和其平铺路径约束，不将 tracker 改动留给 #132。
- [x] 1.4 给共享参数 writer 增加明确的 native 输出模式，正确读取空白分隔的 cfg.para 并输出 stock `%s %lf` 可读的六项参数；保留 legacy 模板模式，主运行/恢复均使用 native 模式，不只改文件名。
- [x] 1.5 沿同一 staged→native assembly→tracker 路径证明完整输入、T 初态、stock 参数/CSV 语法与 source 断开后可运行；覆盖已捕获 checkpoint 的零恢复调用分支和 genuine-miss END=0.5/参数原 bytes 还原。沿用已有缺文件/旧包失败与 project-profile 门禁，不再建 helper 级重复矩阵。

Depends on #171
Depends on #177
Suggested fixture level: expanded - 已有文件格式与 native 路径跨 prepared/staged/assembly 消费者切换；主要风险是计算输入与状态路径，不新增安全平台。
Minimal mergeable slice: atomic - 这一固定格式同时被 prepare、staged loader 和 assembly 消费；拆成逐文件 PR 会让生产消费者暂时拒绝/漏搬同一完整变体。一个 staged→assembly 验收路径覆盖整条格式边界。
Width exception: merged-tasks - 1.1–1.5 同属一个完整 native 输入格式切换，无法交付半套读写格式；shared helper 和 legacy assembly 不重构。

## 2. 真实 prepare-only builder driver

Implementation issue: #208

- [ ] 2.1 实现随 yd 分发的薄 driver：从固定 NWM grid 文件调用 DB-free reader/snapshot preparation，复用 mapping/index、sp.att 重写、Z sampler 和 binding emitter，输出组 1 的两个完整 source 变体；不调用 resolution-only CLI，不构造 NWM 平台审批/QA/UUID/evidence 记录。
- [ ] 2.2 将 `prepare.default_builder` 接入真实 driver，由 run_prepare 显式绑定 local；`invoke_mapping_builder(local, args, runner)` 使用固定解释器/脚本，移除仅为旧 module 使用的 Config 参数；执行 #45 的既有 exec/env 义务，删除 obsolete module 配置、builder-unavailable 代码并迁移调用者。
- [ ] 2.3 baseline GIS 改用模型目录的 `gis/river.shp`/`domain.shp` 与 sidecar；沿用现有 GeoJSON writer 和四终名事务；仅在 prepare 检查当前 yd 不支持的非零 BC/SS/LAKE，不引入条件资产解释器。
- [ ] 2.4 从真实 CLI prepare 路径证明两个 source 的真实 binding/重写/native 文件/GeoJSON 和一个真实 mapping 失败不发布；本地对固定 NWM 库执行 smoke，禁止用 shell 假成功代替 driver。运行 project-profile 门禁。

Depends on #207
Refs #45
Suggested fixture level: expanded - 一次性外部解释器调用、mapping 科学输入和 prepare 事务；不覆盖日常 Slurm/controller 生命周期。
Minimal mergeable slice: atomic - driver、入口与 native/GIS 输出组成一个 CLI prepare 用户路径；单独交付无人调用的脚本或仍抛 unavailable 的入口不解决缺口。
Width exception: merged-tasks - 2.1–2.4 都只验证 prepare 这一路径；exec/config/GIS 改动是这个入口的直接依赖，不引入独立产品功能。

## 既有 issue 路由（非新任务组）

既有 #132：吸收两个前置，worker/receipt 按真实 RunDirectory 字段消费已经由组 1 迁移的 tracker/recovery；#132 不修改 tracker 文件，也不新增 controller/JobRecord API，继续原 review ledger 完成审核及 CI。本 change 不重复创建 #132，不扩大其六文件边界。

既有 M4：在明确授权后使用真实 yd baseline/binary，取得两 source 的 00Z/12Z、7 日输出、T+12/下一轮与 NFS receipt。本地 driver/native 结构证明不能替代它；此路由不作为组 1/2 的本地实现 checkbox，也不把现场部署塞入子 PR。

组 1/2 完成即解除 #132 的 native 业务代码前置；#202 的实现子项与 M4 receipt 分栏记录。M4 需消费集成后的 #132，不反过来成为本地 #132 的代码合并依赖，避免循环依赖。

#202 为父项，组 1 → 组 2 → #132 是实际依赖；两个子项必须各自标明单一路径、文件范围、上述 fixture level 与 atomic 理由。原 #171/#177 为已完成依赖，不重新打开。Native/business adapter 必须全部完成才可销 #202/#132；M4 不再承担缺失代码。

Stage5 实现路由已落为 #207 → #208 → #132；#202 为实现/M4 receipt 分栏父项，M4 的未完成状态不构成 #132 的反向依赖。

禁止把新设计扩成通用包管理、动态 registry、可配置资产角色、审批链、旧格式兼容或多层 checksum 框架。现有合并后的 cleanup/publish/work-identity 保障刻意不动。真实模型/配置不入 Git，现场操作不由本地测试授权。

## Issue #207 implementation fixture

Issue type: bugfix；Project profile: yd-viewer；Blast radius: high。
Fixture level: expanded；Upstream suggested level: expanded（agree）；Repair intensity / effective ledger tier: high，因固定文件读写、共享 assembly/writer 与状态恢复边界，补 Invariant Matrix；不是扩大产品范围。
Scope: 只实现组 1.1–1.5；组2归 #208，#132 原六文件/ledger 与现场 M4 不动。Minimal mergeable slice/Width exception 沿用组1，不另拆出不能消费的半格式。
Seam: design §Sketch 的 `stage_work_inputs → assemble_staged → ensure_twelve_hour_checkpoint`，不增加 helper 级验证路径。
Must preserve: 现有四 IDs/direct-grid/opaque binding、当前 T 状态、WorkClaim/no-follow/资源边界、失败留证/原子提交、legacy external-root 及默认 legacy writer；RunDirectory 字段集合不增。

### Risk packs and evidence mapping

- Public API / CLI / script entry: selected — loader/staged/assembly/writer/tracker 消费接口，E1/E3/E4；CLI wiring 非目标。
- Config / project setup: not selected — 不改 TOML/default/module 配置，归 #208。
- File IO / path safety / overwrite: selected — 固定文件与 native 写入位置，沿用既有 FD/owner/copy，E1/E2/E4。
- Schema / columns / units / field names: selected — v2/14文件、cfg.para、stock 六参数与 index path，E1/E3。
- Auth / permissions / secrets: not selected — 无新增身份认证/凭据/权限策略；已有文件所有权归 File IO。
- Concurrency / shared state / ordering: selected — T 初态、checkpoint 先检查及恢复还原、现有 commit/cleanup 顺序，E1/E3/E4。
- Resource limits / large input / discovery: selected — 迁移固定集合但保留现有 byte/entry limits，不加递归发现/第二快照，E2/E4。
- Legacy compatibility / examples: selected — native 与独立 legacy 的实际字段组合/参数模式均有效，E4。
- Error handling / rollback / partial outputs: selected — 坏包不产生 model、恢复失败仍还原实际参数，E2/E3/E4。
- Release / packaging / dependency compatibility: not selected — 无依赖/发行/入口改动。
- Documentation / migration notes: selected — D1–D3 与 v2 清晰拒绝旧包、要求重做 prepare，E2；不自动删除旧根。
- Geospatial / CRS / shapefile sidecars: not selected — 不改 CRS/GIS/mapping；原站点/坐标/Z 保留由 forcing E1 核对。
- Time series / forcing / temporal boundaries: selected — index 仅移 path 行、CSV bytes/站点/Z 不变，E1/E3。
- 状态链 / warm-start 定戳一致性: selected — T 覆盖 baseline、T+12 两分支，E1/E3。
- NWM 快照溯源与 DB-free 隔离: not selected — 无 NWM import/快照修改，真实 driver 归 #208。

### Invariant Matrix and boundary checklist

Governing invariant: 只有同一 claimed work 内已校验的完整 v2 与当前 T 可进入 native assembly/tracker；移动输入目录不能改变 forcing/state 内容、丢失 owner 检查或破坏独立 legacy。
Source of truth: D1 固定集合、D2 file_checksums/两项 v2 schema、既有 WorkClaim/WorkIdentity、D3 实际 RunDirectory 与 stock reader 语法。
Producers: prepare 的既有 builder seam/fixtures、stage_work_inputs；Validators: load_prepared_variant_handoff/load_staged_work_inputs、tracker._validate_run_directory。
Storage/read surfaces: 已有 prepared/staged 文件与 checksum map；禁止新 registry、快照层、逐 helper 全包复扫。
Public/write surfaces: assemble_staged→共享 assembly kernel/_assemble_io；共享 render_shud_parameters 显式 native 模式；不复制 kernel 或重构 safe_fs。
Downstream: tracker captured/recovery 同 PR；init 的顶层 calibrated_state_path 与 legacy assemble 保持；#132 后续按字段消费，viewer 无变化。
Failure/rollback/stale state: 坏包/缺文件/篡改拒绝；已有 root/FD/commit 保障保留；recovery 在实际 nested parameter_path 写入后按原合同还原，输出不覆盖主 DAT。
Evidence/readiness: E1–E4 及 profile 门禁；只证明本地 native 结构/语法/状态消费，不证明真实 SHUD 数值/Slurm/NFS。已审核范围内的边界均由以上行覆盖。

### Required evidence

- E1（1.1–1.5）：代表性完整 native 变体+与 baseline 不同的 T state 经真实 staging 后断开 source，同一路径组装；从 model cwd 按 stock 规则打开 native 输入/index/CSV，比较 T state、原 CSV/站点/坐标/Z；用 `%s %lf` 数值语法读到六项唯一 runtime 值（START=0、END=7、DT_QR_DOWN=60、Update_IC_STEP=720、BINARY_OUTPUT=1、ASCII_OUTPUT=0），其余参数 bytes 保留。
- E2（1.1/1.2）：沿用现有 loader/staged 缺文件、旧 five-only、checksum/unsafe-entry/limit 用例并迁移有效 fixture；输入被拒绝且没有 model/越权残留。不新增同构 helper 防御矩阵。
- E3（1.3–1.5）：同一 E1 native RunDirectory：已捕获 T+12 时 runner 零调用；genuine miss 时真实 nested 参数的 END=0.5 可被 stock 语法读取，原 bytes 还原且主输出目的不变。既有 recovery 失败还原测试继续通过。
- E4（preserve）：现有 legacy assemble/writer、tracker/recovery、staged/BoundAssemblyIO 和 prepare/init 测试迁移后通过；原公开错误/owner/no-clobber/cleanup 判据不削弱，不为新语法重写 legacy 预期。
- Parent 先运行聚焦集合 `tests/test_prepare_handoff.py tests/test_staged_inputs.py tests/test_assemble_run.py tests/test_assemble_parameters.py tests/test_checkpoint_tracker.py tests/test_checkpoint_recovery.py tests/test_prepare.py`；保留真正保护 E1/E3 的回归，新测试的 pre-fix red proof 由 parent 在独立 source 副本执行，不能用 API 不存在/collection error 冒充行为红。
- Parent 按 project-profile 去重、串行执行 producer/viewer frozen sync、pytest、ruff/format check、`openspec validate --all` 与 stage-log check；源码修改与测试同批交付，leaf 不跑 formatter/linter/build/tests。
- Review focus: 14/13/15 checksum 集合一致；无第二 writer/kernel/loader；native 与 legacy 两种精确路径均受原校验；捕获/恢复两分支均走 native 模式；所有未选 pack 维持非目标。真实 prepare、GIS/BC 支持判断、Slurm/receipt 接线、现场运行不计入 #207 完成。
