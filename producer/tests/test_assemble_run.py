"""Requirement-driven tests for SHUD run-directory assembly."""

from __future__ import annotations

import dataclasses
import inspect
import os
import stat
from pathlib import Path

import pytest
import run_once_fixtures as fixtures
from assembly_fixtures import (
    BINDING,
    PARAMETER_EXPECTED,
    SP_ATT,
    contract,
    forcing_package_key,
    identity,
    prepared,
    run_assemble,
    sources,
    staged_fixture,
    work_dir,
    write_forcing_package,
    write_state,
    write_variant,
)

from yd_producer._work_claim import claim_exact_work
from yd_producer.assemble import AssemblyError, assemble, stage_work_registry
from yd_producer.staged_inputs import (
    STAGED_INPUT_DIRNAME,
    STAGED_INPUTS_MANIFEST_FILENAME,
    STAGED_INPUTS_SCHEMA,
    STAGED_STATES_DIRNAME,
    STAGED_VARIANT_DIRNAME,
    StagedWorkInputs,
    StagedWorkInputsError,
    load_staged_work_inputs,
    stage_work_inputs,
)
from yd_producer.state import MAX_STATE_IC_BYTES
from yd_producer.store import safe_fs
from yd_producer.store.object_store import MAX_OBJECT_MANIFEST_BYTES


def _inputs(tmp_path: Path):
    return prepared(tmp_path)


STAGED_FIELDS = [
    "source",
    "cycle",
    "work_dir",
    "variant_dir",
    "state_path",
    "manifest_path",
    "work_identity",
    "manifest_checksum",
    "file_checksums",
    "prepared",
    "project_name",
    "grid_id",
    "max_manifest_bytes",
    "max_asset_bytes",
    "max_state_bytes",
]
STAGE_PARAMETERS = [
    "claim",
    "source_variant_dir",
    "source_state_path",
    "source",
    "cycle",
    "project_name",
    "grid_id",
    "max_manifest_bytes",
    "max_asset_bytes",
    "max_state_bytes",
]
LOAD_PARAMETERS = [
    "work_dir",
    "source",
    "cycle",
    "project_name",
    "grid_id",
    "max_manifest_bytes",
    "max_asset_bytes",
    "max_state_bytes",
]


def _assert_required_signature(callable_: object, names: list[str]) -> None:
    parameters = inspect.signature(callable_).parameters
    assert list(parameters) == names
    for parameter in parameters.values():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


def test_staged_inputs_public_api_shape_and_freeze(tmp_path: Path) -> None:
    assert STAGED_INPUTS_SCHEMA == "yd.run.staged-inputs.v2"
    assert STAGED_INPUT_DIRNAME == "input"
    assert STAGED_VARIANT_DIRNAME == "variant"
    assert STAGED_STATES_DIRNAME == "states"
    assert STAGED_INPUTS_MANIFEST_FILENAME == "yd.staged-inputs.json"
    _assert_required_signature(StagedWorkInputs, STAGED_FIELDS)
    _assert_required_signature(stage_work_inputs, STAGE_PARAMETERS)
    _assert_required_signature(load_staged_work_inputs, LOAD_PARAMETERS)
    names = [field.name for field in dataclasses.fields(StagedWorkInputs)]
    assert names == STAGED_FIELDS
    assert StagedWorkInputs.__dataclass_params__.frozen
    _, _, staged = staged_fixture(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        staged.source = "ifs"
    values = {field: getattr(staged, field) for field in STAGED_FIELDS}
    invalid_values = {
        "source": 1,
        "project_name": None,
        "grid_id": False,
        "manifest_checksum": b"sha256",
        "work_dir": str(staged.work_dir),
        "prepared": object(),
        "work_identity": [*staged.work_identity],
        "work_identity-bool": (True, staged.work_identity[1]),
        "work_identity-member": ("1", staged.work_identity[1]),
        "file_checksums": [*staged.file_checksums],
        "file_checksums-member": (("key", 1),),
        **{
            f"{field}-{kind}": value
            for field in ("max_manifest_bytes", "max_asset_bytes", "max_state_bytes")
            for kind, value in (("nonpositive", 0), ("bool", True))
        },
    }
    for case, value in invalid_values.items():
        field = case.rsplit("-", 1)[0] if "-" in case else case
        with pytest.raises((TypeError, ValueError)):
            StagedWorkInputs(**(values | {field: value}))


def _refuse(prepared_inputs, *, phase: str, **kwargs):
    _value, work, registry, variant, states, state, forcing = prepared_inputs
    before = sources(variant, states, registry.object_store_root)
    with pytest.raises(AssemblyError) as captured:
        assemble(
            registry=kwargs.get("registry", registry),
            variant_dir=kwargs.get("variant_dir", variant),
            forcing=kwargs.get("forcing", forcing),
            states_root=kwargs.get("states_root", states),
            state_path=kwargs.get("state_path", state),
        )
    error = captured.value
    assert error.phase == phase
    if "path" in kwargs:
        assert error.path == kwargs["path"]
    if kwargs.get("cause_type") is not None:
        assert isinstance(error.__cause__, kwargs["cause_type"])
    if kwargs.get("final_absent", True):
        assert not (work / "model").exists()
    if kwargs.get("snapshot", True):
        assert sources(variant, states, registry.object_store_root) == before
    return error


def test_assembly_overrides_calibrated_state_and_stages_only_shud_members(
    tmp_path: Path,
) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    before = sources(variant, states, registry.object_store_root)
    result = run_assemble((value, work, registry, variant, states, state, forcing))
    assert result.path == work / "model"
    assert result.state_path.read_bytes() == state.read_bytes()
    assert result.state_path.read_bytes() != b"CALIBRATED-STATE\n"
    assert result.parameter_path.read_bytes() == PARAMETER_EXPECTED
    assert result.forcing_index_path.name == "demo.tsd.forc"
    assert result.forcing_index_path.read_bytes().splitlines()[-2:] == [
        b"1\t1\t2\t3\t4\t5\tX1.csv",
        b"2\t6\t7\t8\t9\t10\tX2.csv",
    ]
    assert tuple(path.name for path in result.forcing_csv_paths) == ("X1.csv", "X2.csv")
    assert (result.path / "nested/ordinary.dat").read_bytes() == b"nested bytes\n"
    assert not (result.path / "forcing_package.json").exists()
    assert not (result.path / "payloads").exists()
    assert not (result.path / "debug").exists()
    assert sources(variant, states, registry.object_store_root) == before
    assert result.cleanup_warnings == ()


def test_00z_and_12z_parameters_are_byte_identical(tmp_path: Path) -> None:
    values = []
    for hour in (0, 12):
        value = identity().__class__(
            source_id="gfs",
            cycle_time=identity().cycle_time.replace(hour=hour),
            model_id="demo_model",
            basin_id="basin_a",
            basin_version_id="basin_v1",
            river_network_version_id="rivnet_v1",
            project_name="demo",
        )
        work_dir(tmp_path / str(hour), value)
        registry = stage_work_registry(
            work_root=tmp_path / str(hour),
            identity=value,
            contract=contract(value),
            binding_content=BINDING,
            sp_att_content=SP_ATT,
            max_asset_bytes=4096,
        )
        variant = write_variant(tmp_path / f"variant-{hour}", value)
        states = tmp_path / f"states-{hour}"
        state = write_state(states, value)
        forcing = write_forcing_package(registry.object_store_root, value)
        result = assemble(
            registry=registry,
            variant_dir=variant,
            forcing=forcing,
            states_root=states,
            state_path=state,
        )
        values.append(result.parameter_path.read_bytes())
        for key, expected in (
            (b"START = 0", True),
            (b"END = 7", True),
            (b"DT_QR_DOWN = 60", True),
            (b"Update_IC_STEP = 720", True),
            (b"BINARY_OUTPUT = 1", True),
            (b"ASCII_OUTPUT = 0", True),
        ):
            assert (key in result.parameter_path.read_bytes()) is expected
    assert values == [PARAMETER_EXPECTED, PARAMETER_EXPECTED]


def test_commit_adjacent_reprobe_rejects_planted_run_final_without_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yd_producer._assemble_io import SharedAssemblyIO
    from yd_producer.assemble import rename_entry_no_follow as original_rename_entry

    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    rename_calls: list[str] = []
    original_io_rename = SharedAssemblyIO.rename
    planted = {"done": False}

    def planting_rename(
        self, source_parent, source, target_parent, target, root, *, operation=None
    ):
        if target == "model" and not planted["done"]:
            planted["done"] = True
            (target_parent / target).write_bytes(b"planted-run-final")
        return original_io_rename(
            self,
            source_parent,
            source,
            target_parent,
            target,
            root,
            operation=operation,
        )

    def spying_rename(*args, **kwargs):
        rename_calls.append("rename")
        return original_rename_entry(*args, **kwargs)

    monkeypatch.setattr(SharedAssemblyIO, "rename", planting_rename)
    monkeypatch.setattr("yd_producer.assemble.rename_entry_no_follow", spying_rename)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="assemble-commit",
        snapshot=False,
        final_absent=False,
    )
    assert planted["done"]
    assert rename_calls == []
    assert (work / "model").is_file()
    assert (work / "model").read_bytes() == b"planted-run-final"
    assert not list(work.glob(".model.assemble-stage-*"))


def test_state_exact_path_and_absolute_header_are_required(tmp_path: Path) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    other = states / "gfs" / "other.cfg.ic"
    other.write_bytes(state.read_bytes())
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        state_path=other,
    )
    write_state(
        states,
        value,
        content=b"1 6 720\nIndex Canopy Snow Surface Unsat GW\n1 0 0 0 0 0\n",
    )
    _refuse((value, work, registry, variant, states, state, forcing), phase="validate")
    write_state(
        states,
        value,
        content=b"1 6 not-a-minute\nIndex Canopy Snow Surface Unsat GW\n1 0 0 0 0 0\n",
    )
    _refuse((value, work, registry, variant, states, state, forcing), phase="validate")
    write_state(states, value)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        state_path=state.parent,
    )


def test_state_size_fifo_directory_and_symlinks_fail_before_commit(
    tmp_path: Path,
) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    state.write_bytes(b"x" * (MAX_STATE_IC_BYTES + 1))
    _refuse((value, work, registry, variant, states, state, forcing), phase="validate")
    state.unlink()
    os.mkfifo(state)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        snapshot=False,
    )
    state.unlink()
    write_state(states, value)
    linked_states = states.parent / "linked-states"
    linked_states.symlink_to(states, target_is_directory=True)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        states_root=linked_states,
        state_path=linked_states / "gfs" / f"{value.cycle_time:%Y%m%d%H}.cfg.ic",
    )
    outside = states / "outside.cfg.ic"
    outside.write_bytes(state.read_bytes())
    state.unlink()
    state.symlink_to(outside)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        snapshot=False,
    )


def test_input_roots_inside_work_are_rejected_before_staging(tmp_path: Path) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    inside_variant = write_variant(work / "input" / "variant", value)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        variant_dir=inside_variant,
    )
    assert not (work / "model").exists()

    inside_states = work / "input" / "states"
    inside_state = write_state(inside_states, value)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        states_root=inside_states,
        state_path=inside_state,
    )
    assert not (work / "model").exists()


@pytest.mark.parametrize("entry", ["demo.cfg.ic", "demo.para"])
@pytest.mark.parametrize("shape", ["missing", "directory", "symlink"])
def test_required_variant_entry_must_be_a_regular_no_follow_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str, shape: str
) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    target = variant / entry
    target.unlink()
    if shape == "directory":
        target.mkdir()
    elif shape == "symlink":
        other = "demo.para" if entry == "demo.cfg.ic" else "demo.cfg.ic"
        target.symlink_to(variant / other)
    # Discriminate the exact preflight: _tree must call regular() on the
    # retyped required entry before any staging write. A bypass/tolerance of
    # that check (the missing/directory/symlink para arms are otherwise caught
    # only incidentally by the later parameter read) turns this red.
    checked: list[str] = []
    original = __import__("yd_producer._assemble_fs", fromlist=["regular"]).regular

    def spying_regular(path, root):
        if path.parent == variant:
            checked.append(path.name)
        return original(path, root)

    monkeypatch.setattr("yd_producer._assemble_fs.regular", spying_regular)
    # Snapshot all three sources after constructing the broken input but
    # before assemble; every arm must leave the complete tree byte-identical.
    before = sources(variant, states, registry.object_store_root)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        snapshot=False,
    )
    assert sources(variant, states, registry.object_store_root) == before
    assert entry in checked
    assert not (work / "model").exists()
    assert not list(work.glob(".model.assemble-stage-*"))


def test_variant_nested_symlink_fifo_and_unsafe_component_fail(tmp_path: Path) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    os.mkfifo(variant / "bad.fifo")
    _refuse((value, work, registry, variant, states, state, forcing), phase="validate")
    (variant / "bad.fifo").unlink()
    (variant / "nested-link").symlink_to(variant / "nested", target_is_directory=True)
    _refuse((value, work, registry, variant, states, state, forcing), phase="validate")
    (variant / "nested-link").unlink()
    (variant / "file-link").symlink_to(variant / "nested" / "ordinary.dat")
    _refuse((value, work, registry, variant, states, state, forcing), phase="validate")
    (variant / "file-link").unlink()
    (variant / "bad name").write_bytes(b"no")
    _refuse((value, work, registry, variant, states, state, forcing), phase="validate")


def test_variant_and_output_filename_collision_is_rejected(tmp_path: Path) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    (variant / "X1.csv").write_bytes(b"collision")
    error = _refuse(
        (value, work, registry, variant, states, state, forcing), phase="validate"
    )
    assert "filename collision" in str(error)


@pytest.mark.parametrize("name", ["X1.csv", "demo.tsd.forc"])
def test_empty_directory_output_filename_collision_is_rejected_before_staging(
    tmp_path: Path, name: str
) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    (variant / name).mkdir()
    error = _refuse(
        (value, work, registry, variant, states, state, forcing), phase="validate"
    )
    assert "filename collision" in str(error)
    assert not list(work.glob(".model.assemble-stage-*"))


def test_preexisting_final_forms_are_never_overwritten(tmp_path: Path) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    (work / "model").mkdir()
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        final_absent=False,
    )
    (work / "model").rmdir()
    (work / "model").write_bytes(b"file")
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        final_absent=False,
    )
    (work / "model").unlink()
    (work / "model").symlink_to(variant)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        final_absent=False,
    )


def test_predictable_assemble_staging_collision_rejects_only_this_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    nonce = "dddddddddddddddddddddddddddddddd"
    occupied = work / f".model.assemble-stage-{nonce}"
    occupied.mkdir()
    (occupied / "keep").write_bytes(b"stale")

    class FixedUUID:
        hex = nonce

        def __init__(self, *args, **kwargs):
            return

    monkeypatch.setattr("yd_producer.assemble.uuid.uuid4", lambda: FixedUUID())
    error = _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="assemble-stage",
        snapshot=False,
    )
    assert error.path == occupied
    assert occupied.exists()
    assert (occupied / "keep").read_bytes() == b"stale"
    assert not (work / "model").exists()


def test_copy_write_and_rename_injections_leave_sources_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    original = __import__("yd_producer._assemble_fs", fromlist=["write_new"]).write_new

    def broken_write(path, content, root):
        if path.name == "demo.para":
            raise OSError("injected parameter write failure")
        return original(path, content, root)

    monkeypatch.setattr("yd_producer._assemble_fs.write_new", broken_write)
    error = _refuse(
        (value, work, registry, variant, states, state, forcing), phase="assemble-stage"
    )
    assert isinstance(error.__cause__, OSError)
    assert not list(work.glob(".model.assemble-stage-*"))
    monkeypatch.undo()

    def broken_copy(*args, **kwargs):
        raise OSError("injected copy failure")

    monkeypatch.setattr("yd_producer._assemble_fs.copy_regular", broken_copy)
    error = _refuse(
        (value, work, registry, variant, states, state, forcing), phase="assemble-stage"
    )
    assert isinstance(error.__cause__, OSError)
    monkeypatch.undo()

    def broken_rename(*args, **kwargs):
        raise OSError("injected rename failure")

    monkeypatch.setattr("yd_producer.assemble.rename_entry_no_follow", broken_rename)
    error = _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="assemble-commit",
    )
    assert error.path == work / "model"
    assert not (work / "model").exists()
    assert not list(work.glob(".model.assemble-stage-*"))


def test_typed_assembly_error_during_assemble_still_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)

    def broken(*args, **kwargs):
        raise AssemblyError("injected typed assemble failure", phase="assemble-stage")

    monkeypatch.setattr("yd_producer._assemble_fs.copy_regular", broken)
    error = _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="assemble-stage",
        snapshot=False,
    )
    assert str(error) == "injected typed assemble failure"
    assert not list(work.glob(".model.assemble-stage-*"))
    assert not (work / "model").exists()


def test_precommit_cleanup_failure_preserves_original_assemble_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)

    def broken_rename(*args, **kwargs):
        raise OSError("injected rename failure")

    monkeypatch.setattr("yd_producer.assemble.rename_entry_no_follow", broken_rename)
    monkeypatch.setattr(
        "yd_producer._assemble_fs.clean",
        lambda path, work_root: (f"staging cleanup failed for {path}: injected",),
    )
    error = _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="assemble-commit",
        snapshot=False,
    )
    assert isinstance(error.__cause__, OSError)
    assert any("injected" in warning for warning in error.cleanup_warnings)
    assert not (work / "model").exists()


def test_swap_between_preflight_and_copy_is_rejected_by_copy_time_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    source = registry.object_store_root / forcing_package_key(value) / "shud" / "X1.csv"
    swapped = {"done": False, "payload": b"swapped-between-preflight-and-copy"}
    original_copy = __import__(
        "yd_producer._assemble_fs", fromlist=["copy_regular"]
    ).copy_regular

    def swapping_copy(*args, **kwargs):
        destination = args[1]
        if not swapped["done"] and destination.name == "X1.csv":
            swapped["done"] = True
            source.write_bytes(swapped["payload"])
        return original_copy(*args, **kwargs)

    monkeypatch.setattr("yd_producer._assemble_fs.copy_regular", swapping_copy)
    error = _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="assemble-stage",
        snapshot=False,
    )
    assert swapped["done"] is True
    assert isinstance(error.__cause__, ValueError)
    assert "checksum does not match" in str(error.__cause__)
    assert not (work / "model").exists()
    assert not list(work.glob(".model.assemble-stage-*"))
    assert source.read_bytes() == swapped["payload"]


def test_post_rename_cleanup_warning_returns_in_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    monkeypatch.setattr(
        "yd_producer._assemble_fs.clean",
        lambda path, work_root: (f"staging cleanup failed for {path}: injected",),
    )
    result = run_assemble((value, work, registry, variant, states, state, forcing))
    assert result.path == work / "model"
    assert result.state_path.read_bytes() == state.read_bytes()
    assert len(result.cleanup_warnings) == 1
    assert "injected" in result.cleanup_warnings[0]


def test_parameter_oversize_fails_before_staging(tmp_path: Path) -> None:
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    (variant / f"{value.project_name}.para").write_bytes(
        b"START = old\n" + b"x" * (MAX_OBJECT_MANIFEST_BYTES)
    )
    _refuse((value, work, registry, variant, states, state, forcing), phase="validate")


def test_variant_has_no_invented_entry_or_depth_cap(tmp_path: Path) -> None:
    _value, _work, registry, variant, states, state, forcing = _inputs(tmp_path)
    nested = variant
    for index in range(140):
        nested = nested / f"d{index}"
        nested.mkdir()
    (nested / "leaf.dat").write_bytes(b"deep")
    result = assemble(
        registry=registry,
        variant_dir=variant,
        forcing=forcing,
        states_root=states,
        state_path=state,
    )
    assert (
        result.path / "/".join(f"d{index}" for index in range(140)) / "leaf.dat"
    ).read_bytes() == b"deep"


def test_staged_input_listing_stops_at_exact_fifteen_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config, local = fixtures.write_config_local(tmp_path)
    source_variant = fixtures.write_variant(local)
    source_state = fixtures.write_state(local)
    claim = claim_exact_work(
        work_root=Path(local.scratch_root) / "work",
        source="gfs",
        cycle=fixtures.CYCLE,
        cycle_name=fixtures.cycle_text(fixtures.CYCLE),
    )
    staged = stage_work_inputs(
        claim=claim,
        source_variant_dir=source_variant,
        source_state_path=source_state,
        source="gfs",
        cycle=fixtures.CYCLE,
        project_name=fixtures.PROJECT,
        grid_id="fixture-grid-gfs",
        max_manifest_bytes=65_536,
        max_asset_bytes=65_536,
        max_state_bytes=65_536,
    )
    for index in range(20):
        (staged.variant_dir / f"extra-{index:02d}").write_bytes(b"extra")
    variant = os.stat(staged.variant_dir, follow_symlinks=False)
    variant_id = (variant.st_dev, variant.st_ino)
    original_scandir = safe_fs.os.scandir
    consumed = [0]

    class CountingScan:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self._wrapped)
            consumed[0] += 1
            return entry

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def __exit__(self, *exc):
            return self._wrapped.__exit__(*exc)

        def close(self):
            return self._wrapped.close()

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    def counting_scandir(path, *args, **kwargs):
        iterator = original_scandir(path, *args, **kwargs)
        try:
            info = (
                os.fstat(path)
                if isinstance(path, int)
                else os.stat(path, follow_symlinks=False)
            )
        except OSError:
            return iterator
        if (info.st_dev, info.st_ino) != variant_id:
            return iterator
        return CountingScan(iterator)

    monkeypatch.setattr(safe_fs.os, "scandir", counting_scandir)
    with pytest.raises(StagedWorkInputsError):
        load_staged_work_inputs(
            work_dir=claim.work_dir,
            source="gfs",
            cycle=fixtures.CYCLE,
            project_name=fixtures.PROJECT,
            grid_id="fixture-grid-gfs",
            max_manifest_bytes=65_536,
            max_asset_bytes=65_536,
            max_state_bytes=65_536,
        )
    assert consumed[0] <= 15
    assert not (claim.work_dir / "model").exists()


def _assert_zero_readiness(claim, source_variant, source_state) -> None:
    assert not os.path.lexists(claim.work_dir / "input")
    assert not (claim.work_dir / "model").exists()
    assert source_variant.is_dir()
    assert source_state.is_file()


@pytest.mark.parametrize(
    "case",
    [
        "source-ancestor",
        "source-directory",
        "source-unreadable",
        "source-device",
        "staged-ancestor",
        "staged-directory",
        "staged-unreadable",
        "staged-device",
    ],
)
def test_public_stage_and_load_refuse_hostile_ancestors_directories_unreadable_and_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    _local, source_variant, source_state, claim, ids = fixtures._stage_public(tmp_path)
    if case.startswith("staged-"):
        stage_work_inputs(
            claim=claim,
            source_variant_dir=source_variant,
            source_state_path=source_state,
            **ids,
        )
    if case.endswith("ancestor"):
        real = source_variant if case.startswith("source") else claim.work_dir / "input"
        with pytest.raises(StagedWorkInputsError):
            if case.startswith("source"):
                alias = tmp_path / "alias-variant"
                alias.symlink_to(real, target_is_directory=True)
                stage_work_inputs(
                    claim=claim,
                    source_variant_dir=alias,
                    source_state_path=source_state,
                    **ids,
                )
            else:
                os.replace(real, real.with_name("real-input"))
                real.symlink_to(real.with_name("real-input"), target_is_directory=True)
                load_staged_work_inputs(work_dir=claim.work_dir, **ids)
        if case.startswith("source"):
            _assert_zero_readiness(claim, source_variant, source_state)
        else:
            assert not (claim.work_dir / "model").exists()
        return
    if case.endswith("directory"):
        victim = (
            source_variant / "yd.cfg.para"
            if case.startswith("source")
            else claim.work_dir / "input" / "variant" / "yd.cfg.para"
        )
        if victim.is_file():
            victim.unlink()
        victim.mkdir()
    elif case.endswith("unreadable"):
        if os.geteuid() == 0:
            pytest.skip("root ignores mode bits")
        victim = (
            source_variant
            if case.startswith("source")
            else claim.work_dir / "input" / "variant"
        )
        original = stat.S_IMODE(victim.stat().st_mode)
        victim.chmod(0o000)
        try:
            with pytest.raises(StagedWorkInputsError):
                if case.startswith("source"):
                    stage_work_inputs(
                        claim=claim,
                        source_variant_dir=source_variant,
                        source_state_path=source_state,
                        **ids,
                    )
                else:
                    load_staged_work_inputs(work_dir=claim.work_dir, **ids)
        finally:
            victim.chmod(original)
        if case.startswith("source"):
            _assert_zero_readiness(claim, source_variant, source_state)
        else:
            assert not (claim.work_dir / "model").exists()
        return
    else:
        victim_name = "yd.cfg.para"
        real_open = os.open
        fired: list[str] = []

        def device_open(path, flags, *args, **kwargs):
            leaf = path if isinstance(path, str) else getattr(path, "name", None)
            if leaf == victim_name and not fired:
                fired.append(str(leaf))
                return real_open(os.devnull, os.O_RDONLY)
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", device_open)
    with pytest.raises(StagedWorkInputsError):
        if case.startswith("source"):
            stage_work_inputs(
                claim=claim,
                source_variant_dir=source_variant,
                source_state_path=source_state,
                **ids,
            )
        else:
            load_staged_work_inputs(work_dir=claim.work_dir, **ids)
    if case.startswith("source"):
        _assert_zero_readiness(claim, source_variant, source_state)
    else:
        assert not (claim.work_dir / "model").exists()
