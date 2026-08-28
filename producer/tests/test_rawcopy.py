"""`yd_producer.rawcopy.stage_raw` 只读复制与临时 raw manifest 测试（任务 3.2）。

全部用例使用**内联合成配置值与合成源 manifest**：仓库刻意不提供版本化 `config.toml`
生产实例（归 issue #29），下方的变量名、bundle 模式与 manifest 取值只用于行使 staging
规则，不代表生产取值。

期望值一律在用例内**字面构造**：目录段、`local_key` 形态、承接键名、manifest 级四键
都不从被测模块 import——两侧共用一个字面量会让断言随实现同步漂移，退化成恒真式。
事实来源是 NWM@8ae9b8f2 与勘察清单 §3.1（`SOURCE_DIR_NAMES` 的逐源非对称、
`raw/{source_id}/{compact_cycle}/{bundle}` 的 key 形态、entry metadata 六键、
`idx_selector(s)` 的单复数分工、manifest 级四键）。
"""

import errno
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from yd_producer.config import (
    Config,
    ConfigError,
    CycleConfig,
    RawConfig,
    RawSourceConfig,
    SlurmSchema,
    VariantsConfig,
)
from yd_producer.raw.manifest import DownloadManifest
from yd_producer.rawcopy import RawStagingError, stage_raw
from yd_producer.rawscan import judge
from yd_producer.store.object_store import LocalObjectStore

# --- 内联合成 fixture --------------------------------------------------------

CYCLE = datetime(2026, 3, 4, 0, tzinfo=UTC)
CYCLE_DIR = "2026030400"
CYCLE_ISO = "2026-03-04T00:00:00+00:00"

# 入参 source → raw 目录段/存储身份。**逐字写死**，MUST NOT 从被测模块 import
# `SOURCE_DIR_NAMES`。事实来源：NWM@8ae9b8f2 packages/common/source_identity.py:5-9
# 的 `_STORAGE_SOURCE_IDS = {"GFS": "gfs", "ERA5": "ERA5", "IFS": "IFS"}`。
DIR_SEGMENTS = {"ifs": "IFS", "gfs": "gfs"}

GFS_BUNDLE = "gfs.t{cycle_hour:02d}z.pgrb2.0p25.f{lead:03d}.bundle.grib2"
IFS_BUNDLE = "ifs.t{cycle_hour:02d}z.f{lead:03d}.bundle.grib2"
GFS_SECOND_BUNDLE = "gfs.t{cycle_hour:02d}z.pgrb2.0p25.f{lead:03d}.sfc.grib2"

LEADS = (0, 3, 6)
GFS_VARIABLES = ("tmp2m", "apcp", "rh2m", "dswrf")
IFS_VARIABLES = ("2t", "tp")

# 合成的 GRIB short name 表（只为行使承接，不代表 pin 取值）。
SHORT_NAMES = {
    "tmp2m": "2t",
    "apcp": "tp",
    "rh2m": "r2",
    "dswrf": "dswrf",
    "2t": "2t",
    "tp": "tp",
}

MANIFEST_NAME = "raw-manifest.json"
SOURCE_MANIFEST_NAME = "manifest.json"

# entry metadata 的六个承接键（NWM@8ae9b8f2 gfs_adapter.py:623-634）。
CARRIED_KEYS = (
    "cycle_time",
    "valid_time",
    "bundle",
    "grib_short_name",
    "cfgrib_filter_by_keys",
    "logical_remote_url",
)


def make_source(
    *,
    lead_hours=LEADS,
    variables=GFS_VARIABLES,
    bundles=(GFS_BUNDLE,),
    f000_special=False,
) -> RawSourceConfig:
    return RawSourceConfig(
        lead_hours=tuple(lead_hours),
        variables=tuple(variables),
        bundles=tuple(bundles),
        f000_special=f000_special,
    )


def make_config(
    *,
    ifs: RawSourceConfig | None = None,
    gfs: RawSourceConfig | None = None,
) -> Config:
    return Config(
        forecast_days=7,
        output_interval_minutes=60,
        checkpoint_hours=(12,),
        reach_count=3988,
        nwm_mapping_builder_module="workers.mapping_builder.cli",
        cycle=CycleConfig(hours=(0, 12)),
        variants=VariantsConfig(gfs="input/models/yd_gfs", ifs="input/models/yd_ifs"),
        raw=RawConfig(
            ifs=ifs
            if ifs is not None
            else make_source(variables=IFS_VARIABLES, bundles=(IFS_BUNDLE,)),
            gfs=gfs if gfs is not None else make_source(),
        ),
        slurm=SlurmSchema(required_fields=("partition", "account")),
    )


def bundle_name(source: str, lead: int) -> str:
    pattern = GFS_BUNDLE if source == "gfs" else IFS_BUNDLE
    return pattern.format(cycle_hour=CYCLE.hour, lead=lead)


def cycle_dir(raw_root: Path, source: str) -> Path:
    return raw_root / DIR_SEGMENTS[source] / CYCLE_DIR


def local_key(source: str, lead: int) -> str:
    """pin 的 object-store key 形态：`raw/{source_id}/{compact_cycle}/{bundle}`
    （NWM@8ae9b8f2 gfs_adapter.py:615）。字面构造，不调用被测模块。
    """
    return f"raw/{DIR_SEGMENTS[source]}/{CYCLE_DIR}/{bundle_name(source, lead)}"


def selector_for(variable: str, lead: int) -> dict[str, Any]:
    """合成的 `IdxSelection.as_metadata()` 四键（NWM@8ae9b8f2 :248-258）。"""
    return {
        "step_range": f"{max(lead - 3, 0)}-{lead}",
        "accumulation_type": "interval_bucket",
        "idx_record_number": 7 + lead,
        "selector_warning": None,
    }


def entry_payload(source: str, lead: int, variable: str) -> dict[str, Any]:
    short_name = SHORT_NAMES[variable]
    remote = f"https://example.invalid/{DIR_SEGMENTS[source]}/{CYCLE_DIR}/"
    return {
        "remote_url": remote + bundle_name(source, lead),
        "local_key": local_key(source, lead),
        "variable": variable,
        "forecast_hour": lead,
        "expected_checksum": None,
        "expected_size_bytes": None,
        "metadata": {
            "cycle_time": CYCLE_ISO,
            "valid_time": (CYCLE + timedelta(hours=lead)).isoformat(),
            "bundle": {
                "layout": "per_forecast_hour",
                "variables": list(GFS_VARIABLES if source == "gfs" else IFS_VARIABLES),
                "physical_file_count": 1,
            },
            "grib_short_name": short_name,
            "cfgrib_filter_by_keys": {"shortName": short_name},
            "logical_remote_url": remote + bundle_name(source, lead),
        },
    }


def source_manifest_payload(
    source: str,
    *,
    leads=LEADS,
    variables=None,
    with_idx: bool | None = None,
    with_requested: bool = True,
    declared_hours=None,
) -> dict[str, Any]:
    """合成 NWM 落盘的源 `manifest.json`（DownloadManifest.as_dict 同形）。

    `with_idx` 默认按源形态取：GFS 带 `idx_selectors`（云镜像下载路径注入），IFS 不带
    （pin 侧 IFS 全文无 idx 键）。
    """
    variables = variables or (GFS_VARIABLES if source == "gfs" else IFS_VARIABLES)
    if with_idx is None:
        with_idx = source == "gfs"
    entries = []
    for lead in leads:
        selectors = {var: selector_for(var, lead) for var in variables}
        for variable in variables:
            payload = entry_payload(source, lead, variable)
            if with_idx:
                payload["metadata"]["idx_selectors"] = selectors
                payload["metadata"]["idx_selector"] = selectors[variable]
            entries.append(payload)
    metadata: dict[str, Any] = {
        "first_forecast_hour": min(leads),
        "last_forecast_hour": max(leads),
        "forecast_hours": list(declared_hours if declared_hours else leads),
    }
    if with_requested:
        metadata["requested_forecast_hours"] = list(leads)
    return {
        "source_id": DIR_SEGMENTS[source],
        "cycle_time": CYCLE_ISO,
        "manifest_uri": f"s3://nwm/raw/{DIR_SEGMENTS[source]}/{CYCLE_DIR}/manifest.json",
        "metadata": metadata,
        "entries": entries,
    }


def bundle_bytes(lead: int) -> bytes:
    # 非 UTF-8 前导字节：真实 bundle 是 GRIB2 二进制。
    return b"GRIB\xff\x00lead-%03d" % lead


def build_tree(
    tmp_path: Path,
    source: str = "gfs",
    *,
    leads=LEADS,
    manifest: dict[str, Any] | None = None,
    write_manifest: bool = True,
) -> tuple[Path, Path]:
    """铺一棵 raw fixture 树与一个空 work 根，返回 `(raw_root, work_dir)`。"""
    raw_root = tmp_path / "nwm-raw"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    base = cycle_dir(raw_root, source)
    base.mkdir(parents=True, exist_ok=True)
    for lead in leads:
        (base / bundle_name(source, lead)).write_bytes(bundle_bytes(lead))
    if write_manifest:
        payload = manifest if manifest is not None else source_manifest_payload(source)
        (base / SOURCE_MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")
    return raw_root, work_dir


def write_source_manifest(
    raw_root: Path, source: str, payload: dict[str, Any] | str
) -> None:
    target = cycle_dir(raw_root, source) / SOURCE_MANIFEST_NAME
    if isinstance(payload, str):
        target.write_text(payload, encoding="utf-8")
    else:
        target.write_text(json.dumps(payload), encoding="utf-8")


def snapshot(root: Path) -> dict[str, tuple[int, int, int, int]]:
    """递归快照：路径 -> `lstat` 元组 (size, mtime_ns, ino, mode)。

    零写入取证用它整棵比对，MUST NOT 只断言 `raw-manifest.json` 不存在——只查一个
    文件抓不到「建了目录」「落了半套副本」。
    """
    out: dict[str, tuple[int, int, int, int]] = {}
    for path in sorted(root.rglob("*")):
        status = os.lstat(path)
        out[str(path.relative_to(root))] = (
            status.st_size,
            status.st_mtime_ns,
            status.st_ino,
            status.st_mode,
        )
    return out


def content_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def staged(
    raw_root: Path,
    work_dir: Path,
    source: str = "gfs",
    config: Config | None = None,
    *,
    raw_root_arg=None,
):
    config = config or make_config()
    verdict = judge(raw_root, source, CYCLE, config)
    return stage_raw(
        verdict,
        raw_root if raw_root_arg is None else raw_root_arg,
        work_dir,
        source,
        CYCLE,
        config,
    )


def expect_kind(excinfo, kind: str) -> None:
    assert excinfo.value.kind == kind, f"实际 kind={excinfo.value.kind!r}"


# --- Row 1：正向产出（副本齐全 + 三元组集合相等 + 路径落在 work/raw/ 之下）----


def test_full_cycle_copies_files_and_manifest_triples_match(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    result = staged(raw_root, work_dir)

    expected_copies = tuple(
        work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", lead)
        for lead in LEADS
    )
    assert result.copied_files == expected_copies
    for lead, path in zip(LEADS, expected_copies, strict=True):
        assert path.read_bytes() == bundle_bytes(lead)
    assert result.manifest_path == work_dir / MANIFEST_NAME

    # 三元组完整性：集合**相等**（不是包含），两个方向都由本断言承担。
    expected_pairs = {(lead, var) for lead in LEADS for var in GFS_VARIABLES}
    assert {(e.forecast_hour, e.variable) for e in result.entries} == expected_pairs
    # 同一 (lead, bundle) 的全部变量 entry 共享同一个 local_key。
    for lead in LEADS:
        keys = {e.local_key for e in result.entries if e.forecast_hour == lead}
        assert keys == {local_key("gfs", lead)}
    # entry 顺序：lead 升序 × variables 声明序。
    assert [(e.forecast_hour, e.variable) for e in result.entries] == [
        (lead, var) for lead in LEADS for var in GFS_VARIABLES
    ]

    # `local_key` 是 object-store key，经 resolve_path 落在 work/raw/ 之下。
    store = LocalObjectStore(root=work_dir)
    for entry in result.entries:
        resolved = store.resolve_path(entry.local_key)
        assert resolved.is_file()
        assert (work_dir / "raw") in resolved.parents


def test_entries_missing_one_pair_would_break_set_equality(tmp_path: Path) -> None:
    """三元组集合相等的**两个方向**各自可判：少一条与多一条都不等于期望集。"""
    raw_root, work_dir = build_tree(tmp_path)
    result = staged(raw_root, work_dir)
    pairs = [(e.forecast_hour, e.variable) for e in result.entries]
    expected_pairs = {(lead, var) for lead in LEADS for var in GFS_VARIABLES}
    assert set(pairs[1:]) != expected_pairs  # 漏一条即不等
    assert {*pairs, (99, "apcp")} != expected_pairs  # 多一条即不等
    assert len(pairs) == len(set(pairs)) == len(expected_pairs)


# --- Row：产出 manifest 的正向 schema 断言 -----------------------------------


def test_manifest_json_matches_the_producer_consumer_contract(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    result = staged(raw_root, work_dir)
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    roundtrip = DownloadManifest.from_dict(payload)
    assert roundtrip.source_id == "gfs"  # 存储身份逐源非对称（GFS 小写）
    assert roundtrip.cycle_time == CYCLE
    assert payload["manifest_uri"] is None
    assert payload["metadata"] == {
        "first_forecast_hour": 0,
        "last_forecast_hour": 6,
        "requested_forecast_hours": [0, 3, 6],
        "forecast_hours": [0, 3, 6],
    }
    assert len(payload["entries"]) == len(LEADS) * len(GFS_VARIABLES)
    for entry in payload["entries"]:
        lead = entry["forecast_hour"]
        variable = entry["variable"]
        assert entry["local_key"] == local_key("gfs", lead)
        assert entry["expected_checksum"] is None
        assert entry["expected_size_bytes"] is None
        assert entry["remote_url"] == entry_payload("gfs", lead, variable)["remote_url"]
        metadata = entry["metadata"]
        assert set(metadata) == {*CARRIED_KEYS, "idx_selector", "idx_selectors"}
        for key in CARRIED_KEYS:
            assert (
                metadata[key] == entry_payload("gfs", lead, variable)["metadata"][key]
            )
        # 单数键是**该变量**的 Mapping，不是整个复数 Mapping。
        assert metadata["idx_selector"] == selector_for(variable, lead)
        assert set(metadata["idx_selectors"]) == set(GFS_VARIABLES)


def test_ifs_source_stages_without_any_idx_key(tmp_path: Path) -> None:
    """IFS 作用域证据：无 apcp、无 idx_selectors -> 正常产出，两个 idx 键**缺席**。"""
    raw_root, work_dir = build_tree(tmp_path, "ifs")
    result = staged(raw_root, work_dir, "ifs")
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert payload["source_id"] == "IFS"  # 存储身份逐源非对称（IFS 大写）
    assert {(e.forecast_hour, e.variable) for e in result.entries} == {
        (lead, var) for lead in LEADS for var in IFS_VARIABLES
    }
    for entry in payload["entries"]:
        assert set(entry["metadata"]) == set(CARRIED_KEYS)
    assert result.copied_files == tuple(
        work_dir / "raw" / "IFS" / CYCLE_DIR / bundle_name("ifs", lead)
        for lead in LEADS
    )


def test_source_without_requested_forecast_hours_still_stages(tmp_path: Path) -> None:
    """源侧只强制 `forecast_hours`；`requested_forecast_hours` 缺席（IFS 的 pin 形态）
    MUST NOT 失败，yd 自己写该键 = 本轮 lead 全集。
    """
    raw_root, work_dir = build_tree(
        tmp_path,
        "gfs",
        manifest=source_manifest_payload("gfs", with_requested=False),
    )
    result = staged(raw_root, work_dir)
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["requested_forecast_hours"] == [0, 3, 6]
    assert payload["metadata"]["forecast_hours"] == [0, 3, 6]


def test_gfs_f000_special_trims_variables_but_keeps_the_file(tmp_path: Path) -> None:
    config = make_config(gfs=make_source(f000_special=True))
    raw_root, work_dir = build_tree(tmp_path)
    result = staged(raw_root, work_dir, "gfs", config)
    lead0 = {e.variable for e in result.entries if e.forecast_hour == 0}
    assert lead0 == {"tmp2m", "rh2m"}  # apcp/dswrf 在 f000 无定义
    assert {e.variable for e in result.entries if e.forecast_hour == 3} == set(
        GFS_VARIABLES
    )
    # f000 只削变量集，不削文件集。
    assert work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 0) in set(
        result.copied_files
    )
    assert len(result.copied_files) == len(LEADS)


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
            entry["metadata"]["idx_selector"].pop(key, None)
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
            entry["metadata"]["idx_selector"]["accumulation_type"] = "unknown"
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
        selector = entry["metadata"]["idx_selector"]
        selector["accumulation_policy"] = selector.pop("accumulation_type")
        selector["stepRange"] = selector.pop("step_range")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    assert len(result.entries) == len(LEADS) * len(GFS_VARIABLES)


def test_cumulative_since_cycle_without_step_range_is_accepted(tmp_path: Path) -> None:
    """只有 `interval_bucket` 才要求区间范围。"""
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        selector = entry["metadata"]["idx_selector"]
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


# --- Row：verdict-mismatch 与其相对路径对照 ----------------------------------


def test_raw_root_from_another_call_site_is_rejected(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    other_root = tmp_path / "other-raw"
    other_base = cycle_dir(other_root, "gfs")
    other_base.mkdir(parents=True)
    for lead in LEADS:
        (other_base / bundle_name("gfs", lead)).write_bytes(bundle_bytes(lead))
    write_source_manifest(other_root, "gfs", source_manifest_payload("gfs"))
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, other_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "verdict-mismatch")
    assert snapshot(work_dir) == {}


def test_relative_raw_root_from_the_same_call_site_stages_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """containment 检查 MUST NOT 误拒合法的相对 `raw_root` 调用（同法提升的判别器）。"""
    _raw_root, work_dir = build_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    config = make_config()
    verdict = judge("nwm-raw", "gfs", CYCLE, config)
    result = stage_raw(verdict, "nwm-raw", work_dir, "gfs", CYCLE, config)
    assert len(result.copied_files) == len(LEADS)
    assert result.manifest_path.is_file()


# --- Row：单 bundle 约束 ------------------------------------------------------


def test_two_bundle_layout_is_refused_with_zero_writes(tmp_path: Path) -> None:
    config = make_config(gfs=make_source(bundles=(GFS_BUNDLE, GFS_SECOND_BUNDLE)))
    raw_root, work_dir = build_tree(tmp_path)
    base = cycle_dir(raw_root, "gfs")
    for lead in LEADS:
        (base / GFS_SECOND_BUNDLE.format(cycle_hour=0, lead=lead)).write_bytes(
            bundle_bytes(lead)
        )
    verdict = judge(raw_root, "gfs", CYCLE, config)
    assert verdict.complete is True
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "unsupported-layout")
    assert snapshot(work_dir) == {}


# --- Row：源侧 symlink（叶子与祖先段）----------------------------------------


def test_symlinked_bundle_is_refused_although_judge_says_complete(
    tmp_path: Path,
) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    base = cycle_dir(raw_root, "gfs")
    target = base / bundle_name("gfs", 3)
    real = base / "real-f003.grib2"
    target.rename(real)
    target.symlink_to(real)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    # 3.1/3.2 的有意不对称：judge 走 `is_file()`（跟随 symlink）判完整……
    assert verdict.complete is True
    # ……而 staging 拒绝为该链背书。
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "source-symlink")
    assert snapshot(work_dir) == {}


def test_symlinked_cycle_directory_segment_is_refused(tmp_path: Path) -> None:
    raw_root = tmp_path / "nwm-raw"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True)
    real_cycle = raw_root / "gfs" / "real-2026030400"
    real_cycle.mkdir(parents=True)
    for lead in LEADS:
        (real_cycle / bundle_name("gfs", lead)).write_bytes(bundle_bytes(lead))
    (real_cycle / SOURCE_MANIFEST_NAME).write_text(
        json.dumps(source_manifest_payload("gfs")), encoding="utf-8"
    )
    (raw_root / "gfs" / CYCLE_DIR).symlink_to(real_cycle, target_is_directory=True)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    assert verdict.complete is True
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "source-symlink")
    assert snapshot(work_dir) == {}


# --- Row：目标已存在 ---------------------------------------------------------


def test_existing_target_file_is_never_overwritten(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    target = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"pre-existing")
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "target-exists")
    assert target.read_bytes() == b"pre-existing"
    assert not (work_dir / MANIFEST_NAME).exists()
    assert sorted(p.name for p in target.parent.iterdir()) == [target.name]


def test_existing_raw_manifest_is_never_overwritten(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    (work_dir / MANIFEST_NAME).write_text("stale", encoding="utf-8")
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "target-exists")
    assert (work_dir / MANIFEST_NAME).read_text(encoding="utf-8") == "stale"
    assert not (work_dir / "raw").exists()


# --- Row：复制期失败的两条清理路径 -------------------------------------------


def test_source_mutated_during_copy_leaves_no_partial_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 k 个源文件在复制窗口内被替换 -> `source-mutated`，work 侧不留半套。

    注入点选在 `os.lstat`：当第 k 个副本已经落盘（即该文件的复制已开始）时，把源
    文件真实改掉——复制后的那次 `lstat` 于是自然拿到不同的元组。这不绕过被测闸门，
    被测的是「前后元组比对」本身。
    """
    raw_root, work_dir = build_tree(tmp_path)
    victim = cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)
    victim_copy = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)
    real_lstat = os.lstat
    state = {"done": False}

    def hooked_lstat(path, *args, **kwargs):
        if (
            not state["done"]
            and str(path) == str(victim)
            and os.path.exists(victim_copy)
        ):
            state["done"] = True
            # 等长替换：size 不变，只有 mtime_ns 变——这是「只比对内容/大小不算」
            # 的判别器（`_identity` 若退化成只比 size，本用例即无法变红）。
            victim.write_bytes(b"GRIB\xff\x00lead-XXX")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", hooked_lstat)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    expect_kind(excinfo, "source-mutated")
    assert state["done"] is True
    assert snapshot(work_dir) == {}


def test_copy_failure_leaves_no_partial_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 k 个文件的目标不可写（ENOSPC/权限）-> `copy-failed`，work 侧不留半套。"""
    raw_root, work_dir = build_tree(tmp_path)
    doomed = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 6)
    real_open = os.open

    def hooked_open(path, flags, *args, **kwargs):
        if str(path) == str(doomed):
            raise OSError(errno.ENOSPC, "No space left on device", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", hooked_open)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    expect_kind(excinfo, "copy-failed")
    assert snapshot(work_dir) == {}


def test_unwritable_work_directory_reports_copy_failed(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    mode = work_dir.stat().st_mode
    os.chmod(work_dir, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(RawStagingError) as excinfo:
            staged(raw_root, work_dir)
        expect_kind(excinfo, "copy-failed")
    finally:
        os.chmod(work_dir, mode)
    assert snapshot(work_dir) == {}


# --- Row：不变的兄弟面（源树与 YD_ROOT 模拟根）-------------------------------


def test_source_tree_is_byte_and_metadata_identical_after_staging(
    tmp_path: Path,
) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    before = snapshot(raw_root)
    before_content = content_snapshot(raw_root)
    staged(raw_root, work_dir)
    assert snapshot(raw_root) == before
    assert content_snapshot(raw_root) == before_content


def test_yd_root_mock_is_untouched_and_holds_no_raw_copy(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    yd_root = tmp_path / "yd-root"
    (yd_root / "output").mkdir(parents=True)
    (yd_root / "output" / "keep.txt").write_text("published", encoding="utf-8")
    before = snapshot(yd_root)
    staged(raw_root, work_dir)
    assert snapshot(yd_root) == before
    assert list(yd_root.rglob("raw")) == []
    assert list(yd_root.rglob("*.grib2")) == []


# --- kind 词表与异常面 -------------------------------------------------------


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
        ("gfs", datetime(2026, 3, 4, 0, 30, tzinfo=UTC)),
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
    """`DownloadManifest.from_dict` 的 `KeyError` MUST 收敛成 `source-manifest`。"""
    payload = source_manifest_payload("gfs")
    payload.pop("entries")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")


def test_apcp_without_any_selector_mapping_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        if entry["variable"] == "apcp":
            entry["metadata"].pop("idx_selector")
            entry["metadata"]["idx_selectors"].pop("apcp")
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
        if entry["variable"] == "rh2m":
            entry["metadata"].pop("idx_selector")
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
