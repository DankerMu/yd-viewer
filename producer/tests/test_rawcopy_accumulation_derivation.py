"""源 entry 无 idx 键时，apcp 的累积语义由 raw bundle 的 `stepRange` 自证。

规则来源：openspec `apcp-step-range-self-proof`（spec `raw-scan` 的
「manifest 语义键承接与 fail-closed」末六条 Scenario）与 `docs/compute-loop-design.md`
§7.2。独立成文件而不是并进 `test_rawcopy_source_gates.py`：后者已 617 行，本组用例会
把它推过 `.large-file-guard.json` 的 1000 行上限。

判据说明：pin 形态缺 `idx_selector` 时既有 R4B2 闸门给的 kind **也是**
`accumulation-metadata`，故本文件的每条拒绝用例都另判**消息**——推导路径的消息一律
带上 raw 原件路径与实测到的 `stepRange`，R4B2 的「缺累积语义子 Mapping」消息两者都
没有，于是用例在推导落地之前真的变红。
"""

import errno
import json
from pathlib import Path
from typing import Any

import eccodes
import pytest
from rawcopy_fixtures import (
    CYCLE,
    GFS_VARIABLES,
    IFS_VARIABLES,
    LEADS,
    build_tree,
    bundle_name,
    cycle_dir,
    expect_kind,
    grib_bundle_bytes,
    make_config,
    make_source,
    selector_for,
    snapshot,
    source_manifest_payload,
    staged,
)

from yd_producer import rawcopy
from yd_producer.rawcopy import RawStagingError, stage_raw
from yd_producer.rawscan import judge

# apcp 的真实 GRIB shortName。fixture 默认给 `grib_short_name` 加
# `SOURCE_SHORT_NAME_TOKEN` 后缀（为的是让「承接」与「按别名表自算」可判别），但
# eccodes 的 `shortName` 是概念表里的枚举值，造不出带后缀的报文；推导用例必须让承接来
# 的 `grib_short_name` 与 bundle 里真实存在的 shortName 相等，故此处把 apcp 那两处
# 归一成 `"tp"`。其余变量的后缀原样保留。
TP = "tp"


def _manifest_without_idx(leads: tuple[int, ...]) -> dict[str, Any]:
    """`gfs-idx-selector-v3` 形态的源 manifest：entry 两个 idx 键都缺席。"""
    payload = source_manifest_payload("gfs", leads=leads, with_idx=False)
    for entry in payload["entries"]:
        if entry["variable"] != "apcp":
            continue
        entry["metadata"]["grib_short_name"] = TP
        entry["metadata"]["cfgrib_filter_by_keys"]["shortName"] = TP
    # 前提取证：源侧两个 idx 键确实一个都没有，否则用例走的是 pin 形态、判不到推导。
    assert all(
        "idx_selectors" not in entry["metadata"]
        and "idx_selector" not in entry["metadata"]
        for entry in payload["entries"]
    )
    return payload


def _tree(tmp_path: Path, *, leads: tuple[int, ...], records_for):
    """无 idx 键的源 manifest + 逐 lead 由 `records_for` 决定内容的真 GRIB bundle。"""
    payload = _manifest_without_idx(leads)
    raw_root, work_dir = build_tree(
        tmp_path,
        leads=leads,
        manifest=payload,
        bundle_bytes_for=lambda lead: grib_bundle_bytes(records_for(lead)),
    )
    return raw_root, work_dir, make_config(gfs=make_source(lead_hours=leads))


def _produced_metadata(result, lead: int, variable: str) -> dict[str, Any]:
    """从**落盘**的本轮 manifest 取某条 entry 的 metadata。"""
    written = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    matches = [
        entry
        for entry in written["entries"]
        if entry["forecast_hour"] == lead and entry["variable"] == variable
    ]
    assert len(matches) == 1, (
        f"(lead={lead}, variable={variable!r}) 命中 {len(matches)}"
    )
    return matches[0]["metadata"]


# --- Row：两条自证规则 --------------------------------------------------------


def test_single_zero_start_record_derives_cumulative_with_zero_raw_writes(
    tmp_path: Path,
) -> None:
    leads = (3,)
    # 同 bundle 里另有一条 `2t` 记录且 `stepRange` 不同：不按 `grib_short_name` 过滤的
    # 实现会看到两条记录而拒绝，于是「过滤」这一步有判别力。
    raw_root, work_dir, config = _tree(
        tmp_path,
        leads=leads,
        records_for=lambda lead: (("2t", "3-6"), (TP, f"0-{lead}")),
    )
    before = snapshot(raw_root)
    result = staged(raw_root, work_dir, "gfs", config)
    metadata = _produced_metadata(result, 3, "apcp")
    assert metadata["idx_selector"] == {
        "accumulation_type": "cumulative_since_cycle",
        "step_range": "0-3",
    }
    # 复数键 MUST NOT 被发明：源侧没有，推导只写单数键。
    assert "idx_selectors" not in metadata
    # 零写入取证：整棵 raw 树的 lstat 快照不变，且没有 cfgrib 会写的 `.idx`。
    assert snapshot(raw_root) == before
    assert list(raw_root.rglob("*.idx")) == []
    # 非累积变量不因推导而长出 idx 键。
    assert "idx_selector" not in _produced_metadata(result, 3, "tmp2m")


def test_nonzero_start_record_derives_interval_bucket(tmp_path: Path) -> None:
    leads = (6,)
    raw_root, work_dir, config = _tree(
        tmp_path,
        leads=leads,
        records_for=lambda lead: (("2t", f"0-{lead}"), (TP, "3-6")),
    )
    result = staged(raw_root, work_dir, "gfs", config)
    assert _produced_metadata(result, 6, "apcp")["idx_selector"] == {
        "accumulation_type": "interval_bucket",
        "step_range": "3-6",
    }


# --- Row：四条 fail-closed ----------------------------------------------------


def test_zero_matching_records_fail_closed(tmp_path: Path) -> None:
    leads = (3,)
    raw_root, work_dir, config = _tree(
        tmp_path, leads=leads, records_for=lambda lead: (("2t", f"0-{lead}"),)
    )
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir, "gfs", config)
    expect_kind(excinfo, "accumulation-metadata")
    message = str(excinfo.value)
    assert bundle_name("gfs", 3) in message
    assert "零条" in message
    assert snapshot(work_dir) == {}


def test_two_matching_records_fail_closed_listing_both(tmp_path: Path) -> None:
    leads = (3,)
    raw_root, work_dir, config = _tree(
        tmp_path, leads=leads, records_for=lambda lead: ((TP, "0-3"), (TP, "3-6"))
    )
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir, "gfs", config)
    expect_kind(excinfo, "accumulation-metadata")
    message = str(excinfo.value)
    assert "0-3" in message and "3-6" in message
    assert bundle_name("gfs", 3) in message
    assert snapshot(work_dir) == {}


def test_step_range_end_other_than_lead_fails_closed(tmp_path: Path) -> None:
    leads = (6,)
    raw_root, work_dir, config = _tree(
        tmp_path, leads=leads, records_for=lambda lead: ((TP, "0-3"),)
    )
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir, "gfs", config)
    expect_kind(excinfo, "accumulation-metadata")
    message = str(excinfo.value)
    assert "0-3" in message and "lead=6" in message
    assert snapshot(work_dir) == {}


def test_malformed_step_range_fails_closed(tmp_path: Path) -> None:
    """瞬时形态的 `stepRange`（单端 `3`）不是 `<int>-<int>`，不得当成区间。"""
    leads = (3,)
    raw_root, work_dir, config = _tree(
        tmp_path, leads=leads, records_for=lambda lead: ((TP, "3"),)
    )
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir, "gfs", config)
    expect_kind(excinfo, "accumulation-metadata")
    message = str(excinfo.value)
    assert "'3'" in message
    assert bundle_name("gfs", 3) in message
    assert snapshot(work_dir) == {}


def test_undecodable_bundle_fails_closed(tmp_path: Path) -> None:
    """非 GRIB 的占位 bundle：eccodes 读不动，同样归 `accumulation-metadata`。"""
    leads = (3,)
    payload = _manifest_without_idx(leads)
    # 不给 `bundle_bytes_for`，默认落的是占位字节（`GRIB\xff\x00lead-003`）。
    raw_root, work_dir = build_tree(tmp_path, leads=leads, manifest=payload)
    config = make_config(gfs=make_source(lead_hours=leads))
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir, "gfs", config)
    expect_kind(excinfo, "accumulation-metadata")
    message = str(excinfo.value)
    assert "eccodes" in message
    assert bundle_name("gfs", 3) in message
    # 原始的 eccodes 异常经 `raise ... from` 保留在 `__cause__` 上。
    assert isinstance(excinfo.value.__cause__, eccodes.CodesInternalError)
    assert snapshot(work_dir) == {}


# --- Row：lstat 闸门之后换入的 symlink ----------------------------------------


def test_symlink_swapped_in_after_the_lstat_gate_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_reject_symlinks` 只证明「判定那一刻不是链」，推导的 open 是第二道闩。

    `judge` 之后才换链：rawscan 的读阶段本身用 no-follow 描述符，先换会让 verdict
    不完整、根本走不到推导。`_reject_symlinks` 另行 no-op 掉，模拟 lstat 闸门通过
    之后的那个 TOCTOU 窗口。
    """
    leads = (3,)
    raw_root, work_dir, config = _tree(
        tmp_path, leads=leads, records_for=lambda lead: ((TP, f"0-{lead}"),)
    )
    verdict = judge(raw_root, "gfs", CYCLE, config)
    assert verdict.complete is True

    # raw_root 之外一份**合法**且 stepRange 不同的 GRIB：跟随链的实现会把 `3-6`
    # 推导成 interval_bucket 并落盘，于是「不跟随」可判。
    outside = tmp_path / "outside.grib2"
    outside.write_bytes(grib_bundle_bytes(((TP, "3-6"),)))
    bundle = cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)
    bundle.unlink()
    bundle.symlink_to(outside)
    monkeypatch.setattr(rawcopy, "_reject_symlinks", lambda *args, **kwargs: None)

    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "accumulation-metadata")
    message = str(excinfo.value)
    assert "不跟随" in message
    # 机制取证：失败确实来自 `O_NOFOLLOW` 的 `ELOOP`（不是「文件没了」之类的别的
    # `OSError`），且原异常经 `raise ... from` 挂在 `__cause__` 上。
    assert isinstance(excinfo.value.__cause__, OSError)
    assert excinfo.value.__cause__.errno == errno.ELOOP
    assert "3-6" not in message
    assert snapshot(work_dir) == {}


# --- Row：pin 形态与非累积变量不进推导 ----------------------------------------


def test_pin_form_never_opens_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """源 entry 带 idx 键时逐字承接：eccodes 的枚举入口被打成「一调就炸」也照样绿。"""

    def _boom(*args, **kwargs):
        raise AssertionError("pin 形态 MUST NOT 打开 bundle")

    monkeypatch.setattr(eccodes, "codes_grib_new_from_file", _boom)
    raw_root, work_dir = build_tree(tmp_path)
    before = snapshot(raw_root)
    result = staged(raw_root, work_dir)
    assert len(result.entries) == len(LEADS) * len(GFS_VARIABLES)
    for lead in LEADS:
        assert _produced_metadata(result, lead, "apcp")["idx_selector"] == selector_for(
            "apcp", lead
        )
    assert snapshot(raw_root) == before


def _recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []

    def _record(source_path, *, lead, variable, grib_short_name):
        calls.append((lead, variable))
        return {
            "accumulation_type": "cumulative_since_cycle",
            "step_range": f"0-{lead}",
        }

    monkeypatch.setattr(rawcopy, "_derive_accumulation_selector", _record)
    return calls


def test_only_apcp_entries_enter_the_derivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leads = (3, 6)
    calls = _recorder(monkeypatch)
    payload = _manifest_without_idx(leads)
    raw_root, work_dir = build_tree(tmp_path, leads=leads, manifest=payload)
    config = make_config(gfs=make_source(lead_hours=leads))
    staged(raw_root, work_dir, "gfs", config)
    assert calls == [(3, "apcp"), (6, "apcp")]


def test_ifs_entries_never_enter_the_derivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IFS 的 `tp` 同为累积量，但不在 `ACCUMULATION_VARIABLES` 内，本 issue 不为它发明。"""
    calls = _recorder(monkeypatch)
    raw_root, work_dir = build_tree(tmp_path, "ifs")
    result = staged(raw_root, work_dir, "ifs")
    assert calls == []
    assert len(result.entries) == len(LEADS) * len(IFS_VARIABLES)


def test_pin_form_gfs_entries_never_enter_the_derivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recorder(monkeypatch)
    raw_root, work_dir = build_tree(tmp_path)
    staged(raw_root, work_dir)
    assert calls == []


# --- Row：推导函数自身的解析规则（直调） --------------------------------------


def test_derivation_filters_by_the_carried_grib_short_name(tmp_path: Path) -> None:
    """过滤用的是**承接来的** `grib_short_name`，不是写死的 `"tp"`。"""
    from yd_producer._rawcopy_metadata import _derive_accumulation_selector

    bundle = tmp_path / "mixed.grib2"
    bundle.write_bytes(grib_bundle_bytes((("2t", "0-3"), (TP, "3-6"))))
    assert _derive_accumulation_selector(
        bundle, lead=3, variable="apcp", grib_short_name="2t"
    ) == {"accumulation_type": "cumulative_since_cycle", "step_range": "0-3"}
    assert _derive_accumulation_selector(
        bundle, lead=6, variable="apcp", grib_short_name=TP
    ) == {"accumulation_type": "interval_bucket", "step_range": "3-6"}


def test_derivation_rejects_an_unopenable_path(tmp_path: Path) -> None:
    from yd_producer._rawcopy_metadata import _derive_accumulation_selector

    with pytest.raises(RawStagingError) as excinfo:
        _derive_accumulation_selector(
            tmp_path / "absent.grib2", lead=3, variable="apcp", grib_short_name=TP
        )
    expect_kind(excinfo, "accumulation-metadata")
    assert "不跟随" in str(excinfo.value)
