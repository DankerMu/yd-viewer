Change surface: `_controller_sources.run_sources._worker` 在 `while True` 追赶循环之后加一步 retention；新私有函数负责持锁、plan、execute、rmdir 空 cycle 目录、拼审计文本和映射错误（放在 `_controller_sources.py` 内，或放进新模块 `_retention_run.py`，二选一，按行数决定）。`cli.py` 的 `RunSourcesError` handler（`cli.py:456-457`）改为先输出 `exc.reports`。`cleanup.py` 不改。

Must preserve:
- `cleanup.plan_retention` / `execute_retention_plan` 的语义与错误相位不变：先全量预检再删、只删本源 lane、symlink/越界拒删。`producer/tests/test_cleanup_retention.py`、`test_cleanup_retention_anchor.py` 原样通过。
- 追赶循环本身不变：每个 cycle 的 `run_one`、startup hygiene、startup audit（`startup cleanup:` 前缀附在**第一条**报告）、遇到非 SUCCEEDED 就结束，这些都不变。retention 只在循环结束之后执行一次。
- worker 以 `RunError` 结束时（preflight、startup hygiene 抛错、循环内 `run_one` 抛错）不做 retention，`_SourceWorkerResult(reports, error)` 与原来完全一致。
- 已发布的 DONE、状态链、work 删除不受 retention 成败影响；retention 失败不改已有报告的 outcome。
- 兄弟源独立：一个源的 retention 失败只给该源产生 error，另一源的报告和发布不变。`RunSourcesError` 的聚合文本（`str(exc)`、notes）不变。
- 退出码规则不变（全部 SUCCEEDED 为 0；STOPPED/错误为 3；`RunSourcesError` 走既有路径）。cron.log 的标签规则不变，审计文本跟在 detail 后面，多行规则沿用 compute-loop §6（标签只在第一行，后续行只加时间前缀）。
- 发布锁仍是同一把 `threading.Lock`，`run_once` 的发布段照旧使用它。
- `cleanup.py` ≤ 1000 行（现 997，不加行）；`_controller_sources.py` 及任何新模块 ≤ 1000 行。

Must add/change:
- 时机：worker 每次以报告正常返回之前执行一次 retention，即循环结束（`report.outcome is not SUCCEEDED`）和 startup hygiene 停源（`stop is not None`，`_controller_sources.py:624-627`）两条路径。worker 以 `RunError` 返回（preflight、hygiene 抛错、循环内抛错）时不执行。startup 停源时照常执行，因为它只说明 scratch work 有残留，`output/` 与 `logs/` 不受影响（compute-loop §12）。
- 锚点：只用 `plan_retention(local.yd_root, source)`，它内部按本源普通文件 DONE 取最新值；不得把另一源的 DONE 或墙钟传进去。
- 锁：从 `plan_retention` 到最后一个 `rmdir` 全程持有 `publish_lock`。
- rmdir 空 cycle 目录：对 `plan.output_dirs` 中每个目录的父目录 `output/<T>`，用 `safe_fs.open_directory_no_follow(output_root, containment_root=yd_root)` 取得 fd，再 `os.rmdir(name, dir_fd=fd)`。`ENOTEMPTY`/`EEXIST`/`ENOENT` 静默跳过（兄弟源还占用，或已经不存在）。其它 OSError/SafeFilesystemError 按 retention 失败处理。只有 rmdir 成功才计入审计。`output/<T>` 是 symlink 时 rmdir 得到 ENOTDIR，按失败处理，不跟随。
- 审计文本：`retention cleanup: 删除 <n> 个 output 目录、<m> 个失败日志、<k> 个空 cycle 目录：<abs path>, <abs path>, ...`，追加在该源本 tick 最后一条报告 detail 的末尾，用换行分隔（`dataclasses.replace`）。n、m、k 全为 0 时不追加任何文本。
- 失败：`CleanupError` 或 rmdir 错误转为 `_cleanup_run_error(f"{source}: retention 失败：{orig}", source=source, cycle=None, orig=orig)`。worker 返回 `_SourceWorkerResult(reports=tuple(reports), error=that)`。已经删掉的条目不回滚，也不推断已删清单（`execute_retention_plan` 不返回进度；下一 tick 重新 plan，剩余部分照常删除并审计）。
- `RunSourcesError` 构造校验（`_controller_sources.py:424-428` `_require_source_reports(..., allow_empty=True)`）放宽为：`errors` 中源的 reports 可为空；非空时非末项必须是 SUCCEEDED，末项可为任意结果。原因：retention 在追赶循环之后执行，此时末项必然是首个非 SUCCEEDED。不在 `errors` 中的源校验不变。`test_controller_sources.py:905-909`（`match="partial"`）必须改为：非末项为 STOPPED 时仍以 ValueError 拒绝，末项为 STOPPED 时接受。
- cli：`RunSourcesError` handler 先对 `exc.reports` 按 ifs、gfs 顺序调用既有的结果行渲染（标签、多行时间前缀规则相同），再 `_run_fail(str(exc), *_notes(exc))`。没有报告的源不写。退出码不变（3）。
- 每 tick 都执行（包括 raw 缺口等待的 tick），所以幂等，中断后下一 tick 继续完成。首次上线的首个 tick 一次性删完。

Governing invariant: 对任一 source S，retention 只删除 `cycle < latest_DONE(S) − 14d` 的 `output/<T>/S` 与 `logs/S/<T>.log`，以及删除后已空的 `output/<T>`；任何时刻都不会与兄弟源的发布段并发执行；retention 的成败不改变本 tick 已有报告的 outcome，也不影响兄弟源。

Sibling surfaces:
- `cli.py` 的 run 结果渲染：多行 detail 的时间前缀规则已有测试（`test_cli_run_log.py`），审计文本走同一路径，补一个用例。`RunSourcesError` 输出变化影响所有聚合错误路径：`test_cli_run_log.py::test_run_sources_error_is_printed_once_with_every_line_stamped`（~L330，逐字钉住输出）必须改为先有报告行再有错误块；该用例的 reports 为空时输出逐字不变。
- `residue` 在发布锁外删除本源的 NFS 半成品 `output/<T>/<source>`（`_controller_run.py` ~L540）。它不删 `output/<T>` 本身；兄弟源 rmdir 遇到它留下的内容得到 ENOTEMPTY，静默跳过。本源半成品晚于本源最新 DONE，永远在窗口内。
- `residue` / startup hygiene：读取 `output/<T>/<source>/DONE`。rmdir 只删空目录，不影响它们。
- viewer catalog：只枚举有 DONE 的 source 目录、最新往前 7 天；被删的目录都在 14 天外。
- `_failure_attempts`（#370）：被停 cycle T 晚于本源最新 DONE，永远在窗口内，不会被 retention 删掉而被静默放行（#375 review 已核实）。
- 直接六参数 `run_once` / `catch_up_source`：不接 retention（它们没有双源发布锁的语境）；生产 cron 只走 `run_sources`。

Seams under test:
- `run_sources`，用 `producer/tests/controller_sources_fixtures.py` 现有的 fake executor / driver 夹具，在临时 yd_root 上预置 output/logs/DONE；
- `cli` run 的 stderr 行（`test_cli_run_log.py` 的夹具）。

Required evidence:
- 两源锚点不同：gfs 最新 DONE 为 D，ifs 最新 DONE 为 D−17d。预置两源各自窗口内外的 output 与 logs。tick 结束后，gfs 只删 `< D−14d` 的 gfs 条目；ifs 只删 `< D−31d` 的 ifs 条目；ifs 在 `[D−31d, D−14d)` 的条目保留。
- 窗口边界：恰等于 cutoff 的 cycle 保留，早 12 h 的删除（沿用 plan 语义，端到端断言一次）。
- 空 cycle 目录：两源都在窗口外的 T，tick 后 `output/<T>` 不存在；只有一源在窗口外的 T，`output/<T>` 保留另一源的目录。
- 报告：有删除时最后一条报告 detail 以原文开头，末尾为 `retention cleanup: 删除 n …` 加逐条绝对路径；无删除时 detail 与改动前逐字相同。
- 每 tick 执行：raw 缺口等待的 tick（结果为 STOPPED/raw_incomplete）同样执行删除；紧接着再跑一个 tick，不再有删除，也不加审计文本。
- 锁：用一把会记录持有状态的锁替身（或在 `plan_retention` 上 monkeypatch 断言 `publish_lock.locked()`），证明 plan、execute、rmdir 都在锁内。
- 失败映射：`execute_retention_plan` 抛 `CleanupError` 时 `run_sources` 抛 `RunSourcesError`，该源 error 的 phase 为 "cleanup"、正文含 "retention"、`__cause__` 为原异常；本源已有的 SUCCEEDED 报告与 DONE 保留；兄弟源报告不变。
- 跳过：preflight 抛 `RunError`、或循环内 `run_one` 抛 `RunError` 时，不调用 `plan_retention`（monkeypatch 计数为 0）。startup hygiene 停源（预置无 DONE 的 work 残留）时照常调用，并执行删除。
- cli：有审计文本时，cron.log 第一行带标签，后续行只带时间前缀；退出码不变。
- cli `RunSourcesError`：ifs 报告为 (SUCCEEDED, STOPPED/raw_incomplete) 且 retention 失败；gfs 报告为 (STOPPED/raw_incomplete，detail 末尾带 `retention cleanup:` 审计)。stderr 依次为带前缀的「完成：ifs: …」「等待：ifs: …」「等待：gfs: …」、只带时间前缀的审计行，最后是「错误：」聚合块；退出码 3。
- RunSourcesError 构造：errors 源 reports 为 (SUCCEEDED, STOPPED) 接受；为 (STOPPED, SUCCEEDED) 或 (STOPPED, STOPPED) 拒绝。
- 红跑：还原 src 后，上述删除、报告、锁、失败映射的用例失败。

Non-goals: viewer 清理、窗口值配置化、对 `run_once`/`catch_up_source` 接线、清理 `states/`（另有规则）、清理 scratch work（startup hygiene 负责）、迁移脚本、修改 cleanup.py。

Review focus:
1. 锚点只取本源，两源落后程度不同时不会互删；
2. 锁覆盖 plan→execute→rmdir 全段，不会与兄弟源发布并发；
3. rmdir 只删空目录、走 dir_fd、ENOTEMPTY/ENOENT 静默；
4. 失败不改已有报告的 outcome，兄弟源独立；
5. RunError 路径跳过 retention，startup 停源照常执行；cli 在聚合错误前输出已有报告；
6. 行数上限。
