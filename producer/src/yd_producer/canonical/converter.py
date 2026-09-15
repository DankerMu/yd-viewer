# NWM@8ae9b8f2 workers/canonical_converter/converter.py
# 偏离（#103）：GFS/IFS convert_manifest 在任一转换循环写入前，对全部 selected
# raw 走 store no-follow 预检（object_kind / containment_root=store.root）；解码经
# LocalObjectStore.iter_bytes（内部持 no-follow fd）流式写入上下文管理的私有临时
# 文件，cfgrib 与 netCDF4 回退共用同一 staged 源，绝不把原 raw Path 交给解码器。
# 私有 staging 是设计授权的可移植性取舍（相对 descriptor alias / /dev/fd）。
# 偏离（#102）：load_manifest 与既有 grid-definition JSON 读改走
# store.read_bytes_limited(max_bytes=MAX_OBJECT_MANIFEST_BYTES)；#103
# _staged_contained_raw_path 在 staging/解码前 store.size 无跟随预检，并在
# iter_bytes 流式累计 observed，超过 MAX_RAW_INPUT_BYTES=512*1024*1024 以
# CanonicalConversionError 明示 input size、key、observed size、limit，且不写
# overflow chunk、不调用解码器。解码 RawRecord.values 为
# np.asarray(..., dtype=np.float64).ravel()，不经 .tolist()/Python float 元组。
# 不改算法、坐标元组、错误类型或已接受产物字节。
# 偏离（#104）：IFSCanonicalConverterConfig.grid_definition_uri 唯一值改为
# canonical/ifs/grid/ifs_0p25/grid.json；写入、存在/签名守卫与全部 catalog
# 发出点继续共用该 config 字段，不另建 URI 常量、大小写 fallback 或迁移别名。
from __future__ import annotations

# Compat namespace: leftover stdlib imports and leaf re-exports keep the
# pre-split module bindings. F401 annotations below preserve intentional
# facade exports.
import json
import logging
import math  # noqa: F401
import tempfile
from collections.abc import Iterable, Iterator, Mapping, Sequence  # noqa: F401
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, is_dataclass  # noqa: F401
from datetime import UTC, datetime, timedelta  # noqa: F401
from pathlib import Path
from typing import Any

import numpy as np

from yd_producer.raw.source_identity import normalize_source_id
from yd_producer.store.object_store import (
    MAX_OBJECT_MANIFEST_BYTES,
    LocalObjectStore,
    ObjectStoreError,
    sha256_bytes,
)
from yd_producer.store.safe_fs import SafeFilesystemError

LOGGER = logging.getLogger(__name__)

MAX_RAW_INPUT_BYTES = 512 * 1024 * 1024

from yd_producer.canonical._base_methods import _CanonicalMethods
from yd_producer.canonical._common import (  # noqa: F401
    _apcp_accumulation_type_from_metadata,
    _apcp_selector_metadata,
    _apcp_step_range_from_metadata,
    _cfgrib_backend_kwargs,
    _coord_values_by_name,
    _first_cfgrib_alias,
    _grid_definition_signature,
    _json_time,
    _manifest_entries,
    _manifest_metadata,
    _manifest_value,
    _mapping_value,
    _normalize_longitude,
    _stable_identity,
    canonical_product_is_forcing_usable,
    canonical_readiness_source_is_supported,
    compute_time_axis,
    ensure_utc,
    format_cycle_time,
    map_variable,
    parse_cycle_time,
    required_standard_variables_for_source,
    unit_for_standard_variable,
)
from yd_producer.canonical._readiness import (  # noqa: F401
    _canonical_product_result_readiness_row,
    _canonical_readiness_error_message,
    _canonical_readiness_row,
    _readiness_rejected_row_sample,
    evaluate_canonical_readiness,
)
from yd_producer.canonical._types import (  # noqa: F401
    CFGRIB_VARIABLE_ALIASES,
    CONVERSION_PARAMS,
    ERA5_REQUIRED_STANDARD_VARIABLES,
    ERA5_STANDARD_UNITS,
    ERA5_VARIABLE_MAPPING,
    FORCING_USABLE_CANONICAL_QUALITY_FLAGS,
    GFS_F000_OPTIONAL_INTERVAL_STANDARD_VARIABLES,
    GFS_REQUIRED_STANDARD_VARIABLES,
    IFS_REQUIRED_STANDARD_VARIABLES,
    IFS_SHORTWAVE_NEGATIVE_TOLERANCE_W_M2,
    IFS_STANDARD_UNITS,
    IFS_VARIABLE_MAPPING,
    PRECIP_NEGATIVE_NOISE_TOLERANCE_MM,
    REQUIRED_STANDARD_VARIABLES_BY_SOURCE,
    STANDARD_UNITS,
    VARIABLE_MAPPING,
    CanonicalConversionError,
    CanonicalConverterConfig,
    CanonicalProductResult,
    CanonicalReadinessResult,
    ConversionResult,
    IFSCanonicalConverterConfig,
    MissingForecastVariable,
    RawRecord,
    UnitConversionResult,
)
from yd_producer.canonical._units import (  # noqa: F401
    _finite_float_tuple,
    _ifs_step_hours,
    _step_hours,
    _step_hours_from_step_range,
    _validate_finite_values,
    clamp,
    compute_ifs_relative_humidity,
    compute_ifs_relative_humidity_values,
    compute_relative_humidity,
    compute_relative_humidity_values,
    convert_era5_radiation_values,
    convert_ifs_precipitation_with_metadata,
    convert_ifs_radiation_values,
    convert_ifs_shortwave_down_values,
    convert_units,
    convert_units_with_metadata,
)


class CanonicalConverter(_CanonicalMethods):
    def __init__(
        self,
        *,
        config: CanonicalConverterConfig,
        object_store: LocalObjectStore | None = None,
    ) -> None:
        self.config = config
        self.object_store = object_store or LocalObjectStore(
            self.config.object_store_root,
            object_store_prefix=self.config.object_store_prefix,
        )

    def load_manifest(self, manifest_uri: str) -> dict[str, Any]:
        try:
            return json.loads(
                self.object_store.read_bytes_limited(
                    manifest_uri, max_bytes=MAX_OBJECT_MANIFEST_BYTES
                ).decode("utf-8")
            )
        except (json.JSONDecodeError, OSError, ObjectStoreError, ValueError) as error:
            raise CanonicalConversionError(
                f"Failed to load manifest {manifest_uri}: {error}"
            ) from error

    def _read_record_with_xarray(self, entry: Mapping[str, Any]) -> RawRecord:
        local_key = str(entry["local_key"])
        try:
            import xarray as xr
        except ImportError as error:
            raise CanonicalConversionError(
                f"Cannot parse raw file {local_key}; install xarray, cfgrib, and netCDF4."
            ) from error

        dataset = None
        cfgrib_error: Exception | None = None
        try:
            with _staged_contained_raw_path(
                self.object_store, local_key
            ) as staged_path:
                try:
                    expected_native_variable = str(entry["variable"])
                    backend_kwargs = _cfgrib_backend_kwargs(
                        entry, expected_native_variable
                    )
                    try:
                        dataset = xr.open_dataset(
                            staged_path,
                            engine="cfgrib",
                            backend_kwargs=backend_kwargs,
                        )
                    except Exception as _cfgrib_err:
                        cfgrib_error = _cfgrib_err
                        LOGGER.warning(
                            "Failed to parse raw file %s with cfgrib; falling back to netcdf4: %s",
                            local_key,
                            _cfgrib_err,
                        )
                        dataset = xr.open_dataset(staged_path, engine="netcdf4")
                    data_variable = self._select_data_variable(
                        dataset, expected_native_variable, local_key
                    )
                    data_array = dataset[data_variable]
                    values = np.asarray(data_array.values, dtype=np.float64).ravel()
                    return RawRecord(
                        source_file=self.object_store.uri_for_key(local_key),
                        native_variable=expected_native_variable,
                        forecast_hour=int(entry["forecast_hour"]),
                        values=values,
                        longitudes=_coord_values_by_name(dataset, ("lon", "longitude")),
                        latitudes=_coord_values_by_name(dataset, ("lat", "latitude")),
                        shape=tuple(
                            int(size)
                            for size in getattr(data_array.values, "shape", ())
                        ),
                        metadata=dict(_mapping_value(entry.get("metadata"))),
                    )
                finally:
                    if dataset is not None:
                        dataset.close()
        except CanonicalConversionError:
            raise
        except Exception as error:
            detail = f"Failed to parse raw file {local_key}: {error}"
            if cfgrib_error is not None:
                detail += f" (cfgrib also failed: {cfgrib_error})"
            raise CanonicalConversionError(detail) from error

    def _ensure_grid_definition(self, record: RawRecord) -> None:
        if not record.longitudes or not record.latitudes:
            return
        payload: dict[str, Any]
        if len(record.shape) == 2:
            y_count, x_count = record.shape
            if len(record.longitudes) != x_count or len(record.latitudes) != y_count:
                return
            payload = {
                "schema_version": "nhms.grid_definition.v1",
                "grid_id": self.config.grid_id,
                "layout": "rectilinear",
                "axis_order": ["latitude", "longitude"],
                "shape": [y_count, x_count],
                "longitudes": [
                    _normalize_longitude(longitude) for longitude in record.longitudes
                ],
                "latitudes": list(record.latitudes),
            }
        elif len(record.longitudes) == len(record.values) and len(
            record.latitudes
        ) == len(record.values):
            payload = {
                "schema_version": "nhms.grid_definition.v1",
                "grid_id": self.config.grid_id,
                "cells": [
                    {
                        "id": index,
                        "lon": _normalize_longitude(longitude),
                        "lat": latitude,
                    }
                    for index, (longitude, latitude) in enumerate(
                        zip(record.longitudes, record.latitudes, strict=True)
                    )
                ],
            }
        else:
            return
        try:
            if self.object_store.exists(self.config.grid_definition_uri):
                existing = json.loads(
                    self.object_store.read_bytes_limited(
                        self.config.grid_definition_uri,
                        max_bytes=MAX_OBJECT_MANIFEST_BYTES,
                    ).decode("utf-8")
                )
                if _grid_definition_signature(existing) != _grid_definition_signature(
                    payload
                ):
                    raise CanonicalConversionError(
                        f"Grid definition {self.config.grid_definition_uri} already exists with a different "
                        "longitude/latitude definition or cell order."
                    )
                return
            self.object_store.write_bytes_atomic(
                self.config.grid_definition_uri,
                json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
                    "utf-8"
                ),
            )
        except (OSError, ObjectStoreError, ValueError) as error:
            message = f"Failed to write grid definition {self.config.grid_definition_uri}: {error}"
            raise CanonicalConversionError(message) from error


class IFSCanonicalConverter(CanonicalConverter):
    def __init__(
        self,
        *,
        config: IFSCanonicalConverterConfig,
        object_store: LocalObjectStore | None = None,
    ) -> None:
        super().__init__(
            config=config,
            object_store=object_store,
        )

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

            entries_by_hour = self._entries_by_hour_and_variable(entries)
            forecast_hours = self._configured_forecast_hours(manifest, entries)
            manifest_metadata = _manifest_metadata(manifest)
            policy_identity = _mapping_value(manifest_metadata.get("source_policy"))
            source_object_identity = _mapping_value(
                manifest_metadata.get("source_object_identity")
            )
            products: list[CanonicalProductResult] = []
            previous_precipitation: RawRecord | None = None
            previous_ssr: RawRecord | None = None
            previous_str: RawRecord | None = None
            consecutive_negative_precipitation = 0

            for forecast_hour in forecast_hours:
                records = {
                    native_variable: self._read_record(entry)
                    for native_variable, entry in entries_by_hour[forecast_hour].items()
                }
                self._ensure_grid_definition_from_records(records.values())
                temperature = records["2t"]
                dewpoint = records["2d"]
                wind_u = records["10u"]
                wind_v = records["10v"]
                pressure = records["sp"]
                precipitation = records["tp"]
                ssr = records["ssr"]
                str_ = records["str"]

                temperature_c = convert_units("2t", temperature.values)
                dewpoint_c = convert_units("2d", dewpoint.values)
                products.append(
                    self._write_product(
                        source_id=source_id,
                        cycle_time=cycle_time,
                        standard_variable="air_temperature_2m",
                        forecast_hour=forecast_hour,
                        values=temperature_c,
                        unit=self._unit_for_standard_variable("air_temperature_2m"),
                        source_files=[temperature.source_file],
                        conversion_params={
                            "native_variable": temperature.native_variable,
                            "operation": "K_to_C",
                            "unit_conversion": "K_to_C",
                        },
                        policy_identity=policy_identity,
                        source_object_identity=source_object_identity,
                    )
                )
                products.append(
                    self._write_product(
                        source_id=source_id,
                        cycle_time=cycle_time,
                        standard_variable="relative_humidity_2m",
                        forecast_hour=forecast_hour,
                        values=compute_ifs_relative_humidity_values(
                            temperature_c, dewpoint_c
                        ),
                        unit=self._unit_for_standard_variable("relative_humidity_2m"),
                        source_files=[temperature.source_file, dewpoint.source_file],
                        conversion_params={
                            "native_variables": [
                                temperature.native_variable,
                                dewpoint.native_variable,
                            ],
                            "operation": "magnus_formula",
                            "derived_from": [
                                temperature.native_variable,
                                dewpoint.native_variable,
                            ],
                            "method": "magnus_formula",
                        },
                        lineage_updates={
                            "derived_from": [
                                temperature.native_variable,
                                dewpoint.native_variable,
                            ],
                            "method": "magnus_formula",
                        },
                        policy_identity=policy_identity,
                        source_object_identity=source_object_identity,
                    )
                )
                products.append(
                    self._write_product(
                        source_id=source_id,
                        cycle_time=cycle_time,
                        standard_variable="wind_u_10m",
                        forecast_hour=forecast_hour,
                        values=wind_u.values,
                        unit=self._unit_for_standard_variable("wind_u_10m"),
                        source_files=[wind_u.source_file],
                        conversion_params={
                            "native_variable": wind_u.native_variable,
                            "operation": "pass_through",
                        },
                        policy_identity=policy_identity,
                        source_object_identity=source_object_identity,
                    )
                )
                products.append(
                    self._write_product(
                        source_id=source_id,
                        cycle_time=cycle_time,
                        standard_variable="wind_v_10m",
                        forecast_hour=forecast_hour,
                        values=wind_v.values,
                        unit=self._unit_for_standard_variable("wind_v_10m"),
                        source_files=[wind_v.source_file],
                        conversion_params={
                            "native_variable": wind_v.native_variable,
                            "operation": "pass_through",
                        },
                        policy_identity=policy_identity,
                        source_object_identity=source_object_identity,
                    )
                )
                products.append(
                    self._write_product(
                        source_id=source_id,
                        cycle_time=cycle_time,
                        standard_variable="surface_pressure",
                        forecast_hour=forecast_hour,
                        values=pressure.values,
                        unit=self._unit_for_standard_variable("surface_pressure"),
                        source_files=[pressure.source_file],
                        conversion_params={
                            "native_variable": pressure.native_variable,
                            "operation": "pass_through",
                        },
                        policy_identity=policy_identity,
                        source_object_identity=source_object_identity,
                    )
                )

                (
                    precipitation_conversion,
                    consecutive_negative_precipitation,
                    precip_step_hours,
                ) = convert_ifs_precipitation_with_metadata(
                    precipitation.values,
                    previous_precipitation.values
                    if previous_precipitation is not None
                    else None,
                    forecast_hour=forecast_hour,
                    previous_forecast_hour=previous_precipitation.forecast_hour
                    if previous_precipitation is not None
                    else None,
                    consecutive_negative_count=consecutive_negative_precipitation,
                )
                if (
                    not precipitation_conversion.anomalies
                    or precipitation_conversion.quality_flag == "ok"
                ):
                    consecutive_negative_precipitation = 0
                precipitation_sources = [precipitation.source_file]
                if previous_precipitation is not None:
                    precipitation_sources.insert(0, previous_precipitation.source_file)
                precipitation_params: dict[str, Any] = {
                    "native_variable": precipitation.native_variable,
                    # mm/day, derived from the per-step accumulation rescaled by the
                    # actual step (24 / step_hours); step_hours kept for audit.
                    "operation": "cumulative_m_to_mm_day",
                    "accumulation_type": "since_cycle",
                    "unit_conversion": "m_to_mm_day",
                    "step_hours": precip_step_hours,
                }
                if precipitation_conversion.anomalies:
                    precipitation_params["anomalies"] = list(
                        precipitation_conversion.anomalies
                    )
                products.append(
                    self._write_product(
                        source_id=source_id,
                        cycle_time=cycle_time,
                        standard_variable="prcp_rate_or_amount",
                        forecast_hour=forecast_hour,
                        values=precipitation_conversion.values,
                        unit=self._unit_for_standard_variable("prcp_rate_or_amount"),
                        source_files=precipitation_sources,
                        conversion_params=precipitation_params,
                        quality_flag=precipitation_conversion.quality_flag,
                        policy_identity=policy_identity,
                        source_object_identity=source_object_identity,
                    )
                )

                radiation_values, radiation_step_hours = convert_ifs_radiation_values(
                    ssr.values,
                    str_.values,
                    previous_ssr.values if previous_ssr is not None else None,
                    previous_str.values if previous_str is not None else None,
                    forecast_hour=forecast_hour,
                    previous_forecast_hour=previous_ssr.forecast_hour
                    if previous_ssr is not None
                    else None,
                )
                radiation_sources = [ssr.source_file, str_.source_file]
                if previous_ssr is not None and previous_str is not None:
                    radiation_sources = [
                        previous_ssr.source_file,
                        previous_str.source_file,
                        ssr.source_file,
                        str_.source_file,
                    ]
                shortwave_conversion, shortwave_step_hours = (
                    convert_ifs_shortwave_down_values(
                        ssr.values,
                        previous_ssr.values if previous_ssr is not None else None,
                        forecast_hour=forecast_hour,
                        previous_forecast_hour=previous_ssr.forecast_hour
                        if previous_ssr is not None
                        else None,
                    )
                )
                shortwave_sources = [ssr.source_file]
                if previous_ssr is not None:
                    shortwave_sources.insert(0, previous_ssr.source_file)
                shortwave_params: dict[str, Any] = {
                    "native_variable": ssr.native_variable,
                    "operation": "cumulative_j_m2_to_w_m2_downward_shortwave",
                    "accumulation_type": "since_cycle",
                    "step_hours": shortwave_step_hours,
                }
                if shortwave_conversion.anomalies:
                    shortwave_params["anomalies"] = list(shortwave_conversion.anomalies)
                products.append(
                    self._write_product(
                        source_id=source_id,
                        cycle_time=cycle_time,
                        standard_variable="shortwave_down",
                        forecast_hour=forecast_hour,
                        values=shortwave_conversion.values,
                        unit=self._unit_for_standard_variable("shortwave_down"),
                        source_files=shortwave_sources,
                        conversion_params=shortwave_params,
                        quality_flag=shortwave_conversion.quality_flag,
                        policy_identity=policy_identity,
                        source_object_identity=source_object_identity,
                    )
                )
                products.append(
                    self._write_product(
                        source_id=source_id,
                        cycle_time=cycle_time,
                        standard_variable="net_radiation",
                        forecast_hour=forecast_hour,
                        values=radiation_values,
                        unit=self._unit_for_standard_variable("net_radiation"),
                        source_files=radiation_sources,
                        conversion_params={
                            "native_variables": [
                                ssr.native_variable,
                                str_.native_variable,
                            ],
                            "operation": "cumulative_j_m2_to_w_m2_direct_net",
                            "accumulation_type": "since_cycle",
                            "radiation_method": "direct_net",
                            "components": [ssr.native_variable, str_.native_variable],
                            "step_hours": radiation_step_hours,
                        },
                        lineage_updates={
                            "radiation_method": "direct_net",
                            "components": [ssr.native_variable, str_.native_variable],
                        },
                        policy_identity=policy_identity,
                        source_object_identity=source_object_identity,
                    )
                )

                previous_precipitation = precipitation
                previous_ssr = ssr
                previous_str = str_

            return self._complete_cycle_after_conversion(
                source_id=source_id,
                cycle_time=cycle_time,
                products=products,
                forecast_hours=forecast_hours,
                policy_identity=policy_identity,
                source_object_identity=source_object_identity,
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

    def _records_by_hour_and_variable(
        self, records: list[RawRecord]
    ) -> dict[int, dict[str, RawRecord]]:
        grouped: dict[int, dict[str, RawRecord]] = {}
        for record in records:
            grouped.setdefault(record.forecast_hour, {})[record.native_variable] = (
                record
            )
        return grouped

    def _missing_required_pairs(
        self,
        manifest: Any,
        entries: list[dict[str, Any]],
        records: list[RawRecord],
    ) -> tuple[MissingForecastVariable, ...]:
        pairs = list(super()._missing_required_pairs(manifest, entries, records))
        return self._add_shortwave_missing_pairs(pairs)

    def _missing_required_pairs_from_covered(
        self,
        manifest: Any,
        entries: list[dict[str, Any]],
        covered: set[tuple[str, int]],
    ) -> tuple[MissingForecastVariable, ...]:
        pairs = list(
            super()._missing_required_pairs_from_covered(manifest, entries, covered)
        )
        seen_pairs = {
            (pair.native_variable, pair.standard_variable, pair.forecast_hour)
            for pair in pairs
        }
        for forecast_hour in self._configured_forecast_hours(manifest, entries):
            for native_variable in ("ssr", "str"):
                if (native_variable, forecast_hour) in covered:
                    continue
                pair_key = (native_variable, "net_radiation", forecast_hour)
                if pair_key in seen_pairs:
                    continue
                pairs.append(
                    MissingForecastVariable(
                        native_variable=native_variable,
                        standard_variable="net_radiation",
                        forecast_hour=forecast_hour,
                    )
                )
                seen_pairs.add(pair_key)
        return self._add_shortwave_missing_pairs(pairs)

    def _add_shortwave_missing_pairs(
        self,
        pairs: list[MissingForecastVariable],
    ) -> tuple[MissingForecastVariable, ...]:
        shortwave_pairs = [
            MissingForecastVariable(
                native_variable=pair.native_variable,
                standard_variable="shortwave_down",
                forecast_hour=pair.forecast_hour,
            )
            for pair in pairs
            if pair.native_variable == "ssr"
        ]
        return tuple([*pairs, *shortwave_pairs])

    def _ensure_grid_definition_from_records(
        self, records: Iterable[RawRecord]
    ) -> None:
        for record in records:
            if record.longitudes and record.latitudes:
                self._ensure_grid_definition(record)

    def _write_product(
        self,
        *,
        source_id: str,
        cycle_time: datetime,
        standard_variable: str,
        forecast_hour: int,
        values: tuple[float, ...],
        unit: str,
        source_files: list[str],
        conversion_params: Mapping[str, Any],
        quality_flag: str = "ok",
        lineage_updates: Mapping[str, Any] | None = None,
        policy_identity: Mapping[str, Any] | None = None,
        source_object_identity: Mapping[str, Any] | None = None,
    ) -> CanonicalProductResult:
        valid_time = cycle_time + timedelta(hours=forecast_hour)
        compact_cycle = format_cycle_time(cycle_time)
        canonical_product_id = (
            f"{source_id}_{compact_cycle}_{standard_variable}_f{forecast_hour:03d}"
        )
        object_key = f"canonical/{source_id}/{compact_cycle}/{standard_variable}/{canonical_product_id}.nc"
        lineage_json: dict[str, Any] = {
            "source_files": source_files,
            "source_cycle_id": f"{source_id}_{compact_cycle}",
            "conversion_params": dict(conversion_params),
            "converter_version": self.config.converter_version,
        }
        if lineage_updates:
            lineage_json.update(lineage_updates)
        if policy_identity:
            lineage_json["policy_identity"] = dict(policy_identity)
        if source_object_identity:
            lineage_json["source_object_identity"] = dict(source_object_identity)
        content = self._serialize_product(
            variable=standard_variable,
            values=values,
            cycle_time=cycle_time,
            valid_time=valid_time,
            lead_time_hours=forecast_hour,
            unit=unit,
            lineage_json=lineage_json,
        )
        checksum = sha256_bytes(content)

        existing = self._get_existing_product(canonical_product_id)
        if self._existing_product_is_current(existing, object_key, checksum):
            return CanonicalProductResult(
                canonical_product_id=canonical_product_id,
                variable=standard_variable,
                valid_time=valid_time,
                lead_time_hours=forecast_hour,
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

        self._upsert_product(
            {
                "canonical_product_id": canonical_product_id,
                "source_id": source_id,
                "source_version": compact_cycle,
                "cycle_time": cycle_time,
                "valid_time": valid_time,
                "lead_time_hours": forecast_hour,
                "variable": standard_variable,
                "unit": unit,
                "grid_id": self.config.grid_id,
                "grid_definition_uri": self.config.grid_definition_uri,
                "native_time_resolution": self.config.native_time_resolution,
                "native_spatial_resolution": self.config.native_spatial_resolution,
                "object_uri": object_uri,
                "checksum": checksum,
                "quality_flag": quality_flag,
                "lineage_json": lineage_json,
            }
        )
        return CanonicalProductResult(
            canonical_product_id=canonical_product_id,
            variable=standard_variable,
            valid_time=valid_time,
            lead_time_hours=forecast_hour,
            object_uri=object_uri,
            checksum=checksum,
            status="updated" if existing else "created",
            quality_flag=quality_flag,
            lineage_json=lineage_json,
        )

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
                    "unit": self._unit_for_standard_variable(pair.standard_variable),
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

    def _unit_for_standard_variable(self, standard_variable: str) -> str:
        try:
            return IFS_STANDARD_UNITS[standard_variable]
        except KeyError as error:
            raise CanonicalConversionError(
                f"No IFS standard unit configured for {standard_variable}"
            ) from error


_RAW_STAGING_CHUNK = 1024 * 1024


def _raw_input_size_error(
    local_key: str, observed_size: int
) -> CanonicalConversionError:
    return CanonicalConversionError(
        f"Raw object {local_key} exceeds input size: observed {observed_size} bytes, "
        f"limit {MAX_RAW_INPUT_BYTES} bytes"
    )


@contextmanager
def _staged_contained_raw_path(
    object_store: LocalObjectStore, local_key: str
) -> Iterator[str]:
    try:
        observed_size = object_store.size(local_key)
    except (OSError, ObjectStoreError, ValueError, SafeFilesystemError) as error:
        raise CanonicalConversionError(
            f"Raw object {local_key} is not a contained regular file: {error}"
        ) from error
    if observed_size > MAX_RAW_INPUT_BYTES:
        raise _raw_input_size_error(local_key, observed_size)

    suffix = Path(local_key).suffix or ".raw"
    with tempfile.NamedTemporaryFile(
        prefix="yd-canonical-raw-",
        suffix=suffix,
    ) as staging:
        observed = 0
        try:
            for chunk in object_store.iter_bytes(
                local_key, chunk_size=_RAW_STAGING_CHUNK
            ):
                observed += len(chunk)
                if observed > MAX_RAW_INPUT_BYTES:
                    raise _raw_input_size_error(local_key, observed)
                staging.write(chunk)
        except CanonicalConversionError:
            raise
        except (OSError, ObjectStoreError, ValueError, SafeFilesystemError) as error:
            raise CanonicalConversionError(
                f"Raw object {local_key} is not a contained regular file: {error}"
            ) from error
        staging.flush()
        yield staging.name


del _CanonicalMethods
