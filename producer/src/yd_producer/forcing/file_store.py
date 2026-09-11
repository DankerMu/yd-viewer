# NWM@8ae9b8f2 workers/forcing_producer/file_store.py
"""Work-local object-store forcing repository.

Deviations from the NWM pin (inventory §1 row 37 / issue #14):
- no registry/bbox projection, no env factory, no DB backend
- registry_manifest is an explicit object-store relative key
- all JSON reads are bounded and no-follow
- #104 (inventory §1 row 38): `_grid_definition_uri_for_source` IFS
  literal is canonical/ifs/grid/ifs_0p25/grid.json; GFS/ERA5, catalog
  exact-URI consumption, fallback, and parser logic stay unchanged
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from yd_producer.canonical.converter import unit_for_standard_variable
from yd_producer.forcing.bounded_json import BoundedJSONError, load_bounded_json
from yd_producer.forcing.direct_grid_contract import (
    DirectGridContractError,
    DirectGridForcingContract,
    load_forcing_mapping_contract_from_manifest,
)
from yd_producer.forcing.producer import (
    CanonicalProduct,
    ForcingComponent,
    ForcingTimeseriesRow,
    InterpolationWeight,
    MetStation,
    format_cycle_time,
    parse_cycle_time,
)
from yd_producer.forcing.shud_forcing_contract import (
    CANONICAL_SHUD_FORCING_INDEX_MEMBER,
    SHUD_FORCING_INDEX_MEMBERS,
    SHUD_FORCING_ROLE,
)
from yd_producer.raw.source_identity import normalize_source_id
from yd_producer.store.object_store import (
    LocalObjectStore,
    ObjectStoreError,
    sha256_bytes,
)

LOGGER = logging.getLogger(__name__)

MAX_OBJECT_MANIFEST_BYTES = 16 * 1024 * 1024

from yd_producer.forcing._file_store_common import (
    CANONICAL_PRODUCT_CATALOG_ENVELOPE_KEYS,
    CANONICAL_PRODUCT_CATALOG_ROW_KEYS,
    CANONICAL_PRODUCT_CATALOG_SCHEMA_VERSION,
    FORCING_DOMAIN_HANDOFF_CONTRACT_ID,
    FORCING_DOMAIN_HANDOFF_SCHEMA_VERSION,
    FORCING_DOMAIN_PACKAGE_CONTRACT_ID,
    FORCING_DOMAIN_PACKAGE_MANIFEST_CHECKSUM_FIELD,
    FORCING_DOMAIN_PACKAGE_MANIFEST_URI_FIELD,
    FORCING_PACKAGE_MANIFEST_CHECKSUM_FIELD,
    FORCING_PACKAGE_MANIFEST_URI_FIELD,
    ForcingStoreError,
    _CANONICAL_PRODUCT_CATALOG_REQUIRED_TEXT_FIELDS,
    _FORECAST_PRODUCT_RE,
    _NATIVE_RESOLUTION_RE,
    _duration_label,
    _ensure_utc,
    _first_unit,
    _float_value,
    _forcing_package_manifest_uri,
    _format_time,
    _grid_definition_uri_for_source,
    _grid_id_for_source,
    _handoff_run_id,
    _handoff_timeseries_row,
    _int_or_none,
    _json_bytes,
    _json_default,
    _json_object,
    _json_safe,
    _lead_sort_key,
    _lineage_value,
    _manifest_station_order,
    _native_resolution_delta,
    _native_time_resolution_for_source,
    _parse_shud_tsd_forc_stations,
    _registry_manifest_key,
    _safe_direct_grid_package_member,
    _station_index_member_basename,
    _station_name_from_id,
    _station_name_prefix,
    _time_lattice,
    _time_lattice_resolution_by_variable_time,
    _time_lattice_segment,
    _time_value,
    _timeseries_sort_key,
    _weight_sort_key,
)

from yd_producer.forcing._file_store_catalog import _CatalogMethods
from yd_producer.forcing._file_store_handoff import _HandoffMethods


@dataclass
class FileForcingRepository(_CatalogMethods, _HandoffMethods):
    """Object-store/file backed repository for DB-free forcing production."""

    object_store: LocalObjectStore
    registry_manifest: str
    _registry_cache: Mapping[str, Any] | None = field(
        default=None, init=False, repr=False
    )
    _model_manifest_cache: dict[str, Mapping[str, Any]] = field(
        default_factory=dict, init=False, repr=False
    )
    _stations_by_basin_version: dict[str, tuple[MetStation, ...]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _weights_by_scope: dict[tuple[str, str, str], tuple[InterpolationWeight, ...]] = (
        field(
            default_factory=dict,
            init=False,
            repr=False,
        )
    )
    _forcing_versions: dict[str, dict[str, Any]] = field(
        default_factory=dict, init=False, repr=False
    )
    _forcing_components: dict[str, tuple[ForcingComponent, ...]] = field(
        default_factory=dict, init=False, repr=False
    )
    _forcing_timeseries_summary: dict[str, Mapping[str, Any]] = field(
        default_factory=dict, init=False, repr=False
    )
    _forcing_timeseries_rows: dict[str, tuple[ForcingTimeseriesRow, ...]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def resolve_model_identity(self, *, model_id: str) -> dict[str, Any]:
        model = self._model_entry(model_id)
        return {
            "basin_id": str(model["basin_id"]),
            "basin_version_id": model["basin_version_id"],
            "river_network_version_id": str(
                model.get("river_network_version_id") or ""
            ),
        }

    def resolve_model_basin_version(self, *, model_id: str) -> Any:
        return self._model_entry(model_id)["basin_version_id"]

    def load_met_stations(self, *, basin_version_id: str) -> tuple[MetStation, ...]:
        cached = self._stations_by_basin_version.get(basin_version_id)
        if cached is not None:
            return cached
        model = self._model_entry_for_basin_version(basin_version_id)
        manifest = self._model_manifest(model)
        stations = self._stations_from_model_manifest(
            model=model,
            manifest=manifest,
            basin_version_id=basin_version_id,
        )
        self._stations_by_basin_version[basin_version_id] = stations
        return stations

    def list_canonical_products(
        self, *, source_id: str, cycle_time: datetime
    ) -> tuple[CanonicalProduct, ...]:
        normalized_source = normalize_source_id(source_id)
        return self._canonical_products_from_catalog(
            source_id=normalized_source,
            cycle_time=parse_cycle_time(format_cycle_time(cycle_time)),
        )

    def list_fallback_canonical_products(
        self,
        *,
        source_id: str,
        start_time: datetime,
        end_time: datetime,
        variables: Sequence[str],
    ) -> tuple[CanonicalProduct, ...]:
        normalized_source = normalize_source_id(source_id)
        source_dir = Path(self.object_store.root) / "canonical" / normalized_source
        if not source_dir.exists() or not variables:
            return ()
        selected: dict[tuple[datetime, str], CanonicalProduct] = {}
        for cycle_dir in sorted(path for path in source_dir.iterdir() if path.is_dir()):
            for product_path in sorted(cycle_dir.glob("*/*.nc")):
                product = self._canonical_product_from_path(
                    product_path,
                    source_id=normalized_source,
                    cycle_time=parse_cycle_time(cycle_dir.name),
                )
                if product is None or product.variable not in variables:
                    continue
                if not start_time <= product.valid_time <= end_time:
                    continue
                if product.quality_flag == "fail" or not product.checksum:
                    continue
                key = (product.valid_time, product.variable)
                existing = selected.get(key)
                if existing is None or _lead_sort_key(product) < _lead_sort_key(
                    existing
                ):
                    selected[key] = product
        return tuple(
            sorted(selected.values(), key=lambda item: (item.variable, item.valid_time))
        )

    def load_interp_weights(
        self,
        *,
        source_id: str,
        grid_id: str,
        model_id: str,
    ) -> tuple[InterpolationWeight, ...]:
        return self._weights_by_scope.get((source_id, grid_id, model_id), ())

    def upsert_interp_weights(self, weights: Sequence[InterpolationWeight]) -> None:
        if not weights:
            return
        scopes = {
            (weight.source_id, weight.grid_id, weight.model_id) for weight in weights
        }
        if len(scopes) != 1:
            raise ForcingStoreError(
                "Interpolation weights must be replaced one source/grid/model scope at a time."
            )
        self._weights_by_scope[next(iter(scopes))] = tuple(weights)

    def ensure_direct_grid_met_stations(
        self,
        *,
        basin_version_id: str,
        contract: DirectGridForcingContract,
    ) -> None:
        return None

    def load_forcing_mapping_contract(
        self,
        *,
        model_id: str,
        basin_version_id: str,
        source_id: str | None = None,
    ) -> DirectGridForcingContract | None:
        model = self._model_entry(model_id)
        if str(model.get("basin_version_id") or "") != basin_version_id:
            raise ForcingStoreError(
                f"Model instance {model_id!r} for basin_version_id {basin_version_id!r} was not found."
            )
        resource_profile = model.get("resource_profile") or {}
        if not isinstance(resource_profile, Mapping):
            raise DirectGridContractError(
                "Model resource_profile must be a JSON object.",
                details={
                    "model_id": model_id,
                    "basin_version_id": basin_version_id,
                    "actual_type": type(resource_profile).__name__,
                },
            )
        return load_forcing_mapping_contract_from_manifest(
            resource_profile,
            source_id=source_id,
            allow_root_direct_grid=False,
        )

    def load_direct_grid_validation_assets(
        self,
        *,
        model_id: str,
        basin_version_id: str,
        contract: DirectGridForcingContract,
        max_bytes: int,
    ) -> Mapping[str, Any]:
        model = self._model_entry(model_id)
        if str(model.get("basin_version_id") or "") != basin_version_id:
            raise ForcingStoreError(
                f"Model instance {model_id!r} for basin_version_id {basin_version_id!r} was not found."
            )
        authoritative_contract = self.load_forcing_mapping_contract(
            model_id=model_id,
            basin_version_id=basin_version_id,
            source_id=contract.applicable_source_ids[0]
            if len(contract.applicable_source_ids) == 1
            else None,
        )
        if authoritative_contract != contract:
            raise DirectGridContractError(
                "Direct-grid validation contract does not match file model registry resource_profile.",
                field="direct_grid_forcing",
                details={"model_id": model_id, "basin_version_id": basin_version_id},
            )

        package_uri = str(model.get("model_package_uri") or "").rstrip("/")
        if not package_uri:
            raise ForcingStoreError(
                f"Model {model_id!r} does not declare model_package_uri."
            )
        sp_att_relative = _safe_direct_grid_package_member(contract.sp_att_path)
        sp_att_uri = f"{package_uri}/{sp_att_relative}"
        try:
            binding_content = self.object_store.read_bytes_limited(
                contract.binding_uri, max_bytes=max_bytes
            )
            sp_att_content = self.object_store.read_bytes_limited(
                sp_att_uri, max_bytes=max_bytes
            )
        except (OSError, ObjectStoreError, ValueError) as error:
            raise DirectGridContractError(
                f"DIRECT_GRID_VALIDATION_FAILED: Failed to read authoritative direct-grid assets for model {model_id!r}: {error}",
                field="validation_assets",
            ) from error
        try:
            decoded_sp_att = sp_att_content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ForcingStoreError(
                f"Direct-grid .sp.att for model {model_id!r} is not UTF-8 text."
            ) from error
        return {
            "binding_checksum": sha256_bytes(binding_content),
            "model_input_package_id": authoritative_contract.model_input_package_id,
            "sp_att_checksum": sha256_bytes(sp_att_content),
            "sp_att_content": decoded_sp_att,
        }

    def get_forcing_version(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        model_id: str,
    ) -> dict[str, Any] | None:
        forcing_version_id = f"forc_{normalize_source_id(source_id).lower()}_{format_cycle_time(cycle_time)}_{model_id}"
        return self._forcing_versions.get(forcing_version_id)

    def upsert_forcing_version(self, record: Mapping[str, Any]) -> dict[str, Any]:
        forcing_version_id = str(record["forcing_version_id"])
        stored = dict(record)
        self._forcing_versions[forcing_version_id] = stored
        return dict(stored)

    def finalize_forcing_version(
        self, forcing_version_id: str, checksum: str
    ) -> dict[str, Any]:
        record = dict(
            self._forcing_versions.get(forcing_version_id)
            or {"forcing_version_id": forcing_version_id}
        )
        record["checksum"] = None
        self._forcing_versions[forcing_version_id] = record
        try:
            self._remove_final_readiness_markers(record)
            ready_record = {**record, "checksum": checksum}
            self._write_forcing_version_sidecar(ready_record)
            self._write_forcing_domain_handoff(ready_record)
        except Exception:
            self._remove_final_readiness_markers(record)
            self._forcing_versions[forcing_version_id] = record
            raise
        self._forcing_versions[forcing_version_id] = ready_record
        return dict(ready_record)

    def clear_forcing_version_checksum(self, forcing_version_id: str) -> dict[str, Any]:
        record = dict(
            self._forcing_versions.get(forcing_version_id)
            or {"forcing_version_id": forcing_version_id}
        )
        record["checksum"] = None
        self._forcing_versions[forcing_version_id] = record
        self._remove_final_readiness_markers(record)
        return dict(record)

    def _remove_final_readiness_markers(self, record: Mapping[str, Any]) -> None:
        package_uri = str(record.get("forcing_package_uri") or "")
        forcing_version_id = str(record.get("forcing_version_id") or "")
        if not package_uri or not forcing_version_id:
            return
        package_key = self.object_store.normalize_key(package_uri).strip("/")
        cycle_value = record.get("cycle_time")
        if cycle_value in (None, ""):
            return
        run_id = _handoff_run_id(
            record,
            source_key=str(record.get("source_id") or "").lower(),
            compact_cycle=format_cycle_time(_time_value(cycle_value)),
            model_id=str(record.get("model_id") or ""),
        )
        errors: list[Exception] = []
        for marker_key in (
            f"{package_key}/forcing_version_record.json",
            f"runs/{run_id}/input/forcing_domain_handoff.json",
        ):
            try:
                self.object_store.delete(marker_key)
            except (OSError, ObjectStoreError, ValueError) as error:
                errors.append(error)
        if errors:
            raise ForcingStoreError(
                f"Failed to remove final forcing readiness markers for {forcing_version_id}: {errors[0]}"
            ) from errors[0]

    def verify_forcing_version_children(
        self,
        *,
        forcing_version_id: str,
        expected_components: Sequence[ForcingComponent],
        expected_station_ids: Sequence[str],
        expected_valid_times: Sequence[datetime],
        expected_variables: Sequence[str],
    ) -> Mapping[str, Any]:
        components = self._forcing_components.get(forcing_version_id, ())
        rows = self._forcing_timeseries_rows.get(forcing_version_id, ())
        expected_component_tuples = Counter(
            (
                component.canonical_product_id,
                component.variable,
                component.valid_time_start,
                component.valid_time_end,
                component.role,
            )
            for component in expected_components
        )
        component_tuples = Counter(
            (
                component.canonical_product_id,
                component.variable,
                component.valid_time_start,
                component.valid_time_end,
                component.role,
            )
            for component in components
        )
        expected_timeseries_tuples = Counter(
            (station_id, valid_time, variable)
            for station_id in expected_station_ids
            for valid_time in expected_valid_times
            for variable in expected_variables
        )
        timeseries_tuples = Counter(
            (row.station_id, row.valid_time, row.variable) for row in rows
        )
        expected_timeseries_count = (
            len(expected_station_ids)
            * len(expected_valid_times)
            * len(expected_variables)
        )
        final_evidence_complete = self._final_readiness_evidence_is_coherent(
            forcing_version_id
        )
        complete = (
            component_tuples == expected_component_tuples
            and timeseries_tuples == expected_timeseries_tuples
            and final_evidence_complete
        )
        return {
            "forcing_version_id": forcing_version_id,
            "expected_component_count": len(expected_components),
            "component_count": len(components),
            "expected_component_tuple_count": len(expected_component_tuples),
            "component_tuple_count": len(component_tuples),
            "expected_timeseries_row_count": expected_timeseries_count,
            "timeseries_row_count": len(rows),
            "expected_timeseries_tuple_count": len(expected_timeseries_tuples),
            "timeseries_tuple_count": len(timeseries_tuples),
            "station_count": len({row.station_id for row in rows}),
            "timestep_count": len({row.valid_time for row in rows}),
            "variable_count": len({row.variable for row in rows}),
            "final_evidence_complete": final_evidence_complete,
            "complete": complete,
        }

    def _final_readiness_evidence_is_coherent(self, forcing_version_id: str) -> bool:
        record = self._forcing_versions.get(forcing_version_id)
        if not isinstance(record, Mapping):
            return False
        checksum = str(record.get("checksum") or "").strip()
        package_uri = str(record.get("forcing_package_uri") or "").strip()
        if not checksum or checksum.lower() == "pending" or not package_uri:
            return False
        try:
            package_key = self.object_store.normalize_key(package_uri).strip("/")
            run_id = _handoff_run_id(
                record,
                source_key=str(record.get("source_id") or "").lower(),
                compact_cycle=format_cycle_time(_time_value(record.get("cycle_time"))),
                model_id=str(record.get("model_id") or ""),
            )
            sidecar = self._read_json_reference(
                f"{package_key}/forcing_version_record.json",
                allow_same_store_uri=True,
            )
            domain_package_key = f"{package_key}/forcing_domain_package.json"
            domain_package_content = self.object_store.read_bytes_limited(
                domain_package_key, max_bytes=MAX_OBJECT_MANIFEST_BYTES
            )
            domain_package = load_bounded_json(
                domain_package_content, max_bytes=MAX_OBJECT_MANIFEST_BYTES
            )
            if not isinstance(domain_package, Mapping):
                return False
            handoff = self._read_json_reference(
                f"runs/{run_id}/input/forcing_domain_handoff.json"
            )
        except (
            OSError,
            ObjectStoreError,
            ValueError,
            BoundedJSONError,
            ForcingStoreError,
        ):
            return False
        try:
            domain_package_uri = self.object_store.uri_for_key(domain_package_key)
            expected_source_id = normalize_source_id(str(record.get("source_id") or ""))
            expected_cycle_time = _time_value(record.get("cycle_time"))
            expected_model_id = str(record.get("model_id") or "")
            expected_package_uri = package_uri.rstrip("/")
            expected_package_manifest_uri = _forcing_package_manifest_uri(
                record, package_uri
            )
            return (
                str(sidecar.get("forcing_version_id") or "") == forcing_version_id
                and normalize_source_id(str(sidecar.get("source_id") or ""))
                == expected_source_id
                and _time_value(sidecar.get("cycle_time")) == expected_cycle_time
                and str(sidecar.get("model_id") or "") == expected_model_id
                and str(sidecar.get("forcing_package_uri") or "").rstrip("/")
                == expected_package_uri
                and str(sidecar.get("checksum") or "") == checksum
                and str(domain_package.get("contract_id") or "")
                == FORCING_DOMAIN_PACKAGE_CONTRACT_ID
                and str(domain_package.get("forcing_version_id") or "")
                == forcing_version_id
                and str(domain_package.get("run_id") or "") == run_id
                and normalize_source_id(str(domain_package.get("source_id") or ""))
                == expected_source_id
                and _time_value(domain_package.get("cycle_time")) == expected_cycle_time
                and str(domain_package.get("model_id") or "") == expected_model_id
                and str(handoff.get("contract_id") or "")
                == FORCING_DOMAIN_HANDOFF_CONTRACT_ID
                and str(handoff.get("forcing_version_id") or "") == forcing_version_id
                and str(handoff.get("run_id") or "") == run_id
                and normalize_source_id(str(handoff.get("source_id") or ""))
                == expected_source_id
                and _time_value(handoff.get("cycle_time")) == expected_cycle_time
                and str(handoff.get("model_id") or "") == expected_model_id
                and str(handoff.get("forcing_package_uri") or "").rstrip("/")
                == expected_package_uri
                and str(handoff.get(FORCING_PACKAGE_MANIFEST_URI_FIELD) or "")
                == expected_package_manifest_uri
                and str(handoff.get(FORCING_PACKAGE_MANIFEST_CHECKSUM_FIELD) or "")
                == checksum
                and str(handoff.get(FORCING_DOMAIN_PACKAGE_MANIFEST_URI_FIELD) or "")
                == domain_package_uri
                and str(
                    handoff.get(FORCING_DOMAIN_PACKAGE_MANIFEST_CHECKSUM_FIELD) or ""
                )
                == sha256_bytes(domain_package_content)
            )
        except (TypeError, ValueError):
            return False

    def replace_forcing_components(
        self, forcing_version_id: str, components: Sequence[ForcingComponent]
    ) -> None:
        self._forcing_components[forcing_version_id] = tuple(components)

    def replace_forcing_timeseries(
        self,
        forcing_version_id: str,
        rows: Sequence[ForcingTimeseriesRow],
    ) -> None:
        stored_rows = tuple(rows)
        self._forcing_timeseries_summary[forcing_version_id] = {
            "row_count": len(stored_rows),
            "station_count": len({row.station_id for row in stored_rows}),
            "timestep_count": len({row.valid_time for row in stored_rows}),
            "variable_count": len({row.variable for row in stored_rows}),
        }
        self._forcing_timeseries_rows[forcing_version_id] = stored_rows

    def update_forecast_cycle(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any] | None:
        return {
            "source_id": normalize_source_id(source_id),
            "cycle_time": cycle_time,
            "status": status,
            "error_code": error_code,
            "error_message": error_message,
            "repository_backend": "file",
        }

    def _registry(self) -> Mapping[str, Any]:
        if self._registry_cache is None:
            self._registry_cache = self._read_json_reference(
                _registry_manifest_key(self.registry_manifest)
            )
        return self._registry_cache

    def _model_entry(self, model_id: str) -> Mapping[str, Any]:
        for model in self._registry_models():
            if str(model.get("model_id") or "") == model_id:
                return model
        raise ForcingStoreError(
            f"Model instance {model_id!r} was not found in file model registry."
        )

    def _model_entry_for_basin_version(
        self, basin_version_id: str
    ) -> Mapping[str, Any]:
        for model in self._registry_models():
            if str(model.get("basin_version_id") or "") == basin_version_id:
                return model
        raise ForcingStoreError(
            f"Basin version {basin_version_id!r} was not found in file model registry."
        )

    def _registry_models(self) -> tuple[Mapping[str, Any], ...]:
        registry = self._registry()
        models = registry.get("models")
        if not isinstance(models, Sequence) or isinstance(models, (str, bytes)):
            raise ForcingStoreError("File model registry must contain a models array.")
        return tuple(model for model in models if isinstance(model, Mapping))

    def _model_manifest(self, model: Mapping[str, Any]) -> Mapping[str, Any]:
        manifest_uri = str(model.get("manifest_uri") or "")
        if not manifest_uri:
            resource_profile = model.get("resource_profile") or {}
            if isinstance(resource_profile, Mapping):
                manifest_uri = str(
                    resource_profile.get("model_package_manifest_uri") or ""
                )
        if not manifest_uri:
            raise ForcingStoreError(
                f"Model {model.get('model_id')!r} does not declare a manifest_uri."
            )
        cached = self._model_manifest_cache.get(manifest_uri)
        if cached is not None:
            return cached
        manifest = self._read_json_reference(manifest_uri)
        self._model_manifest_cache[manifest_uri] = manifest
        return manifest

    def _stations_from_model_manifest(
        self,
        *,
        model: Mapping[str, Any],
        manifest: Mapping[str, Any],
        basin_version_id: str,
    ) -> tuple[MetStation, ...]:
        package_uri = str(model.get("model_package_uri") or "").rstrip("/")
        resource_profile = model.get("resource_profile") or {}
        shud_input_name = ""
        if isinstance(resource_profile, Mapping):
            shud_input_name = str(
                resource_profile.get("shud_input_name")
                or resource_profile.get("project_name")
                or ""
            )
        basin_slug = str(manifest.get("basin_slug") or shud_input_name or "model")
        forc_uri = (
            f"{package_uri}/{shud_input_name or basin_slug}.tsd.forc"
            if package_uri
            else ""
        )
        content: str | None = None
        if forc_uri:
            try:
                content = self.object_store.read_bytes_limited(
                    forc_uri, max_bytes=MAX_OBJECT_MANIFEST_BYTES
                ).decode("utf-8")
            except (ObjectStoreError, UnicodeDecodeError, ValueError):
                content = None
        if content is None:
            raise ForcingStoreError(
                f"Model {model.get('model_id')!r} does not expose a readable SHUD forcing index file."
            )
        stations = _parse_shud_tsd_forc_stations(
            content,
            basin_version_id=basin_version_id,
            station_prefix=basin_slug,
        )
        if not stations:
            raise ForcingStoreError(
                f"Model {model.get('model_id')!r} has no forcing stations in SHUD forcing index."
            )
        return stations

    def _canonical_products_from_catalog(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
    ) -> tuple[CanonicalProduct, ...]:
        catalog_key = f"canonical/{source_id}/{format_cycle_time(cycle_time)}/_catalog/catalog.json"
        try:
            content = self.object_store.read_bytes_limited(
                catalog_key, max_bytes=MAX_OBJECT_MANIFEST_BYTES
            )
            payload = load_bounded_json(content, max_bytes=MAX_OBJECT_MANIFEST_BYTES)
        except ObjectStoreError as error:
            if isinstance(error.__cause__, FileNotFoundError):
                return ()
            raise ForcingStoreError(
                f"Failed to read canonical product catalog {catalog_key}: {error}"
            ) from error
        except (OSError, ValueError, BoundedJSONError) as error:
            raise ForcingStoreError(
                f"Failed to read canonical product catalog {catalog_key}: {error}"
            ) from error
        if not isinstance(payload, Mapping):
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} must contain a JSON object."
            )
        self._validate_canonical_catalog_envelope(
            payload,
            catalog_key=catalog_key,
            source_id=source_id,
            cycle_time=cycle_time,
        )
        rows = payload.get("products")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise ForcingStoreError(
                f"Canonical product catalog {catalog_key} must contain a products array."
            )
        products: list[CanonicalProduct] = []
        canonical_product_ids: set[str] = set()
        variable_time_slots: set[tuple[str, datetime]] = set()
        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ForcingStoreError(
                    f"Canonical product catalog {catalog_key} product row {row_index} must be a JSON object."
                )
            product = self._canonical_product_from_catalog_row(
                row,
                catalog_key=catalog_key,
                row_index=row_index,
                requested_source_id=source_id,
                requested_cycle_time=cycle_time,
            )
            if product.canonical_product_id in canonical_product_ids:
                raise ForcingStoreError(
                    f"Canonical product catalog {catalog_key} contains duplicate canonical_product_id "
                    f"{product.canonical_product_id!r}."
                )
            canonical_product_ids.add(product.canonical_product_id)
            slot = (product.variable, product.valid_time)
            if slot in variable_time_slots:
                raise ForcingStoreError(
                    f"Canonical product catalog {catalog_key} contains duplicate variable/valid_time "
                    f"slot {product.variable!r}/{_format_time(product.valid_time)}."
                )
            variable_time_slots.add(slot)
            products.append(product)
        return tuple(
            sorted(
                products,
                key=lambda item: (
                    item.variable,
                    item.valid_time,
                    item.canonical_product_id,
                ),
            )
        )

    def _read_netcdf_attrs(self, product_path: Path) -> Mapping[str, Any]:
        try:
            from yd_producer.forcing.netcdf_open import open_canonical_netcdf

            with open_canonical_netcdf(
                self.object_store,
                self.object_store.uri_for_key(
                    str(product_path.relative_to(self.object_store.root))
                ),
            ) as opened:
                return dict(opened.attrs)
        except Exception as error:
            LOGGER.warning(
                "Failed to read canonical NetCDF attrs from %s: %s", product_path, error
            )
            return {}

    def _read_json_reference(
        self,
        reference: str,
        *,
        allow_same_store_uri: bool = False,
    ) -> Mapping[str, Any]:
        if not reference:
            raise ForcingStoreError("File repository JSON reference is empty.")
        key = reference
        if reference.startswith("s3://"):
            if not allow_same_store_uri:
                raise ForcingStoreError(
                    f"File repository JSON reference must be an object-store relative key: {reference!r}"
                )
            try:
                key = self.object_store.normalize_key(reference)
            except ValueError as error:
                raise ForcingStoreError(
                    f"File repository JSON reference must be an object-store relative key: {reference!r}"
                ) from error
        if Path(key).is_absolute() or ".." in Path(key).parts:
            raise ForcingStoreError(
                f"File repository JSON reference must be an object-store relative key: {reference!r}"
            )
        try:
            content = self.object_store.read_bytes_limited(
                key, max_bytes=MAX_OBJECT_MANIFEST_BYTES
            )
            payload = load_bounded_json(content, max_bytes=MAX_OBJECT_MANIFEST_BYTES)
        except (OSError, ObjectStoreError, ValueError, BoundedJSONError) as error:
            raise ForcingStoreError(
                f"Failed to read file repository JSON reference {reference}: {error}"
            ) from error
        if not isinstance(payload, Mapping):
            raise ForcingStoreError(
                f"File repository JSON reference {reference} must contain a JSON object."
            )
        return payload

    def _write_forcing_version_sidecar(self, record: Mapping[str, Any]) -> None:
        package_uri = str(record.get("forcing_package_uri") or "")
        if not package_uri:
            raise ForcingStoreError(
                "Cannot write forcing version sidecar without forcing_package_uri."
            )
        try:
            package_key = self.object_store.normalize_key(package_uri).strip("/")
            payload = _json_bytes(_json_safe(record))
            self.object_store.write_bytes_atomic(
                f"{package_key}/forcing_version_record.json", payload
            )
        except (OSError, ObjectStoreError, ValueError) as error:
            raise ForcingStoreError(
                f"Failed to write forcing version sidecar for {record.get('forcing_version_id')}: {error}"
            ) from error


del _CatalogMethods, _HandoffMethods
