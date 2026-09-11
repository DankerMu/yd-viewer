# NWM@8ae9b8f2 workers/forcing_producer/producer.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from dataclasses import (
    dataclass,
    field,
)
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    Protocol,
)

from yd_producer.forcing.direct_grid_contract import DirectGridForcingContract

REQUIRED_CANONICAL_VARIABLES: tuple[str, ...] = (
    "prcp_rate_or_amount",
    "air_temperature_2m",
    "relative_humidity_2m",
    "wind_u_10m",
    "wind_v_10m",
    "pressure_surface",
    "shortwave_down",
)
ERA5_REQUIRED_CANONICAL_VARIABLES: tuple[str, ...] = (
    "prcp_rate_or_amount",
    "air_temperature_2m",
    "relative_humidity_2m",
    "wind_u_10m",
    "wind_v_10m",
    "pressure_surface",
    "net_radiation",
)
IFS_REQUIRED_CANONICAL_VARIABLES: tuple[str, ...] = (
    "prcp_rate_or_amount",
    "air_temperature_2m",
    "relative_humidity_2m",
    "wind_u_10m",
    "wind_v_10m",
    "surface_pressure",
    "shortwave_down",
)
FORCING_VARIABLES: tuple[str, ...] = ("PRCP", "TEMP", "RH", "wind", "Rn", "Press")
OUTPUT_UNITS: dict[str, str] = {
    "PRCP": "mm/day",
    "TEMP": "degC",
    "RH": "0-1",
    "wind": "m/s",
    "Rn": "W/m2",
    "Press": "Pa",
}
EXPECTED_CANONICAL_UNITS: dict[str, tuple[str, ...]] = {
    "prcp_rate_or_amount": ("mm/day",),
    "air_temperature_2m": ("degC",),
    "relative_humidity_2m": ("0-1",),
    "wind_u_10m": ("m/s",),
    "wind_v_10m": ("m/s",),
    "pressure_surface": ("Pa",),
    "surface_pressure": ("Pa",),
    "shortwave_down": ("W/m2",),
    "net_radiation": ("W/m2",),
}
CANONICAL_TO_FORCING: dict[str, str] = {
    "prcp_rate_or_amount": "PRCP",
    "air_temperature_2m": "TEMP",
    "relative_humidity_2m": "RH",
    "shortwave_down": "Rn",
    "pressure_surface": "Press",
}
ERA5_CANONICAL_TO_FORCING: dict[str, str] = {
    "prcp_rate_or_amount": "PRCP",
    "air_temperature_2m": "TEMP",
    "relative_humidity_2m": "RH",
    "net_radiation": "Rn",
    "pressure_surface": "Press",
}
IFS_CANONICAL_TO_FORCING: dict[str, str] = {
    "prcp_rate_or_amount": "PRCP",
    "air_temperature_2m": "TEMP",
    "relative_humidity_2m": "RH",
    "shortwave_down": "Rn",
    "surface_pressure": "Press",
}
ERA5_FALLBACK_SOURCE_ID = "gfs"
ERA5_LATENCY_FALLBACK_REASON = "era5_latency"


class ForcingProductionError(RuntimeError):
    """Raised when forcing production cannot complete."""


@dataclass(frozen=True)
class MetStation:
    station_id: str
    basin_version_id: str
    longitude: float
    latitude: float
    elevation_m: float
    station_role: str
    station_name: str | None = None
    properties_json: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GridPoint:
    grid_cell_id: str
    longitude: float
    latitude: float


@dataclass(frozen=True)
class InterpolationWeight:
    source_id: str
    grid_id: str
    model_id: str
    station_id: str
    variable: str
    grid_cell_id: str
    weight: float
    method: str = "direct_grid"


@dataclass(frozen=True)
class CanonicalProduct:
    canonical_product_id: str
    source_id: str
    cycle_time: datetime
    valid_time: datetime
    variable: str
    unit: str
    grid_id: str
    object_uri: str
    checksum: str
    grid_definition_uri: str | None = None
    native_time_resolution: str | None = None
    native_spatial_resolution: str | None = None
    quality_flag: str = "ok"
    lead_time_hours: int | None = None
    lineage_json: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalField:
    product: CanonicalProduct
    grid_points: tuple[GridPoint, ...]
    values_by_grid_cell_id: Mapping[str, float]


@dataclass(frozen=True)
class ForcingTimeseriesRow:
    forcing_version_id: str
    basin_version_id: str
    station_id: str
    valid_time: datetime
    source_id: str
    variable: str
    value: float
    unit: str
    native_resolution: str | None
    quality_flag: str = "ok"


@dataclass(frozen=True)
class ForcingComponent:
    forcing_version_id: str
    canonical_product_id: str
    variable: str
    valid_time_start: datetime
    valid_time_end: datetime
    role: str = "forcing_input"


@dataclass(frozen=True)
class ForcingProductionResult:
    status: str
    forcing_version_id: str
    forcing_package_uri: str
    checksum: str | None
    station_count: int
    timestep_count: int
    variable_count: int = 0
    time_range: Mapping[str, str | int] = field(default_factory=dict)
    units: Mapping[str, str] = field(default_factory=dict)
    file_uris: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FallbackLineage:
    fallback_reason: str
    fallback_source_id: str
    fallback_valid_times: tuple[datetime, ...]


class ForcingRepository(Protocol):
    def resolve_model_identity(self, *, model_id: str) -> Mapping[str, Any]: ...

    def resolve_model_basin_version(self, *, model_id: str) -> str: ...

    def load_met_stations(self, *, basin_version_id: str) -> tuple[MetStation, ...]: ...

    def list_canonical_products(
        self, *, source_id: str, cycle_time: datetime
    ) -> tuple[CanonicalProduct, ...]: ...

    def list_fallback_canonical_products(
        self,
        *,
        source_id: str,
        start_time: datetime,
        end_time: datetime,
        variables: Sequence[str],
    ) -> tuple[CanonicalProduct, ...]: ...

    def load_interp_weights(
        self,
        *,
        source_id: str,
        grid_id: str,
        model_id: str,
    ) -> tuple[InterpolationWeight, ...]: ...

    def upsert_interp_weights(self, weights: Sequence[InterpolationWeight]) -> None: ...

    def ensure_direct_grid_met_stations(
        self,
        *,
        basin_version_id: str,
        contract: DirectGridForcingContract,
    ) -> None: ...

    def load_forcing_mapping_contract(
        self,
        *,
        model_id: str,
        basin_version_id: str,
        source_id: str | None = None,
    ) -> DirectGridForcingContract | None: ...

    def load_direct_grid_validation_assets(
        self,
        *,
        model_id: str,
        basin_version_id: str,
        contract: DirectGridForcingContract,
        max_bytes: int,
    ) -> Mapping[str, Any]: ...

    def get_forcing_version(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        model_id: str,
    ) -> dict[str, Any] | None: ...

    def upsert_forcing_version(self, record: Mapping[str, Any]) -> dict[str, Any]: ...

    def finalize_forcing_version(
        self, forcing_version_id: str, checksum: str
    ) -> dict[str, Any]: ...

    def clear_forcing_version_checksum(
        self, forcing_version_id: str
    ) -> dict[str, Any]: ...

    def verify_forcing_version_children(
        self,
        *,
        forcing_version_id: str,
        expected_components: Sequence[ForcingComponent],
        expected_station_ids: Sequence[str],
        expected_valid_times: Sequence[datetime],
        expected_variables: Sequence[str],
    ) -> Mapping[str, Any]: ...

    def replace_forcing_components(
        self, forcing_version_id: str, components: Sequence[ForcingComponent]
    ) -> None: ...

    def replace_forcing_timeseries(
        self,
        forcing_version_id: str,
        rows: Sequence[ForcingTimeseriesRow],
    ) -> None: ...

    def update_forecast_cycle(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True, kw_only=True)
class ForcingProducerConfig:
    source_id: str = "gfs"
    workspace_root: Path | str
    object_store_root: Path | str
    object_store_prefix: str
    rn_shortwave_factor: float = 1.0
    producer_version: str = "m2.1"
    forcing_filename: str = "forcing.tsd.forc"
    csv_filename: str = "forcing_debug.csv"
    package_manifest_filename: str = "forcing_package.json"
    output_variables: tuple[str, ...] = FORCING_VARIABLES
    required_canonical_variables: tuple[str, ...] = REQUIRED_CANONICAL_VARIABLES
    era5_latency_fallback_hours: int = 23
    max_station_count: int = 10_000
    max_timestep_count: int = 10_000
    max_grid_cell_count: int = 5_000_000
    max_timeseries_row_count: int = 10_000_000
    max_manifest_bytes: int = 33_554_432
    min_lead_hours: int | None = None
