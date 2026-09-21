## 1. Explicit nullable discharge
- [ ] 1.1 Publish docs/products-contract and docs/design policy clarification before source edits.
- [ ] 1.2 Implement nullable row/column and truthful API/TS schemas, chart gaps/tooltip without zero coercion.
- [ ] 1.3 Prove accessor redgreen, HTTP contracts and actual browser gap behavior; run preservation gates.
Suggested fixture level: expanded
Minimal mergeable slice: atomic - one nullable value contract across producer-independent reader/API/frontend.

## Risk evidence
- Selected API/schema/units: direct row/column mixed finite and NaN/+Inf/-Inf→float/None preserving sorted IDs, all168positions and /86400. Direct tests MUST red before code; existing HTTP null serialization alone is not discriminating.
- Selected fallback/errors: GFS bad discharge + goodIFS → map200 GFS null, not IFS; curves200 retains both sources and gaps. Entire source all-null still kept, all-null sole source200, no404. Minute NaN still DatError and existing fallback.
- Selected public schemas: map/curve HTTP response nullable types/OpenAPI semantically accurate, TS nullable arrays; finite values unchanged, no bare NaN/Infinity tokens. Test actual boundary, not source-text or wiring assertions.
- Selected frontend integration: real temporary backend DAT with visible finite values and null interval, actual Vite browser map neutral color; click river chart has GFS/IFS,168positions and no line bridging/no zero-fill; tooltip at missing point must not report0. Use throwaway browser proof, no permanent DOMsuite.
- Selected fileIO: no new write paths or whole-file validation; existing structure/minute/header tests stay passing.
- Selected docs/legacy: products-contract5 and design6/7 updated first; SNAPSHOT description if adapted chart changed, no compatibility shim.
- Not selected config/dependency/auth/concurrency/resource: no new dependencies/config/threads/cache; conversion uses existing slice allocations only.
- Parent gates: viewer uv run python -m pytest; ruff check/format; frontend frozeninstall/typecheck/test/build; strictOpenSpec. Agents skip formatter/linter/fullsuite/build.
