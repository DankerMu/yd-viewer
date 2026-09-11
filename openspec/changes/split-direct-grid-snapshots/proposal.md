## Why
#114 removes three temporary large-file exemptions without changing forcing behavior, old imports or retained test collection. Six user-ordered fixes (#103/#102/#104/#105/#119/#127) are merged before this split.
## What Changes
Mechanically relocate definitions/methods by placement.json. Original producer.py and file_store.py retain concrete public classes and protected module-global read sites; explicit re-exports preserve all baseline names. Empty-state method mixins carry disjoint unchanged method bodies. Original test entry retains all13 seed functions/17node IDs; only fake/builders/assets move to three non-test-named modules. Every original/new snapshot stays strictly under1000lines. Remove exactlythree large-file exclusions; preserve other excludes/threshold.
## Capabilities
### New Capabilities
- `direct-grid-snapshot-module-boundaries`: bounded modules with compatible imports, provenance and retained oracle closure.
### Modified Capabilities
None (structural relocation only).
## Impact
Originalthreepaths,15new snapshot support modules, snapshotinventory, exactlarge-file exclusions and exactpath inherited Ruff snapshot allowances where required. No runtime algorithm/schema/units/time/security/lineage/IO changes; no new tests or dependencies.
