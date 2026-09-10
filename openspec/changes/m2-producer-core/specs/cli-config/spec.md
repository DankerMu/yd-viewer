# cli-config

来源：compute-loop-design §5–6、agent-ops §2.2/§7.2、design.md §11。

## ADDED Requirements

### Requirement: CLI 只暴露三个入口
`yd-producer` MUST 且只 MUST 提供 `prepare`、`init`、`run` 三个子命令；入口层薄委托，不承载业务逻辑。

#### Scenario: 帮助列出三入口
- **WHEN** 执行 `yd-producer --help`
- **THEN** 输出列出且仅列出 `prepare`、`init`、`run` 三个子命令

#### Scenario: 未知子命令被拒绝
- **WHEN** 执行 `yd-producer bootstrap`
- **THEN** 以非零退出码报错，不执行任何业务逻辑

#### Scenario: prepare 的基线包路径必需
- **WHEN** 执行 `yd-producer prepare` 而不给 `--baseline`
- **THEN** 以 argparse 用法错误退出，不装载配置、不执行任何业务逻辑（基线包路径只经调用传入，代码 MUST NOT 内置默认路径，compute-loop §6.1）

#### Scenario: 私有 worker 不扩展公开入口或产品文件边界
- **WHEN** production driver 构造 Slurm worker 的启动命令
- **THEN** 只以 yd 自己的解释器和精确 argv 启动既有 `yd_producer.nwm` 的私有入口，不新增 module、console-script 配置或公开 `worker` 子命令；既有 NWM mapping-builder 仍使用其指定解释器/cwd/PYTHONPATH，日常 worker 不借用 NWM 环境

### Requirement: `yd-producer run` 必须接通生产控制器
`cli.run` MUST 在同一 `run_with_lock(local.cron.lock_path, ...)` 生命周期内调用一次 `controller.run_sources`，并为固定 `{ifs,gfs}` 注入两份独立的 Slurm executor、两份满足既有 `AttemptDriver` 协议的生产 driver、两个实际等待的 poll-wait callable，以及两个独立 `sacct ExitCode` 失败收尾 provider。poll wait 使用版本化 `POLL_INTERVAL_SECONDS = 10`，每次恰调用 `time.sleep(10)`；该值不是 watchdog/总超时，也不新增 TOML 字段。生产入口还 MUST 用 `partial(subprocess_runner, command_timeout_seconds=local.slurm_command_timeout_seconds)` 创建一份无状态 bounded runner，由两份 executor 与两个 provider 共同消费，使每次 sbatch/两类 sacct 使用同一客户端时限；不得让 timeout 进入 JobSpec resources。MUST NOT 以 `FakeJobExecutor`、测试 terminal hook、no-op wait、目录扫描或 staged-unimplemented 分支冒充生产接线；`run_sources`、`run_once`、`catch_up_source` 与七字段 `JobRecord` 的公共签名保持不变。

生产 driver MUST 让 canonical/forcing/`assemble_staged`/SHUD/tracker/recovery 在 Slurm job 内执行，并通过原子、checksum/identity 绑定的 work-local receipt 把同一 source/cycle/work/job 的 `RunDirectory`、DAT、merged log 与已验证 T+12 checkpoint 交给 `collect`；receipt 还 MUST 绑定同一 `WorkIdentity`（含四个 prepared-variant 稳定版本标识）与 direct-grid binding/`.sp.att` checksum。worker 必须先重调 #177 `load_staged_work_inputs`，再从本进程已验证的 `staged.prepared` 创建 registry 和 forcing，最后调用 `assemble_staged(*, registry, staged_inputs, forcing)`；MUST NOT 把 work-relative 输入根交给 legacy `assemble(...)` 或放宽其 outside-work guard。登录节点不得预组装、补跑或从规范文件名重建 checkpoint authority。M4 只负责 node-22 真实 Slurm/NFS/SHUD receipt 与 cron 安装，不负责补写 CLI 业务体。

production driver 的 `WorkIdentity` 与 direct-grid 输入 MUST 走唯一的 prepared-variant handoff：`source_id`、`cycle_time` 逐字来自 controller 的 `AttemptRequest.source`、`.cycle`；`project_name` 只从 `prepare.calibrated_state_path(request.variant_dir)` 指向、prepare 已验证的率定状态文件名仅移除末尾一次 `.cfg.ic` 后取得，MUST NOT 从 `variant_dir` basename 推导。既有 `config.toml` / `local.toml` identity schema 保持闭合，不新增字段。`model_id`、`basin_id`、`basin_version_id`、`river_network_version_id` 必须逐字来自 `load_prepared_variant_handoff` 返回的同一 prepared-variant 稳定版本标识；driver 不得逐 attempt 随机生成或从资产内容补值。登录侧与 #177 controller-staged snapshot 对账，worker 经 staged loader 重建同一模型 snapshot；四值只在同一 scratch registry → forcing → assemble_staged → receipt 链内使用。M4 以真实 node-22 builder/site artifact 对账，M2 synthetic 值不构成现场权威。

worker 只可消费由 prepare 同一 loader 验证、再由 controller `stage_work_inputs` 提交至同一 claimed work 的 `DirectGridForcingContract`、exact opaque binding bytes、manifest 明示的 UTF-8 `.sp.att` bytes 和 exact T state。登录侧 driver 以 `request.variant_dir`、current source/project/grid 与显式 limits 调 #171 唯一 loader，并与 #177 staged snapshot 对账；不读 config identity、环境、`DATABASE_URL`、NWM PostgreSQL/服务型 registry，不扫描 manifest/asset，不从变体 basename、`yd.binding` 内容或测试 fixture 推导 identity、contract、路径或 bytes。`contract.binding_uri`/`contract.sp_att_path` 只是 registry 提交后的 relative keys，不是 prepared asset 读取路径。attempt handoff 只绑定 work/source/cycle、staged manifest digest 与 work-local 引用，不传 NFS source 路径、父进程 capability、fd 或跨进程 inode。worker 使用前重新调用 `load_staged_work_inputs` 并复核 digest，receipt 再绑定同一 source/cycle/work/job/`WorkIdentity` 与两资产 checksum。缺 manifest/明示 asset、schema/identity/checksum/path 不一致或文件形态不可安全确认时，在提交前发现则 MUST 拒绝 `sbatch`，在 worker/collect 点用发现则 MUST 拒绝该阶段且零 `DONE`。本次合同修复的两段独立进程 oracle 只证明源删除后的 source loader→controller stage→worker staged loader/registry/真实 forcing/assemble_staged 可执行性，不证明 canonical 转换、SHUD、tracker/recovery、原子 receipt 或 collect。生产实现仍必须另以独立进程完整验证原子 receipt/collect 与本段所有 MUST；两类 M2 合成证据都不能替代 M4 真实 site artifact。

`run` 的退出码 MUST 为：`0` 表示锁竞争下的成功跳过或返回报告全部为 `SUCCEEDED`；`3` 表示任一源 `STOPPED`/`JOB_FAILED`，以及 `SUCCEEDED_CLEANUP_PENDING` 或运行期 controller/executor/driver/provider 错误；`2` 表示参数或配置错误。raw 缺口产生的 `STOPPED` 因而是 `3`，MUST NOT 为制造退出码 `0` 添加追赶轮数上限。该约定只修改 `run`；`prepare`/`init` 的既有退出码不变。`RunSourcesError` 的单份人读文本 MUST 按 `ifs`、`gfs` 固定顺序包含其中每个底层 `RunError` 及其每条 `__notes__`，每项恰一次；CLI 把该完整文本输出到 stderr 一次且不打印 traceback。这包括 #108 startup hygiene 在首报告前完成的删除审计，不得沿用不含 notes 的旧聚合摘要或另行重复打印底层项。

#### Scenario: 文档中的 run 调用可直接执行
- **WHEN** 运维从 compute-loop 的 synopsis 或 cron 段复制 `run` 命令
- **THEN** 命令逐字包含 `--config <path> --local <path>`，不依赖任何内置配置路径

#### Scenario: 生产依赖在同一锁内注入
- **WHEN** 状态与配置齐备、显式 `command_timeout_seconds = 37` 且锁可取得时执行 `run`
- **THEN** `run_sources` 在锁内恰调用一次，四份按源 mapping 均恰含 `{ifs,gfs}`，两源 executor/driver 实例互不相同，poll wait 会实际等待，失败 provider 遵守独立一次 `sacct -j <job_id> -X -n -P --format=ExitCode` 契约；两 executor 与两 provider 消费同一 bounded runner，sbatch/普通 sacct/ExitCode sacct 的底层 timeout 都为 37，资源映射与 sbatch argv 不含策略键

#### Scenario: production driver 拒绝未明示的 direct-grid 资产
- **WHEN** driver 遇到缺失/非法 prepared-variant v1 manifest 或其明示 `.sp.att`，将 `contract.sp_att_path` 当作变体发现路径，从变体 basename/TOML/环境/数据库/目录扫描/`yd.binding` 内容/测试 fixture 推导任一 identity 或 asset，任一 handoff leaf/ancestor 是 symlink 或非普通文件、超过显式 limit，或任一 schema/bytes/SHA-256/source/project/grid/contract 不一致
- **THEN** 在 `sbatch` 前 fail closed，零 worker receipt、零 DAT、零 `DONE`；M2 独立进程 fixture 使用 #171 source loader 与 #177 controller-staged capability，以 `AttemptRequest.source`/`.cycle/work` 绑定 attempt identity；真实 builder `.sp.att` layout/parser 与 site 值仍由 M4 核验

#### Scenario: 独立 worker 在源断开后只消费 staged capability
- **WHEN** controller 通过 `stage_work_inputs` 提交合法 exact-five variant 与 exact T state 后删除或禁止访问 NFS source，新的 worker 进程只收到 work-local handoff
- **THEN** worker 通过 `load_staged_work_inputs` 重建本进程 capability 并校验 manifest digest，执行真实 registry/forcing/`assemble_staged`，state bytes 保持 exact T；worker argv、环境、handoff、receipt 不含 NFS source 路径或跨进程 inode，legacy `assemble` 对 work-relative 根的拒绝保持

#### Scenario: staged 点用漂移不得进入发布
- **WHEN** worker load、assemble 或 collect 点用时发现 staged layout、manifest digest、内容 checksum、source/cycle/work 或本进程 root identity 不一致
- **THEN** 当前阶段 fail closed，零 `DONE`，不在登录节点补跑、不采用规范文件名恢复、不改写或删除 replacement work

#### Scenario: run 退出码区分结果与配置错误
- **WHEN** 分别出现全成功报告、任一 `STOPPED`、任一 `JOB_FAILED`、cleanup pending、运行期错误，以及参数/配置错误
- **THEN** 退出码分别为 `0`、`3`、`3`、`3`、`3`、`2`，stderr 不含 traceback；锁已被持有时以 `0` 跳过且零 controller 调用

#### Scenario: 双源聚合错误完整渲染 startup cleanup notes
- **WHEN** `RunSourcesError` 的 IFS/GFS 底层错误各带互异 `__notes__`，且 IFS note 含不在异常正文中的 #108 startup cleanup source/cycle/path 清单
- **THEN** `RunSourcesError` 的单份文本按 IFS/GFS 顺序包含每个底层错误及每条 note 恰一次；`run` 返回 `3` 并把该文本输出到 stderr 一次，不含 traceback，不丢失或重复清理审计

### Requirement: config.toml 装载与校验
装载器 MUST 解析版本化 `config.toml` 的全部业务规则字段：cycle 固定 00/12、IFS/GFS raw 完整性规则（变量、bundle 文件模式、f000 特例）、两个模型变体相对路径、`forecast_days=7`、`output_interval_minutes=60`、`checkpoint_hours=[12]`、`reach_count`（生产配置为 3988，products-contract §5）、Slurm 资源字段结构、NWM mapping-builder module 点分名 `nwm_mapping_builder_module` 与每 source 的 NWM canonical grid 标识 `nwm_canonical_grid_id.gfs`/`.ifs`（两者均为版本化快照事实，非现场值）；任何必需字段缺失或类型错误 MUST fail closed。

装载器 MUST 且只 MUST 认领四类取值域：`cycle.hours` 的每个值都属于 `{0,12}`、`forecast_days > 0`、`checkpoint_hours` 的每个值满足 `0 <= hour < 24 * forecast_days`，以及 `raw.ifs.variables` / `raw.gfs.variables` 各自在 source 内单射。前三类违反时抛 `ConfigError`，其结构化 `path` 分别为 `cycle.hours`、`forecast_days`、`checkpoint_hours`；variables 同一 source 内出现重复精确字符串时同样抛 `ConfigError`，`path` 精确为对应的 `raw.<source>.variables`。跨 source 同名与同一 source 内大小写不同的字符串不构成重复；不得大小写归一或静默去重。其它取值域仍归既有下游 owner，不得借本 Requirement 擅自迁入装载器。

#### Scenario: 完整配置装载成功
- **WHEN** 载入包含全部必需字段的 `config.toml`
- **THEN** 返回类型化配置对象，各字段值与文件一致

#### Scenario: 缺失必需字段即报错
- **WHEN** 载入缺少 `forecast_days` 的 `config.toml`
- **THEN** 装载器报错并指明缺失字段名，不返回带默认值的配置

#### Scenario: cycle、forecast 与 checkpoint 取值域在装载边界 fail closed
- **WHEN** `cycle.hours` 含非 00/12、`forecast_days <= 0`，或 `checkpoint_hours` 含小于 0 / 大于等于 `24 * forecast_days` 的值
- **THEN** 装载器抛 `ConfigError`，`path` 精确指向对应字段，不返回配置对象；checkpoint 上界随 `forecast_days` 改变，不写死 168

#### Scenario: raw variables 在每个 source 内保持单射
- **WHEN** `raw.ifs.variables` 或 `raw.gfs.variables` 在同一列表中重复任一精确字符串
- **THEN** 装载器抛 `ConfigError`，`path` 精确指向该 source 的 `raw.<source>.variables`，消息把每个具体重复变量名各列一次且不列未重复名，不返回配置对象且不静默去重；另一个 source 是否含同名变量不影响判定（不承诺重复名的显示顺序）

### Requirement: local.toml 现场值不得猜测
装载器 MUST 从 gitignored `local.toml` 读取现场值（`yd_root`、`scratch_root`、NWM raw 根、NWM checkout 根与解释器路径（仅 prepare）、SHUD 二进制、Slurm partition/account/CPU/内存/walltime、cron lock 与日志位置）；文件缺失或必需字段缺失 MUST 明确报错。唯一例外是 #69 明确授权的 `[slurm].command_timeout_seconds`：它是每次 `sbatch`/`sacct` 客户端子进程的时限，缺席时 MUST 使用唯一内部版本化常量 `yd_producer.config._DEFAULT_SLURM_COMMAND_TIMEOUT_SECONDS = 60`，显式值 MUST 是 strict positive `int`，不是 Slurm 作业 walltime。

`Config.slurm.required_fields` 仍是资源键集的唯一权威，并 MUST NOT 声明保留名 `command_timeout_seconds`。`LocalConfig.slurm` MUST 以只读 `Mapping[str, str | int]` 暴露且只含与 `required_fields` 完全相等的资源投影；装载器从 TOML 表中剥离 timeout，复制资源并用 `types.MappingProxyType` 冻结，调用方不得增删改。timeout 暴露为 additive `LocalConfig.slurm_command_timeout_seconds: int = _DEFAULT_SLURM_COMMAND_TIMEOUT_SECONDS`；字段默认只保持既有程序内 `LocalConfig(...)` 构造兼容，不授权任何其它 local 默认值，也不得把策略键传入 `JobSpec.resources` 或翻译成 `sbatch` flag。

#### Scenario: local.toml 缺失
- **WHEN** 指定路径不存在 `local.toml`
- **THEN** 报错退出并提示需要现场创建，不使用任何内置路径

#### Scenario: 现场字段齐备
- **WHEN** `local.toml` 提供全部必需现场字段以及显式 `command_timeout_seconds = 45`
- **THEN** 配置对象暴露这些值供 `prepare`/`init`/`run` 使用；`LocalConfig.slurm` 是不含 timeout 的 `MappingProxyType` 资源快照，`slurm_command_timeout_seconds == 45`，装载后修改输入或尝试改写资源映射均不能改变配置对象

#### Scenario: Slurm 客户端命令时限有唯一默认
- **WHEN** `[slurm]` 省略 `command_timeout_seconds`
- **THEN** `slurm_command_timeout_seconds == 60`，资源映射键集仍恰等于 `required_fields`；直接用旧参数构造 `LocalConfig` 同样得到 60，除此之外没有字段获得默认值

#### Scenario: 非正整数命令时限被拒绝
- **WHEN** `command_timeout_seconds` 为 bool、float、string、0 或负数，或 `required_fields` 含该保留名
- **THEN** 装载器抛 `ConfigError`，`path` 分别精确为 `slurm.command_timeout_seconds` 或 `slurm.required_fields`，不返回配置对象

### Requirement: run 永不自动 bootstrap
`run` 发现状态目录缺失或为空时 MUST 报错停止，MUST NOT 调用 init 逻辑或自建状态。

#### Scenario: 状态目录缺失
- **WHEN** `states/` 不存在时执行 `run`
- **THEN** 报错退出，`states/` 仍不存在，未提交任何作业

### Requirement: NWM 解释器薄外壳 fail closed
`prepare` 调用 mapping-builder MUST 使用 `local.toml` 指定的精确解释器路径；路径不存在或不可执行 MUST 报错退出，MUST NOT 回退到 `uv run`、`--active` 或系统 Python。

#### Scenario: 解释器缺失即停
- **WHEN** `local.toml` 的 NWM 解释器路径不存在
- **THEN** `prepare` 报错退出，未发起任何 builder 调用

#### Scenario: 以精确解释器调用
- **WHEN** 解释器路径指向可执行文件（测试用假解释器脚本）
- **THEN** 薄外壳以该路径调用 `config.toml` 的 `nwm_mapping_builder_module` 所指 module，调用命令中不出现其它解释器，module 解析上下文（cwd/`PYTHONPATH`）来自 `local.toml` 的 NWM checkout 字段

### Requirement: prepare 的清理告警与残留证据 MUST 到达运维
`prepare` 收集到的清理/回滚失败是总不变量被破坏时的**唯一证据**（agent-ops §8.1 要求每次 `prepare` 调用留 receipt）。CLI MUST 把它们打到 stderr：失败路径上 MUST 渲染在途异常的 `__notes__`（`str(exc)` 不含 notes），成功路径上 MUST 渲染报告的 `cleanup_warnings`。退出码 MUST NOT 因此改变，且 MUST NOT 打印 traceback。

#### Scenario: 失败路径的清理失败随错误一并打印
- **WHEN** `main(["prepare", ...])` 走生产 builder 绑定且清理原语注入失败
- **THEN** 退出码仍为 `3`，stderr 同时含 `BuilderUnavailableError` 消息与该清理失败文本，且不含 `Traceback`

#### Scenario: 成功路径的清理告警打印且不改退出码
- **WHEN** 注入的 `run_prepare` fake 返回带非空 `cleanup_warnings` 的报告
- **THEN** 退出码为 `0`，stderr 含每条告警文本

#### Scenario: 退出码 1 的失败路径同样渲染 notes
- **WHEN** 注入的 `run_prepare` fake 抛出带 `__notes__` 的 `PrepareError`（note 文本 MUST NOT 是 `str(exc)` 的子串，否则 `_fail` 单独即可满足断言、不具判别性）
- **THEN** 退出码为 `1`，stderr 同时含异常消息与 note 文本，且不含 `Traceback`

### Requirement: 拒绝 NWM 数据库环境
producer 任一入口启动时检测到 `DATABASE_URL` 环境变量 MUST 视为配置错误并拒绝执行（agent-ops §2.2）。

#### Scenario: DATABASE_URL 存在即停
- **WHEN** 环境中设置了 `DATABASE_URL` 时执行 `run`
- **THEN** 报错退出，不进行任何发现或提交
