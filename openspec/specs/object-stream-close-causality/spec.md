# object-stream-close-causality Specification

## Purpose
Define one-attempt object-stream cleanup that preserves read-error causality and cancellation identity while retaining chunking, admission and checksum contracts.
## Requirements
### Requirement: Preserve object stream primary across one-shot cleanup
LocalObjectStore.iter_bytes SHALL attempt close exactly once on acquired file descriptors after EOF, read OSError, KeyboardInterrupt or SystemExit. A read OSError SHALL remain the cause of outward ObjectStoreError; close-only OSError SHALL become ObjectStoreError cause; KI/SystemExit SHALL propagate as the same object. Concurrent close OSError SHALL be secondary note evidence, not replace the primary or trigger retry. Existing chunking, admission, checksum and Python generator protocol SHALL be preserved without introducing an early-stop policy.

#### Scenario: Read fails and cleanup also fails
- **WHEN** a real stream yields a chunk then reading raises OSError and close raises OSError
- **THEN** ObjectStoreError has the identical read failure as cause, close details are secondary notes and file close is attempted once

#### Scenario: Only cleanup fails
- **WHEN** stream reaches EOF and close raises OSError
- **THEN** outward ObjectStoreError has that close failure as cause, with no retry or fd-state inference

#### Scenario: Cancellation retains object identity
- **WHEN** reading raises a sentinel KeyboardInterrupt or SystemExit, with or without close OSError
- **THEN** the same cancellation object escapes, file close is attempted once and any close OSError is recorded secondarily

#### Scenario: Successful stream and checksum remain compatible
- **WHEN** a contained regular file is streamed or consumed by size_and_checksum without faults
- **THEN** exact chunk bytes/order, size and checksum remain correct and EOF cleanup attempts close once

#### Scenario: Checksum consumer observes stream failure
- **WHEN** size_and_checksum encounters read failure or EOF close failure
- **THEN** no successful partial result is returned and the same ObjectStoreError cause rules apply

