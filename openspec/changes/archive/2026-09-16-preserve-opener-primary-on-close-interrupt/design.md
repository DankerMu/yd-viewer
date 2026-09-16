## Context
Change surface: producer/src/yd_producer/store/safe_fs.py::open_file_no_follow cleanup loop.
The current helper catches OSError only, so another BaseException skips the next pending fd.
Governing invariant: after a primary failure, each owned pending descriptor receives at most one independent close attempt and the identical primary escapes.

## Goals / Non-Goals
Must add/change: isolate each secondary close BaseException and attach role/type/message diagnostics.
Must preserve: file-before-parent order, one attempt per fd, original primary identity and existing OSError notes.
Must preserve: successful return transfers the live file fd to the caller; already-attempted parent close is never retried.
Must preserve: no-follow validation, post-open identity checks and safety refusal kinds.
Non-goals: directory walkers (#183), object-stream close (#185), bounded-reader semantics (#122), arbitrary async interruption between Python instructions, close retry, inferred failed-close liveness, or production frequency claims.

## Decisions
Catch secondary BaseException only around the opener's helper call; continue remaining ownership cleanup.
Use existing role-specific note formatting; avoid a new cleanup abstraction.
Freeze _close_acquired_file_fd and _file_close_note signatures/return behavior and OSError-only handling; isolate BaseException only at the opener call boundary.
Alternative nested finally ownership flow is rejected because it increases transfer/retry complexity.
Sibling surfaces: read_bytes_no_follow, read_bytes_limited_no_follow, read_tail_bytes_limited_no_follow, _close_directory_fds and LocalObjectStore.iter_bytes use the unchanged helpers; directory-walker and stream-close changes are excluded.
Seams under test: real acquired file/parent fds, injected post-open fstat primary and recorded close attempts.

## Required Evidence
Validation primary plus file-close KeyboardInterrupt -> identical primary, secondary diagnostic, file then parent each once.
Validation primary plus both file/parent secondary interrupts -> identical primary, both diagnostics, no retry.
Parent-only secondary interruption -> identical primary and role-specific diagnostic.
Existing OSError cleanup, safety refusals, parent-close failure and successful transfer tests remain green.
Explicit unchanged controls: test_safe_fs_open_ownership.py (#225 OSError identity/notes) and test_safe_fs_reads.py (#122 reader contract); #183/#185 run only as sibling controls.
Snapshot controls: inventory row 42 published before source; preserve NWM provenance header within HEADER_LINE_BUDGET; never edit pinned tests/test_safe_fs.py.
Tests must be red on pre-change opener and green after; fixture finalization cleanup is outside observed attempts.
Commands: producer uv run pytest; uv run ruff check .; uv run ruff format --check .; uv run python ../scripts/large_file_guard.py; openspec validate --all.

## Risks / Trade-offs
Swallowing a cleanup interrupt delays cancellation only to preserve an already-active primary; diagnostic notes retain the secondary.
Failed close state is unspecified -> no retry or liveness claim.
Review focus: primary identity, parent progression, one-shot cleanup, unchanged shared consumers.
Migration: no data migration; revert the narrow source/test change if needed.
