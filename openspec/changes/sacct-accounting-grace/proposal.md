## Why
M4 stage 5 first on-site `run` (2026-09-21, receipt `m4-stage5-run1-20260921.md`, #202): `sbatch` returned job 52756 and the controller's immediate first `sacct -j 52756 -X …` printed 0 rows because Slurm accounting lags submission by a few seconds. `parse_sacct_record` fails closed on `!= 1` rows, so the source stopped with `RunError(phase="poll")` and the work was preserved while the job kept running on the compute node. Docs are already merged first (`docs/compute-loop-design.md` §10 last paragraph, `docs/agent-ops.md` §8.3): a 0-row `sacct` within 120 s of `submitted_at` is accounting lag and the record stays PENDING.

## What Changes
- `SlurmJobExecutor.poll`: when the sacct stdout has no non-blank line and `clock() - record.submitted_at <= SACCT_ACCOUNTING_GRACE_SECONDS` (module constant `120`), return the existing record unchanged (state PENDING, `started_at`/`ended_at` untouched, `_records` not replaced) instead of calling `parse_sacct_record`. Past the window, or with any multi-row output at any time, the existing `parse_sacct_record` exception path is unchanged.
- `parse_sacct_record` wording and behavior for 0 rows unchanged (other callers unaffected).
- Tests in `producer/tests/test_slurm.py` (`make_executor` gains an optional `clock=`): two empty polls then RUNNING; empty at exactly `submitted_at + 120 s` still grace; empty at `submitted_at + 121 s` still raises; 2 rows still raise; `parse_sacct_record` 0-row wording and `EXPECTED_SBATCH`/sacct argv unchanged.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `run-controller`: 作业提交经执行器抽象且身份可追溯 — bounded accounting grace for 0-row `sacct`.

## Impact
`producer/src/yd_producer/slurm.py` (`poll` only), `producer/tests/test_slurm.py`. No config/local keys, no controller change, no argv change.

Issue type: bugfix
Fixture level: compact
Upstream suggested level: compact (agree)
Blast radius: too-wide grace hides a lost job as PENDING forever; too-narrow grace reproduces the M4 stop. Bounded by `submitted_at` + 120 s and 0-row only, so the worst case is a 120 s later fail-closed.
Selected risk packs: Error handling / rollback / partial outputs (grace must not weaken the multi-row / post-window refusal); Concurrency / shared state / ordering (`_records` must not be replaced on an empty poll). Others not selected: no API/CLI/config/schema/file IO/auth/dependency change.
Evidence floor: `cd producer && uv run pytest tests/test_slurm.py -q` green with the five new cases in tasks.md 1.3, each behavior case red against pre-change source; `uv run ruff check . && uv run ruff format --check .`; `openspec validate sacct-accounting-grace --strict --no-interactive`.
design.md omitted (compact fixture).
