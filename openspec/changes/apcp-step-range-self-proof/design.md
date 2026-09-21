## Change surface
`_rawcopy_metadata.py` (new derivation function next to `_carried_metadata`/`_check_accumulation`), `rawcopy.py` `_build_entries` (one call), test fixtures.

## Must preserve
- Pin form: when `idx_selectors`/`idx_selector` are present, the entry is carried verbatim and no bundle is opened.
- `_check_accumulation` (R4B2) semantics, its messages and kind; `ACCUMULATION_VARIABLES == {"apcp"}`; IFS entries never enter the derivation.
- Zero writes under `raw_root` (agent-ops §4.3, compute-loop §4.1): no `.idx`, no temp files, no cfgrib/xarray open of the original.
- Staging order: `_reject_symlinks` (lstat, every segment) for every source path runs before `_build_entries`; that proves "not a symlink at lstat time" only, so the derivation's own open MUST carry `O_NOFOLLOW` as the syscall-level second gate, exactly as the copy step does (`_rawcopy_common.py:106-110`, `rawcopy.py:411`). No pre/post identity bracket is added: the copy step's `source-mutated` bracket still covers the file that is finally copied.
- Work-root cleanliness on failure: the derivation runs before any copy, so a `RawStagingError` leaves the work root unchanged (existing `snapshot(work_dir) == {}` pattern).
- `ERROR_KINDS` unchanged; the derivation uses `accumulation-metadata` only.

## Must add/change
- `_derive_accumulation_selector(source_path: Path, *, lead: int, variable: str, grib_short_name: str) -> dict[str, str]`:
  - Open the raw original with the same second-gate no-follow open the copy step uses (`rawcopy.py:411`): `with open(os.open(source_path, os.O_RDONLY | O_NOFOLLOW), "rb") as stream:` (`O_NOFOLLOW` from `_rawcopy_common`); never `Path.open()`/`open(path)`; never hand the pathname to a third-party decoder. Any `OSError` on open, including `errno.ELOOP` when the pathname became a symlink after `_reject_symlinks`, → `RawStagingError(f"(lead=…, variable=…) 无法以只读不跟随方式打开 raw 原件 {source_path}：{exc}", "accumulation-metadata")` (the `source-symlink` kind stays reserved for `_reject_symlinks`, `_rawcopy_paths.py:244-246`; the copy step likewise reports its own ELOOP as `copy-failed`). Then loop `handle = eccodes.codes_grib_new_from_file(stream)` until `None`; for each handle read `shortName` and `stepRange` (`codes_get(..., str)`), always `codes_release` in `finally`; keep records whose `shortName == grib_short_name`.
  - Any `eccodes` exception (`CodesInternalError`, `OSError`) → `RawStagingError(f"(lead=…, variable=…) 无法以 eccodes 读取 raw 原件 {source_path}：{exc}", "accumulation-metadata")` chained from the original.
  - Parse `stepRange` as `<int>-<int>`; anything else → reject naming the raw value.
  - Rules: exactly one record and `(start, end) == (0, lead)` → `{"accumulation_type": "cumulative_since_cycle", "step_range": "0-<lead>"}`; exactly one and `start != 0 and end == lead` → `{"accumulation_type": "interval_bucket", "step_range": "<start>-<end>"}`; zero, more than one, or `end != lead` → `RawStagingError("accumulation-metadata")` whose message lists every observed `stepRange` (or states zero records) and the expected lead.
  - Returns the dict; caller stores it under `IDX_SELECTOR_KEY` only (no plural key is invented).
- `_build_entries`: after `metadata = _carried_metadata(...)`: `if variable in ACCUMULATION_VARIABLES and IDX_SELECTORS_KEY not in metadata and IDX_SELECTOR_KEY not in metadata: metadata[IDX_SELECTOR_KEY] = _derive_accumulation_selector(source_path, lead=lead, variable=variable, grib_short_name=metadata["grib_short_name"])`; then `_check_accumulation` as today.
- `eccodes` import: module-level `import eccodes` in `_rawcopy_metadata.py` is acceptable (already a runtime dependency used by the canonical converter); keep it lazy inside the function only if module import cost matters for CLI startup — implementer's call, one line in the deviation report either way.

## Governing invariant
The persisted `idx_selector` of an apcp entry is either carried verbatim from the source manifest or proven from the exact raw-root bundle that will be copied for that (source, cycle, lead), never inferred from policy strings or defaults, and proving it writes nothing under `raw_root`.

## Sibling surfaces
- `canonical/converter.py:677` `_apcp_selector_metadata` consumes the singular `idx_selector` (`accumulation_type` + `step_range`); the derived dict must satisfy it exactly (names `accumulation_type`/`step_range`, not the aliases).
- `_check_accumulation` (R4B2): runs after derivation; its interval-bucket-needs-`step_range` rule is satisfied because the derivation always writes `step_range`.
- `rawscan` symlink/containment gates: unchanged, they run earlier.
- IFS staging path: unaffected (variables not in `ACCUMULATION_VARIABLES`).
- The work manifest writer (`_rawcopy_manifest`/`DownloadManifest.as_dict`): no shape change; the singular key already exists in pin form.

## Seams under test
`rawcopy.stage_raw` end to end through the existing `build_tree` / `staged` helpers with real eccodes-built bundles; direct unit tests of `_derive_accumulation_selector` for the parse rules.

## Required evidence
- GFS manifest `with_idx=False`, apcp bundle one `tp` record `0-3` at lead 3 → staged manifest apcp entry `idx_selector == {"accumulation_type": "cumulative_since_cycle", "step_range": "0-3"}`, no plural key; `snapshot(raw_root)` before == after (zero-write, no `.idx`).
- Same with `3-6` at lead 6 → `interval_bucket`, `3-6`.
- Zero `tp` records (bundle holds only other short names) and two `tp` records (`0-3`, `3-6`) → `RawStagingError` kind `accumulation-metadata`, message lists `0-3` and `3-6` (or states zero), work root untouched.
- Lead 6 with a single `0-3` record → rejected; message names `0-3` and lead 6.
- Malformed `stepRange` (e.g. instantaneous `3`) → rejected.
- A non-GRIB bundle (existing placeholder bytes) for an apcp entry without idx keys → rejected with the eccodes-read message, kind `accumulation-metadata`.
- Symlink swapped in after the lstat gate: monkeypatch `rawcopy._reject_symlinks` to a no-op, replace the apcp bundle in `raw_root` with a symlink to a valid GRIB outside `raw_root` → `RawStagingError` kind `accumulation-metadata` (ELOOP from the no-follow open), the outside file's `stepRange` never reaches the manifest, work root untouched.
- Pin form (`with_idx=True`, default): every existing test unchanged; a recording wrapper or monkeypatched `codes_grib_new_from_file` proves the bundle is not opened.
- Every non-apcp GFS variable and every IFS entry: derivation not invoked.

## Non-goals
IFS `tp`/`ssr`/`str`; converter changes; inference from `source_policy`; reading NWM `.idx` files; caching across entries; any write under `raw_root` or the work root before copy.

## Review focus
1. Zero-write and no-follow proof: `O_NOFOLLOW` on the derivation open (grep the new function for `os.open(` with `O_NOFOLLOW`), the raw tree snapshot byte/inode-stable across a successful derivation; no cfgrib import in the new path.
2. Rule table exactness (`0-<lead>` vs interval, end==lead) and error messages listing observed records.
3. Handle release on every path (exceptions included); file opened once per entry.
4. Pin form untouched, IFS untouched; `ERROR_KINDS` untouched.

## Not yet specified
None within scope.
