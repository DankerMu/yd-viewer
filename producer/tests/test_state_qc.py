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

#: `nwm-snapshot-inventory.md:44` 的双权威副本禁令：这八个 MUST 从 `cfg_ic` **导入**。
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


def test_only_rows_that_actually_carry_a_negative_value_are_reserialised() -> None:
    """脏矩阵：CRLF + 行尾空格 + Tab 分隔 + 段间空行 + 混合记法 + 无末尾换行。

    裁决 1/2 的唯一判别力来源：pin 式整文件 `"\\n".join` 与行内 `"\\t".join` 只在这里变红。
    """
    synthetic = build_cfg_ic_rows(
        mesh_rows=[
            mesh_row(1, canopy="1e-3", snow="-0.0", surface="2.5E+01"),
            mesh_row(2, unsat="-1e-6"),
            mesh_row(3, canopy="0.000000"),
        ],
        river_rows=[river_row(1, "2.5E+01"), river_row(2, "-5e-4")],
        eol="\r\n",
        delimiter="\t",
        header_delimiter="   ",
        trailing_spaces="  ",
        blank_lines=True,
        trailing_newline=False,
    )
    doc = cfg_ic.parse(synthetic.payload)
    changed = {synthetic.mesh_data_indices[1], synthetic.river_data_indices[1]}

    result = state_qc.normalize_negative_residuals(doc)

    assert result.evidence()["normalized_value_count"] == 2
    for index, (old, new) in enumerate(
        zip(doc.lines, result.document.lines, strict=True)
    ):
        if index in changed:
            assert new != old
            continue
        assert new == old, f"line {index} changed: {old!r} -> {new!r}"

    for index in changed:
        old, new = doc.lines[index], result.document.lines[index]
        # 行尾符与行尾空格不变，行内分隔仍是 Tab，只有负值 token 变成 "0"。
        assert old.endswith("\r\n") == new.endswith("\r\n")
        assert new.count("\t") == old.count("\t")
        old_tokens, new_tokens = old.split(), new.split()
        assert len(old_tokens) == len(new_tokens)
        differing = [
            i
            for i, (a, b) in enumerate(zip(old_tokens, new_tokens, strict=True))
            if a != b
        ]
        assert len(differing) == 1
        assert new_tokens[differing[0]] == "0"
        assert old_tokens[differing[0]].startswith("-")
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
    """river 列头拼 `River_Stage` 时 stage 列仍被识别；unsat 不在索引 4 时同样。

    写死索引 4 的实现在生产拼写下会把修正记到错误的域均桶里。
    """
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


def test_module_imports_the_cfg_ic_base_symbols_instead_of_re_porting_them() -> None:
    """`nwm-snapshot-inventory.md:44` 的双权威副本禁令。"""
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
    assert declared == 4
    for ordinal in range(1, declared + 1):
        assert head.count(f"\n{ordinal}. ") == 1, ordinal
    assert f"\n{declared + 1}. " not in head
    assert "with_replaced_lines" in head
    assert "StateResidualRejected" in head
    assert "finiteness 先于负值" in head
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
