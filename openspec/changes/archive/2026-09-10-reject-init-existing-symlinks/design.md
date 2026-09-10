## Context
Issue type: bugfix. Profile: yd-viewer. Blast radius: medium (private helper has multiple consumers).
Fixture level: expanded. Repair intensity: high (state/path safety). Upstream suggested level: absent; mandatory path/state triggers. Minimal slice: user-selected #96 only.
Upstream seams: bootstrap/InitReport and CLI entry, existing m2-producer-core/tasks.md §11.1. No invented product choices.
## Goals / Non-Goals
Add: states/<source> itself or any depth underneath being symlink -> STATES_NOT_EMPTY in phase A, detail names link, written=(), no writes to either source and no target FOLLOW stat regardless of regular/directory/dangling/FIFO/unreadable/loop target.
Preserve: states-root symlink containing prior state still refuses; real empty directories are not state; actual regular state/DONE detection; calibration discovery (including FOLLOW failure -> DISCOVERY_UNREADABLE), output DONE name-filter/non-recursion policy, write-phase fail/no rollback semantics, source order and cfg.ic bytes.
Non-goals: change output symlink visibility, widen calibration rules, root/ancestor path redesign, safe_fs, #87, #97, recovery/rollback policy, actual node operations.
## Decisions
Use explicit state-only policy through existing _entry_kind/_first_regular_file, based on lstat before any FOLLOW stat. Default policy preserves output/calibration callers. Avoid globally treating symlinks as regular files or adding an unrelated framework.
Old issue acceptance 'dangling is invisible' and 'expand output' are superseded by published Wave0 §11.1 #96 ruling; record this deviation, do not follow stale issue text. Existing state-unreadable-link and write-phase dangling-link tests must migrate to phase-A refusal; retain non-symlink write-failure and calibration failure tests.
## Risk packs considered
- Public API / CLI / script entry: selected — bootstrap report and exit1.
- Config / project setup: not selected — unchanged.
- File IO / path safety / overwrite: selected — no FOLLOW stat, no writes, link preserved.
- Schema / columns / units / field names: not selected — no formats change.
- Auth / permissions / secrets: not selected — no auth change; unreadable target covered by IO.
- Concurrency / shared state / ordering: selected — phase A before all writes; races/locking unchanged.
- Resource limits / large input / discovery: selected — no symlink traversal/loop; existing tree discovery only.
- Legacy compatibility / examples: selected — output/calibration/default semantics unchanged.
- Error handling / rollback / partial outputs: selected — precise state refusal, zero partial bootstrap.
- Release / packaging / dependency compatibility: not selected — unchanged.
- Documentation / migration notes: selected — stale module/test comments migrated to already published ruling.
- Geospatial / CRS / shapefile sidecars: not selected — unchanged.
- Time series / forcing / temporal boundaries: not selected — unchanged raw/cycle rules.
- 状态链 / warm-start 定戳一致性: selected — prior entries cannot admit a second chain; successful sibling restamping unchanged.
- NWM 快照溯源与 DB-free 隔离: not selected — unchanged.
## Invariant Matrix
Governing invariant: any state-lane symlink rejects the entire bootstrap before writes without resolving the target; this policy never widens output or calibration predicates.
Identity: symlink lstat at states/<source> or descendants, existing rawscan.SOURCES and InitRefusal.
Producers: bootstrap phase B unchanged; validators: _entry_kind/_first_regular_file state-only policy.
Storage/read: state tree; output DONE and calibration are unchanged sibling consumers.
Public: bootstrap returns STATES_NOT_EMPTY/link path/written=(); CLI exit1.
Failure/rollback: refusal before raw/write; lstat errors still DISCOVERY_UNREADABLE; no deletion.
Evidence: public bootstrap matrices, no-follow sentinel, unchanged sibling tests, real smoke.
Rows: both sources × source-root/nested link × file/dir/dangling/FIFO/unreadable/loop -> state refusal/zero writes/no FOLLOW; same locations real empty directories -> successful two-source bootstrap; root states link containing prior file -> refuse; output links retain DONE-only behavior and hidden-directory policy; calibration FOLLOW errors retain discovery refusal and valid files parse/restamp unchanged.
Boundary checklist: all helper consumers; state reads; phase-A ordering; no writes/cleanup; output/calibration compatibility.
## Risks / Trade-offs
Tuple flag meaning must be documented for state policy; symlink presence is sufficient, no target liveness assumption. Existing TOCTOU and top-level root semantics not redesigned. Rollback: revert source/tests, no data migration.

## Keep / migrate oracle table
- Migrate test_init_bootstrap.py::test_state_symlink_into_an_unreadable_vault_refuses -> STATES_NOT_EMPTY before target stat.
- Migrate test_init_write_phase.py::test_foreign_entry_at_the_target_is_named_and_must_be_removed[dangling-symlink] and test_init_write_failure_wording.py::test_foreign_entry_at_the_parent_component_is_named_and_must_be_removed[symlink-to-dir|dangling-symlink] -> phase-A STATES_NOT_EMPTY, zero writes.
- Keep target ordinary empty-dir EEXIST and parent-component FIFO-as-entry -> WRITE_FAILED foreign-entry path. FIFO is not a symlink.
- Keep test_init_write_failure_wording.py::test_foreign_entry_higher_up_the_write_path_is_named_at_its_own_level[symlink-to-dir|fifo] at states/ itself (C-13) -> existing WRITE_FAILED; source/subtree policy does not widen to states/ root.
- Output DONE symlink-to-regular-file -> DONE_PRESENT; dangling DONE or symlink directory hiding DONE -> otherwise valid bootstrap succeeds. Calibration readable regular-file link -> successful locate/parse/restamp; inaccessible target -> DISCOVERY_UNREADABLE. These are explicit public bootstrap compatibility rows, not private-helper-only assertions.
