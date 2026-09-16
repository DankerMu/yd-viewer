## 1. Fixture and provenance
- [x] 1.1 Register #225 problem and repair in the safe_fs inventory row before source changes.
- [x] 1.2 Obtain fixture pass and strict OpenSpec validation.

## 2. Implementation and evidence
- [x] 2.1 Add real-descriptor local regressions for interruptions, rejection, close precedence and success; show failure regressions red before fix.
- [x] 2.2 Repair only open_file_no_follow ownership through return and demonstrate targeted green tests.
- [x] 2.3 Run producer `uv run python -m pytest`, `uv run ruff check .`, `uv run ruff format --check .`, plus `openspec validate --all` and strict change validation.

## 3. Delivery
- [ ] 3.1 Complete cross-review, green CI, merge gate and archive.

## Risk pack mapping
- Selected Public API / CLI / script entry: unchanged signature and exception families; success/refusal regressions (2.1).
- Selected File IO / path safety / overwrite: real fd ownership, same no-follow/identity checks (2.1–2.2).
- Selected Resource limits / large input / discovery: exactly one close attempt for unreturned fd, bounded tests (2.1).
- Selected Error handling / rollback / partial outputs: KI/SystemExit identity, OSError identity and close notes including parent cleanup (2.1).
- Selected Documentation / migration notes: inventory registration (1.1); no new Wave 0.
- Not selected Auth / permissions / secrets: unchanged path-safety and permission behavior tested by existing suite; no auth changes.
- Not selected Concurrency / shared state / ordering: no new concurrency; existing filesystem identity protections retained under File IO.
- Not selected Legacy compatibility / examples: no caller migration; existing callers covered by producer suite.
- Not selected Config / project setup, Schema / columns / units / field names, Release / packaging / dependency compatibility: no changes.
