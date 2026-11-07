## Why

ifs 2026102012 因确定性的 canonical 错误（#369），每个 cron tick 都重新提交、每次都 FAILED，截至 2026-11-07 04:18Z 已 28 次。现行规格（run-controller「失败处理」：「MUST NOT 维护失败计数、退避或 `status.json`」）让这类故障永远不会停下来等人处理。用户裁决（2026-11-07）：同一 `<source>/<T>` 累计 3 次失败后停源待人工。实测 ifs 2026101800 曾因 `SHUD exited 127` 连败 2 次、第 3 次成功，阈值 3 能让偶发故障自愈。docs 已先行修改（PR #371，compute-loop §2/§10 step 12/§11.3/§12）。Closes #370。

**硬约束：简化项目，不过度设计。**

```text
Issue type: feature
Fixture level: expanded
Upstream suggested level: absent (expanded: 失败日志写入格式与 run 控制流/停因词表，属 file IO、persisted state、CLI 触发项)
Blast radius: 改错 → 失败日志覆盖丢证据，或可恢复的源被误停，或停源判定 fail-open 继续无限重试，或兄弟源被连带停掉
Selected risk packs: File IO / path safety / overwrite; Error handling / partial outputs; Legacy compatibility; Public API / CLI; Resource limits; Documentation
Evidence floor: producer ruff + format + pytest 全绿；计数 0/1/2/3、旧格式、追加保真、symlink/超界、兄弟源独立、cron.log 行形状的测试红→绿；large_file_guard 通过
```

## What Changes

- `cleanup._commit_failure_log`：从覆盖写改为追加写。已有同名普通文件时，no-follow、有界地读出旧内容，原样写入临时文件，再接本次 header 与 stdout/stderr，最后原子 rename。
- 新增 `controller.StopReason.RETRY_EXHAUSTED = "retry_exhausted"`（词表 7 → 8）。
- `_controller_run`：确定待跑 T 之后、`rawscan.judge` 之前，有界读取 `logs/<source>/<T>.log`，统计 header 行条数；≥ 3 时返回 STOPPED/RETRY_EXHAUSTED，零 staging、零提交。
- 阈值是版本化模块常量 3，不进 config。

不做：status.json 或独立计数文件、退避、改 14 天 retention、改退出码、自动放行。

## Capabilities

### Modified Capabilities
- `run-controller`：失败处理——失败日志追加写，3 次失败后停源。

## Impact

- `producer/src/yd_producer/{cleanup.py,controller.py,_controller_run.py}`（各自保持 < 1000 行）；测试见 tasks.md。
