## Context
Change surface: _open_parent_dir, _open_directory_no_follow, open_directory_no_follow, _list_directory_no_follow in safe_fs.py.
All four currently close previous before assigning successor to tracked current.
Inventory convention6 authorizes local fork with prior row registration; no new Wave0.

## Goals / Non-Goals
Governing invariant: after a child open succeeds, successor is owned before any previous close; a failed previous close causes exactly one successor cleanup attempt, never a retry of previous.
Must preserve: root and current distinct ownership; O_NOFOLLOW/O_DIRECTORY, containment, validation, root-only dup semantics and returned-fd/list contracts.
Must preserve: _open_parent_dir consumers open_file_no_follow, atomic/exclusive write, stat, unlink and rmtree retain parent tuple and exception behavior.
Must preserve: public directory consumers verify_tree_no_symlinks, rename_entry_no_follow, _tree_delete and _assemble_fs own only returned fds.
Must preserve: verify_directory_no_follow/directory_identity_no_follow and ensure_directory_no_follow retain private opener behavior; list_directory_no_follow and limited list preserve names and max_entries+1 sentinel semantics.
For injected previous OSError, open/parent/private-open expose the same error object; list keeps SafeFilesystemError(kind=io) with that previous error as cause. Cleanup OSError is secondary and does not replace that primary/cause.
Sibling surfaces: all four named walkers are in scope; _open_child_dir admission and _open_verified_dir guards remain unchanged; #55 ensure and #225 file ownership are regression controls.
Non-goals: #185 iterator, #232 repeated non-OSError interruption during cleanup, #42/#55/#122 implementation changes, generalized IO framework, remote/NFS verification.

## Decisions
Assign current=successor while keeping previous as a separate local before attempting previous close.
Never put failed previous back into any cleanup-owned slot or retry it; its consumed/live state is unspecified.
Ensure successor and root are each cleaned on handoff failure, even when one cleanup reports OSError; preserve primary/cause with secondary diagnostics.
Prefer small common directory cleanup machinery only if it removes duplicated policy; preserve no-follow admission and all caller boundaries.
Root remains pinned until walking concludes; successful fd return must retain caller ownership.

## Risks / Trade-offs
safe_fs.py starts at999 lines: stay within existing1000-line guard by consolidating touched walker logic, not deleting unrelated code/docs or weakening configuration.
Tests distinguish previous consumed-before-error vs unconsumed-on-error without making any platform liveness assumption; test-owned leftovers are cleaned separately.
Bounded repeated faults detect accumulated successor ownership using captured real fds, not process-wide fd counts or lowered global limits.

## Required Evidence
Each of four walkers: multi-component real directory tree, child open returns successor, previous close OSError -> successor closed once, previous attempted once, root cleanup, preserved exception/cause.
Injection must target each named walker's own non-root handoff, not its nested absolute-root opener: containment-relative tree has at least two components (for _open_parent_dir, the parent has at least two). Record that specific _open_child_dir returned successor, then inject on its captured previous fd. A fix only to the nested _open_directory_no_follow must not make the other three regressions pass.
Cover consumed and unconsumed previous injection for each walker; verify successor EBADF only after successful cleanup.
Secondary successor/root cleanup OSError -> original previous object (or list-domain error cause) remains primary, with add_note diagnostics using existing _descriptor_close_note; tests inspect error details in __notes__ on the primary/cause, not exact sentence wording. No retry or fd-liveness assertion for failed cleanup.
Repeat a bounded number of deep faults in each walker -> no accumulated successfully closable successors.
Success: root-only and multi-level open/parent return readable directory fds; listing names/limited sentinel unchanged; symlink/containment refusal via existing suite.
Run defect regressions red before fix then green; existing compatibility controls may already pass baseline.

## Migration Plan
Inventory registration then implementation; rollback source commit without undoing historical provenance. Archive/sync in same PR before final CI because master is protected and user requires one issue per PR; merge remains external SHA-bound gate.
