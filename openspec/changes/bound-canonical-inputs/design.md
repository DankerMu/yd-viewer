## Context
Project profile: yd-viewer. Issue type bugfix. Upstream suggested level/minimal mergeable slice absent. Fixture expanded, repair intensity high due to shared file-reading/representation boundary; this is robustness for semi-trusted inputs, not a claimed hostile-input vulnerability. Normal deployment bbox 329x225=74025 points, about 19MB per IFS hour as Python tuples, not global-grid estimates.
## Goals / Non-Goals
Bound two JSON references and raw decode input; avoid retaining all raw grids as Python float tuples. Preserve GFS/IFS products, catalog JSON, metadata, algorithms, error class, #103 no-follow and unmapped skips.
Non-goals: vectorizing numerical algorithms or changing their tuple return contract, converting coordinate axes (issue names values at former line1502), decoded compressed-data bombs, #104 URI, #105 pin oracles, #127 sibling opener, dead _read_records, shared store/safe_fs repairs, transaction rollback for later failures.
## Decisions
Import the single MAX_OBJECT_MANIFEST_BYTES (16MiB) from object_store and use read_bytes_limited at load_manifest and existing grid-definition read; failures remain CanonicalConversionError. Do not duplicate a JSON limit or add parsing policy.
Add versioned MAX_RAW_INPUT_BYTES=512*1024*1024, matching the existing 512MiB canonical NetCDF input convention without importing forcing or introducing config defaults. This conservative upper bound is not a memory-safety guarantee for compressed grids. stat via object_store.size (no-follow) before staging/decoder; reject size > limit with key, observed size and input-size message. During #103 iter_bytes staging count observed bytes and reject growth beyond the same limit before writing the overflow chunk or calling either decoder. Exact limit accepted. No bare-path stat or alternate opener.
RawRecord.values from decoder is np.asarray(data_array.values,dtype=np.float64).ravel(); retain NumPy allocation after dataset close, no .tolist()/Python-float tuple at this seam. Existing externally constructed tuple RawRecord inputs and numeric function return types remain supported; update input annotations/callers only as necessary. Coordinates remain unchanged to avoid JSON scalar/array-truthiness drift. Existing numeric operations keep exact original rounding/order; no generic vectorization.
## Invariant Matrix
Governing invariant: neither JSON reader accepts bytes above 16MiB, neither decoder sees raw bytes above 512MiB, raw grids remain NumPy while persisted accepted outputs remain byte-identical.
Source of truth: imported store manifest constant; raw constant; normalized key and actual stream; original converter outputs.
- Producers: rawcopy/adapter unchanged, semi-trusted raw/manifest bytes.
- Validators/preflight: load_manifest, _ensure_grid_definition, raw staging size and stream count.
- Storage/cache: store.size/read_bytes_limited/iter_bytes unchanged no-follow; existing private temp cleanup.
- Public entrypoints: GFS/IFS convert_manifest_uri/convert_manifest, all share raw reader.
- Downstream: RawRecord.values numeric consumers, writer/grid/catalog; forcing unchanged.
- Error/cleanup: over-limit CanonicalConversionError before decoder, temporary staging removed; no new atomic whole-cycle rollback.
- Evidence: inventory/module ledger, issue13 Known limits, old-vs-new bytes/catalog, tests.
Regression rows: manifest/grid exactly limit valid -> accept; limit+1 valid JSON -> CanonicalConversionError via bounded read, not parser accident; raw exactly limit -> decode; raw over limit -> size-specific failure before either decoder; stream growth after stat -> same failure+cleanup; raw records -> NumPy float64 values, correct shape/order after close; GFS realGRIB+NetCDF and IFS NetCDF -> product+catalog bytes unchanged; #103 symlink matrix -> unchanged rejection/no writes.
Boundary checklist: both JSON readers, raw stat and streaming boundary, same no-follow source, mapped-entry compatibility, NumPy input consumer closure, no serialized scalar/type changes, owned staging cleanup.
## Risks / Trade-offs
512MiB raw cap is a conservative policy using existing local precedent, test-injectable by module constant monkeypatch; larger legitimate raw now fails clearly. NumPy float64 may copy float32 raw once but avoids Python object amplification. Algorithms may still make temporary tuples; this issue removes retained raw tuple amplification, not all numerical allocation.

## Phase 2 evidence-driven test adaptation
The sole failing pin test uses a FakeValues supporting only ravel().tolist(), not a real NumPy input. Production must not special-case that fake. Replace its plumbing-only engine/kwargs echo oracle with a yd-owned real two-message GRIB bundle selection regression, distinct values per variable and no fallback. This is an explicitly registered test adaptation, not relaxed numeric/filter expectations. The earlier non-goal of untouched pin tests is superseded only for this one named test; all other pin tests remain unchanged.
