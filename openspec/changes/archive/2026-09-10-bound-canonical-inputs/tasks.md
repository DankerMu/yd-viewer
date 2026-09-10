## 1. Risk packs and changes
- Public API / CLI / script entry: selected — shared GFS/IFS manifest/conversion boundaries; unchanged outputs.
- Config / project setup: not selected — versioned constants, no environment/config surface.
- File IO / path safety / overwrite: selected — retain #103 no-follow; bound staging/read before decoder and clean temporary files.
- Schema / columns / units / field names: selected — NumPy internal values, persisted shape/JSON/units unchanged; byte oracle.
- Auth / permissions / secrets: not selected — no identity surface.
- Concurrency / shared state / ordering: selected — stat-then-grow cannot bypass actual stream cap; no new transaction semantics.
- Resource limits / large input / discovery: selected — exact/over JSON and raw, flat NumPy retained grids.
- Legacy compatibility / examples: selected — tuple-created RawRecord fixtures and numeric return API preserved; product/catalog bytes.
- Error handling / rollback / partial outputs: selected — CanonicalConversionError names raw input size; decoder not invoked on oversize, own staging removed.
- Release / packaging / dependency compatibility: not selected — NumPy already dependency; no lock change.
- Documentation / migration notes: selected — inventory/module header and issue13 Known limits updated.
- Geospatial / CRS / shapefile sidecars: not selected — coordinate arrays unchanged.
- Time series / forcing / temporal boundaries: selected — unchanged math/quality/lineage proven by existing suite and bytes.
- 状态链 / warm-start 定戳一致性: not selected — no state operations.
- NWM 快照溯源与 DB-free 隔离: selected — fork register and existing guards.
- [x] 1.1 Register each #102 fork in inventory before source; update converter header and issue13 Known limits when implemented.
- [x] 1.2 Replace both JSON read sites with existing bounded store API/constant.
- [x] 1.3 Add raw stat bound and observed-byte staging cap, exact-limit acceptance, size-specific error before decoders.
- [x] 1.4 Keep RawRecord decoded values NumPy float64; inspect/update all consumers without numerical/output drift; do not edit dead _read_records logic or pin tests.
- [x] 1.5 Add new yd-owned test_canonical_input_bounds.py: tiny injected limits, valid JSON exact/+1, actual raw exact/+1, stat-growth, cleanup, NumPy retained representation and observable values after close. No source text tests or huge allocations.
## 2. Verification and delivery
- [x] 2.1 Parent runs new oversize tests against old converter: genuine red rejection failures, then focused green; no stash or binding ambiguity.
- [x] 2.2 Original-vs-final smoke GFS real GRIB/NetCDF + IFS NetCDF compares all products and catalog JSON bytes; existing canonical/symlink suites remain green.
- [x] 2.3 Parent runs profile serial producer/viewer pytest, ruff, OpenSpec and stage log checks; reviewer fixture approval+strict validation before implementation.
- [x] 2.4 Cross-review/verdicts/final review/CI/preauthorized merge, archive and loop log tracked in PR evidence.

## 3. Phase 2 oracle adaptation
- [x] 3.1 Replace pin test_bundle_entries_open_cfgrib_with_entry_specific_filter (fake values incompatible with NumPy, only wiring assertions) with a real multi-message GRIB selection test in the yd-owned module, distinct same-key variable values and no fallback; inventory explicitly registers this one exception. No production fake compatibility path.
