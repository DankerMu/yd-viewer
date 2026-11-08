## 0. Risk packs

- Config：选中。新的可选键、校验规则、保留名，对应 2.1–2.4。
- Public API / CLI（sbatch argv）：选中。argv 只在非空时追加一项，对应 2.5、2.6。
- Legacy compatibility：选中。缺省时装载结果和 argv 都逐字节不变，旧构造兼容，对应 2.1、2.5。
- Documentation：选中。compute-loop §5、agent-ops §8.3 已由 PR #383 先行修改，对应 3.3。
- 不选的包：
  - File IO：不涉及文件写入。
  - Concurrency：不涉及。
  - Resource limits：列表长度由现场决定，不需要上界。

## 1. 实现

- [ ] 1.1 `config.py`：剥离并校验 `exclude_nodes`，加 additive 字段 `LocalConfig.slurm_exclude_nodes: tuple[str, ...] = ()`，`required_fields` 拒绝这个保留名
- [ ] 1.2 `slurm.py`：`build_sbatch_command` 增加 `exclude_nodes=()` 参数；`SlurmJobExecutor` 增加 `exclude_nodes=()` 参数
- [ ] 1.3 `cli.py`：构造 executor 时传入 `local.slurm_exclude_nodes`

## 2. 测试（新文件 `producer/tests/test_slurm_exclude_nodes.py`）

- [ ] 2.1 缺省：装载结果 `slurm_exclude_nodes == ()`，资源映射键集不变；`LocalConfig(...)` 旧构造得到 `()`
- [ ] 2.2 合法值：`["cn02"]` 得到 `("cn02",)`，`["cn02","cn13"]` 保持原顺序；资源映射不含 exclude
- [ ] 2.3 非法值：非数组（字符串、int、表）、元素不是字符串、空串、含逗号、含空白 → `ConfigError`，`path == "slurm.exclude_nodes"`
- [ ] 2.4 `required_fields` 含 `exclude_nodes` → `ConfigError`，`path == "slurm.required_fields"`
- [ ] 2.5 argv：`exclude_nodes=()` 时与不传参数逐字节相同；`("cn02",)` 时恰多一项 `--exclude=cn02`，`("cn02","cn13")` 时为 `--exclude=cn02,cn13`，其余项与顺序不变
- [ ] 2.6 executor 与 cli 接线：fake runner 记录 sbatch argv；`SlurmJobExecutor(exclude_nodes=("cn02",))` 提交时 argv 含 `--exclude=cn02`，`sacct` argv 不含；cli `run` 装配的 executor 拿到的是 `local.slurm_exclude_nodes`
- [ ] 2.7 红跑：还原 src 后 2.2–2.6 失败

## 3. 验证

- [ ] 3.1 `cd producer && uv run ruff check . && uv run ruff format --check . && uv run pytest` → 全绿
- [ ] 3.2 在仓库根目录执行 `uv run python scripts/large_file_guard.py` → passed
- [ ] 3.3 `openspec validate slurm-exclude-nodes --strict --no-interactive` → valid
- [ ] 3.4 现场（orchestrator）：先把 node-22 checkout ff-only 到新代码，再备份 `local.toml`、加入 `exclude_nodes = ["cn02"]`；首个 tick 正常；之后 yd 作业的 `sacct NodeList` 都不是 cn02；留 receipt
