## Context
Project profile: yd-viewer. Issue type: bugfix. Upstream suggested level/minimal mergeable slice: absent. Fixture level: expanded; repair intensity/effective tier: high (path safety). This is the first issue in the user's serial batch.
## Goals / Non-Goals
Preserve all valid-input values, metadata, NetCDF bytes, catalog and real GRIB decoding. Reject symlink leaf/ancestors before decoding or output.
Non-goals: #102 resource bounds/NumPy, #104 URI spelling, #71 rawcopy writes, safe_fs defects, #127 canonical NetCDF opener retries, CLI wiring, numerical revalidation.
## Decisions
Use existing `open_file_no_follow(..., containment_root=store.root)` plus the existing descriptor-alias convention, keeping the descriptor live until xarray has materialized and closed data. Alternatively use existing store streaming into a private managed temporary file if backend portability requires it; never feed the original raw path to third-party openers. Reuse the existing descriptor helper rather than introducing another alias chooser. No lstat-then-reopen implementation. Avoid changing shared store semantics. Preserve cfgrib backend filters, no-index setting and fallback error context.
Upstream seam: `convert_manifest` for rejection/no products/catalog and valid conversions; private reader is supplemental only. Document missing upstream seams: none.
## Invariant Matrix
Governing invariant: bytes decoded into canonical products must be obtained through the store no-follow containment boundary, never from a followed raw link.
Source of truth: store root + normalized raw key + descriptor identity.
- Producers: rawcopy raw subtree, unchanged; no assumption that store writes created it.
- Validators/read surfaces: CanonicalConverter._read_record_with_xarray, both engines; existing key normalization unchanged.
- Storage/cache/query: LocalObjectStore and safe_fs unchanged; descriptor alias helper reused if appropriate, cfgrib disk index remains disabled.
- Public entrypoint: GFS/IFS convert_manifest, no CLI changes.
- Downstream: canonical products/catalog and forcing consumers unchanged.
- Failure/cleanup: reject before products/catalog; close dataset then descriptor, clean only own temporary staging if used.
- Evidence: inventory converter row and module-header deviation, tests, issue #71 historical correction.
Regression rows: valid NetCDF/real GRIB -> existing values and byte-identical products; BOTH GFS and IFS convert_manifest, with a pre-existing outside leaf/raw/source/cycle link including a late raw entry -> CanonicalConversionError and zero writes under canonical/ (products, catalog and grid definition); backend failure -> close resources/no raw mutation; unchanged forcing consumer -> suite compatibility. Preflight every raw entry using existing no-follow store access before either conversion loop writes; actual decoding still uses no-follow descriptor-bound access. This does not add whole-cycle atomic rollback for a concurrent mutation after preflight.
Boundary checklist: normalized key; store root and every raw segment; fd lifetime through both engines; no raw-path fallback; publication only after accepted raw records; sibling canonical opener unchanged.
## Risks / Trade-offs
Backend descriptor aliases can differ on Darwin; prove real GRIB and NetCDF locally, never treat a fake opener as sufficient. Private staging trades disk I/O for portability if necessary; resource policy belongs to #102. Scope does not promise concurrent in-place writer isolation beyond existing no-follow primitives.
## Migration Plan
Library-only fix, no data migration. Roll back this PR if necessary, explicitly restoring the known unsafe consumer behavior until repaired.
