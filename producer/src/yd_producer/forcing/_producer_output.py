# NWM@8ae9b8f2 workers/forcing_producer/producer.py
"""yd structural glue: imports and `_OutputMethods` stateless carrier shell.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import datetime
from typing import Any

from yd_producer.forcing._producer_common import (
    _directory_uri,
    _ensure_utc,
    _format_number,
    _format_time,
    _json_round_trip,
    _object_source_segment,
    format_cycle_time,
)
from yd_producer.forcing._producer_reuse import (
    _canonical_input_signature,
    _canonical_product_ids,
    _format_grid_signatures,
    _time_range_manifest,
)
from yd_producer.forcing._producer_stations import (
    _quality_flags_manifest,
    _station_forcing_filename,
    _station_forcing_index,
    _station_forcing_sort_key,
    _station_order_manifest,
    _station_properties,
    _station_signature,
    _validate_package_filenames,
)
from yd_producer.forcing._producer_timeseries import _forcing_coverage_end_time
from yd_producer.forcing._producer_types import (
    CanonicalProduct,
    FORCING_VARIABLES,
    FallbackLineage,
    ForcingComponent,
    ForcingProductionError,
    ForcingProductionResult,
    ForcingTimeseriesRow,
    MetStation,
    OUTPUT_UNITS,
)
from yd_producer.forcing.canonical_json import _json_bytes
from yd_producer.forcing.shud_forcing_contract import (
    CANONICAL_SHUD_FORCING_INDEX_MEMBER,
    SHUD_FORCING_ROLE,
)
from yd_producer.store.object_store import sha256_bytes

LOGGER = logging.getLogger("yd_producer.forcing.producer")


def format_tsd_forc(
    rows: Sequence[ForcingTimeseriesRow],
    *,
    stations: Sequence[MetStation],
    variables: Sequence[str] = FORCING_VARIABLES,
) -> str:
    station_ids = [station.station_id for station in stations]
    values = {(row.valid_time, row.variable, row.station_id): row.value for row in rows}
    valid_times = sorted({row.valid_time for row in rows})
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["valid_time", "variable", *station_ids])
    for valid_time in valid_times:
        for variable in variables:
            writer.writerow(
                [
                    _format_time(valid_time),
                    variable,
                    *[
                        _format_number(values[(valid_time, variable, station_id)])
                        for station_id in station_ids
                    ],
                ]
            )
    return output.getvalue()


def format_debug_csv(rows: Sequence[ForcingTimeseriesRow]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["valid_time", "station_id", "variable", "value", "unit"])
    for row in sorted(
        rows, key=lambda item: (item.valid_time, item.station_id, item.variable)
    ):
        writer.writerow(
            [
                _format_time(row.valid_time),
                row.station_id,
                row.variable,
                _format_number(row.value),
                row.unit,
            ]
        )
    return output.getvalue()


def format_shud_forcing_package(
    rows: Sequence[ForcingTimeseriesRow],
    *,
    stations: Sequence[MetStation],
    coverage_end_time: datetime | None = None,
    cycle_time: datetime,
) -> dict[str, str]:
    if not rows or not stations:
        return {}
    station_order = sorted(stations, key=_station_forcing_sort_key)
    rows_by_station_time: dict[tuple[str, datetime], dict[str, float]] = {}
    for row in rows:
        rows_by_station_time.setdefault((row.station_id, row.valid_time), {})[
            row.variable
        ] = row.value
    valid_times = sorted({row.valid_time for row in rows})
    start_time = _ensure_utc(cycle_time)
    first_row_time = _ensure_utc(valid_times[0])
    if first_row_time != start_time:
        raise ForcingProductionError(
            "Forcing Time_Day=0 must be the explicit cycle time "
            f"{start_time.isoformat()}; earliest valid time is {first_row_time.isoformat()}."
        )
    end_time = _ensure_utc(coverage_end_time or valid_times[-1])
    start_date = start_time.strftime("%Y%m%d")
    end_date = end_time.strftime("%Y%m%d")

    files: dict[str, str] = {}
    tsd = io.StringIO()
    tsd.write(f"{len(station_order)} {start_date}\n")
    tsd.write("shud\n")
    tsd.write("ID\tLon\tLat\tX\tY\tZ\tFilename\n")
    for station in station_order:
        forcing_index = _station_forcing_index(station)
        filename = _station_forcing_filename(station, forcing_index)
        props = _station_properties(station)
        tsd.write(
            "\t".join(
                [
                    str(forcing_index),
                    repr(float(station.longitude)),
                    repr(float(station.latitude)),
                    repr(float(props.get("x", 0.0) or 0.0)),
                    repr(float(props.get("y", 0.0) or 0.0)),
                    repr(float(props.get("z", station.elevation_m) or 0.0)),
                    filename,
                ]
            )
            + "\n"
        )
        csv_buffer = io.StringIO()
        csv_buffer.write(f"{len(valid_times)}\t6\t{start_date}\t{end_date}\n")
        csv_buffer.write("Time_Day\tPrecip\tTemp\tRH\tWind\tRN\n")
        for valid_time in valid_times:
            values = rows_by_station_time[(station.station_id, valid_time)]
            time_day = (_ensure_utc(valid_time) - start_time).total_seconds() / 86_400.0
            csv_buffer.write(
                "\t".join(
                    [
                        _format_number(time_day),
                        _format_number(values.get("PRCP", 0.0)),
                        _format_number(values.get("TEMP", 0.0)),
                        _format_number(values.get("RH", 0.0)),
                        _format_number(values.get("wind", 0.0)),
                        _format_number(values.get("Rn", 0.0)),
                    ]
                )
                + "\n"
            )
        files[f"shud/{filename}"] = csv_buffer.getvalue()
    files[CANONICAL_SHUD_FORCING_INDEX_MEMBER] = tsd.getvalue()
    return files


class _OutputMethods:
    """yd structural glue: stateless method carrier; no fields/init/super."""

    def _write_outputs_and_records(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        model_id: str,
        basin_id: str,
        basin_version_id: str,
        river_network_version_id: str,
        scheduler_canonical_identity: Mapping[str, Any],
        grid_id: str,
        stations: Sequence[MetStation],
        rows: Sequence[ForcingTimeseriesRow],
        components: Sequence[ForcingComponent],
        products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
        fallback_lineage: FallbackLineage | None = None,
        lead_window: Mapping[str, int | None] | None = None,
        station_signature: Mapping[str, Any] | None = None,
        grid_signature_by_source_grid: Mapping[tuple[str, str], str] | None = None,
        canonical_input_signature: Mapping[str, Any] | None = None,
        lineage_overrides: Mapping[str, Any] | None = None,
    ) -> ForcingProductionResult:
        assert self.repository is not None
        compact_cycle = format_cycle_time(cycle_time)
        source_segment = _object_source_segment(source_id)
        forcing_version_id = rows[0].forcing_version_id
        valid_times = sorted({row.valid_time for row in rows})
        coverage_end_time = _forcing_coverage_end_time(
            source_id, products_by_variable, row_times=valid_times
        )
        self._enforce_limit(
            "timeseries_row_count", len(rows), self.config.max_timeseries_row_count
        )
        _validate_package_filenames(
            forcing_filename=self.config.forcing_filename,
            csv_filename=self.config.csv_filename,
            package_manifest_filename=self.config.package_manifest_filename,
            stations=stations,
        )
        prefix = (
            f"forcing/{source_segment}/{compact_cycle}/{basin_version_id}/{model_id}"
        )
        package_uri = _directory_uri(self.object_store, prefix)
        package_manifest_key = f"{prefix}/{self.config.package_manifest_filename}"
        package_manifest_uri = self.object_store.uri_for_key(package_manifest_key)

        tsd_content = format_tsd_forc(
            rows, stations=stations, variables=self.config.output_variables
        ).encode("utf-8")
        csv_content = format_debug_csv(rows).encode("utf-8")
        tsd_key = f"{prefix}/{self.config.forcing_filename}"
        csv_key = f"{prefix}/{self.config.csv_filename}"
        tsd_uri = self.object_store.uri_for_key(tsd_key)
        csv_uri = self.object_store.uri_for_key(csv_key)
        tsd_checksum = sha256_bytes(tsd_content)
        csv_checksum = sha256_bytes(csv_content)
        shud_files = format_shud_forcing_package(
            rows,
            stations=stations,
            coverage_end_time=coverage_end_time,
            cycle_time=cycle_time,
        )
        shud_file_payloads: list[tuple[str, bytes]] = []
        shud_file_entries: list[dict[str, str]] = []
        for relative_path, content in shud_files.items():
            content_bytes = content.encode("utf-8")
            key = f"{prefix}/{relative_path}"
            uri = self.object_store.uri_for_key(key)
            shud_file_payloads.append((key, content_bytes))
            shud_file_entries.append(
                {
                    "role": (
                        SHUD_FORCING_ROLE
                        if relative_path == CANONICAL_SHUD_FORCING_INDEX_MEMBER
                        else "shud_forcing_csv"
                    ),
                    "relative_path": relative_path,
                    "uri": uri,
                    "checksum": sha256_bytes(content_bytes),
                }
            )

        variable_set = list(self.config.output_variables)
        units = {variable: OUTPUT_UNITS[variable] for variable in variable_set}
        time_range = _time_range_manifest(valid_times)
        coverage_time_range = _time_range_manifest([valid_times[0], coverage_end_time])
        station_order = _station_order_manifest(stations)
        quality_flags = _quality_flags_manifest(rows, products_by_variable)
        canonical_product_ids = _canonical_product_ids(products_by_variable)
        lineage_json = {
            "producer_version": self.config.producer_version,
            "source_id": source_id,
            "cycle_time": _format_time(cycle_time),
            "min_lead_hours": (lead_window or {}).get("min_lead_hours"),
            "max_lead_hours": (lead_window or {}).get("max_lead_hours"),
            "model_id": model_id,
            "basin_id": basin_id,
            "basin_version_id": basin_version_id,
            "river_network_version_id": river_network_version_id,
            "scheduler_canonical_identity": dict(scheduler_canonical_identity),
            "grid_id": grid_id,
            "station_count": len(stations),
            "station_ids": [station.station_id for station in stations],
            "station_signature": station_signature or _station_signature(stations),
            "grid_signatures": _format_grid_signatures(
                grid_signature_by_source_grid or {}
            ),
            "canonical_input_signature": canonical_input_signature
            or self._canonical_input_signature(products_by_variable, cycle_time),
            "forcing_variables": variable_set,
            "variable_set": variable_set,
            "units": units,
            "variable_count": len(variable_set),
            "time_range": coverage_time_range,
            "row_time_range": time_range,
            "quality_flags": quality_flags,
            "station_order": station_order,
            "canonical_product_ids": canonical_product_ids,
        }
        if lineage_overrides:
            lineage_json.update(_json_round_trip(dict(lineage_overrides)))
        if fallback_lineage is not None:
            lineage_json.update(
                {
                    "fallback_reason": fallback_lineage.fallback_reason,
                    "fallback_source_id": fallback_lineage.fallback_source_id,
                    "fallback_valid_times": [
                        _format_time(valid_time)
                        for valid_time in fallback_lineage.fallback_valid_times
                    ],
                }
            )
        file_entries = [
            {"role": "tsd_forc", "uri": tsd_uri, "checksum": tsd_checksum},
            {"role": "csv_debug", "uri": csv_uri, "checksum": csv_checksum},
            *shud_file_entries,
        ]
        lineage_json["output_files"] = file_entries
        package_manifest = {
            "forcing_version_id": forcing_version_id,
            "model_id": model_id,
            "source_id": source_id,
            "cycle_time": _format_time(cycle_time),
            "start_time": _format_time(valid_times[0]),
            "end_time": _format_time(coverage_end_time),
            "basin_id": basin_id,
            "basin_version_id": basin_version_id,
            "river_network_version_id": river_network_version_id,
            "scheduler_canonical_identity": dict(scheduler_canonical_identity),
            "station_count": len(stations),
            "timestep_count": len(valid_times),
            "variable_count": len(variable_set),
            "time_range": coverage_time_range,
            "row_time_range": time_range,
            "variable_set": variable_set,
            "units": units,
            "quality_flags": quality_flags,
            "station_order": station_order,
            "files": file_entries,
            "lineage": lineage_json,
        }
        package_content = _json_bytes(package_manifest)
        self._enforce_limit(
            "manifest_bytes", len(package_content), self.config.max_manifest_bytes
        )
        package_checksum = sha256_bytes(package_content)

        record = {
            "forcing_version_id": forcing_version_id,
            "model_id": model_id,
            "source_id": source_id,
            "cycle_time": cycle_time,
            "start_time": valid_times[0],
            "end_time": coverage_end_time,
            "station_count": len(stations),
            "forcing_package_uri": package_uri,
            "checksum": None,
            "lineage_json": {
                **lineage_json,
                "forcing_package_manifest_uri": package_manifest_uri,
                "forcing_package_manifest_checksum": package_checksum,
            },
        }
        self.repository.upsert_forcing_version(record)
        self.object_store.write_bytes_atomic(tsd_key, tsd_content)
        self.object_store.write_bytes_atomic(csv_key, csv_content)
        for key, content_bytes in shud_file_payloads:
            self.object_store.write_bytes_atomic(key, content_bytes)
        self.object_store.write_bytes_atomic(package_manifest_key, package_content)
        self.repository.replace_forcing_components(forcing_version_id, components)
        self.repository.replace_forcing_timeseries(forcing_version_id, rows)
        self.repository.finalize_forcing_version(forcing_version_id, package_checksum)
        return ForcingProductionResult(
            status="forcing_ready",
            forcing_version_id=forcing_version_id,
            forcing_package_uri=package_uri,
            checksum=package_checksum,
            station_count=len(stations),
            timestep_count=len(valid_times),
            variable_count=len(variable_set),
            time_range=time_range,
            units=units,
            file_uris={
                "tsd_forc": tsd_uri,
                "csv_debug": csv_uri,
                "package_manifest": package_manifest_uri,
            },
        )

    def _mark_cycle_ready_after_publication(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        forcing_version_id: str,
    ) -> None:
        assert self.repository is not None
        try:
            self.repository.update_forecast_cycle(
                source_id=source_id,
                cycle_time=cycle_time,
                status="forcing_ready",
                error_code="",
                error_message="",
            )
        except Exception:
            try:
                self._mark_forcing_version_pending(forcing_version_id)
            except Exception:
                LOGGER.exception(
                    "Failed to clear finalized checksum for forcing version %s after readiness update failure.",
                    forcing_version_id,
                )
            raise

    def _mark_forcing_version_pending(self, forcing_version_id: str) -> None:
        assert self.repository is not None
        clearer = getattr(self.repository, "clear_forcing_version_checksum", None)
        if not callable(clearer):
            return
        clearer(forcing_version_id)

    def _canonical_input_signature(
        self,
        products_by_variable: Mapping[str, Mapping[datetime, CanonicalProduct]],
        cycle_time: datetime,
    ) -> dict[str, Any]:
        return _canonical_input_signature(
            products_by_variable,
            cycle_time,
            object_store=self.object_store,
            max_manifest_bytes=self.config.max_manifest_bytes,
        )

    def _mark_failed(
        self,
        source_id: str,
        cycle_time: datetime,
        error: Exception,
        *,
        error_code: str = "FORCING_FAILED",
    ) -> None:
        if self.repository is None:
            return
        try:
            self.repository.update_forecast_cycle(
                source_id=source_id,
                cycle_time=cycle_time,
                status="failed_forcing",
                error_code=error_code,
                error_message=str(error),
            )
        except Exception:
            LOGGER.exception(
                "Failed to update forecast cycle forcing failure status for %s",
                format_cycle_time(cycle_time),
            )
