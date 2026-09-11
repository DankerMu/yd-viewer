## Why
Issue #119: format_shud_forcing_package truncates valid station Lon/Lat/X/Y/Z through .10g while registry geometry already uses shortest-roundtrip float text. SHUD therefore receives geometry differing from the source contract.
## What Changes
- Only five geometry fields in shud/stations.tsd.forc use repr(float(value)); preserve existing value selection/coercion/defaults.
- Add full handwritten index-byte and float-exact readback oracle in an existing yd boundary-test file, plus unchanged time-series CSV proof.
## Capabilities
### New Capabilities
- `forcing-station-geometry-precision`: float-exact station-index geometry serialization.
### Modified Capabilities
None.
## Impact
forcing/producer.py and existing test_forcing_helpers_yd.py only for code/tests; required OpenSpec/inventory paperwork. No new runtime/test files, helpers/dependencies or large-file exemptions.
