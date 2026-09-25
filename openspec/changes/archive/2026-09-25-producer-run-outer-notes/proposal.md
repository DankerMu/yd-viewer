## Why

运行中 `cron.lock_path` 的 identity 漂移时，`runlock` 把漂移 note 用 `add_note` 附在 `action()` 抛出的原异常上并原样 re-raise（`runlock.py:183-192`）。`cli.py` 的 run 路径里，`_StatesGuardFailed`（`:228-229`）、`RunSourcesError`（`:455-456`）、`OSError`（`:459-462`）三个 handler 只打 `str(exc)`，不渲染 `__notes__`，"互斥在运行中失效"这条唯一证据就不会进 `cron.log`。compute-loop §6「`run` stderr 行格式」与 cli-config spec 已规定失败路径的每条 note 都要加时间前缀输出，这是代码不符合文档，不需要先改 docs。Closes #350。

**硬约束：简化项目，不过度设计。**

```text
Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent (expanded: CLI 入口的错误处理与退出码，属 expanded 触发项)
Blast radius: 改错 → 退出码漂移（守卫 1 / 运行期 3）破坏 cron 调度判定，或 prepare/init 下 OSError 不再 re-raise，或 note 丢失/重复
Selected risk packs: Public API / CLI / script entry; Error handling; Legacy compatibility
Evidence floor: producer ruff + format + pytest 全绿；三条路径带 note 的 CLI 测试（前缀、恰一次、退出码、无 traceback）；prepare/init 下 OSError 仍 re-raise
```

## What Changes

- `cli.py` run 路径三个 handler 改为 `_run_fail(str(exc), *_notes(exc), code=…)`，与 `RunError`/`ExecutorError`/兜底 `Exception` 的既有写法一致；退出码不变。
- 修正两处过时或误导的文字：`_print_notes` docstring 中「`prepare` 与 `run` 的 handler 都直接调用本函数」；`:454` 注释「prepare/init 不导入 controller/executor 的错误类型」改为按调用链表述。
- CLI 层补测试。

不做：`runlock.py` 附着与漂移判定；`RunSourcesError` 聚合文本（#137）；退出码；prepare/init 输出；traceback 策略；`_run_fail` 签名改动。

## Capabilities

### Modified Capabilities
- `cli-config`：run 的 stderr 行格式——明确外层异常对象上的 notes 在三条失败路径同样输出。

## Impact

- `producer/src/yd_producer/cli.py`；`producer/tests/test_cli_run_log.py`（或同级新文件，受 1000 行上限约束）。
- 设计要点见 design.md；risk pack 取舍见 tasks.md §0。
