"""`yd_producer.state.state_qc` 的行为测试（任务 4.2 结构检查 + 任务 4.4 负残差）。

oracle 纪律：`evidence()` 的每一个计数与域均都**逐值手算**断言，不写 `> 0` / `isinstance`
——#8 实测：只断 `len()` 与 `isinstance(float)` 时，把三处 `append` 换成全零元组的变异体
在全套 46 条下存活。

规模纪律：域均门的分母是 mesh 行数 / river 行数，故 fixture 的**规模必须与幅度配套算过再
写**。用小规模 fixture 配大幅值会得到「拒绝」，据此会误判 pin 语义并去加不存在的逐格上限。
每条阈值用例的手算式都写在断言旁。

判别力纪律：「未改动行逐字节不变」MUST 在脏矩阵（CRLF / 行尾空格 / Tab / 空行 / 混合记法）
上跑——canonical 化的写法在干净输入上恒绿。
"""

from __future__ import annotations

import math
import re

import pytest
import source_probe
from cfg_ic_fixtures import (
    NEL,
    build_cfg_ic,
    build_cfg_ic_rows,
    inject_nel,
    mesh_row,
    river_row,
)

from yd_producer.state import cfg_ic, state_qc

#: 从 NWM pin `state_qc.py` 移植的符号全集：每一个都必须自带**自己的**溯源注释。
PORTED_SYMBOLS = (
    "cfg_ic_header_minute_index",
    "cfg_ic_header_minute_time",
    "CfgIcHeaderShape",
    "cfg_ic_header_shape",
    "StateQCResult",
    "_row_counts",
    "_check_row_counts",
    "_check_block_range",
    "run_state_variable_qc",
    "state_ic_structure_complete",
    "StateResidualNormalization",
    "normalize_negative_residuals",
)

#: `nwm-snapshot-inventory.md` §1 中 `packages/common/state_qc.py` 行的双权威副本禁令：这八个 MUST 从 `cfg_ic` **导入**。
CFG_IC_BASE_SYMBOLS = (
    "_looks_like_column_header",
    "_section_from_column_header",
    "_native_lake_section_preamble",
    "_header_counts",
    "_as_float",
    "_numeric_row",
    "_read_bytes_limited",
    "MAX_STATE_IC_BYTES",
)

#: 裁决 7 与 `_check_water_balance` non-goal：本模块内一律不得有这些定义。
NON_GOAL_SYMBOLS = (
    "StateCheckpoint",
    "StateRunContext",
    "_checkpoint_header_minute",
    "_valid_time_from_header_minute",
    "_checkpoint_with_header_time",
    "_lead_hours_from_run_valid_time",
    "STATE_CHECKPOINT_IC_HEADER_SHAPE_REKEY_SKIPPED",
    "_check_water_balance",
    "water_balance",
)


def _payload(**kwargs: object) -> bytes:
    return build_cfg_ic_rows(**kwargs).payload  # type: ignore[arg-type]


def _plain_mesh(count: int) -> list[tuple[str, ...]]:
    return [mesh_row(index) for index in range(1, count + 1)]


def _plain_river(count: int) -> list[tuple[str, ...]]:
    return [river_row(index) for index in range(1, count + 1)]


# =========================== 任务 4.2 结构检查 ===========================


def test_missing_river_section_is_reported_by_name_not_by_row_count() -> None:
    """spec Scenario「缺 river 段被拒 → 指明缺失段」：pin 只会说 `row count 0 != 4`。"""
    result = state_qc.run_state_variable_qc(
        _payload(mesh_rows=_plain_mesh(3)), expected_river_count=4
    )

    assert result.passed is False
    assert result.reason is not None
    assert "missing river section" in result.reason
    assert "row count 0" not in result.reason


def test_missing_river_section_is_rejected_without_any_expected_count() -> None:
    """spec `结构检查` 的「缺 river 段被拒」Scenario **不带前置条件**。

    段缺席不是「行数比对」的一个特例：spec 的第一条 Requirement 独立要求 `cfg.ic`
    「至少包含 mesh 状态段与 river `Stage` 段」，故 `doc.river is None` 在零 `expected_*`
    下就已可判。把它挂在调用方计数上，等于让 `#16` tracker 的默认调用（全 `None`）把只写
    到一半、river 段还没落盘的 checkpoint 判成「完整」。
    """
    payload = _payload(mesh_rows=_plain_mesh(3))

    result = state_qc.run_state_variable_qc(payload)

    assert result.passed is False
    assert result.reason == "missing river section"
    assert result.checks["row_counts"]["expected_river"] is None
    assert state_qc.state_ic_structure_complete(payload) is False


def test_missing_lake_section_stays_legal_without_a_declared_lake_count() -> None:
    """与上一条的**不对称是刻意的**：lake 段在原生格式里本就可选。

    只有调用方声明了非零 lake 计数、段却不存在时才失败。
    """
    payload = _payload(mesh_rows=_plain_mesh(3), river_rows=_plain_river(4))

    ok = state_qc.run_state_variable_qc(payload)
    assert ok.passed is True, ok.reason
    assert ok.reason is None
    assert state_qc.state_ic_structure_complete(payload) is True

    # 声明了 lake 计数才点名缺段。
    declared = state_qc.run_state_variable_qc(payload, expected_lake_count=2)
    assert declared.passed is False
    assert declared.reason == "missing lake section (expected 2 rows)"
    # 声明为 0 与「段缺席」自洽，仍通过。
    zero = state_qc.run_state_variable_qc(payload, expected_lake_count=0)
    assert zero.passed is True, zero.reason


def test_river_row_count_mismatch_reports_actual_and_expected() -> None:
    result = state_qc.run_state_variable_qc(
        _payload(mesh_rows=_plain_mesh(3), river_rows=_plain_river(3)),
        expected_river_count=4,
    )

    assert result.passed is False
    assert result.reason == "river row count 3 != expected 4"


def test_embedded_u0085_splits_one_physical_river_row_and_is_caught_by_the_count_gate() -> (
    None
):
    """#54 第 2 条：`splitlines` 在 U+0085 上断行，而字节 roundtrip 恒为 True。"""
    payload = _payload(
        mesh_rows=_plain_mesh(3),
        river_rows=[
            river_row(1, "0.110000"),
            river_row(2, "0.120000"),
            river_row(3, "0.130000"),
        ],
    )
    poisoned = inject_nel(
        payload, physical_line="3 0.130000", replacement=f"3 0.130000{NEL}4 0.140000"
    )
    # 字节 roundtrip 抓不到它——`render` 只拼接原始行。
    assert cfg_ic.render(cfg_ic.parse(poisoned)) == poisoned

    result = state_qc.run_state_variable_qc(poisoned, expected_river_count=3)

    assert result.passed is False
    assert result.reason == "river row count 4 != expected 3"
    assert (
        state_qc.state_ic_structure_complete(poisoned, expected_river_count=3) is False
    )


def test_row_count_checks_are_skipped_when_no_expected_counts_are_supplied() -> None:
    payload = _payload(mesh_rows=_plain_mesh(3), river_rows=_plain_river(4))

    result = state_qc.run_state_variable_qc(payload)

    assert result.passed is True
    assert result.reason is None
    assert result.checks["row_counts"] == {
        "mesh": 3,
        "river": 4,
        "lake": 0,
        "expected_mesh": None,
        "expected_river": None,
        "expected_lake": None,
    }
    assert state_qc.state_ic_structure_complete(payload) is True


def test_expected_counts_that_match_pass() -> None:
    payload = build_cfg_ic(mesh_count=3, river_count=4, lake_count=2).payload

    result = state_qc.run_state_variable_qc(
        payload, expected_mesh_count=3, expected_river_count=4, expected_lake_count=2
    )

    assert result.passed is True
    assert result.checks["range"]["lake"] == {"rows": 2, "violations": 0}


def test_non_finite_state_value_is_rejected_and_named_as_non_finite() -> None:
    result = state_qc.run_state_variable_qc(
        _payload(mesh_rows=[mesh_row(1, unsat="nan")], river_rows=_plain_river(1))
    )

    assert result.passed is False
    assert result.reason == "mesh row 0 column 4 is not finite (nan)"


@pytest.mark.parametrize("token", ["nan", "inf", "-inf"])
def test_every_non_finite_literal_is_rejected(token: str) -> None:
    result = state_qc.run_state_variable_qc(
        _payload(mesh_rows=[mesh_row(1, unsat=token)], river_rows=_plain_river(1))
    )

    assert result.passed is False
    assert result.reason is not None
    assert "is not finite" in result.reason


def test_the_same_column_reports_negative_when_the_value_is_merely_negative() -> None:
    """与上一条配对：只测 NaN 被拒不区分是哪道门拦的，两条一起才钉死次序。"""
    result = state_qc.run_state_variable_qc(
        _payload(mesh_rows=[mesh_row(1, unsat="-1.0")], river_rows=_plain_river(1))
    )

    assert result.passed is False
    assert result.reason == "mesh row 0 unsat is negative (-1.0)"


def test_finiteness_gate_is_per_column_single_pass_not_per_block_two_pass() -> None:
    """同一行前列为负、后列为 NaN -> 报**负值**。

    「先扫全块非有限、再扫全块负值」的两遍式实现会在此报非有限而变红（pin `:826-839`
    是行内逐列单遍：同一列先 isfinite 再负值再上界）。
    """
    result = state_qc.run_state_variable_qc(
        _payload(
            mesh_rows=[mesh_row(1, canopy="-1.0", unsat="nan")],
            river_rows=_plain_river(1),
        )
    )

    assert result.passed is False
    assert result.reason == "mesh row 0 canopy is negative (-1.0)"


def test_state_value_above_the_bound_is_rejected_and_the_bound_itself_passes() -> None:
    over = state_qc.run_state_variable_qc(
        _payload(mesh_rows=[mesh_row(1, gw="1000001.0")], river_rows=_plain_river(1))
    )
    assert over.passed is False
    assert over.reason == "mesh row 0 groundwater exceeds bound (1000001.0 > 1000000.0)"

    exactly = state_qc.run_state_variable_qc(
        _payload(mesh_rows=[mesh_row(1, gw="1000000.0")], river_rows=_plain_river(1))
    )
    assert exactly.passed is True, exactly.reason


def test_negative_tolerance_boundary_in_the_range_check() -> None:
    """pin `:832` 是 `value < -_NEGATIVE_ZERO_TOLERANCE`（1.0e-2），两条边界各一条。"""
    within = state_qc.run_state_variable_qc(
        _payload(mesh_rows=[mesh_row(1, canopy="-9e-3")], river_rows=_plain_river(1))
    )
    assert within.passed is True, within.reason

    beyond = state_qc.run_state_variable_qc(
        _payload(mesh_rows=[mesh_row(1, canopy="-1.1e-2")], river_rows=_plain_river(1))
    )
    assert beyond.passed is False
    assert beyond.reason == "mesh row 0 canopy is negative (-0.011)"


def test_short_row_is_reported_as_missing_state_columns() -> None:
    result = state_qc.run_state_variable_qc(
        _payload(mesh_rows=[("1", "0.1", "0.2", "0.3")], river_rows=_plain_river(1))
    )

    assert result.passed is False
    assert result.reason == "mesh row 0 missing state columns (have 4, need >= 6)"


# --- 解析失败本身即 QC 失败，而不是崩溃 ---


def _truncated_body() -> bytes:
    return _payload(
        mesh_rows=_plain_mesh(2), header_tokens=("3", "6", "27000000.000000")
    )


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"\xff\xfe\x00\x01", id="non-utf8"),
        pytest.param(_truncated_body(), id="truncated-body"),
    ],
)
def test_parse_failure_is_a_qc_failure_never_a_crash(payload: bytes) -> None:
    result = state_qc.run_state_variable_qc(payload)

    assert result.passed is False
    assert result.checks["parsed"] is False
    assert result.reason is not None
    assert result.reason.startswith("IC parse failed: ")
    assert result.checks["parse_error"]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"\xff\xfe\x00\x01", id="non-utf8"),
        pytest.param(_truncated_body(), id="truncated-body"),
    ],
)
def test_structure_complete_returns_false_and_never_raises_on_parse_failure(
    payload: bytes,
) -> None:
    assert state_qc.state_ic_structure_complete(payload) is False


def test_qc_result_to_dict_shape_matches_the_pin() -> None:
    payload = _payload(mesh_rows=_plain_mesh(3), river_rows=_plain_river(4))

    payload_dict = state_qc.run_state_variable_qc(payload).to_dict()

    assert set(payload_dict) == {"passed", "checks", "reason"}
    assert set(payload_dict["checks"]) == {"ic_path", "parsed", "row_counts", "range"}
    assert payload_dict["passed"] is True
    assert payload_dict["reason"] is None


def test_bounded_read_flows_through_to_the_parser_with_an_unchanged_default() -> None:
    """读面完全复用 `cfg_ic.parse`；`max_bytes` 一路传下去且模块默认值未变。"""
    assert state_qc.MAX_STATE_IC_BYTES is cfg_ic.MAX_STATE_IC_BYTES
    assert cfg_ic.MAX_STATE_IC_BYTES == 64 * 1024 * 1024

    payload = _payload(mesh_rows=_plain_mesh(3), river_rows=_plain_river(4))
    result = state_qc.run_state_variable_qc(payload, max_bytes=8)

    assert result.passed is False
    assert result.reason == "IC parse failed: IC file exceeds size limit of 8 bytes"
    assert state_qc.state_ic_structure_complete(payload, max_bytes=8) is False


def test_qc_reads_a_path_source(tmp_path) -> None:
    path = build_cfg_ic(mesh_count=3, river_count=4).write(tmp_path / "cfg.ic")

    result = state_qc.run_state_variable_qc(path, expected_mesh_count=3)

    assert result.passed is True
    assert result.checks["ic_path"] == str(path)


# =========================== 任务 4.4 负残差 ===========================


def test_negative_residuals_are_zeroed_with_hand_computed_evidence() -> None:
    """mesh 5 行 / 其中 3 个 unsat 为 -1e-6；river 4 行 / 其中 2 个 stage 为 -5e-4。"""
    payload = _payload(
        mesh_rows=[
            mesh_row(index, unsat="-1e-6" if index <= 3 else "0.100000")
            for index in range(1, 6)
        ],
        river_rows=[
            river_row(index, "-5e-4" if index <= 2 else "0.100000")
            for index in range(1, 5)
        ],
    )
    doc = cfg_ic.parse(payload)

    result = state_qc.normalize_negative_residuals(doc)
    evidence = result.evidence()

    assert result.accepted is True
    assert evidence["policy"] == "unbounded_physical_zero_projection_v4"
    assert evidence["normalized_value_count"] == 5
    assert evidence["normalized_unsat_row_count"] == 3
    assert evidence["normalized_river_row_count"] == 2
    assert evidence["mesh_row_count"] == 5
    assert evidence["river_row_count"] == 4
    assert evidence["normalized_unsat_row_fraction"] == 3 / 5
    assert evidence["normalized_river_row_fraction"] == 2 / 4
    assert evidence["max_unsat_correction_m"] == 1e-6
    assert evidence["max_river_correction_m"] == 5e-4
    assert evidence["max_correction_m"] == 5e-4
    assert evidence["over_tolerance_clamp_count"] == 0
    # 手算：unsat 域均 = 3 x 1e-6 / 5 行 = 6e-7；river 域均 = 2 x 5e-4 / 4 行 = 2.5e-4。
    assert evidence["mean_unsat_correction_m"] == pytest.approx(6e-7, rel=1e-12)
    assert evidence["mean_river_correction_m"] == pytest.approx(2.5e-4, rel=1e-12)
    assert evidence["max_unsat_mean_correction_m"] == 2.0e-4
    assert evidence["max_river_mean_correction_m"] == 2.0e-3
    assert evidence["negative_zero_tolerance_m"] == 1.0e-2

    rendered = cfg_ic.render(result.document)
    assert b"-1e-6" not in rendered
    assert b"-5e-4" not in rendered


# --- 行内 splice 辅助（`token_spans` / `replace_tokens`）的直接单元用例 ---
#
# 这两个是**跨模块公开**面（`restamp.py` import 它们做 header 行的同款 splice），而两个
# 调用点在负残差路径上各自只传**单键**替换，于是「多键替换」的契约在任何层面都没有 oracle。
# round-2 verifier 实测：去掉 `sorted(..., reverse=True)` 的 `reverse` 后全套 777 条全绿，
# 且在 `assert len(replacements) <= 1` 探针下同样全绿——即没有任何用例行使过多键。


def test_token_spans_slices_every_token_of_a_mixed_separator_line() -> None:
    """切片与 `str.split()` 逐 token 对齐，且分隔字节（多空格 / Tab / 行尾空格）不属任何 token。"""
    text = "1   -1e-6\t-5e-4   0.100000  "

    spans = state_qc.token_spans(text)

    assert spans == ((0, 1), (4, 9), (10, 15), (18, 26))
    assert [text[start:end] for start, end in spans] == text.split()
    assert state_qc.token_spans("   ") == ()
    assert state_qc.token_spans("  a") == ((2, 3),)


def test_replace_tokens_splices_right_to_left_so_earlier_edits_never_shift_later_spans() -> (
    None
):
    """裁决 2 的排序不变量：span 是在**原文本**上算的，故替换必须自右向左落。

    左到右的变异体在这条上不是「输出等价」——它会用**已经位移过**的字节偏移去切后一个
    token。verifier 实测该变异体能静默产出一条合法数值行（`-1e-9` 变成 `-1e-000000`
    即 -1.0 m，且后一列被吞），故这里断言结果 bytes 逐字面量相等，不断言「能不能解析」。
    """
    # 缩短（负值 token -> "0"）：两个替换 index，分隔符混排。
    assert (
        state_qc.replace_tokens("1   -1e-6\t-5e-4   0.100000  ", {1: "0", 2: "0"})
        == "1   0\t0   0.100000  "
    )
    # 加长（restamp 的 minute token 就是加长）：位移方向相反，同样只能自右向左。
    assert state_qc.replace_tokens("a  b  c", {0: "XXXX", 2: "YY"}) == "XXXX  b  YY"
    # 三个替换 index：相邻与间隔各一对。
    assert (
        state_qc.replace_tokens("1\t-2   -3  -4 ", {1: "0", 2: "0", 3: "0"})
        == "1\t0   0  0 "
    )
    # 单键与空 mapping 仍逐字节恒等（两个生产调用点的现有形态）。
    assert state_qc.replace_tokens("1   -1e-6  ", {1: "0"}) == "1   0  "
    assert state_qc.replace_tokens("1   -1e-6  ", {}) == "1   -1e-6  "


def _token_spans(text: str) -> list[tuple[int, int]]:
    """测试侧**独立**实现的 token 切片（不复用被测模块的 `state_qc.token_spans`）。"""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for index, char in enumerate(text):
        if char.isspace():
            if start is not None:
                spans.append((start, index))
                start = None
        elif start is None:
            start = index
    if start is not None:
        spans.append((start, len(text)))
    return spans


def _assert_only_these_tokens_spliced(
    old: str, new: str, expected_indices: set[int]
) -> None:
    """裁决 2 的**字节级** oracle：改动行内只有 `expected_indices` 的字节切片被替换。

    为什么不能只断 token 序列 / Tab 计数 / `endswith` 布尔相等：`str.split()` 丢掉分隔
    与行尾空白，Tab 计数在**统一** Tab 分隔下对 `"\\t".join(body.split())` 恒成立，而
    `old.endswith("\\r\\n") == new.endswith("\\r\\n")` 比的是两个布尔值（两侧都为 False
    时空真）。实测：只有这条按**切片前缀/后缀逐字节比对**的断言能杀死 pin 式回写变异体。

    为什么形参是**期望改动 index 的集合**而不是写死的 `len(differing) == 1`：同一行可以
    有多个负值格（mesh 行有五个状态列），而写死 1 时那种行在结构上无法用本 oracle 覆盖。
    单 token 的既有调用点照旧传单元素集合——集合相等比原来的 `len(...) == 1` **更严**
    （还钉住了「改的是哪一个」），不构成放松。
    """
    old_body, new_body = old.splitlines()[0], new.splitlines()[0]
    assert old[len(old_body) :] == new[len(new_body) :], "行尾符被改写"

    old_spans, new_spans = _token_spans(old_body), _token_spans(new_body)
    assert len(old_spans) == len(new_spans), f"token 数变了：{old!r} -> {new!r}"
    differing = {
        i
        for i, ((a0, a1), (b0, b1)) in enumerate(zip(old_spans, new_spans, strict=True))
        if old_body[a0:a1] != new_body[b0:b1]
    }
    assert differing == expected_indices, (
        f"改动的 token 集合不符（期望 {sorted(expected_indices)}，"
        f"实为 {sorted(differing)}）：{old!r} -> {new!r}"
    )
    for index in sorted(expected_indices):
        assert old_body[old_spans[index][0] : old_spans[index][1]].startswith("-")
        assert new_body[new_spans[index][0] : new_spans[index][1]] == "0"
    # 目标 token 之外的**全部字节**（含 token 间的原始分隔与行尾空格）逐字不变：把测试侧
    # **独立**实现的「按原文本 span 自右向左 splice」结果与产物逐字节比对。左到右的实现
    # 会用已经位移过的偏移去切后一个 token，在多 index 行上必然在这里变红。
    expected_body = old_body
    for index in sorted(expected_indices, reverse=True):
        start, end = old_spans[index]
        expected_body = expected_body[:start] + "0" + expected_body[end:]
    assert new_body == expected_body, (
        f"目标 token 之外的字节被改写：{old!r} -> {new!r}（期望 {expected_body!r}）"
    )


def test_only_rows_that_actually_carry_a_negative_value_are_reserialised() -> None:
    """脏矩阵：CRLF + 行尾空格 + 数据行内多空格/Tab 混排 + 段间空行 + 混合记法 + 无末尾换行。

    裁决 1/2 的唯一判别力来源：pin 式整文件 `"\\n".join` 与行内 `"\\t".join` 只在这里变红。
    数据行的行内分隔**必须混排**：统一单一分隔符时 `"\\t".join(body.split())` 与就地
    splice 的输出在字节上重合，这条断言就退化成永真。

    第四条 mesh 行**同一行带两个负值**（canopy 与 unsat），钉住裁决 2 的排序不变量：
    `replace_tokens` 自右向左落，使先落的替换不移动后落 token 的 span。这条行的形态是
    verifier 实测的**静默**形——左到右的实现在这里不会抛，而是产出一条仍可解析的数值行
    `... -1e-000000`（即 1e-9 m 的残差被发布成 -1.0 m、GW 列被吞），故必须逐字面量断言
    整行 bytes，不能只断 `accepted` 或计数。
    """
    synthetic = build_cfg_ic_rows(
        mesh_rows=[
            mesh_row(1, canopy="1e-3", snow="-0.0", surface="2.5E+01"),
            mesh_row(2, unsat="-1e-6"),
            mesh_row(3, canopy="0.000000"),
            mesh_row(4, canopy="-1e-9", unsat="-1e-9"),
        ],
        river_rows=[river_row(1, "2.5E+01"), river_row(2, "-5e-4")],
        eol="\r\n",
        delimiter="\t",
        # 三元循环：mesh 行的分隔序列是 `   ` `\t` `   ` `   ` `\t`，正是 verifier 复现
        # 静默错位所用的形态（两元循环下同一行会退化成抛错的「响」形）。
        data_delimiters=("   ", "\t", "   "),
        header_delimiter="   ",
        trailing_spaces="  ",
        blank_lines=True,
        trailing_newline=False,
    )
    doc = cfg_ic.parse(synthetic.payload)
    #: 改动行 -> 该行内期望被 splice 的 token index 集合（mesh 行：canopy=1 … gw=5）。
    changed = {
        synthetic.mesh_data_indices[1]: {4},
        synthetic.mesh_data_indices[3]: {1, 4},
        synthetic.river_data_indices[1]: {1},
    }

    result = state_qc.normalize_negative_residuals(doc)

    assert result.evidence()["normalized_value_count"] == 4
    for index, (old, new) in enumerate(
        zip(doc.lines, result.document.lines, strict=True)
    ):
        if index in changed:
            assert new != old
            continue
        assert new == old, f"line {index} changed: {old!r} -> {new!r}"

    for index, token_indices in changed.items():
        old, new = doc.lines[index], result.document.lines[index]
        # 构造自检：这一行确实带多空格分隔与非空行尾空格，否则下面的字节级断言对
        # canonical 化回写没有判别力。
        assert old.rstrip("\r\n").endswith("  "), old
        assert "   " in old, old
        _assert_only_these_tokens_spliced(old, new, token_indices)

    # 同行双负值那一行的整行 bytes 逐字面量断言（自右向左 splice 的唯一正确产物）。
    multi_index = synthetic.mesh_data_indices[3]
    assert (
        doc.lines[multi_index]
        == "4   -1e-9\t0.100000   0.100000   -1e-9\t0.100000  \r\n"
    )
    assert (
        result.document.lines[multi_index]
        == "4   0\t0.100000   0.100000   0\t0.100000  \r\n"
    )

    # 行内混排与行尾符两维都要有实例：mesh 改动行同时带多空格与 Tab 分隔、且以 CRLF 收尾；
    # river 改动行是文件末行、**无**行尾符（`endswith` 布尔相等在这里空真，故不能靠它）。
    mesh_changed = doc.lines[synthetic.mesh_data_indices[1]]
    assert "\t" in mesh_changed and "   " in mesh_changed, mesh_changed
    assert mesh_changed.endswith("  \r\n"), mesh_changed
    assert not doc.lines[synthetic.river_data_indices[1]].endswith("\n")
    # `-0.0` 不是负值（`-0.0 >= 0.0` 为 True），故那一格的记法逐字保留。
    assert "-0.0" in result.document.lines[synthetic.mesh_data_indices[0]]
    assert not cfg_ic.render(result.document).endswith(b"\n")


def test_element_id_column_is_never_projected() -> None:
    """pin 的投影循环自 `range(1, len(row))` 起：列索引 0 是元素 id。"""
    payload = _payload(mesh_rows=[("-1", *mesh_row(1)[1:])], river_rows=_plain_river(1))
    doc = cfg_ic.parse(payload)

    result = state_qc.normalize_negative_residuals(doc)

    assert result.evidence()["normalized_value_count"] == 0
    assert cfg_ic.render(result.document) == payload


def test_unsat_domain_mean_cap_is_strictly_greater_than() -> None:
    """mesh 1 行、unsat 修正恰为 2.0e-4 -> 域均 2.0e-4 / 1 行 == cap -> **接受**。"""
    at_cap = cfg_ic.parse(
        _payload(mesh_rows=[mesh_row(1, unsat="-2.0e-4")], river_rows=_plain_river(1))
    )
    accepted = state_qc.normalize_negative_residuals(at_cap)
    assert accepted.accepted is True
    assert accepted.evidence()["mean_unsat_correction_m"] == 2.0e-4

    over_cap_value = math.nextafter(2.0e-4, math.inf)
    over_cap = cfg_ic.parse(
        _payload(
            mesh_rows=[mesh_row(1, unsat=f"-{over_cap_value!r}")],
            river_rows=_plain_river(1),
        )
    )
    with pytest.raises(state_qc.StateResidualRejected) as excinfo:
        state_qc.normalize_negative_residuals(over_cap)
    assert excinfo.value.evidence["mean_unsat_correction_m"] == over_cap_value


def test_river_domain_mean_cap_is_strictly_greater_than() -> None:
    """river 1 行、stage 修正恰为 2.0e-3 -> 域均 2.0e-3 / 1 行 == cap -> **接受**。"""
    at_cap = cfg_ic.parse(
        _payload(mesh_rows=_plain_mesh(1), river_rows=[river_row(1, "-2.0e-3")])
    )
    accepted = state_qc.normalize_negative_residuals(at_cap)
    assert accepted.accepted is True
    assert accepted.evidence()["mean_river_correction_m"] == 2.0e-3

    over_cap_value = math.nextafter(2.0e-3, math.inf)
    over_cap = cfg_ic.parse(
        _payload(
            mesh_rows=_plain_mesh(1),
            river_rows=[river_row(1, f"-{over_cap_value!r}")],
        )
    )
    with pytest.raises(state_qc.StateResidualRejected) as excinfo:
        state_qc.normalize_negative_residuals(over_cap)
    assert excinfo.value.evidence["mean_river_correction_m"] == over_cap_value


def test_rejection_carries_the_full_evidence_and_produces_no_document() -> None:
    payload = _payload(
        mesh_rows=[mesh_row(1, unsat="-0.001")], river_rows=_plain_river(1)
    )
    doc = cfg_ic.parse(payload)

    with pytest.raises(state_qc.StateResidualRejected) as excinfo:
        state_qc.normalize_negative_residuals(doc)

    evidence = excinfo.value.evidence
    assert evidence["accepted"] is False
    assert evidence["policy"] == "unbounded_physical_zero_projection_v4"
    assert evidence["reason"] == (
        "unsat negative-residual domain-mean correction is 0.001000000 m, "
        "above 0.000200000 m"
    )
    assert str(excinfo.value) == evidence["reason"]
    assert isinstance(excinfo.value, ValueError)
    # 不产出修正后状态：原文档字节不变，异常里没有任何文档。
    assert cfg_ic.render(doc) == payload
    assert not hasattr(excinfo.value, "document")


def test_domain_mean_denominators_are_mesh_rows_and_river_rows_respectively() -> None:
    """把 unsat 分母误写成 river 行数（或反之）在此变红：两个分母刻意取不同值。"""
    payload = _payload(
        mesh_rows=[mesh_row(1, unsat="-4e-4"), *_plain_mesh(4)[1:]],
        river_rows=[river_row(1, "-8e-3"), *_plain_river(8)[1:]],
    )

    result = state_qc.normalize_negative_residuals(cfg_ic.parse(payload))
    evidence = result.evidence()

    assert evidence["mesh_row_count"] == 4
    assert evidence["river_row_count"] == 8
    # 手算：unsat 4e-4 / 4 行 = 1e-4（<= 2e-4）；river 8e-3 / 8 行 = 1e-3（<= 2e-3）。
    assert evidence["mean_unsat_correction_m"] == pytest.approx(1e-4, rel=1e-12)
    assert evidence["mean_river_correction_m"] == pytest.approx(1e-3, rel=1e-12)


def test_one_large_negative_stage_is_accepted_when_the_domain_mean_stays_under_the_cap() -> (
    None
):
    """river 100 行、其中一格 -0.15 -> 域均 0.15/100 = 1.5e-3 < 2.0e-3 -> **接受**。

    钉死 pin 模块头逐字记录的 owner directive「无逐格上限、可用性优先」：把
    `_NEGATIVE_ZERO_TOLERANCE` 误用成投影闸门、或补一个逐格上限的实现在此变红。
    """
    payload = _payload(
        mesh_rows=_plain_mesh(2),
        river_rows=[river_row(1, "-0.15"), *_plain_river(100)[1:]],
    )

    result = state_qc.normalize_negative_residuals(cfg_ic.parse(payload))
    evidence = result.evidence()

    assert result.accepted is True
    assert evidence["river_row_count"] == 100
    assert evidence["mean_river_correction_m"] == pytest.approx(1.5e-3, rel=1e-12)
    assert evidence["over_tolerance_clamp_count"] == 1
    assert evidence["max_correction_m"] == 0.15
    assert evidence["normalized_value_count"] == 1


def _descending_residual_rows(
    *, mesh_count: int
) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
    """幅度**降序**的负残差矩阵：每一列的**最大**幅值都落在该列的**第一**条命中行。

    降序是本 fixture 的全部判别力来源。既有 fixture 的负值恰好都按升序出现，于是把
    `max(累加值, correction)` 换成 `= correction`（last-wins）的变异体在全套下存活——
    升序下 last-wins 与 max 的结果**恒等**。降序布局把两者分开：`max_correction_m`
    0.5 vs 1e-09、`max_unsat_correction_m` 0.01 vs 1e-06、`max_river_correction_m`
    0.0005 vs 1e-09。

    规模按域均门算过（分母是 mesh 行数 / river 行数）：unsat 修正和 = 1e-2 + 1e-6，
    `mesh_count=100` 时域均 1.0001e-4 <= 2.0e-4 -> **接受**；`mesh_count=10` 时域均
    1.0001e-3 > 2.0e-4 -> 同一张矩阵走**拒绝**路径（两条路径共用一份累加器，`_reject`
    的证据必须一并钉住）。river 修正和 = 5e-4 + 1e-9，除以 river 行数 10 得 5.00001e-5
    <= 2.0e-3，两种规模下都在门内。

    三条超阈值负值钉在 **canopy**（非域均列；`over_tolerance_clamp_count` 的累加与列
    无关，pin `:246-247`），故它们只抬 clamp 计数、不进任何域均。unsat 的 -1e-2
    **恰等于** `_NEGATIVE_ZERO_TOLERANCE`（严格 `>`），不计入 clamp。
    """
    assert mesh_count >= 3, mesh_count
    mesh_rows = [
        mesh_row(1, canopy="-0.5", unsat="-1e-2"),
        mesh_row(2, canopy="-0.4", unsat="-1e-6"),
        mesh_row(3, canopy="-0.3"),
        *_plain_mesh(mesh_count)[3:],
    ]
    river_rows = [
        river_row(1, "-5e-4"),
        river_row(2, "-1e-9"),
        *_plain_river(10)[2:],
    ]
    return mesh_rows, river_rows


def test_evidence_maxima_are_running_maxima_and_the_clamp_count_accumulates() -> None:
    """接受路径上四个证据字段的降序判别器（`max_*` 三条 + `over_tolerance_clamp_count`）。"""
    mesh_rows, river_rows = _descending_residual_rows(mesh_count=100)

    result = state_qc.normalize_negative_residuals(
        cfg_ic.parse(_payload(mesh_rows=mesh_rows, river_rows=river_rows))
    )
    evidence = result.evidence()

    assert result.accepted is True
    assert evidence["mesh_row_count"] == 100
    assert evidence["river_row_count"] == 10
    # 逐值手算：七格负值 = 三行 canopy + 前两行 unsat + river 两行 stage。
    assert evidence["normalized_value_count"] == 7
    assert evidence["normalized_unsat_row_count"] == 2
    assert evidence["normalized_river_row_count"] == 2
    # 三条 max_*：取该列的**首**格（最大）而非**末**格（最小）。
    assert evidence["max_correction_m"] == 0.5
    assert evidence["max_unsat_correction_m"] == 0.01
    assert evidence["max_river_correction_m"] == 0.0005
    # last-wins 变异体会给出的那三个值，逐个否掉（写死否定值，读者不必回推变异体）。
    assert evidence["max_correction_m"] != 1e-9
    assert evidence["max_unsat_correction_m"] != 1e-6
    assert evidence["max_river_correction_m"] != 1e-9
    # 计数是**累加**：三条 canopy 超阈值（0.5 / 0.4 / 0.3）；unsat 的 1e-2 恰等于阈值，
    # 严格 `>` 不计。`over_tolerance_clamps = 1` 的变异体在此变红——既有断言全是
    # `== 0` / `== 1`，对「累加换成赋值」是盲的。
    assert evidence["over_tolerance_clamp_count"] == 3
    # 域均手算：(1e-2 + 1e-6) / 100、(5e-4 + 1e-9) / 10。
    assert evidence["mean_unsat_correction_m"] == pytest.approx(1.0001e-4, rel=1e-12)
    assert evidence["mean_river_correction_m"] == pytest.approx(5.00001e-5, rel=1e-12)


def test_rejected_evidence_carries_the_same_maxima_and_clamp_count() -> None:
    """`_reject` 与成功路径闭包在**同一组**累加器上，拒绝证据里的四个字段同样要钉住。

    `test_rejection_carries_the_full_evidence_and_produces_no_document` 只断
    `accepted` / `policy` / `reason`，对 `max_*` 与 clamp 计数一条断言都没有。
    """
    mesh_rows, river_rows = _descending_residual_rows(mesh_count=10)
    payload = _payload(mesh_rows=mesh_rows, river_rows=river_rows)
    doc = cfg_ic.parse(payload)

    with pytest.raises(state_qc.StateResidualRejected) as excinfo:
        state_qc.normalize_negative_residuals(doc)

    evidence = excinfo.value.evidence
    assert evidence["accepted"] is False
    # 手算：unsat 域均 (1e-2 + 1e-6) / 10 = 1.0001e-3 > 2.0e-4；river 域均仍在门内。
    assert evidence["reason"] == (
        "unsat negative-residual domain-mean correction is 0.001000100 m, "
        "above 0.000200000 m"
    )
    assert evidence["mean_unsat_correction_m"] == pytest.approx(1.0001e-3, rel=1e-12)
    assert evidence["mean_river_correction_m"] == pytest.approx(5.00001e-5, rel=1e-12)
    assert evidence["max_correction_m"] == 0.5
    assert evidence["max_unsat_correction_m"] == 0.01
    assert evidence["max_river_correction_m"] == 0.0005
    assert evidence["over_tolerance_clamp_count"] == 3
    # 不产出修正后状态。
    assert cfg_ic.render(doc) == payload


@pytest.mark.parametrize(
    ("token", "expected_clamps"),
    [("-9e-3", 0), ("-1.1e-2", 1)],
)
def test_negative_zero_tolerance_only_splits_the_evidence_never_gates_the_projection(
    token: str, expected_clamps: int
) -> None:
    """两条负值钉在 **canopy 列**（非域均列）：`over_tolerance_clamp_count` 的累加与列无关
    （pin `:246-247`），而放 unsat 列会让 mesh 5 行的域均 1.8e-3 > 2.0e-4 变成**拒绝**。"""
    payload = _payload(
        mesh_rows=[mesh_row(1, canopy=token), *_plain_mesh(5)[1:]],
        river_rows=_plain_river(2),
    )

    result = state_qc.normalize_negative_residuals(cfg_ic.parse(payload))
    evidence = result.evidence()

    assert result.accepted is True
    assert evidence["normalized_value_count"] == 1
    assert evidence["over_tolerance_clamp_count"] == expected_clamps
    assert evidence["mean_unsat_correction_m"] == 0.0
    assert b"-" not in cfg_ic.render(result.document).split(b"\n")[2]


@pytest.mark.parametrize(
    ("column", "token", "pin_outcome"),
    [
        ("unsat", "nan", "pin: 静默归零、域均 nan、两门放行 -> accepted"),
        ("unsat", "inf", "pin: `>= 0.0` 早退、原样存活 -> accepted"),
        ("canopy", "-inf", "pin: 非域均列，只计 max/clamp -> accepted"),
        ("unsat", "-inf", "pin: 域均 inf -> pin 唯一拒绝的那条"),
    ],
)
def test_non_finite_values_are_refused_before_any_projection(
    column: str, token: str, pin_outcome: str
) -> None:
    """裁决 4：四条 pin 路径齐备。只测第四条（`-inf` 落 unsat）的实现会全绿。"""
    payload = _payload(
        mesh_rows=[mesh_row(1, **{column: token}), *_plain_mesh(3)[1:]],
        river_rows=_plain_river(2),
    )
    doc = cfg_ic.parse(payload)

    with pytest.raises(ValueError) as excinfo:
        state_qc.normalize_negative_residuals(doc)

    assert "is not finite" in str(excinfo.value), pin_outcome
    # 不得先行归零：原文档字节不变，且异常不是阈值拒绝那一类。
    assert cfg_ic.render(doc) == payload
    assert not isinstance(excinfo.value, state_qc.StateResidualRejected)


def test_non_finite_value_in_the_river_section_is_refused_too() -> None:
    payload = _payload(
        mesh_rows=_plain_mesh(2), river_rows=[river_row(1, "-inf"), river_row(2)]
    )

    with pytest.raises(ValueError) as excinfo:
        state_qc.normalize_negative_residuals(cfg_ic.parse(payload))

    assert str(excinfo.value).startswith("river row 0 column 1 is not finite")


def test_normalization_is_idempotent() -> None:
    payload = _payload(
        mesh_rows=[mesh_row(1, unsat="-1e-6"), *_plain_mesh(4)[1:]],
        river_rows=[river_row(1, "-5e-4"), *_plain_river(4)[1:]],
    )
    once = state_qc.normalize_negative_residuals(cfg_ic.parse(payload))
    twice = state_qc.normalize_negative_residuals(once.document)

    assert cfg_ic.render(twice.document) == cfg_ic.render(once.document)
    assert twice.evidence()["normalized_value_count"] == 0


def test_projection_column_is_located_by_column_header_text_not_by_a_fixed_index() -> (
    None
):
    """列序被打乱时两个投影列仍被识别。

    这条**必须**在非 canonical 列序上跑：`Unsat` 在 canonical mesh 列头里恰好落在索引 4、
    `Stage` 在两列 river 列头里恰好落在索引 1，于是「按列头文本查找」与「写死索引」在
    canonical fixture 上观测重合，用例对 `unsat_index = 4` / `stage_index = 1` 两个变异体
    双双存活。此处 mesh 列头把 `Unsat` 挪到索引 1，river 列头插一个填充列把 `Stage` 挪到
    索引 2。
    """
    # 列头：Index Unsat Canopy Snow Surface GW —— unsat 在索引 1，不在 4。
    mesh_header = ("Index", "Unsat", "Canopy", "Snow", "Surface", "GW")
    # 列头：Index Depth Stage —— stage 在索引 2，不在 1。
    river_header = ("Index", "Depth", "Stage")
    synthetic = build_cfg_ic_rows(
        mesh_rows=[
            ("1", "-1e-6", "0.100000", "0.100000", "0.100000", "0.100000"),
            *(
                (str(element), "0.100000", "0.100000", "0.100000", "0.100000", "0.1")
                for element in range(2, 5)
            ),
        ],
        river_rows=[
            ("1", "0.100000", "-5e-4"),
            *((str(element), "0.100000", "0.100000") for element in range(2, 5)),
        ],
        mesh_header_tokens=mesh_header,
        river_header_tokens=river_header,
    )
    doc = cfg_ic.parse(synthetic.payload)
    # 构造自检：解析器确实按这两个列头分段，且投影列真的不在写死的位置上。
    assert mesh_header.index("Unsat") == 1
    assert river_header.index("Stage") == 2
    assert doc.mesh.row_count == 4
    assert doc.river is not None and doc.river.row_count == 4

    evidence = state_qc.normalize_negative_residuals(doc).evidence()

    assert evidence["normalized_value_count"] == 2
    assert evidence["normalized_unsat_row_count"] == 1
    assert evidence["max_unsat_correction_m"] == pytest.approx(1e-6, rel=1e-12)
    assert evidence["mean_unsat_correction_m"] == pytest.approx(1e-6 / 4, rel=1e-12)
    assert evidence["normalized_river_row_count"] == 1
    assert evidence["max_river_correction_m"] == pytest.approx(5e-4, rel=1e-12)
    assert evidence["mean_river_correction_m"] == pytest.approx(5e-4 / 4, rel=1e-12)


def test_projection_column_lookup_accepts_the_production_river_spelling() -> None:
    """生产拼写 `Index River_Stage`（真实 `.cfg.ic.update` 的形态）仍被识别为 stage 列。"""
    synthetic = build_cfg_ic_rows(
        mesh_rows=[mesh_row(1, unsat="-1e-6"), *_plain_mesh(4)[1:]],
        river_rows=[river_row(1, "-5e-4"), *_plain_river(4)[1:]],
        river_header_tokens=("Index", "River_Stage"),
    )

    evidence = state_qc.normalize_negative_residuals(
        cfg_ic.parse(synthetic.payload)
    ).evidence()

    assert evidence["normalized_unsat_row_count"] == 1
    assert evidence["normalized_river_row_count"] == 1
    assert evidence["mean_river_correction_m"] == pytest.approx(5e-4 / 4, rel=1e-12)


def test_lake_section_negatives_are_projected_but_not_counted_in_either_domain_mean() -> (
    None
):
    payload = _payload(
        mesh_rows=_plain_mesh(2),
        river_rows=_plain_river(2),
        lake_rows=[("1", "-0.2"), ("2", "0.100000")],
    )

    result = state_qc.normalize_negative_residuals(cfg_ic.parse(payload))
    evidence = result.evidence()

    assert result.accepted is True
    assert evidence["normalized_value_count"] == 1
    assert evidence["mean_unsat_correction_m"] == 0.0
    assert evidence["mean_river_correction_m"] == 0.0
    assert evidence["max_correction_m"] == 0.2
    assert evidence["over_tolerance_clamp_count"] == 1


# =========================== 溯源、隔离与裁决的机检闭合 ===========================


def test_ported_symbols_carry_their_own_provenance_comment() -> None:
    source = source_probe.read_source(state_qc.__file__)
    segments = source_probe.definition_segments(source)
    for symbol in PORTED_SYMBOLS:
        assert symbol in segments, symbol
        # 恰好一条：取窗按 `ast` 边界，删掉自己那行即变红。
        assert (
            segments[symbol].count("NWM@8ae9b8f2 packages/common/state_qc.py") == 1
        ), symbol


#: 判定路径含 pin 无对应物的闸门的符号 -> 其溯源注释 MUST 点名的（闸门, 偏离序号）。
#: 三个符号而不是两个：`normalize_negative_residuals` 的 `_require_finite` 同样是非 pin
#: 闸门（pin 的残差函数没有 finiteness 门，且超阈值时返回 `_rejected(...)` 而非抛异常）。
NON_PIN_GATE_SYMBOLS = {
    "run_state_variable_qc": ("_check_missing_sections", "偏离 5"),
    "state_ic_structure_complete": ("_check_missing_sections", "偏离 5"),
    "normalize_negative_residuals": ("_require_finite", "偏离 3"),
}

#: 「逐字移植」的**认领**形态：否定式（`故不是逐字移植`）不算认领，故排除紧邻的「不是」。
#: 只禁字面 `（逐字移植）` 是不够的——被抓到的实际措辞是
#: `（判定语义与证据形状逐字移植）`，它绕开了那条字面断言。
_VERBATIM_PORT_CLAIM = re.compile(r"(?<!不是)逐字移植")


def test_the_qc_symbols_with_non_pin_gates_do_not_claim_a_verbatim_port() -> None:
    """偏离 3/5 的注释面：判定路径含非 pin 闸门的函数，溯源注释不得认领「逐字移植」。

    `_check_missing_sections`（模块头偏离 5）在 `expected_*` 全为 `None` 的负载上与 pin
    判定反转；`_require_finite`（偏离 3）在 pin 的残差函数里根本不存在。注释若认领
    「逐字移植」，读者会按「与 pin 同判」去用这三个入口。
    """
    source = source_probe.read_source(state_qc.__file__)
    segments = source_probe.definition_segments(source)
    # 构造自检：正/否两向都要被这条正则分开，否则本用例对措辞变异体没有判别力。
    assert _VERBATIM_PORT_CLAIM.search("（判定语义与证据形状逐字移植）")
    assert _VERBATIM_PORT_CLAIM.search("（逐字移植）")
    assert not _VERBATIM_PORT_CLAIM.search("故不是逐字移植）")
    for symbol, (gate, deviation) in NON_PIN_GATE_SYMBOLS.items():
        comments = "\n".join(
            line.strip()
            for line in segments[symbol].splitlines()
            if line.strip().startswith("#")
        )
        assert "NWM@8ae9b8f2 packages/common/state_qc.py" in comments, symbol
        assert not _VERBATIM_PORT_CLAIM.search(comments), symbol
        assert gate in comments, symbol
        assert deviation in comments, symbol


def test_module_imports_the_cfg_ic_base_symbols_instead_of_re_porting_them() -> None:
    """`nwm-snapshot-inventory.md` §1 中 `packages/common/state_qc.py` 行的双权威副本禁令。"""
    names = source_probe.definition_names(source_probe.read_source(state_qc.__file__))
    assert names.isdisjoint(CFG_IC_BASE_SYMBOLS), names & set(CFG_IC_BASE_SYMBOLS)
    assert state_qc._as_float is cfg_ic._as_float
    assert state_qc.parse is cfg_ic.parse


def test_module_has_no_nwm_runtime_import_and_no_db_symbols() -> None:
    source = source_probe.read_source(state_qc.__file__)
    for forbidden in (
        "import packages",
        "from packages",
        "psycopg",
        "sqlalchemy",
        "DATABASE_URL",
        "sacct",
        "sbatch",
    ):
        assert forbidden not in source, forbidden


def test_module_writes_no_files() -> None:
    """裁决 6 的执行子句。"""
    source = source_probe.read_source(state_qc.__file__)
    assert source_probe.write_surface_calls(source) == []


def test_module_defines_no_rekey_or_water_balance_symbol() -> None:
    """裁决 7 + `_check_water_balance` non-goal 的执行子句。"""
    source = source_probe.read_source(state_qc.__file__)
    names = source_probe.definition_names(source)
    assert names.isdisjoint(NON_GOAL_SYMBOLS), names & set(NON_GOAL_SYMBOLS)
    for symbol in NON_GOAL_SYMBOLS:
        assert not hasattr(state_qc, symbol), symbol
    # `water_balance` 也不得作为任何公共入口的形参出现。
    for entry in (state_qc.run_state_variable_qc, state_qc.state_ic_structure_complete):
        assert "water_balance" not in entry.__code__.co_varnames


def test_pin_numeric_constants_are_preserved_verbatim() -> None:
    assert state_qc.MAX_UNSAT_MEAN_CORRECTION_M == 2.0e-4
    assert state_qc.MAX_RIVER_MEAN_CORRECTION_M == 2.0e-3
    assert state_qc._NEGATIVE_ZERO_TOLERANCE == 1.0e-2
    assert state_qc._MAX_STATE_VALUE_M == 1.0e6
    assert state_qc._MESH_STATE_COLUMNS == (
        "canopy",
        "snow",
        "surface",
        "unsat",
        "groundwater",
    )
    assert state_qc._RIVER_STATE_COLUMNS == ("river_stage",)
    assert state_qc._LAKE_STATE_COLUMNS == ("lake_stage",)
    assert state_qc._VALID_CFG_IC_HEADER_TOKEN_COUNTS == (3, 4)


def test_module_documents_the_deliberate_deviations() -> None:
    head = source_probe.module_docstring_block(
        source_probe.read_source(state_qc.__file__)
    )
    assert "刻意偏离" in head
    # 条数由 docstring 解析得出并与序号闭合（理由同 `test_cfg_ic.py` 的同名测试）。
    declared = source_probe.declared_deviation_count(head)
    assert declared == 5
    for ordinal in range(1, declared + 1):
        assert head.count(f"\n{ordinal}. ") == 1, ordinal
    assert f"\n{declared + 1}. " not in head
    assert "with_replaced_lines" in head
    assert "StateResidualRejected" in head
    assert "finiteness 先于负值" in head
    # 偏离 5：段缺席无条件判失败，与 pin 在 `expected_*` 全 None 的同一输入上判定反转。
    assert "_check_missing_sections" in head
    assert "判定反转" in head
    # 已知面与 non-goal 各留一条记述。
    assert "1_0" in head
    assert "_check_water_balance" in head


# --- header 判定基座的语义（4.3 的输入） ---


def test_header_minute_index_and_time_read_the_last_numeric_token() -> None:
    assert state_qc.cfg_ic_header_minute_index(["3", "6", "27000000.000000"]) == 2
    assert state_qc.cfg_ic_header_minute_index(["3", "4", "0", "27000000.0"]) == 3
    assert state_qc.cfg_ic_header_minute_index(["6"]) is None
    assert state_qc.cfg_ic_header_minute_index([]) is None
    assert (
        state_qc.cfg_ic_header_minute_time(["3", "6", "27000000.000000"]) == 27000000.0
    )
    assert state_qc.cfg_ic_header_minute_time(["6"]) is None


@pytest.mark.parametrize(
    ("tokens", "valid"),
    [
        (["3", "6", "27000000.0"], True),
        (["3", "4", "0", "27000000.0"], True),
        (["23106", "6"], False),
        (["3", "6", "0", "0.0", "5"], False),
        ([], False),
    ],
)
def test_header_shape_accepts_only_three_or_four_numeric_tokens(
    tokens: list[str], valid: bool
) -> None:
    shape = state_qc.cfg_ic_header_shape(tokens)
    assert shape.valid is valid
    assert (shape.reason is None) is valid


def test_header_shape_checks_the_expected_mesh_count_when_supplied() -> None:
    ok = state_qc.cfg_ic_header_shape(["3", "6", "27000000.0"], expected_mesh_count=3)
    assert ok.valid is True
    assert ok.mesh_count == 3

    bad = state_qc.cfg_ic_header_shape(["3", "6", "27000000.0"], expected_mesh_count=4)
    assert bad.valid is False
    assert bad.reason is not None
    assert "does not match the expected" in bad.reason
