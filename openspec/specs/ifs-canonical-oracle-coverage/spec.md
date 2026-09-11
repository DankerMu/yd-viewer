# ifs-canonical-oracle-coverage Specification

## Purpose
TBD - created by archiving change port-ifs-canonical-oracles. Update Purpose after archive.
## Requirements
### Requirement: Preserve the pinned IFS numerical and lineage oracles without DB dependencies
The yd test suite SHALL carry all15 scenarios from NWM@8ae9b8f29c8b72c574e8cbd95f2994160bd42832 tests/test_ifs_canonical.py, adapting repository observations to persisted catalog/products without modifying converter behavior.
#### Scenario: Negative precipitation branches
- **WHEN** small, significant and third-consecutive negative precipitation deltas are tested independently
- **THEN** they preserve zero-clamped values, respectively ok+small_negative_ifs_precipitation_delta, warning_negative_precip, and error_precip_accumulation with pinned counters
#### Scenario: Shortwave quantization and warning
- **WHEN** pinned small and significant negative shortwave deltas are converted
- **THEN** small remains ok with small anomaly and significant is warn with negative_ifs_shortwave_delta, including persisted product lineage
#### Scenario: RH and per-variable lineage
- **WHEN** pinned IFS temperature/dewpoint inputs and variable families produce canonical products
- **THEN** RH readback is approx0.525(abs1e-3), method is magnus_formula, and pinned per-variable lineage structure/units/values hold
#### Scenario: DB-free missing radiation
- **WHEN** ssr is absent from the pinned input manifest
- **THEN** conversion rejects both derived radiation requirements without creating canonical products or catalog, rather than reconstructing absent DB fail rows
#### Scenario: Demonstrable incremental mutation coverage
- **WHEN** each required previously uncovered branch is changed by its recorded one-line mutant
- **THEN** prior full producer tests survive and the migrated oracle kills the mutant; positive control confirms injection, and runtime source remains unchanged

