"""Requirement-driven tests for work-local registry staging."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from assembly_fixtures import (
    BINDING,
    BINDING_SHA,
    IDENTITY_FIELDS,
    INDEX_LITERAL,
    MANIFEST_BYTES,
    REGISTRY_JSON,
    SP_ATT,
    SP_ATT_SHA,
    contract,
    digest,
    file_repository_contract,
    handoff_paths,
    identity,
    prepared,
    run_assemble,
    stage,
    tree_snapshot,
    work_dir,
    write_file_repository_canonical_catalog,
)

from yd_producer.assemble import (
    AssemblyError,
    WorkIdentity,
    WorkRegistry,
    assemble,
    stage_work_registry,
)
from yd_producer.cleanup import FailureInputs, finalize_failed_job
from yd_producer.executor import JobRecord, JobSpec, JobState
from yd_producer.forcing import ForcingProducer, ForcingProducerConfig
from yd_producer.forcing.file_store import FileForcingRepository
from yd_producer.store.object_store import LocalObjectStore
from yd_producer.store.safe_fs import remove_tree_allow_symlinks


def _stage(tmp_path: Path, value=None, **kwargs):
    return stage(tmp_path, value, **kwargs)


def _refuse(tmp_path: Path, *, phase: str, **kwargs):
    value = kwargs.pop("identity", identity())
    work = kwargs.pop("work", None)
    if work is None:
        work = work_dir(tmp_path, value)
    before = tree_snapshot(work)
    with pytest.raises(AssemblyError) as captured:
        stage_work_registry(
            work_root=kwargs.pop("work_root", tmp_path),
            identity=value,
            contract=kwargs.pop("contract", contract(value)),
            binding_content=kwargs.pop("binding_content", BINDING),
            sp_att_content=kwargs.pop("sp_att_content", SP_ATT),
            max_asset_bytes=kwargs.pop("max_asset_bytes", 4096),
        )
    error = captured.value
    assert error.phase == phase
    if "path" in kwargs:
        assert error.path == kwargs["path"]
    if kwargs.get("cause_type") is not None:
        assert isinstance(error.__cause__, kwargs["cause_type"])
    if kwargs.get("final_absent", True):
        assert not (work / "object-store/models/demo_model").exists()
    if kwargs.get("snapshot", True):
        assert tree_snapshot(work) == before
    return error, work


@pytest.mark.parametrize("source", ["gfs", "ifs", "GFS", "IFS"])
def test_gfs_and_ifs_stage_exact_five_objects_and_literal_bytes(
    tmp_path: Path, source: str
) -> None:
    value, work, registry = _stage(tmp_path, identity(source=source))
    root = registry.object_store_root
    files = sorted(
        str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
    )
    assert files == [
        "models/demo_model/direct-grid/binding.json",
        "models/demo_model/manifest.json",
        "models/demo_model/package/demo.tsd.forc",
        "models/demo_model/package/input/demo.sp.att",
        "models/demo_model/registry.json",
    ]
    payload = json.loads((root / registry.registry_manifest).read_bytes())
    row = payload["models"][0]
    assert set(payload) == {"models"}
    assert set(row) == {
        "model_id",
        "basin_id",
        "basin_version_id",
        "river_network_version_id",
        "model_package_uri",
        "manifest_uri",
        "resource_profile",
    }
    assert set(row["resource_profile"]) == {"direct_grid_forcing", "shud_input_name"}
    assert (root / registry.registry_manifest).read_bytes() == REGISTRY_JSON[
        value.source_id
    ]
    assert (root / registry.model_manifest_uri).read_bytes() == MANIFEST_BYTES
    assert (root / "models/demo_model/direct-grid/binding.json").read_bytes() == BINDING
    assert (root / "models/demo_model/package/input/demo.sp.att").read_bytes() == SP_ATT
    assert (
        root / "models/demo_model/package/demo.tsd.forc"
    ).read_bytes() == INDEX_LITERAL
    assert digest(BINDING) == BINDING_SHA
    assert digest(SP_ATT) == SP_ATT_SHA
    repository = FileForcingRepository(
        LocalObjectStore(root), registry.registry_manifest
    )
    assert repository.resolve_model_identity(model_id="demo_model") == {
        "basin_id": "basin_a",
        "basin_version_id": "basin_v1",
        "river_network_version_id": "rivnet_v1",
    }
    stations = repository.load_met_stations(basin_version_id="basin_v1")
    assert [item.properties_json["shud_forcing_index"] for item in stations] == [1, 2]
    assert [item.properties_json["forcing_filename"] for item in stations] == [
        "X1.csv",
        "X2.csv",
    ]
    assert [(item.longitude, item.latitude, item.elevation_m) for item in stations] == [
        (1.0, 2.0, 5.0),
        (6.0, 7.0, 10.0),
    ]
    assert [item.station_id for item in stations] == ["demo_forc_001", "demo_forc_002"]
    assert not list(work.glob(".demo_model.registry-stage-*"))
    assert registry.cleanup_warnings == ()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_id": "era5"},
        {"source_id": ""},
        {"source_id": "   "},
        {"source_id": 1},
        {"cycle_time": object()},
        {"cycle_time": datetime(2026, 5, 7, tzinfo=None)},  # noqa: DTZ001
        {"cycle_time": datetime(2026, 5, 7, tzinfo=timezone(timedelta(hours=8)))},
        {"cycle_time": datetime(2026, 5, 7, 6, tzinfo=UTC)},
        {"cycle_time": datetime(2026, 5, 7, 12, 30, tzinfo=UTC)},
        {"cycle_time": datetime(2026, 5, 7, 0, 0, 1, tzinfo=UTC)},
        {"cycle_time": datetime(2026, 5, 7, 0, 0, 0, 1, tzinfo=UTC)},
        {"model_id": ""},
        {"model_id": "   "},
        {"model_id": 1},
        {"model_id": "../escape"},
        {"model_id": "."},
        {"model_id": ".."},
        {"basin_id": ""},
        {"basin_id": 1},
        {"basin_version_id": "."},
        {"basin_version_id": ""},
        {"basin_version_id": 1},
        {"river_network_version_id": ""},
        {"river_network_version_id": 1},
        {"project_name": "-unsafe"},
        {"project_name": "非ascii"},
        {"project_name": ""},
        {"project_name": 1},
        {"project_name": "a/b"},
        {"project_name": "a\\b"},
        {"project_name": "a\x00b"},
    ],
)
def test_identity_rejections_are_validate_errors(kwargs) -> None:
    base = dict(IDENTITY_FIELDS)
    base.update(kwargs)
    with pytest.raises(AssemblyError) as captured:
        WorkIdentity(**base)
    assert captured.value.phase == "validate"
    assert captured.value.__cause__ is not None


def test_zero_offset_cycle_normalizes_to_utc() -> None:
    value = WorkIdentity(
        **{
            **IDENTITY_FIELDS,
            "cycle_time": datetime(2026, 5, 7, tzinfo=timezone(timedelta(0))),
        }
    )
    assert value.cycle_time.tzinfo is UTC
    assert value.source_id == "gfs"


def test_contract_checksum_uri_type_and_oversize_fail_before_final_root(
    tmp_path: Path,
) -> None:
    value = identity()
    work = work_dir(tmp_path, value)
    base = contract(value)
    cases = [
        replace(base, binding_checksum=digest(b"wrong")),
        replace(base, binding_checksum=f"sha256:{digest(b'wrong')}"),
        replace(base, sp_att_checksum=digest(b"wrong")),
        replace(base, sp_att_checksum=f"sha256:{digest(b'wrong')}"),
        replace(base, binding_uri="models/demo_model/other.json"),
        replace(base, sp_att_path="input/other.sp.att"),
        replace(base, applicable_source_ids=("gfs", "ifs")),
    ]
    for invalid in cases:
        _refuse(tmp_path, identity=value, work=work, contract=invalid, phase="validate")
    _refuse(
        tmp_path,
        identity=value,
        work=work,
        binding_content=bytearray(BINDING[:-1] + b"X"),
        phase="validate",
    )
    _refuse(
        tmp_path,
        identity=value,
        work=work,
        sp_att_content=memoryview(SP_ATT[:-1] + b"X"),
        phase="validate",
    )
    _refuse(
        tmp_path, identity=value, work=work, sp_att_content=b"\xff", phase="validate"
    )
    _refuse(
        tmp_path,
        identity=value,
        work=work,
        binding_content="not-bytes",
        phase="validate",
    )
    _refuse(tmp_path, identity=value, work=work, max_asset_bytes=0, phase="validate")
    _refuse(tmp_path, identity=value, work=work, max_asset_bytes=-1, phase="validate")
    _refuse(tmp_path, identity=value, work=work, max_asset_bytes=1.5, phase="validate")
    _refuse(
        tmp_path,
        identity=value,
        work=work,
        max_asset_bytes=len(BINDING) - 1,
        phase="validate",
    )


def test_exact_work_path_and_shapes_are_required(tmp_path: Path) -> None:
    value = identity()
    _refuse(
        tmp_path,
        identity=value,
        work=tmp_path / "missing",
        phase="validate",
        snapshot=False,
    )
    file_root = tmp_path / "file-root"
    file_root.write_bytes(b"no")
    _refuse(
        tmp_path,
        identity=value,
        work_root=file_root,
        work=file_root,
        phase="validate",
        snapshot=False,
    )
    source_file = tmp_path / "gfs"
    source_file.write_bytes(b"no")
    _refuse(
        tmp_path, identity=value, work=source_file, phase="validate", snapshot=False
    )


@pytest.mark.parametrize("symlink", ["work_root", "source", "cycle"])
def test_registry_rejects_symlinked_work_ancestry(tmp_path: Path, symlink: str) -> None:
    value = identity()
    real = tmp_path / "real"
    work_dir(real, value)
    if symlink == "work_root":
        root = tmp_path / "root-link"
        root.symlink_to(real, target_is_directory=True)
    elif symlink == "source":
        root = tmp_path / "source-link-root"
        root.mkdir()
        (root / value.source_id).symlink_to(
            real / value.source_id, target_is_directory=True
        )
    else:
        root = tmp_path / "cycle-link-root"
        source = root / value.source_id
        source.mkdir(parents=True)
        (source / f"{value.cycle_time:%Y%m%d%H}").symlink_to(
            real / value.source_id / f"{value.cycle_time:%Y%m%d%H}",
            target_is_directory=True,
        )
    _refuse(
        tmp_path,
        identity=value,
        work_root=root,
        work=root,
        phase="validate",
        snapshot=False,
    )


def test_final_or_fixed_staging_is_never_overwritten(tmp_path: Path) -> None:
    value = identity()
    work = work_dir(tmp_path, value)
    model = work / "object-store/models/demo_model"
    model.mkdir(parents=True)
    sentinel = model / "sentinel"
    sentinel.write_bytes(b"keep")
    error, _work = _refuse(
        tmp_path,
        identity=value,
        work=work,
        phase="validate",
        snapshot=False,
        final_absent=False,
    )
    assert sentinel.read_bytes() == b"keep"
    assert not list(work.glob(".demo_model.registry-stage-*"))
    assert error.path is None


def test_predictable_nonce_collision_rejects_only_this_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = identity()
    work = work_dir(tmp_path, value)
    nonce = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    occupied = work / f".demo_model.registry-stage-{nonce}"
    occupied.mkdir()
    (occupied / "keep").write_bytes(b"stale-this")
    unrelated = work / ".demo_model.registry-stage-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    unrelated.mkdir()
    (unrelated / "keep").write_bytes(b"stale-other")

    class FixedUUID:
        hex = nonce

        def __init__(self, *args, **kwargs):
            return

    monkeypatch.setattr("yd_producer.assemble.uuid.uuid4", lambda: FixedUUID())
    error, _work = _refuse(
        tmp_path, identity=value, work=work, phase="registry-stage", snapshot=False
    )
    assert error.path == occupied
    assert occupied.exists()
    assert (occupied / "keep").read_bytes() == b"stale-this"
    assert unrelated.exists()
    assert not (work / "object-store/models/demo_model").exists()


def test_unrelated_stale_nonce_does_not_block_a_new_call(tmp_path: Path) -> None:
    value = identity()
    work = work_dir(tmp_path, value)
    stale = work / ".demo_model.registry-stage-cccccccccccccccccccccccccccccccc"
    stale.mkdir()
    (stale / "keep").write_bytes(b"other")
    _value, _work, registry = _stage(tmp_path, value)
    assert (registry.object_store_root / "models/demo_model/registry.json").is_file()
    assert stale.exists()


def test_oversize_asset_stage_then_point_of_use_streaming_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = identity()
    big = b"B" * (16 * 1024 * 1024 + 1) + BINDING
    big_contract = replace(contract(value), binding_checksum=digest(big))
    work_dir(tmp_path, value)
    registry = stage_work_registry(
        work_root=tmp_path,
        identity=value,
        contract=big_contract,
        binding_content=big,
        sp_att_content=SP_ATT,
        max_asset_bytes=len(big) + len(SP_ATT),
    )
    root = registry.object_store_root
    assert (root / "models/demo_model/direct-grid/binding.json").is_file()
    payload = json.loads((root / registry.registry_manifest).read_bytes())
    assert payload["models"][0]["resource_profile"]["direct_grid_forcing"][
        "binding_checksum"
    ] == digest(big)
    assert (
        root / "models/demo_model/direct-grid/binding.json"
    ).stat().st_size > 16 * 1024 * 1024
    # Point-of-use assemble re-reads the asset via streaming (no 16 MiB cap).
    fixture = __import__("assembly_fixtures", fromlist=["write_variant"])
    variant = fixture.write_variant(tmp_path / "variant", value)
    states = tmp_path / "states"
    state = fixture.write_state(states, value)
    forcing = fixture.write_forcing_package(root, value)
    # CPython os.read(fd, limit) records a peak allocation of the *requested*
    # size even when the file is small, so every bounded read with a large
    # limit (16 MiB JSONs, 64 MiB state) would add a false peak that swamps
    # the 16+ MiB binding measurement. Bound those small-fixture reads so the
    # tracemalloc peak below reflects the binding read path alone.
    monkeypatch.setattr("yd_producer.assemble.MAX_OBJECT_MANIFEST_BYTES", 128 * 1024)
    monkeypatch.setattr(
        "yd_producer.forcing.file_store.MAX_OBJECT_MANIFEST_BYTES", 128 * 1024
    )
    # MAX_STATE_IC_BYTES is imported into assemble.py at module import time.
    monkeypatch.setattr("yd_producer.assemble.MAX_STATE_IC_BYTES", 128 * 1024)
    import tracemalloc

    tracemalloc.start()
    try:
        result = assemble(
            registry=registry,
            variant_dir=variant,
            forcing=forcing,
            states_root=states,
            state_path=state,
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # The 16+ MiB binding is streamed, not materialized, at point of use.
    assert result.path == registry.work_dir / "model"
    assert result.state_path.read_bytes() == state.read_bytes()
    assert peak < 4 * 1024 * 1024, f"point-of-use peak={peak}"


def test_point_of_use_asset_checksum_drift_is_rejected(
    tmp_path: Path,
) -> None:
    value = identity()
    work_dir(tmp_path, value)
    registry = stage_work_registry(
        work_root=tmp_path,
        identity=value,
        contract=contract(value),
        binding_content=BINDING,
        sp_att_content=SP_ATT,
        max_asset_bytes=4096,
    )
    (
        registry.object_store_root / "models/demo_model/package/input/demo.sp.att"
    ).write_bytes(b"tampered")
    variant = __import__("assembly_fixtures", fromlist=["write_variant"]).write_variant(
        tmp_path / "variant", value
    )
    states = tmp_path / "states"
    state = __import__("assembly_fixtures", fromlist=["write_state"]).write_state(
        states, value
    )
    forcing = __import__(
        "assembly_fixtures", fromlist=["write_forcing_package"]
    ).write_forcing_package(registry.object_store_root, value)
    with pytest.raises(AssemblyError) as captured:
        assemble(
            registry=registry,
            variant_dir=variant,
            forcing=forcing,
            states_root=states,
            state_path=state,
        )
    assert captured.value.phase == "validate"
    assert not (registry.work_dir / "model").exists()
    assert not list(registry.work_dir.glob(".model.assemble-stage-*"))


def test_repository_readback_is_required_before_registry_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = identity()
    work = work_dir(tmp_path, value)

    def broken(*args, **kwargs):
        raise ValueError("injected read-back failure")

    monkeypatch.setattr(
        "yd_producer.assemble.FileForcingRepository.resolve_model_identity", broken
    )
    error, _work = _refuse(tmp_path, identity=value, work=work, phase="registry-stage")
    assert isinstance(error.__cause__, ValueError)
    assert not list(work.glob(".demo_model.registry-stage-*"))


def test_station_index_filename_and_geometry_mismatch_fails_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = identity()
    work = work_dir(tmp_path, value)
    monkeypatch.setattr(
        "yd_producer.assemble._station_index",
        lambda contract: (
            b"ID\tLon\tLat\tX\tY\tZ\tFilename\n"
            b"1\t99\t98\t3\t4\t97\tY1.csv\n"
            b"2\t96\t95\t8\t9\t94\tY2.csv\n"
        ),
    )
    error, _work = _refuse(tmp_path, identity=value, work=work, phase="registry-stage")
    assert isinstance(error.__cause__, ValueError)
    assert "station-index" in str(error.__cause__)
    assert not (work / "object-store/models/demo_model").exists()
    assert not list(work.glob(".demo_model.registry-stage-*"))


def test_commit_adjacent_reprobe_rejects_planted_final_without_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = identity()
    work = work_dir(tmp_path, value)
    rename_calls: list[str] = []
    original_probe = __import__(
        "yd_producer.assemble", fromlist=["_commit_probe"]
    )._commit_probe
    planted = {"done": False}

    def planting_probe(parent, name, root, label):
        if name == value.model_id and not planted["done"]:
            planted["done"] = True
            (parent / name).write_bytes(b"planted-final")
        return original_probe(parent, name, root, label)

    def spying_rename(*args, **kwargs):
        rename_calls.append("rename")
        return __import__(
            "yd_producer.assemble", fromlist=["rename_entry_no_follow"]
        ).rename_entry_no_follow(*args, **kwargs)

    monkeypatch.setattr("yd_producer.assemble._commit_probe", planting_probe)
    monkeypatch.setattr("yd_producer.assemble.rename_entry_no_follow", spying_rename)
    error, _work = _refuse(
        tmp_path,
        identity=value,
        work=work,
        phase="registry-commit",
        snapshot=False,
        final_absent=False,
    )
    assert error.path == work / "object-store/models/demo_model"
    assert rename_calls == []
    assert (work / "object-store/models/demo_model").is_file()
    assert (work / "object-store/models/demo_model").read_bytes() == b"planted-final"
    assert not list(work.glob(".demo_model.registry-stage-*"))


def test_registry_write_and_rename_injections_clean_this_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = identity()
    work = work_dir(tmp_path, value)
    original = __import__(
        "yd_producer.assemble", fromlist=["_assemble_fs"]
    )._assemble_fs.write_new

    def broken_write(path, content, root):
        if path.name == "registry.json":
            raise OSError("injected registry write failure")
        return original(path, content, root)

    monkeypatch.setattr("yd_producer._assemble_fs.write_new", broken_write)
    error, _work = _refuse(tmp_path, identity=value, work=work, phase="registry-stage")
    assert isinstance(error.__cause__, OSError)
    assert not list(work.glob(".demo_model.registry-stage-*"))
    monkeypatch.undo()

    def broken_rename(*args, **kwargs):
        raise OSError("injected rename failure")

    monkeypatch.setattr("yd_producer.assemble.rename_entry_no_follow", broken_rename)
    error, _work = _refuse(
        tmp_path, identity=value, work=work, phase="registry-commit", snapshot=False
    )
    assert error.path == work / "object-store/models/demo_model"
    assert not (work / "object-store/models/demo_model").exists()
    assert not list(work.glob(".demo_model.registry-stage-*"))


def test_precommit_cleanup_failure_keeps_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = identity()
    work = work_dir(tmp_path, value)

    def broken_rename(*args, **kwargs):
        raise OSError("injected rename failure")

    def broken_clean(path, work_root):
        return (f"staging cleanup failed for {path}: injected",)

    monkeypatch.setattr("yd_producer.assemble.rename_entry_no_follow", broken_rename)
    monkeypatch.setattr("yd_producer._assemble_fs.clean", broken_clean)
    error, _work = _refuse(
        tmp_path, identity=value, work=work, phase="registry-commit", snapshot=False
    )
    assert isinstance(error.__cause__, OSError)
    assert any("injected" in warning for warning in error.cleanup_warnings)
    assert not (work / "object-store/models/demo_model").exists()


def test_post_rename_cleanup_warning_does_not_downgrade_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = identity()
    work_dir(tmp_path, value)
    monkeypatch.setattr(
        "yd_producer._assemble_fs.clean",
        lambda path, work: (f"staging cleanup failed for {path}: injected",),
    )
    _value, work, registry = _stage(tmp_path, value)
    assert (work / "object-store/models/demo_model/registry.json").is_file()
    assert len(registry.cleanup_warnings) == 1
    assert "injected" in registry.cleanup_warnings[0]


def test_typed_assembly_error_during_staging_still_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = identity()
    work = work_dir(tmp_path, value)

    def broken(*args, **kwargs):
        raise AssemblyError("injected typed failure", phase="registry-stage", path=work)

    monkeypatch.setattr(
        "yd_producer.assemble.FileForcingRepository.resolve_model_identity", broken
    )
    error, _work = _refuse(
        tmp_path, identity=value, work=work, phase="registry-stage", snapshot=False
    )
    assert error is not None
    assert str(error) == "injected typed failure"
    assert not list(work.glob(".demo_model.registry-stage-*"))


def test_file_repository_produces_handoff_with_native_time_lattice(
    tmp_path: Path,
) -> None:
    value = identity()
    work_dir(tmp_path, value)
    registry = stage_work_registry(
        work_root=tmp_path,
        identity=value,
        contract=file_repository_contract(value),
        binding_content=BINDING,
        sp_att_content=SP_ATT,
        max_asset_bytes=4096,
    )
    store = LocalObjectStore(registry.object_store_root)
    write_file_repository_canonical_catalog(store, value)
    repository = FileForcingRepository(store, registry.registry_manifest)
    result = ForcingProducer(
        config=ForcingProducerConfig(
            workspace_root=registry.work_dir,
            object_store_root=registry.object_store_root,
            object_store_prefix="",
        ),
        repository=repository,
        object_store=store,
    ).produce(
        source_id=value.source_id,
        cycle_time=value.cycle_time,
        model_id=value.model_id,
        basin_id=value.basin_id,
        basin_version_id=value.basin_version_id,
        river_network_version_id=value.river_network_version_id,
    )
    handoff, package = handoff_paths(store, value)
    assert result.status == "forcing_ready"
    assert handoff.is_file() and package.is_file()
    lattice = json.loads(handoff.read_text())["payloads"]["station_timeseries"][
        "time_lattice"
    ]
    assert {segment["native_resolution"] for segment in lattice} == {"3h"}
    assert {segment["variable"] for segment in lattice} == {
        "PRCP",
        "Press",
        "RH",
        "Rn",
        "TEMP",
        "wind",
    }


def test_assembled_run_is_contained_by_real_whole_work_cleanup(tmp_path: Path) -> None:
    value, work, registry, variant, states, state, forcing = prepared(tmp_path)
    result = run_assemble((value, work, registry, variant, states, state, forcing))
    sibling = tmp_path / "outside"
    sibling.write_bytes(b"outside")
    variant_before, states_before, sibling_before = (
        tree_snapshot(variant),
        tree_snapshot(states),
        sibling.read_bytes(),
    )
    assert result.path == work / "model"
    assert (work / "object-store/models/demo_model/registry.json").is_file()
    remove_tree_allow_symlinks(
        registry.work_dir.parent,
        registry.work_dir.name,
        containment_root=tmp_path,
    )
    assert not work.exists()
    assert not result.path.exists()
    assert tree_snapshot(variant) == variant_before
    assert tree_snapshot(states) == states_before
    assert sibling.read_bytes() == sibling_before


def test_assembled_run_is_contained_by_failure_finalization(tmp_path: Path) -> None:
    value, work, registry, variant, states, state, forcing = prepared(tmp_path)
    result = run_assemble((value, work, registry, variant, states, state, forcing))
    outside = tmp_path / "outside-state"
    outside.write_bytes(b"keep-state")
    variant_before, states_before = tree_snapshot(variant), tree_snapshot(states)
    yd_root = tmp_path / "yd"
    yd_root.mkdir()
    log_path = work / "merged.log"
    log_path.write_bytes(b"job ran\n")
    spec = JobSpec(
        name="gfs-2026050700",
        work_dir=work,
        command=("shud", "gfs", "--cycle", "2026050700"),
        log_path=log_path,
        resources={
            "partition": "cpu",
            "account": "a",
            "cpus": 1,
            "memory": "1G",
            "walltime": "01:00:00",
        },
    )
    record = JobRecord(
        job_id="fake-15-run",
        name="gfs-2026050700",
        state=JobState.FAILED,
        resources=spec.resources,
        submitted_at=value.cycle_time,
        started_at=value.cycle_time,
        ended_at=value.cycle_time + timedelta(minutes=1),
    )
    finalize_failed_job(
        FailureInputs(
            yd_root=yd_root,
            work_root=tmp_path,
            source=value.source_id,
            cycle=value.cycle_time,
            job_spec=spec,
            job_record=record,
            exit_code="1:0",
        )
    )
    assert not work.exists()
    assert not result.path.exists()
    assert tree_snapshot(variant) == variant_before
    assert tree_snapshot(states) == states_before
    assert outside.read_bytes() == b"keep-state"
    assert (yd_root / "logs/gfs/2026050700.log").is_file()


def test_registry_is_contained_by_real_whole_work_cleanup(tmp_path: Path) -> None:
    _value, work, registry = _stage(tmp_path)
    sibling = tmp_path / "outside"
    sibling.write_bytes(b"outside")
    remove_tree_allow_symlinks(
        registry.work_dir.parent,
        registry.work_dir.name,
        containment_root=tmp_path,
    )
    assert not work.exists()
    assert sibling.read_bytes() == b"outside"


def test_registry_is_contained_by_failure_finalization(tmp_path: Path) -> None:
    value, work, _registry = _stage(tmp_path)
    outside = tmp_path / "outside-state"
    outside.write_bytes(b"keep-state")
    yd_root = tmp_path / "yd"
    yd_root.mkdir()
    log_path = work / "merged.log"
    log_path.write_bytes(b"job ran\n")
    spec = JobSpec(
        name="gfs-2026050700",
        work_dir=work,
        command=("shud", "gfs", "--cycle", "2026050700"),
        log_path=log_path,
        resources={
            "partition": "cpu",
            "account": "a",
            "cpus": 1,
            "memory": "1G",
            "walltime": "01:00:00",
        },
    )
    record = JobRecord(
        job_id="fake-15",
        name="gfs-2026050700",
        state=JobState.FAILED,
        resources=spec.resources,
        submitted_at=value.cycle_time,
        started_at=value.cycle_time,
        ended_at=value.cycle_time + timedelta(minutes=1),
    )
    finalize_failed_job(
        FailureInputs(
            yd_root=yd_root,
            work_root=tmp_path,
            source=value.source_id,
            cycle=value.cycle_time,
            job_spec=spec,
            job_record=record,
            exit_code="1:0",
        )
    )
    assert not work.exists()
    assert outside.read_bytes() == b"keep-state"
    assert (yd_root / "logs/gfs/2026050700.log").is_file()


def test_hand_constructed_registry_cannot_bypass_exact_layout(tmp_path: Path) -> None:
    value, _work, registry = _stage(tmp_path)
    with pytest.raises(AssemblyError) as captured:
        WorkRegistry(
            identity=value,
            work_dir=registry.work_dir,
            object_store_root=registry.object_store_root,
            registry_manifest="models/demo_model/other.json",
            model_package_uri=registry.model_package_uri,
            model_manifest_uri=registry.model_manifest_uri,
        )
    assert captured.value.phase == "validate"
    with pytest.raises(AssemblyError):
        WorkRegistry(
            identity=value,
            work_dir=tmp_path,
            object_store_root=tmp_path / "object-store",
            registry_manifest=registry.registry_manifest,
            model_package_uri=registry.model_package_uri,
            model_manifest_uri=registry.model_manifest_uri,
        )
