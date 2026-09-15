# NWM@8ae9b8f2 workers/forcing_producer/file_store.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from pathlib import Path
from typing import Any

from yd_producer.forcing._producer_common import parse_cycle_time
from yd_producer.forcing._producer_types import (
    CanonicalProduct,
    ForcingTimeseriesRow,
    InterpolationWeight,
    MetStation,
)
from yd_producer.forcing.direct_grid_contract import DirectGridContractError
from yd_producer.forcing.shud_forcing_contract import (
    CANONICAL_SHUD_FORCING_INDEX_MEMBER,
    SHUD_FORCING_INDEX_MEMBERS,
    SHUD_FORCING_ROLE,
)
from yd_producer.raw.source_identity import normalize_source_id

#: Inlined literals formerly imported from the non-snapshotted
#: ``forcing_domain_handoff`` module (inventory row 37 剥离点).
FORCING_DOMAIN_HANDOFF_CONTRACT_ID = "nhms.forcing_domain_handoff.v1"
FORCING_DOMAIN_PACKAGE_CONTRACT_ID = "nhms.forcing_domain_handoff.package.v1"
FORCING_DOMAIN_HANDOFF_SCHEMA_VERSION = "1.0"
FORCING_DOMAIN_PACKAGE_MANIFEST_URI_FIELD = "forcing_domain_package_manifest_uri"
FORCING_DOMAIN_PACKAGE_MANIFEST_CHECKSUM_FIELD = (
    "forcing_domain_package_manifest_checksum_sha256"
)
FORCING_PACKAGE_MANIFEST_URI_FIELD = "forcing_package_manifest_uri"
FORCING_PACKAGE_MANIFEST_CHECKSUM_FIELD = "forcing_package_manifest_checksum_sha256"


class ForcingStoreError(RuntimeError):
    """Stable public store error for forcing-chain IO failures."""


_FORECAST_PRODUCT_RE = re.compile(
    r"^(?P<source>.+)_(?P<cycle>\d{10})_(?P<variable>.+)_f(?P<lead>\d{3})\.nc$"
)
_NATIVE_RESOLUTION_RE = re.compile(
    r"^(?P<value>[1-9]\d*)(?P<unit>h|min)$", re.IGNORECASE
)

CANONICAL_PRODUCT_CATALOG_SCHEMA_VERSION = "nhms.canonical.product_catalog.v1"
CANONICAL_PRODUCT_CATALOG_ENVELOPE_KEYS = frozenset(
    {"schema_version", "source_id", "cycle_time", "products"}
)
CANONICAL_PRODUCT_CATALOG_ROW_KEYS = frozenset(
    {
        "canonical_product_id",
        "checksum",
        "cycle_time",
        "grid_definition_uri",
        "grid_id",
        "lead_time_hours",
        "lineage_json",
        "native_spatial_resolution",
        "native_time_resolution",
        "object_uri",
        "quality_flag",
        "source_id",
        "source_version",
        "unit",
        "valid_time",
        "variable",
    }
)
_CANONICAL_PRODUCT_CATALOG_REQUIRED_TEXT_FIELDS = frozenset(
    {
        "canonical_product_id",
        "checksum",
        "cycle_time",
        "grid_definition_uri",
        "grid_id",
        "native_spatial_resolution",
        "native_time_resolution",
        "object_uri",
        "quality_flag",
        "source_id",
        "source_version",
        "unit",
        "valid_time",
        "variable",
    }
)


def _registry_manifest_key(reference: str) -> str:
    normalized = reference.replace("\\", "/").strip()
    path = Path(normalized)
    if (
        not normalized
        or normalized.startswith("s3://")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or not normalized.startswith("models/")
    ):
        raise ForcingStoreError(
            "registry_manifest must be a non-empty relative object key under models/."
        )
    return normalized


def _safe_direct_grid_package_member(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = Path(normalized)
    if (
        not normalized
        or path.is_absolute()
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DirectGridContractError(
            "Direct-grid sp_att_path must be a safe model-package-relative path.",
            field="sp_att_path",
            details={"actual": value},
        )
    return normalized


def _parse_shud_tsd_forc_stations(
    content: str,
    *,
    basin_version_id: str,
    station_prefix: str,
) -> tuple[MetStation, ...]:
    stations: list[MetStation] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("ID") or stripped.startswith("/"):
            continue
        parts = stripped.split()
        if len(parts) < 7 or not parts[0].isdigit():
            continue
        forcing_index = int(parts[0])
        try:
            longitude = float(parts[1])
            latitude = float(parts[2])
            elevation = float(parts[5])
        except ValueError:
            continue
        filename = parts[6]
        if elevation < 0:
            elevation = 0.0
        station_id = f"{station_prefix}_forc_{forcing_index:03d}"
        stations.append(
            MetStation(
                station_id=station_id,
                basin_version_id=basin_version_id,
                longitude=longitude,
                latitude=latitude,
                elevation_m=elevation,
                station_role="forcing_grid",
                station_name=f"{station_prefix.upper()} forcing station {forcing_index:03d}",
                properties_json={
                    "shud_forcing_index": forcing_index,
                    "forcing_filename": filename,
                    "manifest_authority": True,
                },
            )
        )
    return tuple(stations)


def _grid_id_for_source(source_id: str) -> str:
    return {
        "gfs": "gfs_0p25",
        "era5": "era5_0p25",
        "ifs": "ifs_0p25",
    }[normalize_source_id(source_id)]


def _grid_definition_uri_for_source(source_id: str) -> str:
    normalized = normalize_source_id(source_id)
    return {
        "gfs": "canonical/gfs/grid/gfs_0p25/grid.json",
        "era5": "canonical/ERA5/grid/era5_0p25/grid.json",
        "ifs": "canonical/ifs/grid/ifs_0p25/grid.json",
    }[normalized]


def _native_time_resolution_for_source(source_id: str) -> str:
    return "1h" if normalize_source_id(source_id) == "ERA5" else "3h"


def _lead_sort_key(product: CanonicalProduct) -> tuple[int, datetime, str]:
    lead_time = (
        product.lead_time_hours if product.lead_time_hours is not None else 999_999
    )
    return (int(lead_time), product.cycle_time, product.canonical_product_id)


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_object(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        default=_json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return _format_time(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable.")


def _time_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _ensure_utc(value)
    return parse_cycle_time(str(value))


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return _ensure_utc(value).isoformat().replace("+00:00", "Z")


def _forcing_package_manifest_uri(record: Mapping[str, Any], package_uri: str) -> str:
    lineage = record.get("lineage_json")
    if isinstance(lineage, Mapping):
        value = lineage.get("forcing_package_manifest_uri")
        if value:
            return str(value)
    return f"{package_uri.rstrip('/')}/forcing_package.json"


def _lineage_value(record: Mapping[str, Any], key: str) -> Any:
    lineage = record.get("lineage_json")
    if isinstance(lineage, Mapping):
        return lineage.get(key)
    return None


def _handoff_run_id(
    record: Mapping[str, Any],
    *,
    source_key: str,
    compact_cycle: str,
    model_id: str,
) -> str:
    lineage_run_id = _lineage_value(record, "run_id")
    if lineage_run_id:
        return str(lineage_run_id)
    value = record.get("run_id")
    if value:
        return str(value)
    return f"fcst_{source_key}_{compact_cycle}_{model_id}"


def _manifest_station_order(
    package_manifest: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    raw_stations = package_manifest.get("station_order")
    stations: dict[str, Mapping[str, Any]] = {}
    if not isinstance(raw_stations, Sequence) or isinstance(
        raw_stations, str | bytes | bytearray
    ):
        return stations
    for index, item in enumerate(raw_stations, start=1):
        if isinstance(item, Mapping):
            station_id = str(item.get("station_id") or "").strip()
            if station_id:
                stations[station_id] = item
        elif isinstance(item, str) and item.strip():
            stations[item.strip()] = {
                "station_id": item.strip(),
                "shud_forcing_index": index,
            }
    return stations


def _station_index_member_basename(package_manifest: Mapping[str, Any]) -> str | None:
    """Return the basename of the station-index member this package declares.

    Station handoff provenance (`properties_json.source`) records the member the
    package *actually* carries instead of a hard-coded basin slug (issue #1359):
    a canonical package yields ``stations.tsd.forc``, a historical replay package
    yields ``qhh.tsd.forc`` — truthfully, because that member exists there.

    Selection mirrors the producer's manifest shape (`producer.py` shud file
    entries): an entry qualifies only when its ``role`` is the SHUD forcing-index
    role **and** its ``relative_path`` is an accepted member. Role alone is not
    narrow enough; membership alone would not survive a role drift. Non-Mapping
    elements are skipped exactly like `_manifest_station_order` does — a
    provenance label must never escalate into an exception that aborts a
    production write. Returns ``None`` when nothing resolves, so the caller can
    omit the key rather than fabricate a value.
    """

    raw_files = package_manifest.get("files")
    if not isinstance(raw_files, list):
        return None
    members: list[str] = []
    for entry in raw_files:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("role") != SHUD_FORCING_ROLE:
            continue
        relative_path = entry.get("relative_path")
        if relative_path in SHUD_FORCING_INDEX_MEMBERS:
            members.append(str(relative_path))
    if not members:
        return None
    # A package declaring both members is pathological but readable: prefer the
    # canonical one (same "named-one-else-canonical" preference the
    # non-direct-grid member resolution already uses).
    member = (
        CANONICAL_SHUD_FORCING_INDEX_MEMBER
        if CANONICAL_SHUD_FORCING_INDEX_MEMBER in members
        else members[0]
    )
    return posixpath.basename(member)


def _float_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted


def _station_name_from_id(basin_id: str, station_id: str) -> str:
    match = re.search(r"(\d+)$", station_id)
    prefix = _station_name_prefix(basin_id)
    if match:
        return f"{prefix} forcing station {int(match.group(1)):03d}"
    return f"{prefix} forcing station {station_id}"


def _station_name_prefix(basin_id: str) -> str:
    value = basin_id.strip()
    if value.lower().startswith("basins_"):
        value = value[7:]
    return value.upper()


def _handoff_timeseries_row(
    row: ForcingTimeseriesRow, *, native_resolution: str | None = None
) -> dict[str, Any]:
    return {
        "forcing_version_id": row.forcing_version_id,
        "basin_version_id": row.basin_version_id,
        "station_id": row.station_id,
        "valid_time": _format_time(row.valid_time),
        "source_id": row.source_id,
        "variable": row.variable,
        "value": row.value,
        "unit": row.unit,
        "native_resolution": native_resolution or row.native_resolution,
        "quality_flag": row.quality_flag,
    }


def _timeseries_sort_key(row: ForcingTimeseriesRow) -> tuple[str, datetime, str]:
    return (row.station_id, _ensure_utc(row.valid_time), row.variable)


def _weight_sort_key(
    weight: InterpolationWeight,
) -> tuple[str, str, str, str, str, str]:
    return (
        weight.source_id,
        weight.grid_id,
        weight.model_id,
        weight.station_id,
        weight.variable,
        weight.grid_cell_id,
    )


def _first_unit(rows: Sequence[ForcingTimeseriesRow], variable: str) -> str:
    for row in rows:
        if row.variable == variable:
            return row.unit
    return ""


def _time_lattice(
    native_resolution_by_time: Mapping[tuple[str, datetime], str],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    variables = sorted(
        {variable for variable, _valid_time in native_resolution_by_time}
    )
    for variable in variables:
        ordered_points = sorted(
            (valid_time, native_resolution)
            for (
                row_variable,
                valid_time,
            ), native_resolution in native_resolution_by_time.items()
            if row_variable == variable
        )
        if not ordered_points:
            continue

        segment_start, segment_resolution = ordered_points[0]
        segment_end = segment_start
        segment_delta = _native_resolution_delta(segment_resolution)
        for valid_time, native_resolution in ordered_points[1:]:
            expected_next = (
                segment_end + segment_delta if segment_delta is not None else None
            )
            if native_resolution == segment_resolution and expected_next == valid_time:
                segment_end = valid_time
                continue
            segments.append(
                _time_lattice_segment(
                    variable, segment_start, segment_end, segment_resolution
                )
            )
            segment_start = valid_time
            segment_end = valid_time
            segment_resolution = native_resolution
            segment_delta = _native_resolution_delta(segment_resolution)
        segments.append(
            _time_lattice_segment(
                variable, segment_start, segment_end, segment_resolution
            )
        )
    return segments


def _time_lattice_resolution_by_variable_time(
    rows: Sequence[ForcingTimeseriesRow],
) -> dict[tuple[str, datetime], str]:
    native_resolution_by_time: dict[tuple[str, datetime], str] = {}
    variables = sorted({row.variable for row in rows})
    for variable in variables:
        rows_for_variable = [row for row in rows if row.variable == variable]
        labels_by_time: dict[datetime, set[str]] = {}
        for row in rows_for_variable:
            label = str(row.native_resolution or "").strip()
            if label:
                labels_by_time.setdefault(_ensure_utc(row.valid_time), set()).add(label)
        ordered_times = sorted(
            {_ensure_utc(row.valid_time) for row in rows_for_variable}
        )
        if not ordered_times:
            continue
        for index, valid_time in enumerate(ordered_times):
            if index > 0:
                inferred = _duration_label(valid_time - ordered_times[index - 1])
            elif len(ordered_times) > 1:
                inferred = _duration_label(ordered_times[1] - valid_time)
            else:
                inferred = None
            existing = sorted(labels_by_time.get(valid_time, ()))
            native_resolution = inferred or (existing[0] if existing else None)
            if native_resolution:
                native_resolution_by_time[(variable, valid_time)] = native_resolution
    return native_resolution_by_time


def _time_lattice_segment(
    variable: str, start: datetime, end: datetime, native_resolution: str
) -> dict[str, Any]:
    return {
        "variable": variable,
        "valid_time_start": _format_time(start),
        "valid_time_end": _format_time(end),
        "native_resolution": native_resolution,
    }


def _duration_label(delta: timedelta) -> str | None:
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return None
    if total_seconds % 3600 == 0:
        return f"{total_seconds // 3600}h"
    if total_seconds % 60 == 0:
        return f"{total_seconds // 60}min"
    return None


def _native_resolution_delta(native_resolution: str) -> timedelta | None:
    match = _NATIVE_RESOLUTION_RE.match(native_resolution)
    if match is None:
        return None
    value = int(match.group("value"))
    if match.group("unit").lower() == "h":
        return timedelta(hours=value)
    return timedelta(minutes=value)
