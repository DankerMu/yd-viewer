## Why
On node-22 `cron.log`, a normal raw-incomplete wait and an already-published result are both written as one `错误：…` line with no timestamp, while a fully successful tick writes nothing. Operators cannot tell waiting from failure or when a line was written. User ruling 2026-09-24; docs first: `docs/compute-loop-design.md` §6 「`run` stderr 行格式」 (PR #346). Issue #347.

## What Changes
- `producer/src/yd_producer/cli.py`, `run` subcommand only: every stderr line starts with the UTC time `YYYY-MM-DDTHH:MM:SSZ` plus one space.
- `_run_exit`: one line per `RunReport` (ifs reports, then gfs), `<time> <label>：<detail>`; label `完成` for `SUCCEEDED`, `等待` for `STOPPED` with `stop_reason == RAW_INCOMPLETE`, `错误` otherwise; missing detail → the outcome name; also written when all succeed.
- Other `run` failure paths (config, lock, states guard, `RunSourcesError`, runtime exceptions incl. `source=`/`phase=`/`job=`/notes lines) keep their text and gain the same prefix on every line.
- Unchanged: exit codes, lock-contention skip (no output), `prepare`/`init` output, the `DATABASE_URL` guard and argparse errors (both run before the command is known).

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `cli-config`: adds the `run` stderr line-format requirement.

## Impact
`producer/src/yd_producer/cli.py` and `producer/tests/test_cli*.py`. No controller, Slurm, file or exit-code change. node-22 takes effect only after an authorized checkout update.

Issue type: feature (operator log format)
Fixture level: expanded
Upstream suggested level: compact (override: the change touches the `run` CLI entrypoint output, an expanded trigger in issue-risk-contract.md)
Blast radius: every cron tick's log line; a mistake can hide real failures behind 「等待」/「完成」, change exit codes, or drop the RunSourcesError notes that carry cleanup audit evidence.
Selected risk packs: Public API / CLI / script entry; Error handling / rollback / partial outputs; Legacy compatibility.
Evidence floor: `cd producer && uv run ruff check . && uv run ruff format --check . && uv run pytest`; `openspec validate producer-run-stderr-lines --strict --no-interactive`.
