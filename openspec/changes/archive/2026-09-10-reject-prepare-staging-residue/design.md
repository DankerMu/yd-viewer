## Context
Issue type: bugfix. Project profile: yd-viewer. Blast radius: low (one startup guard).
Fixture level: expanded. Repair intensity: high (path safety). Upstream suggested level: absent; mandatory path trigger.
Minimal mergeable slice: the user-selected #83 startup rejection only.
Authoritative upstream seam: `run_prepare` and existing CLI exit-1 boundary (m2-producer-core/tasks.md §10.3).

## Goals / Non-Goals
Must add: top-level prefix-only discovery after both roots verify, before all work and target probes; deterministic complete path report, manual cleanup reference, no-follow and fail-closed discovery.
Must preserve: four-target no-overwrite/rollback, own-run cleanup, default-builder exit 3, successful prepare, init ignores root-level staging (its lane-specific preflight remains unchanged).
Non-goals: #87 refactor; sweep, reclamation, PID/mtime heuristics, recursion, locking/TOCTOU redesign, scratch residue, #78 crash recovery, production node operations.

## Decisions
Reuse safe_fs directory enumeration/no-follow primitives and PrepareError wrapping; only names matter, never inspect/follow matching entry targets. Enumerate once, sort matches, reject before final-target probing. A successful scan is not a concurrent-operation lock.
All docs already carry ruling B; no contract rewrite is needed. This isolated delta allows archiving #83 without archiving incomplete m2-producer-core.

## Risk packs considered
- Public API / CLI / script entry: selected — run_prepare/CLI error boundary.
- Config / project setup: not selected — unchanged configuration.
- File IO / path safety / overwrite: selected — prefix entries retained, symlinks never followed.
- Schema / columns / units / field names: not selected — unchanged.
- Auth / permissions / secrets: not selected — no auth change; read permission error covered by IO.
- Concurrency / shared state / ordering: selected — guard precedes side effects; locking is non-goal.
- Resource limits / large input / discovery: selected — top-level only, no recursive reads.
- Legacy compatibility / examples: selected — clean root and init unchanged.
- Error handling / rollback / partial outputs: selected — enumeration failure typed; zero new output.
- Release / packaging / dependency compatibility: not selected — unchanged.
- Documentation / migration notes: selected — existing agent-ops manual procedure appears in diagnostic.
- Geospatial / CRS / shapefile sidecars: not selected — unchanged geometry.
- Time series / forcing / temporal boundaries: not selected — unchanged.
- 状态链 / warm-start 定戳一致性: not selected — unchanged.
- NWM 快照溯源与 DB-free 隔离: not selected — unchanged builder boundary.

## Invariant Matrix
Governing invariant: pre-existing staging-like entries are protected existing data: any top-level prefix hit or discovery failure blocks prepare before work, without modifying anything.
Source of truth: prepare._STAGING_PREFIX and verified YD_ROOT.
Producers: existing staging creation in run_prepare (unchanged).
Validators/preflight and public entry: run_prepare startup -> PrepareError -> cli exit 1.
Storage/write/delete/publish: no new work on rejection; existing own-token staging, commit and rollback unchanged.
Consumers: init lane-specific guard ignores staging; viewer output unchanged.
Failure/stale boundary: every entry type and enumeration SafeFilesystemError or OSError (EACCES/EIO/ESTALE); no auto-cleanup.
Evidence: test_prepare.py public seam snapshots and CLI smoke.
Regression rows: mixed matching entries -> sorted complete rejection and unchanged trees; enumeration OSError -> PrepareError/no work; clean root or nested-only/non-prefix names -> injected-builder success and unchanged default-builder exit 3; init with actual top-level prefix entries on otherwise fresh lanes -> no STATES_NOT_EMPTY refusal, no claiming/deletion.
Boundary checklist: verified root read; startup ordering; no-follow names; no writes/cleanup on rejection; unchanged commit/rollback, init and viewer.

## Risks / Trade-offs
A live prepare staging also blocks a later caller by design; operators confirm ownership before manual cleanup. No race-free mutual exclusion claim. Rollback is revert of the guard only; no data migration.
