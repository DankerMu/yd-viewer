## 0. Risk packs

- Config：选中。新的可选键、校验规则、保留名，对应 2.1–2.4。
- Public API / CLI（sbatch argv）：选中。argv 只在非空时追加一项，对应 2.5、2.6。
- Legacy compatibility：选中。缺省时装载结果和 argv 都逐字节不变，旧构造兼容，对应 2.1、2.5。
- Documentation：选中。compute-loop §5、agent-ops §8.3 已由 PR #383 先行修改，对应 3.3。
- Schema / field names：选中。新增 TOML 字段 `exclude_nodes` 与 `LocalConfig.slurm_exclude_nodes`，对应 2.1–2.4。
- Error handling：选中。非法值时 `ConfigError` fail closed，不返回配置对象，对应 2.3、2.4。
- 不选的包：
  - File IO：不涉及文件写入。
  - Concurrency：不涉及。
  - Resource limits：列表长度由现场决定，不需要上界。

## 1. 实现

- [x] 1.1 `config.py`：剥离并校验 `exclude_nodes`，加 additive 字段 `LocalConfig.slurm_exclude_nodes: tuple[str, ...] = ()`，`required_fields` 拒绝这个保留名
- [x] 1.2 `slurm.py`：`build_sbatch_command` 与 `SlurmJobExecutor.__init__` 各增加 keyword-only 参数 `exclude_nodes=()`；更新 executor 的 docstring 和 `test_slurm_executor.py:331-349` 的构造参数钉子；`config.py` 中 `load_local`、`_build_local_slurm` 的 docstring 同步更新
- [x] 1.3 `cli.py`：构造 executor 时传入 `local.slurm_exclude_nodes`
- [x] 1.4 同步修改 `test_config.py` 的两个字段结构钉子：
  - `test_only_local_command_timeout_field_carries_a_default`（~L2064）改为：恰好只有 `slurm_command_timeout_seconds` 和 `slurm_exclude_nodes` 两个字段有默认值，并钉死后者的默认值是 `()`。
  - `test_complete_round_trip_expected_leaves_are_distinct[local]`（~L900）的叶子数从 11 改为 12；在 `VALID_LOCAL_B` / `EXPECTED_LOCAL_B` 中加入 `exclude_nodes = ["cn02"]`，A 保持缺省得到 `()`，使 A/B 的叶子仍逐项不同。

## 2. 测试（新文件 `producer/tests/test_slurm_exclude_nodes.py`）

- [x] 2.1 缺省：省略键和显式写 `exclude_nodes = []` 时，装载结果都是 `slurm_exclude_nodes == ()`，资源映射键集不变；`LocalConfig(...)` 旧构造得到 `()`
- [x] 2.2 合法值：`["cn02"]` 得到 `("cn02",)`，`["cn02","cn13"]` 保持原顺序；资源映射不含 exclude
- [x] 2.3 非法值 → `ConfigError`，`path == "slurm.exclude_nodes"`。逐项：`"cn02"`（字符串）、`2`、`{a=1}`（表）、`[2]`、`[""]`、`["cn02,cn13"]`、`["cn 02"]`、`["cn02\t"]`
- [x] 2.4 `required_fields` 含 `exclude_nodes` → `ConfigError`，`path == "slurm.required_fields"`
- [x] 2.5 argv 完整 tuple 断言：`exclude_nodes=()` 时等于 `EXPECTED_SBATCH`；`("cn02",)` 时等于 `EXPECTED_SBATCH[:-2] + ("--exclude=cn02",) + EXPECTED_SBATCH[-2:]`；`("cn02","cn13")` 时同理，该项为 `--exclude=cn02,cn13`
- [x] 2.6 executor 与 cli 接线：fake runner 记录 sbatch argv；`SlurmJobExecutor(exclude_nodes=("cn02",))` 提交时 argv 含 `--exclude=cn02`，`sacct` argv 不含；cli：monkeypatch `cli.SlurmJobExecutor` 记录调用的 kwargs（参照 `test_cli.py:682` 的写法），`local.toml` 含 `exclude_nodes = ["cn02"]` 时，断言 ifs、gfs 两个源都拿到 `exclude_nodes == ("cn02",)`
- [x] 2.7 红跑：还原 src 后 2.2–2.6 失败

## 3. 验证

- [x] 3.1 `cd producer && uv run ruff check . && uv run ruff format --check . && uv run pytest` → 全绿
- [x] 3.2 在仓库根目录执行 `uv run python scripts/large_file_guard.py` → passed
- [x] 3.3 `openspec validate slurm-exclude-nodes --strict --no-interactive` → valid
- [ ] 3.4 现场（orchestrator）：先把 node-22 checkout ff-only 到新代码，再备份 `local.toml`、加入 `exclude_nodes = ["cn02"]`；首个 tick 正常；之后 yd 作业的 `sacct NodeList` 都不是 cn02；留 receipt。回滚顺序与部署相反：先从 `local.toml` 删除 `exclude_nodes`，再回退 checkout（否则旧代码每个 tick 装载配置都会失败）
