# NWM@8ae9b8f2 workers/forcing_producer/producer.py
"""File-backed direct-grid forcing producer.

Deviations from the NWM pin (inventory §1 producer/src/yd_producer/forcing/producer.py / issue #14):
- no grid-registry/bbox preflight, no env factory, no IDW path
- grid identity is checked by the yd-authored helper
- Time_Day=0 is anchored to the explicit cycle_time
- canonical NetCDF is opened through a no-follow descriptor alias
- #119: format_shud_forcing_package station-index Lon/Lat/X/Y/Z use
  repr(float(...)) shortest-roundtrip; field selection, coercion, and
  negative-z stay; _format_number and Time_Day/time-series/debug stay
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import AbstractSet, Any, Protocol

from yd_producer.canonical.converter import canonical_product_is_forcing_usable
from yd_producer.forcing.bounded_json import BoundedJSONError, load_bounded_json
from yd_producer.forcing.canonical_json import _json_bytes, _json_default
from yd_producer.forcing.direct_grid_contract import (
    DIRECT_GRID_MODE,
    DirectGridContractError,
    DirectGridForcingContract,
    validate_direct_grid_forcing_contract,
)
from yd_producer.forcing.grid_identity import grid_identity_hash
from yd_producer.forcing.netcdf_open import open_canonical_netcdf
from yd_producer.forcing.shud_forcing_contract import (
    CANONICAL_SHUD_FORCING_INDEX_MEMBER,
    SHUD_FORCING_INDEX_BASENAMES,
    SHUD_FORCING_ROLE,
)
from yd_producer.raw.region import GeoBBox
from yd_producer.raw.source_identity import normalize_source_id
from yd_producer.store.object_store import (
    LocalObjectStore,
    ObjectStoreError,
    sha256_bytes,
)
from yd_producer.store.safe_fs import SafeFilesystemError

LOGGER = logging.getLogger(__name__)

from yd_producer.forcing._producer_types import (
    CANONICAL_TO_FORCING,
    CanonicalField,
    CanonicalProduct,
    ERA5_CANONICAL_TO_FORCING,
    ERA5_FALLBACK_SOURCE_ID,
    ERA5_LATENCY_FALLBACK_REASON,
    ERA5_REQUIRED_CANONICAL_VARIABLES,
    EXPECTED_CANONICAL_UNITS,
    FORCING_VARIABLES,
    FallbackLineage,
    ForcingComponent,
    ForcingProducerConfig,
    ForcingProductionError,
    ForcingProductionResult,
    ForcingRepository,
    ForcingTimeseriesRow,
    GridPoint,
    IFS_CANONICAL_TO_FORCING,
    IFS_REQUIRED_CANONICAL_VARIABLES,
    InterpolationWeight,
    MetStation,
    OUTPUT_UNITS,
    REQUIRED_CANONICAL_VARIABLES,
)
from yd_producer.forcing._producer_common import (
    _SAFE_PATH_COMPONENT,
    _direct_grid_text_asset,
    _direct_grid_validation_error,
    _directory_uri,
    _distance_degrees,
    _ensure_utc,
    _forcing_version_id,
    _format_number,
    _format_time,
    _is_era5_source,
    _is_ifs_source,
    _json_round_trip,
    _normalize_checksum_identity,
    _normalize_longitude,
    _object_source_segment,
    _optional_int,
    _package_manifest_uri,
    _parse_sp_att_forc_values,
    _product_lead_hours,
    _products,
    _repository_basin_version_id,
    _safe_path_component,
    _stable_identity,
    _uses_era5_latency_fallback,
    _valid_geographic_coordinate,
    _validate_sp_att_forc_values,
    format_cycle_time,
    parse_cycle_time,
    wind_speed,
)
from yd_producer.forcing._producer_stations import (
    _PACKAGE_INTERNAL_NAMES,
    _met_stations_from_direct_grid_contract,
    _quality_flags_manifest,
    _reserved_shud_station_filenames,
    _safe_station_forcing_filename,
    _station_forcing_filename,
    _station_forcing_index,
    _station_forcing_sort_key,
    _station_order_manifest,
    _station_properties,
    _station_signature,
    _station_signature_matches,
    _valid_station,
    _validate_forcing_grid_station_contract,
    _validate_package_filenames,
    _validate_unique_station_forcing_contract,
)
from yd_producer.forcing._producer_grid import (
    _coord_by_length,
    _data_array_shape,
    _data_array_size,
    _direct_lon_lat_coords,
    _flat_coord,
    _grid_cell_ids,
    _rectilinear_lon_lat_coords,
    _required_grid_indexes,
    _select_data_array_indexes,
    _select_data_variable,
    _validate_canonical_netcdf_identity,
    _validate_grid_points,
)
from yd_producer.forcing._producer_timeseries import (
    _canonical_variable_for_forcing,
    _cycle_time_from_products,
    _expected_forcing_valid_times,
    _fallback_products_by_required_variable,
    _fallback_variables_for_required,
    _forcing_coverage_end_time,
    _forcing_product_time_plan,
    _gfs_interval_row_times,
    _is_allowed_gfs_forcing_time_gap,
    _lead_window_from_products,
    _limit_products_by_max_lead_hours,
    _limit_products_by_min_lead_hours,
    _max_product_lead_hours,
    _missing_product_details,
    _native_resolution_for_output,
    _planned_product_time,
    _required_grid_cell_ids_by_source_grid,
    _source_id_for_output_variable,
    _valid_times,
    _validate_canonical_product_units,
    _validate_direct_grid_identity,
    _validate_requested_canonical_product_identity,
    _weights_by_source_grid,
    _weights_by_station_variable,
)
from yd_producer.forcing._producer_reuse import (
    _canonical_input_signature,
    _canonical_input_signature_matches,
    _canonical_product_ids,
    _direct_grid_lineage_identity,
    _forcing_components_for_products,
    _format_grid_signatures,
    _grid_definition_content_signature,
    _grid_definition_signature,
    _lineage_identity_matches,
    _output_config_identity,
    _scheduler_canonical_identity_manifest,
    _scheduler_canonical_identity_matches,
    _time_range_manifest,
)
from yd_producer.forcing._producer_output import (
    format_debug_csv,
    format_shud_forcing_package,
    format_tsd_forc,
)

from yd_producer.forcing._producer_contracts import _ContractMethods
from yd_producer.forcing._producer_inputs import _InputMethods
from yd_producer.forcing._producer_output import _OutputMethods
from yd_producer.forcing._producer_reuse import _ReuseMethods
from yd_producer.forcing._producer_timeseries import _TimeseriesMethods


class ForcingProducer(
    _ContractMethods,
    _InputMethods,
    _TimeseriesMethods,
    _OutputMethods,
    _ReuseMethods,
):
    def __init__(
        self,
        *,
        config: ForcingProducerConfig,
        repository: ForcingRepository | None = None,
        object_store: LocalObjectStore | None = None,
        env_reader: Callable[[], GeoBBox] | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.object_store = object_store or LocalObjectStore(
            self.config.object_store_root,
            object_store_prefix=self.config.object_store_prefix,
        )
        # bbox is supplied by config.toml and injected via env_reader.
        self._env_reader = env_reader

    def produce(
        self,
        *,
        source_id: str | None = None,
        cycle_time: str | datetime,
        model_id: str,
        max_lead_hours: int | None = None,
        basin_id: str | None = None,
        basin_version_id: str | None = None,
        river_network_version_id: str | None = None,
        canonical_product_id: str | None = None,
        canonical_identity: Mapping[str, Any] | None = None,
    ) -> ForcingProductionResult:
        if self.repository is None:
            raise ForcingProductionError(
                "A forcing repository is required for production."
            )

        # Request preflight: no repository calls.
        try:
            requested_source_id = (
                self.config.source_id if source_id is None else source_id
            )
            if not isinstance(requested_source_id, str):
                raise TypeError("source_id must be a string.")
            if not isinstance(cycle_time, str | datetime):
                raise TypeError("cycle_time must be a string or datetime.")
            resolved_source_id = normalize_source_id(requested_source_id)
            parsed_cycle_time = parse_cycle_time(cycle_time)
            if (
                parsed_cycle_time.hour not in {0, 12}
                or parsed_cycle_time.minute != 0
                or parsed_cycle_time.second != 0
                or parsed_cycle_time.microsecond != 0
            ):
                raise ValueError(
                    "cycle_time must be a whole-hour UTC 00Z or 12Z cycle."
                )
            _safe_path_component(model_id)
            _safe_path_component(_object_source_segment(resolved_source_id))
            if basin_version_id not in (None, ""):
                _safe_path_component(basin_version_id)
            if max_lead_hours is not None and (
                type(max_lead_hours) is not int or max_lead_hours < 0
            ):
                raise ValueError(
                    "max_lead_hours must be None or a non-negative integer."
                )
        except (TypeError, ValueError) as error:
            raise ForcingProductionError(
                f"Invalid forcing production request: {error}"
            ) from error

        # Repository-return preflight: no existing-state mutation.
        try:
            model_identity = self._resolve_model_identity(model_id=model_id)
            resolved_basin_version_id = _repository_basin_version_id(model_identity)
            self._validate_scheduler_identity(
                model_identity=model_identity,
                basin_id=basin_id,
                basin_version_id=basin_version_id,
                river_network_version_id=river_network_version_id,
            )
            resolved_basin_id = str(model_identity.get("basin_id") or basin_id or "")
            resolved_river_network_version_id = str(
                model_identity.get("river_network_version_id") or ""
            )
            forcing_mapping_contract = self._resolve_forcing_mapping_contract(
                model_id=model_id,
                basin_version_id=resolved_basin_version_id,
                source_id=resolved_source_id,
            )
            if forcing_mapping_contract is None:
                raise ForcingProductionError(
                    "Direct-grid forcing mapping contract is required; IDW fallback is not available."
                )
        except ForcingProductionError:
            raise
        except Exception as error:
            raise ForcingProductionError(
                f"Invalid forcing model identity or mapping contract: {error}"
            ) from error

        # Authority reads remain after existing lookup for stale-ready revocation.
        existing: Mapping[str, Any] | None = None
        existing_currency_checked = False
        try:
            existing = self.repository.get_forcing_version(
                source_id=resolved_source_id,
                cycle_time=parsed_cycle_time,
                model_id=model_id,
            )

            required_variables = self._required_canonical_variables(resolved_source_id)
            canonical_to_forcing = self._canonical_to_forcing(resolved_source_id)
            products_by_variable = self._load_canonical_products(
                source_id=resolved_source_id,
                cycle_time=parsed_cycle_time,
                required_variables=required_variables,
                require_complete=(
                    max_lead_hours is None
                    and not _uses_era5_latency_fallback(resolved_source_id)
                ),
            )
            products_by_variable = _limit_products_by_max_lead_hours(
                products_by_variable,
                cycle_time=parsed_cycle_time,
                max_lead_hours=max_lead_hours,
            )
            products_by_variable = _limit_products_by_min_lead_hours(
                products_by_variable,
                cycle_time=parsed_cycle_time,
                min_lead_hours=self.config.min_lead_hours,
            )
            fallback_lineage = self._apply_era5_latency_fallback(
                source_id=resolved_source_id,
                cycle_time=parsed_cycle_time,
                products_by_variable=products_by_variable,
                required_variables=required_variables,
            )
            self._validate_canonical_products(
                products_by_variable=products_by_variable,
                required_variables=required_variables,
                requested_source_id=resolved_source_id,
                requested_cycle_time=parsed_cycle_time,
            )
            self._validate_scheduler_canonical_identity(
                source_id=resolved_source_id,
                products_by_variable=products_by_variable,
                canonical_product_id=canonical_product_id,
                canonical_identity=canonical_identity,
            )
            if forcing_mapping_contract is None:
                raise ForcingProductionError(
                    "Direct-grid forcing mapping contract is required; IDW fallback is not available."
                )
            self._validate_direct_grid_contract_for_production(
                contract=forcing_mapping_contract,
                model_id=model_id,
                basin_version_id=resolved_basin_version_id,
                products_by_variable=products_by_variable,
            )
            self._enforce_limit(
                "station_count",
                len(forcing_mapping_contract.stations),
                self.config.max_station_count,
            )
            direct_grid_stations = _met_stations_from_direct_grid_contract(
                forcing_mapping_contract,
                basin_version_id=resolved_basin_version_id,
            )
            expected_valid_times = _expected_forcing_valid_times(
                resolved_source_id,
                products_by_variable,
                cycle_time=parsed_cycle_time,
            )
            self._enforce_direct_grid_timeseries_limits(
                expected_valid_times=expected_valid_times,
                station_count=len(direct_grid_stations),
            )
            grid_points_by_source_grid = self._grid_points_by_source_grid_from_products(
                products_by_variable
            )
            lead_window = _lead_window_from_products(
                products_by_variable, parsed_cycle_time
            )
            station_signature = _station_signature(direct_grid_stations)
            scheduler_canonical_identity = _scheduler_canonical_identity_manifest(
                canonical_product_id=canonical_product_id,
                canonical_identity=canonical_identity,
            )
            canonical_input_signature = self._canonical_input_signature(
                products_by_variable, parsed_cycle_time
            )
            grid_signature_by_source_grid = {
                source_grid: grid_identity_hash(grid_points)
                for source_grid, grid_points in grid_points_by_source_grid.items()
            }
            output_config_identity = _output_config_identity(self.config)
            direct_grid_identity = _direct_grid_lineage_identity(
                contract=forcing_mapping_contract,
                station_signature=station_signature,
                canonical_input_signature=canonical_input_signature,
                output_config_identity=output_config_identity,
            )
            existing_currency_checked = True
            existing_is_current = self._existing_forcing_version_is_current(
                existing,
                lead_window=lead_window,
                station_signature=station_signature,
                canonical_input_signature=canonical_input_signature,
                scheduler_canonical_identity=scheduler_canonical_identity,
                expected_lineage_identity=direct_grid_identity,
                expected_station_ids=station_signature["station_ids"],
                expected_valid_times=expected_valid_times,
                expected_variables=self.config.output_variables,
                expected_components=_forcing_components_for_products(
                    forcing_version_id=str(existing["forcing_version_id"])
                    if existing
                    else "",
                    products_by_variable=products_by_variable,
                ),
            )
            if existing_is_current:
                return self._return_existing_ready(
                    existing,
                    source_id=resolved_source_id,
                    cycle_time=parsed_cycle_time,
                    products_by_variable=products_by_variable,
                    expected_valid_times=expected_valid_times,
                )
            if existing is not None and str(existing.get("checksum") or "").strip():
                self._mark_forcing_version_pending(str(existing["forcing_version_id"]))
            self._ensure_direct_grid_met_stations(
                basin_version_id=resolved_basin_version_id,
                contract=forcing_mapping_contract,
            )
            self._materialize_direct_grid_mappings(
                contract=forcing_mapping_contract,
                source_id=resolved_source_id,
                model_id=model_id,
            )
            forcing_version_id = (
                str(existing["forcing_version_id"])
                if existing is not None
                else _forcing_version_id(
                    resolved_source_id, parsed_cycle_time, model_id
                )
            )
            direct_grid_weights = _weights_by_source_grid(
                self._direct_grid_weights_from_contract(
                    contract=forcing_mapping_contract,
                    source_id=resolved_source_id,
                    model_id=model_id,
                )
            )
            values, components = self._generate_timeseries_streaming(
                source_id=resolved_source_id,
                cycle_time=parsed_cycle_time,
                forcing_version_id=forcing_version_id,
                basin_version_id=resolved_basin_version_id,
                products_by_variable=products_by_variable,
                stations=direct_grid_stations,
                weights=direct_grid_weights,
                grid_points_by_source_grid=grid_points_by_source_grid,
                canonical_to_forcing=canonical_to_forcing,
                validate_all_field_values=False,
            )
            result = self._write_outputs_and_records(
                source_id=resolved_source_id,
                cycle_time=parsed_cycle_time,
                model_id=model_id,
                basin_id=resolved_basin_id,
                basin_version_id=resolved_basin_version_id,
                river_network_version_id=resolved_river_network_version_id,
                scheduler_canonical_identity=scheduler_canonical_identity,
                grid_id=forcing_mapping_contract.grid_id,
                stations=direct_grid_stations,
                rows=values,
                components=components,
                products_by_variable=products_by_variable,
                fallback_lineage=fallback_lineage,
                lead_window=lead_window,
                station_signature=station_signature,
                grid_signature_by_source_grid=grid_signature_by_source_grid,
                canonical_input_signature=canonical_input_signature,
                lineage_overrides=direct_grid_identity,
            )
            self._mark_cycle_ready_after_publication(
                source_id=resolved_source_id,
                cycle_time=parsed_cycle_time,
                forcing_version_id=result.forcing_version_id,
            )
            return result

        except Exception as error:
            if (
                existing is not None
                and not existing_currency_checked
                and str(existing.get("checksum") or "").strip()
            ):
                try:
                    self._mark_forcing_version_pending(
                        str(existing["forcing_version_id"])
                    )
                except Exception:
                    LOGGER.exception(
                        "Failed to clear stale finalized forcing version %s after validation failure.",
                        existing.get("forcing_version_id"),
                    )
            self._mark_failed(resolved_source_id, parsed_cycle_time, error)
            if isinstance(error, ForcingProductionError):
                raise
            raise ForcingProductionError(str(error)) from error

    def _read_canonical_grid(self, product: CanonicalProduct) -> tuple[GridPoint, ...]:
        try:
            import xarray as xr
        except ImportError as error:
            raise ForcingProductionError(
                "Reading canonical NetCDF4 products requires xarray."
            ) from error

        try:
            with open_canonical_netcdf(
                self.object_store,
                product.object_uri,
                expected_checksum=product.checksum,
            ) as dataset:
                data_variable = _validate_canonical_netcdf_identity(dataset, product)
                data_array = dataset[data_variable]
                expected_count = _data_array_size(data_array)
                self._enforce_limit(
                    "grid_cell_count", expected_count, self.config.max_grid_cell_count
                )
                grid_points = self._grid_points_for_dataset(
                    product,
                    dataset,
                    data_array,
                    _data_array_shape(data_array),
                    expected_count,
                )
                if len(grid_points) != expected_count:
                    raise ForcingProductionError(
                        f"Canonical product {product.canonical_product_id} has {expected_count} values but "
                        f"{len(grid_points)} grid points."
                    )
                _validate_grid_points(grid_points, product.canonical_product_id)
                return grid_points
        except ForcingProductionError:
            raise
        except (
            OSError,
            ObjectStoreError,
            TypeError,
            ValueError,
            SafeFilesystemError,
        ) as error:
            raise ForcingProductionError(
                f"Failed to read canonical product grid {product.canonical_product_id}: {error}"
            ) from error
        except Exception as error:
            raise ForcingProductionError(
                f"Failed to read canonical product grid {product.canonical_product_id}: {error}"
            ) from error

    def _read_canonical_field(
        self,
        product: CanonicalProduct,
        *,
        required_grid_cell_ids: AbstractSet[str] | None = None,
        expected_grid_points: Sequence[GridPoint] | None = None,
        retain_grid_points: bool = True,
        validate_all_values: bool = True,
    ) -> CanonicalField:
        try:
            import xarray as xr
        except ImportError as error:
            raise ForcingProductionError(
                "Reading canonical NetCDF4 products requires xarray."
            ) from error

        try:
            with open_canonical_netcdf(
                self.object_store,
                product.object_uri,
                expected_checksum=product.checksum,
            ) as dataset:
                data_variable = _validate_canonical_netcdf_identity(dataset, product)
                data_array = dataset[data_variable]
                expected_count = _data_array_size(data_array)
                self._enforce_limit(
                    "grid_cell_count", expected_count, self.config.max_grid_cell_count
                )
                grid_points = self._grid_points_for_dataset(
                    product,
                    dataset,
                    data_array,
                    _data_array_shape(data_array),
                    expected_count,
                )
                if len(grid_points) != expected_count:
                    raise ForcingProductionError(
                        f"Canonical product {product.canonical_product_id} has {expected_count} values but "
                        f"{len(grid_points)} grid points."
                    )
                _validate_grid_points(grid_points, product.canonical_product_id)
                if expected_grid_points is not None:
                    self._validate_field_grid_matches_product(
                        product, grid_points, expected_grid_points
                    )
                selected_indexes = _required_grid_indexes(
                    grid_points, required_grid_cell_ids
                )
                selected_values = _select_data_array_indexes(
                    data_array, selected_indexes
                ).values.ravel()
                if len(selected_values) != len(selected_indexes):
                    raise ForcingProductionError(
                        f"Canonical product {product.canonical_product_id} selected value count does not match "
                        "the requested grid cells."
                    )
                values_by_grid_cell_id: dict[str, float] = {}
                for index, raw_value in zip(
                    selected_indexes, selected_values, strict=True
                ):
                    point = grid_points[index]
                    value = float(raw_value)
                    if (
                        validate_all_values or required_grid_cell_ids is not None
                    ) and not math.isfinite(value):
                        raise ForcingProductionError(
                            f"Canonical product {product.canonical_product_id} has non-finite field value "
                            f"for grid cell {point.grid_cell_id}."
                        )
                    values_by_grid_cell_id[point.grid_cell_id] = value
                if required_grid_cell_ids is not None:
                    missing = sorted(
                        required_grid_cell_ids.difference(values_by_grid_cell_id)
                    )
                    if missing:
                        sample = ", ".join(missing[:5])
                        raise ForcingProductionError(
                            f"Canonical product {product.canonical_product_id} is missing required interpolation "
                            f"grid cells: {sample}."
                        )
                return CanonicalField(
                    product=product,
                    grid_points=grid_points if retain_grid_points else (),
                    values_by_grid_cell_id=values_by_grid_cell_id,
                )
        except ForcingProductionError:
            raise
        except (
            OSError,
            ObjectStoreError,
            TypeError,
            ValueError,
            SafeFilesystemError,
        ) as error:
            raise ForcingProductionError(
                f"Failed to read canonical product {product.canonical_product_id}: {error}"
            ) from error
        except Exception as error:
            raise ForcingProductionError(
                f"Failed to read canonical product {product.canonical_product_id}: {error}"
            ) from error

    def _grid_points_for_dataset(
        self,
        product: CanonicalProduct,
        dataset: Any,
        data_array: Any,
        shape: tuple[int, ...],
        expected_count: int,
    ) -> tuple[GridPoint, ...]:
        grid_from_definition = self._grid_points_from_definition(
            product, expected_count
        )
        if grid_from_definition:
            return grid_from_definition

        grid_cell_ids = _grid_cell_ids(dataset, expected_count)
        direct_coords = _direct_lon_lat_coords(dataset, expected_count)
        if direct_coords is not None:
            longitudes, latitudes = direct_coords
            return tuple(
                GridPoint(
                    grid_cell_id=grid_cell_id, longitude=longitude, latitude=latitude
                )
                for grid_cell_id, longitude, latitude in zip(
                    grid_cell_ids, longitudes, latitudes, strict=True
                )
            )

        rectilinear_coords = _rectilinear_lon_lat_coords(dataset, shape)
        if rectilinear_coords is not None and len(rectilinear_coords) == expected_count:
            return tuple(
                GridPoint(
                    grid_cell_id=grid_cell_id, longitude=longitude, latitude=latitude
                )
                for grid_cell_id, (longitude, latitude) in zip(
                    grid_cell_ids, rectilinear_coords, strict=True
                )
            )

        raise ForcingProductionError(
            f"Canonical product {product.canonical_product_id} does not provide usable geographic grid "
            "coordinates. Provide a readable grid_definition_uri with finite lon/lat cells or NetCDF "
            "longitude/latitude coordinates."
        )

    def _grid_points_from_definition(
        self,
        product: CanonicalProduct,
        expected_count: int,
    ) -> tuple[GridPoint, ...] | None:
        if not product.grid_definition_uri:
            return None
        try:
            content = self.object_store.read_bytes_limited(
                product.grid_definition_uri,
                max_bytes=self.config.max_manifest_bytes,
            )
            definition = load_bounded_json(
                content, max_bytes=self.config.max_manifest_bytes
            )
        except (
            OSError,
            ObjectStoreError,
            ValueError,
            BoundedJSONError,
        ) as error:
            raise ForcingProductionError(
                f"Failed to read canonical grid definition {product.grid_definition_uri} "
                f"for product {product.canonical_product_id}: {error}"
            ) from error
        if not isinstance(definition, Mapping):
            raise ForcingProductionError(
                f"Canonical grid definition {product.grid_definition_uri} for product "
                f"{product.canonical_product_id} must be a JSON object."
            )

        cells = definition.get("cells") or definition.get("points")
        if isinstance(cells, list):
            points: list[GridPoint] = []
            for index, cell in enumerate(cells):
                if not isinstance(cell, Mapping):
                    continue
                try:
                    longitude = float(cell.get("lon", cell.get("longitude")))
                    latitude = float(cell.get("lat", cell.get("latitude")))
                except (TypeError, ValueError):
                    return None
                if not _valid_geographic_coordinate(longitude, latitude):
                    return None
                points.append(
                    GridPoint(
                        grid_cell_id=str(
                            cell.get("grid_cell_id", cell.get("id", index))
                        ),
                        longitude=longitude,
                        latitude=latitude,
                    )
                )
            return tuple(points) if len(points) == expected_count else None
        if definition.get("layout") == "rectilinear":
            try:
                longitudes = tuple(float(value) for value in definition["longitudes"])
                latitudes = tuple(float(value) for value in definition["latitudes"])
                y_count, x_count = (int(value) for value in definition["shape"])
            except (KeyError, TypeError, ValueError):
                return None
            if (
                len(longitudes) != x_count
                or len(latitudes) != y_count
                or x_count * y_count != expected_count
            ):
                return None
            return tuple(
                GridPoint(
                    grid_cell_id=str(index),
                    longitude=_normalize_longitude(longitude),
                    latitude=latitude,
                )
                for index, (latitude, longitude) in enumerate(
                    (lat, lon) for lat in latitudes for lon in longitudes
                )
            )
        return None


del _ContractMethods, _InputMethods, _TimeseriesMethods, _OutputMethods, _ReuseMethods
