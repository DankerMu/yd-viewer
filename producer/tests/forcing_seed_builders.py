# NWM@8ae9b8f2 tests/test_forcing_producer.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from forcing_seed_assets import (
    _direct_grid_validation_assets,
    _write_canonical_products,
)
from forcing_seed_repository import FakeForcingRepository

from yd_producer.forcing import (
    ForcingProducer,
    ForcingProducerConfig,
    MetStation,
)
from yd_producer.forcing.shud_forcing_contract import (
    CANONICAL_SHUD_FORCING_INDEX_BASENAME,
)
from yd_producer.store.object_store import LocalObjectStore


def _build_producer(
    tmp_path: Path,
    repository: FakeForcingRepository,
    store: LocalObjectStore,
) -> ForcingProducer:
    config = ForcingProducerConfig(
        workspace_root=tmp_path,
        object_store_root=tmp_path,
        object_store_prefix="",
    )
    return ForcingProducer(config=config, repository=repository, object_store=store)


def _build_direct_grid_repository(
    tmp_path: Path,
    *,
    contract: Any,
    **repository_kwargs: Any,
) -> tuple[LocalObjectStore, FakeForcingRepository]:
    return _build_repository(
        tmp_path,
        stations=(),
        forcing_mapping_contract=contract,
        direct_grid_validation_assets=_direct_grid_validation_assets(
            binding_checksum=contract.binding_checksum.removeprefix("sha256:"),
            model_input_package_id=contract.model_input_package_id,
            sp_att_checksum=contract.sp_att_checksum.removeprefix("sha256:"),
        ),
        **repository_kwargs,
    )


def _assert_direct_grid_package_contract(
    tmp_path: Path,
    result: Any,
    repository: FakeForcingRepository,
    contract: Any,
) -> None:
    package_root = tmp_path / result.forcing_package_uri.strip("/")
    tsd_forc = (
        (package_root / "shud" / CANONICAL_SHUD_FORCING_INDEX_BASENAME)
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert tsd_forc[0] == f"{len(contract.stations)} 20260507"
    assert tsd_forc[2] == "ID\tLon\tLat\tX\tY\tZ\tFilename"
    tsd_rows = [line.split() for line in tsd_forc[3:]]
    assert [int(row[0]) for row in tsd_rows] == [
        station.shud_forcing_index for station in contract.stations
    ]
    assert [float(row[1]) for row in tsd_rows] == [
        pytest.approx(station.longitude) for station in contract.stations
    ]
    assert [float(row[2]) for row in tsd_rows] == [
        pytest.approx(station.latitude) for station in contract.stations
    ]
    assert [row[-1] for row in tsd_rows] == [
        station.forcing_filename for station in contract.stations
    ]

    for station in contract.stations:
        station_csv = (
            (package_root / "shud" / station.forcing_filename)
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assert station_csv[1].split("\t") == [
            "Time_Day",
            "Precip",
            "Temp",
            "RH",
            "Wind",
            "RN",
        ]
        assert "Press" not in station_csv[1]
    assert "Press" in {row.variable for row in repository.timeseries}

    manifest = json.loads(
        (package_root / "forcing_package.json").read_text(encoding="utf-8")
    )
    lineage = repository.forcing_versions[result.forcing_version_id]["lineage_json"]
    manifest_lineage = manifest["lineage"]
    for payload in (lineage, manifest_lineage):
        assert payload["forcing_mapping_mode"] == "direct_grid"
        assert payload["spatial_mapping_method"] == "direct_grid"
        assert payload["binding_uri"] == contract.binding_uri
        assert payload["binding_checksum"] == contract.binding_checksum
        assert payload["model_input_package_id"] == contract.model_input_package_id
        assert payload["sp_att_path"] == contract.sp_att_path
        assert payload["sp_att_checksum"] == contract.sp_att_checksum
        assert payload["applicable_source_ids"] == list(contract.applicable_source_ids)
        assert payload["grid_id"] == contract.grid_id
        assert payload["contract_grid_signature"] == contract.grid_signature
        assert payload["direct_grid_station_identity"]["station_ids"] == [
            station.station_id for station in contract.stations
        ]
        assert payload["canonical_input_signature"]["checksum"]
        assert payload["output_files"] == manifest["files"]
    assert lineage["forcing_package_manifest_uri"].endswith("/forcing_package.json")
    assert lineage["forcing_package_manifest_checksum"] == result.checksum
    assert {entry["role"] for entry in manifest["files"]} >= {
        "tsd_forc",
        "csv_debug",
        "shud_forcing",
        "shud_forcing_csv",
    }


def _assert_direct_grid_failure_without_idw_or_ready_outputs(
    repository: FakeForcingRepository,
    tmp_path: Path,
) -> None:
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
    assert repository.cycle_updates[-1]["status"] == "failed_forcing"
    assert repository.cycle_updates[-1]["error_code"] == "FORCING_FAILED"
    assert (
        "DIRECT_GRID_VALIDATION_FAILED" in repository.cycle_updates[-1]["error_message"]
    )


def _assert_direct_grid_value_failure_without_ready_outputs(
    repository: FakeForcingRepository,
    tmp_path: Path,
) -> None:
    assert repository.load_station_count == 0
    assert repository.load_weight_count == 0
    assert repository.direct_grid_station_ensure_count == 1
    assert repository.interp_weight_upsert_count == 1
    assert repository.forcing_versions == {}
    assert repository.components == []
    assert repository.timeseries == []
    assert repository.upsert_count == 0
    assert not any(
        event[0] == "finalize_forcing_version" for event in repository.events
    )
    assert not (tmp_path / "forcing").exists()
    assert repository.cycle_updates[-1]["status"] == "failed_forcing"
    assert repository.cycle_updates[-1]["error_code"] == "FORCING_FAILED"


def _build_repository(
    tmp_path: Path,
    *,
    source_id: str = "gfs",
    omitted_variables: set[str] | None = None,
    omitted_by_time: set[tuple[str, int]] | None = None,
    stations: tuple[MetStation, ...] | None = None,
    forcing_mapping_manifest: Mapping[str, Any] | None = None,
    forcing_mapping_contract: Any = None,
    forcing_mapping_contract_error: Exception | None = None,
    direct_grid_validation_assets: Mapping[str, Any] | None = None,
    fail_next_forcing_version_upsert: bool = False,
    fail_next_component_replace: bool = False,
    fail_next_timeseries_replace: bool = False,
    fail_next_finalize: bool = False,
    fail_next_cycle_ready_update: bool = False,
    fail_next_interp_weight_upsert: bool = False,
    fail_next_direct_grid_station_ensure: bool = False,
    include_geographic_coords: bool = True,
    values_by_variable: Mapping[str, tuple[float, float, float]] | None = None,
    radiation_variable: str = "shortwave_down",
    longitudes: tuple[float, float, float] = (-75.0, -74.5, -74.0),
    latitudes: tuple[float, float, float] = (40.0, 40.2, 40.4),
) -> tuple[LocalObjectStore, FakeForcingRepository]:
    store = LocalObjectStore(tmp_path)
    forecast_hours = (0, 3, 6) if source_id == "gfs" else (0, 3)
    products = _write_canonical_products(
        store,
        source_id=source_id,
        forecast_hours=forecast_hours,
        omitted_variables=omitted_variables,
        omitted_by_time=omitted_by_time or set(),
        include_geographic_coords=include_geographic_coords,
        values_by_variable=values_by_variable,
        radiation_variable=radiation_variable,
        longitudes=longitudes,
        latitudes=latitudes,
    )
    repository = FakeForcingRepository(
        stations=stations
        if stations is not None
        else (
            MetStation(
                "station_1",
                "basin_v1",
                -74.7,
                40.1,
                50.0,
                "forcing_grid",
                properties_json={
                    "shud_forcing_index": 1,
                    "forcing_filename": "station_1.csv",
                },
            ),
        ),
        products=products,
        forcing_mapping_manifest=forcing_mapping_manifest,
        forcing_mapping_contract=forcing_mapping_contract,
        forcing_mapping_contract_error=forcing_mapping_contract_error,
        direct_grid_validation_assets=direct_grid_validation_assets,
        fail_next_forcing_version_upsert=fail_next_forcing_version_upsert,
        fail_next_component_replace=fail_next_component_replace,
        fail_next_timeseries_replace=fail_next_timeseries_replace,
        fail_next_finalize=fail_next_finalize,
        fail_next_cycle_ready_update=fail_next_cycle_ready_update,
        fail_next_interp_weight_upsert=fail_next_interp_weight_upsert,
        fail_next_direct_grid_station_ensure=fail_next_direct_grid_station_ensure,
    )
    return store, repository
