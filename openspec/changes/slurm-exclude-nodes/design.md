Change surface: `config._build_local` / `_build_local_slurm`（剥离可选键）、`LocalConfig`（additive 字段）、`slurm.build_sbatch_command` 和 `SlurmJobExecutor`（keyword-only 参数）、`cli.run` 构造 executor 的接线。

Must preserve:
- 不含 `exclude_nodes` 的 `local.toml`（或写成 `exclude_nodes = []`）装载结果与改动前相同，sbatch argv 逐字节相同（`tests/slurm_fixtures.py:77` `EXPECTED_SBATCH`）。
- `LocalConfig(...)`、`SlurmJobExecutor(...)`、`build_sbatch_command(...)` 现有的关键字调用（`cli.py:200`、`tests/slurm_fixtures.py:132`、`test_slurm*.py`）不改也能用。`test_slurm_executor.py:331-349` 对构造参数的钉子，按新增的带默认 keyword-only 参数同步更新。
- `LocalConfig.slurm`、`JobSpec.resources`、`JobRecord.resources` 都不含 exclude；`SBATCH_FLAGS` 不变；两条 `sacct` 命令不变。
- `prepare` 和 `init` 也经 `load_local` 读同一张表：这个键对它们只做校验，不产生其它影响。
- `load_local` 与 `_build_local_slurm` 的 docstring 里「唯一例外」「除 `command_timeout_seconds` 外」的措辞同步更新。

Must add/change:
- 校验：`table.get("exclude_nodes", [])` 必须是 `list`，每个元素必须是 `str`、非空，并且不含 `,`，也不含任何 `str.isspace()` 为真的字符；否则抛 `ConfigError`，`path == "slurm.exclude_nodes"`。合法值转成 tuple，保持原顺序；不去重，也不排序。
- `required_fields` 声明 `exclude_nodes` 时抛 `ConfigError`，`path == "slurm.required_fields"`，与 `command_timeout_seconds` 的处理一致。
- argv：`[*既有前缀, *资源段, "--exclude=a,b", "--wrap", cmd]`。

Governing invariant: `slurm_exclude_nodes == ()` 时，系统的全部可观测行为与改动前逐字节相同；非空时，唯一的差异是每次 sbatch argv 在 `--wrap` 之前多出一项 `--exclude=…`。

Sibling surfaces: `prepare` 与 `init` 的 `load_local`；`slurm_fixtures.py` 中的 executor 工厂；`controller.py:781-807` 对 `local.slurm` 键集的检查（资源映射不变，所以不受影响）。

Required evidence: 见 tasks.md §2，其中 argv 一律按完整 tuple 断言。

Review focus: 缺省时逐字节不变；校验覆盖全部空白字符；cli 两个源都传到了值；keyword-only。
