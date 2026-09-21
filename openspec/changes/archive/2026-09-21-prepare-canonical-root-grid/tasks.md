## 1. Config key
- [x] 1.1 `config.py`: `NwmLocal.canonical_root: str` (docstring: object-store canonical root, prepare-only grid.json authority, compute-loop §5); `_build_nwm` reads `_require_str(table, "canonical_root", "nwm")`.
- [x] 1.2 `test_config.py`: `VALID_LOCAL["nwm"]["canonical_root"]` and `PINNED_LOCAL_KEYS` gain `nwm.canonical_root` (order after `nwm.raw_root` or wherever the dataclass field order puts it so the derived `LOCAL_LOADER_REQUIRED_KEYS` still equals the pinned list); the existing missing-key parametrization then covers the new key; update the explicit `NwmLocal(...)` constructions in that file.
Suggested fixture level: expanded
Minimal mergeable slice: atomic

## 2. Invoke preflight and argv
- [x] 2.1 `nwm.py`: `_CANONICAL_FIELD = "nwm.canonical_root"`; in `invoke_mapping_builder`, after the three checkout checks and before the driver-script check: empty/relative → `ConfigError(f"NWM canonical root 必须是绝对目录：{value}", _CANONICAL_FIELD)`; not exists → `不存在`; not dir → `不是目录`. cwd/PYTHONPATH unchanged.
- [x] 2.2 `prepare.py` `default_builder`: append `"--canonical-root", local.nwm.canonical_root` to the driver args.
- [x] 2.3 `test_nwm.py`: extend the existing failure parametrization (`missing-checkout`, `file-checkout`, …) with `missing-canonical`, `file-canonical`, `relative-canonical` → `ConfigError.path == "nwm.canonical_root"` and zero runner calls; the success path still records `argv == [script, driver, *args]` and cwd/PYTHONPATH == checkout; `_load`/fixture helpers create a canonical root directory by default.
- [x] 2.4 `test_prepare.py`: `test_default_builder_invokes_packaged_driver_with_bound_local` asserts `--canonical-root` is immediately followed by `env.local.nwm.canonical_root`; `test_run_prepare_preserves_checkout_config_error` parametrized over field ∈ {checkout_root, canonical_root} × case ∈ {missing, non-directory} asserting the matching `ConfigError.path`, zero runner calls, `yd_root`/scratch trees unchanged. Setup: `write_local` only writes TOML text (no `mkdir`), so for the `canonical_root` cases first override `python` with a real fake interpreter and `checkout_root` with an existing directory via `replace(env.local, nwm=replace(...))`, then set `canonical_root` to the missing/non-directory path; otherwise the case would stop at the earlier `nwm.checkout_root` check and test the wrong field.
- [x] 2.5 Update every local fixture builder that renders `[nwm]` or constructs `NwmLocal`: `cli_fixtures.py` (TOML template + builder default `root / "nwm" / "canonical"`), `init_bootstrap_fixtures.py`, `run_once_fixtures.py`, and any other hit of `grep -rn "checkout_root" producer/tests`; create the directory where the fixture creates the checkout directory.
Suggested fixture level: expanded
Minimal mergeable slice: atomic

## 3. Driver
- [x] 3.1 `_nwm_prepare_driver.py`: `_parse_args` adds `--canonical-root` (required, `type=pathlib.Path`); `main` passes it; `build_variant(..., canonical_root: pathlib.Path)` fails via `_fail` when not absolute; `_grid_paths(canonical_root, checkout, source, grid_id)` returns grid.json under `canonical_root / physical / "grid" / grid_id`, metadata under `checkout / "canonical" / physical / "grid" / grid_id`, uri unchanged; `_require_file` labels `canonical_root grid.json` and `checkout grid_snapshot_metadata.json`. Module docstring/comment: grid.json authority is the object-store canonical root (compute-loop §6.1 step 4).
- [x] 3.2 No yd-venv test for 3.1 (NWM imports); evidence is the node-22 stage-3 prepare re-run receipt (#202): handoff `grid_signature` per source equals the object-store-derived value and the first run job passes `DIRECT_GRID_VALIDATION`. Record this in the PR body.
Suggested fixture level: expanded
Minimal mergeable slice: atomic

## 4. Verification
- [x] 4.1 `cd producer && uv run pytest tests/test_config.py tests/test_nwm.py tests/test_prepare.py -q`, then `uv run pytest -q`; `uv run ruff check . && uv run ruff format --check .`; `openspec validate prepare-canonical-root-grid --strict --no-interactive`; red→green recorded for the missing-key, preflight, and argv cases.

## Risk evidence
- Config / project setup: missing key → `ConfigError` naming `nwm.canonical_root` (1.2); no default value anywhere (grep `canonical_root` in `config.py` shows only the required read).
- Public API / CLI / script entry: driver argv gains one required flag passed only by `default_builder` (2.2, 2.4); `invoke_mapping_builder` signature unchanged.
- File IO / path safety: two roots, absolute-only, existence/dir checks before any subprocess (2.1, 2.3); driver rejects a relative canonical root (3.1).
- Error handling: each missing file named with its root label (3.1); preflight zero-runner (2.3, 2.4).
- Legacy compatibility: every fixture updated (2.5); an old `local.toml` fails loudly at load, which is the intended behavior (docs §5: four keys required).
- Not selected: Concurrency, Resource limits, Auth, Release, Schema (handoff schema unchanged), Documentation (already merged).
