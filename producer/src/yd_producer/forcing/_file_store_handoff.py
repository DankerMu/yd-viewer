# NWM@8ae9b8f2 workers/forcing_producer/file_store.py
"""yd structural glue: imports and `_HandoffMethods` stateless carrier shell.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any

from yd_producer.forcing._file_store_common import (
    FORCING_DOMAIN_HANDOFF_CONTRACT_ID,
    FORCING_DOMAIN_HANDOFF_SCHEMA_VERSION,
    FORCING_DOMAIN_PACKAGE_CONTRACT_ID,
    FORCING_DOMAIN_PACKAGE_MANIFEST_CHECKSUM_FIELD,
    FORCING_DOMAIN_PACKAGE_MANIFEST_URI_FIELD,
    FORCING_PACKAGE_MANIFEST_CHECKSUM_FIELD,
    FORCING_PACKAGE_MANIFEST_URI_FIELD,
    ForcingStoreError,
    _ensure_utc,
    _first_unit,
    _float_value,
    _forcing_package_manifest_uri,
    _format_time,
    _handoff_run_id,
    _handoff_timeseries_row,
    _json_bytes,
    _json_safe,
    _lineage_value,
    _manifest_station_order,
    _station_index_member_basename,
    _station_name_from_id,
    _time_lattice,
    _time_lattice_resolution_by_variable_time,
    _time_value,
    _timeseries_sort_key,
    _weight_sort_key,
)
from yd_producer.forcing._producer_common import format_cycle_time
from yd_producer.forcing._producer_types import (
    ForcingTimeseriesRow,
    InterpolationWeight,
)
from yd_producer.store.object_store import sha256_bytes


class _HandoffMethods:
    """yd structural glue: stateless method carrier; no fields/init/super."""

    def _write_forcing_domain_handoff(self, record: Mapping[str, Any]) -> None:
        forcing_version_id = str(record.get("forcing_version_id") or "")
        if not forcing_version_id:
            raise ForcingStoreError(
                "Cannot write forcing-domain handoff without forcing_version_id."
            )
        rows = self._forcing_timeseries_rows.get(forcing_version_id, ())
        if not rows:
            raise ForcingStoreError(
                f"Cannot write forcing-domain handoff for {forcing_version_id}: no timeseries rows."
            )

        package_uri = str(record.get("forcing_package_uri") or "")
        if not package_uri:
            raise ForcingStoreError(
                f"Cannot write forcing-domain handoff for {forcing_version_id}: missing package URI."
            )
        package_key = self.object_store.normalize_key(package_uri).strip("/")
        package_manifest_uri = _forcing_package_manifest_uri(
            record, self.object_store.uri_for_key(package_key)
        )
        package_manifest = self._read_json_reference(
            package_manifest_uri, allow_same_store_uri=True
        )

        source_id = str(
            record.get("source_id")
            or package_manifest.get("source_id")
            or rows[0].source_id
        )
        source_key = source_id.lower()
        cycle_time = _time_value(
            record.get("cycle_time")
            or package_manifest.get("cycle_time")
            or rows[0].valid_time
        )
        compact_cycle = format_cycle_time(cycle_time)
        model_id = str(record.get("model_id") or package_manifest.get("model_id") or "")
        basin_version_id = str(
            record.get("basin_version_id")
            or package_manifest.get("basin_version_id")
            or rows[0].basin_version_id
        )
        basin_id = str(
            record.get("basin_id")
            or package_manifest.get("basin_id")
            or _lineage_value(record, "basin_id")
            or basin_version_id
        )
        run_id = _handoff_run_id(
            record,
            source_key=source_key,
            compact_cycle=compact_cycle,
            model_id=model_id,
        )
        start_time = min(_ensure_utc(row.valid_time) for row in rows)
        end_time = max(_ensure_utc(row.valid_time) for row in rows)
        forcing_package_manifest_checksum = str(record.get("checksum") or "")
        if not forcing_package_manifest_checksum:
            raise ForcingStoreError(
                f"Cannot write forcing-domain handoff for {forcing_version_id}: missing package checksum."
            )

        station_rows = self._handoff_station_rows(
            record=record,
            package_manifest=package_manifest,
            rows=rows,
            basin_id=basin_id,
            basin_version_id=basin_version_id,
            model_id=model_id,
        )
        native_resolution_by_time = _time_lattice_resolution_by_variable_time(rows)
        timeseries_rows = [
            _handoff_timeseries_row(
                row,
                native_resolution=native_resolution_by_time.get(
                    (row.variable, _ensure_utc(row.valid_time))
                ),
            )
            for row in sorted(rows, key=_timeseries_sort_key)
        ]
        weight_rows = self._handoff_weight_rows(
            record=record,
            package_manifest=package_manifest,
            source_id=source_id,
            model_id=model_id,
        )
        if not weight_rows:
            raise ForcingStoreError(
                f"Cannot write forcing-domain handoff for {forcing_version_id}: no interpolation rows."
            )

        payload_specs = {
            "station_inventory": (
                "station_inventory.json",
                "met.met_station",
                station_rows,
            ),
            "station_timeseries": (
                "station_timeseries.json",
                "met.forcing_station_timeseries",
                timeseries_rows,
            ),
            "interpolation_weights": (
                "interp_weights.json",
                "met.interp_weight",
                weight_rows,
            ),
        }
        payload_refs: dict[str, dict[str, Any]] = {}
        for role, (filename, table, payload_rows) in payload_specs.items():
            payload_key = f"{package_key}/payloads/{filename}"
            payload_content = _json_bytes(payload_rows)
            payload_uri = self.object_store.write_bytes_atomic(
                payload_key, payload_content
            )
            payload_refs[role] = {
                "uri": payload_uri,
                "checksum_sha256": sha256_bytes(payload_content),
                "table": table,
                "row_count": len(payload_rows),
                "content_type": "application/json",
            }

        variables = sorted({row.variable for row in rows})
        units = {variable: _first_unit(rows, variable) for variable in variables}
        payload_refs["station_timeseries"]["variables"] = variables
        payload_refs["station_timeseries"]["units"] = units
        payload_refs["station_timeseries"]["time_lattice"] = _time_lattice(
            native_resolution_by_time
        )

        table_row_counts = {
            "met.forcing_version": 1,
            "met.met_station": len(station_rows),
            "met.forcing_station_timeseries": len(timeseries_rows),
            "met.interp_weight": len(weight_rows),
        }
        forcing_package_uri = self.object_store.uri_for_key(package_key)
        forcing_domain_package_uri = self.object_store.uri_for_key(
            f"{package_key}/forcing_domain_package.json"
        )
        package_envelope = {
            "schema_version": FORCING_DOMAIN_HANDOFF_SCHEMA_VERSION,
            "contract_id": FORCING_DOMAIN_PACKAGE_CONTRACT_ID,
            "run_id": run_id,
            "source_id": source_id,
            "source": source_key,
            "cycle_time": _format_time(cycle_time),
            "start_time": _format_time(start_time),
            "end_time": _format_time(end_time),
            "model_id": model_id,
            "basin_id": basin_id,
            "basin_version_id": basin_version_id,
            "forcing_version_id": forcing_version_id,
            "station_count": len(station_rows),
            "payloads": payload_refs,
            "table_row_counts": table_row_counts,
        }
        package_content = _json_bytes(package_envelope)
        self.object_store.write_bytes_atomic(
            f"{package_key}/forcing_domain_package.json", package_content
        )
        package_checksum = sha256_bytes(package_content)

        handoff = {
            **package_envelope,
            "contract_id": FORCING_DOMAIN_HANDOFF_CONTRACT_ID,
            "model_package_uri": self._model_package_uri(
                model_id, record, package_manifest
            ),
            "forcing_uri": forcing_package_uri,
            "forcing_package_uri": forcing_package_uri,
            FORCING_PACKAGE_MANIFEST_URI_FIELD: package_manifest_uri,
            FORCING_PACKAGE_MANIFEST_CHECKSUM_FIELD: forcing_package_manifest_checksum,
            FORCING_DOMAIN_PACKAGE_MANIFEST_URI_FIELD: forcing_domain_package_uri,
            FORCING_DOMAIN_PACKAGE_MANIFEST_CHECKSUM_FIELD: package_checksum,
            "scenario_id": str(
                record.get("scenario_id") or f"forecast_{source_key}_deterministic"
            ),
            "run_manifest_uri": self.object_store.uri_for_key(
                f"runs/{run_id}/input/manifest.json"
            ),
            "output_uri": self.object_store.uri_for_key(f"runs/{run_id}/output/"),
        }
        self.object_store.write_bytes_atomic(
            f"runs/{run_id}/input/forcing_domain_handoff.json",
            _json_bytes(handoff),
        )

    def _handoff_station_rows(
        self,
        *,
        record: Mapping[str, Any],
        package_manifest: Mapping[str, Any],
        rows: Sequence[ForcingTimeseriesRow],
        basin_id: str,
        basin_version_id: str,
        model_id: str,
    ) -> list[dict[str, Any]]:
        station_ids = {row.station_id for row in rows}
        stations_by_id = {
            station.station_id: station
            for station in self._stations_by_basin_version.get(basin_version_id, ())
        }
        manifest_stations = _manifest_station_order(package_manifest)
        ordered_ids = [
            station_id for station_id in manifest_stations if station_id in station_ids
        ]
        ordered_ids.extend(sorted(station_ids - set(ordered_ids)))
        # Provenance label for every station of this package: the station-index
        # member this package actually declares (resolved once, not per station).
        source_member = _station_index_member_basename(package_manifest)

        station_rows: list[dict[str, Any]] = []
        for station_id in ordered_ids:
            manifest_station = manifest_stations.get(station_id, {})
            station = stations_by_id.get(station_id)
            longitude = _float_value(manifest_station.get("longitude"))
            latitude = _float_value(manifest_station.get("latitude"))
            elevation_m = _float_value(manifest_station.get("elevation_m"))
            if station is not None:
                longitude = station.longitude if longitude is None else longitude
                latitude = station.latitude if latitude is None else latitude
                elevation_m = (
                    station.elevation_m if elevation_m is None else elevation_m
                )
            if longitude is None or latitude is None or elevation_m is None:
                raise ForcingStoreError(
                    f"Cannot write forcing-domain handoff: station {station_id} lacks coordinates."
                )
            forcing_index = manifest_station.get("shud_forcing_index")
            properties = dict(station.properties_json if station is not None else {})
            if forcing_index is not None:
                properties.setdefault("shud_forcing_index", forcing_index)
            forcing_filename = manifest_station.get("forcing_filename")
            if forcing_filename:
                properties.setdefault("forcing_filename", forcing_filename)
            if source_member is not None:
                properties.setdefault("source", source_member)
            properties.setdefault("basin_id", basin_id)
            properties.setdefault("basin_version_id", basin_version_id)
            properties.setdefault("model_id", model_id)
            station_name = (
                station.station_name
                if station is not None and station.station_name
                else _station_name_from_id(basin_id, station_id)
            )
            station_rows.append(
                {
                    "station_id": station_id,
                    "basin_version_id": basin_version_id,
                    "station_name": station_name,
                    "longitude": longitude,
                    "latitude": latitude,
                    "elevation_m": elevation_m,
                    "station_role": station.station_role
                    if station is not None
                    else "forcing_grid",
                    # §D2 flag ownership: mirror activation belongs to Change 8's cutover flip,
                    # not the runtime producer/file plane. Emit `False` so the ingest lands fresh
                    # rows inactive and the ON CONFLICT DO UPDATE preserves an existing flip.
                    "active_flag": False,
                    "properties_json": _json_safe(properties),
                }
            )
        return station_rows

    def _handoff_weight_rows(
        self,
        *,
        record: Mapping[str, Any],
        package_manifest: Mapping[str, Any],
        source_id: str,
        model_id: str,
    ) -> list[dict[str, Any]]:
        grid_id = str(
            record.get("grid_id")
            or _lineage_value(record, "grid_id")
            or package_manifest.get("grid_id")
            or (package_manifest.get("lineage") or {}).get("grid_id")
            or ""
        )
        scopes = []
        if grid_id:
            scopes.append((source_id, grid_id, model_id))
        scopes.extend(
            scope
            for scope in sorted(self._weights_by_scope)
            if scope[0].lower() == source_id.lower()
            and scope[2] == model_id
            and scope not in scopes
        )
        weights: list[InterpolationWeight] = []
        for scope in scopes:
            weights.extend(self._weights_by_scope.get(scope, ()))
        return [
            {
                "source_id": weight.source_id,
                "grid_id": weight.grid_id,
                "model_id": weight.model_id,
                "station_id": weight.station_id,
                "variable": weight.variable,
                "grid_cell_id": weight.grid_cell_id,
                "weight": weight.weight,
                "method": weight.method,
            }
            for weight in sorted(weights, key=_weight_sort_key)
        ]

    def _model_package_uri(
        self,
        model_id: str,
        record: Mapping[str, Any],
        package_manifest: Mapping[str, Any],
    ) -> str:
        for value in (
            record.get("model_package_uri"),
            _lineage_value(record, "model_package_uri"),
            package_manifest.get("model_package_uri"),
            (package_manifest.get("lineage") or {}).get("model_package_uri")
            if isinstance(package_manifest.get("lineage"), Mapping)
            else None,
        ):
            if value:
                return str(value)
        try:
            model = self._model_entry(model_id)
        except Exception:
            return self.object_store.uri_for_key(f"models/{model_id}/package/")
        return str(
            model.get("model_package_uri")
            or model.get("manifest_uri")
            or (model.get("resource_profile") or {}).get("model_package_manifest_uri")
            or self.object_store.uri_for_key(f"models/{model_id}/package/")
        )
