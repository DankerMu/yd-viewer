"""IFS tp 去累积噪声容差随 GRIB 有效量化格距（#369，compute-loop §7.3）。

场景来自 openspec/changes/ifs-tp-quantization-tolerance tasks.md §2。新 helper 在测试函数内
import，红跑（还原 src）时只让新行为用例失败，不连累本文件与 pinned 用例的收集。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_ifs_canonical import (
    IFS_VARIABLES,
    build_converter,
    build_ifs_manifest,
    catalog_product,
    convert_ifs_precipitation_with_metadata,
    default_ifs_value,
    parse_cycle_time,
    read_product_values,
)

from yd_producer.rawcopy import MANIFEST_FILENAME
from yd_producer.store.object_store import LocalObjectStore

pytestmark = pytest.mark.usefixtures("no_outbound_sockets")

pytest_plugins = ("test_canonical_db_free",)

# E=-13、D=0 的 12-bit 文件步长：2**-13 m = 0.1220703125 mm（spec 场景给定的独立字面值）。
FILE_STEP_M = 2.0**-13
FILE_STEP_MM = 0.1220703125
GRID_NI = 4
GRID_NJ = 2
IFS_CYCLE = "2026050100"


# ---------------------------------------------------------------- helper (2.1)


def test_effective_step_is_coarse_lattice_of_file_step() -> None:
    from yd_producer.canonical._units import effective_quantization_step_mm

    values = [0.125 + 4 * FILE_STEP_M * k for k in (0, 1, 3, 7, 250, 1000)]
    assert effective_quantization_step_mm(values, -13, 0, 12) == 0.48828125


def test_effective_step_takes_power_of_two_factor_of_sparse_gcd() -> None:
    from yd_producer.canonical._units import effective_quantization_step_mm

    # 偏移只有 {0, 24}：原始 gcd 24 被稀疏场放大，取最大的 2 的幂因子 8。
    values = [0.125, 0.125 + 24 * FILE_STEP_M, 0.125, 0.125 + 24 * FILE_STEP_M]
    assert effective_quantization_step_mm(values, -13, 0, 12) == 0.9765625


def test_effective_step_ignores_non_finite_values() -> None:
    from yd_producer.canonical._units import effective_quantization_step_mm

    values = [float("nan"), 0.125, 0.125 + 2 * FILE_STEP_M, float("inf")]
    assert effective_quantization_step_mm(values, -13, 0, 12) == 0.244140625


@pytest.mark.parametrize(
    ("values", "keys"),
    [
        pytest.param([0.0, 0.0, 0.0], (-13, 0, 12), id="constant-field-gcd-zero"),
        pytest.param([0.0, FILE_STEP_M], (-13, 0, 0), id="bits-zero"),
        pytest.param([0.0, FILE_STEP_M], (None, 0, 12), id="missing-binary"),
        pytest.param([0.0, FILE_STEP_M], (-13, None, 12), id="missing-decimal"),
        pytest.param([0.0, FILE_STEP_M], (-13, 0, None), id="missing-bits"),
        pytest.param([0.0, FILE_STEP_M], (-13.0, 0, 12), id="float-key"),
        pytest.param([0.0, FILE_STEP_M], (-13, False, 12), id="bool-key"),
        pytest.param([float("nan")], (-13, 0, 12), id="no-finite-values"),
    ],
)
def test_effective_step_absent_without_usable_quantization(
    values: list[float], keys: tuple[Any, Any, Any]
) -> None:
    from yd_producer.canonical._units import effective_quantization_step_mm

    assert effective_quantization_step_mm(values, *keys) is None


# ---------------------------------------------- pure conversion (2.2 / 2.3)


def _precip(delta_mm: float, **steps: float | None) -> tuple[Any, int]:
    conversion, next_count, _ = convert_ifs_precipitation_with_metadata(
        [0.5 + delta_mm / 1000.0],
        [0.5],
        forecast_hour=6,
        previous_forecast_hour=3,
        **steps,
    )
    return conversion, next_count


def test_one_effective_step_negative_delta_is_noise() -> None:
    conversion, next_count = _precip(
        -0.1221, quantization_step_mm=0.1221, previous_quantization_step_mm=0.1221
    )

    assert conversion.quality_flag == "ok"
    assert next_count == 0
    assert conversion.values == pytest.approx((0.0,))
    (anomaly,) = conversion.anomalies
    assert anomaly["type"] == "small_negative_ifs_precipitation_delta"
    assert anomaly["tolerance_mm"] == pytest.approx(0.1221)


def test_first_lead_uses_current_step_only() -> None:
    conversion, _ = _precip(-0.1221, quantization_step_mm=0.1221)

    assert conversion.quality_flag == "ok"
    assert conversion.anomalies[0]["tolerance_mm"] == pytest.approx(0.1221)


def test_coarser_side_sets_tolerance() -> None:
    conversion, _ = _precip(
        -0.2, quantization_step_mm=0.1221, previous_quantization_step_mm=0.2442
    )

    assert conversion.quality_flag == "ok"
    assert conversion.anomalies[0]["tolerance_mm"] == pytest.approx(0.2442)


def test_two_effective_steps_negative_delta_is_flagged() -> None:
    conversion, next_count = _precip(
        -0.2442, quantization_step_mm=0.1221, previous_quantization_step_mm=0.1221
    )

    assert conversion.quality_flag == "warning_negative_precip"
    assert next_count == 1
    assert conversion.values == pytest.approx((0.0,))
    (anomaly,) = conversion.anomalies
    assert anomaly["type"] == "negative_ifs_precipitation_delta"
    assert anomaly["tolerance_mm"] == pytest.approx(0.1221)


def test_third_consecutive_flagged_delta_escalates_with_step() -> None:
    conversion, next_count, _ = convert_ifs_precipitation_with_metadata(
        [0.5 - 0.0002442],
        [0.5],
        forecast_hour=6,
        previous_forecast_hour=3,
        consecutive_negative_count=2,
        quantization_step_mm=0.1221,
        previous_quantization_step_mm=0.1221,
    )

    assert conversion.quality_flag == "error_precip_accumulation"
    assert next_count == 3


@pytest.mark.parametrize(
    ("delta_mm", "quality_flag", "anomaly_type"),
    [
        pytest.param(
            -0.12, "warning_negative_precip", "negative_ifs_precipitation_delta"
        ),
        pytest.param(-0.05, "ok", "small_negative_ifs_precipitation_delta"),
        pytest.param(
            -0.5, "warning_negative_precip", "negative_ifs_precipitation_delta"
        ),
    ],
)
def test_without_step_metadata_tolerance_stays_legacy(
    delta_mm: float, quality_flag: str, anomaly_type: str
) -> None:
    conversion, _ = _precip(delta_mm)

    assert conversion.quality_flag == quality_flag
    assert conversion.values == pytest.approx((0.0,))
    assert [anomaly["type"] for anomaly in conversion.anomalies] == [anomaly_type]


# ------------------------------------------------- end-to-end real GRIB2 (2.4)


def _encode_field(
    short_name: str,
    forecast_hour: int,
    values: list[float],
    cycle_time: datetime,
    *,
    bits_per_value: int | None = None,
) -> bytes:
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
        eccodes.codes_set(handle, "longitudeOfLastGridPointInDegrees", 0.75)
        eccodes.codes_set(handle, "iDirectionIncrementInDegrees", 0.25)
        eccodes.codes_set(handle, "jDirectionIncrementInDegrees", 0.25)
        if bits_per_value is not None:
            eccodes.codes_set(handle, "bitsPerValue", bits_per_value)
        eccodes.codes_set_values(handle, values)
        return eccodes.codes_get_message(handle)
    finally:
        eccodes.codes_release(handle)


def _bits_per_value(message: bytes) -> int:
    import eccodes

    handle = eccodes.codes_new_from_message(message)
    try:
        return int(eccodes.codes_get(handle, "bitsPerValue"))
    finally:
        eccodes.codes_release(handle)


def _file_step_mm(message: bytes) -> float:
    """编码后回读实际的 E/D（不硬编码 E）。"""

    import eccodes

    handle = eccodes.codes_new_from_message(message)
    try:
        exponent = eccodes.codes_get(handle, "binaryScaleFactor")
        decimal = eccodes.codes_get(handle, "decimalScaleFactor")
        assert eccodes.codes_get(handle, "bitsPerValue") == 12
    finally:
        eccodes.codes_release(handle)
    return 1000.0 * 2.0**exponent * 10.0 ** (-decimal)


def _write_grib_bundles(
    store: LocalObjectStore, tp_by_hour: dict[int, tuple[list[float], int | None]]
) -> tuple[dict[str, Any], dict[int, bytes]]:
    """每个时效一个真实多消息 GRIB2 bundle（按生产 raw 的 shortName 过滤约定）。"""

    cycle_time = parse_cycle_time(IFS_CYCLE)
    cells = GRID_NI * GRID_NJ
    entries: list[dict[str, Any]] = []
    tp_messages: dict[int, bytes] = {}
    for forecast_hour, (tp_values, tp_bits) in tp_by_hour.items():
        local_key = f"raw/IFS/{IFS_CYCLE}/ifs.t00z.f{forecast_hour:03d}.bundle.grib2"
        bundle = b""
        for variable in IFS_VARIABLES:
            if variable == "tp":
                message = _encode_field(
                    "tp", forecast_hour, tp_values, cycle_time, bits_per_value=tp_bits
                )
                tp_messages[forecast_hour] = message
            else:
                value = default_ifs_value(variable, forecast_hour)
                message = _encode_field(
                    variable, forecast_hour, [value] * cells, cycle_time
                )
            bundle += message
            entries.append(
                {
                    "remote_url": f"mock://IFS/{variable}/{forecast_hour}",
                    "local_key": local_key,
                    "variable": variable,
                    "forecast_hour": forecast_hour,
                    "metadata": {
                        "bundle": {
                            "layout": "per_forecast_hour",
                            "variables": list(IFS_VARIABLES),
                        },
                        "grib_short_name": variable,
                        "cfgrib_filter_by_keys": {"shortName": variable},
                    },
                }
            )
        store.write_bytes_atomic(local_key, bundle)
    hours = sorted(tp_by_hour)
    manifest = {
        "source_id": "IFS",
        "cycle_time": cycle_time.isoformat(),
        "metadata": {"forecast_hours": hours, "max_lead_hours": max(hours)},
        "entries": entries,
    }
    return manifest, tp_messages


def test_12bit_coarse_lattice_noise_keeps_cycle_ready(tmp_path: Path) -> None:
    # 两条消息都落在 4 个文件步长的格上；f006 以自身最小值为原点，比 f003 的最小值低 3s
    # （≡ +s mod 4s），于是存在 delta = −3s 的格点。span 4092s 使 12 bit 推出 E = −13。
    origin = 0.125
    lattice_k = (0, 1, 2, 1023, 5, 6, 7, 8)
    rise_j = (0, 1, 0, 0, 2, 0, 3, 0)
    f003 = [origin + 4 * FILE_STEP_M * k for k in lattice_k]
    f006 = [
        value - 3 * FILE_STEP_M + 4 * FILE_STEP_M * j for value, j in zip(f003, rise_j)
    ]
    store = LocalObjectStore(tmp_path)
    manifest, tp_messages = _write_grib_bundles(store, {3: (f003, 12), 6: (f006, 12)})
    for message in tp_messages.values():
        assert _file_step_mm(message) == FILE_STEP_MM

    result = build_converter(tmp_path).convert_manifest(manifest)

    assert result.status == "canonical_ready"
    product = catalog_product(store, "prcp_rate_or_amount", 6)
    assert product["quality_flag"] == "ok"
    anomalies = product["lineage_json"]["conversion_params"]["anomalies"]
    (anomaly,) = anomalies
    assert anomaly["type"] == "small_negative_ifs_precipitation_delta"
    assert anomaly["tolerance_mm"] == 4 * FILE_STEP_MM
    assert anomaly["tolerance_mm"] == 0.48828125
    assert anomaly["min_delta_mm"] == pytest.approx(-3 * FILE_STEP_MM)
    assert anomaly["negative_count"] == 5
    # 数值不变：max(0, delta)·24/3，delta 以文件步长计为 [-3, 1, -3, -3, 5, -3, 9, -3]。
    assert read_product_values(store, product, "prcp_rate_or_amount") == pytest.approx(
        [0.0, 0.9765625, 0.0, 0.0, 4.8828125, 0.0, 8.7890625, 0.0]
    )


def test_constant_first_lead_does_not_inflate_tolerance(tmp_path: Path) -> None:
    # f000 全零常量场（eccodes 编成 bitsPerValue 0）接 f003：一个格点为 −0.5 mm 的真负值，
    # 其余偏移 gcd 为 1，S_curr = 1 个文件步长 < 0.5 mm。
    cells = GRID_NI * GRID_NJ
    origin = -0.0005
    f003 = [origin + FILE_STEP_M * k for k in (0, 5, 6, 7, 4000, 9, 10, 11)]
    store = LocalObjectStore(tmp_path)
    manifest, tp_messages = _write_grib_bundles(
        store, {0: ([0.0] * cells, None), 3: (f003, 12)}
    )
    assert _bits_per_value(tp_messages[0]) == 0
    assert _file_step_mm(tp_messages[3]) == FILE_STEP_MM

    result = build_converter(tmp_path).convert_manifest(manifest)

    assert result.status == "canonical_incomplete"
    product = catalog_product(store, "prcp_rate_or_amount", 3)
    assert product["quality_flag"] == "warning_negative_precip"
    anomalies = product["lineage_json"]["conversion_params"]["anomalies"]
    (anomaly,) = anomalies
    assert anomaly["type"] == "negative_ifs_precipitation_delta"
    assert anomaly["min_delta_mm"] == pytest.approx(-0.5, abs=1e-6)


# ------------------------------------------------ _convert_canonical (2.5)


def _run_convert_canonical(
    tmp_path: Path, forecast_hours: tuple[int, ...], tp_values: dict[int, float]
) -> str:
    from yd_producer.nwm import ProductionAttemptError, _convert_canonical

    store, manifest = build_ifs_manifest(
        tmp_path,
        forecast_hours=forecast_hours,
        overrides={("tp", hour): [value] for hour, value in tp_values.items()},
    )
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    staged = SimpleNamespace(source="ifs", grid_id="yd_grid", work_dir=tmp_path)

    with pytest.raises(ProductionAttemptError) as raised:
        _convert_canonical(staged, store)
    return str(raised.value)


def test_incomplete_conversion_error_names_missing_leads(tmp_path: Path) -> None:
    # f051 累积量比 f048 少 1 mm → warning_negative_precip → readiness 判 f051 缺降水。
    message = _run_convert_canonical(tmp_path, (48, 51), {48: 0.048, 51: 0.047})

    assert message.startswith(
        "canonical conversion did not succeed: canonical_incomplete; "
    )
    assert "\n" not in message
    assert "missing_variables=[]" in message
    assert "missing_leads=[(51,['prcp_rate_or_amount'])]" in message
    assert "rejected_quality_flags={'warning_negative_precip': 1}" in message


def test_incomplete_conversion_error_bounds_missing_leads(tmp_path: Path) -> None:
    # 每个时效累积量都下降 1 mm：f003..f072 共 24 个时效被拒，消息只列前 20 个。
    hours = tuple(range(0, 73, 3))
    message = _run_convert_canonical(
        tmp_path, hours, {hour: 0.1 - hour / 3 * 0.001 for hour in hours}
    )

    assert "\n" not in message
    assert "(3,['prcp_rate_or_amount'])" in message
    assert "(60,['prcp_rate_or_amount'])" in message
    assert "(63,['prcp_rate_or_amount'])" not in message
    assert "...(+4 more)" in message
    assert "rejected_quality_flags={" in message
