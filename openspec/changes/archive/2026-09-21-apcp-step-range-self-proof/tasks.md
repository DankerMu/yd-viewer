## 1. Derivation
- [x] 1.1 `_rawcopy_metadata.py`: `_derive_accumulation_selector(source_path, *, lead, variable, grib_short_name)` per design.md (no-follow read-only open `open(os.open(source_path, os.O_RDONLY | O_NOFOLLOW), "rb")` mirroring `rawcopy.py:411`, `ELOOP`/any open `OSError` → kind `accumulation-metadata` (do not mint `source-symlink` outside `_reject_symlinks`), sequential `eccodes.codes_grib_new_from_file`, `codes_release` in `finally`, filter by `shortName`, collect `stepRange`, parse `<int>-<int>`, rule table, `RawStagingError("accumulation-metadata")` listing observed records; eccodes/OSError chained). Docstring cites compute-loop §7.2 and the 2026-09-21 site evidence (one `tp` per lead, `0-N`).
- [x] 1.2 `rawcopy.py` `_build_entries`: call it only when `variable in ACCUMULATION_VARIABLES` and neither idx key is in the carried metadata; store under `IDX_SELECTOR_KEY`; `_check_accumulation` still follows.
Suggested fixture level: expanded
Minimal mergeable slice: atomic

## 2. Tests
- [x] 2.1 `rawcopy_fixtures.py`: `grib_bundle_bytes(records: Sequence[tuple[str, str]]) -> bytes` building one GRIB2 message per `(short_name, step_range)` from the `regular_ll_sfc_grib2` sample with `stepType="accum"` when the range has two ends (pattern: `test_canonical_db_free.py` ~line 130); `build_tree(..., bundle_bytes_for: Callable[[int], bytes] | None = None)` (default keeps `bundle_bytes`); `source_manifest_payload("gfs", with_idx=False)` already yields no idx keys.
- [x] 2.2 New file `producer/tests/test_rawcopy_accumulation_derivation.py` (keeps `test_rawcopy_source_gates.py` under the 1000-line guard): cumulative `0-3`@3 → staged apcp `idx_selector` exact dict, no `idx_selectors` key, `snapshot(raw_root)` unchanged and no `*.idx` under `raw_root`; interval `3-6`@6; zero `tp` records; two records; end≠lead (`0-3`@6); malformed `stepRange`; placeholder (non-GRIB) bundle → eccodes-read rejection; symlink swapped in after the lstat gate (monkeypatch `rawcopy._reject_symlinks` to no-op, apcp bundle replaced by a symlink to a valid outside GRIB) → kind `accumulation-metadata` (ELOOP) and the outside `stepRange` never lands; pin form default: monkeypatch `eccodes.codes_grib_new_from_file` to raise and assert staging still succeeds and existing outputs unchanged; IFS and non-apcp GFS entries never call the derivation (monkeypatch the function with a recorder).
- [x] 2.3 Existing `test_rawcopy_*` suites unchanged and green.
Suggested fixture level: expanded
Minimal mergeable slice: atomic

## 3. Verification
- [x] 3.1 `cd producer && uv run pytest tests/test_rawcopy_accumulation_derivation.py tests/test_rawcopy_source_gates.py tests/test_rawcopy_manifest_outputs.py -q`; full `uv run pytest -q`; `uv run ruff check . && uv run ruff format --check .`; `uv run --project producer --frozen python scripts/large_file_guard.py` from repo root; `openspec validate apcp-step-range-self-proof --strict --no-interactive`; red→green recorded per behavior case.

## Risk evidence
- File IO / path safety: raw tree snapshot stable across derivation, no `.idx`; the open carries `O_NOFOLLOW` (second gate after `_reject_symlinks`, symlink-swap test); no cfgrib/xarray import in the new path (2.2).
- Schema / field names: derived dict is exactly `accumulation_type` + `step_range`, consumed unchanged by `_check_accumulation` and the converter's `_apcp_selector_metadata` (2.2, sibling surfaces).
- Error handling: one kind `accumulation-metadata`; messages list observed `stepRange`s and the expected lead; work root untouched on every failure (2.2).
- Legacy compatibility: pin form bypasses the derivation entirely, proven by the raising monkeypatch (2.2); IFS untouched.
- Resource limits: one file open per apcp entry, every handle released in `finally` (1.1, review focus 3).
- Not selected: Config, Auth, Concurrency, Release (eccodeslib already required), Documentation (merged in #311).
