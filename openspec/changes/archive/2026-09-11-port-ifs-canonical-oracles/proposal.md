## Why
Issue #105 restores the fifteen omitted IFS canonical oracle scenarios from NWM@8ae9b8f29c8b72c574e8cbd95f2994160bd42832 tests/test_ifs_canonical.py. User selects route A: pinned oracle migration, not re-derivation or converter behavior changes.
## What Changes
- Add DB-free producer/tests/test_ifs_canonical.py with all fifteen pin scenarios and provenance.
- Replace fake repository observations with actual catalog/product observations, explicit yd config paths and lowercase canonical identity.
- Independently collect three precipitation branches and preserve shortwave, Magnus RH and lineage values/structure.
- Prove each previously uncovered branch with old-suite-survives/new-test-kills mutants; close the known coverage-loss record.
## Capabilities
### New Capabilities
- `ifs-canonical-oracle-coverage`: pinned DB-free IFS numerical, QC and lineage regressions.
### Modified Capabilities
None; tests-only, no production behavior change.
## Impact
One new snapshot test module, inventory and issue13 Known limits. No converter/runtime/config/dependency/DB changes, no live-node validation claim.
