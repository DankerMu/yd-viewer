# NWM@8ae9b8f2 workers/forcing_producer/producer.py
"""yd structural glue: imports and `_ContractMethods` stateless carrier shell.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import logging
from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import datetime
from typing import Any

from yd_producer.forcing._producer_common import (
    _direct_grid_text_asset,
    _direct_grid_validation_error,
    _parse_sp_att_forc_values,
    _products,
    _stable_identity,
    _validate_sp_att_forc_values,
)
from yd_producer.forcing._producer_reuse import _scheduler_canonical_identity_manifest
from yd_producer.forcing._producer_stations import (
    _station_forcing_sort_key,
    _valid_station,
    _validate_forcing_grid_station_contract,
    _validate_unique_station_forcing_contract,
)
from yd_producer.forcing._producer_timeseries import _validate_direct_grid_identity
from yd_producer.forcing._producer_types import (
    CanonicalProduct,
    ForcingComponent,
    ForcingProductionError,
    ForcingTimeseriesRow,
    GridPoint,
    InterpolationWeight,
    MetStation,
)
from yd_producer.forcing.direct_grid_contract import (
    DIRECT_GRID_MODE,
    DirectGridContractError,
    DirectGridForcingContract,
    validate_direct_grid_forcing_contract,
)
from yd_producer.forcing.grid_identity import grid_identity_hash
from yd_producer.store.object_store import (
    ObjectStoreError,
    sha256_bytes,
)

LOGGER = logging.getLogger("yd_producer.forcing.producer")


class _ContractMethods:
    """yd structural glue: stateless method carrier; no fields/init/super."""

    def _resolve_model_identity(self, *, model_id: str) -> Mapping[str, Any]:
        assert self.repository is not None
        resolver = getattr(self.repository, "resolve_model_identity", None)
        if callable(resolver):
            identity = dict(resolver(model_id=model_id))
        else:
            identity = {
                "basin_version_id": self.repository.resolve_model_basin_version(
                    model_id=model_id
                )
            }
        if identity.get("basin_version_id") in (None, ""):
            raise ForcingProductionError(
                f"Model instance {model_id!r} has no basin_version_id."
            )
        return identity

    def _validate_scheduler_identity(
        self,
        *,
        model_identity: Mapping[str, Any],
        basin_id: str | None,
        basin_version_id: str | None,
        river_network_version_id: str | None,
    ) -> None:
        expected = {
            "basin_id": basin_id,
            "basin_version_id": basin_version_id,
            "river_network_version_id": river_network_version_id,
        }
        for field_name, expected_value in expected.items():
            if expected_value in (None, ""):
                continue
            actual_value = model_identity.get(field_name)
            if actual_value in (None, ""):
                if field_name == "basin_id":
                    continue
                raise ForcingProductionError(f"Model identity is missing {field_name}.")
            if str(actual_value) != str(expected_value):
                raise ForcingProductionError(
                    f"Scheduler {field_name} {expected_value!r} does not match repository value {actual_value!r}."
                )

    def _resolve_forcing_mapping_contract(
        self,
        *,
        model_id: str,
        basin_version_id: str,
        source_id: str,
    ) -> DirectGridForcingContract | None:
        assert self.repository is not None
        load_contract = getattr(self.repository, "load_forcing_mapping_contract", None)
        if not callable(load_contract):
            return None
        try:
            contract = load_contract(
                model_id=model_id,
                basin_version_id=basin_version_id,
                source_id=source_id,
            )
            if contract is not None:
                validate_direct_grid_forcing_contract(contract, source_id=source_id)
        except DirectGridContractError as error:
            raise ForcingProductionError(
                f"Invalid forcing mapping contract: {error}"
            ) from error
        return contract

    def _stop_at_direct_grid_package_boundary(
        self,
        *,
        contract: DirectGridForcingContract,
        rows: Sequence[ForcingTimeseriesRow],
        components: Sequence[ForcingComponent],
    ) -> None:
        if not rows:
            raise ForcingProductionError(
                f"Direct-grid contract {contract.binding_checksum!r} generated no station value rows."
            )
        if not components:
            raise ForcingProductionError(
                f"Direct-grid contract {contract.binding_checksum!r} generated no forcing components."
            )
        raise ForcingProductionError(
            "Direct-grid rows/components generated; stopping at the issue #546 package/lineage boundary for "
            f"contract {contract.binding_checksum!r}."
        )

    def _materialize_direct_grid_mappings(
        self,
        *,
        contract: DirectGridForcingContract,
        source_id: str,
        model_id: str,
    ) -> tuple[InterpolationWeight, ...]:
        assert self.repository is not None
        weights = self._direct_grid_weights_from_contract(
            contract=contract,
            source_id=source_id,
            model_id=model_id,
        )
        self.repository.upsert_interp_weights(weights)
        return weights

    def _direct_grid_weights_from_contract(
        self,
        *,
        contract: DirectGridForcingContract,
        source_id: str,
        model_id: str,
    ) -> tuple[InterpolationWeight, ...]:
        return tuple(
            InterpolationWeight(
                source_id=source_id,
                grid_id=contract.grid_id,
                model_id=model_id,
                station_id=station.station_id,
                variable=variable,
                grid_cell_id=station.grid_cell_id,
                weight=1.0,
                method=DIRECT_GRID_MODE,
            )
            for station in sorted(
                contract.stations, key=lambda item: item.shud_forcing_index
            )
            for variable in self.config.output_variables
        )

    def _ensure_direct_grid_met_stations(
        self,
        *,
        basin_version_id: str,
        contract: DirectGridForcingContract,
    ) -> None:
        assert self.repository is not None
        ensure = getattr(self.repository, "ensure_direct_grid_met_stations", None)
        if not callable(ensure):
            raise ForcingProductionError(
                "Direct-grid repository does not implement ensure_direct_grid_met_stations; "
                "cannot make contract station ids compatible with met.met_station."
            )
        ensure(basin_version_id=basin_version_id, contract=contract)

    def _validate_direct_grid_contract_for_production(
        self,
        *,
        contract: DirectGridForcingContract,
        model_id: str,
        basin_version_id: str,
        products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    ) -> None:
        assets = self._load_direct_grid_validation_assets(
            model_id=model_id,
            basin_version_id=basin_version_id,
            contract=contract,
        )
        _validate_direct_grid_identity(
            field="binding_checksum",
            expected=contract.binding_checksum,
            actual=assets.get("binding_checksum"),
            source="binding_uri",
        )
        _validate_direct_grid_identity(
            field="model_input_package_id",
            expected=contract.model_input_package_id,
            actual=assets.get("model_input_package_id"),
            source="model_input_package",
        )
        _validate_direct_grid_identity(
            field="sp_att_checksum",
            expected=contract.sp_att_checksum,
            actual=assets.get("sp_att_checksum"),
            source="sp_att",
        )

        grid_points_by_source_grid = self._grid_points_by_source_grid_from_products(
            products_by_variable
        )
        source_grid_keys = tuple(sorted(grid_points_by_source_grid))
        grid_ids = tuple(sorted({grid_id for _, grid_id in source_grid_keys}))
        actual_grid_id = grid_ids[0] if len(grid_ids) == 1 else "mixed"
        _validate_direct_grid_identity(
            field="grid_id",
            expected=contract.grid_id,
            actual=actual_grid_id,
            source="canonical_product",
        )
        if len(source_grid_keys) != 1:
            raise _direct_grid_validation_error(
                "Direct-grid canonical products must use one source/grid identity.",
                field="grid_id",
                source="canonical_product",
                expected=contract.grid_id,
                actual=actual_grid_id,
                details={"canonical_source_grids": source_grid_keys},
            )
        actual_grid_signature = grid_identity_hash(
            grid_points_by_source_grid[source_grid_keys[0]]
        )
        _validate_direct_grid_identity(
            field="grid_signature",
            expected=contract.grid_signature,
            actual=actual_grid_signature,
            source="canonical_product",
        )
        self._validate_direct_grid_product_grids(
            products_by_variable=products_by_variable,
            expected_grid_points_by_source_grid=grid_points_by_source_grid,
        )

        sp_att_content = _direct_grid_text_asset(
            assets.get("sp_att_content"), field="sp_att_content"
        )
        forc_values = _parse_sp_att_forc_values(sp_att_content)
        _validate_sp_att_forc_values(
            forc_values,
            valid_indexes={station.shud_forcing_index for station in contract.stations},
        )

    def _validate_direct_grid_product_grids(
        self,
        *,
        products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
        expected_grid_points_by_source_grid: Mapping[
            tuple[str, str], Sequence[GridPoint]
        ],
    ) -> None:
        for products_for_variable in products_by_variable.values():
            for product in products_for_variable.values():
                source_grid = (product.source_id, product.grid_id)
                expected_grid_points = expected_grid_points_by_source_grid[source_grid]
                actual_grid_points = self._read_canonical_grid(product)
                actual_signature = grid_identity_hash(actual_grid_points)
                expected_signature = grid_identity_hash(expected_grid_points)
                if actual_signature != expected_signature:
                    raise _direct_grid_validation_error(
                        "Direct-grid canonical product grid definition/order mismatch.",
                        field="grid_signature",
                        source="canonical_product",
                        expected=expected_signature,
                        actual=actual_signature,
                        details={
                            "canonical_product_id": product.canonical_product_id,
                            "source_id": product.source_id,
                            "grid_id": product.grid_id,
                        },
                    )

    def _load_direct_grid_validation_assets(
        self,
        *,
        model_id: str,
        basin_version_id: str,
        contract: DirectGridForcingContract,
    ) -> Mapping[str, Any]:
        assert self.repository is not None
        loader = getattr(self.repository, "load_direct_grid_validation_assets", None)
        assets: dict[str, Any] = {}
        if callable(loader):
            loaded_assets = loader(
                model_id=model_id,
                basin_version_id=basin_version_id,
                contract=contract,
                max_bytes=self.config.max_manifest_bytes,
            )
            if not isinstance(loaded_assets, Mapping):
                raise _direct_grid_validation_error(
                    "Direct-grid validation assets must be returned as a mapping.",
                    field="validation_assets",
                    source="repository",
                    expected="mapping",
                    actual=type(loaded_assets).__name__,
                )
            assets.update(dict(loaded_assets))

        assets.setdefault("model_input_package_id", contract.model_input_package_id)
        try:
            if contract.binding_uri and not assets.get("binding_checksum"):
                assets["binding_checksum"] = self.object_store.checksum_limited(
                    contract.binding_uri,
                    max_bytes=self.config.max_manifest_bytes,
                )
            if contract.sp_att_path and not assets.get("sp_att_content"):
                sp_att_content = self.object_store.read_bytes_limited(
                    contract.sp_att_path,
                    max_bytes=self.config.max_manifest_bytes,
                )
                assets["sp_att_content"] = sp_att_content
            if assets.get("sp_att_content") is not None and not assets.get(
                "sp_att_checksum"
            ):
                sp_att_content = _direct_grid_text_asset(
                    assets["sp_att_content"], field="sp_att_content"
                )
                assets["sp_att_checksum"] = sha256_bytes(sp_att_content.encode("utf-8"))
        except (OSError, ObjectStoreError, TypeError, ValueError) as error:
            raise _direct_grid_validation_error(
                "Failed to load direct-grid validation assets.",
                field="validation_assets",
                source="object_store",
                expected="readable binding/sp_att assets",
                actual=str(error),
            ) from error
        return assets

    def _validate_scheduler_canonical_identity(
        self,
        *,
        source_id: str,
        products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
        canonical_product_id: str | None,
        canonical_identity: Mapping[str, Any] | None,
    ) -> None:
        identity = _scheduler_canonical_identity_manifest(
            canonical_product_id=canonical_product_id,
            canonical_identity=canonical_identity,
        )
        expected_policy = _stable_identity(identity.get("policy_identity"))
        expected_source_object = _stable_identity(
            identity.get("source_object_identity")
        )
        if (
            not expected_policy
            and not expected_source_object
            and not identity.get("canonical_product_id")
        ):
            return
        mismatches: list[str] = []
        for product in _products(products_by_variable):
            if product.source_id != source_id:
                continue
            lineage = (
                product.lineage_json
                if isinstance(product.lineage_json, Mapping)
                else {}
            )
            row_policy = _stable_identity(
                lineage.get("policy_identity")
                or lineage.get("source_policy")
                or lineage.get("canonical_policy_identity")
            )
            row_source_object = _stable_identity(
                lineage.get("source_object_identity")
                or lineage.get("source_identity")
                or lineage.get("object_identity")
            )
            row_canonical_product_id = lineage.get("canonical_product_id")
            if expected_policy and row_policy != expected_policy:
                mismatches.append(f"{product.canonical_product_id}:policy_identity")
            if expected_source_object and row_source_object != expected_source_object:
                mismatches.append(
                    f"{product.canonical_product_id}:source_object_identity"
                )
            if (
                identity.get("canonical_product_id")
                and row_canonical_product_id not in (None, "")
                and str(row_canonical_product_id)
                != str(identity["canonical_product_id"])
            ):
                mismatches.append(
                    f"{product.canonical_product_id}:canonical_product_id"
                )
        if mismatches:
            raise ForcingProductionError(
                "Canonical products do not match scheduler-selected canonical identity: "
                + ", ".join(mismatches[:10])
            )

    def _enforce_limit(self, name: str, value: int, limit: int) -> None:
        if value > limit:
            raise ForcingProductionError(
                f"Forcing {name} {value} exceeds configured limit {limit}."
            )

    def _enforce_direct_grid_timeseries_limits(
        self,
        *,
        expected_valid_times: Sequence[datetime],
        station_count: int,
    ) -> None:
        self._enforce_limit(
            "timestep_count", len(expected_valid_times), self.config.max_timestep_count
        )
        expected_row_count = (
            len(expected_valid_times)
            * station_count
            * len(self.config.output_variables)
        )
        self._enforce_limit(
            "timeseries_row_count",
            expected_row_count,
            self.config.max_timeseries_row_count,
        )

    def _load_valid_stations(self, *, basin_version_id: str) -> tuple[MetStation, ...]:
        assert self.repository is not None
        loaded = self.repository.load_met_stations(basin_version_id=basin_version_id)
        selected = tuple(
            station for station in loaded if station.station_role == "forcing_grid"
        )
        stations: list[MetStation] = []
        for station in selected:
            if not _valid_station(station):
                LOGGER.warning(
                    "Excluding invalid met station %s for basin %s",
                    station.station_id,
                    basin_version_id,
                )
                continue
            _validate_forcing_grid_station_contract(station)
            stations.append(station)

        if not stations:
            raise ForcingProductionError(
                f"No active forcing_grid meteorological stations are defined for basin version {basin_version_id}."
            )
        _validate_unique_station_forcing_contract(stations)
        return tuple(sorted(stations, key=_station_forcing_sort_key))
