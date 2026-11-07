## 0. Risk packs

- File IO / path safety / overwrite：选中。rmdir 空 cycle 目录需 no-follow、dir_fd；对应 2.3、2.8。
- Error handling / partial outputs：选中。retention 失败映射为 cleanup RunError，已删条目不回滚，已有报告保留；对应 2.6。
- Concurrency / shared state：选中。持发布锁，两源 worker 并行，锚点分源；对应 2.1、2.5。
- Legacy compatibility：选中。无删除时报告逐字不变；cleanup seam 语义不变；RunError、startup 停源路径不变；对应 2.4、2.7、3.1。
- Public API / CLI：选中。cron.log 多行审计文本，退出码不变；对应 2.9。
- Documentation / migration notes：选中。compute-loop §12 已由 PR #377 先行；本 change 的 spec delta 修改「保留窗口与安全清理」；首次上线删除约 75 个 cycle，需授权并留 receipt；对应 3.3、3.4。
- Resource limits / large input：不选。每 tick 只枚举 output/ 与 logs/<source>/ 一层，首次上线约 100 个条目；plan 已有界。
- Schema / columns / units：不选。没有产物格式变化。
- Config、Auth、Release：不选。不新增配置键或依赖。

## 1. 实现

- [ ] 1.1 新增私有 retention 步骤（`_controller_sources.py` 内或新模块 `_retention_run.py`）：持 `publish_lock` 执行 plan → execute → rmdir 空 `output/<T>`；返回审计文本（无删除时为空），失败时抛 cleanup `RunError`
- [ ] 1.2 `run_sources._worker`：循环正常结束时调用 1.1，把审计文本追加到最后一条报告的 detail；失败时带着已有报告返回 error；RunError 与 startup 停源路径不调用
- [ ] 1.3 所有文件 ≤ 1000 行，`cleanup.py` 不改

## 2. 测试（场景驱动；新文件 `producer/tests/test_retention_run.py`，单文件 < 1000 行，复用 `test_controller_sources.py` 夹具）

- [ ] 2.1 两源锚点不同（ifs 比 gfs 落后 17 天）：各自只删自己窗口外的条目，ifs 在 `[D−31d, D−14d)` 的条目保留
- [ ] 2.2 窗口边界端到端：等于 cutoff 保留，早 12 h 删除；logs 同理
- [ ] 2.3 空 cycle 目录：两源都在窗口外 → `output/<T>` 被 rmdir；只有一源在窗口外 → 保留
- [ ] 2.4 报告：有删除时 detail = 原文 + `\n` + `retention cleanup: …` 逐条绝对路径；无删除时 detail 逐字不变
- [ ] 2.5 锁：plan、execute、rmdir 都在 `publish_lock` 内执行
- [ ] 2.6 失败映射：execute 抛 CleanupError → RunSourcesError，该源 error.phase == "cleanup"，正文含 "retention"，cause 是原异常；已有 SUCCEEDED 报告与 DONE 保留；兄弟源报告不变
- [ ] 2.7 跳过：循环以 RunError 结束、startup hygiene 停源 → plan_retention 调用 0 次
- [ ] 2.8 rmdir 遇到 symlink 的 `output/<T>` → 按失败处理，symlink 及其目标都不被删
- [ ] 2.9 每 tick 执行且幂等：raw 缺口的 tick 也删除；紧接的第二个 tick 无删除、无审计文本
- [ ] 2.10 cli：带审计文本的结果 → 第一行带「完成：」或「等待：」标签，后续行只带时间前缀，退出码不变
- [ ] 2.11 红跑：还原 src 后，2.1、2.3、2.4、2.5、2.6、2.9 失败

## 3. 验证

- [ ] 3.1 `cd producer && uv run ruff check . && uv run ruff format --check . && uv run pytest` → 全绿（含原样通过的 `test_cleanup_retention*.py`）
- [ ] 3.2 `uv run python scripts/large_file_guard.py` → passed
- [ ] 3.3 `openspec validate retention-wiring --strict --no-interactive` → valid
- [ ] 3.4 现场（orchestrator，需用户授权）：部署前列出两源窗口外清单；首个 tick 后核对实际删除与清单一致、窗口内条目完整；node-27 `/api/cycles`、health 正常；留 node-22 / node-27 receipt
