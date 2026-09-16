## Why
Issue #219: recovery currently buffers merged stdout/stderr with communicate() before appending, unlike the bounded primary SHUD reader.
This independent post-M2 fixture changes consumption, not log contents or retention.

## What Changes
- Reuse _run_shud_live chunk reads/EOF drain for recovery; remove recovery communicate().
- Preserve main-run capture, recovery return codes, ordered job.log and receipt checksum/collect authority.

## Capabilities
### Modified Capabilities
- `cli-config`: bounded worker log consumption for primary and recovery.

## Impact
Only nwm.py and corresponding tests. No external dependency or operational deployment.
Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent (file IO, process ordering and resource limits)
Blast radius: job.log bytes/checksum and worker checkpoint receipt.
Selected risk packs: File IO / path safety / overwrite; Concurrency / shared state / ordering; Resource limits / large input / discovery; Error handling / rollback / partial outputs; Legacy compatibility / examples; Documentation / migration notes
Evidence floor: multi-chunk real synthetic SHUD output; 520000/1040000-byte equality/checksum and collect, bounded append sizes; producer suite/ruff and OpenSpec.
