"""`yd_producer.rawcopy.stage_raw` 源 manifest / verdict / 词表闸门回归。"""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from rawcopy_fixtures import (
    CARRIED_KEYS,
    CYCLE,
    CYCLE_DIR,
    FLOOR_MESSAGE,
    GFS_BUNDLE,
    GFS_SECOND_BUNDLE,
    GFS_VARIABLES,
    LEADS,
    SOURCE_MANIFEST_NAME,
    STRUCTURE_MESSAGE,
    build_tree,
    bundle_bytes,
    bundle_name,
    cycle_dir,
    expect_kind,
    make_config,
    make_source,
    selector_for,
    snapshot,
    source_entry,
    source_manifest_payload,
    staged,
    write_source_manifest,
)

from yd_producer.config import ConfigError
from yd_producer.raw.manifest import DownloadManifest
from yd_producer.rawcopy import (
    ERROR_KINDS,
    RawStagingError,
    stage_raw,
)
from yd_producer.rawscan import judge

# --- Row：incomplete verdict 零写入 -------------------------------------------


def test_incomplete_verdict_refuses_with_zero_writes(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    (cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)).unlink()
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    assert verdict.complete is False
    before = snapshot(work_dir)
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "incomplete-verdict")
    assert snapshot(work_dir) == before == {}


# --- Row：R4B2 三条 fail-closed ----------------------------------------------


def _manifest_without_selector_key(key: str, *, variable: str = "apcp"):
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        if entry["variable"] == variable:
            entry["metadata"]["idx_selectors"][variable].pop(key, None)
    return payload


def test_missing_apcp_accumulation_type_fails_closed(tmp_path: Path) -> None:
    payload = _manifest_without_selector_key("accumulation_type")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    before = snapshot(work_dir)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "accumulation-metadata")
    assert snapshot(work_dir) == before == {}


def test_out_of_domain_accumulation_type_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        if entry["variable"] == "apcp":
            entry["metadata"]["idx_selectors"]["apcp"]["accumulation_type"] = "unknown"
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    before = snapshot(work_dir)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "accumulation-metadata")
    assert snapshot(work_dir) == before == {}


def test_interval_bucket_without_step_range_fails_closed(tmp_path: Path) -> None:
    payload = _manifest_without_selector_key("step_range")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "accumulation-metadata")
    assert snapshot(work_dir) == {}


def test_accumulation_aliases_are_accepted(tmp_path: Path) -> None:
    """别名 `accumulation_policy`/`stepRange` 同样可满足 R4B2（域检查覆盖别名）。"""
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        selector = entry["metadata"]["idx_selectors"][entry["variable"]]
        selector["accumulation_policy"] = selector.pop("accumulation_type")
        selector["stepRange"] = selector.pop("step_range")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    assert len(result.entries) == len(LEADS) * len(GFS_VARIABLES)


def test_cumulative_since_cycle_without_step_range_is_accepted(tmp_path: Path) -> None:
    """只有 `interval_bucket` 才要求区间范围。"""
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        selector = entry["metadata"]["idx_selectors"][entry["variable"]]
        selector["accumulation_type"] = "cumulative_since_cycle"
        selector.pop("step_range")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    assert len(result.entries) == len(LEADS) * len(GFS_VARIABLES)


# --- Row：源 manifest 相关 fail-closed ---------------------------------------


def test_missing_manifest_level_forecast_hours_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    payload["metadata"].pop("forecast_hours")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_non_list_forecast_hours_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    # 取一个**逐字符都可转成 lead** 的字符串：若类型闸门退化成「可迭代即可」，
    # 该值会一路走到成功产出，本用例才是 list 类型闸门自己的判别器。
    payload["metadata"]["forecast_hours"] = "036"
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")


def test_forecast_hours_not_covering_a_lead_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs", declared_hours=(0, 3))
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_superset_forecast_hours_is_accepted(tmp_path: Path) -> None:
    """反向不要求：源可以比 yd 要的多。"""
    payload = source_manifest_payload("gfs", declared_hours=(0, 3, 6, 9))
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    written = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert written["metadata"]["forecast_hours"] == [0, 3, 6]


def test_absent_source_manifest_fails_closed(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path, write_manifest=False)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_unparsable_source_manifest_fails_closed(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    write_source_manifest(raw_root, "gfs", "{not json")
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")


def test_source_entries_not_covering_a_variable_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    payload["entries"] = [e for e in payload["entries"] if e["variable"] != "rh2m"]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_missing_carried_metadata_key_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        entry["metadata"].pop("cfgrib_filter_by_keys")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")


# --- Row：单 bundle 约束 ------------------------------------------------------


def test_two_bundle_layout_is_refused_with_zero_writes(tmp_path: Path) -> None:
    config = make_config(gfs=make_source(bundles=(GFS_BUNDLE, GFS_SECOND_BUNDLE)))
    raw_root, work_dir = build_tree(tmp_path)
    base = cycle_dir(raw_root, "gfs")
    for lead in LEADS:
        second_name = GFS_SECOND_BUNDLE.replace("{cycle_hour}", "00").replace(
            "{lead}", f"{lead:03d}"
        )
        (base / second_name).write_bytes(bundle_bytes(lead))
    verdict = judge(raw_root, "gfs", CYCLE, config)
    assert verdict.complete is True
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "unsupported-layout")
    assert snapshot(work_dir) == {}


# --- kind 词表与异常面 -------------------------------------------------------


def test_error_kinds_equals_the_closed_nine_fixture_literals() -> None:
    # Fixture: m2-producer-core/tasks.md 任务 3.2 闭合词表（恰好九项）。
    assert ERROR_KINDS == frozenset(
        {
            "incomplete-verdict",
            "unsupported-layout",
            "source-symlink",
            "source-manifest",
            "verdict-mismatch",
            "accumulation-metadata",
            "source-mutated",
            "target-exists",
            "copy-failed",
        }
    )


def test_no_bare_stdlib_exception_escapes_for_each_failure_shape(
    tmp_path: Path,
) -> None:
    """九项 kind 之外，`stage_raw` MUST NOT 外泄裸 OSError/KeyError/JSONDecodeError。"""
    raw_root, work_dir = build_tree(tmp_path)
    write_source_manifest(raw_root, "gfs", "[]")
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert not isinstance(excinfo.value, OSError | KeyError | json.JSONDecodeError)


@pytest.mark.parametrize(
    ("source", "cycle"),
    [
        ("GFS", CYCLE),
        ("era5", CYCLE),
        ("gfs", datetime(2026, 3, 4, 0)),  # noqa: DTZ001 naive 是被测输入
        # 整点闸门是 (minute, second, microsecond) 三元组：逐分量各一行，一个只动
        # minute 的输入不足以为三分量的闸门背书。
        ("gfs", datetime(2026, 3, 4, 0, 30, tzinfo=UTC)),
        ("gfs", datetime(2026, 3, 4, 0, 0, 30, tzinfo=UTC)),
        ("gfs", datetime(2026, 3, 4, 0, 0, 0, 30, tzinfo=UTC)),
        # tz 闸门是 `utcoffset() != timedelta(0)`：naive（上面那行，utcoffset 为
        # None）与「aware 但非 UTC」是两条不同的腿。
        ("gfs", datetime(2026, 3, 4, 0, tzinfo=timezone(timedelta(hours=8)))),
    ],
)
def test_malformed_call_parameters_raise_config_error(
    tmp_path: Path, source: str, cycle: datetime
) -> None:
    """形参写错归 `ConfigError`（「配置写错了」），不占九项 kind 的名额。"""
    raw_root, work_dir = build_tree(tmp_path)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, work_dir, source, cycle, config)
    assert snapshot(work_dir) == {}


# --- 闸门审计补洞：值传播闸门与承接分支的判别器 ------------------------------


def test_non_numeric_forecast_hours_entry_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    payload["metadata"]["forecast_hours"] = [0, 3, "six"]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_non_integer_typed_forecast_hours_entry_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    payload["metadata"]["forecast_hours"] = [0, 3, None]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")


def test_source_manifest_without_entries_key_fails_closed(tmp_path: Path) -> None:
    """`DownloadManifest.from_dict` 的 `KeyError` MUST 收敛成 `source-manifest`。

    同时是形态闸门 `if not isinstance(entries, list): return` 这条腿的判别器：该腿的
    语义是「结构面不归本闸门，交给下一步的结构闸门」。判别器取在**消息**上——kind
    两边相同（地板兜底也给 `source-manifest`），去掉该腿后 `enumerate(None)` 的
    `TypeError` 会掉进准入地板，消息随之从具名的结构诊断退化成泛化的兜底诊断。
    """
    payload = source_manifest_payload("gfs")
    payload.pop("entries")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert STRUCTURE_MESSAGE in str(excinfo.value)
    assert FLOOR_MESSAGE not in str(excinfo.value)


def test_apcp_without_any_selector_mapping_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        if entry["variable"] == "apcp":
            entry["metadata"]["idx_selectors"].pop("apcp", None)
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "accumulation-metadata")
    assert snapshot(work_dir) == {}


def test_variable_absent_from_idx_selectors_omits_the_singular_key(
    tmp_path: Path,
) -> None:
    """非 apcp 变量在复数键里缺席 -> 单数键**缺席**，MUST NOT 写空 Mapping。"""
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        entry["metadata"]["idx_selectors"] = {
            k: v for k, v in entry["metadata"]["idx_selectors"].items() if k != "rh2m"
        }
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    written = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    for entry in written["entries"]:
        if entry["variable"] == "rh2m":
            assert "idx_selector" not in entry["metadata"]
            assert set(entry["metadata"]) == {*CARRIED_KEYS, "idx_selectors"}
        else:
            assert entry["metadata"]["idx_selector"] == selector_for(
                entry["variable"], entry["forecast_hour"]
            )


def _handmade_verdict(raw_root: Path, source: str, leads, variables):
    from yd_producer.rawscan import ScanVerdict

    files = tuple(
        cycle_dir(raw_root, source) / bundle_name(source, lead) for lead in leads
    )
    return ScanVerdict(
        complete=True,
        expected_files=files,
        missing_files=(),
        unreadable_files=(),
        expected_variables={lead: tuple(variables) for lead in leads},
    )


def test_verdict_missing_a_lead_variable_set_is_rejected(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    verdict = _handmade_verdict(raw_root, "gfs", LEADS, GFS_VARIABLES)
    broken = type(verdict)(
        complete=True,
        expected_files=verdict.expected_files,
        missing_files=(),
        unreadable_files=(),
        expected_variables={0: GFS_VARIABLES, 6: GFS_VARIABLES},
    )
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(broken, raw_root, work_dir, "gfs", CYCLE, make_config())
    expect_kind(excinfo, "verdict-mismatch")
    assert snapshot(work_dir) == {}


def test_bundle_pattern_validation_is_reused_from_rawscan(tmp_path: Path) -> None:
    """渲染面复用的判别器：模式非法时 staging 走 `rawscan` 的 `ConfigError`。"""
    raw_root, work_dir = build_tree(tmp_path)
    config = make_config(gfs=make_source(bundles=("no-lead-field.grib2",)))
    verdict = _handmade_verdict(raw_root, "gfs", LEADS, GFS_VARIABLES)
    with pytest.raises(ConfigError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    assert excinfo.value.path == "raw.gfs.bundles"
    assert snapshot(work_dir) == {}


# --- Row：verdict 多出一个 lead（集合相等的另一个方向）----------------------


def test_verdict_with_an_extra_lead_is_rejected(tmp_path: Path) -> None:
    """`expected_variables` 多出一个 lead 也是不同源。

    少一个 lead 的方向由 `test_verdict_missing_a_lead_variable_set_is_rejected` 钉住；
    本用例钉另一个方向：多出的 lead 会让产出 manifest 的
    `forecast_hours == requested_forecast_hours == sorted(expected_variables)` 三键
    相等（tasks.md:677/:708）静默失真——`forecast_hours` 由重构的 lead 推导，比
    verdict 声明的少。
    """
    raw_root, work_dir = build_tree(tmp_path)
    verdict = _handmade_verdict(raw_root, "gfs", LEADS, GFS_VARIABLES)
    broken = type(verdict)(
        complete=True,
        expected_files=verdict.expected_files,
        missing_files=(),
        unreadable_files=(),
        expected_variables={
            **{lead: GFS_VARIABLES for lead in LEADS},
            99: GFS_VARIABLES,
        },
    )
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(broken, raw_root, work_dir, "gfs", CYCLE, make_config())
    expect_kind(excinfo, "verdict-mismatch")
    assert snapshot(work_dir) == {}


@pytest.mark.parametrize(
    "bad_value",
    [
        None,  # 不可迭代：留着会漏一个裸 TypeError
        5,  # 同上
        "tmp2m",  # 可迭代但更糟：逐字符扇出，静默产出变量名全错的 manifest
    ],
)
def test_verdict_lead_with_a_malformed_variable_set_is_rejected(
    tmp_path: Path, bad_value: Any
) -> None:
    """键集相等但值的形态不对：集合相等判不了值，仍需逐个判值面形态。"""
    raw_root, work_dir = build_tree(tmp_path)
    verdict = _handmade_verdict(raw_root, "gfs", LEADS, GFS_VARIABLES)
    broken = type(verdict)(
        complete=True,
        expected_files=verdict.expected_files,
        missing_files=(),
        unreadable_files=(),
        expected_variables={0: GFS_VARIABLES, 3: bad_value, 6: GFS_VARIABLES},
    )
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(broken, raw_root, work_dir, "gfs", CYCLE, make_config())
    expect_kind(excinfo, "verdict-mismatch")
    assert snapshot(work_dir) == {}


# --- Row：forecast_hours 逐项类型闸门的逐分量判别器 --------------------------


def test_bool_forecast_hours_entry_fails_closed(tmp_path: Path) -> None:
    """`True` 是 `int` 的子类：`isinstance(value, bool)` 这条析取分支的判别器。

    取值必须是**超集** `[0, 3, 6, True]`：`True` 会静默变成 `1`，若写成
    `[0, 3, True]` 则覆盖检查会先因缺 lead 6 而拒绝，本闸门照样无判别力。
    """
    payload = source_manifest_payload("gfs")
    payload["metadata"]["forecast_hours"] = [0, 3, 6, True]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_numeric_string_forecast_hours_are_accepted(tmp_path: Path) -> None:
    """`int | str` 联合的 `str` 分量：数字字符串是**合法**取值，MUST NOT 被拒。"""
    payload = source_manifest_payload("gfs")
    payload["metadata"]["forecast_hours"] = [0, 3, "6"]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    assert len(result.copied_files) == len(LEADS)


# --- Row：源 manifest 结构异常的逐分支收敛（多类型 except 元组的逐分量判别器）--


def test_non_utf8_source_manifest_bytes_fail_closed(tmp_path: Path) -> None:
    """源 manifest 不是合法 UTF-8 -> `UnicodeDecodeError` 收敛成 `source-manifest`。

    字节要挑**任何编码下都非法**的：以 `\xff\xfe` 开头会被 `json.detect_encoding`
    当成 UTF-16 BOM 解码成功，于是走的是 `JSONDecodeError` 腿而不是本用例要钉的
    解码腿（实测：那种输入下「只留 JSONDecodeError」的变异体存活）。
    """
    raw_root, work_dir = build_tree(tmp_path)
    (cycle_dir(raw_root, "gfs") / SOURCE_MANIFEST_NAME).write_bytes(
        b'{"source_id": "\xff\xfe\xfdgfs"}'
    )
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


@pytest.mark.parametrize(
    ("key", "value", "leaked"),
    [
        # `DownloadManifest.from_dict` 对同一份坏 manifest 会抛四种不同的裸异常，
        # 每种是 except 元组里的一个**独立分量**：少写一个就有一种直接外泄。
        ("cycle_time", "nope", ValueError),  # datetime.fromisoformat
        ("entries", 5, TypeError),  # 不可迭代
        ("cycle_time", 5, AttributeError),  # int 没有 .strip
    ],
)
def test_structurally_broken_source_manifest_never_leaks_a_bare_exception(
    tmp_path: Path, key: str, value: Any, leaked: type[Exception]
) -> None:
    payload = source_manifest_payload("gfs")
    payload[key] = value
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    # 前提取证：这份输入确实会让底层原语抛出被点名的那个裸异常。
    with pytest.raises(leaked):
        DownloadManifest.from_dict(dict(payload))
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert not isinstance(excinfo.value, leaked)
    assert snapshot(work_dir) == {}


# --- Row：外部 JSON 的值形态（不可哈希值 MUST NOT 漏裸 TypeError）------------


@pytest.mark.parametrize("bad_value", [["interval_bucket"], {"a": 1}])
def test_unhashable_accumulation_type_fails_closed(
    tmp_path: Path, bad_value: Any
) -> None:
    """`"accumulation_type": ["interval_bucket"]` 是合法 JSON，反序列化成不可哈希值。

    闸门写成 `x not in frozenset(...)` 时求哈希抛裸 `TypeError`，而 `_build_entries`
    整段在 `stage_raw` 的 try 块**之前**，三层 handler 一条也接不到。§3.1 对该字段
    无任何类型约束，「pin 不会写 list」是对生成器的观察、不是对落盘 JSON 的保证。
    """
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        if entry["variable"] == "apcp":
            entry["metadata"]["idx_selectors"]["apcp"]["accumulation_type"] = bad_value
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    # 前提取证：这份取值确实不可哈希（求哈希即裸 TypeError）。
    with pytest.raises(TypeError):
        hash(bad_value)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "accumulation-metadata")
    assert not isinstance(excinfo.value, TypeError)
    assert snapshot(work_dir) == {}


def test_unhashable_entry_variable_fails_closed(tmp_path: Path) -> None:
    """同类的另一个出口：`ManifestEntry.from_dict` 不强制 `variable` 的类型，

    一个 `"variable": ["apcp"]` 会在按 (lead, variable) 建索引时让 `dict` 求哈希抛裸
    `TypeError`——同样在 try 块之前。
    """
    payload = source_manifest_payload("gfs")
    source_entry(payload, LEADS[0], GFS_VARIABLES[0])["variable"] = ["apcp"]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    # 前提取证：坏值确实穿过了 `from_dict`（它只强制 forecast_hour/metadata）。
    assert ["apcp"] in [
        entry.variable for entry in DownloadManifest.from_dict(dict(payload)).entries
    ]
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert not isinstance(excinfo.value, TypeError)
    assert snapshot(work_dir) == {}


# --- Row：lead_hours 的排序 / verdict 变量集的 list 分量 ----------------------


def test_unsorted_lead_hours_stage_normally(tmp_path: Path) -> None:
    """`lead_hours=(6, 0, 3)` 是合法配置（装载器不排序也不要求有序）。

    `_reconstruct_sources` 的 `sorted(lead_hours)` 是活闸门而不是展示用排序：去掉它
    时该合法调用会被 `verdict-mismatch` 误拒（`judge` 侧的 `_expected_leads` 排序）。
    """
    config = make_config(gfs=make_source(lead_hours=(6, 0, 3)))
    raw_root, work_dir = build_tree(tmp_path)
    result = staged(raw_root, work_dir, "gfs", config)
    assert result.copied_files == tuple(
        work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", lead)
        for lead in LEADS
    )
    written = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert written["metadata"]["forecast_hours"] == [0, 3, 6]


def test_verdict_lead_variable_set_as_a_list_stages_normally(tmp_path: Path) -> None:
    """值形态闸门的 `list` 分量：`isinstance(variables, tuple | list)` 的右半。

    该分量声明「`list` 值集是合法输入」，但既有三个坏输入（`None`/`5`/`"tmp2m"`）对
    两个分量咬合相同，把闸门收窄成只认 `tuple` 的变异体照样全绿。
    """
    raw_root, work_dir = build_tree(tmp_path)
    verdict = _handmade_verdict(raw_root, "gfs", LEADS, GFS_VARIABLES)
    as_list = type(verdict)(
        complete=True,
        expected_files=verdict.expected_files,
        missing_files=(),
        unreadable_files=(),
        expected_variables={lead: list(GFS_VARIABLES) for lead in LEADS},
    )
    result = stage_raw(as_list, raw_root, work_dir, "gfs", CYCLE, make_config())
    assert {(e.forecast_hour, e.variable) for e in result.entries} == {
        (lead, var) for lead in LEADS for var in GFS_VARIABLES
    }
