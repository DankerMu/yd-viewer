# NWM@8ae9b8f2 workers/canonical_converter/converter.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

# IFS 累积净太阳辐射夜间持平,GRIB 位打包让持平段抖动几百 J/m²(≈0.1 W/m²),
# 去累积后产生伪负 delta。SHUD 模型自身(NetcdfForcingProvider.cpp)对净辐射 Rn 即
# "rn<0 → 0" 再 nearbyint 取整到整数 W/m²,故亚 W/m² 的负值经模型读入后与 0 逐位等价,
# 并非数据降级。低于此速率阈值的负值按模型约定 clamp 到 0,不标 quality_flag=warn,
# 以免夜间 shortwave 产品被 forcing 当不可用剔除;超阈值的负值仍标 warn 供诊断。
IFS_SHORTWAVE_NEGATIVE_TOLERANCE_W_M2 = 1.0

# 累积降水去累积的负 delta 噪声容差(mm/步)。IFS tp(米,GRIB 16-bit 打包)的量化步长
# ≈1000×2⁻¹⁶=0.0153mm,会让晚时次(累积量大)出现伪负 delta;原 0.01mm 阈值比量化步长还
# 小,把量化噪声误判为 warning_negative_precip → 被 forcing(只收 quality_flag==ok)剔除致缺产品。
# SHUD 模型自身对 precip<0 即钳 0 并量化到 4 位,故亚阈值负值与 0 等价。默认 0.1mm 覆盖
# IFS tp 量化步长和 GFS APCP 常见 1/16mm 量化噪声;超阈值的负值仍标 warn 供诊断。
PRECIP_NEGATIVE_NOISE_TOLERANCE_MM = 0.1

VARIABLE_MAPPING: dict[str, str] = {
    "tmp2m": "air_temperature_2m",
    "apcp": "prcp_rate_or_amount",
    "rh2m": "relative_humidity_2m",
    "u10m": "wind_u_10m",
    "v10m": "wind_v_10m",
    "pressfc": "pressure_surface",
    "dswrf": "shortwave_down",
}
ERA5_VARIABLE_MAPPING: dict[str, str] = {
    "2m_temperature": "air_temperature_2m",
    "2m_dewpoint_temperature": "relative_humidity_2m",
    "10m_u_component_of_wind": "wind_u_10m",
    "10m_v_component_of_wind": "wind_v_10m",
    "surface_pressure": "pressure_surface",
    "total_precipitation": "prcp_rate_or_amount",
    "surface_net_solar_radiation": "net_radiation",
    "surface_net_thermal_radiation": "net_radiation",
}
IFS_VARIABLE_MAPPING: dict[str, str] = {
    "2t": "air_temperature_2m",
    "2d": "relative_humidity_2m",
    "tp": "prcp_rate_or_amount",
    "10u": "wind_u_10m",
    "10v": "wind_v_10m",
    "sp": "surface_pressure",
    "ssr": "net_radiation",
    "str": "net_radiation",
}
STANDARD_UNITS: dict[str, str] = {
    "air_temperature_2m": "degC",
    "prcp_rate_or_amount": "mm/day",
    "relative_humidity_2m": "0-1",
    "wind_u_10m": "m/s",
    "wind_v_10m": "m/s",
    "wind_speed": "m/s",
    "pressure_surface": "Pa",
    "shortwave_down": "W/m2",
    "net_radiation": "W/m2",
}
ERA5_STANDARD_UNITS: dict[str, str] = {
    **STANDARD_UNITS,
    "prcp_rate_or_amount": "mm/day",
}
IFS_STANDARD_UNITS: dict[str, str] = {
    **STANDARD_UNITS,
    "surface_pressure": "Pa",
    "prcp_rate_or_amount": "mm/day",
}
GFS_REQUIRED_STANDARD_VARIABLES: tuple[str, ...] = (
    "prcp_rate_or_amount",
    "air_temperature_2m",
    "relative_humidity_2m",
    "wind_u_10m",
    "wind_v_10m",
    "pressure_surface",
    "shortwave_down",
)
GFS_F000_OPTIONAL_INTERVAL_STANDARD_VARIABLES: frozenset[str] = frozenset(
    {"prcp_rate_or_amount", "shortwave_down"}
)
IFS_REQUIRED_STANDARD_VARIABLES: tuple[str, ...] = (
    "prcp_rate_or_amount",
    "air_temperature_2m",
    "relative_humidity_2m",
    "wind_u_10m",
    "wind_v_10m",
    "surface_pressure",
    "shortwave_down",
)
ERA5_REQUIRED_STANDARD_VARIABLES: tuple[str, ...] = (
    "prcp_rate_or_amount",
    "air_temperature_2m",
    "relative_humidity_2m",
    "wind_u_10m",
    "wind_v_10m",
    "pressure_surface",
    "net_radiation",
)
REQUIRED_STANDARD_VARIABLES_BY_SOURCE: dict[str, tuple[str, ...]] = {
    "gfs": GFS_REQUIRED_STANDARD_VARIABLES,
    "ERA5": ERA5_REQUIRED_STANDARD_VARIABLES,
    "IFS": IFS_REQUIRED_STANDARD_VARIABLES,
    # 偏离（fixture 裁决 5 未覆盖）：yd 的 normalize_source_id 把 "IFS" 归一成 "ifs"，
    # 上面逐字承接 pin 的 "IFS"/"ERA5" 两键在 yd 侧同为不可达；IFS 是 yd 的在用源，
    # 故按纯新增补上归一化后的键，pin 原有键原样保留（清单裁决 1）。
    "ifs": IFS_REQUIRED_STANDARD_VARIABLES,
}
CONVERSION_PARAMS: dict[str, str] = {
    "tmp2m": "K_to_C",
    "apcp": "cumulative_to_mm_day",
    "rh2m": "pct_to_frac",
    "u10m": "pass_through",
    "v10m": "pass_through",
    "pressfc": "pass_through",
    "dswrf": "pass_through",
    "2m_temperature": "K_to_C",
    "2m_dewpoint_temperature": "dewpoint_magnus_rh",
    "10m_u_component_of_wind": "pass_through",
    "10m_v_component_of_wind": "pass_through",
    "surface_pressure": "pass_through",
    "total_precipitation": "cumulative_m_to_mm_day",
    "surface_net_solar_radiation": "cumulative_j_m2_to_w_m2",
    "surface_net_thermal_radiation": "cumulative_j_m2_to_w_m2",
    "2t": "K_to_C",
    "2d": "dewpoint_magnus_rh",
    "tp": "cumulative_m_to_mm_day",  # IFS tp emits mm/day; this alias entry is unreachable for IFS
    "10u": "pass_through",
    "10v": "pass_through",
    "sp": "pass_through",
    "ssr": "cumulative_j_m2_to_w_m2",
    "str": "cumulative_j_m2_to_w_m2",
}
CFGRIB_VARIABLE_ALIASES: dict[str, tuple[str, ...]] = {
    "tmp2m": ("tmp2m", "t2m", "2t"),
    "apcp": ("apcp", "tp", "total_precipitation"),
    "rh2m": ("rh2m", "r2", "2r"),
    "u10m": ("u10m", "u10", "10u"),
    "v10m": ("v10m", "v10", "10v"),
    "pressfc": ("pressfc", "sp", "pres"),
    "dswrf": ("dswrf", "ssrd", "sdswrf"),
    "2m_temperature": ("2m_temperature", "t2m", "2t"),
    "2m_dewpoint_temperature": ("2m_dewpoint_temperature", "d2m", "2d"),
    "10m_u_component_of_wind": ("10m_u_component_of_wind", "u10", "10u"),
    "10m_v_component_of_wind": ("10m_v_component_of_wind", "v10", "10v"),
    "surface_pressure": ("surface_pressure", "sp"),
    "total_precipitation": ("total_precipitation", "tp"),
    "surface_net_solar_radiation": ("surface_net_solar_radiation", "ssr"),
    "surface_net_thermal_radiation": ("surface_net_thermal_radiation", "str"),
    "2t": ("2t", "t2m"),
    "2d": ("2d", "d2m"),
    "10u": ("10u", "u10"),
    "10v": ("10v", "v10"),
    "tp": ("tp",),
    "sp": ("sp",),
    "ssr": ("ssr",),
    "str": ("str",),
}


class CanonicalConversionError(RuntimeError):
    """Raised when canonical conversion cannot complete for a cycle."""


@dataclass(frozen=True, kw_only=True)
class CanonicalConverterConfig:
    source_id: str = "gfs"
    workspace_root: Path | str
    object_store_root: Path | str
    object_store_prefix: str
    converter_version: str = "m1.4"
    grid_id: str = "gfs_0p25"
    grid_definition_uri: str = "canonical/gfs/grid/gfs_0p25/grid.json"
    native_time_resolution: str = "3h"
    native_spatial_resolution: str = "0.25deg"
    variable_mapping: Mapping[str, str] = field(
        default_factory=lambda: dict(VARIABLE_MAPPING)
    )
    cfgrib_variable_aliases: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(CFGRIB_VARIABLE_ALIASES)
    )


@dataclass(frozen=True)
class IFSCanonicalConverterConfig(CanonicalConverterConfig):
    source_id: str = "IFS"
    converter_version: str = "m4.1"
    grid_id: str = "ifs_0p25"
    grid_definition_uri: str = "canonical/ifs/grid/ifs_0p25/grid.json"
    native_time_resolution: str = "3h"
    native_spatial_resolution: str = "0.25deg"
    variable_mapping: Mapping[str, str] = field(
        default_factory=lambda: dict(IFS_VARIABLE_MAPPING)
    )
    cfgrib_variable_aliases: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(CFGRIB_VARIABLE_ALIASES)
    )


@dataclass(frozen=True)
class RawRecord:
    source_file: str
    native_variable: str
    forecast_hour: int
    values: tuple[float, ...] | np.ndarray
    longitudes: tuple[float, ...] = ()
    latitudes: tuple[float, ...] = ()
    shape: tuple[int, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MissingForecastVariable:
    native_variable: str
    standard_variable: str
    forecast_hour: int


@dataclass(frozen=True)
class UnitConversionResult:
    values: tuple[float, ...]
    quality_flag: str = "ok"
    anomalies: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class CanonicalProductResult:
    canonical_product_id: str
    variable: str
    valid_time: datetime
    lead_time_hours: int
    object_uri: str
    checksum: str
    status: str
    quality_flag: str = "ok"
    lineage_json: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConversionResult:
    status: str
    products: tuple[CanonicalProductResult, ...]


@dataclass(frozen=True)
class CanonicalReadinessResult:
    status: str
    ready: bool
    evidence: dict[str, Any]


FORCING_USABLE_CANONICAL_QUALITY_FLAGS = {"ok", "warn"}
