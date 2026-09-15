# NWM@8ae9b8f2 workers/canonical_converter/converter.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from yd_producer.canonical._types import (
    CFGRIB_VARIABLE_ALIASES,
    FORCING_USABLE_CANONICAL_QUALITY_FLAGS,
    REQUIRED_STANDARD_VARIABLES_BY_SOURCE,
    STANDARD_UNITS,
    VARIABLE_MAPPING,
    CanonicalConversionError,
)
from yd_producer.raw.source_identity import normalize_source_id


def canonical_product_is_forcing_usable(product: Mapping[str, Any]) -> bool:
    quality_flag = str(product.get("quality_flag") or "ok")
    return quality_flag in FORCING_USABLE_CANONICAL_QUALITY_FLAGS and bool(
        str(product.get("checksum") or "").strip()
    )


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_cycle_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return ensure_utc(value)
    candidate = value.strip()
    if len(candidate) == 10 and candidate.isdigit():
        return datetime.strptime(candidate, "%Y%m%d%H").replace(tzinfo=UTC)
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    return ensure_utc(datetime.fromisoformat(candidate))


def format_cycle_time(value: str | datetime) -> str:
    return parse_cycle_time(value).strftime("%Y%m%d%H")


def _json_time(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def map_variable(
    native_variable: str, mapping: Mapping[str, str] | None = None
) -> str | None:
    return dict(mapping or VARIABLE_MAPPING).get(native_variable)


def required_standard_variables_for_source(source_id: str) -> tuple[str, ...]:
    normalized = normalize_source_id(source_id)
    try:
        return REQUIRED_STANDARD_VARIABLES_BY_SOURCE[normalized]
    except KeyError as error:
        raise CanonicalConversionError(
            f"Unsupported canonical readiness source: {source_id}"
        ) from error


def canonical_readiness_source_is_supported(source_id: str) -> bool:
    return normalize_source_id(source_id) in REQUIRED_STANDARD_VARIABLES_BY_SOURCE


def _stable_identity(value: Mapping[str, Any] | None) -> str:
    if not value:
        return ""
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), default=str)


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _apcp_selector_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    selector = metadata.get("idx_selector")
    return selector if isinstance(selector, Mapping) else metadata


def _apcp_accumulation_type_from_metadata(metadata: Mapping[str, Any]) -> str | None:
    selector = _apcp_selector_metadata(metadata)
    value = selector.get("accumulation_type")
    if value is None:
        value = selector.get("accumulation_policy")
    if value is None:
        return None
    parsed = str(value)
    if parsed in {"cumulative_since_cycle", "interval_bucket"}:
        return parsed
    return None


def _apcp_step_range_from_metadata(metadata: Mapping[str, Any]) -> str | None:
    selector = _apcp_selector_metadata(metadata)
    value = selector.get("step_range") or selector.get("stepRange")
    if value is None:
        return None
    return str(value)


def _coord_values_by_name(dataset: Any, names: tuple[str, ...]) -> tuple[float, ...]:
    for name in names:
        if name in dataset.coords:
            values = dataset[name].values.ravel().tolist()
            return tuple(float(value) for value in values)
    return ()


def unit_for_standard_variable(standard_variable: str) -> str:
    try:
        return STANDARD_UNITS[standard_variable]
    except KeyError as error:
        raise CanonicalConversionError(
            f"No standard unit configured for {standard_variable}"
        ) from error


def _normalize_longitude(longitude: float) -> float:
    value = float(longitude)
    while value > 180.0:
        value -= 360.0
    while value < -180.0:
        value += 360.0
    return value


def _grid_definition_signature(definition: Mapping[str, Any]) -> tuple[Any, ...]:
    if definition.get("layout") == "rectilinear":
        try:
            shape = tuple(int(value) for value in definition["shape"])
            longitudes = tuple(
                round(_normalize_longitude(float(value)), 12)
                for value in definition["longitudes"]
            )
            latitudes = tuple(
                round(float(value), 12) for value in definition["latitudes"]
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CanonicalConversionError("Grid definition is invalid.") from error
        return ("rectilinear", shape, longitudes, latitudes)

    cells = definition.get("cells") or definition.get("points")
    if isinstance(cells, list):
        signature: list[tuple[str, float, float]] = []
        for index, cell in enumerate(cells):
            if not isinstance(cell, Mapping):
                raise CanonicalConversionError("Grid definition cell is invalid.")
            try:
                longitude = _normalize_longitude(
                    float(cell.get("lon", cell.get("longitude")))
                )
                latitude = float(cell.get("lat", cell.get("latitude")))
            except (TypeError, ValueError) as error:
                raise CanonicalConversionError(
                    "Grid definition cell coordinates are invalid."
                ) from error
            signature.append(
                (
                    str(cell.get("grid_cell_id", cell.get("id", index))),
                    round(longitude, 12),
                    round(latitude, 12),
                )
            )
        return ("cells", tuple(signature))

    raise CanonicalConversionError("Grid definition layout is unsupported.")


def compute_time_axis(
    cycle_time: str | datetime, forecast_hours: list[int]
) -> list[dict[str, Any]]:
    parsed_cycle_time = parse_cycle_time(cycle_time)
    return [
        {
            "valid_time": parsed_cycle_time + timedelta(hours=forecast_hour),
            "lead_time_hours": forecast_hour,
        }
        for forecast_hour in forecast_hours
    ]


def _manifest_value(manifest: Any, key: str) -> Any:
    if isinstance(manifest, Mapping):
        return manifest[key]
    return getattr(manifest, key)


def _manifest_metadata(manifest: Any) -> dict[str, Any]:
    if isinstance(manifest, Mapping):
        return dict(manifest.get("metadata") or {})
    return dict(getattr(manifest, "metadata", {}) or {})


def _manifest_entries(manifest: Any) -> list[dict[str, Any]]:
    entries = _manifest_value(manifest, "entries")
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            normalized.append(dict(entry))
        elif hasattr(entry, "as_dict"):
            normalized.append(entry.as_dict())
        else:
            normalized.append(
                {
                    "local_key": entry.local_key,
                    "variable": entry.variable,
                    "forecast_hour": entry.forecast_hour,
                }
            )
    return normalized


def _cfgrib_backend_kwargs(
    entry: Mapping[str, Any], expected_native_variable: str
) -> dict[str, Any]:
    metadata = _mapping_value(entry.get("metadata"))
    explicit = metadata.get("cfgrib_filter_by_keys")
    if isinstance(explicit, Mapping):
        return {"filter_by_keys": dict(explicit), "indexpath": ""}

    bundle = metadata.get("bundle")
    if isinstance(bundle, Mapping) and bundle.get("layout") == "per_forecast_hour":
        short_name = metadata.get("grib_short_name") or _first_cfgrib_alias(
            expected_native_variable
        )
        if short_name:
            return {"filter_by_keys": {"shortName": short_name}, "indexpath": ""}

    return {"indexpath": ""}


def _first_cfgrib_alias(native_variable: str) -> str | None:
    aliases = CFGRIB_VARIABLE_ALIASES.get(native_variable)
    if aliases:
        return aliases[0]
    return native_variable or None
