# NWM@8ae9b8f2 workers/forcing_producer/producer.py
"""yd structural glue: imports and `_ReuseMethods` stateless carrier shell.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import json
import logging
from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import datetime
from typing import Any

from yd_producer.forcing._producer_common import (
    _format_time,
    _json_round_trip,
    _normalize_longitude,
    _optional_int,
    _package_manifest_uri,
    _product_lead_hours,
    _products,
    _stable_identity,
    _valid_geographic_coordinate,
)
from yd_producer.forcing._producer_stations import _station_signature_matches
from yd_producer.forcing._producer_timeseries import _forcing_coverage_end_time
from yd_producer.forcing._producer_types import (
    CanonicalProduct,
    ForcingComponent,
    ForcingProducerConfig,
    ForcingProductionResult,
    OUTPUT_UNITS,
)
from yd_producer.forcing.bounded_json import (
    BoundedJSONError,
    load_bounded_json,
)
from yd_producer.forcing.canonical_json import _json_bytes
from yd_producer.forcing.direct_grid_contract import (
    DIRECT_GRID_MODE,
    DirectGridForcingContract,
)
from yd_producer.store.object_store import (
    LocalObjectStore,
    ObjectStoreError,
    sha256_bytes,
)

LOGGER = logging.getLogger("yd_producer.forcing.producer")


def _format_grid_signatures(
    signatures: Mapping[tuple[str, str], str],
) -> dict[str, str]:
    return {
        f"{source_id}:{grid_id}": signature
        for (source_id, grid_id), signature in sorted(signatures.items())
    }


def _output_config_identity(config: ForcingProducerConfig) -> dict[str, Any]:
    """Fingerprint every configuration field that changes forcing output semantics."""

    payload = {
        "rn_shortwave_factor": config.rn_shortwave_factor,
        "forcing_filename": config.forcing_filename,
        "csv_filename": config.csv_filename,
        "package_manifest_filename": config.package_manifest_filename,
        "output_variables": list(config.output_variables),
        "required_canonical_variables": list(config.required_canonical_variables),
        "era5_latency_fallback_hours": config.era5_latency_fallback_hours,
        "min_lead_hours": config.min_lead_hours,
    }
    payload_bytes = _json_bytes(payload)
    return {
        "schema_version": "nhms.forcing_output_config_identity.v1",
        "payload": payload,
        "checksum": sha256_bytes(payload_bytes),
    }


def _direct_grid_lineage_identity(
    *,
    contract: DirectGridForcingContract,
    station_signature: Mapping[str, Any],
    canonical_input_signature: Mapping[str, Any],
    output_config_identity: Mapping[str, Any],
) -> dict[str, Any]:
    stations = [
        {
            "station_id": station.station_id,
            "shud_forcing_index": station.shud_forcing_index,
            "forcing_filename": station.forcing_filename,
            "longitude": station.longitude,
            "latitude": station.latitude,
            "x": station.x,
            "y": station.y,
            "z": station.z,
            "grid_id": station.grid_id,
            "grid_cell_id": station.grid_cell_id,
        }
        for station in sorted(
            contract.stations, key=lambda item: item.shud_forcing_index
        )
    ]
    station_identity = {
        "schema_version": "nhms.direct_grid_station_identity.v1",
        "station_count": len(stations),
        "station_ids": [station["station_id"] for station in stations],
        "checksum": sha256_bytes(_json_bytes({"stations": stations})),
        "stations": stations,
    }
    return {
        "forcing_mapping_mode": DIRECT_GRID_MODE,
        "spatial_mapping_method": DIRECT_GRID_MODE,
        "binding_uri": contract.binding_uri,
        "binding_checksum": contract.binding_checksum,
        "model_input_package_id": contract.model_input_package_id,
        "sp_att_path": contract.sp_att_path,
        "sp_att_checksum": contract.sp_att_checksum,
        "applicable_source_ids": list(contract.applicable_source_ids),
        "grid_id": contract.grid_id,
        "contract_grid_signature": contract.grid_signature,
        "direct_grid_station_identity": station_identity,
        "direct_grid_station_signature": station_identity["checksum"],
        "station_signature": station_signature,
        "canonical_input_signature": canonical_input_signature,
        "output_config_identity": dict(output_config_identity),
    }


def _lineage_identity_matches(
    lineage: Mapping[str, Any], expected: Mapping[str, Any]
) -> bool:
    if not expected:
        return True
    for key, expected_value in expected.items():
        if key in {"station_signature", "canonical_input_signature"}:
            continue
        existing_value = lineage.get(key)
        if _stable_identity(existing_value) != _stable_identity(expected_value):
            return False
    return True


def _canonical_product_ids(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            product.canonical_product_id for product in _products(products_by_variable)
        )
    )


def _forcing_components_for_products(
    *,
    forcing_version_id: str,
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
) -> tuple[ForcingComponent, ...]:
    return tuple(
        ForcingComponent(
            forcing_version_id=forcing_version_id,
            canonical_product_id=product.canonical_product_id,
            variable=product.variable,
            valid_time_start=product.valid_time,
            valid_time_end=product.valid_time,
        )
        for product in sorted(
            _products(products_by_variable),
            key=lambda item: (item.variable, item.valid_time),
        )
    )


def _time_range_manifest(valid_times: Sequence[datetime]) -> dict[str, str | int]:
    if not valid_times:
        return {"start_time": "", "end_time": "", "timestep_count": 0}
    ordered = sorted(valid_times)
    return {
        "start_time": _format_time(ordered[0]),
        "end_time": _format_time(ordered[-1]),
        "timestep_count": len(ordered),
    }


def _scheduler_canonical_identity_manifest(
    *,
    canonical_product_id: str | None,
    canonical_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    identity = dict(canonical_identity or {})
    if canonical_product_id not in (None, ""):
        identity["canonical_product_id"] = str(canonical_product_id)
    if "policy_identity" in identity and isinstance(
        identity["policy_identity"], Mapping
    ):
        identity["policy_identity"] = dict(identity["policy_identity"])
    if "source_object_identity" in identity and isinstance(
        identity["source_object_identity"], Mapping
    ):
        identity["source_object_identity"] = dict(identity["source_object_identity"])
    return _json_round_trip(identity)


def _scheduler_canonical_identity_matches(
    existing: Any, current: Mapping[str, Any]
) -> bool:
    if not current:
        return True
    if not isinstance(existing, Mapping):
        return False
    return _stable_identity(existing) == _stable_identity(current)


def _canonical_input_signature(
    products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
    cycle_time: datetime,
    *,
    object_store: LocalObjectStore | None,
    max_manifest_bytes: int,
) -> dict[str, Any]:
    product_rows = [
        {
            "canonical_product_id": product.canonical_product_id,
            "source_id": product.source_id,
            "cycle_time": _format_time(product.cycle_time),
            "valid_time": _format_time(product.valid_time),
            "lead_time_hours": _product_lead_hours(product, cycle_time),
            "variable": product.variable,
            "unit": product.unit,
            "grid_id": product.grid_id,
            "grid_definition_uri": product.grid_definition_uri,
            "native_time_resolution": product.native_time_resolution,
            "native_spatial_resolution": product.native_spatial_resolution,
            "object_uri": product.object_uri,
            "checksum": product.checksum,
            "quality_flag": product.quality_flag,
            "grid_definition_content_signature": _grid_definition_content_signature(
                product,
                object_store,
                max_manifest_bytes=max_manifest_bytes,
            ),
        }
        for products_for_variable in products_by_variable.values()
        for product in products_for_variable.values()
    ]
    product_rows.sort(
        key=lambda row: (
            str(row["valid_time"]),
            str(row["variable"]),
            str(row["canonical_product_id"]),
            str(row["source_id"]),
        )
    )
    checksum = sha256_bytes(_json_bytes({"products": product_rows}))
    return {
        "schema_version": "nhms.forcing_canonical_input_signature.v2",
        "product_count": len(product_rows),
        "canonical_product_ids": [
            str(row["canonical_product_id"]) for row in product_rows
        ],
        "checksum": checksum,
        "products": product_rows,
    }


def _grid_definition_content_signature(
    product: CanonicalProduct,
    object_store: LocalObjectStore | None,
    *,
    max_manifest_bytes: int,
) -> dict[str, Any] | None:
    if object_store is None or not product.grid_definition_uri:
        return None
    try:
        content = object_store.read_bytes_limited(
            product.grid_definition_uri, max_bytes=max_manifest_bytes
        )
        definition = load_bounded_json(content, max_bytes=max_manifest_bytes)
    except (ObjectStoreError, OSError, ValueError, BoundedJSONError):
        return None
    grid_signature = _grid_definition_signature(definition)
    if grid_signature is None:
        return None
    return {
        "schema_version": "nhms.grid_definition_content_signature.v1",
        "uri": product.grid_definition_uri,
        "checksum": sha256_bytes(content),
        "grid_signature": grid_signature,
    }


def _grid_definition_signature(definition: Any) -> dict[str, Any] | None:
    if not isinstance(definition, Mapping):
        return None
    if definition.get("layout") == "rectilinear":
        try:
            y_count, x_count = (int(value) for value in definition["shape"])
            longitudes = tuple(
                round(_normalize_longitude(float(value)), 12)
                for value in definition["longitudes"]
            )
            latitudes = tuple(
                round(float(value), 12) for value in definition["latitudes"]
            )
        except (KeyError, TypeError, ValueError):
            return None
        if len(longitudes) != x_count or len(latitudes) != y_count:
            return None
        return {
            "layout": "rectilinear",
            "shape": [y_count, x_count],
            "longitudes": list(longitudes),
            "latitudes": list(latitudes),
        }

    cells = definition.get("cells") or definition.get("points")
    if not isinstance(cells, list):
        return None
    signed_cells: list[dict[str, Any]] = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, Mapping):
            return None
        try:
            longitude = _normalize_longitude(
                float(cell.get("lon", cell.get("longitude")))
            )
            latitude = float(cell.get("lat", cell.get("latitude")))
        except (TypeError, ValueError):
            return None
        if not _valid_geographic_coordinate(longitude, latitude):
            return None
        signed_cells.append(
            {
                "grid_cell_id": str(cell.get("grid_cell_id", cell.get("id", index))),
                "longitude": round(longitude, 12),
                "latitude": round(latitude, 12),
            }
        )
    return {"layout": "cells", "cells": signed_cells}


def _canonical_input_signature_matches(
    existing: Any, current: Mapping[str, Any]
) -> bool:
    if not isinstance(existing, Mapping):
        return False
    return (
        str(existing.get("schema_version") or "")
        == str(current.get("schema_version") or "")
        and _optional_int(existing.get("product_count"))
        == int(current["product_count"])
        and list(existing.get("canonical_product_ids") or [])
        == list(current["canonical_product_ids"])
        and str(existing.get("checksum") or "") == str(current["checksum"])
    )


class _ReuseMethods:
    """yd structural glue: stateless method carrier; no fields/init/super."""

    def _existing_forcing_version_is_current(
        self,
        existing: Mapping[str, Any] | None,
        *,
        lead_window: Mapping[str, int | None],
        station_signature: Mapping[str, Any],
        canonical_input_signature: Mapping[str, Any],
        scheduler_canonical_identity: Mapping[str, Any],
        expected_lineage_identity: Mapping[str, Any],
        expected_station_ids: Sequence[str],
        expected_valid_times: Sequence[datetime],
        expected_variables: Sequence[str],
        expected_components: Sequence[ForcingComponent],
    ) -> bool:
        if not existing:
            return False
        checksum = str(existing.get("checksum") or "").strip()
        package_uri = str(existing.get("forcing_package_uri") or "")
        if not checksum or checksum.lower() == "pending" or not package_uri:
            return False
        lineage = existing.get("lineage_json")
        if isinstance(lineage, str):
            try:
                lineage = json.loads(lineage)
            except json.JSONDecodeError:
                lineage = {}
        if not isinstance(lineage, Mapping):
            lineage = {}
        # Output transform semantics are versioned via producer_version; a mismatch means the
        # stored bytes predate the current output unit/conversion contract and must be recomputed.
        if str(lineage.get("producer_version") or "") != self.config.producer_version:
            return False
        if _optional_int(lineage.get("min_lead_hours")) != lead_window.get(
            "min_lead_hours"
        ):
            return False
        if _optional_int(lineage.get("max_lead_hours")) != lead_window.get(
            "max_lead_hours"
        ):
            return False
        if not _station_signature_matches(
            lineage.get("station_signature"), station_signature
        ):
            return False
        if list(lineage.get("station_ids") or []) != list(
            station_signature["station_ids"]
        ):
            return False
        if _optional_int(existing.get("station_count")) != int(
            station_signature["station_count"]
        ):
            return False
        if not _canonical_input_signature_matches(
            lineage.get("canonical_input_signature"), canonical_input_signature
        ):
            return False
        if not _scheduler_canonical_identity_matches(
            lineage.get("scheduler_canonical_identity"),
            scheduler_canonical_identity,
        ):
            return False
        if not _lineage_identity_matches(lineage, expected_lineage_identity):
            return False
        try:
            manifest_uri = _package_manifest_uri(
                package_uri, self.config.package_manifest_filename
            )
            if (
                not self.object_store.exists(manifest_uri)
                or self.object_store.checksum(manifest_uri) != checksum
            ):
                return False
            manifest = self._load_existing_package_manifest(manifest_uri)
            if _optional_int(manifest.get("station_count")) != int(
                station_signature["station_count"]
            ):
                return False
            manifest_lineage = manifest.get("lineage")
            if not isinstance(manifest_lineage, Mapping):
                return False
            # Manifests without producer_version predate output-semantics versioning -> stale.
            if (
                str(manifest_lineage.get("producer_version") or "")
                != self.config.producer_version
            ):
                return False
            if not _station_signature_matches(
                manifest_lineage.get("station_signature"), station_signature
            ):
                return False
            if not _canonical_input_signature_matches(
                manifest_lineage.get("canonical_input_signature"),
                canonical_input_signature,
            ):
                return False
            if not _scheduler_canonical_identity_matches(
                manifest_lineage.get("scheduler_canonical_identity"),
                scheduler_canonical_identity,
            ):
                return False
            if not _lineage_identity_matches(
                manifest_lineage, expected_lineage_identity
            ):
                return False
            if not self._existing_package_files_are_complete(
                manifest=manifest,
                lineage=manifest_lineage,
                fallback_lineage=lineage,
                expected_lineage_identity=expected_lineage_identity,
            ):
                return False
            if not self._existing_db_lineage_matches_manifest(
                lineage=lineage,
                manifest=manifest,
                manifest_lineage=manifest_lineage,
                manifest_uri=manifest_uri,
                manifest_checksum=checksum,
                expected_lineage_identity=expected_lineage_identity,
            ):
                return False
            return self._forcing_children_are_complete(
                forcing_version_id=str(existing["forcing_version_id"]),
                expected_components=expected_components,
                expected_station_ids=expected_station_ids,
                expected_valid_times=expected_valid_times,
                expected_variables=expected_variables,
            )
        except (OSError, ObjectStoreError, ValueError):
            LOGGER.warning(
                "Existing forcing package checksum could not be verified for %s",
                package_uri,
            )
            return False

    def _existing_package_files_are_complete(
        self,
        *,
        manifest: Mapping[str, Any],
        lineage: Mapping[str, Any],
        fallback_lineage: Mapping[str, Any],
        expected_lineage_identity: Mapping[str, Any],
    ) -> bool:
        manifest_files = manifest.get("files")
        lineage_files = lineage.get("output_files")
        if not isinstance(manifest_files, Sequence) or isinstance(
            manifest_files, (str, bytes)
        ):
            return False
        if not isinstance(lineage_files, Sequence) or isinstance(
            lineage_files, (str, bytes)
        ):
            fallback_lineage_files = fallback_lineage.get("output_files")
            return False
        if not manifest_files or len(manifest_files) != len(lineage_files):
            return False
        for manifest_entry, lineage_entry in zip(
            manifest_files, lineage_files, strict=True
        ):
            if not isinstance(manifest_entry, Mapping) or not isinstance(
                lineage_entry, Mapping
            ):
                return False
            if _stable_identity(manifest_entry) != _stable_identity(lineage_entry):
                return False
            uri = str(manifest_entry.get("uri") or "").strip()
            checksum = str(manifest_entry.get("checksum") or "").strip()
            if not uri or not checksum:
                return False
            if (
                not self.object_store.exists(uri)
                or self.object_store.checksum(uri) != checksum
            ):
                return False
        return True

    def _existing_db_lineage_matches_manifest(
        self,
        *,
        lineage: Mapping[str, Any],
        manifest: Mapping[str, Any],
        manifest_lineage: Mapping[str, Any],
        manifest_uri: str,
        manifest_checksum: str,
        expected_lineage_identity: Mapping[str, Any],
    ) -> bool:
        if expected_lineage_identity.get("forcing_mapping_mode") != DIRECT_GRID_MODE:
            return True
        manifest_files = manifest.get("files")
        manifest_lineage_files = manifest_lineage.get("output_files")
        db_lineage_files = lineage.get("output_files")
        if (
            not isinstance(manifest_files, Sequence)
            or isinstance(manifest_files, (str, bytes))
            or not isinstance(manifest_lineage_files, Sequence)
            or isinstance(manifest_lineage_files, (str, bytes))
            or not isinstance(db_lineage_files, Sequence)
            or isinstance(db_lineage_files, (str, bytes))
        ):
            return False
        if _stable_identity(db_lineage_files) != _stable_identity(manifest_files):
            return False
        if _stable_identity(db_lineage_files) != _stable_identity(
            manifest_lineage_files
        ):
            return False
        if str(lineage.get("forcing_package_manifest_uri") or "") != manifest_uri:
            return False
        if (
            str(lineage.get("forcing_package_manifest_checksum") or "")
            != manifest_checksum
        ):
            return False
        return True

    def _return_existing_ready(
        self,
        existing: Mapping[str, Any],
        *,
        source_id: str,
        cycle_time: datetime,
        products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
        expected_valid_times: Sequence[datetime],
    ) -> ForcingProductionResult:
        assert self.repository is not None
        self.repository.update_forecast_cycle(
            source_id=source_id,
            cycle_time=cycle_time,
            status="forcing_ready",
            error_code="",
            error_message="",
        )
        existing_coverage_end = _forcing_coverage_end_time(
            source_id,
            products_by_variable,
            row_times=expected_valid_times,
        )
        return ForcingProductionResult(
            status="already_done",
            forcing_version_id=str(existing["forcing_version_id"]),
            forcing_package_uri=str(existing["forcing_package_uri"]),
            checksum=str(existing["checksum"]),
            station_count=int(existing["station_count"]),
            timestep_count=len(expected_valid_times),
            variable_count=len(self.config.output_variables),
            time_range=_time_range_manifest(
                [expected_valid_times[0], existing_coverage_end]
            ),
            units={
                variable: OUTPUT_UNITS[variable]
                for variable in self.config.output_variables
            },
            file_uris=self._existing_ready_file_uris(
                str(existing["forcing_package_uri"])
            ),
        )

    def _load_existing_package_manifest(self, manifest_uri: str) -> Mapping[str, Any]:
        content = self.object_store.read_bytes_limited(
            manifest_uri, max_bytes=self.config.max_manifest_bytes
        )
        manifest = load_bounded_json(content, max_bytes=self.config.max_manifest_bytes)
        if not isinstance(manifest, Mapping):
            raise TypeError("Forcing package manifest must be a JSON object.")
        return manifest

    def _existing_ready_file_uris(self, package_uri: str) -> dict[str, str]:
        manifest_uri = _package_manifest_uri(
            package_uri, self.config.package_manifest_filename
        )
        file_uris = {"package_manifest": manifest_uri}
        try:
            manifest = self._load_existing_package_manifest(manifest_uri)
            files = manifest.get("files")
        except (OSError, ObjectStoreError, ValueError, BoundedJSONError):
            files = None
        if isinstance(files, Sequence) and not isinstance(files, (str, bytes)):
            for entry in files:
                if not isinstance(entry, Mapping):
                    continue
                role = str(entry.get("role") or "")
                uri = str(entry.get("uri") or "")
                if role in {"tsd_forc", "csv_debug"} and uri:
                    file_uris[role] = uri
        file_uris.setdefault(
            "tsd_forc", f"{package_uri.rstrip('/')}/{self.config.forcing_filename}"
        )
        file_uris.setdefault(
            "csv_debug", f"{package_uri.rstrip('/')}/{self.config.csv_filename}"
        )
        return file_uris

    def _forcing_children_are_complete(
        self,
        *,
        forcing_version_id: str,
        expected_components: Sequence[ForcingComponent],
        expected_station_ids: Sequence[str],
        expected_valid_times: Sequence[datetime],
        expected_variables: Sequence[str],
    ) -> bool:
        assert self.repository is not None
        verifier = getattr(self.repository, "verify_forcing_version_children", None)
        if not callable(verifier):
            return False
        proof = verifier(
            forcing_version_id=forcing_version_id,
            expected_components=expected_components,
            expected_station_ids=expected_station_ids,
            expected_valid_times=expected_valid_times,
            expected_variables=expected_variables,
        )
        return bool(proof.get("complete"))
