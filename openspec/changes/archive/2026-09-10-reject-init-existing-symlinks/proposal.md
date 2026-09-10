## Why
Issue #96 / Wave 0 ruling 8 closes state bootstrap visibility: symlink directory/dangling/special targets are existing entries, never an empty state lane. Parent m2-producer-core §11.1 and compute-loop §6.2 already settle this.
## What Changes
- State-only classification uses lstat symlink identity and refuses before FOLLOW stat, raw scanning or writes.
- Keep output DONE and calibration discovery compatibility; ordinary empty directories remain allowed.
## Capabilities
### New Capabilities
- `init-state-symlink-preflight`: scoped implementation acceptance for ruling 8.
### Modified Capabilities
None; parent contract already contains the ruling.
## Impact
producer/src/yd_producer/init.py and related init tests; no schema/API/dependency changes. #87 refactor and #97 excluded.
