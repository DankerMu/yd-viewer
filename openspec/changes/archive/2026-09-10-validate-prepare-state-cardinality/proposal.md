## Why
Implement user-selected #97 / Wave0 ruling 15 at the prepare submission boundary. Current exact-five handoff validation already rejects extra files generically; the missing behavior is the shared top-level calibrated-state cardinality check and its source/directory/count/candidate diagnostic.
## What Changes
- Require exactly one top-level `*.cfg.ic` regular-file candidate before any YD_ROOT staging/commit, sharing init's existing locating predicate.
- Preserve VARIANT_CALIBRATED_STATE_NAME = `yd.cfg.ic`, fixed path and exact-five handoff, never select a backup/alternate by discovery.
## Capabilities
### New Capabilities
- `prepare-state-cardinality`: scoped Wave0 implementation acceptance.
### Modified Capabilities
None; parent prepare-variants/tasks §10.3 and compute-loop §6.1 already contain ruling.
## Impact
Only producer/src/yd_producer/prepare.py, init.py if needed for shared locator, and related tests. #87 refactor excluded.
