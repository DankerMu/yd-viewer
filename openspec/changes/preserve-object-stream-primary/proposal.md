## Why
Issue #185: iter_bytes finally close can replace a read error/cancellation and expose bare close OSError. The stream API must retain its primary while closing the returned file descriptor once.

Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent (expanded: shared streaming file IO API)
Blast radius: LocalObjectStore stream/checksum and canonical raw staging consumers.
Selected risk packs: Public API; File IO; Resource limits; Error handling; Documentation
Evidence floor: real-fd read/EOF/cancellation+close red/green, consumer checks, producer pytest/ruff/line guard, OpenSpec strict/all.

## What Changes
- One-attempt finally cleanup and primary-aware convergence in iter_bytes.
- Read OSError remains ObjectStoreError cause; close-only OSError becomes ObjectStoreError cause; cancellation identity preserved with close notes.
- Prior inventory registration in object_store row under existing local-fork authorization.

## Capabilities
### New Capabilities
- `object-stream-close-causality`: primary-preserving one-shot stream cleanup.
### Modified Capabilities
None.

## Impact
object_store.py::LocalObjectStore.iter_bytes and yd-owned tests only, plus workflow artifacts. No safe_fs, caller, dependency, format or signature changes.
