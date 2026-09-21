## Why
M4 stage-5 first `run` (2026-09-21, receipt `m4-stage5-run1-20260921.md`, #202): job 52756 failed in `forcing.produce` with `DIRECT_GRID_VALIDATION_FAILED grid_signature`. The prepare driver computed the handoff signature from the NWM checkout's `canonical/IFS/grid/ifs_0p25/grid.json` (repo snapshot, latitude ascending, sha `37c147a2…`), while the runtime yd converter writes the canonical grid byte-identical to the object-store `/ghdc/data/nwm/object-store/canonical/IFS/grid/ifs_0p25/grid.json` (latitude descending, sha `be449ef4…`). gfs differs the same way. The object-store grid directory holds only `grid.json`; `grid_snapshot_metadata.json` exists only in the checkout. Docs are already merged (#311): `docs/compute-loop-design.md` §5 (`[nwm].canonical_root` required) and §6.1 step 4 (grid.json from the object-store canonical root, metadata from the checkout; prepare MUST NOT read grid.json from the checkout), `docs/agent-ops.md` §15.2.

## What Changes
- `config.py`: `NwmLocal.canonical_root: str` (required, read by `load_local` as `nwm.canonical_root`; presence/type checked like the other three `[nwm]` keys).
- `nwm.py` `invoke_mapping_builder`: preflight `local.nwm.canonical_root` (non-empty, absolute, existing directory) as `ConfigError(path="nwm.canonical_root")` after the checkout preflight and before any runner call; cwd/PYTHONPATH stay the checkout.
- `prepare.py` `default_builder`: passes `--canonical-root <local.nwm.canonical_root>` to the driver alongside the existing four arguments.
- `_nwm_prepare_driver.py`: required `--canonical-root` (absolute path, else fail); `grid.json` = `<canonical_root>/<SRC>/grid/<grid_id>/grid.json` (`<SRC>` from the existing `_SOURCE_GRID_DIR`), `grid_snapshot_metadata.json` still `<cwd checkout>/canonical/<SRC>/grid/<grid_id>/`; each missing file fails naming its own path; `grid_definition_uri` unchanged (`canonical/<SRC>/grid/<grid_id>/grid.json`).
- Tests: config loader (missing key, pinned key list, fixture closure), invoke preflight (missing / file / relative canonical root → `ConfigError` `nwm.canonical_root`, zero runner calls, `yd_root`/scratch untouched via `run_prepare`), `default_builder` argv carries `--canonical-root`; all test local fixtures gain the key.

## Capabilities
### New Capabilities
None.
### Modified Capabilities
- `cli-config`: `local.toml 现场值不得猜测` gains the NWM canonical root.
- `yd-native-builder`: `File-only grid inputs without platform governance` (grid.json source) and `Minimal fixed interpreter cutover` (canonical root argument and preflight).

## Impact
`producer/src/yd_producer/{config.py,nwm.py,prepare.py,_nwm_prepare_driver.py}`, tests `test_config.py`, `test_nwm.py`, `test_prepare.py`, `cli_fixtures.py`, `init_bootstrap_fixtures.py`, `run_once_fixtures.py` (any other `NwmLocal(...)` / local TOML builder). Site: node-22 `producer/local.toml` gains `canonical_root` (done outside this PR, agent-ops §15.2). No change to init/run, runtime signature computation, NWM, or the object store.

Issue type: bugfix
Fixture level: expanded
Upstream suggested level: expanded (agree: production config key, prepare entrypoint, file paths)
Blast radius: wrong root silently prepares a binding whose grid indices do not match runtime forcing (caught only by the worker's signature check on the first job); missing preflight lets the driver fail late inside the NWM interpreter instead of at config time.
Selected risk packs: Config / project setup; Public API / CLI / script entry (driver argv); File IO / path safety (two roots, absolute-path rule); Error handling (fail closed naming the path); Legacy compatibility (every `NwmLocal`/local TOML fixture and node-22 `local.toml` must gain the key; old files fail loudly).
Evidence floor: `cd producer && uv run pytest -q` green with the new cases (each behavior case red against pre-change source); `uv run ruff check . && uv run ruff format --check .`; `openspec validate prepare-canonical-root-grid --strict --no-interactive`. Driver-internal path selection cannot be unit-tested in the yd venv (see design.md) and is proven by the M4 stage-3 prepare re-run oracle on node-22.
AC adjustment (recorded here, not silently): the issue's "driver fixture with grid.json A under canonical_root and B under the checkout, handoff signature from A" is not runnable as a yd test because `_nwm_prepare_driver.py` imports NWM `packages.*`/`workers.*` at module level and only runs under the NWM interpreter; the equivalent evidence is the node-22 prepare re-run receipt (handoff `grid_signature` must equal the signature derived from the object-store grid.json, and the first job must pass `DIRECT_GRID_VALIDATION`). The missing-file and argv behaviors are covered by yd-side tests at the invoke/prepare boundary.
