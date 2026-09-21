## Why
M4 stage-5 run attempt 2 (2026-09-21 12:51Z, checkout `cc1ade8`, receipt `m4-stage5-rerun-20260921.md`, #202): `sbatch` succeeded for both sources (jobs 52782/52783) and the first `sacct` poll already returned `RUNNING` rows, but `JobRecord.__post_init__` raised `started_at 不得早于 submitted_at` and both sources stopped at `phase=poll`. `sacct` reports Submit and Start at second precision and both were `2026-09-21T12:51:53` (idle partition scheduled the job within the submission second), while the executor stamped `submitted_at` from `datetime.now(UTC)` with microseconds, so the truncated `started_at` sorted before it. Docs are merged first (PR #318): `docs/compute-loop-design.md` §10 — `submitted_at` is truncated to whole seconds when stamped; no tolerance; sacct times are never rewritten; login-node/slurmctld clock skew is out of scope.

## What Changes
- `SlurmJobExecutor.submit`: `submitted_at=self._clock().replace(microsecond=0)` (floor to the second, tzinfo preserved). Docstring sentence citing §10.
- Nothing else: `JobRecord` invariants, `parse_sacct_record`, `poll` (incl. the 120 s grace measured from the now whole-second `submitted_at`), controller, argv all unchanged.
- Tests in `producer/tests/test_slurm_executor.py`: submit with a clock returning `T0 + 400 ms` then a `RUNNING` row at `T0` → poll succeeds, `submitted_at == T0` (microsecond 0, tz UTC), `started_at == T0`; existing whole-second clock tests unchanged; `EXPECTED_SBATCH` argv unchanged.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `run-controller`: 作业提交经执行器抽象且身份可追溯 — whole-second `submitted_at`.

## Impact
`producer/src/yd_producer/slurm.py` (one expression), `producer/tests/test_slurm_executor.py`. No config/local keys, no controller change.

Issue type: bugfix
Fixture level: compact
Upstream suggested level: compact (agree)
Blast radius: without the fix any job that starts within its submission second stops the source; with it, the only behavior change is that `submitted_at` loses sub-second precision (report/receipt timestamps and the 120 s grace anchor move by < 1 s earlier).
Selected risk packs: Error handling (the invariant must still reject a genuinely earlier start, e.g. `started_at = T0 - 1 s`); Legacy compatibility (existing tests with whole-second `StepClock` unchanged). Others not selected: no API/config/schema/file IO change.
Evidence floor: `cd producer && uv run pytest tests/test_slurm.py tests/test_slurm_executor.py -q` green with the new cases (the sub-second case red against pre-change source); full `uv run pytest -q`; `uv run ruff check . && uv run ruff format --check .`; `openspec validate submit-timestamp-whole-second --strict --no-interactive`.
design.md omitted (compact fixture).
