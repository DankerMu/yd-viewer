## Why
Producer wraps the merged boundary in FeatureCollection, violating products-contract §6 and both viewer consumers. Fix before real prepare; existing published files are not auto-migrated.

## What Changes
- **BREAKING** boundary builder/writer output becomes single Feature; rivers stays FeatureCollection.
- Migrate geometry and prepare consumer tests; prove real writer output passes viewer loader.
- Clarify producer canonical shape contract, preserving GIS and atomic publication behavior.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `prepare-variants`: explicit top-level shapes in viewer GeoJSON generation.

## Impact
producer geometry.py, geometry/prepare tests, current producer docs/spec; no viewer behavior/dependencies or remote actions.

Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent; schema/writer/GeoJSON triggers expanded.
Blast radius: prepare output and viewer startup.
Selected risk packs: public API, schema, file IO, rollback, legacy/migration, documentation; CRS preservation.
Evidence floor: red-green producer shape regression, real writer-to-viewer smoke, producer suite/ruff and viewer suite, strict OpenSpec.
