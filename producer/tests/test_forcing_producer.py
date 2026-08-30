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

DIRECT_GRID_CACHE_STATION_ROLE = "direct_grid_cache"


class FakeForcingRepository:
    def __init__(
        self,
        *,
        stations: tuple[MetStation, ...],
        products: tuple[CanonicalProduct, ...],
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
    ) -> None:
        self.basin_by_model = {"demo_model": "basin_v1"}
        self.model_identity_by_model = {
            "demo_model": {
                "basin_id": "basin_a",
                "basin_version_id": "basin_v1",
                "river_network_version_id": "rivnet_v1",
            }
        }
        self.stations = stations
        self.products = products
        self.forcing_mapping_manifest = forcing_mapping_manifest
        self.forcing_mapping_contract = forcing_mapping_contract
        self.forcing_mapping_contract_error = forcing_mapping_contract_error
        self.direct_grid_validation_assets = dict(direct_grid_validation_assets or {})
        self.interp_weights: list[InterpolationWeight] = []
        self.met_station_ids = {station.station_id for station in stations}
        self.direct_grid_station_ensure_calls: list[dict[str, Any]] = []
        self.direct_grid_station_ensure_count = 0
        self.forcing_versions: dict[str, dict[str, Any]] = {}
        self.components: list[ForcingComponent] = []
        self.timeseries: list[ForcingTimeseriesRow] = []
        self.cycle_updates: list[dict[str, Any]] = []
        self.events: list[tuple[str, Any]] = []
        self.mapping_contract_calls: list[dict[str, Any]] = []
        self.load_station_count = 0
        self.load_weight_count = 0
        self.fail_next_forcing_version_upsert = fail_next_forcing_version_upsert
        self.fail_next_component_replace = fail_next_component_replace
        self.fail_next_timeseries_replace = fail_next_timeseries_replace
        self.fail_next_finalize = fail_next_finalize
        self.fail_next_cycle_ready_update = fail_next_cycle_ready_update
        self.fail_next_interp_weight_upsert = fail_next_interp_weight_upsert
        self.fail_next_direct_grid_station_ensure = fail_next_direct_grid_station_ensure
        self.upsert_count = 0
        self.interp_weight_upsert_count = 0

    def resolve_model_basin_version(self, *, model_id: str) -> str:
        return self.basin_by_model[model_id]

    def resolve_model_identity(self, *, model_id: str) -> Mapping[str, Any]:
        return dict(self.model_identity_by_model[model_id])

    def load_met_stations(self, *, basin_version_id: str) -> tuple[MetStation, ...]:
        self.load_station_count += 1
        loaded = tuple(
            station
            for station in self.stations
            if station.basin_version_id == basin_version_id
            and _is_legacy_loadable_station(station)
        )
        self.met_station_ids.update(station.station_id for station in loaded)
        return loaded

    def list_canonical_products(
        self, *, source_id: str, cycle_time: Any
    ) -> tuple[CanonicalProduct, ...]:
        return tuple(
            product
            for product in self.products
            if product.source_id == source_id and product.cycle_time == cycle_time
        )

    def list_fallback_canonical_products(
        self,
        *,
        source_id: str,
        start_time: Any,
        end_time: Any,
        variables: list[str] | tuple[str, ...],
    ) -> tuple[CanonicalProduct, ...]:
        selected: dict[tuple[Any, str], CanonicalProduct] = {}
        for product in self.products:
            if product.source_id != source_id or product.variable not in variables:
                continue
            if not start_time <= product.valid_time <= end_time:
                continue
            if product.quality_flag == "fail" or not product.checksum:
                continue
            key = (product.valid_time, product.variable)
            existing = selected.get(key)
            if existing is None or _lead_time_sort_key(product) < _lead_time_sort_key(
                existing
            ):
                selected[key] = product
        return tuple(
            sorted(
                selected.values(),
                key=lambda product: (product.variable, product.valid_time),
            )
        )

    def load_interp_weights(
        self,
        *,
        source_id: str,
        grid_id: str,
        model_id: str,
    ) -> tuple[InterpolationWeight, ...]:
        self.load_weight_count += 1
        return tuple(
            weight
            for weight in self.interp_weights
            if weight.source_id == source_id
            and weight.grid_id == grid_id
            and weight.model_id == model_id
        )

    def upsert_interp_weights(
        self, weights: list[InterpolationWeight] | tuple[InterpolationWeight, ...]
    ) -> None:
        self.interp_weight_upsert_count += 1
        if self.fail_next_interp_weight_upsert:
            self.fail_next_interp_weight_upsert = False
            raise RuntimeError("interp weight write failed")
        unknown_station_ids = sorted(
            {weight.station_id for weight in weights} - self.met_station_ids
        )
        if unknown_station_ids:
            raise RuntimeError(
                f"interp weight station ids missing from met_station: {unknown_station_ids}"
            )
        if not weights:
            return
        scopes = {
            (weight.source_id, weight.grid_id, weight.model_id) for weight in weights
        }
        if len(scopes) != 1:
            raise ForcingStoreError(
                "Interpolation weights must be replaced one source/grid/model scope at a time."
            )
        source_id, grid_id, model_id = next(iter(scopes))
        self.interp_weights = [
            weight
            for weight in self.interp_weights
            if not (
                weight.source_id == source_id
                and weight.grid_id == grid_id
                and weight.model_id == model_id
            )
        ]
        existing_keys = {
            (
                weight.source_id,
                weight.grid_id,
                weight.model_id,
                weight.station_id,
                weight.variable,
                weight.grid_cell_id,
            )
            for weight in self.interp_weights
        }
        for weight in weights:
            key = (
                weight.source_id,
                weight.grid_id,
                weight.model_id,
                weight.station_id,
                weight.variable,
                weight.grid_cell_id,
            )
            if key not in existing_keys:
                self.interp_weights.append(weight)
                existing_keys.add(key)
            else:
                self.interp_weights = [
                    weight
                    if (
                        existing.source_id,
                        existing.grid_id,
                        existing.model_id,
                        existing.station_id,
                        existing.variable,
                        existing.grid_cell_id,
                    )
                    == key
                    else existing
                    for existing in self.interp_weights
                ]

    def ensure_direct_grid_met_stations(
        self, *, basin_version_id: str, contract: Any
    ) -> None:
        self.direct_grid_station_ensure_count += 1
        self.direct_grid_station_ensure_calls.append(
            {
                "basin_version_id": basin_version_id,
                "station_ids": tuple(
                    station.station_id for station in contract.stations
                ),
                "grid_cell_ids": tuple(
                    station.grid_cell_id for station in contract.stations
                ),
            }
        )
        if self.fail_next_direct_grid_station_ensure:
            self.fail_next_direct_grid_station_ensure = False
            raise RuntimeError("direct-grid met_station mirror failed")
        existing_by_id = {station.station_id: station for station in self.stations}
        mirrors: list[MetStation] = []
        for station in sorted(
            contract.stations, key=lambda item: item.shud_forcing_index
        ):
            properties = {
                **dict(station.properties),
                "derived_cache": True,
                "forcing_mapping_mode": "direct_grid",
                "direct_grid": True,
                "manifest_authority": True,
                "binding_checksum": contract.binding_checksum,
                "binding_uri": contract.binding_uri,
                "model_input_package_id": contract.model_input_package_id,
                "sp_att_path": contract.sp_att_path,
                "sp_att_checksum": contract.sp_att_checksum,
                "grid_id": station.grid_id,
                "contract_grid_id": contract.grid_id,
                "grid_cell_id": station.grid_cell_id,
                "grid_signature": contract.grid_signature,
                "shud_forcing_index": station.shud_forcing_index,
                "forcing_filename": station.forcing_filename,
                "x": station.x,
                "y": station.y,
                "z": station.z,
                "mirror_identity": _direct_grid_mirror_identity(
                    contract, station.grid_id
                ),
            }
            mirror = MetStation(
                station.station_id,
                basin_version_id,
                station.longitude,
                station.latitude,
                station.z,
                DIRECT_GRID_CACHE_STATION_ROLE,
                station_name=f"Direct-grid station {station.shud_forcing_index}",
                properties_json=properties,
            )
            existing = existing_by_id.get(station.station_id)
            if existing is not None and not _same_direct_grid_mirror(existing, mirror):
                raise ForcingStoreError(
                    "Direct-grid met_station mirror conflicts with an existing station_id that is not the same "
                    "derived direct-grid cache binding."
                )
            mirrors.append(mirror)
        mirror_ids = {station.station_id for station in mirrors}
        self.stations = tuple(
            station for station in self.stations if station.station_id not in mirror_ids
        ) + tuple(mirrors)
        self.met_station_ids.update(mirror_ids)

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
        if self.forcing_mapping_contract_error is not None:
            raise self.forcing_mapping_contract_error
        if self.forcing_mapping_manifest is not None:
            return load_forcing_mapping_contract_from_manifest(
                self.forcing_mapping_manifest, source_id=source_id
            )
        return self.forcing_mapping_contract

    def load_direct_grid_validation_assets(
        self,
        *,
        model_id: str,
        basin_version_id: str,
        contract: Any,
        max_bytes: int,
    ) -> Mapping[str, Any]:
        return dict(self.direct_grid_validation_assets)

    def get_forcing_version(
        self, *, source_id: str, cycle_time: Any, model_id: str
    ) -> dict[str, Any] | None:
        for record in self.forcing_versions.values():
            if (
                record["source_id"] == source_id
                and record["cycle_time"] == cycle_time
                and record["model_id"] == model_id
            ):
                return dict(record)
        return None

    def upsert_forcing_version(self, record: dict[str, Any]) -> dict[str, Any]:
        self.upsert_count += 1
        if self.fail_next_forcing_version_upsert:
            self.fail_next_forcing_version_upsert = False
            raise RuntimeError("forcing version parent write failed")
        self.forcing_versions[record["forcing_version_id"]] = dict(record)
        self.events.append(("upsert_forcing_version", record["checksum"]))
        return self.forcing_versions[record["forcing_version_id"]]

    def finalize_forcing_version(
        self, forcing_version_id: str, checksum: str
    ) -> dict[str, Any]:
        if self.fail_next_finalize:
            self.fail_next_finalize = False
            raise RuntimeError("forcing version finalize failed")
        self.forcing_versions[forcing_version_id]["checksum"] = checksum
        self.events.append(("finalize_forcing_version", checksum))
        return dict(self.forcing_versions[forcing_version_id])

    def clear_forcing_version_checksum(self, forcing_version_id: str) -> dict[str, Any]:
        self.forcing_versions[forcing_version_id]["checksum"] = None
        self.events.append(("clear_forcing_version_checksum", forcing_version_id))
        return dict(self.forcing_versions[forcing_version_id])

    def verify_forcing_version_children(
        self,
        *,
        forcing_version_id: str,
        expected_components: list[ForcingComponent] | tuple[ForcingComponent, ...],
        expected_station_ids: list[str] | tuple[str, ...],
        expected_valid_times: list[Any] | tuple[Any, ...],
        expected_variables: list[str] | tuple[str, ...],
    ) -> Mapping[str, Any]:
        expected_component_tuples = Counter(
            (
                component.canonical_product_id,
                component.variable,
                component.valid_time_start,
                component.valid_time_end,
                component.role,
            )
            for component in expected_components
        )
        components = [
            component
            for component in self.components
            if component.forcing_version_id == forcing_version_id
        ]
        component_tuples = Counter(
            (
                component.canonical_product_id,
                component.variable,
                component.valid_time_start,
                component.valid_time_end,
                component.role,
            )
            for component in components
        )
        rows = [
            row
            for row in self.timeseries
            if row.forcing_version_id == forcing_version_id
        ]
        timeseries_tuples = Counter(
            (row.station_id, row.valid_time, row.variable) for row in rows
        )
        expected_timeseries_tuples = Counter(
            (station_id, valid_time, variable)
            for station_id in expected_station_ids
            for valid_time in expected_valid_times
            for variable in expected_variables
        )
        expected_row_count = (
            len(expected_station_ids)
            * len(expected_valid_times)
            * len(expected_variables)
        )
        proof = {
            "forcing_version_id": forcing_version_id,
            "expected_component_count": len(expected_components),
            "component_count": len(components),
            "expected_component_tuple_count": len(expected_component_tuples),
            "component_tuple_count": len(component_tuples),
            "expected_timeseries_row_count": expected_row_count,
            "timeseries_row_count": len(rows),
            "expected_timeseries_tuple_count": len(expected_timeseries_tuples),
            "timeseries_tuple_count": len(timeseries_tuples),
            "station_count": len({row.station_id for row in rows}),
            "timestep_count": len({row.valid_time for row in rows}),
            "variable_count": len({row.variable for row in rows}),
        }
        proof["complete"] = (
            proof["component_count"] == proof["expected_component_count"]
            and component_tuples == expected_component_tuples
            and proof["timeseries_row_count"] == proof["expected_timeseries_row_count"]
            and timeseries_tuples == expected_timeseries_tuples
            and proof["station_count"] == len(expected_station_ids)
            and proof["timestep_count"] == len(expected_valid_times)
            and proof["variable_count"] == len(expected_variables)
        )
        return proof

    def replace_forcing_components(
        self,
        forcing_version_id: str,
        components: list[ForcingComponent] | tuple[ForcingComponent, ...],
    ) -> None:
        if self.fail_next_component_replace:
            self.fail_next_component_replace = False
            raise RuntimeError("component write failed")
        self.components = [
            component
            for component in self.components
            if component.forcing_version_id != forcing_version_id
        ]
        self.components.extend(components)
        self.events.append(("replace_forcing_components", forcing_version_id))

    def replace_forcing_timeseries(
        self,
        forcing_version_id: str,
        rows: list[ForcingTimeseriesRow] | tuple[ForcingTimeseriesRow, ...],
    ) -> None:
        if self.fail_next_timeseries_replace:
            self.fail_next_timeseries_replace = False
            raise RuntimeError("timeseries write failed")
        self.timeseries = [
            row
            for row in self.timeseries
            if row.forcing_version_id != forcing_version_id
        ]
        self.timeseries.extend(rows)
        self.events.append(("replace_forcing_timeseries", forcing_version_id))

    def update_forecast_cycle(self, **kwargs: Any) -> dict[str, Any]:
        self.cycle_updates.append(dict(kwargs))
        if (
            kwargs.get("status") == "forcing_ready"
            and self.fail_next_cycle_ready_update
        ):
            self.fail_next_cycle_ready_update = False
            raise RuntimeError("forecast cycle ready update failed")
        return dict(kwargs)


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


def _file_direct_grid_repository(
    tmp_path: Path,
    *,
    snapshot_overrides: Mapping[str, Any] | None = None,
    duplicate_snapshot_overrides: Mapping[str, Any] | None = None,
) -> tuple[FileForcingRepository, Any, bytes, bytes]:
    store = LocalObjectStore(tmp_path, object_store_prefix="s3://nhms")
    binding_content = b'{"schema_version":"nhms.direct_grid.binding.v1"}'
    sp_att_content = _sp_att_content().encode("utf-8")
    binding_uri = "models/demo/direct-grid/binding.json"
    package_uri = "models/demo/direct-grid/package"
    store.write_bytes_atomic(binding_uri, binding_content)
    store.write_bytes_atomic(f"{package_uri}/input/demo.sp.att", sp_att_content)
    direct_grid = _direct_grid_manifest_for_default_grid()
    direct_grid.update(
        {
            "binding_uri": binding_uri,
            "binding_checksum": sha256_bytes(binding_content),
            "sp_att_path": "input/demo.sp.att",
            "sp_att_checksum": sha256_bytes(sp_att_content),
        }
    )
    snapshot = {
        "source_id": "GFS",
        "grid_id": direct_grid["grid_id"],
        "grid_signature": direct_grid["grid_signature"],
        "grid_snapshot_id": "9dcbb4cf-cdaf-4255-8500-364f75cf2e00",
        "bbox_south": 8.0,
        "bbox_north": 64.0,
        "bbox_west": 63.0,
        "bbox_east": 145.0,
        "superseded_at": None,
    }
    snapshot.update(snapshot_overrides or {})

    def model_entry(model_id: str, projection: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "model_id": model_id,
            "basin_id": "basin_a",
            "basin_version_id": "basin_v1",
            "river_network_version_id": "rivnet_v1",
            "model_package_uri": package_uri,
            "resource_profile": {
                "direct_grid_forcing": direct_grid,
                "canonical_grid_snapshot": dict(projection),
            },
        }

    models = [model_entry("demo_model", snapshot)]
    if duplicate_snapshot_overrides is not None:
        duplicate_snapshot = dict(snapshot)
        duplicate_snapshot.update(duplicate_snapshot_overrides)
        models.append(model_entry("demo_model_duplicate", duplicate_snapshot))
    store.write_bytes_atomic(
        "models/demo/registry.json", json.dumps({"models": models}).encode("utf-8")
    )
    registry_manifest = "models/demo/registry.json"
    repository = FileForcingRepository(
        object_store=store, registry_manifest=registry_manifest
    )
    contract = parse_direct_grid_forcing_contract(direct_grid, source_id="GFS")
    return repository, contract, binding_content, sp_att_content


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


def _direct_grid_manifest() -> dict[str, Any]:
    return {
        "forcing_mapping_mode": "direct_grid",
        "binding_uri": "models/demo/direct-grid/binding.json",
        "binding_checksum": "sha256:binding",
        "model_input_package_id": "model-input-demo-v1",
        "sp_att_path": "input/qhh.sp.att",
        "sp_att_checksum": "sha256:sp-att",
        "applicable_source_ids": ["GFS", "IFS"],
        "grid_id": "ifs_gfs_025deg",
        "grid_signature": "sha256:grid-signature",
        "station_bindings": [
            {
                "station_id": "qhh_forc_001",
                "shud_forcing_index": 1,
                "forcing_filename": "X100.95Y36.25.csv",
                "longitude": 100.95,
                "latitude": 36.25,
                "x": 1,
                "y": 2,
                "z": 3657,
                "grid_id": "ifs_gfs_025deg",
                "grid_cell_id": "cell-001",
            },
            {
                "station_id": "qhh_forc_002",
                "shud_forcing_index": 2,
                "forcing_filename": "X101.05Y36.25.csv",
                "longitude": 101.05,
                "latitude": 36.25,
                "x": 2,
                "y": 3,
                "z": -9999,
                "grid_id": "ifs_gfs_025deg",
                "grid_cell_id": "cell-002",
            },
        ],
    }


def _direct_grid_manifest_for_default_grid() -> dict[str, Any]:
    manifest = _direct_grid_manifest()
    manifest.update(
        {
            "binding_checksum": "sha256:binding-actual",
            "sp_att_checksum": "sha256:sp-att-actual",
            "grid_id": "grid_a",
            "grid_signature": "b56a451cd543e6d23dfce1d486fe5fccdb6e14385b283eceec01d3af30870d4c",
        }
    )
    manifest["station_bindings"][0].update(
        {"grid_id": "grid_a", "grid_cell_id": "0", "longitude": -75.0, "latitude": 40.0}
    )
    manifest["station_bindings"][1].update(
        {"grid_id": "grid_a", "grid_cell_id": "1", "longitude": -74.5, "latitude": 40.2}
    )
    return manifest


def _sp_att_content(forc_values: tuple[str | int, ...] = (1, 2)) -> str:
    rows = "\n".join(
        f"{index}\t0\t0\t0\t{value}" for index, value in enumerate(forc_values, start=1)
    )
    return f"2 1\nTRI\tA\tB\tC\tFORC\n{rows}\n"


def _direct_grid_validation_assets(
    *,
    binding_checksum: str = "binding-actual",
    model_input_package_id: str = "model-input-demo-v1",
    sp_att_checksum: str = "sp-att-actual",
    sp_att_content: str | None = None,
) -> dict[str, Any]:
    return {
        "binding_checksum": binding_checksum,
        "model_input_package_id": model_input_package_id,
        "sp_att_checksum": sp_att_checksum,
        "sp_att_content": _sp_att_content()
        if sp_att_content is None
        else sp_att_content,
    }


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
    assert repository.cycle_updates[-1]["status"] == "failed_forcing"
    assert repository.cycle_updates[-1]["error_code"] == "FORCING_FAILED"
    assert (
        "Invalid forcing mapping contract"
        in repository.cycle_updates[-1]["error_message"]
    )


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


def _write_canonical_products(
    store: LocalObjectStore,
    *,
    source_id: str = "gfs",
    cycle_time_text: str = "2026050700",
    product_id_prefix: str | None = None,
    forecast_hours: tuple[int, ...] = (0, 3),
    lead_time_by_hour: Mapping[int, int] | None = None,
    omitted_variables: set[str] | None = None,
    omitted_by_time: set[tuple[str, int]] | None = None,
    include_geographic_coords: bool = True,
    values_by_variable: Mapping[str, tuple[float, float, float]] | None = None,
    radiation_variable: str = "shortwave_down",
    longitudes: tuple[float, float, float] = (-75.0, -74.5, -74.0),
    latitudes: tuple[float, float, float] = (40.0, 40.2, 40.4),
) -> tuple[CanonicalProduct, ...]:
    cycle_time = parse_cycle_time(cycle_time_text)
    product_id_prefix = product_id_prefix or source_id.lower()
    lead_time_by_hour = lead_time_by_hour or {}
    omitted_variables = omitted_variables or set()
    omitted_by_time = omitted_by_time or set()
    values_by_variable = values_by_variable or {}
    products: list[CanonicalProduct] = []
    variables = {
        "prcp_rate_or_amount": ("mm/day", 1.0),
        "air_temperature_2m": ("degC", 10.0),
        "relative_humidity_2m": ("0-1", 0.5),
        "wind_u_10m": ("m/s", 3.0),
        "wind_v_10m": ("m/s", 4.0),
        "pressure_surface": ("Pa", 101000.0),
        radiation_variable: ("W/m2", 250.0),
    }
    if omitted_variables:
        variables = {
            variable: details
            for variable, details in variables.items()
            if variable not in omitted_variables
        }
    compact_cycle = cycle_time.strftime("%Y%m%d%H")
    for forecast_hour in forecast_hours:
        valid_time = cycle_time + timedelta(hours=forecast_hour)
        for variable, (unit, base_value) in variables.items():
            if (
                variable in omitted_variables
                or (variable, forecast_hour) in omitted_by_time
            ):
                continue
            if (
                source_id == "gfs"
                and forecast_hour == 0
                and variable in {"prcp_rate_or_amount", radiation_variable}
            ):
                continue
            product_id = (
                f"{product_id_prefix}_{compact_cycle}_{variable}_f{forecast_hour:03d}"
            )
            key = f"canonical/{source_id}/{compact_cycle}/{variable}/{product_id}.nc"
            values = values_by_variable.get(
                variable,
                (
                    base_value + forecast_hour,
                    base_value + forecast_hour + 1.0,
                    base_value + forecast_hour + 2.0,
                ),
            )
            content = _netcdf_bytes(
                variable,
                values=values,
                include_geographic_coords=include_geographic_coords,
                longitudes=longitudes,
                latitudes=latitudes,
            )
            object_uri = store.write_bytes_atomic(key, content)
            products.append(
                CanonicalProduct(
                    canonical_product_id=product_id,
                    source_id=source_id,
                    cycle_time=cycle_time,
                    valid_time=valid_time,
                    variable=variable,
                    unit=unit,
                    grid_id="grid_a",
                    object_uri=object_uri,
                    checksum=sha256_bytes(content),
                    native_time_resolution="3h",
                    native_spatial_resolution="1deg",
                    lead_time_hours=lead_time_by_hour.get(forecast_hour, forecast_hour),
                )
            )
    return tuple(products)


def _netcdf_bytes(
    variable: str,
    *,
    values: tuple[float, float, float],
    include_geographic_coords: bool = True,
    longitudes: tuple[float, float, float] = (-75.0, -74.5, -74.0),
    latitudes: tuple[float, float, float] = (40.0, 40.2, 40.4),
    cell_ids: tuple[str, ...] = ("0", "1", "2"),
) -> bytes:
    import xarray as xr

    coords: dict[str, Any] = (
        {"point": list(cell_ids)} if len(cell_ids) == 3 else {"point": list(cell_ids)}
    )
    if include_geographic_coords:
        coords.update(
            {
                "longitude": ("point", list(longitudes)),
                "latitude": ("point", list(latitudes)),
            }
        )
    dataset = xr.Dataset(
        data_vars={variable: ("point", list(values))},
        coords=coords,
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".nc") as temp_file:
            dataset.to_netcdf(temp_file.name, engine="netcdf4", format="NETCDF4")
            temp_file.seek(0)
            return temp_file.read()
    finally:
        dataset.close()


def _lead_time_sort_key(product: CanonicalProduct) -> tuple[int, Any, str]:
    lead_time = (
        product.lead_time_hours if product.lead_time_hours is not None else 10**9
    )
    return lead_time, product.cycle_time, product.canonical_product_id


def _direct_grid_mirror_identity(contract: Any, station_grid_id: str) -> dict[str, str]:
    return {
        "binding_checksum": contract.binding_checksum,
        "model_input_package_id": contract.model_input_package_id,
        "grid_signature": contract.grid_signature,
        "contract_grid_id": contract.grid_id,
        "grid_id": station_grid_id,
    }


def _is_legacy_loadable_station(station: MetStation) -> bool:
    properties = dict(station.properties_json or {})
    return (
        station.station_role != DIRECT_GRID_CACHE_STATION_ROLE
        and properties.get("derived_cache") is not True
        and properties.get("forcing_mapping_mode") != "direct_grid"
    )


def _same_direct_grid_mirror(existing: MetStation, mirror: MetStation) -> bool:
    existing_properties = dict(existing.properties_json or {})
    mirror_properties = dict(mirror.properties_json or {})
    identity_fields = (
        "binding_checksum",
        "model_input_package_id",
        "grid_signature",
        "contract_grid_id",
        "grid_id",
    )
    return (
        existing.basin_version_id == mirror.basin_version_id
        and existing.station_role == DIRECT_GRID_CACHE_STATION_ROLE
        and existing_properties.get("derived_cache") is True
        and existing_properties.get("forcing_mapping_mode") == "direct_grid"
        and all(
            existing_properties.get(field) == mirror_properties.get(field)
            for field in identity_fields
        )
    )
