"""Requirement-driven tests for the SHUD parameter writer."""

from __future__ import annotations

import pytest
from assembly_fixtures import (
    PARAMETER_EXPECTED,
    PARAMETER_SAME_LINE,
    PARAMETER_SAME_LINE_EXPECTED,
    PARAMETER_TEMPLATE,
)

from yd_producer.assemble import AssemblyError, render_shud_parameters
from yd_producer.store.object_store import MAX_OBJECT_MANIFEST_BYTES


def test_registered_shapes_zero_match_and_line_endings() -> None:
    assert render_shud_parameters(PARAMETER_TEMPLATE) == PARAMETER_EXPECTED
    crlf = b"START={{START}}\r\nEND=${END}\r\nOTHER = keep\r\n"
    assert render_shud_parameters(crlf) == (
        b"START=0\r\nEND=7\r\nOTHER = keep\r\n"
        b"DT_QR_DOWN = 60\r\nUpdate_IC_STEP = 720\r\n"
        b"BINARY_OUTPUT = 1\r\nASCII_OUTPUT = 0\r\n"
    )
    no_final_newline = b"START = prior"
    result = render_shud_parameters(no_final_newline)
    assert result.startswith(b"START = 0\nEND = 7\n")
    assert result.endswith(b"ASCII_OUTPUT = 0\n")
    empty = render_shud_parameters(b"")
    assert empty == (
        b"START = 0\nEND = 7\nDT_QR_DOWN = 60\n"
        b"Update_IC_STEP = 720\nBINARY_OUTPUT = 1\nASCII_OUTPUT = 0\n"
    )


def test_same_line_different_keys_are_replaced_cumulatively() -> None:
    result = render_shud_parameters(PARAMETER_SAME_LINE)
    assert result == PARAMETER_SAME_LINE_EXPECTED
    assert b"{{START}}" not in result
    assert b"${END}" not in result
    assert b"{{DT_QR_DOWN}}" not in result
    assert b"keep 0 7 60 trailing\n" in result


def test_comments_and_substrings_are_not_matches() -> None:
    content = (
        b"# START = comment\n"
        b"KEEPSTART = leave\n"
        b"STARTX = leave\n"
        b"note {{STARTER}}\n"
        b"note ${ENDING}\n"
    )
    result = render_shud_parameters(content)
    assert b"# START = comment\n" in result
    assert b"KEEPSTART = leave\n" in result
    assert b"STARTX = leave\n" in result
    assert b"note {{STARTER}}\n" in result
    assert b"note ${ENDING}\n" in result
    assert b"START = 0\n" in result
    assert b"END = 7\n" in result


def test_assignment_comment_suffix_is_preserved() -> None:
    result = render_shud_parameters(b"START = old  # keep\n")
    assert result.startswith(b"START = 0  # keep\n")


@pytest.mark.parametrize(
    "content",
    [
        b"START={{START}}\nSTART=${START}\n",
        b"END = 1\nEND = 2\n",
        b"Update_IC_STEP={{Update_IC_STEP}}\nUpdate_IC_STEP = 3\n",
        b"keep {{START}} ${START}\n",
        b"\xff",
        b"x" * (MAX_OBJECT_MANIFEST_BYTES + 1),
    ],
)
def test_duplicate_invalid_utf8_and_oversize_fail(content: bytes) -> None:
    with pytest.raises(AssemblyError) as captured:
        render_shud_parameters(content)
    assert captured.value.phase == "validate"


def test_each_parameter_value_is_the_independent_literal() -> None:
    result = render_shud_parameters(PARAMETER_TEMPLATE)
    text = result.decode("utf-8")
    assert "START = 0" in text
    assert "END = 7" in text
    assert "DT_QR_DOWN = 60" in text
    assert "Update_IC_STEP = 720" in text
    assert "BINARY_OUTPUT = 1" in text
    assert "ASCII_OUTPUT = 0" in text
    assert result == PARAMETER_EXPECTED


def test_end_override_rewrites_only_the_end_assignment() -> None:
    """issue #17 证据 1：`end="0.5"` 只改 END，默认 bytes 与固定表都不被污染。

    期望值由 #15 的独立字面量 `PARAMETER_EXPECTED` 做**单点**字面替换得到，不由
    实现反推；「随后默认调用仍为 7」证明 override 走的是调用期映射而非全局状态。
    """
    recovery = b"END = 0.5\n"
    assert render_shud_parameters(PARAMETER_TEMPLATE, end="0.5") == (
        PARAMETER_EXPECTED.replace(b"END = 7\n", recovery)
    )
    assert render_shud_parameters(PARAMETER_TEMPLATE, end="7") == PARAMETER_EXPECTED
    assert render_shud_parameters(PARAMETER_TEMPLATE) == PARAMETER_EXPECTED
    # 其余五项、注释行与行尾规则逐字保持（byte-for-byte，只有 END 一行不同）。
    assert (
        sum(
            a != b
            for a, b in zip(
                PARAMETER_EXPECTED.splitlines(),
                (render_shud_parameters(PARAMETER_TEMPLATE, end="0.5").splitlines()),
            )
        )
        == 1
    )


# fmt: off
@pytest.mark.parametrize("end", ["0.5 ", "8", "0.500000", "07", "", 0.5, True, None,
                                 [], {}, ["7"]])  # 后三支 unhashable：成员判据会先抛裸 TypeError
def test_illegal_end_values_are_refused(end) -> None:
    """两个 literal 之外没有接受域：数值型/带空白/等价写法/**unhashable 容器**一律 validate 期拒绝。"""
# fmt: on
    with pytest.raises(AssemblyError) as captured:
        render_shud_parameters(PARAMETER_TEMPLATE, end=end)  # type: ignore[arg-type]
    assert captured.value.phase == "validate"
