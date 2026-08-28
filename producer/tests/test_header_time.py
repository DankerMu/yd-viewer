r"""`yd_producer.state.header_time` 的行为测试（移植原语的 pin 逐条对照）。

oracle 纪律：期望值来自 **pin 自身的语义声明**（「最后一个数值 token 即 minute-time」、
3/4 数值 token 的 shape 门）与手写 token 序列，不由被测函数回读。

承重条不是「3 token 正例通过」——那对任何宽松实现都恒绿；真正判别的是
2-token（pin issue #1197 形态）与 5-token 两侧的 fail-closed，以及非数值 token 穿插时
minute-time 仍取**最后一个数值 token 的下标**（不是最后一个 token 的下标）。
"""

from __future__ import annotations

import ast
import dataclasses
import math
import pathlib

import pytest

from yd_producer.state import cfg_ic, header_time

#: 从 NWM pin 移植的符号全集：每一个都必须自带自己的溯源注释。
PORTED_SYMBOLS = (
    "cfg_ic_header_minute_index",
    "cfg_ic_header_minute_time",
    "cfg_ic_header_shape",
    "CfgIcHeaderShape",
    "_VALID_CFG_IC_HEADER_TOKEN_COUNTS",
)

PROVENANCE_MARKER = "NWM@8ae9b8f2 packages/common/state_qc.py"


def _symbol_source_segments(source: str) -> dict[str, str]:
    """按 `ast` 的**符号边界**切出每个顶层符号自己的源码段（含其内部/前置注释）。

    覆盖三类节点：函数、类（`CfgIcHeaderShape`）与模块级赋值
    （`_VALID_CFG_IC_HEADER_TOKEN_COUNTS`）。定长切片窗口不可用：它会越进邻居，
    于是一个符号可以被**别人的**溯源注释满足，删掉自己那行照样绿。

    模块级赋值没有可容纳注释的语法体，故其窗口 = 赋值行 + 其上**紧邻的连续注释块**。
    """
    lines = source.splitlines()
    tree = ast.parse(source)
    segments: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            segments[node.name] = ast.get_source_segment(source, node) or ""
        elif isinstance(node, ast.Assign):
            start = node.lineno - 1
            while start > 0 and lines[start - 1].lstrip().startswith("#"):
                start -= 1
            block = "\n".join(lines[start : node.end_lineno])
            for target in node.targets:
                if isinstance(target, ast.Name):
                    segments[target.id] = block
    return segments


# --- `_as_float` 单一权威 ---


def test_as_float_is_reused_from_cfg_ic_not_redefined() -> None:
    """两份 `_as_float` 定义即双权威；本模块 MUST 复用 `cfg_ic` 的那一份。"""
    assert header_time._as_float is cfg_ic._as_float
    module_source = pathlib.Path(header_time.__file__).read_text(encoding="utf-8")
    assert "def _as_float" not in module_source


# --- cfg_ic_header_minute_index ---


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        # native 3-token `<mesh> <mesh-state-columns> <minute-time>`
        (["23106", "6", "27000000.000000"], 2),
        # 兼容 4-token `<mesh> <river> <lake> <minute-time>`
        (["23106", "413", "1", "27000000.000000"], 3),
        # 非数值 token 穿插：取最后一个**数值** token 的下标，不是最后一个 token 的下标
        (["mesh", "23106", "6", "27000000.000000"], 3),
        (["23106", "6", "27000000.000000", "trailing"], 2),
        # 少于两个数值 token（无「计数 + minute-time」对）
        (["23106", "6"], 1),
        (["23106"], None),
        ([], None),
        (["mesh", "count"], None),
        (["23106", "note"], None),
    ],
)
def test_minute_index_follows_the_last_numeric_token_rule(
    tokens: list[str], expected: int | None
) -> None:
    assert header_time.cfg_ic_header_minute_index(tokens) == expected


# --- cfg_ic_header_minute_time ---


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["23106", "6", "27000000.000000"], 27000000.0),
        (["23106", "413", "1", "27000000.000000"], 27000000.0),
        (["23106", "6", "720.000000"], 720.0),
        (["23106", "6", "-1.5"], -1.5),
        (["23106", "6", "1e3"], 1000.0),
        (["23106"], None),
        ([], None),
        (["header", "text"], None),
    ],
)
def test_minute_time_reads_the_trailing_numeric_token(
    tokens: list[str], expected: float | None
) -> None:
    assert header_time.cfg_ic_header_minute_time(tokens) == expected


@pytest.mark.parametrize("token", ["nan", "inf", "-inf"])
def test_minute_time_passes_non_finite_tokens_through(token: str) -> None:
    """pin 的 `_as_float` 接受 `nan`/`inf`；有限性闸在调用方（`controller`），不在此。"""
    value = header_time.cfg_ic_header_minute_time(["23106", "6", token])
    assert value is not None
    assert not math.isfinite(value)


# --- cfg_ic_header_shape ---


@pytest.mark.parametrize(
    ("tokens", "numeric_token_count"),
    [
        (["23106", "6", "27000000.000000"], 3),
        (["23106", "413", "1", "27000000.000000"], 4),
        (["23106", "6", "nan"], 3),
        (["23106", "6", "inf"], 3),
        # 非数值 token 不计入数值 token 数，剩下的 3 个数值 token 仍是合法形状
        (["23106", "6", "27000000.000000", "comment"], 3),
    ],
)
def test_shape_accepts_the_two_known_layouts(
    tokens: list[str], numeric_token_count: int
) -> None:
    shape = header_time.cfg_ic_header_shape(tokens)
    assert shape.valid is True
    assert shape.reason is None
    assert shape.numeric_token_count == numeric_token_count
    assert shape.mesh_count == 23106


@pytest.mark.parametrize(
    ("tokens", "numeric_token_count"),
    [
        ([], 0),
        (["23106"], 1),
        # pin issue #1197 的 `23106\t6` 形态：运行时会把列数当成 epoch-minute 覆写
        (["23106", "6"], 2),
        (["23106", "413", "1", "2", "27000000.000000"], 5),
        (["1", "2", "3", "4", "5", "6"], 6),
        (["Index", "Canopy"], 0),
    ],
)
def test_shape_refuses_unknown_layouts_fail_closed(
    tokens: list[str], numeric_token_count: int
) -> None:
    shape = header_time.cfg_ic_header_shape(tokens)
    assert shape.valid is False
    assert shape.reason is not None
    assert f"{numeric_token_count} numeric token(s)" in shape.reason
    assert shape.numeric_token_count == numeric_token_count


def test_shape_gate_constant_is_exactly_three_and_four() -> None:
    assert header_time._VALID_CFG_IC_HEADER_TOKEN_COUNTS == (3, 4)


@pytest.mark.parametrize(
    ("tokens", "expected_mesh_count", "valid"),
    [
        (["23106", "6", "27000000.000000"], 23106, True),
        (["23106", "6", "27000000.000000"], 23107, False),
        (["23106", "413", "1", "27000000.000000"], 23106, True),
        (["23106", "413", "1", "27000000.000000"], 99, False),
    ],
)
def test_shape_checks_expected_mesh_count_when_supplied(
    tokens: list[str], expected_mesh_count: int, valid: bool
) -> None:
    shape = header_time.cfg_ic_header_shape(
        tokens, expected_mesh_count=expected_mesh_count
    )
    assert shape.valid is valid
    if not valid:
        assert shape.reason is not None
        assert "does not match the expected" in shape.reason


def test_shape_mesh_count_is_none_when_leading_numeric_token_is_fractional() -> None:
    shape = header_time.cfg_ic_header_shape(["23106.5", "6", "27000000.000000"])
    assert shape.mesh_count is None
    assert shape.valid is True
    # 有 `expected_mesh_count` 时非整数首 token 即不匹配 → fail-closed
    mismatched = header_time.cfg_ic_header_shape(
        ["23106.5", "6", "27000000.000000"], expected_mesh_count=23106
    )
    assert mismatched.valid is False


def test_shape_mesh_count_is_none_when_there_is_no_numeric_token() -> None:
    shape = header_time.cfg_ic_header_shape(["Index", "Canopy"])
    assert shape.mesh_count is None
    assert shape.valid is False


def test_shape_verdict_is_frozen() -> None:
    shape = header_time.cfg_ic_header_shape(["23106", "6", "27000000.000000"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        shape.valid = False  # type: ignore[misc]


# --- 溯源与隔离 ---


def test_every_ported_symbol_carries_its_own_provenance_comment() -> None:
    source = pathlib.Path(header_time.__file__).read_text(encoding="utf-8")
    segments = _symbol_source_segments(source)
    for symbol in PORTED_SYMBOLS:
        assert symbol in segments, symbol
        assert PROVENANCE_MARKER in segments[symbol], symbol


def test_provenance_windows_do_not_leak_into_neighbour_symbols() -> None:
    """取窗自身的守卫：每个符号窗口里恰好只有自己那一条溯源标记。"""
    source = pathlib.Path(header_time.__file__).read_text(encoding="utf-8")
    segments = _symbol_source_segments(source)
    for symbol in PORTED_SYMBOLS:
        assert segments[symbol].count(PROVENANCE_MARKER) == 1, symbol


def test_module_stays_nwm_and_db_free() -> None:
    source = pathlib.Path(header_time.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "import packages",
        "from packages",
        "psycopg",
        "sqlalchemy",
        "DATABASE_URL",
        "sacct",
        "sbatch",
    ):
        assert forbidden not in source


def test_module_documents_the_deliberate_non_port() -> None:
    """裁决 2：刻意不移植 `_valid_time_from_header_minute`，且该决定写在模块头。"""
    source = pathlib.Path(header_time.__file__).read_text(encoding="utf-8")
    head = source[: source.index('"""', 3) + 3]
    assert "_valid_time_from_header_minute" in head
    assert "刻意偏离" in head
    for ordinal in ("\n1. ", "\n2. "):
        assert head.count(ordinal) == 1, ordinal
    assert "\n3. " not in head
    # 该符号 MUST NOT 出现在模块体里（只允许在模块头的「刻意不移植」说明中出现一次）
    assert source.count("_valid_time_from_header_minute") == 1
