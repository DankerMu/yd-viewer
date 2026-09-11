# NWM@8ae9b8f2 tests/test_forcing_producer.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from yd_producer.forcing import (
    CanonicalProduct,
    InterpolationWeight,
    MetStation,
    load_forcing_mapping_contract_from_manifest,
)
from yd_producer.forcing._file_store_common import ForcingStoreError
from yd_producer.forcing._producer_types import (
    ForcingComponent,
    ForcingTimeseriesRow,
)

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
