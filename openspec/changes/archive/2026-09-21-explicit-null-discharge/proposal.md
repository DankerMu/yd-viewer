## Why
User chose explicit per-point null for NaN/+Inf/-Inf (issue290 comment5754798560). Current framework happens to emit null but raw accessors/types do not declare it; eliminate accidental serializer-dependent semantics.
## What Changes
- Reader accessors return finite converted values or None, retaining every source and timestep.
- Public API/TS values are nullable; map uses missing color, charts leave gaps without zero-fill or bridging.
- Docs contract first, regression tests and real local HTTP/UI evidence.
## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `viewer-dat-reader`: nullable discharge accessors.
- `viewer-api`: explicit nullable map/curve values without source rejection.
- `viewer-frontend`: nullable types and missing-point chart behavior.
## Impact
viewer dat/app, API/reader tests, frontend api/chart and consumer types, docs/products-contract.md and docs/design.md. No producer/lockfile changes.
Issue type: bugfix
Fixture level: expanded
Upstream suggested level: absent; API/schema/shared reader triggers expanded.
Blast radius: all discharge consumers.
Selected risks: public API, schema/units, reader/fileIO preservation, errors/fallback, docs, frontend integration.
Evidence floor: accessor redgreen, HTTP map/curve null/no-fallback invariants, real browser gaps, existing backend/frontend gates.
