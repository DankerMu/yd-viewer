# NWM@8ae9b8f2 workers/forcing_producer/producer.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import (
    UTC,
    datetime,
)
from typing import (
    AbstractSet,
    Any,
)

from yd_producer.forcing._producer_types import (
    CanonicalProduct,
    ForcingProductionError,
)
from yd_producer.forcing.canonical_json import (
    _json_bytes,
    _json_default,
)
from yd_producer.raw.source_identity import normalize_source_id
from yd_producer.store.object_store import LocalObjectStore


def wind_speed(u_value: float, v_value: float) -> float:
    return math.sqrt(u_value**2 + v_value**2)


def parse_cycle_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return _ensure_utc(value)
    candidate = value.strip()
    if len(candidate) == 10 and candidate.isdigit():
        return datetime.strptime(candidate, "%Y%m%d%H").replace(tzinfo=UTC)
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    return _ensure_utc(datetime.fromisoformat(candidate))


def format_cycle_time(value: str | datetime) -> str:
    return parse_cycle_time(value).strftime("%Y%m%d%H")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return _ensure_utc(value).isoformat().replace("+00:00", "Z")


def _format_number(value: float) -> str:
    return f"{float(value):.10g}"


def _valid_geographic_coordinate(longitude: float, latitude: float) -> bool:
    return (
        math.isfinite(longitude)
        and math.isfinite(latitude)
        and -180.0 <= longitude <= 180.0
        and -90.0 <= latitude <= 90.0
    )


def _normalize_longitude(longitude: float) -> float:
    if longitude > 180.0:
        return longitude - 360.0
    return longitude


def _products(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
) -> tuple[CanonicalProduct, ...]:
    return tuple(
        product
        for products_for_variable in products_by_variable.values()
        for product in products_for_variable.values()
    )


def _stable_identity(value: Any) -> str:
    if value in (None, ""):
        return ""
    return json.dumps(
        _json_round_trip(value),
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_round_trip(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=_json_default))


def _normalize_checksum_identity(value: str) -> str:
    return value.removeprefix("sha256:").strip().lower()


def _direct_grid_validation_error(
    message: str,
    *,
    field: str,
    source: str,
    expected: Any,
    actual: Any,
    details: Mapping[str, Any] | None = None,
) -> ForcingProductionError:
    payload = {
        "error_code": "DIRECT_GRID_VALIDATION_FAILED",
        "message": message,
        "field": field,
        "source": source,
        "expected": _json_round_trip(expected),
        "actual": _json_round_trip(actual),
    }
    payload.update(dict(details or {}))
    return ForcingProductionError(
        f"Direct-grid validation failed: {_json_bytes(payload).decode('utf-8')}"
    )


def _direct_grid_text_asset(value: Any, *, field: str) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise _direct_grid_validation_error(
                "Direct-grid text asset must be UTF-8.",
                field=field,
                source="repository",
                expected="UTF-8 text",
                actual="non-UTF-8 bytes",
            ) from error
    if isinstance(value, str):
        return value
    raise _direct_grid_validation_error(
        "Direct-grid validation asset is missing.",
        field=field,
        source="repository",
        expected="text",
        actual=type(value).__name__,
    )


def _parse_sp_att_forc_values(content: str) -> tuple[tuple[int, int], ...]:
    lines = content.splitlines()
    if len(lines) < 3:
        raise _direct_grid_validation_error(
            "Direct-grid .sp.att is missing FORC data rows.",
            field="sp_att.FORC",
            source="sp_att",
            expected="FORC column with triangle rows",
            actual="missing",
        )
    header_tokens = lines[1].split()
    try:
        forcing_column = next(
            index
            for index, token in enumerate(header_tokens)
            if token.upper() == "FORC"
        )
    except StopIteration:
        forcing_column = 4
    values: list[tuple[int, int]] = []
    for line_number, line in enumerate(lines[2:], start=3):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) <= forcing_column:
            raise _direct_grid_validation_error(
                "Direct-grid .sp.att row is missing FORC.",
                field="sp_att.FORC",
                source="sp_att",
                expected="integer shud_forcing_index",
                actual="missing",
                details={"line_number": line_number},
            )
        raw_value = parts[forcing_column]
        try:
            value = int(raw_value)
        except ValueError as error:
            raise _direct_grid_validation_error(
                "Direct-grid .sp.att FORC must be an integer.",
                field="sp_att.FORC",
                source="sp_att",
                expected="integer shud_forcing_index",
                actual=raw_value,
                details={"line_number": line_number},
            ) from error
        values.append((line_number, value))
    if not values:
        raise _direct_grid_validation_error(
            "Direct-grid .sp.att is missing FORC data rows.",
            field="sp_att.FORC",
            source="sp_att",
            expected="at least one FORC value",
            actual="missing",
        )
    return tuple(values)


def _validate_sp_att_forc_values(
    forc_values: Sequence[tuple[int, int]],
    *,
    valid_indexes: AbstractSet[int],
) -> None:
    expected = tuple(sorted(valid_indexes))
    actual = tuple(sorted({value for _, value in forc_values}))
    for line_number, value in forc_values:
        if value <= 0:
            raise _direct_grid_validation_error(
                "Direct-grid .sp.att FORC must be positive.",
                field="sp_att.FORC",
                source="sp_att",
                expected=expected,
                actual=value,
                details={"line_number": line_number},
            )
        if value not in valid_indexes:
            raise _direct_grid_validation_error(
                "Direct-grid .sp.att FORC references an unknown shud_forcing_index.",
                field="sp_att.FORC",
                source="sp_att",
                expected=expected,
                actual=value,
                details={"line_number": line_number},
            )
    missing = tuple(index for index in expected if index not in actual)
    if missing:
        raise _direct_grid_validation_error(
            "Direct-grid .sp.att FORC is missing bound shud_forcing_index values.",
            field="sp_att.FORC",
            source="sp_att",
            expected=expected,
            actual=actual,
            details={"missing_indexes": missing},
        )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _product_lead_hours(product: CanonicalProduct, cycle_time: datetime) -> int:
    if product.lead_time_hours is not None:
        return int(product.lead_time_hours)
    elapsed_seconds = (
        _ensure_utc(product.valid_time) - _ensure_utc(cycle_time)
    ).total_seconds()
    return int(round(elapsed_seconds / 3600.0))


def _is_era5_source(source_id: str) -> bool:
    return normalize_source_id(source_id) == "ERA5"


def _is_ifs_source(source_id: str) -> bool:
    return normalize_source_id(source_id) == "ifs"


def _uses_era5_latency_fallback(source_id: str) -> bool:
    return _is_era5_source(source_id)


def _distance_degrees(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> float:
    longitude_scale = math.cos(math.radians((lat_a + lat_b) / 2.0))
    return math.hypot((lon_a - lon_b) * longitude_scale, lat_a - lat_b)


def _forcing_version_id(source_id: str, cycle_time: datetime, model_id: str) -> str:
    return f"forc_{_object_source_segment(source_id)}_{format_cycle_time(cycle_time)}_{model_id}"


def _object_source_segment(source_id: str) -> str:
    return normalize_source_id(source_id).lower()


def _directory_uri(object_store: LocalObjectStore, key_prefix: str) -> str:
    prefix = object_store.object_store_prefix.rstrip("/")
    if not prefix:
        return key_prefix.rstrip("/") + "/"
    return f"{prefix}/{key_prefix.strip('/')}/"


def _package_manifest_uri(package_uri: str, manifest_filename: str) -> str:
    return f"{package_uri.rstrip('/')}/{manifest_filename}"


_SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")


def _repository_basin_version_id(model_identity: Mapping[str, Any]) -> str:
    value = model_identity.get("basin_version_id")
    try:
        return _safe_path_component(value)
    except (TypeError, ValueError) as error:
        raise ForcingProductionError(
            "Model identity has an invalid basin_version_id path component."
        ) from error


def _safe_path_component(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid path component.")
    if (
        value == "."
        or value.startswith("-")
        or "/" in value
        or "\\" in value
        or ".." in value
        or "\x00" in value
    ):
        raise ValueError("Invalid path component.")
    if _SAFE_PATH_COMPONENT.fullmatch(value) is None:
        raise ValueError("Invalid path component.")
    return value
