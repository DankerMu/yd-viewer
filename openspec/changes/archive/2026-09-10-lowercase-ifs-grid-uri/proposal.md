## Why
Issue #104 and Wave0 decision13 choose option A: one lowercase IFS canonical scratch namespace. Runtime still emits the pin uppercase grid URI despite already-approved m2 specs. APFS masks filesystem case differences, so consumer string assertions are the oracle.
## What Changes
- Canonical config and forcing default grid URI become canonical/ifs/grid/ifs_0p25/grid.json.
- Migrate all yd tests/fixtures relying on the old emitted URI, without case aliases or fallback.
- Register the forcing caller adaptation alongside the already-approved converter inventory fork.
## Capabilities
### New Capabilities
- `lowercase-ifs-grid-identity`: one lowercase IFS grid identity from canonical to forcing.
### Modified Capabilities
None; consumes approved m2 forcing-chain decision16 and Wave0 decision13.
## Impact
Canonical config, forcing file-store default, yd tests/fixtures, inventory. No file migration: work tree is per-cycle disposable; raw source capitalization and generic object_path parser unchanged.
