## 0. Risk packs

- File IO / path safety / overwrite — 选中：失败日志读写 no-follow、原子追加 → 1.2、1.3、2.4、2.5。
- Error handling / partial outputs — 选中：不可判定 fail-closed；追加失败保留 work → 2.5、2.6。
- Legacy compatibility — 选中：旧格式单记录计 1；计数 < 3 时行为不变；词表 7 → 8 → 2.2、2.3、2.9。
- Public API / CLI — 选中：新停因的 stderr 标签与退出码 → 2.8。
- Resource limits / large input — 选中：读取有字节上界 → 2.5。
- Documentation / migration notes — 选中：compute-loop 已由 PR #371 先行；本 change 的 spec delta 修改 run-controller「失败处理」→ 3.3。
- Concurrency / shared state — 选中：双源独立 → 2.7。
- Config、Auth、Release — 不选：不新增配置键或依赖。

## 1. 实现

- [ ] 1.1 新模块 `producer/src/yd_producer/_failure_attempts.py`：`FAILURE_ATTEMPT_LIMIT = 3`、字节上界常量；有界 no-follow reader；`count_failure_attempts(yd_root, source, cycle) -> int`，不可判定时抛出带原因的专用异常
- [ ] 1.2 `cleanup._commit_failure_log`：已有普通文件时，先把旧内容写入临时文件，再写本次 header 与 stdout/stderr；超界或不可读时报 `CleanupError(phase="log")`
- [ ] 1.3 `controller.StopReason` 增加 `RETRY_EXHAUSTED = "retry_exhausted"`，附注释
- [ ] 1.4 `_controller_run`：在 `rawscan.judge` 之前调用计数；≥ 3 或不可判定时返回 STOPPED 决策（detail 形状见 design）
- [ ] 1.5 三个文件保持 ≤ 1000 行；如需搬运，只做机械搬运并在报告中登记

## 2. 测试（场景驱动；新文件 `producer/tests/test_failure_attempts.py` 及必要的既有文件修改，单文件 < 1000 行）

- [ ] 2.1 计数函数：缺席 → 0；1、2、3 条 → 1、2、3；stdout 中含非 header 的 JSON 行时不误计
- [ ] 2.2 旧格式（现行 `_failure_log_header` 加 stdout 的单记录文件）→ 1
- [ ] 2.3 控制器：0、1、2 条 → 正常提交（fake executor submit 1 次）；3 条 → STOPPED/RETRY_EXHAUSTED，submit 0 次，无 work 目录
- [ ] 2.4 追加保真：已有 1 条时再走一次 `finalize_failed_job` → 新文件前缀与旧文件逐字节相同，计数 2
- [ ] 2.5 不可判定：symlink、目录、超界（用 monkeypatch 调小上界常量）→ STOPPED/RETRY_EXHAUSTED，detail 含「不可判定」，submit 0 次；追加路径遇超界旧文件 → `CleanupError(phase="log")`，work 保留
- [ ] 2.6 追加后 retention plan 仍能识别并处理 `logs/<source>/<T>.log`（沿用现有 retention 测试即可）
- [ ] 2.7 双源：ifs 达上限、gfs 正常 → gfs 提交并 SUCCEEDED，ifs STOPPED
- [ ] 2.8 cli：`retry_exhausted` 结果对应的 stderr 行为 `<ts> 错误：…`，退出码 3
- [ ] 2.9 `test_stop_reason_vocabulary_is_closed` 更新为 8 项
- [ ] 2.10 红跑：还原 src 后 2.3（3 条那一例）、2.4、2.5 失败

## 3. 验证

- [ ] 3.1 `cd producer && uv run ruff check . && uv run ruff format --check . && uv run pytest` → 全绿
- [ ] 3.2 `uv run python scripts/large_file_guard.py` → passed
- [ ] 3.3 `openspec validate <change> --strict --no-interactive` → valid
- [ ] 3.4 现场（orchestrator）：部署后现有 `logs/ifs/2026102012.log`（旧格式，计 1）不阻塞追赶；cron.log 中不再出现同一 cycle 每小时一次的 FAILED
