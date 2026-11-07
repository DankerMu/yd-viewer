Change surface: `cleanup._commit_failure_log`（覆盖写改为追加写）、`controller.StopReason`（+1 项）、`_controller_run` 中 residue 之后、`rawscan.judge` 之前的单源前沿流程、新模块 `producer/src/yd_producer/_failure_attempts.py`（计数读取与追加前缀读取）。`cli.py` 不改：其停因到标签的映射已规定「非 raw_incomplete 的 STOPPED 标为错误」。

Must preserve:
- 失败日志的 header 与 stdout/stderr 布局逐字节不变（`_failure_log_header` 与 `_STDOUT_STDERR_SEPARATOR`）；新记录接在旧内容之后。目标为 symlink 或非普通文件时照旧拒绝（`_reject_non_regular_log_target`）。临时文件 O_EXCL 加原子 rename，失败时清理临时文件，这些语义不变。
- 日志提交成功之后才删除 work（compute-loop §11.3），这一顺序不变。
- merged log 照旧分块流式复制，不整读：`test_cleanup_failure.py::test_large_merged_log_is_copied_byte_exact_without_linear_peak` 保持通过。
- 计数 < 3 时控制流与现状完全一致：raw 扫描、claim、提交、失败收尾、下一 tick 重试都照旧。
- 兄弟源独立。双源 ThreadPool 组合与 `RunSourcesError` 聚合不变。
- 退出码不变：STOPPED 为 3。`cli.py` 的标签规则不变：`retry_exhausted` 落在「错误」。
- 14 天 retention 只匹配 `logs/<source>/<T>.log`，行为不变。
- `cleanup.py`、`controller.py`、`_controller_run.py` 保持 ≤ 1000 行（现为 993、998、999）。没有余量时，把相邻私有代码机械搬到新模块，不改行为，并在报告里登记。

Must add/change:
- 常量 `FAILURE_ATTEMPT_LIMIT = 3`。
- 既有测试 `test_cleanup_failure.py::test_repeat_failure_on_the_same_cycle_atomically_replaces_the_old_log` 断言覆盖写语义，必须改写为追加语义（原子性断言保留）。
- 计数：header 行的判定标准为：单独一行、`json.loads` 得到 dict、且 `schema == "yd-failure-log-v1"`（与 compute-loop §11.3 一致，不加其它条件）。按字节 `b"\n"` 切行，只对候选行做 UTF-8 解码与 `json.loads`；解码或解析失败的行视为「非 header」，不令整个文件不可判定（日志正文是 SHUD 原始 stdout，可含非 UTF-8 字节）。stdout/stderr 里偶然出现的合格 JSON 行会被误计，接受这一点（只会提前停源，fail-closed）。schema 常量由 `_failure_attempts` 定义、`cleanup` 引用，不形成循环 import。读取走 no-follow（`stat_no_follow`/`open_file_no_follow`，containment_root = yd_root），有字节上界（建议 64 MiB，常量）。
- 判定：文件缺席（ENOENT）为 0。普通文件按 header 行计数。symlink、非普通文件、超界或其它 OSError（读失败），一律 STOPPED/RETRY_EXHAUSTED，detail 写明「失败日志不可判定：<原因>」，不 fail-open。
- STOPPED detail：`<source>: 待跑 T=<T>；同一 cycle 已失败 <n> 次（上限 3），停源待人工；确认根因后把 <abs log path> 移出 logs/<source>/ 再等下一 tick`。
- 追加写：读旧内容复用同一个有界 no-follow reader；旧内容超界时提交失败（CleanupError phase="log"），保留 work。这与现有「日志提交失败保留 work」的语义一致。旧内容非空且不以 `\n` 结尾时，先补一个 `\n` 再写本次 header（旧内容仍是新文件的逐字节前缀），保证 header 单独成行。
- 计数检查放在 residue 处理之后、`failure_exit_code` 未知 work 分类之后、`rawscan.judge` 之前。这样即使 raw 齐全，也不会产生 staging 或提交。优先级：`work/<source>/<T>` 已存在时 `classify_unverified_work`（`_controller_run.py:564-571`）先返回 `UNVERIFIED_WORK_RESIDUE`，先于 `retry_exhausted`。

Governing invariant: 同一 `<source>/<T>` 的失败日志里有 ≥ 3 条有效 header 时，该源本 tick 零 staging、零 sbatch。其它情况下控制流与改动前一致。

Sibling surfaces:
- `cleanup` 的 retention plan 读取 `logs/`：只匹配 `.log` 后缀加 cycle 解析，不受影响，验证即可。
- `cli` 的 stderr 标签：沿用现有规则，加用例。
- `test_stop_reason_vocabulary_is_closed`：词表 7 → 8，同步修改。
- `test_cli_run_log.py:255` 对 `list(StopReason)` 做参数化：新成员自动覆盖。
- startup hygiene 与 residue：不读 `logs/`，不受影响。
- 失败收尾 provider、ExitCode sacct：不变。

Seams under test:
- `_failure_attempts` 纯读取函数（临时目录）；
- `cleanup.finalize_failed_job`（真实 FailureInputs，沿用现有测试夹具）；
- `run_sources` 或单源 controller 入口，配 fake executor（沿用 `test_controller_*` 现有夹具），用于计数门与兄弟源独立的用例；
- `cli` run 的 stderr 行。

Required evidence:
- 计数：日志缺席、1 条、2 条 → 正常提交 sbatch；3 条 → STOPPED/RETRY_EXHAUSTED，executor submit 调用 0 次，work 目录不存在。
- 旧格式（现行单记录文件）计 1。
- 正文含非 UTF-8 字节（如 `cleanup_fixtures.FAILED_LOG_BYTES`）的单记录日志计 1，控制器正常提交。
- 旧日志正文不以 `\n` 结尾（如 `cleanup_fixtures.TIMEOUT_LOG_BYTES`）时再失败一次 → 旧文件仍是逐字节前缀，计数 2。
- 追加：先有 1 条，再失败一次 → 文件前缀与旧内容逐字节相同，header 计 2。
- symlink 日志 / 目录 / 超界 → STOPPED/RETRY_EXHAUSTED 且 detail 含「不可判定」，零 submit。
- 双源：ifs 达上限、gfs 正常 → gfs 照常提交并发布，ifs 停。
- cron.log：`retry_exhausted` 一行标为「错误：」并带时间前缀，退出码 3。
- 红跑：还原 src 后，3 条 → 停源的用例与追加保真用例失败。

Non-goals: 自动放行、按时间衰减、阈值入 config、退避、status.json、改 retention、给现有失败日志补迁移。

Review focus:
1. 计数读取的 no-follow 与上界，不可判定时 fail-closed；
2. 追加写的原子性与旧内容保真；超界时提交失败、保留 work；
3. 门的位置：零 staging、零 submit；
4. 兄弟源独立；
5. 三个文件的行数上限与搬运是否等价。
