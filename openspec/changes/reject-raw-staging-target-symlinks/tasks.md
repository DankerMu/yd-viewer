## 1. Implementation
- [x] 1.1 Add destination-relative no-follow symlink admission before any mkdir/copy/manifest write, preserving root aliases and nine kinds.
- [x] 1.2 Add public stage_raw regressions for raw/source/cycle/leaf symlinks, internal/dangling destinations and valid root alias; compare complete work and link-target snapshots. Preserve source bytes and normal no-clobber.
- [x] 1.3 Inspect existing controller WorkClaim dependency and unchanged claim writers; record inspected surfaces and deviations.

## 2. Evidence
- [x] 2.1 Orchestrator runs focused regressions against baseline then fixed source; negative link tests fail baseline; fixed rawcopy suite and producer suite pass.
- [x] 2.2 Run profile matrix: producer/viewer pytest and ruff check/format --check, openspec validate --all, stage-pipeline log gate; required Ubuntu CI, no macOS CI.

## Risk packs
- Public API / CLI / script entry: selected — stage_raw stable typed failure and root compatibility.
- Config / project setup: not selected — no config change.
- File IO / path safety / overwrite: selected — component matrix + two-sided no-follow snapshots + no-clobber regression.
- Schema / columns / units / field names: not selected — manifest contents unchanged, existing success suite.
- Auth / permissions / secrets: not selected — no new policy, IO failures use existing kind.
- Concurrency / shared state / ordering: selected — all destination checks precede writes; existing WorkClaim tests; new standalone concurrent swap guarantees explicitly excluded.
- Resource limits / large input / discovery: not selected — bounded destination components, no discovery changes.
- Legacy compatibility / examples: selected — valid symlink root and ordinary callers remain accepted.
- Error handling / rollback / partial outputs: selected — refusal has zero new paths on both sides, existing rollback tests unchanged.
- Release / packaging / dependency compatibility: not selected — no package/dependency/CI changes.
- Documentation / migration notes: selected — fixture explains existing §3.3/§7.2 work containment, no governing-doc contradiction.
- Geospatial / CRS / shapefile sidecars: not selected — untouched.
- Time series / forcing / temporal boundaries: not selected — cycle and leads unchanged.
- 状态链 / warm-start 定戳一致性: not selected — untouched.
- NWM 快照溯源与 DB-free 隔离: selected — source tree remains read-only, existing snapshots.
Seams under test: #71 public stage_raw; caller-supplied root alias and relative destination components. No new seam deviation.
