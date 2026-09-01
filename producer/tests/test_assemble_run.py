"""Requirement-driven tests for SHUD run-directory assembly."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
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
    work_dir,
    write_forcing_package,
    write_state,
    write_variant,
)

from yd_producer.assemble import AssemblyError, assemble, stage_work_registry
from yd_producer.state import MAX_STATE_IC_BYTES
from yd_producer.store.object_store import MAX_OBJECT_MANIFEST_BYTES


def _inputs(tmp_path: Path):
    return prepared(tmp_path)


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
    value, work, registry, variant, states, state, forcing = _inputs(tmp_path)
    rename_calls: list[str] = []
    original_probe = __import__(
        "yd_producer.assemble", fromlist=["_commit_probe"]
    )._commit_probe
    planted = {"done": False}

    def planting_probe(parent, name, root, label):
        if name == "model" and not planted["done"]:
            planted["done"] = True
            (parent / name).write_bytes(b"planted-run-final")
        return original_probe(parent, name, root, label)

    def spying_rename(*args, **kwargs):
        rename_calls.append("rename")
        return __import__(
            "yd_producer.assemble", fromlist=["rename_entry_no_follow"]
        ).rename_entry_no_follow(*args, **kwargs)

    monkeypatch.setattr("yd_producer.assemble._commit_probe", planting_probe)
    monkeypatch.setattr("yd_producer.assemble.rename_entry_no_follow", spying_rename)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="assemble-commit",
        snapshot=False,
        final_absent=False,
    )
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
    inside_variant = write_variant(work / "inside-variant", value)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        variant_dir=inside_variant,
    )
    inside_states = work / "inside-states"
    inside_state = write_state(inside_states, value)
    _refuse(
        (value, work, registry, variant, states, state, forcing),
        phase="validate",
        states_root=inside_states,
        state_path=inside_state,
    )


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
    _refuse((value, work, registry, variant, states, state, forcing), phase="validate")


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
