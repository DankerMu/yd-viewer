# NWM@8ae9b8f2 workers/forcing_producer/producer.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import math
import re
from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import datetime
from pathlib import Path
from typing import Any

from yd_producer.forcing._producer_common import (
    _SAFE_PATH_COMPONENT,
    _optional_int,
)
from yd_producer.forcing._producer_types import (
    CanonicalProduct,
    ForcingProductionError,
    ForcingTimeseriesRow,
    MetStation,
)
from yd_producer.forcing.canonical_json import _json_bytes
from yd_producer.forcing.direct_grid_contract import (
    DIRECT_GRID_MODE,
    DirectGridForcingContract,
)
from yd_producer.forcing.shud_forcing_contract import SHUD_FORCING_INDEX_BASENAMES
from yd_producer.store.object_store import sha256_bytes


def _station_properties(station: MetStation) -> Mapping[str, Any]:
    properties = getattr(station, "properties_json", None)
    return properties if isinstance(properties, Mapping) else {}


def _validate_forcing_grid_station_contract(station: MetStation) -> None:
    props = _station_properties(station)
    raw_index = props.get("shud_forcing_index")
    if raw_index in (None, ""):
        raise ForcingProductionError(
            f"Fixed forcing_grid station {station.station_id} is missing shud_forcing_index metadata."
        )
    try:
        forcing_index = int(raw_index)
    except (TypeError, ValueError) as error:
        raise ForcingProductionError(
            f"Fixed forcing_grid station {station.station_id} has invalid shud_forcing_index metadata."
        ) from error
    if forcing_index < 1:
        raise ForcingProductionError(
            f"Fixed forcing_grid station {station.station_id} has non-positive shud_forcing_index metadata."
        )
    raw_filename = props.get("forcing_filename")
    filename = str(raw_filename or "").strip()
    if not _safe_station_forcing_filename(filename):
        raise ForcingProductionError(
            f"Fixed forcing_grid station {station.station_id} is missing a safe forcing_filename metadata value."
        )


def _validate_unique_station_forcing_contract(stations: Sequence[MetStation]) -> None:
    indexes: dict[int, str] = {}
    filenames: dict[str, str] = {}
    reserved = {name.casefold() for name in _reserved_shud_station_filenames()}
    for station in stations:
        forcing_index = _station_forcing_index(station)
        filename = _station_forcing_filename(station, forcing_index)
        filename_key = filename.casefold()
        if filename_key in reserved:
            raise ForcingProductionError(
                f"Reserved SHUD forcing filename {filename!r} cannot be used for station {station.station_id}."
            )
        existing_station_id = indexes.setdefault(forcing_index, station.station_id)
        if existing_station_id != station.station_id:
            raise ForcingProductionError(
                f"Duplicate SHUD forcing index {forcing_index} for stations "
                f"{existing_station_id} and {station.station_id}."
            )
        existing_filename_station_id = filenames.setdefault(
            filename_key, station.station_id
        )
        if existing_filename_station_id != station.station_id:
            raise ForcingProductionError(
                f"Duplicate SHUD forcing filename {filename!r} for stations "
                f"{existing_filename_station_id} and {station.station_id}."
            )
    expected_indexes = list(range(1, len(stations) + 1))
    actual_indexes = sorted(indexes)
    if actual_indexes != expected_indexes:
        raise ForcingProductionError(
            "Fixed forcing_grid stations must use contiguous SHUD forcing indexes "
            f"{expected_indexes}; got {actual_indexes}."
        )


def _met_stations_from_direct_grid_contract(
    contract: DirectGridForcingContract,
    *,
    basin_version_id: str,
) -> tuple[MetStation, ...]:
    stations = tuple(
        MetStation(
            station_id=station.station_id,
            basin_version_id=basin_version_id,
            longitude=station.longitude,
            latitude=station.latitude,
            elevation_m=station.z,
            station_role="forcing_grid",
            station_name=f"Direct-grid station {station.shud_forcing_index}",
            properties_json={
                **dict(station.properties),
                "shud_forcing_index": station.shud_forcing_index,
                "forcing_filename": station.forcing_filename,
                "x": station.x,
                "y": station.y,
                "z": station.z,
                "forcing_mapping_mode": DIRECT_GRID_MODE,
                "grid_id": station.grid_id,
                "grid_cell_id": station.grid_cell_id,
                "binding_checksum": contract.binding_checksum,
                "grid_signature": contract.grid_signature,
            },
        )
        for station in sorted(
            contract.stations, key=lambda item: item.shud_forcing_index
        )
    )
    _validate_unique_station_forcing_contract(stations)
    return stations


def _station_forcing_index(station: MetStation) -> int:
    props = _station_properties(station)
    value = props.get("shud_forcing_index")
    if value is not None:
        return int(value)
    match = re.search(r"(\d+)$", station.station_id)
    return int(match.group(1)) if match else 1


def _station_forcing_filename(station: MetStation, forcing_index: int) -> str:
    props = _station_properties(station)
    filename = str(props.get("forcing_filename") or "").strip()
    if _safe_station_forcing_filename(filename):
        return filename
    return f"forcing_{forcing_index:03d}.csv"


def _safe_station_forcing_filename(filename: str) -> bool:
    return bool(
        filename
        and "/" not in filename
        and "\\" not in filename
        and "\x00" not in filename
        and ".." not in Path(filename).parts
        and filename not in {".", ".."}
        and _SAFE_PATH_COMPONENT.fullmatch(filename) is not None
    )


def _reserved_shud_station_filenames() -> set[str]:
    # Both station-index basenames stay reserved: a station CSV must never
    # collide with the canonical index name nor with the legacy one still
    # carried by historical packages.
    return {
        *SHUD_FORCING_INDEX_BASENAMES,
        "forcing_package.json",
        "forcing_debug.csv",
        "forcing.tsd.forc",
    }


_PACKAGE_INTERNAL_NAMES = frozenset(
    {
        "forcing_version_record.json",
        "forcing_domain_package.json",
        "shud",
        "payloads",
    }
)


def _validate_package_filenames(
    *,
    forcing_filename: str,
    csv_filename: str,
    package_manifest_filename: str,
    stations: Sequence[MetStation],
) -> None:
    package_names = {
        "forcing_filename": forcing_filename,
        "csv_filename": csv_filename,
        "package_manifest_filename": package_manifest_filename,
    }
    seen: dict[str, str] = {}
    internal_names = {name.casefold() for name in _PACKAGE_INTERNAL_NAMES}
    for field_name, filename in package_names.items():
        filename_text = str(filename)
        if not _safe_station_forcing_filename(filename_text):
            raise ForcingProductionError(
                f"Configured package filename {field_name}={filename!r} is unsafe."
            )
        filename_key = filename_text.casefold()
        if filename_key in internal_names:
            raise ForcingProductionError(
                f"Configured package filename {field_name}={filename!r} is reserved."
            )
        previous = seen.setdefault(filename_key, field_name)
        if previous != field_name:
            raise ForcingProductionError(
                f"Configured package filename {filename!r} is reused by {previous}."
            )
    station_names = {
        _station_forcing_filename(station, _station_forcing_index(station))
        for station in stations
    }
    reserved = {name.casefold() for name in _reserved_shud_station_filenames()}
    collisions = sorted(name for name in station_names if name.casefold() in reserved)
    if collisions:
        raise ForcingProductionError(
            "Station forcing filenames collide with reserved SHUD/package names: "
            + ", ".join(collisions)
        )


def _station_forcing_sort_key(station: MetStation) -> tuple[int, str]:
    return (_station_forcing_index(station), station.station_id)


def _station_order_manifest(stations: Sequence[MetStation]) -> list[dict[str, Any]]:
    return [
        {
            "station_id": station.station_id,
            "shud_forcing_index": _station_forcing_index(station),
            "forcing_filename": _station_forcing_filename(
                station, _station_forcing_index(station)
            ),
            "longitude": float(station.longitude),
            "latitude": float(station.latitude),
            "elevation_m": float(station.elevation_m),
        }
        for station in sorted(stations, key=_station_forcing_sort_key)
    ]


def _quality_flags_manifest(
    rows: Sequence[ForcingTimeseriesRow],
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
) -> dict[str, Any]:
    return {
        "station_timeseries": sorted({row.quality_flag for row in rows}),
        "canonical_products": sorted(
            {
                product.quality_flag
                for products_for_variable in products_by_variable.values()
                for product in products_for_variable.values()
            }
        ),
    }


def _valid_station(station: MetStation) -> bool:
    return (
        math.isfinite(station.longitude)
        and math.isfinite(station.latitude)
        and -180.0 <= station.longitude <= 180.0
        and -90.0 <= station.latitude <= 90.0
        and math.isfinite(station.elevation_m)
    )


def _station_signature(stations: Sequence[MetStation]) -> dict[str, Any]:
    station_rows = [
        {
            "station_id": station.station_id,
            "station_role": station.station_role,
            "longitude": round(float(station.longitude), 12),
            "latitude": round(float(station.latitude), 12),
            "elevation_m": round(float(station.elevation_m), 6),
            "shud_forcing_index": _station_properties(station).get(
                "shud_forcing_index"
            ),
            "forcing_filename": _station_forcing_filename(
                station, _station_forcing_index(station)
            ),
        }
        for station in sorted(stations, key=lambda item: item.station_id)
    ]
    checksum = sha256_bytes(_json_bytes({"stations": station_rows}))
    return {
        "schema_version": "nhms.forcing_station_signature.v1",
        "station_count": len(station_rows),
        "station_ids": [row["station_id"] for row in station_rows],
        "checksum": checksum,
        "stations": station_rows,
    }


def _station_signature_matches(existing: Any, current: Mapping[str, Any]) -> bool:
    if not isinstance(existing, Mapping):
        return False
    return (
        _optional_int(existing.get("station_count")) == int(current["station_count"])
        and list(existing.get("station_ids") or []) == list(current["station_ids"])
        and str(existing.get("checksum") or "") == str(current["checksum"])
    )
