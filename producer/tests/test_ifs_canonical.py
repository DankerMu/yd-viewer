# NWM@8ae9b8f2 tests/test_ifs_canonical.py
"""IFS canonical numerical/QC/lineage oracles from NWM@8ae9b8f29c8b72c574e8cbd95f2994160bd42832.

Adaptations (inventory row 54 / port-ifs-canonical-oracles):
- Drop FakeCanonicalRepository and every repository argument/construction.
- build_converter requires workspace_root/object_store_root/object_store_prefix.
- Observe persisted catalog rows at canonical/ifs/2026050100/_catalog/catalog.json;
  stored IDs are ifs_..., raw/IFS and manifest source_id IFS stay pin form.
- missing-ssr renamed; typed CanonicalConversionError covers ssr->net_radiation and
  ssr->shortwave_down; no canonical products/catalog; DB fail rows not reconstructed.
- Negative precipitation is one parametrized test with independent small/significant/
  consecutive IDs; retain values/quality/counters and add small anomaly type.
- Broad Exception tightened to CanonicalConversionError; encode_test_netcdf4 reused.
"""

from __future__ import annotations

import importlib
import json
import math
from pathlib import Path
from typing import Any

import pytest
from netcdf_fixture import encode_test_netcdf4

from yd_producer.store.object_store import LocalObjectStore

converter_module = importlib.import_module("yd_producer.canonical.converter")

CanonicalConversionError = converter_module.CanonicalConversionError
IFSCanonicalConverter = converter_module.IFSCanonicalConverter
IFSCanonicalConverterConfig = converter_module.IFSCanonicalConverterConfig
IFS_VARIABLE_MAPPING = converter_module.IFS_VARIABLE_MAPPING
convert_ifs_precipitation_with_metadata = (
    converter_module.convert_ifs_precipitation_with_metadata
)
convert_ifs_shortwave_down_values = converter_module.convert_ifs_shortwave_down_values
parse_cycle_time = converter_module.parse_cycle_time

IFS_STANDARD_UNITS = converter_module.IFS_STANDARD_UNITS

IFS_VARIABLES: tuple[str, ...] = ("2t", "2d", "10u", "10v", "tp", "sp", "ssr", "str")
IFS_COMPACT_CYCLE = "2026050100"
IFS_CATALOG_KEY = f"canonical/ifs/{IFS_COMPACT_CYCLE}/_catalog/catalog.json"

pytestmark = pytest.mark.usefixtures("no_outbound_sockets")

pytest_plugins = ("test_canonical_db_free",)


def default_ifs_value(variable: str, forecast_hour: int) -> float:
    if variable == "2t":
        return 293.15
    if variable == "2d":
        return 283.15
    if variable == "10u":
        return 3.5
    if variable == "10v":
        return -2.0
    if variable == "tp":
        return forecast_hour * 0.001
    if variable == "sp":
        return 100500.0
    if variable == "ssr":
        return forecast_hour * 3600.0 * 120.0
    if variable == "str":
        return forecast_hour * 3600.0 * -40.0
    raise ValueError(f"Unsupported IFS variable: {variable}")


def build_ifs_manifest(
    tmp_path: Path,
    *,
    forecast_hours: tuple[int, ...] = (0,),
    overrides: dict[tuple[str, int], list[float]] | None = None,
) -> tuple[LocalObjectStore, dict[str, Any]]:
    cycle_time = parse_cycle_time("2026050100")
    compact_cycle = "2026050100"
    store = LocalObjectStore(tmp_path)
    entries: list[dict[str, Any]] = []
    overrides = overrides or {}

    for forecast_hour in forecast_hours:
        for variable in IFS_VARIABLES:
            values = overrides.get(
                (variable, forecast_hour), [default_ifs_value(variable, forecast_hour)]
            )
            local_key = f"raw/IFS/{compact_cycle}/ifs.t00z.f{forecast_hour:03d}.{variable}.grib2"
            store.write_bytes_atomic(
                local_key,
                encode_test_netcdf4(
                    variable,
                    forecast_hour,
                    values=values,
                    cycle_time=cycle_time,
                    source="IFS",
                ),
            )
            entries.append(
                {
                    "remote_url": f"mock://IFS/{variable}/{forecast_hour}",
                    "local_key": local_key,
                    "variable": variable,
                    "forecast_hour": forecast_hour,
                }
            )

    return (
        store,
        {
            "source_id": "IFS",
            "cycle_time": cycle_time.isoformat(),
            "metadata": {
                "forecast_hours": list(forecast_hours),
                "max_lead_hours": max(forecast_hours),
            },
            "entries": entries,
        },
    )


def build_converter(tmp_path: Path) -> IFSCanonicalConverter:
    config = IFSCanonicalConverterConfig(
        workspace_root=tmp_path,
        object_store_root=tmp_path,
        object_store_prefix="",
    )
    return IFSCanonicalConverter(
        config=config,
        object_store=LocalObjectStore(tmp_path),
    )


def read_catalog_rows(store: LocalObjectStore) -> dict[str, dict[str, Any]]:
    catalog = json.loads(store.read_bytes(IFS_CATALOG_KEY).decode("utf-8"))
    return {row["canonical_product_id"]: row for row in catalog["products"]}


def catalog_product(
    store: LocalObjectStore, standard_variable: str, forecast_hour: int
) -> dict[str, Any]:
    product_id = f"ifs_{IFS_COMPACT_CYCLE}_{standard_variable}_f{forecast_hour:03d}"
    return read_catalog_rows(store)[product_id]


def read_product_values(
    store: LocalObjectStore, product: dict[str, Any], variable: str
) -> list[float]:
    import xarray as xr

    dataset = xr.open_dataset(
        store.resolve_path(product["object_uri"]), engine="netcdf4"
    )
    try:
        return [float(value) for value in dataset[variable].values.tolist()]
    finally:
        dataset.close()


def test_ifs_variable_mapping_uses_surface_pressure() -> None:
    assert IFS_VARIABLE_MAPPING["2t"] == "air_temperature_2m"
    assert IFS_VARIABLE_MAPPING["2d"] == "relative_humidity_2m"
    assert IFS_VARIABLE_MAPPING["tp"] == "prcp_rate_or_amount"
    assert IFS_VARIABLE_MAPPING["sp"] == "surface_pressure"


def test_missing_ssr_rejects_without_products_or_catalog(tmp_path: Path) -> None:
    _, manifest = build_ifs_manifest(tmp_path, forecast_hours=(0,))
    manifest["entries"] = [
        entry for entry in manifest["entries"] if entry["variable"] != "ssr"
    ]
    converter = build_converter(tmp_path)

    with pytest.raises(CanonicalConversionError, match="ssr->net_radiation") as raised:
        converter.convert_manifest(manifest)

    assert "ssr->shortwave_down" in str(raised.value)
    assert not converter.object_store.exists(IFS_CATALOG_KEY)
    assert list(tmp_path.glob("canonical/**/*.nc")) == []


def test_temperature_rh_wind_and_pressure_conversion(tmp_path: Path) -> None:
    store, manifest = build_ifs_manifest(
        tmp_path,
        forecast_hours=(0,),
        overrides={
            ("2t", 0): [293.15],
            ("2d", 0): [283.15],
            ("10u", 0): [6.0],
            ("10v", 0): [-4.0],
            ("sp", 0): [100125.0],
        },
    )
    converter = build_converter(tmp_path)

    result = converter.convert_manifest(manifest)

    assert result.status == "canonical_ready"
    assert len(result.products) == 8
    temperature = catalog_product(store, "air_temperature_2m", 0)
    humidity = catalog_product(store, "relative_humidity_2m", 0)
    wind_u = catalog_product(store, "wind_u_10m", 0)
    wind_v = catalog_product(store, "wind_v_10m", 0)
    assert read_product_values(
        store, temperature, "air_temperature_2m"
    ) == pytest.approx([20.0])
    assert read_product_values(
        store, humidity, "relative_humidity_2m"
    ) == pytest.approx([0.525], abs=1e-3)
    assert read_product_values(store, wind_u, "wind_u_10m") == pytest.approx([6.0])
    assert read_product_values(store, wind_v, "wind_v_10m") == pytest.approx([-4.0])
    pressure = catalog_product(store, "surface_pressure", 0)
    assert pressure["variable"] == "surface_pressure"
    assert pressure["unit"] == "Pa"
    assert read_product_values(store, pressure, "surface_pressure") == pytest.approx(
        [100125.0]
    )
    catalog_path = store.resolve_path(IFS_CATALOG_KEY)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_pressure = next(
        row
        for row in catalog["products"]
        if row["canonical_product_id"] == pressure["canonical_product_id"]
    )
    assert catalog_pressure["variable"] == "surface_pressure"
    assert catalog_pressure["unit"] == "Pa"


def test_precipitation_cumulative_m_to_mm_per_step(tmp_path: Path) -> None:
    store, manifest = build_ifs_manifest(
        tmp_path,
        forecast_hours=(3, 6),
        overrides={
            ("tp", 3): [0.003],
            ("tp", 6): [0.006],
        },
    )
    converter = build_converter(tmp_path)

    converter.convert_manifest(manifest)

    precipitation = catalog_product(store, "prcp_rate_or_amount", 6)
    assert precipitation["unit"] == "mm/day"
    assert precipitation["quality_flag"] == "ok"
    # per-step delta 3.0 mm over a 3h step -> 3.0 * 24 / 3 = 24.0 mm/day
    assert read_product_values(
        store, precipitation, "prcp_rate_or_amount"
    ) == pytest.approx([24.0])
    lineage = precipitation["lineage_json"]["conversion_params"]
    assert lineage["accumulation_type"] == "since_cycle"
    assert lineage["unit_conversion"] == "m_to_mm_day"
    assert lineage["step_hours"] == 3.0


def test_ifs_canonical_prcp_unit_contract_is_mm_day(tmp_path: Path) -> None:
    # Cross-layer contract: IFS canonical PRCP is emitted in mm/day, aligned with ERA5. The
    # converter rescales each per-step accumulation by its actual step (24 / step_hours) so the
    # producer can pass it through (factor 1.0) without any further step-dependent conversion.
    # Pin the mm/day contract both at the constant and at the actually-produced canonical product.
    assert IFS_STANDARD_UNITS["prcp_rate_or_amount"] == "mm/day"

    store, manifest = build_ifs_manifest(
        tmp_path,
        forecast_hours=(3, 6),
        overrides={("tp", 3): [0.003], ("tp", 6): [0.006]},
    )
    converter = build_converter(tmp_path)
    converter.convert_manifest(manifest)

    precipitation = catalog_product(store, "prcp_rate_or_amount", 6)
    assert precipitation["unit"] == "mm/day"
    assert precipitation["unit"] == IFS_STANDARD_UNITS["prcp_rate_or_amount"]
    # per-step delta 3.0 mm over a 3h step -> 24.0 mm/day
    assert read_product_values(
        store, precipitation, "prcp_rate_or_amount"
    ) == pytest.approx([24.0])


@pytest.mark.parametrize(
    (
        "current",
        "previous",
        "consecutive_negative_count",
        "quality_flag",
        "count",
        "anomaly_type",
    ),
    [
        pytest.param(
            [0.001999],
            [0.002],
            0,
            "ok",
            0,
            "small_negative_ifs_precipitation_delta",
            id="small",
        ),
        pytest.param(
            [0.001],
            [0.002],
            0,
            "warning_negative_precip",
            1,
            None,
            id="significant",
        ),
        pytest.param(
            [0.001],
            [0.002],
            2,
            "error_precip_accumulation",
            3,
            None,
            id="consecutive",
        ),
    ],
)
def test_negative_precipitation_handling_all_cases(
    current: list[float],
    previous: list[float],
    consecutive_negative_count: int,
    quality_flag: str,
    count: int,
    anomaly_type: str | None,
) -> None:
    conversion, next_count, _ = convert_ifs_precipitation_with_metadata(
        current,
        previous,
        consecutive_negative_count=consecutive_negative_count,
    )

    assert conversion.values == pytest.approx((0.0,))
    assert conversion.quality_flag == quality_flag
    assert next_count == count
    if anomaly_type is not None:
        assert conversion.anomalies[0]["type"] == anomaly_type


def test_ifs_precip_grib_quantization_noise_stays_ok() -> None:
    # IFS tp 的 GRIB 16-bit 量化步长 ≈1000×2⁻¹⁶=0.0152587890625mm。该幅度的伪负 delta
    # 是量化噪声(实测 cycle 2026060400 +129h/+144h 即此值),应判 small→quality_flag=ok,
    # 不得判 warning_negative_precip(否则被 forcing 当不可用剔除致缺降水产品)。
    quant_step_m = 1000.0 * (2.0**-16) / 1000.0  # 0.0152587890625mm 对应的米
    result, count, _ = convert_ifs_precipitation_with_metadata(
        [0.002 - quant_step_m], [0.002], forecast_hour=129, previous_forecast_hour=126
    )
    assert result.values == pytest.approx((0.0,))
    assert result.quality_flag == "ok"
    assert count == 0


def test_radiation_cumulative_diff_to_w_m2(tmp_path: Path) -> None:
    store, manifest = build_ifs_manifest(
        tmp_path,
        forecast_hours=(3, 6),
        overrides={
            ("ssr", 3): [720_000.0],
            ("str", 3): [-180_000.0],
            ("ssr", 6): [1_440_000.0],
            ("str", 6): [-360_000.0],
        },
    )
    converter = build_converter(tmp_path)

    converter.convert_manifest(manifest)

    radiation = catalog_product(store, "net_radiation", 6)
    shortwave = catalog_product(store, "shortwave_down", 6)
    assert read_product_values(store, radiation, "net_radiation") == pytest.approx(
        [50.0]
    )
    assert read_product_values(store, shortwave, "shortwave_down") == pytest.approx(
        [66.6666667]
    )
    assert radiation["lineage_json"]["radiation_method"] == "direct_net"
    assert radiation["lineage_json"]["components"] == ["ssr", "str"]
    assert (
        shortwave["lineage_json"]["conversion_params"]["operation"]
        == "cumulative_j_m2_to_w_m2_downward_shortwave"
    )


def test_ifs_shortwave_rejects_nonfinite_accumulated_values() -> None:
    with pytest.raises(CanonicalConversionError, match="finite"):
        convert_ifs_shortwave_down_values(
            [float("nan")], [0.0], forecast_hour=3, previous_forecast_hour=0
        )


def test_ifs_precipitation_rejects_nonfinite_accumulated_values() -> None:
    with pytest.raises(CanonicalConversionError, match="finite"):
        convert_ifs_precipitation_with_metadata(
            [math.nan], [0.0], forecast_hour=3, previous_forecast_hour=0
        )


def test_ifs_shortwave_significant_negative_delta_is_warn_lineage_not_silent_ok() -> (
    None
):
    # -20000 J/m² over 3h ≈ -1.85 W/m²,超出量化噪声容差(1.0 W/m²)→ 真异常,标 warn。
    conversion, step_hours = convert_ifs_shortwave_down_values(
        [0.0],
        [20000.0],
        forecast_hour=6,
        previous_forecast_hour=3,
    )

    assert step_hours == 3.0
    assert conversion.values == pytest.approx((0.0,))
    assert conversion.quality_flag == "warn"
    assert conversion.anomalies[0]["type"] == "negative_ifs_shortwave_delta"


def test_ifs_shortwave_quantization_noise_negative_delta_stays_ok() -> None:
    # 夜间持平段的 GRIB 量化抖动:-100 J/m² over 3h ≈ -0.009 W/m²,SHUD 自身对 Rn<0
    # 即钳 0 并取整到整数 W/m²,此负值读入后与 0 逐位等价 → 不标 warn,值 clamp 到 0。
    conversion, step_hours = convert_ifs_shortwave_down_values(
        [100.0],
        [200.0],
        forecast_hour=6,
        previous_forecast_hour=3,
    )

    assert step_hours == 3.0
    assert conversion.values == pytest.approx((0.0,))
    assert conversion.quality_flag == "ok"
    assert conversion.anomalies[0]["type"] == "small_negative_ifs_shortwave_delta"


def test_ifs_shortwave_negative_delta_writes_warn_product_with_lineage(
    tmp_path: Path,
) -> None:
    store, manifest = build_ifs_manifest(
        tmp_path,
        forecast_hours=(3, 6),
        overrides={
            ("ssr", 3): [20000.0],
            ("ssr", 6): [0.0],
        },
    )
    converter = build_converter(tmp_path)

    converter.convert_manifest(manifest)

    shortwave = catalog_product(store, "shortwave_down", 6)
    assert shortwave["quality_flag"] == "warn"
    anomalies = shortwave["lineage_json"]["conversion_params"]["anomalies"]
    assert anomalies[0]["type"] == "negative_ifs_shortwave_delta"


def test_ifs_convert_manifest_streams_by_group_without_read_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, manifest = build_ifs_manifest(tmp_path, forecast_hours=(0, 3))
    converter = build_converter(tmp_path)

    def forbidden_read_records(_entries: list[dict[str, Any]]) -> list[Any]:
        raise AssertionError("_read_records must not be used by convert_manifest")

    monkeypatch.setattr(converter, "_read_records", forbidden_read_records)

    result = converter.convert_manifest(manifest)

    assert result.status == "canonical_ready"
    assert len(result.products) == 16


def test_lineage_json_structure_for_each_variable_type(tmp_path: Path) -> None:
    store, manifest = build_ifs_manifest(tmp_path, forecast_hours=(0, 3))
    converter = build_converter(tmp_path)

    converter.convert_manifest(manifest)

    temperature = catalog_product(store, "air_temperature_2m", 0)["lineage_json"]
    humidity = catalog_product(store, "relative_humidity_2m", 0)["lineage_json"]
    precipitation = catalog_product(store, "prcp_rate_or_amount", 3)["lineage_json"]
    radiation = catalog_product(store, "net_radiation", 3)["lineage_json"]
    wind = catalog_product(store, "wind_u_10m", 0)["lineage_json"]
    pressure = catalog_product(store, "surface_pressure", 0)["lineage_json"]

    assert temperature["conversion_params"]["unit_conversion"] == "K_to_C"
    assert humidity["conversion_params"]["derived_from"] == ["2t", "2d"]
    assert humidity["method"] == "magnus_formula"
    assert precipitation["conversion_params"]["operation"] == "cumulative_m_to_mm_day"
    assert precipitation["conversion_params"]["step_hours"] == 3.0
    assert radiation["conversion_params"]["components"] == ["ssr", "str"]
    assert wind["conversion_params"]["operation"] == "pass_through"
    assert pressure["conversion_params"]["native_variable"] == "sp"
    lineages = (temperature, humidity, precipitation, radiation, wind, pressure)
    assert {lineage["converter_version"] for lineage in lineages} == {"m4.1"}
