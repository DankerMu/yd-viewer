# M2 Producer 基础 — 技术设计

## Context

业务规则已由 [docs/compute-loop-design.md](../../../docs/compute-loop-design.md) 全文钉死（cycle/来源/warm start/发布顺序/清理均"已拍板"），本设计只补文档未覆盖的工程决策。M2 的 oracle 是本地测试（design.md §10：阶段门禁 = compute-loop §13.1 七项全绿）；direct-grid/SHUD/T+12/Slurm 的最终 oracle 在 M4，本阶段一律以合成 fixture 与注入式假执行器验证。

## Goals / Non-Goals

**Goals:**

- proposal 所列 8 个 capability 的可实现设计与测试策略；
- compute-loop §13.1 七项本地测试逐项有归属；
- 每个 NWM 快照模块可追溯（来源 commit + 相对路径）。

**Non-Goals:**

- 不做 node-22 现场执行、cron、真实 Slurm 提交、SHUD 二进制运行（M4）；
- 不做 viewer（M3）与部署（M5）；
- 不实现 compute-loop §2 排除项（degraded、registry、血缘、退避、watchdog 等）。

## Decisions

**D1 仓库布局**（grill 用户拍板）：`producer/`（uv 项目，包 `yd_producer`）与 `viewer/` 双顶层，各自独立环境与 lockfile。与部署形态对应：node-22 只用 `producer/`，node-27 只构建 `viewer/`。M2 代码全部落 `producer/src/yd_producer/`。

**D2 NWM 快照锚点**（事实核查）：pin 公开仓 DankerMu/SHUD-NWM 的 `origin/master` HEAD `8ae9b8f29c8b72c574e8cbd95f2994160bd42832`。本地 checkout 领先的 2 个 commit 只改 scheduler/orchestrator（yd 禁止复制的部分），对快照模块无差异。每个快照模块头部注释记录 `NWM@8ae9b8f2 <原路径>`。精确文件清单由 forcing-chain 首个勘察任务在该 commit 上确定，勘察只读、不改 NWM 工作区（agent-ops §5）。

**D3 Slurm 注入式执行器**：控制器依赖 `JobExecutor` 协议（submit/poll 语义），生产实现封装 `sbatch`/`sacct`，测试注入进程内 fake（同步完成/失败/超时可编排）。备选是 subprocess mock，弃——mock 命令行参数脆弱且测不到状态机。真实提交行为属 M4 oracle；Slurm 生产实现在 M2 不做行为测试，本地判据 = 协议一致性 + fake 三态可编排 + `local.toml` 参数装配的纯函数级检查。

**D4 配置装载**：stdlib `tomllib` + dataclass 显式校验；`config.toml` 版本化业务规则、`local.toml` gitignored 现场值（字段清单见 compute-loop §5）。任何必需字段缺失即 fail closed 报错，不设默认猜测（design.md §11：现场值不得在代码中猜测）。

**D5 依赖策略**：骨架 `dependencies = []` 为刻意留空；numpy/xarray/cfgrib 随 forcing-chain 依赖任务加入并 `uv lock`；cfgrib 的 eccodes 运行时库经 `eccodeslib`（ECMWF 官方二进制 wheel，含 manylinux_2_28）显式引入，不依赖系统 `libeccodes`（组 6 已落地）。几何选轻量组合 pyshp + pyproj + shapely（读 shp/dbf、重投影、合并边界），不引入 GDAL/geopandas——viewer 契约本就禁 GDAL 运行时，producer 侧同样够用且 CI 安装面小；随 prepare-variants 任务加入。CLI 用 stdlib `argparse`，零框架依赖（KISS；三个子命令不值 click/typer）。

**D6 NWM 解释器薄外壳 fail-closed**：`prepare` 中 mapping-builder 调用封装为独立函数——接受 `local.toml` 的解释器路径与 NWM checkout 上下文（cwd/`PYTHONPATH`）、以及 `config.toml` 的 `nwm_mapping_builder_module` 作为 module 名（module 名是版本化快照事实而非现场值，归属裁决见 #32；原文笼统写作"接受 `local.toml` 的解释器路径与 module 名"，其中 module 名的出处经 #32 更正 5 裁定为 `config.toml`，issue #3 按文档优先原则同步），路径不存在或非可执行即报错退出；绝不回退到 `uv run`、`--active` 或系统 Python（agent-ops §7.2）。本地测试用假解释器脚本验证调用形态与 fail-closed 分支。

**D7 init 的测试归属**：compute-loop §13.1 表无 init 行——init 的"已有状态/DONE 即拒绝"“7 天窗最早完整 cycle”“率定末态重戳到首轮 T"场景由 `init-bootstrap` spec 自带 Scenario 覆盖；重戳纯函数复用 state 行测试。此处显式声明以免被读作测试缺口。

**D8 基建在 change 外**（grill 用户拍板）：仓库 public、CI（ruff+pytest、Py3.12、openspec validate、stage-pipeline-log 锚）、双骨架已随 commit `3f18de8` 落地，不进本 change 的 spec/tasks。


**D9 tracker 快照分次落地（issue #16 捕获半 / #17 补跑半）**：`nwm-snapshot-inventory.md` §1 第 6/7 行的抽取集横跨 compute-loop §9.2 捕获与 §9.3 补跑两半，对应任务 9.1 与 9.2、issue #16 与 #17。两半分两个 PR 落地；生产实现仍共同落在 `producer/src/yd_producer/tracker/checkpoint_tracker.py`，但测试按 #17 的文件体量裁决拆成捕获 `producer/tests/test_checkpoint_tracker.py` 与补跑 `producer/tests/test_checkpoint_recovery.py`。这是 spec `checkpoint-tracker` 的 `快照可追溯` Requirement 所要求的显式偏离记录；清单的既有 cap 6 行只登记捕获目标，新 cap 6b 行登记补跑目标，不能再把单个 `落地状态` 读成两半都已搬完。

配套的取舍，逐条（细则见 tasks.md 的 Issue #16/#17 fixture）：捕获半按 D4 零默认改写——目标小时取 `Config.checkpoint_hours` 显式入参而非 manifest 的三路 fail-open 解析，`project_name`/`run_dir` 为显式入参而非 pin 的四路 fallback，**不含轮询循环与 `time.sleep`**（观测由调用方重复调用），只接受相对分钟 header，结构校验复用本仓 `state.parse`，IO 原语复用 `store.safe_fs`。补跑半继续沿用这些边界：只支持产品权威 `checkpoint_hours=[12]`，把 pin 的 `SHUDRuntime` 方法改为显式 `RunDirectory` + 同步注入 runner 的独立函数，不移植 outer watcher、scheduler 提交或 timeout 状态机。

**D9 补记（2026-08-28，越界撤回）**：#16 起初还在 `state/cfg_ic.py` 新增了 `header_minute_time` 与 `_header_minute_index` 两个 pin 移植（理由是避免「哪个 token 是 minute-time」出现双权威），属对 issue "PR Boundary: tracker 模块与测试" 的刻意越界。评审期间 issue #22（任务 12.1）在 master 落了 `state/header_time.py`，含同一 pin 行段的同两个符号，落点更宽更正。#16 据此**撤回全部越界改动**，改为消费 `yd_producer.state.cfg_ic_header_minute_time`：越界归零，双权威顾虑由单一权威模块彻底解决。裁决细则见 tasks.md `### Issue #16 fixture` 的「落点裁决修订 R1」。

**D9 补记 2（2026-09-01，#17 文件体量裁决已关闭）**：选择拆测试文件方案，不给 `test_checkpoint_tracker.py` 增加 large-file exclude，也不把 13 项假 solver 常量塞进独立公共 fixture API。捕获测试与其结账表保持原文件；经 yd seam 适配后的补跑测试及独立结账表进入 `test_checkpoint_recovery.py`，并在快照清单新增 cap 6b 行。原三处「补跑也进同一个测试文件」MUST 由本补记覆盖；实现文件仍是同一个 tracker 模块。

**D12 issue #17 job-local 补跑与 authority**：公开补跑 seam 为 `ensure_twelve_hour_checkpoint(*, tracker, run_directory, runner) -> CapturedCheckpoint`；runner 是同步注入的 `RecoveryRunner(run_directory, output_dir) -> int`，本模块不提交/轮询第二个 Slurm 作业，也不直接启动子进程。`tracker.run_dir` 必须与 `RunDirectory.path` 同一绝对路径，目标集必须恰为 `(12,)`；`[720]`、多目标或其它小时在任何 runner/写操作前 fail closed。若实时捕获已有记录，函数在 point-of-use 重新核 checksum、relative-720 header 与原生分段结构后原样返回，runner 零调用。

一次 work 即一次 Slurm attempt；重排队/进程重启必须由 controller 删除并重组整棵 work，禁止从旧 `run_dir` 恢复 `_captured`。磁盘规范文件名从不构成 authority：只信同一 tracker 实例的 `CapturedCheckpoint` 与其 checksum。新 tracker 遇到既有 `state_checkpoints/<project>.f012.cfg.ic.update` 不覆盖、不删除；补跑同样把它判为未验证 residue 并失败，保留证据。专用输出固定为 `<work>/state_checkpoint_recovery/f012`，整棵 recovery root 必须在本次调用前不存在；任何旧条目/目录/symlink 都拒绝而不半清理。

canonical 的删除规则进一步收敛为零：tracker 在捕获或补跑安装后，即使自己刚完成 O_EXCL 创建，也不再按 pathname 清理校验/回读失败的条目。O_EXCL 返回只能证明创建瞬间的所有权；另一个写者可在返回后、回读前 unlink/替换同名 entry，而现有 `safe_fs` 没有 compare-and-unlink 原语可证明当前 pathname 仍指向本调用创建的 inode。失败条目因此作为未验证 residue 保留、不得记入 `_captured`，并阻断该 work 内重试；#26 在整轮失败路径统一删除整棵 work。这样避免 tracker 自建一套不完整的补偿删除协议。

补跑仍使用 `RunDirectory` 的同一初态、forcing index 与 forcing CSV。调用前后对这些显式静态输入做 descriptor-bound checksum 对账；runner 只得到原 `RunDirectory` 与专用 output dir。参数文件在同步调用窗口内经唯一 writer 临时改成 `END=0.5`、`Update_IC_STEP=720`，并在 `finally` 原字节恢复；恢复失败、静态输入漂移、runner 异常/非零退出、无候选、候选 header/body 不合法或安装后 checksum 漂移均抛 `TrackerError`，不得把候选写入 `_captured`。候选只认 output dir 顶层精确 `<project>.cfg.ic.update`，不递归发现、不接受主跑末态或旧 residue。

`render_shud_parameters` 扩为 `render_shud_parameters(content, *, end: Literal["7", "0.5"] = "7")`；默认调用 byte-for-byte 保持 #15 的六项主跑参数，补跑只允许显式 `end="0.5"`，其它值拒绝。yd publisher 已以显式 `scratch_checkpoint` 为输入，全仓无 `state_checkpoints.json` 消费者；因此不移植 pin 的 `write_manifest`/recovery outcome/provenance/final-IC JSON、两处 Slurm 环境读取或 `state_cli.py` rekey 面，避免制造第二套完成/发现协议。控制器提交计数与无 `DONE` 集成断言仍归 #26。

**D13 issue #26 单源单轮 controller 与计算节点边界**：任务 14.1 的公开入口为 `controller.run_once(*, config, local, source, executor, driver, poll_wait) -> RunReport`。controller 真实持有且按序执行的面只有：Slurm/路径 preflight → 不含 raw 的严格前沿 → NFS residue 计划/执行 → `rawscan.judge` → `rawcopy.stage_raw` → 组装并校验一次 `JobSpec` → `JobExecutor.submit/poll` → 同 job 的显式产物交接与 checkpoint point-of-use 重验 → `publish.publish`。多轮循环、第二源、失败日志/失败 work 回收、跨 tick 崩溃恢复都不在 14.1。

`decide_frontier` 的公开行为与签名保持；内部把“DONE/状态得到 T”与“调用 raw_complete”拆开，`run_once` 复用前半段，而不是用 `raw_complete=lambda _: True` 伪造一次“raw 完整”结论。任意可解析但不在 `config.cycle.hours` 的 T 在接入 `rawscan.judge` **之前**收敛为本源 `RAW_INCOMPLETE` 停止报告，零 residue 删除、零 work、零提交；合法 T 才按 compute-loop §10 执行 residue plan/execute，再直接调用 `rawscan.judge`。这样既不让 `ConfigError` 击穿整个 tick，也不在非法前沿上触发删除。合法 T 的 raw 真不完整时报告保留 T，residue 已按设计先行清理。

controller 不拥有 `WorkIdentity` 的 model/basin/project 五项，也不能从变体 basename 或 `yd.binding` 反造；同时真实 forcing/SHUD 必须在 Slurm 计算节点内运行，不能搬到登录节点。故只新增一个显式、无默认的 `AttemptDriver` seam：`prepare(request) -> PreparedAttempt` 在提交前只交出 frozen `WorkIdentity + worker argv + scratch DAT 终名`；controller 自己从 `(resolved work_root, source, T, local.slurm)` 唯一构造 `JobSpec`（name=`yd-<source>-<T>`、work_dir=`<work_root>/<source>/<T>`、log_path=`<work_dir>/job.log`），driver 无权选择删除路径、日志路径或资源。DAT 终名允许 driver 按真实 solver/worker 绑定声明，但必须位于 work 内且提交前不存在；job log 同样在提交前不存在。`collect(attempt, terminal_record) -> AttemptProducts` 只在同一 job 已报 `SUCCEEDED` 后交出 `job_id + RunDirectory + 同一 job-local CheckpointTracker + scratch DAT + merged log`，其中 DAT/log 必须逐字等于 prepare/JobSpec 已声明的路径，不能另换；RunDirectory/tracker 必须绑定同一 identity/work。controller 对 source/cycle/work/log/resources/job-id/run-dir/checkpoint canonical path 全部 point-of-use 重验；再以 `ensure_twelve_hour_checkpoint` 的“已有 authority、runner 零调用”分支取得 `CapturedCheckpoint`，禁止按规范文件名扫描或重建 authority。

`variant_reach_count` 不信任 driver/作业回执，也不从 DAT 列表反推。controller 复用 `prepare.variant_targets(local, config)` 取得该 source 的唯一变体根，复用 `prepare.calibrated_state_path` 与 `state.parse` 读取 prepare 已校验的率定状态，要求 river 段存在并把其 `row_count` 作为独立权威；随后 publisher 同时比较 DAT `nc`、`Config.reach_count` 与该值。

M2 的 `AttemptDriver` 是注入式计算节点边界，不是假成功入口：端到端测试必须用一个包住真实 `FakeJobExecutor` 的 terminal hook，在 `poll` 首次返回 `SUCCEEDED` 的同一跃迁内执行合成 DB-free 链、SHUD/tracker/recovery 并生成 DAT/log；提交前这些终态产物必须不存在。hook 内的 recovery 仍只调用 #17 同步 runner，不接触 executor，因此同一 source/cycle 的 `FakeJobExecutor.submissions` 精确为 1。controller 不扩 `FakeJobExecutor` 协议，也不让 fake 自己解释 SHUD。

生产 CLI 暂不绑定该 seam：仓内尚无计算节点 worker 命令，也没有能跨 Slurm 进程携带同一 tracker authority 的原子 receipt；伪造目录扫描或在提交前预埋产物都比 fail closed 更坏。`cli.py run` 继续返回既有 staged-unimplemented 语义，真实 worker/跨进程 receipt 与 node-22 argv 归 M4；M2 只声明上述注入式本地 oracle，不声明真 Slurm 可运行。

为让 rawcopy/canonical/forcing/registry 共用同一 `LocalObjectStore`，14.1 把 `stage_raw` 的 `work_dir` 实参明确取为 `<attempt-work>/object-store`：raw 落 `object-store/raw/`，本轮 manifest 落 `object-store/raw-manifest.json`，canonical/forcing/models 同根。`stage_raw` 自身的 standalone 合同与 local key `raw/...` 不改；这是 controller 的接线选择。整棵 `<work_root>/<source>/<T>` 仍是一次 attempt 的唯一回收单元。

报告拆为 `JobRunReport`（job ID、提交记录中的 partition、终态、submitted/started/ended）与 `RunReport`（source/cycle/outcome/stop reason/detail/job/publish/DONE path）。资源键集必须与 config/local 完全一致，且 `partition` 必须同时出现在 `Config.slurm.required_fields` 和 `LocalConfig.slurm`；该 preflight 在发现、清理、建 work、driver 与 submit 之前完成。`poll_wait` 无默认，controller 不内置 sleep/interval，也不发明 watchdog、自动取消或 controller timeout；它在每次非终态 poll 后调用一次该策略，再继续查询。每个 poll record 的 job/name/resources/submitted_at 与状态单调性都相对提交记录重验；M2 fake 序列必须确定性到达终态，真实等待/取消仍归 M4。

正常成功只由 `publish` 返回后记 `SUCCEEDED`，并要求精确 work 已删除。`PublishCleanupError` 表示 `DONE` 已承诺本轮，记 `SUCCEEDED_CLEANUP_PENDING`，绝不能降格为 job/publish 失败或触发失败侧回收；历史孤儿 work 扫描仍由 #108 跟踪。`FAILED/TIMEOUT` 只记 `JOB_FAILED` 报告、不 collect、不 publish；退出码 owner 与失败日志/work 删除留给 #47/#28。`PublishError`、driver/executor/poll/collect 错误以结构化 `RunError` 响亮失败并保留证据，14.1 不抢 #28 的恢复协议。

#94 的危险删除闸在本 issue 同批闭合：`PublishInputs` 构造期必须重验 resolved `work_dir == resolved work_root / source / cycle_id(cycle)`；少/多一层或兄弟 source/cycle 在任何 IO 前拒绝。把守卫放在危险边界而非只靠唯一 caller，使未来调用方不能绕过。

**D10 issue #14 direct-grid forcing 的落点与 seam**：任务 8.1 只落 NWM pin `8ae9b8f2` 的 file-backend direct-grid producer；公开验收 seam 取快照原生的 `yd_producer.forcing.ForcingProducer.produce(...) -> ForcingProductionResult`，不在本 PR 另造 `forcing.build(work, manifest)` facade。上文 seam 4 的 `forcing.build` 是组 8 完整链的编排层草图，连同临时 registry 生命周期和 `assemble(...)` 归 issue #15；提前实现会穿越 #14 的 Minimal mergeable slice。

本 issue 中「站点集合等于格点集合」按 direct-grid binding 的权威定义解释：输出站点与 binding 声明的 canonical `grid_cell_id` 集合一一对应，canonical 中未被 binding 引用的额外格点不成为站点，也不读取其值；这与 pin 的「Required grid cells are subset before value extraction」一致，不恢复旧 105 站 IDW。GFS/IFS 各用自己的 contract、grid id 和 cell id，禁止跨 source 复用 binding。source singleton 不是 file parser 的偶然性质：`ForcingProducer` 在每次 repository 返回 contract 后必须再次要求 normalized `applicable_source_ids == (requested_source,)`，因此注入式或未来 repository 也不能用 source-less parser/direct constructor 绕过生产边界。

`Time_Day` 的零点由调用参数 `cycle_time` 决定，不由最早可产出的 `valid_time` 反推。00Z/12Z 都要求首个 SHUD 行在 cycle；若输入不能产出 cycle 行，producer fail closed 且不写 ready package，不能把 T+3/T+12 重标为 `Time_Day=0`。公开 seam 在任何 repository lookup/write/cleanup 前拒绝 06Z、非整点或非零秒/微秒，避免按小时命名的 storage identity 与合法 00Z/12Z ready 状态碰撞。

canonical catalog 是产品清单，但 row metadata 必须先独立自洽，再与精确 object key、NetCDF 自描述 identity 交叉验证。catalog constructor 先要求 `valid_time - cycle_time` 是非负整小时且逐字等于 `lead_time_hours`，并要求 `canonical_product_id == <normalized-source>_<YYYYMMDDHH>_<variable>_f<lead:03d>`；不能让 row 与 NetCDF 成对伪造后循环作证。对象 key 再由已验证 row 的 source/cycle/variable/canonical product id 唯一导出；dataset 的 data variable、`cycle_time`、`valid_time`、`lead_time_hours`、`unit`、`grid_id` 必须与 row 相同，构成第二道独立比较。descriptor gateway 对单个 canonical NetCDF 采用版本化 512 MiB 上限（`MAX_CANONICAL_NETCDF_BYTES = 536870912`），在同一 no-follow fd 上先以 `fstat` 拒绝已知超限 regular file，并在 checksum 流式读取中再次按累计字节 fail closed；该数值覆盖当前 5,000,000-cell 单变量产品预算，不引入环境变量或第二份 config authority。

forcing ready currency 除 `producer_version` 外还记录一个 canonical-JSON stable output-config identity，至少覆盖 `rn_shortwave_factor`、三个输出文件名、output/required variable 集、ERA5 latency policy 与 `min_lead_hours`。只要其中会改变 package bytes、shape、path 或选择策略的字段变化，同 `(source, cycle, model)` 就必须重算/拒绝，不能返回旧 `already_done`。

Round 2 depth corrective action 把 `produce` 的 pre-currency 边界固定为四段，而不是继续逐 finding 补丁：(1) public request preflight 在零 repository call 前验证 source/cycle、forcing-owned model/basin-version path component 与 `max_lead_hours`；(2) 只读解析 repository model identity 与 returned direct-grid contract，在 `get_forcing_version` 前完成 basin path 与完整 task-8.1 contract structure 验证；(3) 读取 existing 后才读取 catalog、binding/`.sp.att` 与 descriptor-bound NetCDF 来证明 authority/currentness；(4) 全部通过后才作 currency decision 与 mapping/package/finalize mutation。前两段失败不得调用 existing lookup、失败状态写或 cleanup，已有合法 sibling 的 record/package/domain/sidecar/handoff/cycle-ready bytes 必须不变；第三段发现 authoritative input drift 时，旧 ready 已不再被当前输入证明，仍必须撤销。不能把 `get_forcing_version` 粗暴挪到所有 object reads 之后，否则会让已变化的 binding/catalog/NetCDF 错误保留 stale ready。

forcing-owned path component grammar 保留现有 ASCII component 约束，只补 literal `.`/`..` 拒绝；不修改 shared object-store/safe-fs，也不发明新的全局 ID grammar。`max_lead_hours` 只接受 `None` 或 strict nonnegative `int`（bool/float/string/negative 均拒绝），`0` 与任意可表达的大整数合法，不设业务无据的上限。

`DirectGridForcingContract`/`DirectGridStationBinding` 是公开 frozen dataclass，故 parser 不是唯一生产 owner。一个 shared semantic validator 同时由 parser 构造后与 `ForcingProducer` 的每个 repository-return boundary 调用：生产传 current source 时要求 mode/source singleton，并重验 non-empty/capped station tuple、nonblank contract/station identity、strict positive contiguous indexes、safe casefold-unique CSV filenames、station/grid coherence、unique station/cell identity、finite canonical coordinates与 Mapping properties。JSON object/list extraction仍是 parser-only；binding/`.sp.att` checksum、canonical grid/signature/cell existence仍由后续 authority owner证明。source-less parser 的 pin-compatible multi-source shape保留，但进入生产必须单 source。parser 对已在 canonical `[-180,180)` 内的 longitude 必须原 float 返回（只保留既有 `-0.0 -> 0.0`），避免无条件 `% 360` 给合法 full-precision 值制造 1 ULP 漂移；只有 legacy `[180,360]` 输入继续以减 360 归一。独立可执行清单见 `.workplans/14/review/round2-boundary-input-inventory.md`；OpenSpec 本身保留全部规范义务，不依赖该 gitignored evidence 才成立。

上述 inventory 同时列出而不越界修复 task 1.1/#26 拥有的 config constructor grammar（例如 nonfinite factor、malformed output-variable set、negative/wrong-type `min_lead_hours`/resource limits）以及 #15 的 registry/assembly 生命周期；本轮只维持这些 config 对 valid-value output currency 与既有 limit enforcement 的 owner。

快照 DB-free 守卫检查真实外部耦合（数据库驱动/URL、scheduler 或 registry 包 import、环境读取），不再把普通标识符或错误消息里的裸 `scheduler`/`registry` 单词当成耦合。显式注入、只服务本轮 work 的 file manifest adapter 是 forcing-chain spec 明文允许且 issue #15 负责生成/清理的内部文件契约，不是外部 registry 服务。

`producer.py`、`file_store.py` 和抽取式 `test_forcing_producer.py` 保持 pin 的文件边界与溯源身份；若按清单闭包落地后超过项目 1000 行闸门，只把这三份 vendored/snapshot 文件逐文件登记进 `.large-file-guard.json`。yd 自撰验收测试必须拆分在 1000 行内，不得借此扩豁免。

**D11 issue #15 临时 file backend 与 SHUD 组装边界**：任务 8.2/8.3 落一个 yd 自撰公开模块 `yd_producer.assemble`，不带 NWM 溯源头；为把 descriptor-bound copy/write/rename 保持为私有实现且守住每文件 1000 行闸门，可配一个 yd 自撰私有支持模块 `yd_producer._assemble_fs`，但它不得形成第二条公共 seam 或改写 shared `safe_fs`。D10/Sketch seam 4 里的 `forcing.build(work, manifest)` 是尚未落码时的概念草图；#14 已落真实 `ForcingProducer.produce`，再包一层只转调 facade 没有独立合同。#15 以两个最高可测边界替代：`stage_work_registry(...) -> WorkRegistry` 生成 file repository 的本轮输入，既有 `ForcingProducer.produce` 负责 forcing，`assemble(...) -> RunDirectory` 组装 SHUD 输入；#26 只编排三者，不在本 issue 提前接 CLI/controller。

一份 frozen `WorkIdentity` 是两条 seam 的唯一字符串/时间 owner：`source_id`、UTC 00Z/12Z `cycle_time`、`model_id`、`basin_id`、`basin_version_id`、`river_network_version_id`、`project_name`。现有 `Config` 没有后五项，任务 1.1 的 schema 已闭合且 #29 只负责生产实例；#15 不新增配置字段、不猜变体 basename、不从 `yd.binding` 反造 identity/contract。调用方显式提供 identity、#14 的 `DirectGridForcingContract` 以及同一已验证 binding/`.sp.att` 产物的 bytes；#26/M4 后继从 config、prepare 与现场 builder 结果接线。

`work_dir` 唯一派生为 `<work_root>/<normalized-source>/<YYYYMMDDHH>`，调用时必须已存在且逐分量 no-follow；registry/object-store/run 产物不得落到其外。临时 model 根固定为 `object-store/models/<model_id>/`：`registry.json`、`manifest.json`、`direct-grid/binding.json`、`package/<contract.sp_att_path>` 与 `package/<project_name>.tsd.forc`。contract 的 `binding_uri` 必须逐字等于该 final binding key，`sp_att_path` 必须逐字等于 `input/<project_name>.sp.att`；binding/`.sp.att` checksum 在首写前与 contract 核对。station index 只由 contract stations 按 forcing index 单向生成，承担 file-backend 兼容读面，不反过来定义 station id，也不解析或手工维护第二份 station rows。其五个 geometry 数值用 Python float shortest round-trip 文本，避免合法 full-precision 值在真实 repository 读回时被 `.10g` 截短；既有 FileForcingRepository 把负 elevation 归一为 `0.0`，故 station-index 的 z 兼容视图也写 `max(contract.z, 0.0)` 并按该值读回对拍。direct-grid forcing 数值站点仍由 contract 直接构造，不能让该兼容归一反向改写 contract 或 forcing station。

registry 文档只含一个 model，字段固定为 `model_id/basin_id/basin_version_id/river_network_version_id/model_package_uri/manifest_uri/resource_profile`；profile 只含 serialized `direct_grid_forcing` 与 `shud_input_name`。model manifest 只含 `basin_slug=<project_name>`，不伪造额外 schema/version。为在提交前用**未改写的最终相对 key**行使真实 `FileForcingRepository`，整棵 model 根先在本轮 work 内的隔离 shadow object-store（`<work>/.<model_id>.registry-stage-<nonce>/object-store/models/<model_id>`）构造/读回；全部通过后，把 staged model 子树以同一 work/filesystem 的一次 no-follow rename 提交到 `<work>/object-store/models/<model_id>`。这不是“同父目录 staging”；原措辞与“提交前真实 repository 读最终 key”不可同时满足，按后一条可执行不变量修正。终名或 nonce staging 预存、checksum/path/identity 不一致均在提交前拒绝；不允许固定共享 staging 名。现有 shared `rename_entry_no_follow` 是裸 POSIX `renameat`，没有可移植 `RENAME_NOREPLACE`：受支持的生产调用必须由 #23 `run_with_lock` 覆盖整个 #26 controller 生命周期，且两个提交 seam 均在 rename 紧前复探终名，把首次预检后的窗口缩到最小；不得伪称可抵御不遵守同一 runlock 的外部并发写者。没有细粒度 `cleanup_registry`：registry 的生命周期由物理 containment 保证，成功/失败收尾继续复用 publish/cleanup 对整棵 work 的删除 owner，避免第二套清理协议。

`assemble` 接受前一 seam 的 `WorkRegistry`、绝对 `variant_dir`、显式 `state_path` 与 #14 的 `ForcingProductionResult`。它在 point-of-use 重验 WorkRegistry identity/path与registry/model JSON；binding/`.sp.att`资产只受创建时显式`max_asset_bytes`，二次重验走descriptor-bound streaming checksum，不得误套16 MiB JSON manifest上限。`state_path` 必须逐字是某个已存在 states 根下 `<source>/<T>.cfg.ic` 的绝对 no-follow 普通文件，原生分段可解析且 header 最后数值 token 按现有绝对分钟规则对应 T。状态只验证、逐字复制，不重戳、不修正，运行目录初态因而 byte-for-byte 等于 T 状态。

运行目录终名固定为 `<work_dir>/model`。变体顶层必须有且只有一个显式 project 对：`<project_name>.cfg.ic` 与 `<project_name>.para`；所有变体条目逐层 no-follow 复制到 staging，但率定 `cfg.ic` 不复制，改以 T 状态独占写到同名。非普通条目、source/target 名碰撞、已有 `model`、wrong-cycle state 或任何读写失败都不得提交终名；只清本次 staging，绝不改 variant、state、forcing package 或已提交 registry。

forcing package 的 authority 是 `ForcingProductionResult.file_uris["package_manifest"]` 的 bounded/no-follow JSON bytes，其 SHA-256 必须等于 result.checksum，manifest 的 source/cycle/model/version 必须与 WorkIdentity/result 相同；#14 manifest 本身不含 package URI，故 package identity 由 result `forcing_package_uri`、manifest key 必须落在该 prefix 下，以及每个 member URI 必须逐字等于 prefix + relative path 三方联合证明，不伪造/要求不存在的 manifest 字段。Assembler 只接受 `files` 中恰一个 canonical/legacy SHUD index role 与 checksum 完整的 `shud_forcing_csv` 条目；两种 index 同时出现、角色/相对路径/URI/checksum/filename set 漂移均拒绝。index 与 CSV 均 descriptor-bound streaming 校验；index parser只保留当前有限字段与至多 contract 上限 10000 个已声明 CSV basename，不保留整文件/整行。index 在运行目录改名为 `<project_name>.tsd.forc`，station CSV basename 原样落根；debug、payload、handoff、domain package 与其它内部文件不进入 SHUD 运行目录。

六项参数的唯一 writer 在 `assemble`：对 `<project_name>.para` 逐键识别 pin 已登记的 `{{KEY}}`、`${KEY}`、`KEY = value` 三种形态，零命中则按源文件行尾追加 `KEY = value`，多于一处则响亮拒绝，防止一份参数出现两个 authority。只改六项命中/追加字节，其余行逐字保持；固定值为 `START=0`、`END=7`、`DT_QR_DOWN=60`、`Update_IC_STEP=720`、`BINARY_OUTPUT=1`、`ASCII_OUTPUT=0`，不读取 cycle hour，因此 00Z/12Z 参数 bytes 必须相同。#17 的 `END=0.5` 补跑复用此纯改写 owner，不复制第二套 helper。

run staging 完整后，先在commit紧前复探终名，再以同父目录 no-follow rename 一次提交；其不覆盖保证同样依赖#23 runlock的单写模型，不宣称裸renameat具有noreplace。失败时 final `model` 不存在，源三面 bytes/shape 均不变；staging 清理失败只作为原异常附加证据并留在 work 内，由整棵 work owner最终回收。#15 不执行 SHUD、不创建 checkpoint/DAT/DONE/运行报告、不删除整棵 work、不动 NFS正式产物。

## Sketch seams under test

测试行使的公共边界，从高到低（每 seam 一行理由）：

1. `controller.run_once(*, config, local, source, executor, driver, poll_wait) -> RunReport`（对 tmp 目录树 + fake executor + 注入式 attempt driver）——任务 14.1 最高可本地行使的 seam，覆盖单源单轮的前沿、residue、raw staging、一次 submit/poll、attempt-local checkpoint authority、DONE 最后写与 work 清理；14.2 再在同一 runlock 内循环该 seam，14.3 再组合双源并发、失败隔离与崩溃恢复，不把那些场景伪算成 14.1 已完成。
2. `rawscan.judge(raw_root, source, cycle, config) -> ScanVerdict`（目录 fixture）——完整性规则与 f000 特例的判定边界，独立于控制器演进。（本行于 issue #6 修订：原写作 `raw_scan.scan(raw_root, source, cycle) -> Manifest | Incomplete`。三处修订理由——模块名以 issue #6 的 `yd_producer.rawscan` 为准；规则全集在 `Config` 内，故 `config` 必须是显式形参而非隐式全局；返回 `Manifest` 与 tasks.md 组 3 的切分冲突——manifest 结构归 3.2，3.1 只返回判定结果 `ScanVerdict`。复制与 manifest 生成的 seam 由 3.2 另行钉定。）
2b. `rawcopy.stage_raw(verdict, raw_root, work_dir, source, cycle, config) -> StagedRaw`（目录 fixture + 合成源 manifest）——issue #7 按上一行的交接钉定：只读复制与临时 `raw-manifest.json` 生成的边界。独立成 seam 而不并入 `judge` 的三个理由——判定是纯函数、staging 是写面，两者的失败语义不同（不完整不是异常 vs 写面失败即异常）；staging 需要 `work_dir` 与源 manifest 两个 `judge` 不需要的入参；produce→converter 的产物契约（entry 逐变量扇出、`idx_selector` 累积语义、manifest 级 forecast hours）只在此边界可断言。编号取 2b 而非重排后续各行，避免与既有引用（本文件与 tasks.md 多处按序号引用 seam 3–7）产生第二份编号。
3. `state` 模块文件级纯函数（parse/restamp/negative-residual/check，file→file）——格式正确性是状态链安全的根，必须在最细边界钉死。
4. `assemble.stage_work_registry(...) -> WorkRegistry`、既有 `ForcingProducer.produce(...) -> ForcingProductionResult` 与 `assemble.assemble(...) -> RunDirectory`（合成 canonical/变体/state fixture）——issue #15 按 D11 把旧 `forcing.build` 概念草图拆成有独立合同的临时 file-backend seam 与组装 seam；`state_path`/WorkIdentity 显式入参保证 warm-start 覆盖、source/cycle/model 一致和 registry 生命周期都可被断言，不新增只转调 facade。
5. `CheckpointTracker.capture_available()`（模拟 `cfg.ic.update` 覆写序列）与 `ensure_twelve_hour_checkpoint(*, tracker, run_directory, runner) -> CapturedCheckpoint`（注入同步假 SHUD runner）——前者确定性重放轮询竞态，后者在同一 attempt 内闭合漏采补跑、输入一致性与失败传导；#16/#17 fixture 记录从早期 `tracker.capture(shud_dir, target_minute)` 草图到这两个有状态 seam 的适配理由。

6. `cli.main(argv, env) -> int`（进程内调用，不起子进程）——入口层**自身的契约**（三入口枚举、未知子命令拒绝、`DATABASE_URL` 守卫、`run` 状态目录守卫）由 spec cli-config 的 Scenario 逐条钉死，必须在此边界行使；委托目标以注入的假实现替换，故不牵连业务模块。
7. `nwm.invoke_mapping_builder(local, config, args, runner) -> CompletedProcess`（假解释器脚本 + 注入 runner）——D6 薄外壳的调用形态（精确解释器路径、module 名、cwd/`PYTHONPATH`）与 fail-closed 只能在此边界断言。

CLI 入口层**不做业务行为测试**（薄委托）：`prepare`/`init`/`run` 的业务行为经各自模块 seam（1–5）验证。这不豁免 seam 6——入口层自己的枚举/拒绝/守卫契约是 spec 明文 Scenario，属入口层而非业务模块，必须在入口层测。（原文写作"CLI 入口层不做行为测试"，与 spec cli-config 的 `--help`/未知子命令/`DATABASE_URL`/`run` 守卫四条 Scenario 直接冲突，issue #3 按文档优先原则更正。）

## Risks / Trade-offs

- [NWM 快照模块存在隐藏 DB/registry 耦合] → 勘察任务先行定清单；确有耦合时在快照内最小改写为文件后端并在模块头注明偏离，不引入运行时 NWM import。
- [cfgrib/eccodes 在 CI ubuntu 上安装失败] → 已按「优先二进制 wheel」分支解决：显式加 `eccodeslib` 依赖（PyPI 上 `eccodes` 仅发 win_amd64 wheel，linux/macOS 不自带库），CI producer job 无需 `apt-get libeccodes0`。
- [pyproj/shapely 在 CI 安装失败] → 已随 prepare-variants 依赖任务（10.1）解决并验证：三者均走 cp312 manylinux wheel（pyproj `manylinux_2_28`、shapely `manylinux_2_17`、pyshp 纯 py3），CI producer job 无需额外 apt 包。
- [无真实 GRIB/canonical 数据可本地验证数值] → 合成 fixture 验证结构与管线正确性；数值正确性显式归 M4 receipt，不在 M2 声明。
- [flock 语义测试跨平台脆弱（macOS/Linux 差异）] → 锁封装为小模块，单元测试进程内验证非阻塞跳过语义；真实 cron+flock 行为归 M4。

## Migration Plan

全新代码，无迁移；回滚 = revert 对应 PR。不触碰 `YD_ROOT` 契约与现有文档。

## Open Questions

无——grill 门禁 5 分支全部拍板，0 开放项。
