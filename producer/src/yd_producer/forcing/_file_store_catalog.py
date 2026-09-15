# NWM@8ae9b8f2 workers/forcing_producer/file_store.py
"""yd structural glue: imports and `_CatalogMethods` stateless carrier shell.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import (
    datetime,
    timedelta,
)
from pathlib import Path
from typing import Any

from yd_producer.canonical.converter import unit_for_standard_variable
from yd_producer.forcing._file_store_common import (
    CANONICAL_PRODUCT_CATALOG_ENVELOPE_KEYS,
    CANONICAL_PRODUCT_CATALOG_ROW_KEYS,
    CANONICAL_PRODUCT_CATALOG_SCHEMA_VERSION,
    ForcingStoreError,
    _CANONICAL_PRODUCT_CATALOG_REQUIRED_TEXT_FIELDS,
    _FORECAST_PRODUCT_RE,
    _grid_definition_uri_for_source,
    _grid_id_for_source,
    _int_or_none,
    _json_object,
    _native_time_resolution_for_source,
)
from yd_producer.forcing._producer_common import (
    format_cycle_time,
    parse_cycle_time,
)
from yd_producer.forcing._producer_types import CanonicalProduct
from yd_producer.raw.source_identity import normalize_source_id
from yd_producer.store.object_store import ObjectStoreError


class _CatalogMethods:
    """yd structural glue: stateless method carrier; no fields/init/super."""

    def _canonical_product_from_path(
        self,
        product_path: Path,
        *,
        source_id: str,
        cycle_time: datetime,
    ) -> CanonicalProduct | None:
        variable = product_path.parent.name
        match = _FORECAST_PRODUCT_RE.fullmatch(product_path.name)
        if match is None:
            return None
        canonical_product_id = product_path.stem
        attrs = self._read_netcdf_attrs(product_path)
        valid_time = parse_cycle_time(
            str(attrs.get("valid_time") or cycle_time.isoformat())
        )
        lead_time = _int_or_none(attrs.get("lead_time_hours"))
        if lead_time is None:
            lead_time = int(match.group("lead"))
        unit = str(attrs.get("unit") or unit_for_standard_variable(variable))
        grid_id = str(attrs.get("grid_id") or _grid_id_for_source(source_id))
        lineage_json = _json_object(attrs.get("lineage_json"))
        try:
            object_uri = self.object_store.uri_for_key(
                str(product_path.relative_to(self.object_store.root))
            )
            checksum = self.object_store.checksum(object_uri)
        except (OSError, ObjectStoreError, ValueError) as error:
            raise ForcingStoreError(
                f"Failed to inspect canonical product {product_path}: {error}"
            ) from error
        return CanonicalProduct(
            canonical_product_id=canonical_product_id,
            source_id=source_id,
            cycle_time=cycle_time,
            valid_time=valid_time,
            lead_time_hours=lead_time,
            variable=variable,
            unit=unit,
            grid_id=grid_id,
            grid_definition_uri=_grid_definition_uri_for_source(source_id),
            native_time_resolution=_native_time_resolution_for_source(source_id),
            native_spatial_resolution="0.25deg",
            object_uri=object_uri,
            checksum=checksum,
            quality_flag=str(attrs.get("quality_flag") or "ok"),
            lineage_json=lineage_json,
        )

    def _validate_canonical_catalog_envelope(
        self,
        payload: Mapping[str, Any],
        *,
        catalog_key: str,
        source_id: str,
        cycle_time: datetime,
    ) -> None:
        actual_keys = frozenset(payload)
        if actual_keys != CANONICAL_PRODUCT_CATALOG_ENVELOPE_KEYS:
            missing = sorted(CANONICAL_PRODUCT_CATALOG_ENVELOPE_KEYS - actual_keys)
            extra = sorted(actual_keys - CANONICAL_PRODUCT_CATALOG_ENVELOPE_KEYS)
            details: list[str] = []
            if missing:
                details.append(f"missing fields: {', '.join(missing)}")
            if extra:
                details.append(f"unexpected fields: {', '.join(extra)}")
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} has invalid envelope schema ({'; '.join(details)})."
            )
        if payload.get("schema_version") != CANONICAL_PRODUCT_CATALOG_SCHEMA_VERSION:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} has invalid schema_version."
            )
        catalog_source = payload.get("source_id")
        if not isinstance(catalog_source, str):
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} has invalid source_id."
            )
        try:
            normalized_catalog_source = normalize_source_id(catalog_source)
        except (TypeError, ValueError) as error:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} has invalid source_id."
            ) from error
        if normalized_catalog_source != source_id:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} source_id does not match the requested source_id."
            )
        catalog_cycle = payload.get("cycle_time")
        if not isinstance(catalog_cycle, str):
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} has invalid cycle_time."
            )
        try:
            parsed_catalog_cycle = parse_cycle_time(catalog_cycle)
        except (TypeError, ValueError) as error:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} has invalid cycle_time."
            ) from error
        if parsed_catalog_cycle != cycle_time:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} cycle_time does not match the requested cycle_time."
            )

    def _canonical_product_from_catalog_row(
        self,
        row: Mapping[str, Any],
        *,
        catalog_key: str,
        row_index: int,
        requested_source_id: str,
        requested_cycle_time: datetime,
    ) -> CanonicalProduct:
        actual_keys = frozenset(row)
        if actual_keys != CANONICAL_PRODUCT_CATALOG_ROW_KEYS:
            missing = sorted(CANONICAL_PRODUCT_CATALOG_ROW_KEYS - actual_keys)
            extra = sorted(actual_keys - CANONICAL_PRODUCT_CATALOG_ROW_KEYS)
            details: list[str] = []
            if missing:
                details.append(f"missing fields: {', '.join(missing)}")
            if extra:
                details.append(f"unexpected fields: {', '.join(extra)}")
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} product row {row_index} has invalid schema ({'; '.join(details)})."
            )
        for field_name in _CANONICAL_PRODUCT_CATALOG_REQUIRED_TEXT_FIELDS:
            value = row[field_name]
            if not isinstance(value, str) or not value.strip():
                raise ForcingStoreError(
                    f"Canonical product catalog {catalog_key} product row {row_index} has invalid {field_name}."
                )
        if not isinstance(row["lineage_json"], Mapping):
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} product row {row_index} has invalid lineage_json."
            )
        lead_time = row["lead_time_hours"]
        if type(lead_time) is not int:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} product row {row_index} has invalid lead_time_hours."
            )
        try:
            row_source_id = normalize_source_id(row["source_id"])
            row_cycle_time = parse_cycle_time(row["cycle_time"])
            valid_time = parse_cycle_time(row["valid_time"])
        except (TypeError, ValueError) as error:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} product row {row_index} has malformed identity fields."
            ) from error
        if row_source_id != requested_source_id:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} product row {row_index} source_id does not match the requested source_id."
            )
        if row_cycle_time != requested_cycle_time:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} product row {row_index} cycle_time does not match the requested cycle_time."
            )
        elapsed = valid_time - row_cycle_time
        hour = timedelta(hours=1)
        if elapsed < timedelta(0) or elapsed % hour != timedelta(0):
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} product row {row_index} has incoherent valid_time/cycle_time/lead_time_hours."
            )
        if elapsed // hour != lead_time:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} product row {row_index} has incoherent valid_time/cycle_time/lead_time_hours."
            )
        expected_product_id = (
            f"{row_source_id}_{format_cycle_time(row_cycle_time)}_"
            f"{row['variable']}_f{lead_time:03d}"
        )
        if row["canonical_product_id"] != expected_product_id:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} product row {row_index} canonical_product_id does not match canonical product identity."
            )
        expected_key = (
            f"canonical/{row_source_id}/{format_cycle_time(row_cycle_time)}/"
            f"{row['variable']}/{row['canonical_product_id']}.nc"
        )
        try:
            actual_key = self.object_store.normalize_key(row["object_uri"])
        except (TypeError, ValueError) as error:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} product row {row_index} has invalid object_uri."
            ) from error
        if actual_key != expected_key:
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} product row {row_index} object_uri does not match canonical object identity."
            )
        return CanonicalProduct(
            canonical_product_id=row["canonical_product_id"],
            source_id=row_source_id,
            cycle_time=row_cycle_time,
            valid_time=valid_time,
            lead_time_hours=lead_time,
            variable=row["variable"],
            unit=row["unit"],
            grid_id=row["grid_id"],
            grid_definition_uri=row["grid_definition_uri"],
            native_time_resolution=row["native_time_resolution"],
            native_spatial_resolution=row["native_spatial_resolution"],
            object_uri=row["object_uri"],
            checksum=row["checksum"],
            quality_flag=row["quality_flag"],
            lineage_json=dict(row["lineage_json"]),
        )
