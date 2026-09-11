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

## Issue #208 implementation fixture

Issue type: feature/bugfix；Project profile: yd-viewer；Blast radius: prepare-only external execution and four-target transaction。
Fixture level: expanded；Upstream suggested level: expanded（agree）；Repair intensity / effective ledger tier: high，因真实进程、文件/身份投影与既有发布事务相接，补 Invariant Matrix；不扩成日常 Slurm 或平台治理。

### Scope and preserved boundaries

- 只实现组 2 的 2.1–2.4；已合并 #207 的 14/13/15 native 合同、assembly/tracker 不重设计。#132 的 worker/receipt 与原 PR197 ledger 保留给后继；M4 现场授权/真实数值不在本项。
- `invoke_mapping_builder(local, args, runner)` 保留名字，去掉旧 Config 参数；driver 是 yd 随包分发的绝对脚本路径。`run_prepare` 显式绑定 local 给默认 builder，保留注入式 `Callable[[VariantBuildRequest], None]` seam，不用 global/env 发现配置。
- 删除 `nwm_mapping_builder_module` 的当前配置/构造/调用者及 unavailable 生产分支；历史 fixture 已由 m2-producer-core/tasks.md 顶部替代声明覆盖，不把历史记录重写成当前实现。
- 原四终名预检、staging residue 拒绝、scratch→YD_ROOT staging→逐名提交、回滚/清理 note 与成功 warning、率定状态基数/河段数仍由已有 prepare 负责。真实 builder 错误走现有 PrepareError 路径；不得保留假 exit3、吞错误或重构 cleanup/publish。
- NWM 原件、环境、DB、服务不改。日常 yd worker 不 import NWM；仅独立 driver 使用明确的 pin 库。现有本地 NWM Python3.11.14 可作为只读解释器，yd 的3.12环境不能代替该运行面；driver 须兼容 pin 的 Python>=3.11。

### Risk packs and evidence mapping

- Public API / CLI / script entry: selected — 真实 prepare CLI、default builder 与 invoke 签名迁移，E1/E3/E4。
- Config / project setup: selected — 删除一个 obsolete 业务字段，保留其余配置与必需键，E3/E4；不新增环境/路径平台。
- File IO / path safety / overwrite: selected — 固定 native 文件、GIS、driver/cwd 及原四终名事务，E1/E2/E3。
- Schema / columns / units / field names: selected — 十字段投影、裸 hex 前缀、稳定四身份、真实 elevation 与 element BC/SS/LAKE、river BC，E1/E2。
- Auth / permissions / secrets: selected — 仅既有 DATABASE_URL 禁继承与固定执行对象；不扩展到 PG* 清扫、凭据或权限系统，E3。
- Concurrency / shared state / ordering: selected — 原预检/提交/失败清理次序和输入身份稳定性，E1/E2/E4；不新增并发 prepare 或锁协议。
- Resource limits / large input / discovery: selected — 固定文件集合、沿用发布/loader 限制，不裁剪完整 canonical grid、不引入递归资产发现，E1/E4。
- Legacy compatibility / examples: selected — 注入 builder seam、其余 Config 消费者与 legacy assembly/tracker 保留；旧 module/unavailable 是明确删除而非兼容，E3/E4。
- Error handling / rollback / partial outputs: selected — 真实 mapping/解析失败、执行前分类、notes/warnings 和四终名不提交，E2/E3。
- Release / packaging / dependency compatibility: selected — 随包 driver 在明确 NWM 解释器执行，不能只在源码 cwd 可用；不把 NWM 依赖装进日常 yd 环境，E1/E3。
- Documentation / migration notes: selected — D4/当前 cli-config 合同与生产 config 一致，明确旧包重做 prepare 和 M4 边界，E4。
- Geospatial / CRS / shapefile sidecars: selected — river.shp/domain.shp 与 sidecars、真实 mapping/mesh Z，E1/E2。
- Time series / forcing / temporal boundaries: selected — source/grid/站点/完整 grid signature 与现有 binding 语义，E1；不改日常 raw converter。
- 状态链 / warm-start 定戳一致性: selected — 原始 calibrated state bytes、现有状态基数/河段数校验保持，E1/E4；不重做 T+12 路径。
- NWM 快照溯源与 DB-free 隔离: selected — 精确 pin 库、独立进程环境、opaque binding 原 bytes，E1/E3；不构造平台 evidence/approval/UUID。

### Invariant Matrix and boundary checklist

Governing invariant: 只有固定解释器/checkout 下真实库生成、经同一 v2 loader 接受的双源完整产物才能通过既有四终名事务；真实 mapping、schema、执行或支持域失败必须保留原错误/清理证据且不提交终名。
Source of truth: D1/D4、native-builder spec、#45 已有四项 exec 义务、现有 prepare/geometry/loader/Config 边界。D4 已决定 PYTHONPATH override 并去掉 PYTHONHOME，不重新发起 prepend 决策。
External execution: interpreter 为原始绝对 venv 路径而非 resolve/PATH fallback；checkout 为明确绝对目录，缺失/非目录先分类；脚本绝对路径不依赖调用者 cwd；child 不含 DATABASE_URL/PYTHONHOME/继承 PYTHONPATH。
Read/transform: 源路径对照 gfs/IFS 固定；read_input_record→prepare_snapshot 保留 snapshot ID None 与完整 cells；D4 所列 mapping/FORC/Z/emitter 真调用，baseline 固定资产/状态按原 bytes 搬运，不复制算法或构造平台记录。
Identity/checksum: 同一份 baseline checksum map 派生 basin/river 版本；model 版本使用 source、完整 grid signature、pin、algorithm/sampler ID。opaque binding 不改；只做十字段投影与两项 sha256 前缀适配，不改变站点顺序/值或新增身份字段。
GIS/support: baseline 直接含 native yd.* 和 gis；使用 river.shp/domain.shp，复用既有 GeoJSON writer。一次性拒绝 element BC/SS/LAKE 或 river BC 非零，不建设条件资产框架。
Failure/publication: 真实 library/解析错误经子进程和 PrepareError 到 CLI exit1，stderr 保留实际错误与 cleanup notes，无 traceback/unavailable；四终名不提交。既有注入失败、回滚与成功 cleanup warning 行为保留。
Downstream/removed behavior: 所有实际 Config 构造与 invoke 调用同步迁移；只删旧 module/unavailable oracle，不把旧 wording pin 改钉新文案。#207 loader、init、日常 worker/legacy assembly 不增加 NWM import 或新配置。
Evidence/readiness: 以下 E1–E4；真实小模型/库执行仅为本地 adapter proof，不是 SHUD 数值、Slurm/NFS 或现场 prepare 授权。

### Required evidence

- E1（2.1/2.2/2.3/2.4）：从真实 `yd-producer prepare` CLI 在明确 NWM pin 环境、无 DATABASE_URL 下运行小型合法 native 几何/网格，得到双源完整 v2 与两个 GeoJSON；经同一 yd loader 读取，核对 lowercase singleton、D11 URI、原始 opaque binding bytes、两项 checksum 前缀、非零真实 mesh elevation 的 Z。样例至少使用四个 cells（pin small-basin gate），保留未使用 cells 以证明完整 grid 未裁剪；两 source grid 有可辨别差异。
- E1 稳定性：相同输入在新目标再执行真实 prepare，四身份与 mapping/binding bytes 一致。只在独立受控测试 checkout 创建小网格/资料；原 NWM 文件、venv 与 canonical grid 均不修改。记录实际解释器和所 import pin 库路径；随包 driver 也须从非源码 cwd 可达。
- E2（2.3/2.4）：同一路径使用一个真实 mapping/解析失败，观察实际错误/exit1、四终名未提交；既有 cleanup note 用例迁移到真实错误分支，notes 与无 traceback 仍可见。分别保护 element BC、SS、LAKE 与 river BC 四个支持域判据，不以丢弃资产伪装成功。
- E3（2.2/#45）：实际执行边界证明相对/无斜杠 interpreter 不到 runner，绝对 venv symlink 原样执行；缺失/非目录 checkout 分类为 ConfigError 且零调用；child 不含 DATABASE_URL/PYTHONHOME/继承 PYTHONPATH，只有明确 checkout。环境毒化在已启动的测试进程中施加，不能让 PYTHONHOME 先阻止 parent interpreter 启动再冒充 child 验证。真实 CLI 正向证明不得由 fake shell 写产物替代。
- E4（preserve/profile）：迁移 prepare/nwm/cli/config 与所有直接 fixtures 的原有行为测试；保留 overwrite/residue/rollback/notes/state/legacy 契约。Parent 串行执行 producer/viewer frozen sync、pytest、ruff/format、OpenSpec all/strict、stage-log；同时记录 pin 库 CLI smoke，普通 CI 的 fake 边界测试不能替代它。冻结前核对当前 master 合并面的验证证据，保持 branch-tip 一致。
- Parent 保留一个公共路径的 pre-fix 行为红证明（当前真实 CLI 的 unavailable 拒绝或 exec/env 真实缺陷），不以新脚本/API 不存在或 collection error 计红。永久测试只保留能击穿可构造行为缺陷的用例，driver fixture/源码同批交付；所有 leaf 跳过验证，由 parent 统一运行。
- Pin references：NWM8ae9b8f2 algorithm.py:133/1209–1284（四格 gate）；integrity.py:834–859（domain.prj 权威且 bytes 一致）；cli.py:399–469（真实 Elevation）；z_policy_verdict.py:137–153/376–448（仓库资料发现及固定 checksum）；grid_registry/input_record.py:209–303 与 registry.py:294–331（DB-free 文件读取/完整 grid）。
