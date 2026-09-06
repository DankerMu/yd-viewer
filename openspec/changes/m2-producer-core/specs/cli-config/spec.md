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

### Requirement: `yd-producer run` 必须接通生产控制器
`cli.run` MUST 在同一 `run_with_lock(local.cron.lock_path, ...)` 生命周期内调用一次 `controller.run_sources`，并为固定 `{ifs,gfs}` 注入两份独立的 Slurm executor、两份满足既有 `AttemptDriver` 协议的生产 driver、两个实际等待的 poll-wait callable，以及两个独立 `sacct ExitCode` 失败收尾 provider。poll wait 使用版本化 `POLL_INTERVAL_SECONDS = 10`，每次恰调用 `time.sleep(10)`；该值不是 watchdog/总超时，也不新增 TOML 字段。MUST NOT 以 `FakeJobExecutor`、测试 terminal hook、no-op wait、目录扫描或 staged-unimplemented 分支冒充生产接线；`run_sources`、`run_once`、`catch_up_source` 与七字段 `JobRecord` 的公共签名保持不变。

生产 driver MUST 让 canonical/forcing/assemble/SHUD/tracker/recovery 在 Slurm job 内执行，并通过原子、checksum/identity 绑定的 work-local receipt 把同一 source/cycle/work/job 的 `RunDirectory`、DAT、merged log 与已验证 T+12 checkpoint 交给 `collect`；登录节点不得补跑或从规范文件名重建 checkpoint authority。M4 只负责 node-22 真实 Slurm/NFS/SHUD receipt 与 cron 安装，不负责补写 CLI 业务体。

`run` 的退出码 MUST 为：`0` 表示锁竞争下的成功跳过或返回报告全部为 `SUCCEEDED`；`3` 表示任一源 `STOPPED`/`JOB_FAILED`，以及 `SUCCEEDED_CLEANUP_PENDING` 或运行期 controller/executor/driver/provider 错误；`2` 表示参数或配置错误。raw 缺口产生的 `STOPPED` 因而是 `3`，MUST NOT 为制造退出码 `0` 添加追赶轮数上限。该约定只修改 `run`；`prepare`/`init` 的既有退出码不变。

#### Scenario: 文档中的 run 调用可直接执行
- **WHEN** 运维从 compute-loop 的 synopsis 或 cron 段复制 `run` 命令
- **THEN** 命令逐字包含 `--config <path> --local <path>`，不依赖任何内置配置路径

#### Scenario: 生产依赖在同一锁内注入
- **WHEN** 状态与配置齐备且锁可取得时执行 `run`
- **THEN** `run_sources` 在锁内恰调用一次，四份按源 mapping 均恰含 `{ifs,gfs}`，两源 executor/driver 实例互不相同，poll wait 会实际等待，失败 provider 遵守独立一次 `sacct -j <job_id> -n -P --format=ExitCode` 契约

#### Scenario: run 退出码区分结果与配置错误
- **WHEN** 分别出现全成功报告、任一 `STOPPED`、任一 `JOB_FAILED`、cleanup pending、运行期错误，以及参数/配置错误
- **THEN** 退出码分别为 `0`、`3`、`3`、`3`、`3`、`2`，stderr 不含 traceback；锁已被持有时以 `0` 跳过且零 controller 调用

### Requirement: config.toml 装载与校验
装载器 MUST 解析版本化 `config.toml` 的全部业务规则字段：cycle 固定 00/12、IFS/GFS raw 完整性规则（变量、bundle 文件模式、f000 特例）、两个模型变体相对路径、`forecast_days=7`、`output_interval_minutes=60`、`checkpoint_hours=[12]`、`reach_count`（生产配置为 3988，products-contract §5）、Slurm 资源字段结构、NWM mapping-builder module 点分名 `nwm_mapping_builder_module` 与每 source 的 NWM canonical grid 标识 `nwm_canonical_grid_id.gfs`/`.ifs`（两者均为版本化快照事实，非现场值）；任何必需字段缺失或类型错误 MUST fail closed。

装载器还 MUST 且只 MUST 在本裁决中认领三条取值域：`cycle.hours` 的每个值都属于 `{0,12}`、`forecast_days > 0`、`checkpoint_hours` 的每个值满足 `0 <= hour < 24 * forecast_days`。违反时抛 `ConfigError`，其结构化 `path` 分别为 `cycle.hours`、`forecast_days`、`checkpoint_hours`。其它取值域仍归既有下游 owner，不得借本 Requirement 擅自迁入装载器。

#### Scenario: 完整配置装载成功
- **WHEN** 载入包含全部必需字段的 `config.toml`
- **THEN** 返回类型化配置对象，各字段值与文件一致

#### Scenario: 缺失必需字段即报错
- **WHEN** 载入缺少 `forecast_days` 的 `config.toml`
- **THEN** 装载器报错并指明缺失字段名，不返回带默认值的配置

#### Scenario: 三条取值域在装载边界 fail closed
- **WHEN** `cycle.hours` 含非 00/12、`forecast_days <= 0`，或 `checkpoint_hours` 含小于 0 / 大于等于 `24 * forecast_days` 的值
- **THEN** 装载器抛 `ConfigError`，`path` 精确指向对应字段，不返回配置对象；checkpoint 上界随 `forecast_days` 改变，不写死 168

### Requirement: local.toml 现场值不得猜测
装载器 MUST 从 gitignored `local.toml` 读取现场值（`yd_root`、`scratch_root`、NWM raw 根、NWM checkout 根与解释器路径（仅 prepare）、SHUD 二进制、Slurm partition/account/CPU/内存/walltime、cron lock 与日志位置）；文件缺失或字段缺失 MUST 明确报错，代码中 MUST NOT 内置任何现场默认值。`LocalConfig.slurm` MUST 以只读 `Mapping[str, str | int]` 暴露，装载器复制校验后的值并用 `types.MappingProxyType` 冻结；调用方不得通过该字段增删改资源配置。键集的唯一权威仍是 `Config.slurm.required_fields`，不得改成固定 Slurm 字段 dataclass。

#### Scenario: local.toml 缺失
- **WHEN** 指定路径不存在 `local.toml`
- **THEN** 报错退出并提示需要现场创建，不使用任何内置路径

#### Scenario: 现场字段齐备
- **WHEN** `local.toml` 提供全部必需现场字段
- **THEN** 配置对象暴露这些值供 `prepare`/`init`/`run` 使用；`LocalConfig.slurm` 是 `MappingProxyType` 只读快照，装载后修改输入或尝试改写该映射均不能改变配置对象

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
