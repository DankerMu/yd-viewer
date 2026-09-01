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
