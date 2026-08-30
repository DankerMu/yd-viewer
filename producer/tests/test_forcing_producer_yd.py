"""Issue #14 yd-authored acceptance suite: direct-grid forcing production.

Tests live at the fixture-declared public seams:
1. ``ForcingProducer.produce`` with a synthetic object store + explicit
   file repository — station/cell values, weights, source isolation,
   00Z/12Z anchoring, failure-without-ready.
2. ``FileForcingRepository`` — bounded/no-follow reads, no host path fallback.
3. ``parse_direct_grid_forcing_contract`` — pin field/rejection matrix.
4. provenance-free helpers: grid signature, descriptor alias, bounded JSON.

Expected values are independent literals or hand-worked numbers, never
recomputed with production helpers.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from forcing_fixtures import (
    CYCLE_00Z,
    CYCLE_12Z,
    DEFAULT_GRID_SIGNATURE,
    GFS_GRID_ID,
    IFS_GRID_ID,
    canonical_products_for_cycle,
    direct_grid_binding,
    sp_att_content,
)

from yd_producer.forcing import (
    DirectGridContractError,
    ForcingProducer,
    ForcingProducerConfig,
    ForcingProductionError,
    parse_cycle_time,
    parse_direct_grid_forcing_contract,
)
from yd_producer.forcing.file_store import FileForcingRepository
from yd_producer.forcing.producer import FORCING_VARIABLES
from yd_producer.store.object_store import LocalObjectStore, sha256_bytes


def _contract(seed: dict[str, Any]) -> Any:
    source_id = seed["applicable_source_ids"][0]
    return parse_direct_grid_forcing_contract(seed, source_id=source_id)


def _write_registry(store: LocalObjectStore, models: list[dict[str, Any]]) -> str:
    store.write_bytes_atomic(
        "models/demo/registry.json", json.dumps({"models": models}).encode("utf-8")
    )
    return "models/demo/registry.json"


def _file_repository(
    tmp_path: Path,
    *,
    seed: dict[str, Any],
    store: LocalObjectStore,
) -> tuple[FileForcingRepository, Any]:
    binding_content = json.dumps(
        {"schema_version": "nhms.direct_grid.binding.v1"}
    ).encode("utf-8")
    stations = seed["station_bindings"]
    sp_att = sp_att_content(tuple(range(1, len(stations) + 1))).encode("utf-8")
    # keep the seed checksum in sync with the bytes we actually write
    seed["binding_checksum"] = sha256_bytes(binding_content)
    seed["sp_att_checksum"] = sha256_bytes(sp_att)
    store.write_bytes_atomic(seed["binding_uri"], binding_content)
    store.write_bytes_atomic(f"models/demo/package/{seed['sp_att_path']}", sp_att)
    model = {
        "model_id": "demo_model",
        "basin_id": "basin_a",
        "basin_version_id": "basin_v1",
        "river_network_version_id": "rivnet_v1",
        "model_package_uri": "models/demo/package",
        "resource_profile": {"direct_grid_forcing": seed, "shud_input_name": "demo"},
    }
    registry = _write_registry(store, [model])
    repository = FileForcingRepository(object_store=store, registry_manifest=registry)
    contract = _contract(seed)
    return repository, contract


def _produce_for_cycle(
    tmp_path: Path,
    *,
    cycle_text: str,
    source_id: str = "gfs",
    grid_id: str = GFS_GRID_ID,
    grid_signature: str = DEFAULT_GRID_SIGNATURE,
    cell_ids: tuple[str, ...] = ("0", "1"),
    wind_uv: tuple[tuple[float, float], ...] = ((3.0, 4.0), (6.0, 8.0)),
    netcdf_cell_ids: tuple[str, ...] = ("0", "1", "2"),
    max_manifest_bytes: int = 33_554_432,
) -> tuple[Any, Any, Any]:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding(
        grid_id=grid_id,
        grid_signature=grid_signature,
        applicable_source_ids=(source_id.upper(),),
        cell_ids=cell_ids,
        wind_uv=wind_uv,
    )
    repository, _ = _file_repository(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id=source_id,
        cycle_text=cycle_text,
        grid_id=grid_id,
        grid_signature=grid_signature,
        cell_ids=netcdf_cell_ids,
    )
    producer = ForcingProducer(
        config=ForcingProducerConfig(
            workspace_root=tmp_path,
            object_store_root=tmp_path,
            object_store_prefix="",
            max_manifest_bytes=max_manifest_bytes,
        ),
        repository=repository,
        object_store=store,
    )
    result = producer.produce(
        source_id=source_id, cycle_time=cycle_text, model_id="demo_model"
    )
    return result, repository, store


def _station_csv_lines(
    store: LocalObjectStore, result: Any, filename: str
) -> list[str]:
    contents = store.read_bytes(
        f"{result.forcing_package_uri.strip('/')}/shud/{filename}"
    ).decode("utf-8")
    return contents.splitlines()


# --- spec 场景: 合成 canonical 到 forcing 包 ----------------------------------


def test_direct_grid_materializes_two_bound_stations_with_wind_5_10(
    tmp_path: Path,
) -> None:
    result, repository, store = _produce_for_cycle(tmp_path, cycle_text=CYCLE_00Z)

    assert result.status == "forcing_ready"
    assert result.station_count == 2
    # exactly two stations, one mapping per station/variable at weight 1.0
    weights = repository.load_interp_weights(
        source_id="gfs", grid_id=GFS_GRID_ID, model_id="demo_model"
    )
    assert len(weights) == 2 * len(FORCING_VARIABLES)
    for weight in weights:
        assert weight.method == "direct_grid"
        assert weight.weight == 1.0
    station_grid_cells = {weight.station_id: weight.grid_cell_id for weight in weights}
    assert set(station_grid_cells.values()) == {"0", "1"}

    csv_a = _station_csv_lines(store, result, "X1.csv")
    csv_b = _station_csv_lines(store, result, "X2.csv")
    assert csv_a == [
        "1\t6\t20260507\t20260507",
        "Time_Day\tPrecip\tTemp\tRH\tWind\tRN",
        "0\t1\t10\t0.5\t5\t100",
    ]
    assert csv_b == [
        "1\t6\t20260507\t20260507",
        "Time_Day\tPrecip\tTemp\tRH\tWind\tRN",
        "0\t2\t20\t0.75\t10\t200",
    ]
    # unbound extra cell 2 is neither read nor output
    assert all("999" not in line for line in csv_a + csv_b)
    # Press stays in the package variable_set but not in the SHUD CSV
    manifest = json.loads(
        store.read_bytes(
            f"{result.forcing_package_uri.strip('/')}/forcing_package.json"
        ).decode("utf-8")
    )
    assert "Press" in manifest["variable_set"]
    assert "Press" not in csv_a[1]


def test_package_manifest_lineage_records_contract_signature_only(
    tmp_path: Path,
) -> None:
    result, _, store = _produce_for_cycle(tmp_path, cycle_text=CYCLE_00Z)

    manifest = json.loads(
        store.read_bytes(
            f"{result.forcing_package_uri.strip('/')}/forcing_package.json"
        ).decode("utf-8")
    )
    lineage = manifest["lineage"]
    assert lineage["contract_grid_signature"] == DEFAULT_GRID_SIGNATURE
    assert "grid_signature" not in lineage
    assert "validated_grid_signature" not in lineage
    assert lineage["forcing_mapping_mode"] == "direct_grid"
    assert lineage["grid_id"] == GFS_GRID_ID


# --- spec 场景: config 构造契约（必填无回退） ---------------------------------


def test_config_requires_three_paths_and_never_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError):
        ForcingProducerConfig(workspace_root=tmp_path)
    with pytest.raises(TypeError):
        ForcingProducerConfig(workspace_root=tmp_path, object_store_root=tmp_path)
    with pytest.raises(TypeError):
        ForcingProducerConfig(workspace_root=tmp_path, object_store_prefix="")
    # empty object_store_root must NOT fall back to workspace_root
    config = ForcingProducerConfig(
        workspace_root=tmp_path,
        object_store_root="",
        object_store_prefix="",
    )
    assert config.object_store_root == ""
    assert config.workspace_root == tmp_path
    # The lazy constructor must preserve the same explicit empty root; it must
    # not replace it with workspace_root before constructing LocalObjectStore.
    observed_roots: list[Path | str] = []

    class ObservedLocalObjectStore:
        def __init__(self, root: Path | str, *, object_store_prefix: str) -> None:
            observed_roots.append(root)

    monkeypatch.setattr(
        "yd_producer.forcing.producer.LocalObjectStore", ObservedLocalObjectStore
    )
    producer = ForcingProducer(config=config)
    assert observed_roots == [""]
    assert producer.object_store is not None
    # five versioned limits preserved as literals, min_lead_hours default None
    assert config.max_station_count == 10_000
    assert config.max_timestep_count == 10_000
    assert config.max_grid_cell_count == 5_000_000
    assert config.max_timeseries_row_count == 10_000_000
    assert config.max_manifest_bytes == 33_554_432
    assert config.min_lead_hours is None


# --- spec 场景: 时间零点锚定 cycle --------------------------------------------


def test_time_day_zero_is_explicit_cycle_00z_and_12z(tmp_path: Path) -> None:
    for cycle_text in (CYCLE_00Z, CYCLE_12Z):
        result, _, store = _produce_for_cycle(tmp_path, cycle_text=cycle_text)
        csv_lines = _station_csv_lines(store, result, "X1.csv")
        first_col = csv_lines[2].split("\t")[0]
        assert first_col == "0", (
            f"cycle {cycle_text} first Time_Day must be 0, got {first_col}"
        )


# --- spec 场景: source-specific binding 隔离 ----------------------------------


def test_source_binding_isolation_gfs_and_ifs_own_cells(tmp_path: Path) -> None:
    gfs_result, gfs_repo, _ = _produce_for_cycle(
        tmp_path / "gfs",
        cycle_text=CYCLE_00Z,
        source_id="gfs",
        grid_id=GFS_GRID_ID,
        cell_ids=("0",),
    )
    ifs_signature = "af8a8ba9dd27b07d330b81208984fab5503eb49f14a9419c1ee1e4e3ef4fc2e2"
    ifs_result, ifs_repo, ifs_store = _produce_for_cycle(
        tmp_path / "ifs",
        cycle_text=CYCLE_12Z,
        source_id="ifs",
        grid_id=IFS_GRID_ID,
        grid_signature=ifs_signature,
        cell_ids=("7",),
        netcdf_cell_ids=("7", "8", "9"),
    )
    assert gfs_result.station_count == 1
    assert ifs_result.station_count == 1
    ifs_manifest = json.loads(
        ifs_store.read_bytes(
            f"{ifs_result.forcing_package_uri.strip('/')}/forcing_package.json"
        ).decode("utf-8")
    )
    assert "surface_pressure" in {
        product.variable
        for product in ifs_repo.list_canonical_products(
            source_id="ifs", cycle_time=parse_cycle_time(CYCLE_12Z)
        )
    }
    assert ifs_manifest["variable_set"] == list(FORCING_VARIABLES)
    gfs_weights = gfs_repo.load_interp_weights(
        source_id="gfs", grid_id=GFS_GRID_ID, model_id="demo_model"
    )
    ifs_weights = ifs_repo.load_interp_weights(
        source_id="ifs", grid_id=IFS_GRID_ID, model_id="demo_model"
    )
    gfs_cells = {w.grid_cell_id for w in gfs_weights}
    ifs_cells = {w.grid_cell_id for w in ifs_weights}
    assert gfs_cells == {"0"}
    assert ifs_cells == {"7"}
    assert gfs_weights[0].grid_id == GFS_GRID_ID
    assert ifs_weights[0].grid_id == IFS_GRID_ID


def test_contract_with_unknown_source_fails_closed(tmp_path: Path) -> None:
    seed = direct_grid_binding(applicable_source_ids=("ERA5",))
    with pytest.raises(DirectGridContractError, match="unsupported source"):
        _contract(seed)


def test_ifs_12z_without_cycle_row_fails_closed(tmp_path: Path) -> None:
    """IFS T+3/T+6 catalog reaches the formatter and rejects a missing cycle row."""
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding(
        grid_id=IFS_GRID_ID,
        grid_signature="af8a8ba9dd27b07d330b81208984fab5503eb49f14a9419c1ee1e4e3ef4fc2e2",
        applicable_source_ids=("IFS",),
        cell_ids=("7",),
    )
    repository, _ = _file_repository(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="ifs",
        cycle_text=CYCLE_12Z,
        grid_id=IFS_GRID_ID,
        grid_signature="af8a8ba9dd27b07d330b81208984fab5503eb49f14a9419c1ee1e4e3ef4fc2e2",
        cell_ids=("7", "8", "9"),
        forecast_hours=(3, 6),
    )
    producer = ForcingProducer(
        config=ForcingProducerConfig(
            workspace_root=tmp_path,
            object_store_root=tmp_path,
            object_store_prefix="",
        ),
        repository=repository,
        object_store=store,
    )

    with pytest.raises(
        ForcingProductionError,
        match="Forcing Time_Day=0 must be the explicit cycle time",
    ):
        producer.produce(source_id="ifs", cycle_time=CYCLE_12Z, model_id="demo_model")

    assert (
        repository.get_forcing_version(
            source_id="ifs",
            cycle_time=parse_cycle_time(CYCLE_12Z),
            model_id="demo_model",
        )
        is None
    )
    assert not (tmp_path / "forcing").exists()


# --- spec 场景: 绑定格点缺失或身份不匹配 ---------------------------------------


def _rebuild_with_missing_cell(tmp_path: Path) -> None:
    seed = direct_grid_binding()
    seed["station_bindings"][1]["grid_cell_id"] = "missing-cell"
    store = LocalObjectStore(tmp_path / "m")
    repository, _ = _file_repository(tmp_path / "m", seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )
    producer = ForcingProducer(
        config=ForcingProducerConfig(
            workspace_root=tmp_path / "m",
            object_store_root=tmp_path / "m",
            object_store_prefix="",
        ),
        repository=repository,
        object_store=store,
    )
    with pytest.raises(
        ForcingProductionError,
        match="missing required interpolation grid cells: missing-cell",
    ):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")
    assert not (tmp_path / "m" / "forcing").exists()


def test_missing_cell_fails_closed(tmp_path: Path) -> None:
    _rebuild_with_missing_cell(tmp_path)


def test_mismatched_grid_signature_fails_closed(tmp_path: Path) -> None:
    seed = direct_grid_binding(grid_signature="deadbeef")
    store = LocalObjectStore(tmp_path / "sig")
    repository, _ = _file_repository(tmp_path / "sig", seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )
    producer = ForcingProducer(
        config=ForcingProducerConfig(
            workspace_root=tmp_path / "sig",
            object_store_root=tmp_path / "sig",
            object_store_prefix="",
        ),
        repository=repository,
        object_store=store,
    )
    with pytest.raises(ForcingProductionError, match="grid_signature mismatch"):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")
    assert not (tmp_path / "sig" / "forcing").exists()


def test_symlinked_canonical_product_fails_closed(tmp_path: Path) -> None:
    """canonical NetCDF leaf 是 symlink 时必须 fail closed，且不读根外字节。

    判别 descriptor-bound 实现：改回裸 Path（`os.open(path)` 跟随 symlink）时，
    produce 会成功读取 store 外的伪造值并产出 ready——本用例随即变红。
    Fake repository 的内存 products 不触发 object-store checksum 的 no-follow，
    使本用例只切中 `open_canonical_netcdf` 的打开路径。
    """
    import os

    from test_forcing_producer import (
        _build_producer,
        _build_repository,
        _direct_grid_manifest_for_default_grid,
        _write_canonical_products,
    )

    store = LocalObjectStore(tmp_path)
    contract = parse_direct_grid_forcing_contract(
        _direct_grid_manifest_for_default_grid(), source_id="GFS"
    )
    _, repository = _build_repository(
        tmp_path,
        forcing_mapping_contract=contract,
        direct_grid_validation_assets={
            "binding_checksum": contract.binding_checksum.removeprefix("sha256:"),
            "model_input_package_id": contract.model_input_package_id,
            "sp_att_checksum": contract.sp_att_checksum.removeprefix("sha256:"),
            "sp_att_content": sp_att_content(),
        },
    )
    _write_canonical_products(store)
    # Replace one canonical NetCDF leaf with a symlink to an outside file.
    product_key = (
        "canonical/gfs/2026050700/air_temperature_2m/"
        "gfs_2026050700_air_temperature_2m_f000.nc"
    )
    store.delete(product_key)
    outside = tmp_path.parent / "hostile.nc"
    import xarray as xr

    dataset = xr.Dataset(
        data_vars={"air_temperature_2m": ("point", [99.0, 99.0, 99.0])},
        coords={
            "point": ["0", "1", "2"],
            "longitude": ("point", [-75.0, -74.5, -74.0]),
            "latitude": ("point", [40.0, 40.2, 40.4]),
        },
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".nc") as temp_file:
            dataset.to_netcdf(temp_file.name, engine="netcdf4", format="NETCDF4")
            temp_file.seek(0)
            outside.write_bytes(temp_file.read())
    finally:
        dataset.close()
    leaf = store.resolve_path(product_key)
    leaf.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, leaf)

    producer = _build_producer(tmp_path, repository, store)
    with pytest.raises(ForcingProductionError):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")
    assert not (tmp_path / "forcing").exists()
