Change surface: `yd_producer.cli` — `run()` exit rendering (`_run_exit`, `_runtime_fail`), and the `run`-command branches of `main()` that print to stderr (`ConfigError` → 2 at BOTH handlers: load-time `cli.py:391-395` without notes and delegation-time `:408-412` with `_print_notes`; `_StatesGuardFailed` → 1 via `_fail`, `RunLockError`, `RunSourcesError`, `RunError`/`ExecutorError` + `source=`/`phase=`/`job=` + notes, `OSError`, generic `Exception`).

Must preserve:
- Exit codes exactly as `cli-config` 「`yd-producer run` 必须接通生产控制器」: 0 (all SUCCEEDED or lock skip), 3 (any STOPPED/JOB_FAILED/CLEANUP_PENDING/runtime), 2 (config), 1 (states guard).
- `RunSourcesError` text printed once, IFS then GFS, each error and each note exactly once, no traceback (existing cli-config scenario); the prefix is added per physical line of that single block, content and occurrence count unchanged.
- Lock-contention skip prints nothing.
- `prepare`/`init`/`_fail` for non-run commands, the `DATABASE_URL` guard (runs before argument parsing) and argparse usage errors: byte-identical output.
- stdout unchanged (run writes nothing to stdout today).

Must add/change:
- `_stamp()` → `datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")`; a small run-only helper writes each physical line of a message to stderr as `f"{stamp} {line}"` (same stamp for all lines of one message). The shared helpers `_fail` and `_print_notes` stay unchanged because non-run commands call them too; run call sites use run-specific prefixed variants instead (e.g. a prefixed notes printer).
- Multi-line detail (e.g. `_attach_startup_audit` in `_controller_sources.py:45-50` prepends `startup cleanup: …\n` to the first report): the label goes on the first physical line only; every following line gets only the timestamp; the text appears exactly once.
- One source can yield several reports per tick (worker loops until a non-SUCCEEDED, `_controller_sources.py:605-627`; production shape `ifs=(SUCCEEDED, STOPPED raw_incomplete)`): one labelled entry per report, all ifs reports then all gfs reports.
- `_run_exit`: iterate `(*report.ifs, *report.gfs)`; label per report: `SUCCEEDED` → 完成, `STOPPED` and `stop_reason is StopReason.RAW_INCOMPLETE` → 等待, else 错误; text `f"{label}：{report.detail or report.outcome.value}"`; exit 0 iff all SUCCEEDED else 3.

Governing invariant: a line labelled 完成 or 等待 never corresponds to a failure outcome, and the exit code is a function of outcomes only, independent of labels.

Sibling surfaces: `RunReport` construction in `_controller_run.py` (detail strings; unchanged), `RunOutcome`/`StopReason` in `controller.py` (read only); cron line in agent-ops §8.2 (unchanged; appends stderr to `cron.log`); `prepare`/`init` printers (must stay unprefixed); tests in `producer/tests/test_cli.py`, `test_cli_states.py`, `cli_fixtures.py` that assert `错误：` substrings.

Seams under test: `cli.main(argv, env)` with the existing fakes/fixtures (capsys), plus `_run_exit` on constructed `RunSourcesReport`s.

Required evidence:
- ifs SUCCEEDED + gfs STOPPED(RAW_INCOMPLETE) → exit 3; stderr exactly two lines matching `^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ 完成：ifs: …` and `^… 等待：gfs: …`.
- both SUCCEEDED → exit 0; two `完成：` lines.
- STOPPED with another stop reason (e.g. STATE_MISSING) and JOB_FAILED and SUCCEEDED_CLEANUP_PENDING → `错误：` label, exit 3.
- report with empty detail → `<label>：<outcome value>`.
- RunSourcesError with notes → every stderr line prefixed; each note exactly once; exit 3; no traceback.
- config error on `run` → exit 2, every line prefixed, still contains `错误：`; states guard → exit 1, prefixed.
- lock contention → exit 0, empty stderr.
- `prepare`/`init` failure and `DATABASE_URL` guard → stderr has no timestamp prefix (byte-identical to before).

- Also: tasks.md 2.5 (delegation-time ConfigError with note), 2.5a (runtime paths), 2.5b (multi-line detail), 2.5c (multi-report), 2.6 (prepare notes unprefixed).

Non-goals: exit-code changes, a lock-skip line, log rotation, stdout output, node-22 deployment.

Review focus: exit codes untouched on every path; no failure outcome can be labelled 完成/等待; notes/audit text neither dropped nor duplicated after line-splitting; non-run commands unprefixed; tests assert whole-line shape, not only substrings.
