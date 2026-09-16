## Context
Change surface: producer/src/yd_producer/store/safe_fs.py::open_file_no_follow.
The current Exception handler misses KeyboardInterrupt/SystemExit and direct close can mask the primary.
Inventory convention 6 permits this local fork; snapshot test bodies remain unchanged.

## Goals / Non-Goals
Governing invariant: every acquired file fd is either returned after validation and parent cleanup or receives exactly one close attempt; cleanup never replaces an existing primary.
Must preserve: no-follow flags, containment, regular-file and inode/device checks, return type, SafeFilesystemError kind and OSError propagation.
Must preserve: successful consumers own the returned fd and can read its bytes.
Must preserve (existing producer-suite coverage, no caller edits): controller._read_header_line maps opener OSError/SafeFilesystemError to STATE_UNREADABLE; LocalObjectStore.iter_bytes wraps these in ObjectStoreError with cause. Bounded readers, netcdf_open.open_canonical_netcdf, tracker digest/open, publish._open_scratch_dat, assemble copy/stream, nwm digest and cleanup merged_log own only successfully returned descriptors and retain their existing error mapping.
Non-goals: directory walkers (#183), iter_bytes (#185), atomic writer, ensure-directory, bounded-reader changes, caller edits, generalized cleanup framework.
Sibling surfaces: _close_acquired_file_fd / _file_close_note and bounded readers establish one-attempt secondary-note precedent; object_store.iter_bytes and all callers only own successfully returned fds.
Sibling audit is read-only except open_file_no_follow; other issues remain separate.

## Decisions
Keep ownership inside the opener through its parent cleanup, rather than declaring transfer before a finally which can still fail.
On validation failure, attempt file close once, then parent close once; preserve the original exception object and attach cleanup failures as notes.
On successful validation followed by parent-close failure, clean the unreturned file once and propagate that parent failure unchanged.
A parent-close failure during an existing primary is secondary, not a new primary.
Reuse existing one-attempt helper where its OSError contract suffices; do not retry failed close or infer fd state.
Retain error behavior before acquisition and avoid touching walker ownership in this issue.

## Risks / Trade-offs
Failed close has platform-dependent fd state: tests assert attempts and diagnostics, never liveness after failed close.
Fault injection must target only captured real descriptors; cleanup of test-owned leftovers is separate from assertions.
Tests use regular temporary files, no blocking FIFOs or unbounded waits.

## Required Evidence
Seams under test: public open_file_no_follow with real os.open and narrow injected fstat/identity/close failures.
KeyboardInterrupt and SystemExit at post-open fstat -> same exception object, one file close, parent returned.
Validation OSError, nonregular mode, file identity mismatch and final parent identity rejection -> original kind/cause, one file close.
Validation failure plus file/parent close failure -> original primary and secondary notes, no retries.
Parent close fails after valid open -> no fd returned, one file close, original parent failure.
Success -> returned fd reads exact payload and remains open until caller closes it.
New failure regressions run red against baseline, then green after the fix; compatibility controls may already pass baseline.

## Migration Plan
Register inventory before implementation; no runtime migration. Rollback is a source revert retaining historical provenance.
Review focus: complete acquisition-to-return ownership, exception identity, one-attempt close, unchanged refusal semantics.
