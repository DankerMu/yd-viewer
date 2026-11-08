## Why

2026-11-08 现场：计算节点 cn02 缺少 `libmpi.so.40`（用 `srun -w cn02 ldd shud` 实测），yd 在 cn02 上跑过的 2 个作业都以 SHUD 退出码 127 失败。cn02 目前是 `idle`，仍会被调度。根治要靠集群管理员，yd 侧需要一个现场可配置的节点排除。docs 已先行修改（PR #383：compute-loop §5、agent-ops §8.3）。Closes #382。

**硬约束：简化，不过度设计。**

```text
Issue type: feature
Fixture level: expanded
Upstream suggested level: absent (expanded: 命中 production config、public API（导出的 `build_sbatch_command`、`LocalConfig`）、legacy compatibility 和 Slurm/sbatch domain 触发)
Blast radius: 改错 → 旧配置被拒、run 整体失败，或 argv 变化导致 sbatch 拒绝提交，或空列表误生成 `--exclude=`
Selected risk packs: Config; Schema / field names; Error handling; Public API / CLI (sbatch argv); Legacy compatibility; Documentation
Evidence floor: producer ruff + format + pytest 全绿；缺省/空列表 argv 逐字节不变、单个/多个节点、非法值报错、不计入资源键集、cli 接线的测试红→绿
```

## What Changes

- `config._build_local`：从 `[slurm]` 表中剥离可选键 `exclude_nodes`（与 `command_timeout_seconds` 一样不计入资源键集），校验后暴露为 additive 字段 `LocalConfig.slurm_exclude_nodes: tuple[str, ...] = ()`。合法值是 TOML 字符串数组，每个元素非空、不含逗号和空白；其它情形抛 `ConfigError`，`path == "slurm.exclude_nodes"`。`required_fields` 不得声明这个保留名。
- `slurm.build_sbatch_command(spec, *, required_fields, exclude_nodes=())`：`exclude_nodes` 是 keyword-only 参数。非空时紧接资源段之后、`--wrap` 之前插入一项 `--exclude=<",".join(exclude_nodes)>`；为空时 argv 与改动前逐字节相同。
- `SlurmJobExecutor(*, required_fields, clock, runner, exclude_nodes=())`：`exclude_nodes` 是 keyword-only 参数，保存后传给 `build_sbatch_command`。同时把 docstring「三个构造参数均 keyword-only 且无默认值」改成准确描述。
- `cli.run` 用 `exclude_nodes=local.slurm_exclude_nodes` 构造 executor。

Must preserve:
- 不含该键的 `local.toml` 装载结果与改动前相同；`LocalConfig(...)` 旧构造兼容（字段有默认值）。
- `LocalConfig.slurm` 资源映射、`JobSpec.resources`、`JobRecord.resources` 都不含 exclude。
- `sacct` 两条命令（轮询、ExitCode）不变。
- `SBATCH_FLAGS` 不变，exclude 不是资源。
- 现有 `test_config.py`、`test_cli.py`、`test_slurm.py`、`test_slurm_executor.py` 原样通过。`test_cli.py` 已满 1000 行，新用例放进新文件。

Non-goals：坏节点自动探测、重试换节点、`--nodelist`、改 NWM、改 `config.toml`。

## Capabilities

### Modified Capabilities
- `cli-config`：local.toml 现场值不得猜测——新增可选 `exclude_nodes` 例外。
- `run-controller`：作业提交经执行器抽象——`exclude_nodes` 非空时 sbatch 追加 `--exclude`。

## Impact

- `producer/src/yd_producer/{config.py,slurm.py,cli.py}`；新测试文件 `producer/tests/test_slurm_exclude_nodes.py`。
- 现场：先把 node-22 checkout 更新到新代码，再在 `local.toml` 里加 `exclude_nodes = ["cn02"]`（先备份）。旧代码会因为多余的键拒绝整个 run。
