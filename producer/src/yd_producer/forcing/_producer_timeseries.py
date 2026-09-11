# NWM@8ae9b8f2 workers/forcing_producer/producer.py
"""yd structural glue: imports and `_TimeseriesMethods` stateless carrier shell.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import math
from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import datetime
from typing import Any

from yd_producer.canonical.converter import canonical_product_is_forcing_usable
from yd_producer.forcing._producer_common import (
    _direct_grid_validation_error,
    _ensure_utc,
    _format_time,
    _normalize_checksum_identity,
    _product_lead_hours,
    _products,
    parse_cycle_time,
    wind_speed,
)
from yd_producer.forcing._producer_types import (
    CanonicalField,
    CanonicalProduct,
    EXPECTED_CANONICAL_UNITS,
    ForcingComponent,
    ForcingProductionError,
    ForcingTimeseriesRow,
    GridPoint,
    InterpolationWeight,
    MetStation,
    OUTPUT_UNITS,
)
from yd_producer.raw.source_identity import normalize_source_id


def _missing_product_details(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    required_variables: Sequence[str],
) -> list[str]:
    all_times = sorted(
        {
            valid_time
            for variable in required_variables
            for valid_time in products_by_variable.get(variable, {})
        }
    )
    missing: list[str] = []
    for variable in required_variables:
        product_times = set(products_by_variable.get(variable, {}))
        if not product_times:
            missing.append(f"{variable}:*")
            continue
        for valid_time in all_times:
            if valid_time not in product_times:
                missing.append(f"{variable}:{_format_time(valid_time)}")
    return missing


def _is_allowed_gfs_forcing_time_gap(
    missing_detail: str,
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    *,
    required_variables: Sequence[str],
) -> bool:
    if not required_variables:
        return False
    source_ids = {
        normalize_source_id(product.source_id)
        for products_for_variable in products_by_variable.values()
        for product in products_for_variable.values()
    }
    if source_ids != {"gfs"} or ":" not in missing_detail:
        return False
    variable, time_text = missing_detail.split(":", maxsplit=1)
    if time_text == "*":
        return False
    try:
        valid_time = parse_cycle_time(time_text)
    except ValueError:
        return False
    interval_variables = {"prcp_rate_or_amount", "shortwave_down"}
    if variable in interval_variables:
        cycle_time = _cycle_time_from_products(products_by_variable)
        return valid_time == cycle_time
    if variable in set(required_variables) - interval_variables:
        interval_times = set(products_by_variable.get("prcp_rate_or_amount", {})) | set(
            products_by_variable.get("shortwave_down", {})
        )
        return bool(interval_times) and valid_time == max(interval_times)
    return False


def _validate_requested_canonical_product_identity(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    *,
    source_id: str,
    cycle_time: datetime,
) -> None:
    expected_source = normalize_source_id(source_id)
    expected_cycle = _ensure_utc(cycle_time)
    mismatches: list[str] = []
    for product in _products(products_by_variable):
        try:
            actual_source = normalize_source_id(product.source_id)
        except (TypeError, ValueError):
            actual_source = str(product.source_id)
        actual_cycle = _ensure_utc(product.cycle_time)
        if actual_source != expected_source:
            mismatches.append(
                f"{product.canonical_product_id}:source_id={product.source_id!r}"
            )
        if actual_cycle != expected_cycle:
            mismatches.append(
                f"{product.canonical_product_id}:cycle_time={_format_time(actual_cycle)}"
            )
    if mismatches:
        raise ForcingProductionError(
            "Canonical products do not match the requested source/cycle: "
            + ", ".join(mismatches[:10])
        )


def _validate_canonical_product_units(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
) -> None:
    mismatches: list[str] = []
    for variable, products_for_variable in products_by_variable.items():
        expected_units = EXPECTED_CANONICAL_UNITS.get(variable)
        if expected_units is None:
            continue
        for valid_time, product in sorted(
            products_for_variable.items(), key=lambda item: item[0]
        ):
            if product.unit not in expected_units:
                mismatches.append(
                    f"{variable}:{_format_time(valid_time)} unit={product.unit!r} expected={list(expected_units)}"
                )
    if mismatches:
        raise ForcingProductionError(
            f"Canonical product unit mismatch: {', '.join(mismatches[:10])}"
        )


def _validate_direct_grid_identity(
    *,
    field: str,
    expected: Any,
    actual: Any,
    source: str,
) -> None:
    expected_text = str(expected or "").strip()
    actual_text = str(actual or "").strip()
    if field.endswith("checksum"):
        expected_compare = _normalize_checksum_identity(expected_text)
        actual_compare = _normalize_checksum_identity(actual_text)
    else:
        expected_compare = expected_text
        actual_compare = actual_text
    if not actual_text or actual_compare != expected_compare:
        raise _direct_grid_validation_error(
            f"Direct-grid {field} mismatch.",
            field=field,
            source=source,
            expected=expected_text,
            actual=actual_text,
        )


def _valid_times(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
) -> tuple[datetime, ...]:
    return tuple(
        sorted(
            {
                valid_time
                for products_for_variable in products_by_variable.values()
                for valid_time in products_for_variable
            }
        )
    )


def _cycle_time_from_products(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
) -> datetime:
    cycle_times = {
        product.cycle_time
        for products_for_variable in products_by_variable.values()
        for product in products_for_variable.values()
    }
    if not cycle_times:
        raise ForcingProductionError("No canonical products are available.")
    if len(cycle_times) != 1:
        raise ForcingProductionError(
            "Canonical products span multiple cycle_time values."
        )
    return next(iter(cycle_times))


def _expected_forcing_valid_times(
    source_id: str,
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    *,
    cycle_time: datetime,
) -> tuple[datetime, ...]:
    valid_times = _valid_times(products_by_variable)
    if normalize_source_id(source_id) != "gfs":
        return valid_times
    interval_times = sorted(
        set(products_by_variable.get("prcp_rate_or_amount", {}))
        & set(products_by_variable.get("shortwave_down", {}))
    )
    if not interval_times:
        return valid_times
    interval_row_times = _gfs_interval_row_times(interval_times, cycle_time=cycle_time)
    point_times = set(products_by_variable.get("air_temperature_2m", {}))
    return tuple(
        valid_time for valid_time in interval_row_times if valid_time in point_times
    )


def _forcing_product_time_plan(
    source_id: str,
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    *,
    forcing_times: Sequence[datetime],
    cycle_time: datetime,
) -> dict[str, dict[datetime, datetime]]:
    normalized_source = normalize_source_id(source_id)
    plan: dict[str, dict[datetime, datetime]] = {
        variable: {valid_time: valid_time for valid_time in forcing_times}
        for variable in products_by_variable
    }
    if normalized_source != "gfs":
        return plan

    interval_times = sorted(
        set(products_by_variable.get("prcp_rate_or_amount", {}))
        & set(products_by_variable.get("shortwave_down", {}))
    )
    if not interval_times:
        return plan
    row_by_interval_end = dict(
        zip(
            interval_times,
            _gfs_interval_row_times(interval_times, cycle_time=cycle_time),
        )
    )
    for variable in ("prcp_rate_or_amount", "shortwave_down"):
        plan[variable] = {
            row_time: interval_end
            for interval_end, row_time in row_by_interval_end.items()
            if row_time in set(forcing_times)
        }
    return plan


def _gfs_interval_row_times(
    interval_times: Sequence[datetime], *, cycle_time: datetime
) -> tuple[datetime, ...]:
    ordered = tuple(sorted(interval_times))
    if not ordered:
        return ()
    row_times: list[datetime] = []
    previous_end = cycle_time
    for interval_end in ordered:
        row_times.append(previous_end)
        previous_end = interval_end
    return tuple(row_times)


def _forcing_coverage_end_time(
    source_id: str,
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    *,
    row_times: Sequence[datetime],
) -> datetime:
    if not row_times:
        raise ForcingProductionError("No forcing row times are available.")
    if normalize_source_id(source_id) != "gfs":
        return sorted(row_times)[-1]
    interval_times = sorted(
        set(products_by_variable.get("prcp_rate_or_amount", {}))
        & set(products_by_variable.get("shortwave_down", {}))
    )
    return interval_times[-1] if interval_times else sorted(row_times)[-1]


def _limit_products_by_max_lead_hours(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    *,
    cycle_time: datetime,
    max_lead_hours: int | None,
) -> dict[str, dict[datetime, CanonicalProduct]]:
    if max_lead_hours is None:
        return {
            variable: dict(products_for_variable)
            for variable, products_for_variable in products_by_variable.items()
        }
    return {
        variable: {
            valid_time: product
            for valid_time, product in products_for_variable.items()
            if _product_lead_hours(product, cycle_time) <= max_lead_hours
        }
        for variable, products_for_variable in products_by_variable.items()
    }


def _limit_products_by_min_lead_hours(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    *,
    cycle_time: datetime,
    min_lead_hours: int | None,
) -> dict[str, dict[datetime, CanonicalProduct]]:
    if min_lead_hours is None:
        return {
            variable: dict(products_for_variable)
            for variable, products_for_variable in products_by_variable.items()
        }
    return {
        variable: {
            valid_time: product
            for valid_time, product in products_for_variable.items()
            if _product_lead_hours(product, cycle_time) >= min_lead_hours
        }
        for variable, products_for_variable in products_by_variable.items()
    }


def _lead_window_from_products(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    cycle_time: datetime,
) -> dict[str, int | None]:
    lead_hours = sorted(
        {
            _product_lead_hours(product, cycle_time)
            for products_for_variable in products_by_variable.values()
            for product in products_for_variable.values()
        }
    )
    return {
        "min_lead_hours": lead_hours[0] if lead_hours else None,
        "max_lead_hours": lead_hours[-1] if lead_hours else None,
    }


def _max_product_lead_hours(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    cycle_time: datetime,
) -> int | None:
    lead_hours = [
        _product_lead_hours(product, cycle_time)
        for products_for_variable in products_by_variable.values()
        for product in products_for_variable.values()
    ]
    return max(lead_hours) if lead_hours else None


def _fallback_variables_for_required(
    required_variables: Sequence[str],
) -> tuple[str, ...]:
    variables: list[str] = []
    for variable in required_variables:
        fallback_variable = (
            "shortwave_down" if variable == "net_radiation" else variable
        )
        if fallback_variable not in variables:
            variables.append(fallback_variable)
    return tuple(variables)


def _fallback_products_by_required_variable(
    products: Sequence[CanonicalProduct],
    *,
    required_variables: Sequence[str],
) -> dict[str, dict[datetime, CanonicalProduct]]:
    reverse_variables = {
        ("shortwave_down" if variable == "net_radiation" else variable): variable
        for variable in required_variables
    }
    grouped: dict[str, dict[datetime, CanonicalProduct]] = {
        variable: {} for variable in required_variables
    }
    for product in products:
        required_variable = reverse_variables.get(product.variable)
        if required_variable is None or not canonical_product_is_forcing_usable(
            {"quality_flag": product.quality_flag, "checksum": product.checksum}
        ):
            continue
        grouped[required_variable][product.valid_time] = product
    return grouped


def _required_grid_cell_ids_by_source_grid(
    weights: Mapping[tuple[str, str], Sequence[InterpolationWeight]],
) -> dict[tuple[str, str], frozenset[str]]:
    return {
        source_grid: frozenset(weight.grid_cell_id for weight in source_grid_weights)
        for source_grid, source_grid_weights in weights.items()
    }


def _weights_by_source_grid(
    weights: Sequence[InterpolationWeight],
) -> dict[tuple[str, str], tuple[InterpolationWeight, ...]]:
    grouped: dict[tuple[str, str], list[InterpolationWeight]] = {}
    for weight in weights:
        grouped.setdefault((weight.source_id, weight.grid_id), []).append(weight)
    return {
        source_grid: tuple(
            sorted(
                group,
                key=lambda item: (item.station_id, item.variable, item.grid_cell_id),
            )
        )
        for source_grid, group in grouped.items()
    }


def _canonical_variable_for_forcing(
    variable: str, canonical_to_forcing: Mapping[str, str]
) -> str:
    matches = sorted(
        canonical_variable
        for canonical_variable, forcing_variable in canonical_to_forcing.items()
        if forcing_variable == variable
    )
    if not matches:
        raise ForcingProductionError(
            f"No canonical variable is mapped to forcing variable {variable}."
        )
    return matches[0]


def _source_id_for_output_variable(
    variable: str,
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    valid_time: datetime,
    canonical_to_forcing: Mapping[str, str],
    *,
    product_time_plan: Mapping[str, Mapping[datetime, datetime]] | None = None,
) -> str:
    if variable == "wind":
        product_time = _planned_product_time(
            "wind_u_10m", valid_time, product_time_plan
        )
        return products_by_variable["wind_u_10m"][product_time].source_id
    canonical_variable = _canonical_variable_for_forcing(variable, canonical_to_forcing)
    product_time = _planned_product_time(
        canonical_variable, valid_time, product_time_plan
    )
    return products_by_variable[canonical_variable][product_time].source_id


def _weights_by_station_variable(
    weights: Sequence[InterpolationWeight],
) -> dict[tuple[str, str], tuple[InterpolationWeight, ...]]:
    grouped: dict[tuple[str, str], list[InterpolationWeight]] = {}
    for weight in weights:
        grouped.setdefault((weight.station_id, weight.variable), []).append(weight)
    return {
        key: tuple(sorted(group, key=lambda item: item.grid_cell_id))
        for key, group in grouped.items()
    }


def _native_resolution_for_output(
    variable: str,
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    valid_time: datetime,
    canonical_to_forcing: Mapping[str, str],
    *,
    product_time_plan: Mapping[str, Mapping[datetime, datetime]] | None = None,
) -> str | None:
    if variable == "wind":
        product_time = _planned_product_time(
            "wind_u_10m", valid_time, product_time_plan
        )
        return products_by_variable["wind_u_10m"][product_time].native_time_resolution
    canonical_variable = _canonical_variable_for_forcing(variable, canonical_to_forcing)
    product_time = _planned_product_time(
        canonical_variable, valid_time, product_time_plan
    )
    return products_by_variable[canonical_variable][product_time].native_time_resolution


def _planned_product_time(
    variable: str,
    valid_time: datetime,
    product_time_plan: Mapping[str, Mapping[datetime, datetime]] | None,
) -> datetime:
    if product_time_plan is None:
        return valid_time
    return product_time_plan.get(variable, {}).get(valid_time, valid_time)


class _TimeseriesMethods:
    """yd structural glue: stateless method carrier; no fields/init/super."""

    def _generate_timeseries_streaming(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        forcing_version_id: str,
        basin_version_id: str,
        products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
        stations: Sequence[MetStation],
        weights: Mapping[tuple[str, str], Sequence[InterpolationWeight]],
        grid_points_by_source_grid: Mapping[tuple[str, str], Sequence[GridPoint]],
        canonical_to_forcing: Mapping[str, str],
        validate_all_field_values: bool = True,
    ) -> tuple[tuple[ForcingTimeseriesRow, ...], tuple[ForcingComponent, ...]]:
        forcing_times = _expected_forcing_valid_times(
            source_id,
            products_by_variable,
            cycle_time=cycle_time,
        )
        product_time_plan = _forcing_product_time_plan(
            source_id,
            products_by_variable,
            forcing_times=forcing_times,
            cycle_time=cycle_time,
        )
        weights_by_source_grid_station_variable = {
            source_grid: _weights_by_station_variable(source_grid_weights)
            for source_grid, source_grid_weights in weights.items()
        }
        required_grid_cell_ids_by_source_grid = _required_grid_cell_ids_by_source_grid(
            weights
        )
        rows: list[ForcingTimeseriesRow] = []
        radiation_variable = _canonical_variable_for_forcing("Rn", canonical_to_forcing)
        pressure_variable = _canonical_variable_for_forcing(
            "Press", canonical_to_forcing
        )

        for valid_time in forcing_times:
            field_cache: dict[str, CanonicalField] = {}

            def field_for(variable: str) -> CanonicalField:
                cached = field_cache.get(variable)
                if cached is not None:
                    return cached
                product_valid_time = product_time_plan[variable][valid_time]
                product = products_by_variable[variable][product_valid_time]
                source_grid = (product.source_id, product.grid_id)
                expected_grid_points = tuple(grid_points_by_source_grid[source_grid])
                field = self._read_canonical_field(
                    product,
                    required_grid_cell_ids=required_grid_cell_ids_by_source_grid.get(
                        source_grid
                    ),
                    expected_grid_points=expected_grid_points,
                    retain_grid_points=False,
                    validate_all_values=validate_all_field_values,
                )
                field_cache[variable] = field
                return field

            precip_product = products_by_variable["prcp_rate_or_amount"][
                product_time_plan["prcp_rate_or_amount"][valid_time]
            ]
            precip_factor = self._precip_to_timestep_factor(source_id, precip_product)
            station_values: dict[str, dict[str, float]] = {
                "PRCP": {
                    station_id: value * precip_factor
                    for station_id, value in self._interpolate_forcing_variable(
                        "PRCP",
                        field_for("prcp_rate_or_amount"),
                        stations,
                        weights_by_source_grid_station_variable,
                    ).items()
                },
                "TEMP": self._interpolate_forcing_variable(
                    "TEMP",
                    field_for("air_temperature_2m"),
                    stations,
                    weights_by_source_grid_station_variable,
                ),
                "RH": self._interpolate_forcing_variable(
                    "RH",
                    field_for("relative_humidity_2m"),
                    stations,
                    weights_by_source_grid_station_variable,
                ),
                "Rn": {
                    station_id: value
                    * (
                        1.0
                        if radiation_variable == "net_radiation"
                        else self.config.rn_shortwave_factor
                    )
                    for station_id, value in self._interpolate_forcing_variable(
                        "Rn",
                        field_for(radiation_variable),
                        stations,
                        weights_by_source_grid_station_variable,
                    ).items()
                },
                "Press": self._interpolate_forcing_variable(
                    "Press",
                    field_for(pressure_variable),
                    stations,
                    weights_by_source_grid_station_variable,
                ),
            }
            u_values = self._interpolate_forcing_variable(
                "wind",
                field_for("wind_u_10m"),
                stations,
                weights_by_source_grid_station_variable,
            )
            v_values = self._interpolate_forcing_variable(
                "wind",
                field_for("wind_v_10m"),
                stations,
                weights_by_source_grid_station_variable,
            )
            station_values["wind"] = {
                station.station_id: wind_speed(
                    u_values[station.station_id], v_values[station.station_id]
                )
                for station in stations
            }

            for variable in self.config.output_variables:
                native_resolution = _native_resolution_for_output(
                    variable,
                    products_by_variable,
                    valid_time,
                    canonical_to_forcing,
                    product_time_plan=product_time_plan,
                )
                row_source_id = _source_id_for_output_variable(
                    variable,
                    products_by_variable,
                    valid_time,
                    canonical_to_forcing,
                    product_time_plan=product_time_plan,
                )
                for station in stations:
                    value = station_values[variable][station.station_id]
                    if not math.isfinite(value):
                        raise ForcingProductionError(
                            f"Interpolated forcing value is not finite for station {station.station_id} "
                            f"variable {variable} at {_format_time(valid_time)}."
                        )
                    rows.append(
                        ForcingTimeseriesRow(
                            forcing_version_id=forcing_version_id,
                            basin_version_id=basin_version_id,
                            station_id=station.station_id,
                            valid_time=valid_time,
                            source_id=row_source_id,
                            variable=variable,
                            value=value,
                            unit=OUTPUT_UNITS[variable],
                            native_resolution=native_resolution,
                        )
                    )
            field_cache.clear()

        components = tuple(
            ForcingComponent(
                forcing_version_id=forcing_version_id,
                canonical_product_id=product.canonical_product_id,
                variable=product.variable,
                valid_time_start=product.valid_time,
                valid_time_end=product.valid_time,
            )
            for products_for_variable in products_by_variable.values()
            for product in products_for_variable.values()
        )
        return tuple(rows), components

    def _interpolate_forcing_variable(
        self,
        variable: str,
        field: CanonicalField,
        stations: Sequence[MetStation],
        weights_by_source_grid_station_variable: Mapping[
            tuple[str, str],
            Mapping[tuple[str, str], tuple[InterpolationWeight, ...]],
        ],
    ) -> dict[str, float]:
        values: dict[str, float] = {}
        source_grid = (field.product.source_id, field.product.grid_id)
        try:
            weights_by_station_variable = weights_by_source_grid_station_variable[
                source_grid
            ]
        except KeyError as error:
            raise ForcingProductionError(
                f"No interpolation weights are available for source {field.product.source_id} "
                f"grid {field.product.grid_id}."
            ) from error
        for station in stations:
            station_weights = weights_by_station_variable[
                (station.station_id, variable)
            ]
            weighted_value = 0.0
            for weight in station_weights:
                try:
                    grid_value = field.values_by_grid_cell_id[weight.grid_cell_id]
                except KeyError as error:
                    raise ForcingProductionError(
                        f"Canonical product {field.product.canonical_product_id} does not contain "
                        f"grid cell {weight.grid_cell_id} required by interpolation weights."
                    ) from error
                weighted_value += grid_value * weight.weight
            values[station.station_id] = weighted_value
        return values

    def _precip_to_timestep_factor(
        self, source_id: str, precip_product: CanonicalProduct
    ) -> float:
        """Return the multiplier that converts canonical precip into SHUD ``PRCP`` (mm/day).

        The authoritative SHUD runtime unit for ``PRCP`` read from the package's
        station-index member (``shud/stations.tsd.forc``; legacy packages carry
        ``shud/qhh.tsd.forc``) is a
        daily rate, ``mm/day`` (Decision A). All canonical sources (GFS/IFS/ERA5) now emit
        precip in ``mm/day``: the canonical converter rescales each per-step accumulation by
        its own actual step (``24 / step_hours``) before persisting. The producer therefore
        passes the canonical value through unchanged (factor ``1.0``).

        Any other unit is rejected so we never silently emit a physically wrong precip
        amount; a per-step ``mm`` value drifting from upstream is also rejected by the
        ``EXPECTED_CANONICAL_UNITS`` unit gate before reaching this method.
        """
        unit = (precip_product.unit or "").strip().lower()
        if unit == "mm/day":
            return 1.0
        raise ForcingProductionError(
            f"Unsupported precipitation unit '{precip_product.unit}' for source {source_id}: "
            "no documented PRCP->mm/day conversion."
        )
