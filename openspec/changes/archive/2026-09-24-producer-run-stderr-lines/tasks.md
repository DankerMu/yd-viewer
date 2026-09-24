## 1. Implementation (`producer/src/yd_producer/cli.py`)
- [x] 1.1 Add `_stamp()` and a stderr writer that prefixes every line of a message with one timestamp (design.md).
- [x] 1.2 `_run_exit`: one labelled line per report (完成/等待/错误), written also on all-success; exit codes unchanged.
- [x] 1.3 Route every `run`-command stderr print in `run()`/`main()` through the prefixed writer (ConfigError→2 at both the load-time and delegation-time handlers, states guard→1, RunLockError, RunSourcesError, RunError/ExecutorError with `source=`/`phase=`/`job=`/notes, OSError, generic Exception). `prepare`/`init`, the DATABASE_URL guard and argparse output stay byte-identical; `_fail` and `_print_notes` themselves are not modified.

## 2. Tests (`producer/tests/test_cli*.py`)
- [x] 2.1 Mixed ifs 完成 + gfs 等待 → exit 3, exactly two full-line regex matches.
- [x] 2.2 All succeed → exit 0, two 完成 lines.
- [x] 2.3 Other STOPPED reason, JOB_FAILED, SUCCEEDED_CLEANUP_PENDING → 错误, exit 3; empty detail → outcome value.
- [x] 2.4 RunSourcesError with notes → every line prefixed, each note exactly once, exit 3, no traceback (update the existing scenario test, do not weaken it).
- [x] 2.5 run ConfigError at load time → 2 and at delegation time with a note → 2 (note line prefixed too); states guard → 1, every line prefixed; lock contention → 0 with empty stderr.
- [x] 2.5a Parametrized runtime paths — RunError and ExecutorError carrying `source`/`phase`/`job` and a note, OSError, generic Exception, RunLockError: every stderr line matches `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z `, exit 3.
- [x] 2.5b Multi-line detail: first ifs report detail `startup cleanup: removed X\nifs: 一轮成功发布完成（…）` → first line `<ts> 完成：startup cleanup: …`, second line `<ts> ifs: …` (no label), audit text exactly once.
- [x] 2.5c Multi-report source: ifs=(SUCCEEDED, STOPPED raw_incomplete), gfs=(STOPPED raw_incomplete) → three labelled lines in order 完成(ifs), 等待(ifs), 等待(gfs); exit 3.
- [x] 2.6 prepare/init failure, a prepare failure with `__notes__` (extend the seam of `test_cleanup_note_reaches_stderr_on_the_exit_one_path`: the note line has no prefix) and the DATABASE_URL guard → no timestamp prefix.
- [x] 2.7 Existing tests asserting `错误：` for run keep passing or are updated only where the line now starts with a timestamp or is legitimately 等待/完成; list each updated assertion in the report.
- [x] 2.8 Record red→green for 2.1, 2.2, 2.3, 2.5a, 2.5b, 2.5c.

## 3. Gates
- [x] 3.1 `cd producer && uv run ruff check . && uv run ruff format --check . && uv run pytest`.
- [x] 3.2 `uv run python scripts/large_file_guard.py` via `cd producer && uv run python ../scripts/large_file_guard.py` (cli.py and test files stay ≤1000 lines; split a test file rather than adding an exclusion).
- [x] 3.3 `openspec validate producer-run-stderr-lines --strict --no-interactive`.

Suggested fixture level: expanded
Minimal mergeable slice: atomic - one CLI rendering function and its callers; splitting would leave run output half-prefixed.

## Risk evidence
- Public API / CLI / script entry: 2.1–2.6 incl. 2.5a–2.5c through `cli.main`.
- Error handling / partial outputs: 2.4 notes exactly once; 2.3 failure outcomes never 完成/等待.
- Legacy compatibility: exit codes asserted in 2.1–2.5; 2.6 non-run outputs byte-identical; lock skip silent (2.5).
- Not selected: Config/setup (no config keys), File IO/path safety (no files), Schema/units (none), Auth/secrets (DATABASE_URL guard unchanged, 2.6), Concurrency (lock untouched), Resource limits (none), Release/packaging (no deps), Docs (compute-loop §6 merged in #346 and #348).
