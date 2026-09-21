## 1. Bounded accounting grace in `SlurmJobExecutor.poll`
- [x] 1.1 Add module constant `SACCT_ACCOUNTING_GRACE_SECONDS = 120` in `producer/src/yd_producer/slurm.py` (not in `Config`/`LocalConfig`, not in `JobSpec.resources` or any argv).
- [x] 1.2 In `poll`, after `_run` and before `parse_sacct_record`: if stdout has no non-blank line and `self._clock() - record.submitted_at <= timedelta(seconds=SACCT_ACCOUNTING_GRACE_SECONDS)`, return `record` as is (no new `JobRecord`, `_records[job_id]` not replaced). Otherwise fall through to `parse_sacct_record` unchanged. `clock` is consulted once per empty poll; terminal-idempotent and unknown-job paths unchanged.
- [x] 1.3 Tests in `producer/tests/test_slurm.py`. Add a keyword-only `clock: Callable[[], datetime] | None = None` parameter to the existing `make_executor` helper (default keeps `StepClock(start=T0, step=STEP)`, so all existing callers are unchanged); the default `STEP = 10 s` clock cannot reach `+121 s`, so the two timing cases below pass their own `StepClock`. Five cases:
  - two empty stdout polls then `12345|RUNNING|<start>|Unknown`: first two polls return the submitted record (`state is PENDING`, `started_at is None`, same object or equal record) and `_records["12345"]` is unchanged; third poll is RUNNING with the parsed start.
  - `make_executor([...], clock=StepClock(start=T0, step=timedelta(seconds=121)))`: submit stamps `T0`, the first empty poll reads `T0 + 121 s`: raises `ExecutorError` whose message contains `期望恰好 1 行记录，实际 0 行`, bound to `12345`; `_records["12345"]` still the submitted record.
  - boundary: `make_executor([...], clock=StepClock(start=T0, step=timedelta(seconds=120)))`, empty poll at exactly `submitted_at + 120 s` is still grace (`<=`): returns the PENDING record, no exception.
  - two-row stdout inside the window still raises the existing `实际 2 行` exception.
  - `parse_sacct_record("", "12345")` still raises with the same wording; `EXPECTED_SBATCH` / `build_sacct_command` argv unchanged (existing pinned tests stay green).
- [x] 1.4 `cd producer && uv run pytest tests/test_slurm.py -q`, then full `uv run pytest -q`; `uv run ruff check . && uv run ruff format --check .`; `openspec validate sacct-accounting-grace --strict --no-interactive`.
Suggested fixture level: compact
Minimal mergeable slice: atomic - one branch in `poll` plus its tests.

## Risk evidence
- Error handling: post-window 0 rows and multi-row output at any time keep the existing `parse_sacct_record` exceptions verbatim; the grace never fabricates a state, start or end. Unknown state strings and blank fields are still refused by `parse_sacct_record` (untouched).
- Concurrency / shared state: an empty poll returns the stored record without replacing `_records`, so a later successful poll rebuilds from the same submission (`submitted_at`, `name`, `resources`).
- Non-goals: no `squeue`, no ExitCode provider change, no controller/`run_once` change, no config key, no retry/backoff beyond the existing poll loop.
