## Why
Issue #99: carried grib_short_name and cfgrib_filter_by_keys.shortName can disagree, yielding contradictory manifest entries.
## What Changes
- Cross-check both carried values in admission and fail source-manifest on mismatch, non-Mapping filters or missing shortName.
- Preserve both original values and unrelated filter keys on equality; no aliases or normalization.
- Update six-key m2 contract and retire its #99 Known-limit.
## Capabilities
### New Capabilities
- `carried-grib-short-name-consistency`: consistent entry-level GRIB filter identities.
### Modified Capabilities
None.
## Impact
rawcopy.py _carried_metadata, test_rawcopy.py, OpenSpec only. No converter, idx-selector, loader, schema fields, configuration or CI changes.
