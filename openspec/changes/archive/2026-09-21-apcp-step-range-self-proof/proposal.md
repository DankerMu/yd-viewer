## Why
M4 stage-5 first `run` (2026-09-21, receipt `m4-stage5-run1-20260921.md`, #202): gfs stopped in `phase=raw` with `RawStagingError (lead=3, variable='apcp') 缺累积语义子 Mapping idx_selector`. The real NWM manifest (`nhms.gfs.source_policy.v3`, `gfs-idx-selector-v3`) carries no per-entry `idx_selectors`/`idx_selector`; the selection result exists only in the downloaded bundle, and the policy only as the top-level string `source_policy.apcp_selector_policy`. eccodes on node-22 (cycle 2026091412) shows every GFS lead bundle holds exactly one `shortName=tp` record with `stepRange` `0-<lead>`, `stepType=accum`. Docs are merged first (#311, `docs/compute-loop-design.md` §7.2): derive the accumulation semantics from the raw-root original bundle with a zero-write eccodes read, fail closed otherwise.

## What Changes
- `_rawcopy_metadata.py`: new `_derive_accumulation_selector(source_path, *, lead, variable, grib_short_name)` used by `rawcopy._build_entries` right after `_carried_metadata` and before `_check_accumulation`, only when `variable in ACCUMULATION_VARIABLES` and the carried metadata has neither idx key. It opens the raw original read-only, enumerates GRIB records with `eccodes.codes_grib_new_from_file`, keeps those whose `shortName` equals the entry's `grib_short_name`, collects `stepRange`, and writes `carried["idx_selector"]` per the rules; zero/multi/mismatch/unparseable/undecodable → `RawStagingError("accumulation-metadata")`.
- Pin form (idx keys present) unchanged; `_check_accumulation` unchanged and still runs after derivation.
- Tests: rawcopy fixture gains an eccodes-built GRIB bundle helper (`shortName`, `stepType=accum`, `stepRange`) and a `with_idx=False` GFS manifest path; cases for the four rules plus pin-form unchanged and raw-root zero-write.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `raw-scan`: `manifest 语义键承接与 fail-closed` gains the bundle-derived accumulation path.

## Impact
`producer/src/yd_producer/_rawcopy_metadata.py`, `rawcopy.py` (call site only), `producer/tests/rawcopy_fixtures.py`, `test_rawcopy_source_gates.py` (or a new `test_rawcopy_accumulation_derivation.py` if the file would pass the 1000-line guard otherwise). No converter, IFS, NWM, or config change. Runtime dependency: `eccodeslib` already in `producer/pyproject.toml`.

Issue type: bugfix
Fixture level: expanded
Upstream suggested level: expanded (agree: external file-format parse feeding a persisted manifest field)
Blast radius: a wrong derivation mislabels precipitation accumulation for the whole cycle (converter consumes `idx_selector`); a write side effect under `raw_root` violates the NWM read-only hard constraint (agent-ops §4.3).
Selected risk packs: File IO / path safety (read the raw original only, zero writes, no `.idx`); Schema / field names (`idx_selector` shape consumed by the converter); Error handling (fail closed listing observed records, one kind); Legacy compatibility (pin form untouched); Resource limits (sequential handle enumeration with release on every path, one open per entry).
Evidence floor: `cd producer && uv run pytest tests/test_rawcopy_source_gates.py tests/test_rawcopy_manifest_outputs.py -q` plus full `uv run pytest -q` green with the new cases (each behavior case red against pre-change source); `uv run ruff check . && uv run ruff format --check .`; `openspec validate apcp-step-range-self-proof --strict --no-interactive`. Site oracle: node-22 stage-5 re-run stages gfs 2026091412 with `cumulative_since_cycle` / `0-3`… in the work manifest and the raw stat snapshot is unchanged.
