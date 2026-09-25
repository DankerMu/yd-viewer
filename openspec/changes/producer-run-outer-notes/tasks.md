## 0. Risk packs

- Public API / CLI / script entry — 选中：run 的 stderr 与退出码 → 1.1、2.1–2.4。
- Error handling — 选中：三个 handler 的 note 渲染 → 2.1–2.3。
- Legacy compatibility — 选中：退出码、prepare/init 下 OSError re-raise、既有 run 日志测试 → 2.4、3.1。
- Documentation / migration notes — 选中：docstring 与注释修正、spec delta → 1.2、3.3；docs-first 不需要（compute-loop §6 `docs/compute-loop-design.md:213` 已要求失败路径每条 note 加前缀），列为显式 non-goal。
- File IO、Concurrency、Config、Permissions — 不选：不改 runlock、IO 与配置逻辑。

## 1. 实现

- [ ] 1.1 `producer/src/yd_producer/cli.py`：`_StatesGuardFailed`（run 内）、`RunSourcesError`、run 下 `OSError` 三个 handler 改为 `_run_fail(str(exc), *_notes(exc), code=…)`，退出码与 re-raise 分支不变
- [ ] 1.2 修正 `_print_notes` docstring 中「`prepare` 与 `run` 的 handler 都直接调用本函数」为只由 prepare 路径（`PrepareError`、非 run 的 `ConfigError`）调用；`:454` 注释改为「这三类只在 `run_sources` 调用链上抛出」

## 2. 测试（`producer/tests/test_cli_run_log.py` 或同级新文件；note 不是 `str(exc)` 的子串）

注入方式：用**真实** `run_with_lock`（不替换 `cli.run_with_lock`），在 `action()` 执行途中 `unlink` 锁文件触发真实 identity 漂移，同 `test_controller_lock_identity.py` 的 `test_exit_unavailable_note_when_path_missing`；断言真实 note 文本 `cron.lock_path … identity drifted`。
- 2.1：monkeypatch `cli._check_states_dir`（在 `action()` 内被调用，`cli.py:191`）为先 unlink 锁文件、再返回拒绝理由字符串；
- 2.2/2.3：monkeypatch `cli.run_sources` 为先 unlink 锁文件、再抛不带 note 的 `RunSourcesError` / `OSError`（note 由 runlock 附上）。

- [ ] 2.1 状态守卫在锁漂移后失败（`_StatesGuardFailed` 由 runlock 附上漂移 note）：退出码 `1`；stderr 含带前缀的「错误：…」行，其后为带前缀的 note 行，note 恰出现一次；`"Traceback" not in err`
- [ ] 2.2 `run_sources` 抛出外层带 note 的 `RunSourcesError`：退出码 `3`；聚合文本恰出现一次，note 行带前缀、恰出现一次、在聚合文本之后；无 traceback
- [ ] 2.3 run 下抛出带 note 的 `OSError`：退出码 `3`；note 行带前缀、恰一次；无 traceback
- [ ] 2.4 prepare（或 init）下抛出带 note 的 `OSError`：仍 re-raise（`pytest.raises(OSError)`），stderr 恰为 `""`（不复用断言 `err` 非空的 `_assert_unstamped_output`），证明 prepare 路径不渲染该 note
- [ ] 2.5 红跑：还原 `cli.py` 后 2.1–2.3 失败、2.4 通过

## 3. 验证（输入 → 预期）

- [ ] 3.1 `cd producer && uv run ruff check . && uv run ruff format --check . && uv run pytest` → 全绿
- [ ] 3.2 `uv run python scripts/large_file_guard.py`（仓库根）→ passed
- [ ] 3.3 `openspec validate producer-run-outer-notes --strict --no-interactive` → valid
