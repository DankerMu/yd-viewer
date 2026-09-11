# NWM@8ae9b8f2 workers/forcing_producer/producer.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import math
from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    AbstractSet,
    Any,
)

from yd_producer.forcing._producer_common import (
    _ensure_utc,
    _valid_geographic_coordinate,
    parse_cycle_time,
)
from yd_producer.forcing._producer_types import (
    CanonicalProduct,
    ForcingProductionError,
    GridPoint,
)


def _validate_grid_points(
    grid_points: Sequence[GridPoint], canonical_product_id: str
) -> None:
    grid_cell_ids: set[str] = set()
    for point in grid_points:
        if point.grid_cell_id in grid_cell_ids:
            raise ForcingProductionError(
                f"Canonical product {canonical_product_id} has duplicate grid_cell_id "
                f"{point.grid_cell_id!r}."
            )
        grid_cell_ids.add(point.grid_cell_id)
        if not math.isfinite(point.longitude) or not math.isfinite(point.latitude):
            raise ForcingProductionError(
                f"Canonical product {canonical_product_id} has non-finite grid coordinates "
                f"for grid cell {point.grid_cell_id}."
            )
        if not _valid_geographic_coordinate(point.longitude, point.latitude):
            raise ForcingProductionError(
                f"Canonical product {canonical_product_id} has grid coordinates outside geographic bounds "
                f"for grid cell {point.grid_cell_id}."
            )


def _validate_canonical_netcdf_identity(dataset: Any, product: CanonicalProduct) -> str:
    """Require the canonical writer's self-description before reading grid or values."""

    data_variable = _select_data_variable(dataset, product.variable)
    attrs = getattr(dataset, "attrs", None)
    if not isinstance(attrs, Mapping):
        raise ForcingProductionError(
            f"Canonical product {product.canonical_product_id} has no NetCDF attributes."
        )
    required_attrs = (
        "cycle_time",
        "valid_time",
        "lead_time_hours",
        "unit",
        "grid_id",
    )
    missing = [name for name in required_attrs if name not in attrs]
    if missing:
        raise ForcingProductionError(
            f"Canonical product {product.canonical_product_id} is missing NetCDF attributes: "
            + ", ".join(missing)
            + "."
        )
    try:
        actual_cycle_time = parse_cycle_time(attrs["cycle_time"])
        actual_valid_time = parse_cycle_time(attrs["valid_time"])
    except (TypeError, ValueError) as error:
        raise ForcingProductionError(
            f"Canonical product {product.canonical_product_id} has malformed NetCDF time attributes."
        ) from error
    raw_lead_time_hours = attrs["lead_time_hours"]
    try:
        lead_time_hours = int(raw_lead_time_hours)
    except (TypeError, ValueError, OverflowError) as error:
        raise ForcingProductionError(
            f"Canonical product {product.canonical_product_id} has non-integral NetCDF lead_time_hours."
        ) from error
    if isinstance(raw_lead_time_hours, bool) or lead_time_hours != raw_lead_time_hours:
        raise ForcingProductionError(
            f"Canonical product {product.canonical_product_id} has non-integral NetCDF lead_time_hours."
        )
    expected_lead_time_hours = product.lead_time_hours
    if type(expected_lead_time_hours) is not int:
        raise ForcingProductionError(
            f"Canonical product {product.canonical_product_id} has invalid catalog lead_time_hours."
        )
    actual_values = {
        "cycle_time": actual_cycle_time,
        "valid_time": actual_valid_time,
        "lead_time_hours": lead_time_hours,
        "unit": attrs["unit"],
        "grid_id": attrs["grid_id"],
    }
    expected_values = {
        "cycle_time": _ensure_utc(product.cycle_time),
        "valid_time": _ensure_utc(product.valid_time),
        "lead_time_hours": expected_lead_time_hours,
        "unit": product.unit,
        "grid_id": product.grid_id,
    }
    mismatches = [
        name
        for name, expected in expected_values.items()
        if actual_values[name] != expected
    ]
    if mismatches:
        raise ForcingProductionError(
            f"Canonical product {product.canonical_product_id} NetCDF identity mismatch: "
            + ", ".join(mismatches)
            + "."
        )
    return data_variable


def _select_data_variable(dataset: Any, expected_variable: str) -> str:
    if expected_variable in dataset.data_vars:
        return expected_variable
    data_variables = list(dataset.data_vars)
    raise ForcingProductionError(
        f"NetCDF product for {expected_variable} has no matching variable; found {data_variables}."
    )


def _data_array_size(data_array: Any) -> int:
    size = getattr(data_array, "size", None)
    if size is not None:
        return int(size)
    shape = _data_array_shape(data_array)
    total = 1
    for dimension in shape:
        total *= dimension
    return total


def _data_array_shape(data_array: Any) -> tuple[int, ...]:
    shape = getattr(data_array, "shape", ())
    return tuple(int(size) for size in shape)


def _required_grid_indexes(
    grid_points: Sequence[GridPoint],
    required_grid_cell_ids: AbstractSet[str] | None,
) -> tuple[int, ...]:
    if required_grid_cell_ids is None:
        return tuple(range(len(grid_points)))
    return tuple(
        index
        for index, point in enumerate(grid_points)
        if point.grid_cell_id in required_grid_cell_ids
    )


def _select_data_array_indexes(data_array: Any, indexes: Sequence[int]) -> Any:
    dimensions = tuple(str(dimension) for dimension in data_array.dims)
    if len(dimensions) != 1:
        raise ForcingProductionError(
            "Direct-grid canonical field must have exactly one grid-cell dimension."
        )
    return data_array.isel({dimensions[0]: list(indexes)})


def _grid_cell_ids(dataset: Any, expected_count: int) -> tuple[str, ...]:
    for name in ("grid_cell_id", "cell", "point"):
        if name in dataset.coords:
            values = dataset[name].values.ravel().tolist()
            if len(values) == expected_count:
                return tuple(str(value) for value in values)
    return tuple(str(index) for index in range(expected_count))


def _direct_lon_lat_coords(
    dataset: Any, expected_count: int
) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    longitudes = _flat_coord(dataset, ("lon", "longitude"), expected_count)
    latitudes = _flat_coord(dataset, ("lat", "latitude"), expected_count)
    if longitudes is None or latitudes is None:
        return None
    return longitudes, latitudes


def _flat_coord(
    dataset: Any, names: Sequence[str], expected_count: int
) -> tuple[float, ...] | None:
    for name in names:
        if name in dataset.coords:
            values = dataset[name].values.ravel().tolist()
            if len(values) == expected_count:
                return tuple(float(value) for value in values)
    return None


def _rectilinear_lon_lat_coords(
    dataset: Any, shape: tuple[int, ...]
) -> tuple[tuple[float, float], ...] | None:
    if len(shape) != 2:
        return None
    y_count, x_count = shape
    longitudes = _coord_by_length(dataset, ("lon", "longitude"), x_count)
    latitudes = _coord_by_length(dataset, ("lat", "latitude"), y_count)
    if longitudes is None or latitudes is None:
        return None
    return tuple(
        (longitude, latitude) for latitude in latitudes for longitude in longitudes
    )


def _coord_by_length(
    dataset: Any, names: Sequence[str], expected_count: int
) -> tuple[float, ...] | None:
    for name in names:
        if name in dataset.coords:
            values = dataset[name].values.ravel().tolist()
            if len(values) == expected_count:
                return tuple(float(value) for value in values)
    return None
