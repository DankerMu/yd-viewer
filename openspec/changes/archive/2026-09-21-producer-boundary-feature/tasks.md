## 1. Boundary handoff
- [x] 1.1 Commit/push documentation clarification before source, retaining historical archive context.
- [x] 1.2 Return single boundary Feature and migrate all affected geometry/prepare consumer tests.
- [x] 1.3 Prove regression red→green, real writer→viewer handoff, preservation suites and strict spec validation.

Suggested fixture level: expanded
Minimal mergeable slice: atomic - schema cutover and consumer tests together.

## Risk evidence
- Selected public API/schema: boundary builder Polygon and MultiPolygon produce Feature/empty properties; real writer rivers remains FeatureCollection. Red tests before fix; all affected tests migrated.
- Selected fileIO/rollback: run existing writer fault and prepare refusal tests; no changes to atomic write/commit/cleanup behavior.
- Selected geospatial: existing union-before-reprojection, holes/orientation/nonfinite tests stay passing; do not delete behavioral assertions while migrating JSON access paths.
- Selected legacy/docs: explicit canonical single-Feature contract, historical supersession note if needed; no aliases/dual-format loader or existing data mutation.
- Selected integration: synthetic shapefiles→actual write_viewer_geojson→temporary JSON files→actual viewer load_geometry yields exact reach ID set. Before fix loader rejects boundary, after fix succeeds. No mocked writer/loader.
- Not selected config/dependency/auth/resource/concurrency: no new knobs/dependencies/permissions/limits/parallel writers; existing failure and staging behavior unchanged.
- Evidence commands: producer uv run python -m pytest; uv run ruff check .; uv run ruff format --check .; viewer uv run python -m pytest; strict OpenSpec validation. Parent runs project-wide commands once after implementation; agents skip format/lint/fullsuite.
- No real prepare CLI/oracle claim: mapping builder/default driver not modified; smoke exercises actual geometry writer and loader only, no remote operations/M4/M5 proof.
