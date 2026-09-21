## 1. Whole-second submitted_at
- [x] 1.1 `producer/src/yd_producer/slurm.py` `SlurmJobExecutor.submit`: stamp `submitted_at=self._clock().replace(microsecond=0)`; one docstring/comment sentence: sacct Submit/Start/End are second-precision (compute-loop §10, 2026-09-21 run attempt 2), so the stamp is floored to the second; no tolerance elsewhere.
- [x] 1.2 Tests in `producer/tests/test_slurm_executor.py` (helpers from `slurm_fixtures.py`: `make_executor(..., clock=)`, `StepClock`, `T0`, `make_spec`, `build_sacct_command`):
  - sub-second clock: `make_executor([...], clock=StepClock(start=T0 + timedelta(microseconds=400_000), step=STEP))`, submit, then sacct `12345|RUNNING|<T0 formatted %Y-%m-%dT%H:%M:%S>|Unknown` → `poll` returns RUNNING, `record.submitted_at == T0`, `record.submitted_at.microsecond == 0`, `record.submitted_at.tzinfo is UTC`, `record.started_at == T0`.
  - red guard kept: sacct start at `T0 - 1 s` with the same sub-second clock still raises `ExecutorError` 且消息为 `` `started_at` 不得早于 `submitted_at` ``（含反引号，见 `executor.py:158`） (invariant untouched).
  - existing `test_submit_builds_argv_and_stamps_with_injected_clock` and the #308 grace tests unchanged and green (whole-second `StepClock` unaffected).
- [x] 1.3 `cd producer && uv run pytest tests/test_slurm.py tests/test_slurm_executor.py -q`; full `uv run pytest -q`; `uv run ruff check . && uv run ruff format --check .`; `openspec validate submit-timestamp-whole-second --strict --no-interactive`; red→green recorded for the sub-second case.
Suggested fixture level: compact
Minimal mergeable slice: atomic - one expression plus two tests.

## Risk evidence
- Error handling: the invariant still rejects a start genuinely earlier than the (floored) submission (1.2 red guard); no tolerance, no rewriting of sacct times.
- Legacy compatibility: whole-second clocks produce byte-identical records; existing executor and grace tests untouched.
- Non-goals: JobRecord/parse/poll/controller unchanged; clock skew between login node and slurmctld not handled (docs §10).
