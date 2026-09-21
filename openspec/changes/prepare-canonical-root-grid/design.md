## Change surface
`config.py` (`NwmLocal`, `_build_nwm`), `nwm.py` (`invoke_mapping_builder` preflight), `prepare.py` (`default_builder` argv), `_nwm_prepare_driver.py` (`_parse_args`, `build_variant`, `_grid_paths`).

## Must preserve
- `invoke_mapping_builder` argv shape `[interpreter, driver_script, *args]`; cwd and `PYTHONPATH` = `checkout_root`; `DATABASE_URL`/`PYTHONHOME`/inherited `PYTHONPATH` dropped; interpreter and checkout preflight errors and their `ConfigError.path` values unchanged.
- `grid_definition_uri` string `canonical/<SRC>/grid/<grid_id>/grid.json` and the lowercase yd `canonical/ifs/...` daily object key (lowercase-ifs-grid-identity spec).
- `grid_snapshot_metadata.json` still read from the checkout (object store has no metadata).
- `LocalConfig.slurm`/timeout semantics; `yd_root` resolution; every other `[nwm]` key.
- The handoff schema and the fourteen-file output contract of the driver.

## Must add/change
- `NwmLocal.canonical_root: str` (kw-only, no default, like the other three).
- Preflight in `invoke_mapping_builder`: empty or relative → `ConfigError("NWM canonical root 必须是绝对目录：…", "nwm.canonical_root")`; missing → `不存在`; non-directory → `不是目录`; ordered after the checkout checks, before the driver-script check or the runner call (zero runner calls on failure).
- `default_builder` args gain `"--canonical-root", local.nwm.canonical_root` after `--output`.
- Driver: `--canonical-root` required (`type=pathlib.Path`); `build_variant(..., canonical_root=)` fails with `_fail` when not absolute; `_grid_paths(canonical_root, checkout, source, grid_id)` → (`canonical_root/<SRC>/grid/<grid_id>/grid.json`, `checkout/canonical/<SRC>/grid/<grid_id>/grid_snapshot_metadata.json`, uri); `_require_file` labels `canonical_root grid.json` and `checkout grid_snapshot_metadata.json` so the error names which root is missing what.

## Governing invariant
The grid.json that defines the prepared binding is byte-identical to the grid the runtime converter writes, so the worker's `grid_signature` check passes on the first job; prepare never reads grid.json from the checkout.

## Sibling surfaces
- `prepare.run_prepare` (calls `default_builder`; must surface the canonical-root `ConfigError` before any filesystem change, same as the checkout case).
- `controller.py:741` `_preflight` (run-time absolute-path gate over run-consumed fields): no change. `canonical_root` is prepare-only, exactly like `checkout_root`/`python`, which are deliberately absent from that tuple.
- Test fixtures constructing `NwmLocal` or rendering `[nwm]` TOML: `cli_fixtures.py`, `init_bootstrap_fixtures.py`, `run_once_fixtures.py`, `test_config.py` (`VALID_LOCAL`, `PINNED_LOCAL_KEYS`), any `prepare_fixtures.py`/`controller_sources_fixtures.py` builder.
- Node-22 `producer/local.toml` (outside the PR; agent-ops §15.2 already lists the value).
- none for init/run: they never touch grid.json.

## Seams under test
`load_local` (config), `invoke_mapping_builder` with a recording runner (nwm), `default_builder` with a fake invoke (prepare), `run_prepare` with a forbidden runner (prepare, filesystem untouched).

## Required evidence
- `local.toml` without `[nwm].canonical_root` → `ConfigError(path="nwm.canonical_root")`, loader keeps the other three keys' behavior; `PINNED_LOCAL_KEYS` and `VALID_LOCAL` closures agree.
- `invoke_mapping_builder` with canonical root missing / regular file / relative → `ConfigError` path `nwm.canonical_root`, zero runner calls; with a valid directory → argv unchanged in shape, cwd/PYTHONPATH still the checkout.
- `default_builder` → recorded args contain `--canonical-root` followed by `local.nwm.canonical_root`.
- `run_prepare` with an invalid canonical root → the same `ConfigError`, `yd_root` and scratch trees unchanged (parametrize the existing checkout preservation test).
- Driver: no yd-venv test (NWM imports). Site oracle: node-22 stage-3 `prepare` re-run receipt records handoff `grid_signature` for both sources equal to the object-store-derived value and the first `run` job passes `DIRECT_GRID_VALIDATION` (#202).

## Non-goals
No metadata in the object store, no NWM change, no runtime signature change, no init/run change, no fallback from canonical root to checkout, no `[nwm]` default values.

## Review focus
1. Preflight ordering and zero-runner guarantee; `ConfigError.path` exact string.
2. Driver reads grid.json only from the canonical root and metadata only from the checkout; error labels name the root; uri unchanged.
3. Every `NwmLocal`/TOML fixture updated so the suite fails loudly, not by fixture drift.
4. Spec deltas faithful to the merged docs wording.

## Not yet specified
None within scope.
