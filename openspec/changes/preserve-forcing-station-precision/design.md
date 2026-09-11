## Context
Project profile yd-viewer. Bugfix, fixture expanded (geospatial/file-format), repair intensity medium: five formatter expressions, no new filesystem/security/resource algorithm. Upstream suggested level absent. User explicitly requests repr-level geometry precision; approved registry implementation assemble._station_index is the positive reference.
## Goals / Non-Goals
Preserve every legitimate source geometry float through float(text)==value, using shortest-roundtrip representation, and exact source field order/ID/filename/header/line endings. Do not alter time-series data or Time_Day/debug CSV formatting, registry, FileForcingRepository, contract/parser/validator, SHUD parser, canonical or source geometry.
## Decisions
Replace only geometry uses of _format_number at format_shud_forcing_package (Lon/Lat/X/Y/Z) with repr(float(...)); keep _format_number itself and all nongeometry calls unchanged. Retain existing props.get(...)/or0.0/elevation fallback behavior; this is precision, not new signed-zero/default normalization policy. Preserve negative Z at the forcing contract level (registry compatibility clamp is separate).
Add one focused regression to existing producer/tests/test_forcing_helpers_yd.py, reuse its _base_manifest and real parse_direct_grid_forcing_contract and production _met_stations_from_direct_grid_contract adapter, call public format_shud_forcing_package. Use a high-precision station with issue values (116.1234567890123,39.87654321098765,1540123.4567890123,4123456.789012345,3375.123456789012) and a required second simple station with negative Z. Expected COMPLETE stations.tsd.forc bytes must be manually written, not constructed by repr/formatter. Parse all five fields and compare exact floats against originating binding values, not approx. Include a precise forcing value in a per-station CSV and assert its pre-existing .10g bytes, guarding against a global formatter change.
No new source/test files or dependencies. Current producer.py is already4064lines and explicitly excluded by existing .large-file-guard.json; issue#114 owns the later behavior-preserving split per user's order. Keep that existing exact exemption, add no exemption/glob, keep edited yd boundary test under1000lines. Required workflow fixture files are documentation, not runtime/test expansion.
## Risk packs
Selected: PublicAPI (formatter output), Schema/units/fields (five geometry fields exact), Geospatial/CRS (precision only,no CRS change), Legacy compatibility (time-series bytes/fallback values unchanged), Documentation (snapshot registration). NWM provenance selected for five-expression fork.
Not selected: Config/setup, File IO safety/overwrite, Auth/secrets, Concurrency, Resource limits, Error/rollback, Release/dependencies, Time-series behavior, State/warm-start — no new implementation on these surfaces; time-series bytes explicitly preserved as compatibility evidence.
## Evidence
Handwritten full-index bytes+five-field exact roundtrip must fail on old producer and pass fixed; existing forcing_producer and round2 suites green. Parent smoke directly calls formatter with >10digit geometry and confirms source readback+unchanged CSV output. Full profile matrix, no runtime/test file addition, no exemptions, no edits outside precision boundary.
## Risks / Trade-offs
Checksums for station index/package appropriately change when text gains precision (including .0 for integral floats); consumers parse floats, not fixed10digit text. Numeric time-series formatting stays unchanged. No migration of already-generated work packages; this fixes new serialization.

## Fixture-review exact oracle clarification
Authority is inventory §1 producer.py row37 剥离点, not converter row36; #119 registration already exists. Module deviation note references target path/formatter rather than stale row number.
The registry helper is a precision reference ONLY. Producer index retains count/date and shud prefix; do not copy registry envelope or max(z,0) clamp. Existing geometry props.get/or0.0/elevation fallbacks stay verbatim.
Required test: existing helpers_yd _base_manifest -> real parser -> _met_stations_from_direct_grid_contract -> public format_shud_forcing_package. Cycle is UTC2026-05-07T00:00:00. Use TWO stations: first all five high-precision issue values; second index2/idforc_002/filenameX2.csv/grid_cell_id1 with lon100.0,lat30.0,x1.0,y2.0,z-12.345678901234 (negativeZ required). Use PRCP116.1234567890123 at cycle for each station, all other forcing variables absent/default0.
Handwritten full byte literal (write byte-string escapes in the test, never derive with repr/format):
```text
2 20260507
shud
ID	Lon	Lat	X	Y	Z	Filename
1	116.1234567890123	39.87654321098765	1540123.4567890123	4123456.789012345	3375.123456789012	X1.csv
2	100.0	30.0	1.0	2.0	-12.345678901234	X2.csv
```
Literal uses tabs between fields and LF after every row, including last. Each station CSV is exactly `1\t6\t20260507\t20260507\nTime_Day\tPrecip\tTemp\tRH\tWind\tRN\n0\t116.1234568\t0\t0\t0\t0\n`. Parse all five geometry tokens and compare exact floats to corresponding binding values, not approx. _format_number/global formatter must remain unchanged.
