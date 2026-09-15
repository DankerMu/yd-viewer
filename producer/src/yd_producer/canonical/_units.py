# NWM@8ae9b8f2 workers/canonical_converter/converter.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

import numpy as np

from yd_producer.canonical._types import (
    IFS_SHORTWAVE_NEGATIVE_TOLERANCE_W_M2,
    PRECIP_NEGATIVE_NOISE_TOLERANCE_MM,
    CanonicalConversionError,
    UnitConversionResult,
)


def convert_units(
    native_variable: str,
    values: tuple[float, ...] | list[float] | np.ndarray,
    previous_values: tuple[float, ...] | list[float] | np.ndarray | None = None,
) -> tuple[float, ...]:
    return convert_units_with_metadata(native_variable, values, previous_values).values


def convert_units_with_metadata(
    native_variable: str,
    values: tuple[float, ...] | list[float] | np.ndarray,
    previous_values: tuple[float, ...] | list[float] | np.ndarray | None = None,
    *,
    forecast_hour: int | None = None,
    previous_forecast_hour: int | None = None,
    accumulation_type: str | None = None,
    step_range: str | None = None,
) -> UnitConversionResult:
    current = tuple(float(value) for value in values)
    if native_variable in {
        "tmp2m",
        "2m_temperature",
        "2m_dewpoint_temperature",
        "2t",
        "2d",
    }:
        return UnitConversionResult(tuple(value - 273.15 for value in current))
    if native_variable == "apcp":
        if accumulation_type == "interval_bucket":
            _validate_finite_values(current, "APCP precipitation")
            step_hours = _step_hours_from_step_range(step_range)
            negative_values = tuple(value for value in current if value < 0.0)
            anomalies_list: list[dict[str, Any]] = []
            quality_flag = "ok"
            if negative_values:
                _tol = PRECIP_NEGATIVE_NOISE_TOLERANCE_MM
                small_negatives = tuple(
                    value for value in negative_values if -_tol < value < 0.0
                )
                significant_negatives = tuple(
                    value for value in negative_values if value <= -_tol
                )
                if small_negatives:
                    anomalies_list.append(
                        {
                            "type": "small_negative_apcp_bucket",
                            "forecast_hour": forecast_hour,
                            "step_range": step_range,
                            "negative_count": len(small_negatives),
                            "min_delta": min(small_negatives),
                        }
                    )
                if significant_negatives:
                    quality_flag = "warn"
                    anomalies_list.append(
                        {
                            "type": "negative_apcp_bucket",
                            "forecast_hour": forecast_hour,
                            "step_range": step_range,
                            "negative_count": len(significant_negatives),
                            "min_delta": min(significant_negatives),
                        }
                    )
            mm_per_day = tuple(max(0.0, value) * 24.0 / step_hours for value in current)
            return UnitConversionResult(mm_per_day, quality_flag, tuple(anomalies_list))

        previous = (
            tuple(float(value) for value in previous_values)
            if previous_values is not None
            else (0.0,) * len(current)
        )
        if len(previous) != len(current):
            raise CanonicalConversionError(
                "APCP previous/current value arrays must have the same length."
            )
        _validate_finite_values((*current, *previous), "APCP precipitation")
        # The GFS adapter resolves FV3-GFS duplicate APCP records to the 0-fhr
        # cumulative-since-cycle record. Canonical precipitation is therefore a
        # straight de-accumulation against the previous lead.
        deltas = tuple(
            current_value - previous_value
            for current_value, previous_value in zip(current, previous)
        )
        # 量化噪声级微小负 delta(|δ|<容差)按 SHUD precip<0.0001mm/day→0 的钳零+量化约定
        # 与 0 等价,记 anomaly 但保持 quality_flag=ok(对齐 IFS 降水 small/significant 处理);
        # 显著负值才标 warn。容差用 PRECIP_NEGATIVE_NOISE_TOLERANCE_MM 以覆盖 GRIB 量化步长。
        _tol = PRECIP_NEGATIVE_NOISE_TOLERANCE_MM
        small_negatives = tuple(delta for delta in deltas if -_tol < delta < 0.0)
        significant_negatives = tuple(delta for delta in deltas if delta <= -_tol)
        anomalies_list: list[dict[str, Any]] = []
        quality_flag = "ok"
        if small_negatives:
            anomalies_list.append(
                {
                    "type": "small_negative_apcp_delta",
                    "forecast_hour": forecast_hour,
                    "previous_forecast_hour": previous_forecast_hour,
                    "negative_count": len(small_negatives),
                    "min_delta": min(small_negatives),
                }
            )
        if significant_negatives:
            quality_flag = "warn"
            anomalies_list.append(
                {
                    "type": "negative_apcp_delta",
                    "forecast_hour": forecast_hour,
                    "previous_forecast_hour": previous_forecast_hour,
                    "negative_count": len(significant_negatives),
                    "min_delta": min(significant_negatives),
                }
            )
        anomalies: tuple[dict[str, Any], ...] = tuple(anomalies_list)
        # On the first frame (previous=None) the smallest forecast hour may be >0
        # when GFS_FORECAST_START_HOUR is configured; use the full 0->fh span rather
        # than the shared _step_hours default of 1.0.
        if previous_forecast_hour is None and forecast_hour and forecast_hour > 0:
            step_hours = float(forecast_hour)
        else:
            step_hours = _step_hours(forecast_hour, previous_forecast_hour)
        mm_per_day = tuple(max(0.0, delta) * 24.0 / step_hours for delta in deltas)
        return UnitConversionResult(mm_per_day, quality_flag, anomalies)
    if native_variable == "rh2m":
        # canonical 单位为分数 0-1;GRIB rh2m 常含过饱和 >100%,按 SHUD 模型(rh 钳 [0,1])
        # 与 IFS RH 路径一致钳到 [0,1],避免 canonical 产品越界声明单位。
        return UnitConversionResult(
            tuple(clamp(value / 100.0, 0.0, 1.0) for value in current)
        )
    return UnitConversionResult(current)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def compute_relative_humidity(temperature_c: float, dewpoint_c: float) -> float:
    e_s = 6.112 * math.exp(17.67 * temperature_c / (temperature_c + 243.5))
    e_d = 6.112 * math.exp(17.67 * dewpoint_c / (dewpoint_c + 243.5))
    return clamp(e_d / e_s, 0.0, 1.0)


def compute_relative_humidity_values(
    temperature_c: tuple[float, ...] | list[float] | np.ndarray,
    dewpoint_c: tuple[float, ...] | list[float] | np.ndarray,
) -> tuple[float, ...]:
    if len(temperature_c) != len(dewpoint_c):
        raise CanonicalConversionError(
            "Temperature and dewpoint arrays must have the same length."
        )
    return tuple(
        compute_relative_humidity(float(t), float(td))
        for t, td in zip(temperature_c, dewpoint_c)
    )


def convert_era5_radiation_values(
    ssr_values: tuple[float, ...] | list[float] | np.ndarray,
    str_values: tuple[float, ...] | list[float] | np.ndarray,
    previous_ssr_values: tuple[float, ...] | list[float] | np.ndarray | None = None,
    previous_str_values: tuple[float, ...] | list[float] | np.ndarray | None = None,
    *,
    forecast_hour: int | None = None,
    previous_forecast_hour: int | None = None,
) -> tuple[float, ...]:
    ssr = tuple(float(value) for value in ssr_values)
    str_ = tuple(float(value) for value in str_values)
    previous_ssr = (
        tuple(float(value) for value in previous_ssr_values)
        if previous_ssr_values is not None
        else (0.0,) * len(ssr)
    )
    previous_str = (
        tuple(float(value) for value in previous_str_values)
        if previous_str_values is not None
        else (0.0,) * len(str_)
    )
    lengths = {len(ssr), len(str_), len(previous_ssr), len(previous_str)}
    if len(lengths) != 1:
        raise CanonicalConversionError(
            "ERA5 radiation arrays must have the same length."
        )
    _validate_finite_values(
        (*ssr, *str_, *previous_ssr, *previous_str), "ERA5 radiation"
    )

    step_seconds = _step_hours(forecast_hour, previous_forecast_hour) * 3600.0
    return tuple(
        ((current_ssr - prior_ssr) + (current_str - prior_str)) / step_seconds
        for current_ssr, prior_ssr, current_str, prior_str in zip(
            ssr, previous_ssr, str_, previous_str
        )
    )


def compute_ifs_relative_humidity(temperature_c: float, dewpoint_c: float) -> float:
    e_s = math.exp(17.625 * temperature_c / (243.04 + temperature_c))
    e_d = math.exp(17.625 * dewpoint_c / (243.04 + dewpoint_c))
    return clamp(e_d / e_s, 0.0, 1.0)


def compute_ifs_relative_humidity_values(
    temperature_c: tuple[float, ...] | list[float] | np.ndarray,
    dewpoint_c: tuple[float, ...] | list[float] | np.ndarray,
) -> tuple[float, ...]:
    if len(temperature_c) != len(dewpoint_c):
        raise CanonicalConversionError(
            "IFS temperature and dewpoint arrays must have the same length."
        )
    return tuple(
        compute_ifs_relative_humidity(float(t), float(td))
        for t, td in zip(temperature_c, dewpoint_c)
    )


def convert_ifs_precipitation_with_metadata(
    values_m: tuple[float, ...] | list[float] | np.ndarray,
    previous_values_m: tuple[float, ...] | list[float] | np.ndarray | None = None,
    *,
    forecast_hour: int | None = None,
    previous_forecast_hour: int | None = None,
    consecutive_negative_count: int = 0,
) -> tuple[UnitConversionResult, int, float]:
    current = tuple(float(value) for value in values_m)
    previous = (
        tuple(float(value) for value in previous_values_m)
        if previous_values_m is not None
        else (0.0,) * len(current)
    )
    if len(previous) != len(current):
        raise CanonicalConversionError(
            "IFS precipitation previous/current arrays must have the same length."
        )
    _validate_finite_values((*current, *previous), "IFS precipitation")

    step_hours = _ifs_step_hours(forecast_hour, previous_forecast_hour)
    deltas_mm = tuple(
        (current_value - previous_value) * 1000.0
        for current_value, previous_value in zip(current, previous)
    )
    # 容差用 PRECIP_NEGATIVE_NOISE_TOLERANCE_MM 覆盖 IFS tp 的 GRIB 量化步长(≈0.0153mm),
    # 否则量化噪声被判 significant→warning_negative_precip→被 forcing 当不可用剔除致缺产品。
    _tol = PRECIP_NEGATIVE_NOISE_TOLERANCE_MM
    small_negatives = tuple(delta for delta in deltas_mm if -_tol < delta < 0.0)
    significant_negatives = tuple(delta for delta in deltas_mm if delta <= -_tol)
    anomalies: list[dict[str, Any]] = []
    next_consecutive_negative_count = 0
    quality_flag = "ok"

    if small_negatives:
        anomalies.append(
            {
                "type": "small_negative_ifs_precipitation_delta",
                "forecast_hour": forecast_hour,
                "previous_forecast_hour": previous_forecast_hour,
                "negative_count": len(small_negatives),
                "min_delta_mm": min(small_negatives),
            }
        )
    if significant_negatives:
        next_consecutive_negative_count = consecutive_negative_count + 1
        quality_flag = "warning_negative_precip"
        if next_consecutive_negative_count >= 3:
            quality_flag = "error_precip_accumulation"
        anomalies.append(
            {
                "type": "negative_ifs_precipitation_delta",
                "forecast_hour": forecast_hour,
                "previous_forecast_hour": previous_forecast_hour,
                "negative_count": len(significant_negatives),
                "min_delta_mm": min(significant_negatives),
                "consecutive_negative_count": next_consecutive_negative_count,
            }
        )

    values = tuple(max(0.0, delta) * 24.0 / step_hours for delta in deltas_mm)
    return (
        UnitConversionResult(values, quality_flag, tuple(anomalies)),
        next_consecutive_negative_count,
        step_hours,
    )


def convert_ifs_radiation_values(
    ssr_values: tuple[float, ...] | list[float] | np.ndarray,
    str_values: tuple[float, ...] | list[float] | np.ndarray,
    previous_ssr_values: tuple[float, ...] | list[float] | np.ndarray | None = None,
    previous_str_values: tuple[float, ...] | list[float] | np.ndarray | None = None,
    *,
    forecast_hour: int | None = None,
    previous_forecast_hour: int | None = None,
) -> tuple[tuple[float, ...], float]:
    ssr = _finite_float_tuple(ssr_values, "IFS ssr")
    str_ = _finite_float_tuple(str_values, "IFS str")
    previous_ssr = (
        _finite_float_tuple(previous_ssr_values, "previous IFS ssr")
        if previous_ssr_values is not None
        else (0.0,) * len(ssr)
    )
    previous_str = (
        _finite_float_tuple(previous_str_values, "previous IFS str")
        if previous_str_values is not None
        else (0.0,) * len(str_)
    )
    lengths = {len(ssr), len(str_), len(previous_ssr), len(previous_str)}
    if len(lengths) != 1:
        raise CanonicalConversionError(
            "IFS radiation arrays must have the same length."
        )

    step_hours = _ifs_step_hours(forecast_hour, previous_forecast_hour)
    step_seconds = step_hours * 3600.0
    values: list[float] = []
    for current_ssr, prior_ssr, current_str, prior_str in zip(
        ssr, previous_ssr, str_, previous_str
    ):
        ssr_delta = current_ssr - prior_ssr
        str_delta = current_str - prior_str
        if not math.isfinite(ssr_delta) or not math.isfinite(str_delta):
            raise CanonicalConversionError("IFS radiation deltas must be finite.")
        values.append((ssr_delta + str_delta) / step_seconds)
    return tuple(values), step_hours


def convert_ifs_shortwave_down_values(
    ssr_values: tuple[float, ...] | list[float] | np.ndarray,
    previous_ssr_values: tuple[float, ...] | list[float] | np.ndarray | None = None,
    *,
    forecast_hour: int | None = None,
    previous_forecast_hour: int | None = None,
) -> tuple[UnitConversionResult, float]:
    ssr = _finite_float_tuple(ssr_values, "IFS ssr")
    previous_ssr = (
        _finite_float_tuple(previous_ssr_values, "previous IFS ssr")
        if previous_ssr_values is not None
        else (0.0,) * len(ssr)
    )
    if len(ssr) != len(previous_ssr):
        raise CanonicalConversionError(
            "IFS shortwave radiation arrays must have the same length."
        )

    step_hours = _ifs_step_hours(forecast_hour, previous_forecast_hour)
    step_seconds = step_hours * 3600.0
    values: list[float] = []
    negative_deltas: list[float] = []
    small_negative_deltas: list[float] = []
    for current_ssr, prior_ssr in zip(ssr, previous_ssr):
        delta = current_ssr - prior_ssr
        if not math.isfinite(delta):
            raise CanonicalConversionError(
                "IFS shortwave radiation deltas must be finite."
            )
        rate = delta / step_seconds
        # 只有负速率超过量化噪声容差才算真异常(标 warn);夜间持平段的亚阈值伪负值
        # 记 anomaly 但保持 quality_flag=ok(对齐 IFS 降水 small/significant 处理)。
        if rate < -IFS_SHORTWAVE_NEGATIVE_TOLERANCE_W_M2:
            negative_deltas.append(delta)
        elif delta < 0.0:
            small_negative_deltas.append(delta)
        values.append(max(0.0, rate))
    anomalies_list: list[dict[str, Any]] = []
    quality_flag = "ok"
    if small_negative_deltas:
        anomalies_list.append(
            {
                "type": "small_negative_ifs_shortwave_delta",
                "forecast_hour": forecast_hour,
                "previous_forecast_hour": previous_forecast_hour,
                "negative_count": len(small_negative_deltas),
                "min_delta_j_m2": min(small_negative_deltas),
            }
        )
    if negative_deltas:
        quality_flag = "warn"
        anomalies_list.append(
            {
                "type": "negative_ifs_shortwave_delta",
                "forecast_hour": forecast_hour,
                "previous_forecast_hour": previous_forecast_hour,
                "negative_count": len(negative_deltas),
                "min_delta_j_m2": min(negative_deltas),
            }
        )
    return UnitConversionResult(
        tuple(values), quality_flag, tuple(anomalies_list)
    ), step_hours


def _finite_float_tuple(values: Iterable[float], label: str) -> tuple[float, ...]:
    parsed = tuple(float(value) for value in values)
    _validate_finite_values(parsed, label)
    return parsed


def _validate_finite_values(values: Iterable[float], label: str) -> None:
    if not all(math.isfinite(value) for value in values):
        raise CanonicalConversionError(f"{label} values must be finite.")


def _step_hours(forecast_hour: int | None, previous_forecast_hour: int | None) -> float:
    if forecast_hour is None or previous_forecast_hour is None:
        return 1.0
    return float(max(1, forecast_hour - previous_forecast_hour))


def _step_hours_from_step_range(step_range: str | None) -> float:
    if not step_range:
        raise CanonicalConversionError(
            "APCP interval bucket conversion requires step_range metadata."
        )
    start_text, separator, end_text = step_range.partition("-")
    if not separator:
        raise CanonicalConversionError(
            f"Invalid APCP step_range metadata: {step_range!r}."
        )
    try:
        start = int(start_text)
        end = int(end_text)
    except ValueError as error:
        raise CanonicalConversionError(
            f"Invalid APCP step_range metadata: {step_range!r}."
        ) from error
    if end <= start:
        raise CanonicalConversionError(
            f"Invalid APCP step_range metadata: {step_range!r}."
        )
    return float(end - start)


def _ifs_step_hours(
    forecast_hour: int | None, previous_forecast_hour: int | None
) -> float:
    if forecast_hour is None:
        return 3.0
    if previous_forecast_hour is None:
        return float(forecast_hour) if forecast_hour > 0 else 3.0
    return float(max(1, forecast_hour - previous_forecast_hour))
