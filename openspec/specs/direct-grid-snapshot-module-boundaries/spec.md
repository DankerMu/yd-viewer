# direct-grid-snapshot-module-boundaries Specification

## Purpose
TBD - created by archiving change split-direct-grid-snapshots. Update Purpose after archive.
## Requirements
### Requirement: Direct-grid snapshot decomposition preserves compatibility and oracles
The three original forcing snapshot paths and every split source/test support module MUST remain strictly below1000lines without new large-file exemptions or changed threshold. The split MUST preserve all existing executable function/method bodies, constants, dataclass state, public imports, protected injection read sites, decoded/output/error behavior and all retained test oracles. Each moved definition MUST have exactly one owner and its exact NWM provenance registration. All following scenarios are independently REQUIRED.
#### Scenario: Existing imports and mutable read seams
- **WHEN** consumers import yd_producer.forcing and old producer/file_store modules or exercise existing LocalObjectStore/open_canonical_netcdf/MAX_OBJECT_MANIFEST_BYTES injections
- **THEN** baseline symbols/signatures/classaliases and actualreadsites remain compatible without bulkcaller changes
#### Scenario: Retained seed collection
- **WHEN** pytest collects the original test_forcing_producer.py entry
- **THEN** the exact17baseline IDs and13seed bodies/decorators remain, support is not duplicatecollected, and fake/builder/assertion closures preserve meaning
#### Scenario: Pure relocation and provenance
- **WHEN** originalcallables/state/constants are matched to placementowners
- **THEN** each exists exactlyonce with unchanged AST/values, every extractedmodule has its precise NWMheader/inventory row, and no scientific/security/serialization fix is folded in
#### Scenario: Size guard restored
- **WHEN** all old/new snapshotfiles are staged with the guardconfig
- **THEN** each is <1000lines, exactlythe three requested excludes are absent, otherexcludes/threshold unchanged, and the actualguard accepts
#### Scenario: Mutation and runtime compatibility
- **WHEN** all53 inherited numbered forcing mutationlegs are applied individually using current lower-caseIFS and Darwin-memory/Linux-alias contracts
- **THEN** actual corresponding tests turn red, exactsource is restored betweenlegs, and restoredfocused/full suites plus realforcing smoke remain green without weakening literal/failure oracles

