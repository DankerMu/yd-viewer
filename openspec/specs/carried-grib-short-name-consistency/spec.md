# carried-grib-short-name-consistency Specification

## Purpose
TBD - created by archiving change validate-carried-grib-short-name. Update Purpose after archive.
## Requirements
### Requirement: Cross-check carried GRIB identities before staging
stage_raw MUST require cfgrib_filter_by_keys to be a Mapping containing shortName, and that value MUST equal the same entry's grib_short_name. Failure MUST raise RawStagingError(kind=source-manifest) before writes. Mismatch diagnostics MUST identify lead, variable and both actual values. Accepted values and unrelated metadata MUST be carried verbatim, without alias inference or normalization.
#### Scenario: Contradictory names
- **WHEN** apcp filter shortName is 2t-WRONG while grib_short_name retains its original value, or the opposite side alone is changed
- **THEN** staging refuses with the specified typed diagnostic and unchanged recursive raw/work snapshots
#### Scenario: Undefined filter shape
- **WHEN** the filter is not a Mapping or lacks shortName, including when the peer grib_short_name is null
- **THEN** staging raises source-manifest without leaking AttributeError/TypeError and without writes
#### Scenario: Equal custom identities
- **WHEN** both names equal a non-normalized custom source value and extra filter keys are present
- **THEN** staging succeeds and preserves both names and extra values exactly

