# NWM@8ae9b8f2 workers/forcing_producer/producer.py
"""yd structural glue: imports and `_InputMethods` stateless carrier shell.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import (
    datetime,
    timedelta,
)

from yd_producer.canonical.converter import canonical_product_is_forcing_usable
from yd_producer.forcing._producer_common import (
    _is_era5_source,
    _is_ifs_source,
    _uses_era5_latency_fallback,
)
from yd_producer.forcing._producer_timeseries import (
    _fallback_products_by_required_variable,
    _fallback_variables_for_required,
    _is_allowed_gfs_forcing_time_gap,
    _missing_product_details,
    _validate_canonical_product_units,
    _validate_requested_canonical_product_identity,
)
from yd_producer.forcing._producer_types import (
    CANONICAL_TO_FORCING,
    CanonicalField,
    CanonicalProduct,
    ERA5_CANONICAL_TO_FORCING,
    ERA5_FALLBACK_SOURCE_ID,
    ERA5_LATENCY_FALLBACK_REASON,
    ERA5_REQUIRED_CANONICAL_VARIABLES,
    FallbackLineage,
    ForcingProductionError,
    GridPoint,
    IFS_CANONICAL_TO_FORCING,
    IFS_REQUIRED_CANONICAL_VARIABLES,
)
from yd_producer.forcing.grid_identity import grid_identity_hash


class _InputMethods:
    """yd structural glue: stateless method carrier; no fields/init/super."""

    def _required_canonical_variables(self, source_id: str) -> tuple[str, ...]:
        if _is_ifs_source(source_id):
            return IFS_REQUIRED_CANONICAL_VARIABLES
        if _is_era5_source(source_id):
            return ERA5_REQUIRED_CANONICAL_VARIABLES
        return self.config.required_canonical_variables

    def _canonical_to_forcing(self, source_id: str) -> Mapping[str, str]:
        if _is_ifs_source(source_id):
            return IFS_CANONICAL_TO_FORCING
        if _is_era5_source(source_id):
            return ERA5_CANONICAL_TO_FORCING
        return CANONICAL_TO_FORCING

    def _load_canonical_products(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        required_variables: Sequence[str],
        require_complete: bool = True,
    ) -> dict[str, dict[datetime, CanonicalProduct]]:
        assert self.repository is not None
        products = self.repository.list_canonical_products(
            source_id=source_id, cycle_time=cycle_time
        )
        products_by_variable: dict[str, dict[datetime, CanonicalProduct]] = {
            variable: {} for variable in required_variables
        }
        for product in products:
            if product.variable not in products_by_variable:
                continue
            if not canonical_product_is_forcing_usable(
                {"quality_flag": product.quality_flag, "checksum": product.checksum}
            ):
                continue
            products_by_variable[product.variable][product.valid_time] = product

        if not require_complete:
            return products_by_variable

        self._validate_canonical_products(
            products_by_variable=products_by_variable,
            required_variables=required_variables,
        )
        return products_by_variable

    def _validate_canonical_products(
        self,
        *,
        products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
        required_variables: Sequence[str],
        requested_source_id: str | None = None,
        requested_cycle_time: datetime | None = None,
    ) -> None:
        if requested_source_id is not None and requested_cycle_time is not None:
            _validate_requested_canonical_product_identity(
                products_by_variable,
                source_id=requested_source_id,
                cycle_time=requested_cycle_time,
            )
        missing = _missing_product_details(products_by_variable, required_variables)
        missing = [
            item
            for item in missing
            if not _is_allowed_gfs_forcing_time_gap(
                item,
                products_by_variable,
                required_variables=required_variables,
            )
        ]
        if missing:
            raise ForcingProductionError(
                f"Missing required canonical products: {', '.join(missing)}"
            )

        grid_ids = {
            product.grid_id
            for products_for_variable in products_by_variable.values()
            for product in products_for_variable.values()
        }
        if not grid_ids:
            raise ForcingProductionError("No canonical products are available.")
        _validate_canonical_product_units(products_by_variable)

    def _apply_era5_latency_fallback(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        products_by_variable: dict[str, dict[datetime, CanonicalProduct]],
        required_variables: Sequence[str],
    ) -> FallbackLineage | None:
        if not _uses_era5_latency_fallback(source_id):
            return None

        assert self.repository is not None
        existing_valid_times = sorted(
            {
                valid_time
                for products_for_variable in products_by_variable.values()
                for valid_time in products_for_variable
            }
        )
        if existing_valid_times:
            target_valid_times = tuple(existing_valid_times)
            start_time = existing_valid_times[0]
            end_time = existing_valid_times[-1]
        else:
            target_valid_times = ()
            start_time = cycle_time
            end_time = cycle_time + timedelta(
                hours=self.config.era5_latency_fallback_hours
            )

        fallback_variables = _fallback_variables_for_required(required_variables)
        fallback_products = self.repository.list_fallback_canonical_products(
            source_id=ERA5_FALLBACK_SOURCE_ID,
            start_time=start_time,
            end_time=end_time,
            variables=fallback_variables,
        )
        fallback_by_variable = _fallback_products_by_required_variable(
            fallback_products,
            required_variables=required_variables,
        )
        if not target_valid_times:
            target_valid_times = tuple(
                sorted(
                    {
                        valid_time
                        for products_for_variable in fallback_by_variable.values()
                        for valid_time in products_for_variable
                    }
                )
            )

        fallback_valid_times: set[datetime] = set()
        for valid_time in target_valid_times:
            missing_variables = [
                variable
                for variable in required_variables
                if valid_time not in products_by_variable[variable]
            ]
            for variable in missing_variables:
                fallback_product = fallback_by_variable.get(variable, {}).get(
                    valid_time
                )
                if fallback_product is None:
                    continue
                products_by_variable[variable][valid_time] = fallback_product
                fallback_valid_times.add(valid_time)

        if not fallback_valid_times:
            return None
        return FallbackLineage(
            fallback_reason=ERA5_LATENCY_FALLBACK_REASON,
            fallback_source_id=ERA5_FALLBACK_SOURCE_ID,
            fallback_valid_times=tuple(sorted(fallback_valid_times)),
        )

    def _read_fields(
        self,
        products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    ) -> dict[str, dict[datetime, CanonicalField]]:
        fields: dict[str, dict[datetime, CanonicalField]] = {}
        for variable, products_for_variable in products_by_variable.items():
            fields[variable] = {}
            for valid_time, product in products_for_variable.items():
                fields[variable][valid_time] = self._read_canonical_field(product)
        return fields

    def _grid_points_by_source_grid_from_products(
        self,
        products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    ) -> dict[tuple[str, str], tuple[GridPoint, ...]]:
        representatives: dict[tuple[str, str], CanonicalProduct] = {}
        for products_for_variable in products_by_variable.values():
            for product in products_for_variable.values():
                representatives.setdefault(
                    (product.source_id, product.grid_id), product
                )
        if not representatives:
            raise ForcingProductionError(
                "No canonical products are available for interpolation grid discovery."
            )
        return {
            source_grid: self._read_canonical_grid(product)
            for source_grid, product in sorted(
                representatives.items(), key=lambda item: item[0]
            )
        }

    def _validate_field_grid_matches_product(
        self,
        product: CanonicalProduct,
        grid_points: Sequence[GridPoint],
        expected_grid_points: Sequence[GridPoint],
    ) -> None:
        if grid_identity_hash(grid_points) != grid_identity_hash(expected_grid_points):
            raise ForcingProductionError(
                f"Canonical product {product.canonical_product_id} grid definition/order does not match "
                f"the interpolation grid for source {product.source_id} grid {product.grid_id}."
            )
