"""`yd_producer.rawcopy.stage_raw` 只读复制与临时 raw manifest 测试（任务 3.2）。

本模块每个用例仍使用**内联合成配置值与合成源 manifest**。下方的本地变量名、bundle
模式、lead 与 manifest 取值只用于行使本模块的 staging 规则，不代表生产取值。

版本化生产实例由 `producer/tests/test_config.py` 独立验证；本模块 MUST NOT 从它派生
oracle（包括配置值、bundle 模式、lead 或 manifest 取值）。

期望值一律在用例内**字面构造**：目录段、`local_key` 形态、承接键名、manifest 级四键
都不从被测模块 import——两侧共用一个字面量会让断言随实现同步漂移，退化成恒真式。
事实来源是 NWM@8ae9b8f2 与勘察清单 §3.1（`SOURCE_DIR_NAMES` 的逐源非对称、
`raw/{source_id}/{compact_cycle}/{bundle}` 的 key 形态、entry metadata 六键、
`idx_selector(s)` 的单复数分工、manifest 级四键）。
"""

import json
from pathlib import Path

from rawcopy_fixtures import (
    CARRIED_KEYS,
    CYCLE,
    CYCLE_DIR,
    GFS_VARIABLES,
    IFS_VARIABLES,
    LEADS,
    MANIFEST_NAME,
    SOURCE_BUNDLE_TOKEN,
    SOURCE_CFGRIB_TOKEN,
    SOURCE_IDX_TOKEN,
    SOURCE_IDX_TOKEN_KEY,
    SOURCE_SHORT_NAME_TOKEN,
    SOURCE_TIME_SUFFIX,
    SOURCE_UNCARRIED_METADATA_KEY,
    build_tree,
    bundle_bytes,
    bundle_name,
    entry_payload,
    local_key,
    make_config,
    make_source,
    selector_for,
    source_entry,
    source_manifest_payload,
    staged,
)

from yd_producer.raw.manifest import DownloadManifest
from yd_producer.store.object_store import LocalObjectStore

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
    # 同一 (lead, bundle) 的全部变量 entry 共享同一个 local_key，且该 key 由 yd
    # **自己算**：源 manifest 的同名字段带 `nwm-bucket/` 前缀，照抄源就会取到它。
    for lead in LEADS:
        keys = {e.local_key for e in result.entries if e.forecast_hour == lead}
        assert keys == {local_key("gfs", lead)}
        assert keys != {entry_payload("gfs", lead, "tmp2m")["local_key"]}
    assert [
        entry.local_key for entry in result.entries if entry.variable == "tmp2m"
    ] == [
        "raw/gfs/2026030400/gfs.t00z.pgrb2.0p25.f000.bundle.grib2",
        "raw/gfs/2026030400/gfs.t00z.pgrb2.0p25.f003.bundle.grib2",
        "raw/gfs/2026030400/gfs.t00z.pgrb2.0p25.f006.bundle.grib2",
    ]
    # entry 顺序：lead 升序 × variables 声明序。
    assert [(e.forecast_hour, e.variable) for e in result.entries] == [
        (lead, var) for lead in LEADS for var in GFS_VARIABLES
    ]

    # `local_key` 是 object-store key，**每一条**经 resolve_path 都落在 work/raw/ 之下
    # 且指向已存在的副本。在发散前缀的 fixture 下这条断言才有判别力：照抄源 key 的
    # 实现会解析到 `<work>/nwm-bucket/raw/...`，既不在 work/raw 之下也不存在。
    store = LocalObjectStore(root=work_dir)
    assert len(result.entries) == len(LEADS) * len(GFS_VARIABLES)
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
    assert {entry["local_key"] for entry in payload["entries"]} == {
        "raw/IFS/2026030400/ifs.t00z.f000.bundle.grib2",
        "raw/IFS/2026030400/ifs.t00z.f003.bundle.grib2",
        "raw/IFS/2026030400/ifs.t00z.f006.bundle.grib2",
    }


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


# --- Row：manifest 级四键由 yd 自算（first/last 两端都发散）------------------


def test_manifest_level_hour_keys_are_self_computed_not_copied(tmp_path: Path) -> None:
    """源侧小时表在**两端**都比本轮 lead 宽：first 与 last 各自可判。

    默认 fixture 只让 last 发散（源侧 `[0,3,6,9,12]` vs 本轮 `[0,3,6]`），照抄源侧
    `first_forecast_hour` 的实现在那份输入上取值恰好重合。这里把本轮 lead 收成
    `(3, 6)`、源侧仍从 0 起，四键全部发散。
    """
    leads = (3, 6)
    config = make_config(gfs=make_source(lead_hours=leads))
    payload = source_manifest_payload("gfs", leads=leads, declared_hours=(0, 3, 6, 9))
    raw_root, work_dir = build_tree(tmp_path, leads=leads, manifest=payload)
    result = staged(raw_root, work_dir, "gfs", config)
    written = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert written["metadata"] == {
        "first_forecast_hour": 3,
        "last_forecast_hour": 6,
        "requested_forecast_hours": [3, 6],
        "forecast_hours": [3, 6],
    }
    # 前提取证：源侧四键与上面每一项都不同（否则本用例判别不了「照抄」）。
    assert payload["metadata"] == {
        "first_forecast_hour": 0,
        "last_forecast_hour": 9,
        "forecast_hours": [0, 3, 6, 9],
        "requested_forecast_hours": [0, 3, 6, 9],
    }


# --- Row：oracle 类的**闭合谓词**，按谓词清扫而不是逐条打补丁 -----------------
#
# 谓词（round-3 batch-C verifier 写下）：**凡测试对产出 `raw-manifest.json` 断言的每
# 一个值，源侧对应值 MUST 被偏移使「承接自源」与「由 yd 自算」发散。** round 1 找到
# 1 条腿、round 2 找到 6 条、round 3 又找到 2 条——一个每次独立清扫都还能吐出新实例
# 的类是被搜刮空了，不是闭合了。本用例把谓词本身写成断言：它对**产出 manifest 的
# 全部断言面**逐项核对源侧取值确实不同，于是将来任何一次 fixture 漂移让两侧重新
# 重合，都会在这里变红，而不是靠下一位 reviewer 去猜还有没有第 N+1 条腿。


def test_every_asserted_manifest_value_diverges_from_its_source_side_value(
    tmp_path: Path,
) -> None:
    # 本轮 lead 取 `(3, 6)` 而源侧小时表从 0 起到 9 止：四个小时键在**两端**都发散。
    # 默认 fixture 只让 last 一端发散，`first_forecast_hour` 两侧恰好都是 0——那正是
    # 本谓词要抓的重合形态，故清扫必须在两端都发散的输入上做。
    leads = (3, 6)
    config = make_config(gfs=make_source(lead_hours=leads))
    payload = source_manifest_payload("gfs", leads=leads, declared_hours=(0, 3, 6, 9))
    raw_root, work_dir = build_tree(tmp_path, leads=leads, manifest=payload)
    result = staged(raw_root, work_dir, "gfs", config)
    produced = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    # --- manifest 级 ---
    assert produced["source_id"] != payload["source_id"]
    assert produced["cycle_time"] != payload["cycle_time"]
    assert produced["manifest_uri"] != payload["manifest_uri"]
    for key in (
        "first_forecast_hour",
        "last_forecast_hour",
        "requested_forecast_hours",
        "forecast_hours",
    ):
        assert produced["metadata"][key] != payload["metadata"][key], key
    # 「仅含」半边：源侧 manifest 级 metadata 与产出侧**整体**不等（上面四键逐条不等
    # 已蕴含），此处显式记明它由 `==` 全等断言承担，不另设偏移键。
    assert produced["metadata"] != payload["metadata"]

    # --- entry 集合与顺序 ---
    assert len(produced["entries"]) != len(payload["entries"])
    produced_pairs = [(e["forecast_hour"], e["variable"]) for e in produced["entries"]]
    source_pairs = [(e["forecast_hour"], e["variable"]) for e in payload["entries"]]
    assert set(produced_pairs) != set(source_pairs)  # 集合由 verdict 定，不是照搬
    common = [pair for pair in source_pairs if pair in set(produced_pairs)]
    assert common != produced_pairs  # 顺序由 verdict 定，不是照搬源侧顺序

    # --- entry 级 ---
    for entry in produced["entries"]:
        lead, variable = entry["forecast_hour"], entry["variable"]
        origin = source_entry(payload, lead, variable)
        assert entry["local_key"] != origin["local_key"]
        assert entry["expected_checksum"] != origin["expected_checksum"]
        assert entry["expected_size_bytes"] != origin["expected_size_bytes"]
        metadata, source_metadata = entry["metadata"], origin["metadata"]
        # 「仅含」半边：源侧带一个非承接键，整份照抄会带上它。
        assert SOURCE_UNCARRIED_METADATA_KEY in source_metadata
        assert SOURCE_UNCARRIED_METADATA_KEY not in metadata
        # entry 级 `cycle_time`/`valid_time` 反过来 MUST 逐字承接：可判别性来自
        # 「自算的实现会写 `+00:00`」，故断言的是**与自算写法**不同。
        for key in ("cycle_time", "valid_time"):
            assert metadata[key] == source_metadata[key]
            assert metadata[key].endswith(SOURCE_TIME_SUFFIX)
            assert not metadata[key].endswith("+00:00")
        # `bundle`/`cfgrib_filter_by_keys` 各带一个不可由 config / `grib_short_name`
        # 推导的分量，于是「承接」与「按已知形状重建」可判别。
        assert metadata["bundle"]["build_id"] == SOURCE_BUNDLE_TOKEN
        assert metadata["cfgrib_filter_by_keys"]["filterToken"] == SOURCE_CFGRIB_TOKEN
        # `grib_short_name`：逐字承接。源侧带不可推导后缀，故与「按变量名查标准别名表
        # 自算」发散——没有这个偏移，源侧就是标准别名本身，两种实现同值。
        assert metadata["grib_short_name"] == source_metadata["grib_short_name"]
        assert metadata["grib_short_name"].endswith(SOURCE_SHORT_NAME_TOKEN)
        assert metadata["grib_short_name"] != variable
        # 复数 `idx_selectors` 的**取值**（不只是键集）逐字承接：键集可由 config 的
        # 变量表推导，只断言键集时「整份伪造复数键」的实现无法判别。
        assert metadata["idx_selectors"] == source_metadata["idx_selectors"]
        assert metadata["idx_selectors"]
        for name, selector in metadata["idx_selectors"].items():
            assert selector[SOURCE_IDX_TOKEN_KEY] == SOURCE_IDX_TOKEN, name
        # `remote_url` MUST 取源 entry 的同名字段，MUST NOT 取 `logical_remote_url`。
        assert entry["remote_url"] == origin["remote_url"]
        assert entry["remote_url"] != source_metadata["logical_remote_url"]
        # 单数 idx 键由 yd 按变量从复数键取；源侧多变量 bundle 上根本没有单数键。
        assert "idx_selector" not in source_metadata
        assert metadata["idx_selector"] == source_metadata["idx_selectors"][variable]

    # --- 两处**故意排除**：查找键本身。偏移它们会让查找落空，判别的是查找而不是
    # 实现，故两端必须重合。这是本类的真实边界，不是漏项。
    for entry in produced["entries"]:
        origin = source_entry(payload, entry["forecast_hour"], entry["variable"])
        assert entry["forecast_hour"] == origin["forecast_hour"]
        assert entry["variable"] == origin["variable"]
