## Why
Issue #83 implements Wave 0 ruling 10 already published in compute-loop §6.1, products-contract §2, agent-ops §8.1 and m2-producer-core prepare-variants. Interrupted staging must no longer accumulate silently.

## What Changes
- Reject every top-level `_STAGING_PREFIX` name before final-target probes, scratch creation or builder calls; report sorted absolute paths and the manual cleanup reference.
- Preserve all existing entries and convert enumeration failures to PrepareError.

## Capabilities
### New Capabilities
- `prepare-staging-preflight`: issue-scoped implementation acceptance for the existing Wave 0 ruling.
### Modified Capabilities
None; the parent m2-producer-core contract already contains the ruling.

## Impact
Only producer/src/yd_producer/prepare.py and prepare regression tests. No API/schema/dependency change. #87 refactoring is excluded.
