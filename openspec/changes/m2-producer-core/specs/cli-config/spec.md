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

### Requirement: config.toml 装载与校验
装载器 MUST 解析版本化 `config.toml` 的全部业务规则字段：cycle 固定 00/12、IFS/GFS raw 完整性规则（变量、bundle 文件模式、f000 特例）、两个模型变体相对路径、`forecast_days=7`、`output_interval_minutes=60`、`checkpoint_hours=[12]`、`reach_count`（生产配置为 3988，products-contract §5）、Slurm 资源字段结构；任何必需字段缺失或类型错误 MUST fail closed。

#### Scenario: 完整配置装载成功
- **WHEN** 载入包含全部必需字段的 `config.toml`
- **THEN** 返回类型化配置对象，各字段值与文件一致

#### Scenario: 缺失必需字段即报错
- **WHEN** 载入缺少 `forecast_days` 的 `config.toml`
- **THEN** 装载器报错并指明缺失字段名，不返回带默认值的配置

### Requirement: local.toml 现场值不得猜测
装载器 MUST 从 gitignored `local.toml` 读取现场值（`yd_root`、`scratch_root`、NWM raw 根、NWM checkout 根与解释器路径（仅 prepare）、SHUD 二进制、Slurm partition/account/CPU/内存/walltime、cron lock 与日志位置）；文件缺失或字段缺失 MUST 明确报错，代码中 MUST NOT 内置任何现场默认值。

#### Scenario: local.toml 缺失
- **WHEN** 指定路径不存在 `local.toml`
- **THEN** 报错退出并提示需要现场创建，不使用任何内置路径

#### Scenario: 现场字段齐备
- **WHEN** `local.toml` 提供全部必需现场字段
- **THEN** 配置对象暴露这些值供 `prepare`/`init`/`run` 使用

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
- **THEN** 薄外壳以该路径调用配置的 mapping-builder module，调用命令中不出现其它解释器，module 解析上下文（cwd/`PYTHONPATH`）来自 `local.toml` 的 NWM checkout 字段

### Requirement: 拒绝 NWM 数据库环境
producer 任一入口启动时检测到 `DATABASE_URL` 环境变量 MUST 视为配置错误并拒绝执行（agent-ops §2.2）。

#### Scenario: DATABASE_URL 存在即停
- **WHEN** 环境中设置了 `DATABASE_URL` 时执行 `run`
- **THEN** 报错退出，不进行任何发现或提交
