# NWM@8ae9b8f2 workers/canonical_converter/converter.py
"""yd structural glue: imports and `_CanonicalMethods` stateless carrier shell.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from yd_producer.canonical._common import (
    _apcp_accumulation_type_from_metadata,
    _apcp_step_range_from_metadata,
    _json_time,
    _manifest_entries,
    _manifest_metadata,
    _manifest_value,
    _mapping_value,
    format_cycle_time,
    map_variable,
    parse_cycle_time,
    required_standard_variables_for_source,
    unit_for_standard_variable,
)
from yd_producer.canonical._readiness import (
    _canonical_product_result_readiness_row,
    _canonical_readiness_error_message,
    evaluate_canonical_readiness,
)
from yd_producer.canonical._types import (
    CONVERSION_PARAMS,
    GFS_F000_OPTIONAL_INTERVAL_STANDARD_VARIABLES,
    CanonicalConversionError,
    CanonicalProductResult,
    CanonicalReadinessResult,
    ConversionResult,
    MissingForecastVariable,
    RawRecord,
)
from yd_producer.canonical._units import convert_units_with_metadata
from yd_producer.raw.source_identity import normalize_source_id
from yd_producer.store.object_store import ObjectStoreError, sha256_bytes
from yd_producer.store.safe_fs import SafeFilesystemError

LOGGER = logging.getLogger("yd_producer.canonical.converter")


class _CanonicalMethods:
    """yd structural glue: stateless method carrier; no fields/init/super."""

    def _unit_for_standard_variable(self, standard_variable: str) -> str:
        return unit_for_standard_variable(standard_variable)

    def convert_manifest(self, manifest: Any) -> ConversionResult:
        cycle_time = parse_cycle_time(_manifest_value(manifest, "cycle_time"))
        source_id = _manifest_value(manifest, "source_id")
        if source_id != self.config.source_id:
            raise CanonicalConversionError(
                f"Manifest source_id {source_id!r} does not match converter source_id {self.config.source_id!r}."
            )
        # 偏离（`剥离点`(f)/tasks.md 裁决 12）：入口归一一次，此后全链只用归一值。
        # pin 上 normalize_source_id("IFS") == "IFS"，yd 侧归一成 "ifs"（issue #5 落地），
        # 不归一则 readiness 以 "ifs" 过滤、打戳却用 "IFS"，IFS 的每一行都被丢弃。
        # 归一后产物对象键、catalog 键与行 source_id、canonical_product_id、readiness 行
        # 与过滤同用一个小写身份。权威是裁决 12 本身（canonical 命名空间归 yd 所有）加上
        # 这个真实缺陷，不是 products-contract §3.2——§3.2 管的是发布布局，canonical/ 键
        # 是 work 内 scratch 工件，不受其约束。
        source_id = normalize_source_id(source_id)

        try:
            entries = _manifest_entries(manifest)
            covered_pairs = self._covered_required_pairs(entries)
            missing_pairs = self._missing_required_pairs_from_covered(
                manifest, entries, covered_pairs
            )
            if missing_pairs:
                self._record_missing_products(source_id, cycle_time, missing_pairs)
                raise CanonicalConversionError(
                    self._missing_pairs_message(missing_pairs)
                )
            self._preflight_raw_entries(entries)

            entries_by_standard_variable = self._entries_by_standard_variable(entries)
            missing_variables = sorted(
                set(self.required_standard_variables())
                - set(entries_by_standard_variable)
            )
            if missing_variables:
                raise CanonicalConversionError(
                    f"Missing required canonical variables: {', '.join(missing_variables)}"
                )

            manifest_metadata = _manifest_metadata(manifest)
            products: list[CanonicalProductResult] = []
            for standard_variable in sorted(entries_by_standard_variable):
                native_entries = sorted(
                    entries_by_standard_variable[standard_variable],
                    key=lambda entry: int(entry["forecast_hour"]),
                )
                previous_values: tuple[float, ...] | np.ndarray | None = None
                previous_source_file: str | None = None
                previous_forecast_hour: int | None = None
                apcp_cumulative_gap = False
                for entry in native_entries:
                    record = self._read_record(entry)
                    apcp_accumulation_type = _apcp_accumulation_type_from_metadata(
                        record.metadata
                    )
                    if (
                        record.native_variable == "apcp"
                        and apcp_accumulation_type == "cumulative_since_cycle"
                        and apcp_cumulative_gap
                    ):
                        raise CanonicalConversionError(
                            "Cannot convert GFS APCP cumulative record after an interval-bucket gap "
                            f"at f{record.forecast_hour:03d}; exact interval de-accumulation would be ambiguous."
                        )
                    product = self._convert_record(
                        source_id=source_id,
                        cycle_time=cycle_time,
                        standard_variable=standard_variable,
                        record=record,
                        previous_values=previous_values,
                        previous_source_file=previous_source_file,
                        previous_forecast_hour=previous_forecast_hour,
                        policy_identity=_mapping_value(
                            manifest_metadata.get("source_policy")
                        ),
                        source_object_identity=_mapping_value(
                            manifest_metadata.get("source_object_identity")
                        ),
                    )
                    products.append(product)
                    if (
                        record.native_variable == "apcp"
                        and apcp_accumulation_type == "interval_bucket"
                    ):
                        previous_values = None
                        previous_source_file = None
                        previous_forecast_hour = None
                        apcp_cumulative_gap = True
                        continue
                    previous_values = record.values
                    previous_source_file = record.source_file
                    previous_forecast_hour = record.forecast_hour
                    if record.native_variable == "apcp":
                        apcp_cumulative_gap = False

            return self._complete_cycle_after_conversion(
                source_id=source_id,
                cycle_time=cycle_time,
                products=products,
                forecast_hours=self._configured_forecast_hours(manifest, entries),
                policy_identity=_mapping_value(manifest_metadata.get("source_policy")),
                source_object_identity=_mapping_value(
                    manifest_metadata.get("source_object_identity")
                ),
            )
        except Exception as error:
            try:
                self._update_cycle_status(
                    cycle_time,
                    status="failed_convert",
                    error_code="CONVERT_FAILED",
                    error_message=str(error),
                )
            except Exception:
                LOGGER.exception(
                    "Failed to record CONVERT_FAILED status for %s; preserving original conversion error",
                    format_cycle_time(cycle_time),
                )
            raise error

    def convert_manifest_uri(self, manifest_uri: str) -> ConversionResult:
        return self.convert_manifest(self.load_manifest(manifest_uri))

    def canonical_readiness(
        self,
        *,
        cycle_time: str | datetime,
        forecast_hours: Sequence[int] | None = None,
        policy_identity: Mapping[str, Any] | None = None,
        source_object_identity: Mapping[str, Any] | None = None,
        canonical_product_id: str | None = None,
        model_id: str | None = None,
        basin_id: str | None = None,
    ) -> CanonicalReadinessResult:
        return evaluate_canonical_readiness(
            source_id=self.config.source_id,
            cycle_time=cycle_time,
            products=(),
            forecast_hours=forecast_hours,
            policy_identity=policy_identity,
            source_object_identity=source_object_identity,
            canonical_product_id=canonical_product_id,
            model_id=model_id,
            basin_id=basin_id,
        )

    def _complete_cycle_after_conversion(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        products: Sequence[CanonicalProductResult],
        forecast_hours: Sequence[int],
        policy_identity: Mapping[str, Any] | None = None,
        source_object_identity: Mapping[str, Any] | None = None,
    ) -> ConversionResult:
        rows = [
            _canonical_product_result_readiness_row(
                product, source_id=source_id, cycle_time=cycle_time
            )
            for product in products
        ]
        readiness = evaluate_canonical_readiness(
            source_id=source_id,
            cycle_time=cycle_time,
            products=rows,
            forecast_hours=forecast_hours,
            policy_identity=policy_identity,
            source_object_identity=source_object_identity,
            canonical_product_id=f"canon_{normalize_source_id(source_id).lower()}_{format_cycle_time(cycle_time)}",
        )
        error_code = "" if readiness.ready else "CANONICAL_INCOMPLETE"
        error_message = (
            ""
            if readiness.ready
            else _canonical_readiness_error_message(readiness.evidence)
        )
        self._write_product_catalog(
            source_id=source_id,
            cycle_time=cycle_time,
            products=products,
        )
        self._update_cycle_status(
            cycle_time,
            status=readiness.status,
            error_code=error_code,
            error_message=error_message,
        )
        return ConversionResult(status=readiness.status, products=tuple(products))

    def _write_product_catalog(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        products: Sequence[CanonicalProductResult],
    ) -> None:
        compact_cycle = format_cycle_time(cycle_time)
        key = f"canonical/{source_id}/{compact_cycle}/_catalog/catalog.json"
        rows = [
            {
                "canonical_product_id": product.canonical_product_id,
                "source_id": source_id,
                "source_version": compact_cycle,
                "cycle_time": _json_time(cycle_time),
                "valid_time": _json_time(product.valid_time),
                "lead_time_hours": product.lead_time_hours,
                "variable": product.variable,
                "unit": self._unit_for_standard_variable(product.variable),
                "grid_id": self.config.grid_id,
                "grid_definition_uri": self.config.grid_definition_uri,
                "native_time_resolution": self.config.native_time_resolution,
                "native_spatial_resolution": self.config.native_spatial_resolution,
                "object_uri": product.object_uri,
                "checksum": product.checksum,
                "quality_flag": product.quality_flag,
                "lineage_json": dict(product.lineage_json),
            }
            for product in products
        ]
        payload = {
            "schema_version": "nhms.canonical.product_catalog.v1",
            "source_id": source_id,
            "cycle_time": _json_time(cycle_time),
            "products": rows,
        }
        try:
            self.object_store.write_bytes_atomic(
                key,
                json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
            )
        except (OSError, ObjectStoreError, ValueError) as error:
            raise CanonicalConversionError(
                f"Failed to write canonical product catalog {key}: {error}"
            ) from error

    def _read_records(self, entries: list[dict[str, Any]]) -> list[RawRecord]:
        records: list[RawRecord] = []
        for entry in entries:
            native_variable = entry["variable"]
            standard_variable = map_variable(
                native_variable, self.config.variable_mapping
            )
            if standard_variable is None:
                LOGGER.warning(
                    "Skipping unmapped variable %s from %s",
                    native_variable,
                    entry["local_key"],
                )
                continue
            records.append(self._read_record(entry))
        return records

    def _read_record(self, entry: Mapping[str, Any]) -> RawRecord:
        return self._read_record_with_xarray(entry)

    def _preflight_raw_entries(self, entries: list[dict[str, Any]]) -> None:
        for entry in entries:
            if map_variable(entry["variable"], self.config.variable_mapping) is None:
                continue
            local_key = str(entry["local_key"])
            try:
                kind = self.object_store.object_kind(local_key)
            except (
                OSError,
                ObjectStoreError,
                ValueError,
                SafeFilesystemError,
            ) as error:
                raise CanonicalConversionError(
                    f"Raw object {local_key} is not a contained regular file: {error}"
                ) from error
            if kind != "file":
                raise CanonicalConversionError(
                    f"Raw object {local_key} is not a contained regular file: {kind}"
                )

    def _select_data_variable(
        self, dataset: Any, expected_native_variable: str, local_key: str
    ) -> str:
        return self._select_cfgrib_data_variable(
            dataset, expected_native_variable, local_key
        )

    def _select_cfgrib_data_variable(
        self, dataset: Any, expected_native_variable: str, local_key: str
    ) -> str:
        expected_names = set(
            self.config.cfgrib_variable_aliases.get(expected_native_variable, ())
        )
        expected_names.add(expected_native_variable)
        matches: list[str] = []
        available: list[str] = []
        for data_variable in dataset.data_vars:
            variable_attrs = dataset[data_variable].attrs
            candidates = {
                str(data_variable),
                str(variable_attrs.get("GRIB_shortName", "")),
                str(variable_attrs.get("shortName", "")),
            }
            available.append(
                "/".join(sorted(candidate for candidate in candidates if candidate))
            )
            if candidates & expected_names:
                matches.append(str(data_variable))

        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise CanonicalConversionError(
                f"cfgrib variable mismatch for {local_key}: manifest expected {expected_native_variable} "
                f"(aliases: {sorted(expected_names)}); dataset variables: {available}."
            )
        raise CanonicalConversionError(
            f"cfgrib variable mapping for {local_key} is ambiguous: manifest expected "
            f"{expected_native_variable}, matched {matches}."
        )

    def _group_records(self, records: list[RawRecord]) -> dict[str, list[RawRecord]]:
        grouped: dict[str, list[RawRecord]] = {}
        for record in records:
            standard_variable = map_variable(
                record.native_variable, self.config.variable_mapping
            )
            if standard_variable is None:
                LOGGER.warning(
                    "Skipping unmapped variable %s from %s",
                    record.native_variable,
                    record.source_file,
                )
                continue
            grouped.setdefault(standard_variable, []).append(record)
        return grouped

    def _entries_by_standard_variable(
        self, entries: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            native_variable = entry["variable"]
            standard_variable = map_variable(
                native_variable, self.config.variable_mapping
            )
            if standard_variable is None:
                LOGGER.warning(
                    "Skipping unmapped variable %s from %s",
                    native_variable,
                    entry["local_key"],
                )
                continue
            grouped.setdefault(standard_variable, []).append(entry)
        return grouped

    def _entries_by_hour_and_variable(
        self, entries: list[dict[str, Any]]
    ) -> dict[int, dict[str, dict[str, Any]]]:
        grouped: dict[int, dict[str, dict[str, Any]]] = {}
        for entry in entries:
            native_variable = str(entry["variable"])
            if map_variable(native_variable, self.config.variable_mapping) is None:
                LOGGER.warning(
                    "Skipping unmapped variable %s from %s",
                    native_variable,
                    entry["local_key"],
                )
                continue
            grouped.setdefault(int(entry["forecast_hour"]), {})[native_variable] = entry
        return grouped

    def _covered_required_pairs(
        self, entries: list[dict[str, Any]]
    ) -> set[tuple[str, int]]:
        return {
            (str(entry["variable"]), int(entry["forecast_hour"]))
            for entry in entries
            if map_variable(str(entry["variable"]), self.config.variable_mapping)
            is not None
        }

    def _missing_required_pairs(
        self,
        manifest: Any,
        entries: list[dict[str, Any]],
        records: list[RawRecord],
    ) -> tuple[MissingForecastVariable, ...]:
        covered = {(record.native_variable, record.forecast_hour) for record in records}
        return self._missing_required_pairs_from_covered(manifest, entries, covered)

    def _missing_required_pairs_from_covered(
        self,
        manifest: Any,
        entries: list[dict[str, Any]],
        covered: set[tuple[str, int]],
    ) -> tuple[MissingForecastVariable, ...]:
        forecast_hours = self._configured_forecast_hours(manifest, entries)
        missing: list[MissingForecastVariable] = []
        required_standard_variables = set(self.required_standard_variables())
        for forecast_hour in forecast_hours:
            for native_variable, standard_variable in sorted(
                self.config.variable_mapping.items()
            ):
                if standard_variable not in required_standard_variables:
                    continue
                if (
                    normalize_source_id(self.config.source_id) == "gfs"
                    and forecast_hour == 0
                    and standard_variable
                    in GFS_F000_OPTIONAL_INTERVAL_STANDARD_VARIABLES
                ):
                    continue
                if (native_variable, forecast_hour) not in covered:
                    missing.append(
                        MissingForecastVariable(
                            native_variable=native_variable,
                            standard_variable=standard_variable,
                            forecast_hour=forecast_hour,
                        )
                    )
        return tuple(missing)

    def required_standard_variables(self) -> tuple[str, ...]:
        return required_standard_variables_for_source(self.config.source_id)

    def _configured_forecast_hours(
        self, manifest: Any, entries: list[dict[str, Any]]
    ) -> list[int]:
        metadata = _manifest_metadata(manifest)
        if isinstance(metadata.get("forecast_hours"), list):
            return sorted(
                {int(forecast_hour) for forecast_hour in metadata["forecast_hours"]}
            )

        first_hour = metadata.get("first_forecast_hour")
        last_hour = metadata.get("last_forecast_hour")
        step_hours = self._native_time_resolution_hours()
        if first_hour is not None and last_hour is not None and step_hours is not None:
            return list(range(int(first_hour), int(last_hour) + 1, step_hours))

        return sorted({int(entry["forecast_hour"]) for entry in entries})

    def _native_time_resolution_hours(self) -> int | None:
        resolution = self.config.native_time_resolution.strip().lower()
        if not resolution.endswith("h"):
            return None
        try:
            step_hours = int(resolution[:-1])
        except ValueError:
            return None
        return step_hours if step_hours > 0 else None

    def _missing_pairs_message(
        self, missing_pairs: tuple[MissingForecastVariable, ...]
    ) -> str:
        details = ", ".join(
            f"{pair.native_variable}->{pair.standard_variable} f{pair.forecast_hour:03d}"
            for pair in missing_pairs[:20]
        )
        suffix = ""
        if len(missing_pairs) > 20:
            suffix = f", ... ({len(missing_pairs)} total missing pairs)"
        return f"Missing required canonical variables forecast-hour coverage: {details}{suffix}"

    def _record_missing_products(
        self,
        source_id: str,
        cycle_time: datetime,
        missing_pairs: tuple[MissingForecastVariable, ...],
    ) -> None:
        compact_cycle = format_cycle_time(cycle_time)
        for pair in missing_pairs:
            canonical_product_id = f"{source_id}_{compact_cycle}_{pair.standard_variable}_f{pair.forecast_hour:03d}"
            object_key = f"canonical/{source_id}/{compact_cycle}/{pair.standard_variable}/{canonical_product_id}.missing"
            lineage_json = {
                "source_files": [],
                "source_cycle_id": f"{source_id}_{compact_cycle}",
                "conversion_params": {
                    "operation": "coverage_validation",
                    "missing_native_variable": pair.native_variable,
                    "missing_standard_variable": pair.standard_variable,
                    "missing_forecast_hour": pair.forecast_hour,
                },
                "converter_version": self.config.converter_version,
            }
            self._upsert_product(
                {
                    "canonical_product_id": canonical_product_id,
                    "source_id": source_id,
                    "source_version": compact_cycle,
                    "cycle_time": cycle_time,
                    "valid_time": cycle_time + timedelta(hours=pair.forecast_hour),
                    "lead_time_hours": pair.forecast_hour,
                    "variable": pair.standard_variable,
                    "unit": unit_for_standard_variable(pair.standard_variable),
                    "grid_id": self.config.grid_id,
                    "grid_definition_uri": self.config.grid_definition_uri,
                    "native_time_resolution": self.config.native_time_resolution,
                    "native_spatial_resolution": self.config.native_spatial_resolution,
                    "object_uri": self.object_store.uri_for_key(object_key),
                    "checksum": "",
                    "quality_flag": "fail",
                    "lineage_json": lineage_json,
                }
            )

    def _convert_record(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        standard_variable: str,
        record: RawRecord,
        previous_values: tuple[float, ...] | np.ndarray | None,
        previous_source_file: str | None,
        previous_forecast_hour: int | None,
        policy_identity: Mapping[str, Any] | None = None,
        source_object_identity: Mapping[str, Any] | None = None,
    ) -> CanonicalProductResult:
        conversion = convert_units_with_metadata(
            record.native_variable,
            record.values,
            previous_values,
            forecast_hour=record.forecast_hour,
            previous_forecast_hour=previous_forecast_hour,
            accumulation_type=_apcp_accumulation_type_from_metadata(record.metadata),
            step_range=_apcp_step_range_from_metadata(record.metadata),
        )
        valid_time = cycle_time + timedelta(hours=record.forecast_hour)
        compact_cycle = format_cycle_time(cycle_time)
        canonical_product_id = f"{source_id}_{compact_cycle}_{standard_variable}_f{record.forecast_hour:03d}"
        object_key = f"canonical/{source_id}/{compact_cycle}/{standard_variable}/{canonical_product_id}.nc"
        source_files = [record.source_file]
        apcp_accumulation_type = _apcp_accumulation_type_from_metadata(record.metadata)
        if (
            record.native_variable == "apcp"
            and previous_source_file is not None
            and apcp_accumulation_type != "interval_bucket"
        ):
            source_files = [previous_source_file, record.source_file]
        conversion_params: dict[str, Any] = {
            "native_variable": record.native_variable,
            "operation": CONVERSION_PARAMS.get(record.native_variable, "pass_through"),
        }
        if record.native_variable == "apcp":
            conversion_params["accumulation_type"] = (
                apcp_accumulation_type or "cumulative_since_cycle"
            )
            step_range = _apcp_step_range_from_metadata(record.metadata)
            if step_range:
                conversion_params["step_range"] = step_range
            if apcp_accumulation_type == "interval_bucket":
                conversion_params["operation"] = "interval_bucket_mm_to_mm_day"
            if record.metadata:
                conversion_params["raw_metadata"] = dict(record.metadata)
        if conversion.anomalies:
            conversion_params["anomalies"] = list(conversion.anomalies)
            conversion_params["negative_delta_forecast_hours"] = [
                anomaly["forecast_hour"]
                for anomaly in conversion.anomalies
                if anomaly.get("type") == "negative_apcp_delta"
            ]
        lineage_json = {
            "source_files": source_files,
            "source_cycle_id": f"{source_id}_{compact_cycle}",
            "conversion_params": conversion_params,
            "converter_version": self.config.converter_version,
        }
        if policy_identity:
            lineage_json["policy_identity"] = dict(policy_identity)
        if source_object_identity:
            lineage_json["source_object_identity"] = dict(source_object_identity)
        self._ensure_grid_definition(record)
        content = self._serialize_product(
            variable=standard_variable,
            values=conversion.values,
            cycle_time=cycle_time,
            valid_time=valid_time,
            lead_time_hours=record.forecast_hour,
            unit=unit_for_standard_variable(standard_variable),
            lineage_json=lineage_json,
        )
        checksum = sha256_bytes(content)

        existing = self._get_existing_product(canonical_product_id)
        if self._existing_product_is_current(existing, object_key, checksum):
            return CanonicalProductResult(
                canonical_product_id=canonical_product_id,
                variable=standard_variable,
                valid_time=valid_time,
                lead_time_hours=record.forecast_hour,
                object_uri=existing["object_uri"],
                checksum=existing["checksum"],
                status="already_done",
                quality_flag=existing.get("quality_flag", "ok"),
                lineage_json=_mapping_value(existing.get("lineage_json")),
            )

        try:
            object_uri = self.object_store.write_bytes_atomic(object_key, content)
        except (OSError, ObjectStoreError, ValueError) as error:
            raise CanonicalConversionError(
                f"Failed to write canonical product {object_key}: {error}"
            ) from error

        record_payload = {
            "canonical_product_id": canonical_product_id,
            "source_id": source_id,
            "source_version": compact_cycle,
            "cycle_time": cycle_time,
            "valid_time": valid_time,
            "lead_time_hours": record.forecast_hour,
            "variable": standard_variable,
            "unit": unit_for_standard_variable(standard_variable),
            "grid_id": self.config.grid_id,
            "grid_definition_uri": self.config.grid_definition_uri,
            "native_time_resolution": self.config.native_time_resolution,
            "native_spatial_resolution": self.config.native_spatial_resolution,
            "object_uri": object_uri,
            "checksum": checksum,
            "quality_flag": conversion.quality_flag,
            "lineage_json": lineage_json,
        }
        self._upsert_product(record_payload)
        return CanonicalProductResult(
            canonical_product_id=canonical_product_id,
            variable=standard_variable,
            valid_time=valid_time,
            lead_time_hours=record.forecast_hour,
            object_uri=object_uri,
            checksum=checksum,
            status="updated" if existing else "created",
            quality_flag=conversion.quality_flag,
            lineage_json=lineage_json,
        )

    def _serialize_product(
        self,
        *,
        variable: str,
        values: tuple[float, ...],
        cycle_time: datetime,
        valid_time: datetime,
        lead_time_hours: int,
        unit: str,
        lineage_json: Mapping[str, Any],
    ) -> bytes:
        try:
            import netCDF4  # noqa: F401
            import xarray as xr
        except ImportError as error:
            raise CanonicalConversionError(
                "NetCDF4 serialization requires xarray and netCDF4; install both dependencies."
            ) from error

        dataset = xr.Dataset(
            data_vars={variable: ("point", list(values))},
            coords={"point": list(range(len(values)))},
            attrs={
                "cycle_time": cycle_time.isoformat(),
                "valid_time": valid_time.isoformat(),
                "lead_time_hours": lead_time_hours,
                "unit": unit,
                "grid_id": self.config.grid_id,
                "lineage_json": json.dumps(dict(lineage_json), sort_keys=True),
            },
        )
        try:
            with tempfile.NamedTemporaryFile(suffix=".nc") as temp_file:
                dataset.to_netcdf(temp_file.name, engine="netcdf4", format="NETCDF4")
                temp_file.seek(0)
                return temp_file.read()
        except (OSError, ValueError, RuntimeError) as error:
            raise CanonicalConversionError(
                f"Failed to serialize NetCDF4 product {variable}: {error}"
            ) from error
        finally:
            dataset.close()

    def _get_existing_product(self, canonical_product_id: str) -> dict[str, Any] | None:
        return None

    def _existing_product_is_current(
        self,
        existing: Mapping[str, Any] | None,
        object_key: str,
        checksum: str,
    ) -> bool:
        if existing is None or existing.get("quality_flag") == "fail":
            return False
        # Treat products written by a different (or missing, i.e. legacy)
        # converter_version as stale so semantic changes force a re-conversion.
        # converter_version is recorded inside lineage_json (not at top level),
        # with a top-level fallback for forward compatibility.
        existing_lineage = _mapping_value(existing.get("lineage_json"))
        existing_version = existing_lineage.get(
            "converter_version", existing.get("converter_version")
        )
        if existing_version != self.config.converter_version:
            return False
        existing_checksum = str(existing.get("checksum", ""))
        if existing_checksum != checksum:
            return False
        try:
            return (
                self.object_store.exists(object_key)
                and self.object_store.checksum(object_key) == checksum
            )
        except (OSError, ObjectStoreError, ValueError):
            LOGGER.exception(
                "Failed to verify existing canonical object %s", object_key
            )
            return False

    def _upsert_product(self, record: Mapping[str, Any]) -> None:
        return

    def _update_cycle_status(
        self,
        cycle_time: datetime,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        return
