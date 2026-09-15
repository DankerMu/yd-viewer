# forcing-station-geometry-precision Specification

## Purpose
TBD - created by archiving change preserve-forcing-station-precision. Update Purpose after archive.
## Requirements
### Requirement: Station-index geometry roundtrips full float precision
format_shud_forcing_package SHALL serialize station-index Lon/Lat/X/Y/Z using shortest-roundtrip float text, retaining existing field selection and producing float-exact values for legitimate contract geometry. Time-series/Time_Day/debug numeric formatting MUST remain unchanged.
#### Scenario: High-precision contract geometry
- **WHEN** a contract station has more than ten significant digits in each of Lon/Lat/X/Y/Z
- **THEN** complete stations.tsd.forc bytes match an independent handwritten repr-level literal and reading the five fields as float returns the original binding values exactly
#### Scenario: Timeseries and station-layout compatibility
- **WHEN** full-precision geometry is emitted with a high-precision forcing value
- **THEN** station ID/filename/header/order remain unchanged and per-station forcing CSV retains the existing .10g numeric text

