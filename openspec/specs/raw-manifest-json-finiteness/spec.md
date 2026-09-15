# raw-manifest-json-finiteness Specification

## Purpose
TBD - created by archiving change reject-nonfinite-raw-manifest. Update Purpose after archive.
## Requirements
### Requirement: Serialize raw manifests as finite JSON
Raw staging MUST serialize its output with allow_nan=False and MUST map nonfinite carried values through the existing RawStagingError source-manifest branch before any copies or manifest writes. Valid finite output schema and contents MUST remain unchanged.
#### Scenario: Nonfinite carried nested value
- **WHEN** a carried metadata object contains NaN, positive Infinity, or negative Infinity
- **THEN** stage_raw raises RawStagingError with kind source-manifest and recursive source/work snapshots remain unchanged
#### Scenario: Finite carried nested value
- **WHEN** valid metadata contains a finite numeric value
- **THEN** staging succeeds, the exact value is preserved, and json.loads with a rejecting parse_constant callback accepts the manifest

