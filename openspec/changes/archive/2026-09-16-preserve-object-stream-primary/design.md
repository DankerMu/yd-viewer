## Context
Change surface: LocalObjectStore.iter_bytes in producer/src/yd_producer/store/object_store.py.
Current direct finally os.close can override read ObjectStoreError or KeyboardInterrupt/SystemExit.
Inventory convention6 permits local fork; register object_store's own row first, no new Wave0.

## Goals / Non-Goals
Governing invariant: every acquired stream file fd receives one cleanup attempt on EOF/read-error/KI/SystemExit; close OSError never replaces an existing read/cancellation primary and is never retried.
Must preserve: lazy generator execution, chunk_size validation timing, chunk bytes/order, no-follow and containment via open_file_no_follow, pre-acquisition ObjectStoreError mapping/cause.
Must preserve: LocalObjectStore.size_and_checksum/checksum exact successful size/digest; on stream failure no successful partial checksum result.
Must preserve: canonical.converter._staged_contained_raw_path maps ObjectStoreError to CanonicalConversionError; no canonical caller edits.
Must preserve: current assembly SharedAssemblyIO.iter_regular / BoundStore.iter_bytes are separate implementations; legacy bind_store can return LocalObjectStore. Assembly success/failure results remain covered by existing full producer suite, not falsely claimed as direct iterator calls.
Sibling surfaces: size_and_checksum/checksum direct consumers; bounded safe_fs readers and SharedAssemblyIO.iter_regular demonstrate primary-preserving cleanup shape, inspected but not modified.
Non-goals: #225 opener, #183 walkers, #122 readers, #42 writer, #55 ensure-directory, #232 second non-OSError cleanup interruption, remote/NFS or upstream repin.
GeneratorExit/consumer early-stop policy is a separate explicitly excluded issue lane: do not yield during cleanup, suppress GeneratorExit explicitly, wrap cancellation into domain errors or add retry. Preserve Python generator protocol without defining a new early-stop strategy.

## Decisions
Retain finally-based one-shot close; collect its OSError without allowing it to replace a captured primary.
Converge at the iterator boundary: read OSError -> ObjectStoreError raised from the identical read error; attach secondary close detail to the outward primary error via add_note.
No read error plus close OSError -> ObjectStoreError raised from the identical close error.
KeyboardInterrupt/SystemExit -> raise identical original object with any close OSError as note, not ObjectStoreError.
Catch BaseException only to retain/rethrow, never to indiscriminately wrap into a domain error. Ensure convergence is reached on exception as well as EOF across yield suspension.
Prefer existing one-shot cleanup/note convention, without introducing a shared generic framework or changing safe_fs.

## Risks / Trade-offs
Failed close does not imply a live or dead fd; assert attempt counts and notes, not post-failure liveness.
Narrow injections arm after real open_file_no_follow returns to isolate this post-acquisition owner from directory and opener cleanup.
Tests must exercise a yielded chunk before a later read failure/cancellation as well as EOF, not only fail before generator suspension.

## Required Evidence
Real file payload -> exact chunks, EOF then one close; size_and_checksum -> independently computed digest and exact size.
Read OSError alone and read+close OSError -> ObjectStoreError with read object as __cause__, close details in __notes__ only when present, one close.
EOF then close-only OSError -> ObjectStoreError whose __cause__ is close object, no retry.
KI/SystemExit alone and with close OSError -> same sentinel object escapes, close attempted once, secondary details only.
Read/close fault through size_and_checksum -> same cause semantics and no successful partial result.
No-follow symlink/containment/chunk_size/pre-open error controls remain via existing object-store/refusal/full-suite coverage.
Ordinary generator close compatibility may be smoked without imposing new GeneratorExit+close-failure policy; no explicit GeneratorExit swallowing/yielding.
New defect regressions red on baseline then green; passing baseline compatibility controls labeled separately.

## Migration Plan
Inventory before code; rollback source with provenance retained. Expanded cross-review, archive/spec sync in same PR, final SHA-bound CI/merge gate. No caller migration.
