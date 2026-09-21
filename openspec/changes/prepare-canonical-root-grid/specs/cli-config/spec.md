## MODIFIED Requirements

### Requirement: local.toml 现场值不得猜测
装载器 MUST 从 gitignored `local.toml` 读取现场值（`yd_root`、`scratch_root`、NWM raw 根、NWM object-store canonical 根（仅 prepare 读 grid 定义）、NWM checkout 根与解释器路径（仅 prepare）、SHUD 二进制、Slurm partition/account/CPU/内存/walltime、cron lock 与日志位置）；文件缺失或必需字段缺失 MUST 明确报错。`[nwm]` 的 `raw_root`、`canonical_root`、`checkout_root`、`python` 四键都必需，缺一即 `ConfigError` 并点名该键（如 `nwm.canonical_root`），MUST NOT 为任何一键内置默认或从另一键推导。唯一例外是 #69 明确授权的 `[slurm].command_timeout_seconds`：它是每次 `sbatch`/`sacct` 客户端子进程的时限，缺席时 MUST 使用唯一内部版本化常量 `yd_producer.config._DEFAULT_SLURM_COMMAND_TIMEOUT_SECONDS = 60`，显式值 MUST 是 strict positive `int`，不是 Slurm 作业 walltime。

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

#### Scenario: 缺 NWM canonical 根即停
- **WHEN** `local.toml` 的 `[nwm]` 提供 `raw_root`、`checkout_root`、`python` 但缺 `canonical_root`
- **THEN** `load_local` 抛 `ConfigError`，其 `path` 为 `nwm.canonical_root`，不产生配置对象，也不从 `raw_root` 或 `checkout_root` 推导该值
