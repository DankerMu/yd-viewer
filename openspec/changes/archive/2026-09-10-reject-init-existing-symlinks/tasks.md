## 1. State-only classification
- [x] 1.1 Implement explicit state policy before FOLLOW stat, migrate every private helper caller without altering output/calibration behavior; update stale in-code comments only.
- [x] 1.2 Public bootstrap matrix covers both sources/source-root/nested symlink and file/dir/dangling/FIFO/unreadable/loop target: STATES_NOT_EMPTY, exact path, written=(), unchanged trees, no FOLLOW target stat or phase-B write; direct no-follow syscall fault injection is allowed at OS boundary.
- [x] 1.3 Reverse controls real empty directories succeed; existing states-root link with state refuses; output DONE/non-DONE/dangling/directory links and calibration file/FOLLOW error behavior unchanged; migrate old dangling stage-B carrier to state refusal while keeping real-empty-dir EEXIST evidence.
## 2. Verification and review
- [x] 2.1 Parent batched old-source red proof then focused green and public bootstrap/CLI smoke; leaf skips validation per harness policy.
- [x] 2.2 Parent producer/viewer pytest, Ruff/check-format, OpenSpec strict/all, stage anchor and actual merge-result check pass; no oracle weakening.
- [x] 2.3 Four high-risk seats and independent final review on frozen SHA, CI/evidence before preauthorized merge.

## Keep / migrate oracle table
- Migrate test_init_bootstrap.py::test_state_symlink_into_an_unreadable_vault_refuses -> STATES_NOT_EMPTY before target stat.
- Migrate test_init_write_phase.py::test_foreign_entry_at_the_target_is_named_and_must_be_removed[dangling-symlink] and test_init_write_failure_wording.py::test_foreign_entry_at_the_parent_component_is_named_and_must_be_removed[symlink-to-dir|dangling-symlink] -> phase-A STATES_NOT_EMPTY, zero writes.
- Keep target ordinary empty-dir EEXIST and parent-component FIFO-as-entry -> WRITE_FAILED foreign-entry path. FIFO is not a symlink.
- Keep test_init_write_failure_wording.py::test_foreign_entry_higher_up_the_write_path_is_named_at_its_own_level[symlink-to-dir|fifo] at states/ itself (C-13) -> existing WRITE_FAILED; source/subtree policy does not widen to states/ root.
- Output DONE symlink-to-regular-file -> DONE_PRESENT; dangling DONE or symlink directory hiding DONE -> otherwise valid bootstrap succeeds. Calibration readable regular-file link -> successful locate/parse/restamp; inaccessible target -> DISCOVERY_UNREADABLE. These are explicit public bootstrap compatibility rows, not private-helper-only assertions.
