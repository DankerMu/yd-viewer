# NWM@8ae9b8f2 tests/test_forcing_producer.py
"""Extractive snapshot of NWM forcing-producer tests (issue #14).

Retained seeds: 4 file-repository tests + 9 direct-grid tests plus the fake
repository / builder closure. Registry/bbox/snapshot-projection surfaces,
IDW coverage, handoff parser coverage, and DB-backed store coverage are
deliberately out of the snapshot (inventory row 53 剥离点); the yd-authored
acceptance suite lives in test_forcing_producer_yd.py.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import tempfile
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from forcing_seed_assets import (
    _direct_grid_manifest,
    _direct_grid_manifest_for_default_grid,
    _direct_grid_validation_assets,
    _file_direct_grid_repository,
    _netcdf_bytes,
    _sp_att_content,
    _write_canonical_products,
)
from forcing_seed_builders import (
    _assert_direct_grid_failure_without_idw_or_ready_outputs,
    _assert_direct_grid_package_contract,
    _assert_direct_grid_value_failure_without_ready_outputs,
    _build_direct_grid_repository,
    _build_producer,
    _build_repository,
)
from forcing_seed_repository import (
    DIRECT_GRID_CACHE_STATION_ROLE,
    FakeForcingRepository,
    _direct_grid_mirror_identity,
    _is_legacy_loadable_station,
    _lead_time_sort_key,
    _same_direct_grid_mirror,
)

from yd_producer.forcing import (
    CanonicalProduct,
    DirectGridContractError,
    ForcingProducer,
    ForcingProducerConfig,
    ForcingProductionError,
    GridPoint,
    InterpolationWeight,
    MetStation,
    load_forcing_mapping_contract_from_manifest,
    parse_cycle_time,
    parse_direct_grid_forcing_contract,
    wind_speed,
)
from yd_producer.forcing.direct_grid_contract import (
    MAX_DIRECT_GRID_STATION_BINDINGS,
    REQUIRED_MANIFEST_FIELDS,
    REQUIRED_STATION_FIELDS,
)
from yd_producer.forcing.file_store import FileForcingRepository, ForcingStoreError
from yd_producer.forcing.producer import (
    EXPECTED_CANONICAL_UNITS,
    FORCING_VARIABLES,
    OUTPUT_UNITS,
    ForcingComponent,
    ForcingTimeseriesRow,
    format_shud_forcing_package,
)
from yd_producer.forcing.shud_forcing_contract import (
    CANONICAL_SHUD_FORCING_INDEX_BASENAME,
    CANONICAL_SHUD_FORCING_INDEX_MEMBER,
    SHUD_FORCING_ROLE,
)
from yd_producer.store.object_store import LocalObjectStore, sha256_bytes


def test_file_forcing_repository_reads_model_registry_stations_and_canonical_attrs(
    tmp_path: Path,
) -> None:
    xr = pytest.importorskip("xarray")
    store = LocalObjectStore(tmp_path, object_store_prefix="s3://nhms")
    model_root = tmp_path / "models" / "basins_qhh_shud" / "v1"
    package_root = model_root / "package"
    package_root.mkdir(parents=True)
    (package_root / "qhh.tsd.forc").write_text(
        "\n".join(
            [
                "2 19790101",
                "/legacy/qhh/forcing",
                "ID\tLon\tLat\tX\tY\tZ\tFilename",
                "1\t100.95\t36.25\t0\t0\t-9999\tX100.95Y36.25.csv",
                "2\t101.05\t36.25\t0\t0\t3375\tX101.05Y36.25.csv",
            ]
        ),
        encoding="utf-8",
    )
    (model_root / "manifest.json").write_text(
        json.dumps({"basin_slug": "qhh"}), encoding="utf-8"
    )
    registry_content = json.dumps(
        {
            "models": [
                {
                    "model_id": "basins_qhh_shud",
                    "basin_id": "basins_qhh",
                    "basin_version_id": "basins_qhh_vbasins",
                    "river_network_version_id": "basins_qhh_rivnet_vbasins",
                    "manifest_uri": "models/basins_qhh_shud/v1/manifest.json",
                    "model_package_uri": "models/basins_qhh_shud/v1/package/",
                    "resource_profile": {"shud_input_name": "qhh"},
                }
            ]
        }
    ).encode("utf-8")
    store.write_bytes_atomic("models/demo/registry.json", registry_content)
    cycle_time = datetime(2026, 6, 21, 18, tzinfo=UTC)
    product_path = (
        tmp_path
        / "canonical"
        / "gfs"
        / "2026062118"
        / "air_temperature_2m"
        / "gfs_2026062118_air_temperature_2m_f003.nc"
    )
    product_path.parent.mkdir(parents=True)
    dataset = xr.Dataset(
        data_vars={"air_temperature_2m": ("point", [12.5])},
        coords={"point": [0]},
        attrs={
            "cycle_time": cycle_time.isoformat(),
            "valid_time": "2026-06-21T21:00:00+00:00",
            "lead_time_hours": 3,
            "unit": "degC",
            "grid_id": "gfs_0p25",
            "lineage_json": json.dumps(
                {
                    "policy_identity": {"source": "gfs"},
                    "source_object_identity": {
                        "manifest": "raw/gfs/2026062118/manifest.json"
                    },
                },
                sort_keys=True,
            ),
        },
    )
    try:
        dataset.to_netcdf(product_path)
    finally:
        dataset.close()

    store.write_bytes_atomic(
        "canonical/gfs/2026062118/_catalog/catalog.json",
        json.dumps(
            {
                "schema_version": "nhms.canonical.product_catalog.v1",
                "source_id": "gfs",
                "cycle_time": "2026-06-21T18:00:00Z",
                "products": [
                    {
                        "canonical_product_id": "gfs_2026062118_air_temperature_2m_f003",
                        "source_id": "gfs",
                        "source_version": "2026062118",
                        "cycle_time": "2026-06-21T18:00:00Z",
                        "valid_time": "2026-06-21T21:00:00Z",
                        "lead_time_hours": 3,
                        "variable": "air_temperature_2m",
                        "unit": "degC",
                        "grid_id": "gfs_0p25",
                        "grid_definition_uri": "canonical/gfs/grid/gfs_0p25/grid.json",
                        "native_time_resolution": "3h",
                        "native_spatial_resolution": "0.25deg",
                        "object_uri": (
                            "s3://nhms/canonical/gfs/2026062118/air_temperature_2m/"
                            "gfs_2026062118_air_temperature_2m_f003.nc"
                        ),
                        "checksum": sha256_bytes(product_path.read_bytes()),
                        "quality_flag": "ok",
                        "lineage_json": {"policy_identity": {"source": "gfs"}},
                    }
                ],
            }
        ).encode("utf-8"),
    )
    repository = FileForcingRepository(
        object_store=store, registry_manifest="models/demo/registry.json"
    )

    assert repository.resolve_model_identity(model_id="basins_qhh_shud") == {
        "basin_id": "basins_qhh",
        "basin_version_id": "basins_qhh_vbasins",
        "river_network_version_id": "basins_qhh_rivnet_vbasins",
    }
    stations = repository.load_met_stations(basin_version_id="basins_qhh_vbasins")
    assert [station.station_id for station in stations] == [
        "qhh_forc_001",
        "qhh_forc_002",
    ]
    assert stations[0].elevation_m == 0.0
    assert stations[1].properties_json["forcing_filename"] == "X101.05Y36.25.csv"

    products = repository.list_canonical_products(
        source_id="gfs", cycle_time=cycle_time
    )
    assert len(products) == 1
    product = products[0]
    assert product.canonical_product_id == "gfs_2026062118_air_temperature_2m_f003"
    assert product.valid_time == datetime(2026, 6, 21, 21, tzinfo=UTC)
    assert product.lead_time_hours == 3
    assert product.object_uri == (
        "s3://nhms/canonical/gfs/2026062118/air_temperature_2m/"
        "gfs_2026062118_air_temperature_2m_f003.nc"
    )
    assert product.lineage_json["policy_identity"] == {"source": "gfs"}


def test_file_forcing_repository_loads_direct_grid_assets_and_snapshot_projection(
    tmp_path: Path,
) -> None:
    repository, contract, binding_content, sp_att_content = (
        _file_direct_grid_repository(tmp_path)
    )

    assets = repository.load_direct_grid_validation_assets(
        model_id="demo_model",
        basin_version_id="basin_v1",
        contract=contract,
        max_bytes=33_554_432,
    )

    assert assets == {
        "binding_checksum": sha256_bytes(binding_content),
        "model_input_package_id": contract.model_input_package_id,
        "sp_att_checksum": sha256_bytes(sp_att_content),
        "sp_att_content": sp_att_content.decode("utf-8"),
    }


def test_file_forcing_repository_rejects_unsafe_direct_grid_sp_att_path(
    tmp_path: Path,
) -> None:
    repository, contract, _, _ = _file_direct_grid_repository(tmp_path)
    unsafe_contract = dataclasses.replace(
        contract, sp_att_path="../baseline/demo.sp.att"
    )
    repository._registry_cache = {
        "models": [
            {
                **repository._registry_models()[0],
                "resource_profile": {
                    **repository._registry_models()[0]["resource_profile"],
                    "direct_grid_forcing": {
                        **repository._registry_models()[0]["resource_profile"][
                            "direct_grid_forcing"
                        ],
                        "sp_att_path": "../baseline/demo.sp.att",
                    },
                },
            }
        ]
    }

    with pytest.raises(DirectGridContractError, match="model-package-relative"):
        repository.load_direct_grid_validation_assets(
            model_id="demo_model",
            basin_version_id="basin_v1",
            contract=unsafe_contract,
            max_bytes=33_554_432,
        )


def test_file_forcing_repository_prefers_canonical_product_catalog(
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path, object_store_prefix="s3://nhms")
    store.write_bytes_atomic(
        "canonical/gfs/2026062118/_catalog/catalog.json",
        json.dumps(
            {
                "schema_version": "nhms.canonical.product_catalog.v1",
                "source_id": "gfs",
                "cycle_time": "2026-06-21T18:00:00Z",
                "products": [
                    {
                        "canonical_product_id": "gfs_2026062118_air_temperature_2m_f003",
                        "source_id": "gfs",
                        "source_version": "2026062118",
                        "cycle_time": "2026-06-21T18:00:00Z",
                        "valid_time": "2026-06-21T21:00:00Z",
                        "lead_time_hours": 3,
                        "variable": "air_temperature_2m",
                        "unit": "degC",
                        "grid_id": "gfs_0p25",
                        "grid_definition_uri": "canonical/gfs/grid/gfs_0p25/grid.json",
                        "native_time_resolution": "3h",
                        "native_spatial_resolution": "0.25deg",
                        "object_uri": (
                            "s3://nhms/canonical/gfs/2026062118/air_temperature_2m/"
                            "gfs_2026062118_air_temperature_2m_f003.nc"
                        ),
                        "checksum": "abc123",
                        "quality_flag": "ok",
                        "lineage_json": {"policy_identity": {"source": "gfs"}},
                    }
                ],
            }
        ).encode("utf-8"),
    )
    store.write_bytes_atomic(
        "models/demo/registry.json",
        json.dumps({"models": []}).encode("utf-8"),
    )
    repository = FileForcingRepository(
        object_store=store, registry_manifest="models/demo/registry.json"
    )

    products = repository.list_canonical_products(
        source_id="gfs",
        cycle_time=datetime(2026, 6, 21, 18, tzinfo=UTC),
    )

    assert len(products) == 1
    assert products[0].checksum == "abc123"
    assert products[0].lineage_json["policy_identity"] == {"source": "gfs"}


def test_producer_direct_grid_materializes_exact_mappings_and_writes_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = parse_direct_grid_forcing_contract(
        _direct_grid_manifest_for_default_grid(), source_id="GFS"
    )
    store, repository = _build_direct_grid_repository(tmp_path, contract=contract)
    producer = _build_producer(tmp_path, repository, store)

    result = producer.produce(
        source_id="gfs", cycle_time="2026050700", model_id="demo_model"
    )

    assert result.status == "forcing_ready"
    assert repository.mapping_contract_calls == [
        {"model_id": "demo_model", "basin_version_id": "basin_v1", "source_id": "gfs"}
    ]
    assert repository.load_station_count == 0
    assert repository.load_weight_count == 0
    assert repository.direct_grid_station_ensure_count == 1
    assert repository.direct_grid_station_ensure_calls == [
        {
            "basin_version_id": "basin_v1",
            "station_ids": tuple(station.station_id for station in contract.stations),
            "grid_cell_ids": tuple(
                station.grid_cell_id for station in contract.stations
            ),
        }
    ]
    assert {station.station_id for station in contract.stations}.issubset(
        repository.met_station_ids
    )
    assert repository.interp_weight_upsert_count == 1
    assert len(repository.interp_weights) == len(contract.stations) * len(
        FORCING_VARIABLES
    )
    assert {
        (
            weight.source_id,
            weight.grid_id,
            weight.model_id,
            weight.station_id,
            weight.variable,
            weight.grid_cell_id,
            weight.method,
            weight.weight,
        )
        for weight in repository.interp_weights
    } == {
        (
            "gfs",
            "grid_a",
            "demo_model",
            station.station_id,
            variable,
            station.grid_cell_id,
            "direct_grid",
            1.0,
        )
        for station in contract.stations
        for variable in FORCING_VARIABLES
    }
    assert (
        repository.forcing_versions[result.forcing_version_id]["checksum"]
        == result.checksum
    )
    assert len(repository.components) == len(repository.products)
    assert (
        len(repository.timeseries)
        == len(contract.stations) * len(FORCING_VARIABLES) * result.timestep_count
    )
    assert {row.variable for row in repository.timeseries} == set(FORCING_VARIABLES)
    assert repository.upsert_count == 1
    assert repository.events[:4] == [
        ("upsert_forcing_version", None),
        ("replace_forcing_components", result.forcing_version_id),
        ("replace_forcing_timeseries", result.forcing_version_id),
        ("finalize_forcing_version", result.checksum),
    ]
    assert repository.cycle_updates[-1]["status"] == "forcing_ready"
    _assert_direct_grid_package_contract(tmp_path, result, repository, contract)


def test_producer_direct_grid_rows_equal_bound_canonical_grid_cell_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalObjectStore(tmp_path)
    products = _write_canonical_products(
        store,
        forecast_hours=(0, 3),
        values_by_variable={
            "prcp_rate_or_amount": (1.0, 2.0, 999.0),
            "air_temperature_2m": (10.0, 20.0, 999.0),
            "relative_humidity_2m": (0.50, 0.75, 999.0),
            "shortwave_down": (100.0, 200.0, 999.0),
            "wind_u_10m": (3.0, 6.0, 999.0),
            "wind_v_10m": (4.0, 8.0, 999.0),
            "pressure_surface": (101000.0, 102000.0, 999.0),
        },
    )
    contract = parse_direct_grid_forcing_contract(
        _direct_grid_manifest_for_default_grid(), source_id="GFS"
    )
    repository = FakeForcingRepository(
        stations=(),
        products=products,
        forcing_mapping_contract=contract,
        direct_grid_validation_assets=_direct_grid_validation_assets(),
    )
    producer = _build_producer(tmp_path, repository, store)

    result = producer.produce(
        source_id="gfs", cycle_time="2026050700", model_id="demo_model"
    )

    rows = tuple(repository.timeseries)
    components = tuple(repository.components)
    planned_valid_time = parse_cycle_time("2026050700")
    assert result.status == "forcing_ready"
    assert {row.valid_time for row in rows} == {planned_valid_time}
    values = {(row.station_id, row.variable): row.value for row in rows}
    assert values[("qhh_forc_001", "PRCP")] == pytest.approx(1.0)
    assert values[("qhh_forc_001", "TEMP")] == pytest.approx(10.0)
    assert values[("qhh_forc_001", "RH")] == pytest.approx(0.50)
    assert values[("qhh_forc_001", "Rn")] == pytest.approx(100.0)
    assert values[("qhh_forc_001", "wind")] == pytest.approx(5.0)
    assert values[("qhh_forc_002", "PRCP")] == pytest.approx(2.0)
    assert values[("qhh_forc_002", "TEMP")] == pytest.approx(20.0)
    assert values[("qhh_forc_002", "RH")] == pytest.approx(0.75)
    assert values[("qhh_forc_002", "Rn")] == pytest.approx(200.0)
    assert values[("qhh_forc_002", "wind")] == pytest.approx(10.0)
    assert {row.station_id for row in rows} == {"qhh_forc_001", "qhh_forc_002"}
    assert {row.variable for row in rows} == set(FORCING_VARIABLES)
    assert {component.canonical_product_id for component in components} == {
        product.canonical_product_id for product in products
    }
    assert {component.variable for component in components} == {
        product.variable for product in products
    }
    assert repository.load_station_count == 0
    assert repository.load_weight_count == 0
    assert (
        repository.forcing_versions[result.forcing_version_id]["checksum"]
        == result.checksum
    )
    assert repository.upsert_count == 1
    assert (
        tmp_path / result.forcing_package_uri.strip("/") / "forcing_package.json"
    ).exists()
    assert repository.cycle_updates[-1]["status"] == "forcing_ready"


def test_producer_direct_grid_reads_only_required_bound_grid_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = parse_direct_grid_forcing_contract(
        _direct_grid_manifest_for_default_grid(), source_id="GFS"
    )
    store, repository = _build_repository(
        tmp_path,
        forcing_mapping_contract=contract,
        direct_grid_validation_assets=_direct_grid_validation_assets(),
        values_by_variable={
            "air_temperature_2m": (10.0, 11.0, math.nan),
            "relative_humidity_2m": (0.50, 0.75, math.nan),
            "wind_u_10m": (3.0, 6.0, math.nan),
            "wind_v_10m": (4.0, 8.0, math.nan),
            "pressure_surface": (101000.0, 102000.0, math.nan),
            "prcp_rate_or_amount": (1.0, 2.0, math.nan),
            "shortwave_down": (100.0, 200.0, math.nan),
        },
    )
    producer = _build_producer(tmp_path, repository, store)
    original_read = producer._read_canonical_field
    read_proof: list[tuple[str, frozenset[str] | None, tuple[str, ...], bool]] = []

    def capture_read(*args: Any, **kwargs: Any) -> Any:
        field = original_read(*args, **kwargs)
        product = args[0]
        read_proof.append(
            (
                product.variable,
                kwargs.get("required_grid_cell_ids"),
                tuple(sorted(field.values_by_grid_cell_id)),
                kwargs.get("validate_all_values", True),
            )
        )
        return field

    monkeypatch.setattr(producer, "_read_canonical_field", capture_read)

    result = producer.produce(
        source_id="gfs", cycle_time="2026050700", model_id="demo_model"
    )

    assert result.status == "forcing_ready"
    assert read_proof
    assert {required for _, required, _, _ in read_proof} == {frozenset({"0", "1"})}
    assert {retained for _, _, retained, _ in read_proof} == {("0", "1")}
    assert {validate_all for _, _, _, validate_all in read_proof} == {False}


def test_producer_direct_grid_missing_bound_grid_cell_fails_before_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _direct_grid_manifest_for_default_grid()
    manifest["station_bindings"][1]["grid_cell_id"] = "missing-cell"
    contract = parse_direct_grid_forcing_contract(manifest, source_id="GFS")
    store, repository = _build_repository(
        tmp_path,
        forcing_mapping_contract=contract,
        direct_grid_validation_assets=_direct_grid_validation_assets(),
    )
    producer = _build_producer(tmp_path, repository, store)

    with pytest.raises(
        ForcingProductionError,
        match="missing required interpolation grid cells: missing-cell",
    ):
        producer.produce(
            source_id="gfs", cycle_time="2026050700", model_id="demo_model"
        )

    _assert_direct_grid_value_failure_without_ready_outputs(repository, tmp_path)


def test_producer_direct_grid_non_finite_bound_canonical_value_fails_before_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalObjectStore(tmp_path)
    products = _write_canonical_products(
        store,
        forecast_hours=(0, 3),
        values_by_variable={
            "air_temperature_2m": (10.0, math.nan, 999.0),
            "relative_humidity_2m": (0.50, 0.75, 999.0),
            "wind_u_10m": (3.0, 6.0, 999.0),
            "wind_v_10m": (4.0, 8.0, 999.0),
            "pressure_surface": (101000.0, 102000.0, 999.0),
            "prcp_rate_or_amount": (1.0, 2.0, 999.0),
            "shortwave_down": (100.0, 200.0, 999.0),
        },
    )
    contract = parse_direct_grid_forcing_contract(
        _direct_grid_manifest_for_default_grid(), source_id="GFS"
    )
    repository = FakeForcingRepository(
        stations=(),
        products=products,
        forcing_mapping_contract=contract,
        direct_grid_validation_assets=_direct_grid_validation_assets(),
    )
    producer = _build_producer(tmp_path, repository, store)

    with pytest.raises(
        ForcingProductionError, match="non-finite field value for grid cell 1"
    ):
        producer.produce(
            source_id="gfs", cycle_time="2026050700", model_id="demo_model"
        )

    _assert_direct_grid_value_failure_without_ready_outputs(repository, tmp_path)


def test_producer_rejects_root_direct_grid_manifest_before_station_loading(
    tmp_path: Path,
) -> None:
    class RootDirectGridManifestRepository(FakeForcingRepository):
        def load_forcing_mapping_contract(
            self,
            *,
            model_id: str,
            basin_version_id: str,
            source_id: str | None = None,
        ) -> Any:
            self.mapping_contract_calls.append(
                {
                    "model_id": model_id,
                    "basin_version_id": basin_version_id,
                    "source_id": source_id,
                }
            )
            return load_forcing_mapping_contract_from_manifest(
                _direct_grid_manifest(),
                source_id=source_id,
                allow_root_direct_grid=False,
            )

    store, repository = _build_repository(tmp_path)
    repository = RootDirectGridManifestRepository(
        stations=repository.stations, products=repository.products
    )
    producer = _build_producer(tmp_path, repository, store)

    with pytest.raises(
        ForcingProductionError, match="Invalid forcing mapping contract"
    ):
        producer.produce(
            source_id="gfs", cycle_time="2026050700", model_id="demo_model"
        )

    assert repository.mapping_contract_calls == [
        {"model_id": "demo_model", "basin_version_id": "basin_v1", "source_id": "gfs"}
    ]
    assert repository.load_station_count == 0
    assert repository.load_weight_count == 0
    assert repository.direct_grid_station_ensure_count == 0
    assert repository.interp_weights == []
    assert repository.forcing_versions == {}
    assert repository.components == []
    assert repository.timeseries == []
    assert repository.upsert_count == 0
    assert not any(
        event[0] == "finalize_forcing_version" for event in repository.events
    )
    assert not (tmp_path / "forcing").exists()
    assert repository.cycle_updates == []


@pytest.mark.parametrize(
    ("sp_att_content", "expected_actual"),
    [
        (_sp_att_content((0, 1)), "0"),
        (_sp_att_content((-1, 1)), "-1"),
        ("2 1\nTRI\tA\tB\tC\tFORC\n1\t0\t0\t0\n", "missing"),
        (_sp_att_content(("x", 1)), "x"),
        (_sp_att_content((3, 1)), "3"),
    ],
)
def test_producer_direct_grid_validation_sp_att_forc_invalid_cases_fail_before_idw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sp_att_content: str,
    expected_actual: str,
) -> None:
    contract = parse_direct_grid_forcing_contract(
        _direct_grid_manifest_for_default_grid(), source_id="GFS"
    )
    store, repository = _build_repository(
        tmp_path,
        forcing_mapping_contract=contract,
        direct_grid_validation_assets=_direct_grid_validation_assets(
            sp_att_content=sp_att_content
        ),
    )
    producer = _build_producer(tmp_path, repository, store)

    with pytest.raises(ForcingProductionError) as exc_info:
        producer.produce(
            source_id="gfs", cycle_time="2026050700", model_id="demo_model"
        )

    message = str(exc_info.value)
    assert "DIRECT_GRID_VALIDATION_FAILED" in message
    assert '"field":"sp_att.FORC"' in message
    assert expected_actual in message
    _assert_direct_grid_failure_without_idw_or_ready_outputs(repository, tmp_path)


def test_producer_direct_grid_validation_sp_att_forc_missing_bound_index_fails_before_idw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = parse_direct_grid_forcing_contract(
        _direct_grid_manifest_for_default_grid(), source_id="GFS"
    )
    store, repository = _build_repository(
        tmp_path,
        forcing_mapping_contract=contract,
        direct_grid_validation_assets=_direct_grid_validation_assets(
            sp_att_content=_sp_att_content((1, 1))
        ),
    )
    producer = _build_producer(tmp_path, repository, store)

    with pytest.raises(ForcingProductionError) as exc_info:
        producer.produce(
            source_id="gfs", cycle_time="2026050700", model_id="demo_model"
        )

    message = str(exc_info.value)
    assert "DIRECT_GRID_VALIDATION_FAILED" in message
    assert '"field":"sp_att.FORC"' in message
    assert '"expected":[1,2]' in message
    assert '"actual":[1]' in message
    assert '"missing_indexes":[2]' in message
    _assert_direct_grid_failure_without_idw_or_ready_outputs(repository, tmp_path)


def test_producer_direct_grid_fallback_oversized_binding_fails_before_idw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sp_att_content = _sp_att_content().encode("utf-8")
    manifest = _direct_grid_manifest_for_default_grid()
    manifest.update(
        {
            "binding_checksum": f"sha256:{sha256_bytes(b'expected')}",
            "sp_att_path": "models/demo_model/input/qhh.sp.att",
            "sp_att_checksum": f"sha256:{sha256_bytes(sp_att_content)}",
        }
    )
    contract = parse_direct_grid_forcing_contract(manifest, source_id="GFS")
    store, repository = _build_repository(tmp_path, forcing_mapping_contract=contract)
    store.write_bytes_atomic(contract.binding_uri, b"x" * 17)
    store.write_bytes_atomic(contract.sp_att_path, sp_att_content)

    class RepositoryWithoutValidationLoader:
        def __init__(self, wrapped: FakeForcingRepository) -> None:
            self._wrapped = wrapped

        def __getattr__(self, name: str) -> Any:
            if name == "load_direct_grid_validation_assets":
                raise AttributeError(name)
            return getattr(self._wrapped, name)

    producer = ForcingProducer(
        config=ForcingProducerConfig(
            workspace_root=tmp_path,
            object_store_root=tmp_path,
            object_store_prefix="",
            max_manifest_bytes=16,
        ),
        repository=RepositoryWithoutValidationLoader(repository),
        object_store=store,
    )

    with pytest.raises(ForcingProductionError) as exc_info:
        producer.produce(
            source_id="gfs", cycle_time="2026050700", model_id="demo_model"
        )

    message = str(exc_info.value)
    assert "DIRECT_GRID_VALIDATION_FAILED" in message
    assert '"field":"validation_assets"' in message
    assert "exceeds read limit" in message
    _assert_direct_grid_failure_without_idw_or_ready_outputs(repository, tmp_path)
