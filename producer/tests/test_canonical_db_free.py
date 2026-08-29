"""任务 7.1（issue #13）：canonical converter 的 DB-free 运行期闸门与真实 cfgrib 读路径。

本文件是 **yd 自撰**的，不是 NWM 快照：它没有溯源头部注释，也不进 `nwm-snapshot-inventory.md`
的 §1 路径表。快照用例逐字落在 `test_canonical_converter.py`，把自撰用例混进去会在
diff-vs-pin 里造出无法归类的差异段（tasks.md「Issue #13 fixture」裁决 2）。

这里钉三件快照用例钉不住的事：

1. **运行期无出站连接**（fixture 裁决 7 的判别力承重条）。静态 grep 对「运行期动态 import
   一个 DB 驱动」零判别力，故用 `socket.socket.connect` 闸门覆盖完整的 `convert_manifest`
   路径（读 raw → 转换 → 写 NetCDF → 写 catalog）。
2. **构造面无 repository**：`CanonicalRepository` 协议已不存在，两个转换器的构造签名里也没有
   `repository` 形参；`CanonicalConverterConfig` 的三个路径字段是无默认必填 kw-only（D4 零默认）。
3. **真实 cfgrib 解码**（fixture 裁决 8）。pin 的整套快照用例按设计走 NetCDF fallback
   （`netcdf_fixture.py` 的 docstring 逐字写着 "replaces mock_grib for test data generation"），
   没有一个让 cfgrib 解码过一个字节。这里用锁内 `eccodes` 造真 GRIB2，并断言 fallback 警告
   **未**出现——fallback 静默生效是本用例最可能的假绿形态。
"""

from __future__ import annotations

import inspect
import json
import logging
import socket
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from netcdf_fixture import encode_test_netcdf4

from yd_producer.canonical import converter as converter_module
from yd_producer.canonical.converter import (
    CanonicalConversionError,
    CanonicalConverter,
    CanonicalConverterConfig,
    IFSCanonicalConverter,
)
from yd_producer.store.object_store import LocalObjectStore

COMPACT_CYCLE = "2026050700"
CATALOG_KEY = f"canonical/gfs/{COMPACT_CYCLE}/_catalog/catalog.json"
FALLBACK_WARNING = "falling back to netcdf4"

#: 每个 GFS 原生变量的 GRIB shortName（eccodes `regular_ll_sfc_grib2` 样本可设），
#: 与 `CFGRIB_VARIABLE_ALIASES` 的别名集相交，故 `_select_cfgrib_data_variable` 在真数据上能选中。
GRIB_SHORT_NAMES: dict[str, str] = {
    "tmp2m": "2t",
    "apcp": "tp",
    "rh2m": "2r",
    "u10m": "10u",
    "v10m": "10v",
    "pressfc": "sp",
    "dswrf": "ssrd",
}

#: 每个原生变量在 f000 / f003 上写入的格点值（4 个格点同值）。apcp 是 cycle 累积量，
#: 故 f000 为 0、f003 为 3mm —— 去累积后 3mm/3h = 24 mm/day。
NATIVE_VALUES: dict[str, tuple[float, float]] = {
    "tmp2m": (280.0, 283.0),
    "apcp": (0.0, 3.0),
    "rh2m": (50.0, 50.0),
    "u10m": (1.0, 1.0),
    "v10m": (2.0, 2.0),
    "pressfc": (101325.0, 101325.0),
    "dswrf": (100.0, 100.0),
}

FORECAST_HOURS = (0, 3)
GRID_NI = 2
GRID_NJ = 2
GRID_CELLS = GRID_NI * GRID_NJ


def _build_converter(tmp_path: Path) -> CanonicalConverter:
    root = tmp_path.resolve()
    return CanonicalConverter(
        config=CanonicalConverterConfig(
            workspace_root=root,
            object_store_root=root,
            object_store_prefix="",
        ),
        object_store=LocalObjectStore(root),
    )


def _local_key(variable: str, forecast_hour: int) -> str:
    return (
        f"raw/gfs/{COMPACT_CYCLE}/gfs.t00z.pgrb2.0p25"
        f".f{forecast_hour:03d}.{variable}.grib2"
    )


def _manifest(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "source_id": "gfs",
        "cycle_time": converter_module.parse_cycle_time(COMPACT_CYCLE).isoformat(),
        "entries": entries,
    }


def _write_netcdf_raw(store: LocalObjectStore) -> dict[str, Any]:
    """合成 raw（NetCDF 替身，与 pin 快照用例同一套 fixture）+ manifest。"""

    cycle_time = converter_module.parse_cycle_time(COMPACT_CYCLE)
    entries: list[dict[str, Any]] = []
    for forecast_hour in FORECAST_HOURS:
        for variable in converter_module.VARIABLE_MAPPING:
            local_key = _local_key(variable, forecast_hour)
            store.write_bytes_atomic(
                local_key,
                encode_test_netcdf4(variable, forecast_hour, cycle_time=cycle_time),
            )
            entries.append(
                {
                    "remote_url": f"mock://{variable}/{forecast_hour}",
                    "local_key": local_key,
                    "variable": variable,
                    "forecast_hour": forecast_hour,
                }
            )
    return _manifest(entries)


def _encode_grib2(
    *,
    short_name: str,
    forecast_hour: int,
    value: float,
    cycle_time: datetime,
) -> bytes:
    """用 eccodes 的 `regular_ll_sfc_grib2` 样本造一条真 GRIB2 报文（约 215 字节）。"""

    import eccodes

    handle = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        eccodes.codes_set(handle, "dataDate", int(cycle_time.strftime("%Y%m%d")))
        eccodes.codes_set(handle, "dataTime", cycle_time.hour * 100)
        eccodes.codes_set(handle, "stepUnits", 1)
        eccodes.codes_set(handle, "step", forecast_hour)
        eccodes.codes_set(handle, "shortName", short_name)
        eccodes.codes_set(handle, "Ni", GRID_NI)
        eccodes.codes_set(handle, "Nj", GRID_NJ)
        eccodes.codes_set(handle, "latitudeOfFirstGridPointInDegrees", 0.25)
        eccodes.codes_set(handle, "longitudeOfFirstGridPointInDegrees", 0.0)
        eccodes.codes_set(handle, "latitudeOfLastGridPointInDegrees", 0.0)
        eccodes.codes_set(handle, "longitudeOfLastGridPointInDegrees", 0.25)
        eccodes.codes_set(handle, "iDirectionIncrementInDegrees", 0.25)
        eccodes.codes_set(handle, "jDirectionIncrementInDegrees", 0.25)
        eccodes.codes_set_values(handle, [value] * GRID_CELLS)
        return eccodes.codes_get_message(handle)
    finally:
        eccodes.codes_release(handle)


def _write_grib2_raw(store: LocalObjectStore) -> dict[str, Any]:
    """合成真 GRIB2 raw + 携带 `metadata.grib_short_name` 的 manifest。"""

    cycle_time = converter_module.parse_cycle_time(COMPACT_CYCLE)
    entries: list[dict[str, Any]] = []
    for index, forecast_hour in enumerate(FORECAST_HOURS):
        for variable in converter_module.VARIABLE_MAPPING:
            short_name = GRIB_SHORT_NAMES[variable]
            local_key = _local_key(variable, forecast_hour)
            store.write_bytes_atomic(
                local_key,
                _encode_grib2(
                    short_name=short_name,
                    forecast_hour=forecast_hour,
                    value=NATIVE_VALUES[variable][index],
                    cycle_time=cycle_time,
                ),
            )
            entries.append(
                {
                    "remote_url": f"mock://{variable}/{forecast_hour}",
                    "local_key": local_key,
                    "variable": variable,
                    "forecast_hour": forecast_hour,
                    "metadata": {
                        "grib_short_name": short_name,
                        "cfgrib_filter_by_keys": {"shortName": short_name},
                    },
                }
            )
    return _manifest(entries)


def _read_catalog(converter: CanonicalConverter) -> dict[str, Any]:
    return json.loads(converter.object_store.read_bytes(CATALOG_KEY).decode("utf-8"))


def _product_values(converter: CanonicalConverter, object_uri: str, variable: str):
    import xarray as xr

    path = converter.object_store.resolve_path(object_uri)
    dataset = xr.open_dataset(path, engine="netcdf4")
    try:
        return [float(value) for value in dataset[variable].values.ravel().tolist()]
    finally:
        dataset.close()


@pytest.fixture
def no_outbound_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """运行期闸门：转换全程一次出站连接都不许发生。

    静态禁区 grep（`test_snapshot_provenance.py`）对「运行期动态 import 一个 DB 驱动」零判别力，
    本闸门才是 issue #13「无数据库连接」验收的运行期判别器。
    """

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "canonical 转换期间发生了出站 socket 连接；DB-free 快照不得建立任何连接"
        )

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def test_convert_manifest_writes_products_and_catalog_without_any_socket(
    tmp_path: Path, no_outbound_sockets: None
) -> None:
    converter = _build_converter(tmp_path)
    manifest = _write_netcdf_raw(converter.object_store)

    result = converter.convert_manifest(manifest)

    assert result.status == "canonical_ready"
    assert len(result.products) == 14
    catalog = _read_catalog(converter)
    assert catalog["schema_version"] == "nhms.canonical.product_catalog.v1"
    assert len(catalog["products"]) == 14
    for product in result.products:
        assert converter.object_store.exists(product.object_uri)


def test_converter_module_exposes_no_repository_surface() -> None:
    assert hasattr(converter_module, "CanonicalRepository") is False
    assert hasattr(converter_module, "PsycopgMetStore") is False
    for cls in (CanonicalConverter, IFSCanonicalConverter):
        assert "repository" not in inspect.signature(cls.__init__).parameters
        assert not hasattr(cls, "from_env")


def test_converter_config_path_fields_are_required_keyword_only(tmp_path: Path) -> None:
    # D4 零默认：三个路径字段无缺省、空串回退（pin 的 `__post_init__`）已删。
    with pytest.raises(TypeError):
        CanonicalConverterConfig(workspace_root=tmp_path)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        CanonicalConverterConfig(  # type: ignore[call-arg]
            workspace_root=tmp_path, object_store_root=tmp_path
        )
    config = CanonicalConverterConfig(
        workspace_root=tmp_path, object_store_root=tmp_path, object_store_prefix=""
    )
    assert config.object_store_root == tmp_path


def test_convert_manifest_decodes_real_grib2_through_cfgrib_backend(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, no_outbound_sockets: None
) -> None:
    converter = _build_converter(tmp_path)
    manifest = _write_grib2_raw(converter.object_store)

    with caplog.at_level(logging.WARNING, logger=converter_module.LOGGER.name):
        result = converter.convert_manifest(manifest)

    # 本用例最可能的假绿形态：cfgrib 后端报错、netcdf4 回退静默成功。
    assert FALLBACK_WARNING not in caplog.text
    assert result.status == "canonical_ready"
    assert len(result.products) == 14

    catalog = _read_catalog(converter)
    assert len(catalog["products"]) == 14
    by_id = {row["canonical_product_id"]: row for row in catalog["products"]}

    temperature = by_id[f"gfs_{COMPACT_CYCLE}_air_temperature_2m_f003"]
    assert temperature["unit"] == "degC"
    assert _product_values(
        converter, temperature["object_uri"], "air_temperature_2m"
    ) == pytest.approx([283.0 - 273.15] * GRID_CELLS, abs=0.05)

    # apcp 是 cycle 累积量：f003 的 3mm 去累积后是 3mm/3h = 24 mm/day。
    precipitation = by_id[f"gfs_{COMPACT_CYCLE}_prcp_rate_or_amount_f003"]
    assert precipitation["unit"] == "mm/day"
    assert _product_values(
        converter, precipitation["object_uri"], "prcp_rate_or_amount"
    ) == pytest.approx([24.0] * GRID_CELLS, abs=0.05)

    humidity = by_id[f"gfs_{COMPACT_CYCLE}_relative_humidity_2m_f000"]
    assert _product_values(
        converter, humidity["object_uri"], "relative_humidity_2m"
    ) == pytest.approx([0.5] * GRID_CELLS, abs=0.01)


def test_unparseable_raw_bytes_fail_without_partial_catalog(tmp_path: Path) -> None:
    converter = _build_converter(tmp_path)
    manifest = _write_netcdf_raw(converter.object_store)
    corrupted_key = _local_key("tmp2m", 3)
    converter.object_store.write_bytes_atomic(corrupted_key, b"not a netcdf file")

    with pytest.raises(CanonicalConversionError, match=corrupted_key):
        converter.convert_manifest(manifest)

    assert not converter.object_store.exists(CATALOG_KEY)
