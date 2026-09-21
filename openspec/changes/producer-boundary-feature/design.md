## Context
Issue271: build_boundary_geojson wraps its already-built Feature in FeatureCollection. Writer serializes unchanged; viewer geometry loader and MapPage reject it.

## Goals / Non-Goals
Goal: producer writes exactly the shapes already required by products-contract §6.
Non-goals: no viewer tolerance/shims, GIS algorithm changes, overwrite flags, remote deployment or existing YD_ROOT mutation.

## Decisions
Return existing Feature directly; retain writer serialization and all error/rollback paths.
Migrate every boundary shape consumer including test_prepare.py, not only geometry tests.
Canonical prepare-variants spec explicitly names shapes; archived M2 wording is historical and may receive only a clear supersession note, not a rewritten claim about history.
No new cross-project production dependency: permanent producer tests assert public JSON contract; a throwaway subprocess handoff runs real producer writer then real viewer loader in their own uv environments.

## Invariants and sibling surfaces
Rivers=FeatureCollection, boundary=Feature with empty properties and Polygon/MultiPolygon; builder→writer→prepare→viewer all agree.
Preserve source-CRS union before reprojection, holes, ring orientation, finite coordinates, EPSG4326, reach identity, filenames, atomic temporary writes, failure cleanup and prepare refusal to overwrite.
Sibling consumers: producer geometry tests, prepare tests, backend load_geometry, frontend boundary parser, current product contract.

## Sketch seams under test
Builder geometry regressions, real file writer JSON, prepare published outputs; real writer output consumed by viewer load_geometry.

## Risks / Trade-offs
Old generated files remain old; regenerate only via existing authorized clean-staging process, no in-place conversion.
Independent uv projects require two subprocesses for smoke, not dependency changes.

## Migration Plan
Docs clarification committed/pushed before source edits. New prepare generates compliant files. No remote state claims.

## Not yet specified
None for this issue; issue290 policy is a separate change.
