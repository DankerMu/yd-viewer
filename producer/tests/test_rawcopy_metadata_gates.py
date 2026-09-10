"""`yd_producer.rawcopy.stage_raw` 承接 metadata 与源 entry 索引闸门回归。"""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from rawcopy_fixtures import (
    CYCLE,
    CYCLE_ISO,
    FLOOR_MESSAGE,
    LEADS,
    SOURCE_CFGRIB_TOKEN,
    SOURCE_EXTRA_HOURS,
    STRUCTURE_MESSAGE,
    build_tree,
    content_snapshot,
    expect_kind,
    snapshot,
    source_entry,
    source_iso,
    source_manifest_payload,
    staged,
)

from yd_producer import rawcopy as rawcopy_module
from yd_producer.rawcopy import RawStagingError

# --- Row：承接来的 entry 时间与 (cycle, lead) 槽位的一致性 --------------------
#
# 承接是逐字的，但一条「时间属于另一轮」的源 entry 若被照单全收，产出的
# `raw-manifest.json` 会在 manifest 级声明一个 cycle（yd 自算）、在每条 entry 上声明
# 另一个，且全部 lead 共用同一个 `valid_time`——一份自相矛盾的落盘产物，而这份矛盾
# yd 从自己的入参就能判出来。下面四条按**分量**取判别器：`cycle_time` 分量、
# `valid_time` 分量、解析腿各一条，外加一条证明比较取在**时刻**而不是文本上的绿用例。


def _retime_entries(
    payload: dict[str, Any],
    *,
    cycle_time=None,
    valid_time=None,
) -> None:
    """把每条源 entry 的两个时间键改写成给定值（可调用则按 lead 取值）。"""
    for entry in payload["entries"]:
        lead = entry["forecast_hour"]
        if cycle_time is not None:
            entry["metadata"]["cycle_time"] = (
                cycle_time(lead) if callable(cycle_time) else cycle_time
            )
        if valid_time is not None:
            entry["metadata"]["valid_time"] = (
                valid_time(lead) if callable(valid_time) else valid_time
            )


def test_entries_labelled_with_another_cycle_are_refused(tmp_path: Path) -> None:
    """整份源 manifest 的 entry 时间属于另一轮 -> 拒绝，零写入。

    这是 round-4 的复现输入：staging 原本**成功**，产出 manifest 的 manifest 级
    `cycle_time` 是本轮、每条 entry 是 2019 年那轮，且三个 lead 的 `valid_time` 相同。
    """
    payload = source_manifest_payload("gfs")
    other_cycle = datetime(2019, 7, 4, 18, tzinfo=UTC)
    _retime_entries(
        payload,
        cycle_time=source_iso(other_cycle),
        valid_time=source_iso(other_cycle + timedelta(hours=3)),
    )
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_entry_cycle_time_alone_off_by_one_cycle_is_refused(tmp_path: Path) -> None:
    """`cycle_time` **分量**的判别器：只有它错、`valid_time` 全部正确。"""
    payload = source_manifest_payload("gfs")
    _retime_entries(payload, cycle_time=source_iso(CYCLE - timedelta(hours=6)))
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert "cycle_time" in str(excinfo.value)
    assert snapshot(work_dir) == {}


def test_entry_valid_time_alone_not_matching_the_lead_is_refused(
    tmp_path: Path,
) -> None:
    """`valid_time` **分量**的判别器：`cycle_time` 全部正确，`valid_time` 恒等于
    cycle（即 round-4 复现里「所有 lead 同一个 valid_time」那半边）。

    lead 0 上它恰好是对的，判别力来自 lead 3/6——故本用例同时证明该分量是**逐 lead**
    判的，不是只看一条。
    """
    payload = source_manifest_payload("gfs")
    _retime_entries(payload, valid_time=source_iso(CYCLE))
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert "valid_time" in str(excinfo.value)
    assert snapshot(work_dir) == {}


@pytest.mark.parametrize(
    ("bad_value", "leg"),
    [
        # `json.load` 会产出的非字符串形态：`parse_cycle_time` 在 `.strip()` 上抛
        # `AttributeError`。
        (20260304, "AttributeError"),
        # 字符串但不是 ISO-8601：在 `fromisoformat` 上抛 `ValueError`。
        ("not-a-time", "ValueError"),
    ],
)
def test_unparseable_entry_time_is_refused_by_the_entry_time_gate(
    tmp_path: Path, bad_value: Any, leg: str
) -> None:
    """解析腿的判别器：不可解析的时间由**本闸门**以具名消息拒，不是掉进准入地板。

    地板对二者给出同一个 kind，故判别器取在**消息**上：闸门说「不是可解析的时刻」，
    地板说「准入期出现未预期的异常」。去掉解析腿就只剩后者。
    `except (AttributeError, ValueError)` 是复合闸门，故**两个分量各一行参数**：
    只收窄到其中一个，另一行就会红。
    """
    payload = source_manifest_payload("gfs")
    _retime_entries(payload, cycle_time=bad_value)
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert "不是可解析的时刻" in str(excinfo.value), leg
    assert "准入期出现未预期的异常" not in str(excinfo.value)
    assert snapshot(work_dir) == {}


def test_entry_times_written_in_another_offset_stage_normally(tmp_path: Path) -> None:
    """一致性核对取在**时刻**上，不是文本上：源侧用 `-05:00` 写同一批时刻 ->
    正常产出，且落盘值仍是源侧那串**原文本**（承接是逐字的，核对不改写）。

    这是「按字符串比 `cycle.isoformat()`」这种退化实现的判别器：那样写会把一份
    完全正确的源 manifest 拒掉。
    """

    def offset_iso(moment: datetime) -> str:
        return moment.astimezone(timezone(timedelta(hours=-5))).isoformat()

    payload = source_manifest_payload("gfs")
    _retime_entries(
        payload,
        cycle_time=offset_iso(CYCLE),
        valid_time=lambda lead: offset_iso(CYCLE + timedelta(hours=lead)),
    )
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    produced = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert produced["cycle_time"] == CYCLE_ISO  # manifest 级仍由 yd 自算
    for entry in produced["entries"]:
        metadata = entry["metadata"]
        assert metadata["cycle_time"] == offset_iso(CYCLE)
        assert metadata["cycle_time"].endswith("-05:00")
        assert metadata["valid_time"] == offset_iso(
            CYCLE + timedelta(hours=entry["forecast_hour"])
        )


# --- Row：承接来的 grib_short_name 与 cfgrib_filter_by_keys.shortName 一致性 --
#
# 六键逐字承接之后，这两个值按 pin 是同一身份的两次书写。源侧若写成互斥的两个
# shortName，产出 manifest 会在同一条 entry 上同时声明两个 GRIB 变量。核对的是
# 两个已承接值之间的关系，不是按变量名查别名表。


APCP_WRONG_FILTER_SHORT_NAME = "2t-WRONG"
CUSTOM_EQUAL_SHORT_NAME = "custom-tp-not-an-alias"
CUSTOM_EXTRA_FILTER_KEY = "typeOfLevel"
CUSTOM_EXTRA_FILTER_VALUE = "surface"


def _apcp_metadata_at_leads(
    payload: dict[str, Any],
) -> list[tuple[int, dict[str, Any]]]:
    """本轮会消费的 apcp entry 及其 lead；源侧多出的 lead/变量不在此列。"""
    return [(lead, source_entry(payload, lead, "apcp")["metadata"]) for lead in LEADS]


def _expect_grib_identity_refusal(tmp_path: Path, payload: dict[str, Any]):
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    before_work = snapshot(work_dir)
    before_source = snapshot(raw_root)
    before_source_content = content_snapshot(raw_root)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == before_work == {}
    assert snapshot(raw_root) == before_source
    assert content_snapshot(raw_root) == before_source_content
    return excinfo


def test_apcp_filter_short_name_2t_wrong_is_refused(tmp_path: Path) -> None:
    """issue #99 复现：apcp 的 filter shortName 改成 2t-WRONG，grib_short_name 不动。"""
    payload = source_manifest_payload("gfs")
    originals: list[tuple[int, str]] = []
    for lead, metadata in _apcp_metadata_at_leads(payload):
        originals.append((lead, metadata["grib_short_name"]))
        metadata["cfgrib_filter_by_keys"]["shortName"] = APCP_WRONG_FILTER_SHORT_NAME
    excinfo = _expect_grib_identity_refusal(tmp_path, payload)
    message = str(excinfo.value)
    lead, original = originals[0]
    assert f"(lead={lead}, variable='apcp')" in message
    assert original in message
    assert APCP_WRONG_FILTER_SHORT_NAME in message


def test_apcp_grib_short_name_alone_changed_is_refused(tmp_path: Path) -> None:
    """反向分量：只改 grib_short_name，filter shortName 仍是源侧原值。"""
    payload = source_manifest_payload("gfs")
    originals: list[tuple[int, str]] = []
    for lead, metadata in _apcp_metadata_at_leads(payload):
        originals.append((lead, metadata["cfgrib_filter_by_keys"]["shortName"]))
        metadata["grib_short_name"] = APCP_WRONG_FILTER_SHORT_NAME
    excinfo = _expect_grib_identity_refusal(tmp_path, payload)
    message = str(excinfo.value)
    lead, original = originals[0]
    assert f"(lead={lead}, variable='apcp')" in message
    assert original in message
    assert APCP_WRONG_FILTER_SHORT_NAME in message


def test_non_mapping_cfgrib_filter_is_refused_without_bare_exception(
    tmp_path: Path,
) -> None:
    payload = source_manifest_payload("gfs")
    source_entry(payload, LEADS[0], "apcp")["metadata"]["cfgrib_filter_by_keys"] = [
        "shortName"
    ]
    excinfo = _expect_grib_identity_refusal(tmp_path, payload)
    message = str(excinfo.value)
    assert f"(lead={LEADS[0]}, variable='apcp')" in message
    assert "cfgrib_filter_by_keys" in message


def test_missing_filter_short_name_is_refused_even_when_peer_is_none(
    tmp_path: Path,
) -> None:
    """缺 shortName 子键即使对端 grib_short_name 是 None 也无效。"""
    payload = source_manifest_payload("gfs")
    metadata = source_entry(payload, LEADS[0], "apcp")["metadata"]
    metadata["grib_short_name"] = None
    metadata["cfgrib_filter_by_keys"].pop("shortName")
    excinfo = _expect_grib_identity_refusal(tmp_path, payload)
    message = str(excinfo.value)
    assert f"(lead={LEADS[0]}, variable='apcp')" in message
    assert "shortName" in message


def test_equal_custom_short_names_and_extra_filter_keys_are_carried_verbatim(
    tmp_path: Path,
) -> None:
    """两边同为非规范化自定义名、且 filter 另有无关键 -> 成功，落盘仍是源侧原值。"""
    payload = source_manifest_payload("gfs")
    for _, metadata in _apcp_metadata_at_leads(payload):
        metadata["grib_short_name"] = CUSTOM_EQUAL_SHORT_NAME
        metadata["cfgrib_filter_by_keys"]["shortName"] = CUSTOM_EQUAL_SHORT_NAME
        metadata["cfgrib_filter_by_keys"][CUSTOM_EXTRA_FILTER_KEY] = (
            CUSTOM_EXTRA_FILTER_VALUE
        )
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    produced = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    for lead in LEADS:
        origin = source_entry(payload, lead, "apcp")["metadata"]
        written = source_entry(produced, lead, "apcp")["metadata"]
        assert written["grib_short_name"] == CUSTOM_EQUAL_SHORT_NAME
        assert written["grib_short_name"] == origin["grib_short_name"]
        assert written["cfgrib_filter_by_keys"] == origin["cfgrib_filter_by_keys"]
        assert written["cfgrib_filter_by_keys"]["shortName"] == CUSTOM_EQUAL_SHORT_NAME
        assert (
            written["cfgrib_filter_by_keys"][CUSTOM_EXTRA_FILTER_KEY]
            == CUSTOM_EXTRA_FILTER_VALUE
        )
        assert written["cfgrib_filter_by_keys"]["filterToken"] == SOURCE_CFGRIB_TOKEN


# --- Row：源 entry 的索引键完整性（两条**独立**缺陷，各自可触发）--------------
#
# round-3 verifier 实测证明二者可分离，只补一条会直接重演类闭合失败：
# - 形态闸门关不掉「纯整数重复键」（不涉及任何归一）；
# - injectivity 守卫关不掉「唯一一条 `3.9` entry」（不涉及任何重复）。
# 这是本模块唯一一条会**成功返回 + 输出静默错误**的路径：影子 entry 顶掉真实
# (lead, variable) 的 `remote_url` 与六键，副本是真的、清单的来源声明是假的。


def _shadow_entry(payload: dict[str, Any], lead: int, variable: str, **overrides: Any):
    """复制一条真实 entry 做影子，并**保留 `idx_selectors`**。

    verifier 记录的构造陷阱：影子 entry 若少了 `metadata["idx_selectors"]`，会被更早
    的 `_check_accumulation` 以 `accumulation-metadata` 吸收，用例于是「因为错误的
    理由」变绿，看起来像已经防御住了。
    """
    original = source_entry(payload, lead, variable)
    shadow = json.loads(json.dumps(original))
    assert IDX_SELECTORS_TEST_KEY in shadow["metadata"]
    shadow["remote_url"] = "https://attacker.invalid/bogus.grib2"
    shadow["metadata"]["grib_short_name"] = "ATTACKER"
    shadow.update(overrides)
    payload["entries"].append(shadow)
    return shadow


IDX_SELECTORS_TEST_KEY = "idx_selectors"


@pytest.mark.parametrize(
    ("bad_value", "why"),
    [
        (3.9, "浮点被 `int()` 截断成 3"),
        (3.0, "整值浮点同样不是源里写的那个值"),
        ("3", "字符串被 `int()` 解析成 3"),
        (True, "`bool` 是 `int` 子类，`int(True) == 1`"),
    ],
)
def test_lossy_forecast_hour_shape_is_refused_before_any_write(
    tmp_path: Path, bad_value: Any, why: str
) -> None:
    """`forecast_hour` 的**形态闸门**：`int()` 有损归一的取值一律拒。

    单独可触发：本用例的坏 entry 是该 (lead, variable) 的**唯一**一条，不涉及任何
    重复键，故 injectivity 守卫关不掉它。
    """
    payload = source_manifest_payload("gfs")
    entry = source_entry(payload, 3, "apcp")
    entry["forecast_hour"] = bad_value
    # 前提取证：pin 侧的 `int()` 确实会把它静默归一成另一个合法 lead。
    assert int(bad_value) in (1, 3) and int(bad_value) is not bad_value
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert "forecast_hour" in str(excinfo.value)
    assert snapshot(work_dir) == {}


# 形态闸门 `if not isinstance(entry, Mapping) or "forecast_hour" not in entry` 是一条
# **复合闸门**，两个分量各自的语义都是「这条 entry 不归本闸门管，交给结构闸门给出具名
# 诊断」。两个分量各配一条判别器，判据取在消息上（kind 两侧同为 `source-manifest`）。


def test_non_mapping_source_entry_is_diagnosed_by_the_structure_gate(
    tmp_path: Path,
) -> None:
    """第一分量 `not isinstance(entry, Mapping)`：非容器 entry MUST 走结构闸门。

    去掉该分量后 `"forecast_hour" not in 42` 抛 `TypeError`（`int` 不可迭代），落进
    准入地板，诊断退化成泛化兜底。用 `42` 而不是字符串：字符串上 `in` 是**合法**的
    子串判定、不抛，那样的输入对本分量没有判别力。
    """
    payload = source_manifest_payload("gfs")
    payload["entries"].append(42)
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert STRUCTURE_MESSAGE in str(excinfo.value)
    assert FLOOR_MESSAGE not in str(excinfo.value)
    assert snapshot(work_dir) == {}


def test_source_entry_without_forecast_hour_is_diagnosed_by_the_structure_gate(
    tmp_path: Path,
) -> None:
    """第二分量 `"forecast_hour" not in entry`：缺该键的 entry MUST 走结构闸门。

    去掉该分量后 `entry["forecast_hour"]` 抛 `KeyError`，同样落进准入地板。
    """
    payload = source_manifest_payload("gfs")
    orphan = json.loads(json.dumps(source_entry(payload, 3, "apcp")))
    orphan.pop("forecast_hour")
    payload["entries"].append(orphan)
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert STRUCTURE_MESSAGE in str(excinfo.value)
    assert FLOOR_MESSAGE not in str(excinfo.value)
    assert snapshot(work_dir) == {}


def test_safe_repr_falls_back_again_when_even_the_type_name_raises() -> None:
    """`_safe_repr` 的**内层**兜底：连 `type(value).__name__` 都抛时仍不得抛。

    外层兜底自己会取类型名去拼消息，于是一个 `__name__` 抛异常的元类能让「保证不抛」
    的收口器与 `rollback` 在它们**自己的**兜底逻辑里失守。这条腿此前无判别器（删掉
    内层 `except` 全套件不变红）。
    """

    class ExplodingName(type):
        @property
        def __name__(cls) -> str:  # 元类上的 property，故形参是类而非实例
            raise RuntimeError("类型名也取不到")

    class Pathological(metaclass=ExplodingName):
        def __repr__(self) -> str:
            raise RuntimeError("repr 自身抛异常")

    value = Pathological()
    # 前提取证：两层都真的会抛，本用例不是在测一个不会触发的分支。
    with pytest.raises(RuntimeError):
        repr(value)
    with pytest.raises(RuntimeError):
        _ = type(value).__name__
    # 抛出即失败，但**不让病态对象进异常链**：它会连 pytest 自己的报告拼装一起炸
    # （实测：内层兜底缺失时整轮 pytest 以 INTERNALERROR 收场，看不到红条）。
    try:
        rendered = rawcopy_module._safe_repr(value)
    except BaseException as exc:  # noqa: BLE001 —— 判的就是「它抛了没有」
        rendered = f"<_safe_repr 抛了 {type(exc).__name__}>"
    assert rendered == "<无法取 repr 的对象>"

    # 对照：只有 `__repr__` 抛的普通对象走**外层**兜底，两条腿不是同一条。
    class OnlyReprRaises:
        def __repr__(self) -> str:
            raise RuntimeError("只有 repr 抛")

    assert rawcopy_module._safe_repr(OnlyReprRaises()) == (
        "<OnlyReprRaises 对象，repr() 自身抛异常>"
    )


def test_fractional_shadow_entry_cannot_hijack_a_real_slot(tmp_path: Path) -> None:
    """影子 entry（`forecast_hour: 3.9` + 完整 `idx_selectors`）MUST NOT 被接受。

    **本用例是两条闸门的联合端到端回归，不是任一条的判别器**，这一点如实写明：该影子
    同时是「`int()` 有损归一」与「(3, "apcp") 重复键」两种违规，于是只去形态闸门时由
    injectivity 守卫接住、只去 injectivity 守卫时由形态闸门接住，**只有两条同时去掉
    才变红**（round-4 实测：G1 不红、G2 不红、G1+G2 红）。原 docstring 声称「没有形态
    闸门时它会占住槽位且 staging 正常成功」，在本 head 上不成立。
    单条闸门各自的判别器另有其人，不在此重复构造：形态闸门是
    `test_lossy_forecast_hour_shape_is_refused_before_any_write[3.9]`（坏 entry 是该
    (lead, variable) 的唯一一条，不涉重复），injectivity 守卫是
    `test_duplicate_source_entry_key_is_refused`（重复键是纯整数，不涉归一）。
    它在这里的价值是那条**联合**性质：「一条源侧影子 entry 无法劫持一个真实槽位」。
    `idx_selectors` 必须带上——否则它会被 `_check_accumulation` 提前吸收成
    `accumulation-metadata`，用例白绿。
    """
    payload = source_manifest_payload("gfs")
    shadow = _shadow_entry(payload, 3, "apcp", forecast_hour=3.9)
    assert int(shadow["forecast_hour"]) == 3  # 前提取证：确实指向真实槽位
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_duplicate_source_entry_key_is_refused(tmp_path: Path) -> None:
    """`_index_source_entries` 的 **injectivity 守卫**：重复键一律拒，不后写覆盖。

    单独可触发：本用例的重复键是**纯整数**的，不涉及任何 `int()` 归一，故形态闸门
    关不掉它。两条断言合起来是「两条缺陷可分离」的回归形式。
    """
    payload = source_manifest_payload("gfs")
    shadow = _shadow_entry(payload, 3, "apcp")
    assert shadow["forecast_hour"] == 3 and isinstance(shadow["forecast_hour"], int)
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert "唯一" in str(excinfo.value)
    assert snapshot(work_dir) == {}


def test_duplicate_key_on_a_lead_this_round_does_not_request_is_still_refused(
    tmp_path: Path,
) -> None:
    """重复键的判定面是**整份源 manifest**，不是本轮消费到的那几条。

    索引是 (lead, variable) -> entry 的函数；源侧不满足单射时本模块无从判断该取哪
    一条，MUST NOT 因为「这条这轮用不到」就放行——下一轮 config 换个 lead 就命中。
    """
    payload = source_manifest_payload("gfs")
    _shadow_entry(payload, SOURCE_EXTRA_HOURS[0], "apcp")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}
