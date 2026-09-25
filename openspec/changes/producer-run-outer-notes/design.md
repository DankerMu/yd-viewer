## Context

`run_with_lock` 在退出时发现锁文件 identity 漂移：若 `action()` 成功，抛 `RunLockError(note)`（note 即消息，已被 `:230-231` 输出）；若 `action()` 失败，把 note `add_note` 到原异常再 re-raise。原异常可能是 `_StatesGuardFailed`、`RunSourcesError`、`OSError`、`RunError`/`ExecutorError`、`ConfigError` 或其它异常；后三类的 handler 已渲染 `_notes(exc)`，前三类没有。

## Governing invariant

- run 路径上被 `main`/`run` 接住的异常，其 `__notes__` 每条恰输出一次，每个物理行带同一时间前缀，位于「错误：<str(exc)>」之后；不打 traceback。
- 退出码不变：`_StatesGuardFailed` → `EXIT_GUARD`(1)；`RunSourcesError`、run 下 `OSError` → `EXIT_RUNTIME`(3)。
- prepare/init 下 `OSError` 仍 re-raise（输出与行为不变）。

## Sibling surfaces

- `RunLockError` handler（`:230-231`）：note 即消息，不改。
- `RunError`/`ExecutorError`、兜底 `Exception`、委托期 run 的 `ConfigError`（`cli.py:449-450`）：已渲染 notes，保持不变，作为对照。
- 装载期 run 的 `ConfigError`（`cli.py:432-434`，`_run_fail(str(exc), code=EXIT_USAGE)` 不带 notes）：不改。它在取锁之前抛出，不会被 runlock 附 note，且 `config.py` 无任何 `add_note`，该路径本无 note 可丢。
- `RunSourcesError` 的 `str(exc)`：已含内层各源错误及其 notes（#137）；外层 notes 追加在聚合文本之后，聚合文本仍只输出一次。
- `_print_notes`（prepare 路径）：行为不变，只改 docstring。

## Must preserve

- 退出码（守卫 1、运行期 3、装载期 ConfigError 2）；prepare/init 下 `OSError` re-raise；不带 note 时三条路径输出与现状逐字节一致（既有 `test_run_states_guard_is_stamped_and_exits_one` 与 `os-error` 参数用例）。
- cron 按退出码判定调度，`cron.log` 用于事后取证。

## Seams under test

- 真实 `run_with_lock` + 途中 unlink 锁文件（tasks §2 注入方式）；`cli._check_states_dir`、`cli.run_sources` 两个 monkeypatch 点。

## Decisions

- 用现有 `_notes(exc)` 追加到 `_run_fail` 的 `extra`，不改 `_run_fail` 签名（issue 备选方案 diff 更大且无收益）。
- 不在 runlock 把原异常包成 `RunLockError`：会改变异常身份与退出码。

## Non-goals

- `OSError` 以外路径上其它模块 `add_note` 的内容是否逃逸到 `main`（issue 中的推断）：本改动使 run 下 `OSError` 的所有 notes 都输出，已覆盖该推断的影响面，不另行排查。

## Review focus

- 三个 handler 的退出码与 re-raise 分支未变。
- 测试断言 note 行带前缀、恰出现一次、位于错误行之后，且 `"Traceback" not in err`；note 不是 `str(exc)` 的子串（否则断言无判别性；真实 drift note 与拒绝理由同在 tmp_path 下，允许有公共子串）。
