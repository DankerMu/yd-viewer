"""`yd_producer.raw.cycle_hours` 显式入参用例（清单 §4 风险 8：pin 上无可快照的既有测试）。

期望值来源是 pin `8ae9b8f2` 的 `workers/data_adapters/cycle_hours.py` 源码，不从实现回读：
`parse_cycle_hours_utc`(L14-27) 空串 / 空 token / 非整数 token 三条抛错文案，
`normalize_cycle_hours_utc`(L30-48) 去重 + 升序 + 0..23 值域 + 非空校验。
`env_cycle_hours_utc` 在本快照里已按清单 `剥离点` 删掉 `os.getenv`，改为**必填**的可空
字符串形参（D4 零默认：形参本身不带 `= None` 缺省，漏传即 TypeError）。
"""

from __future__ import annotations

import pytest

from yd_producer.raw import cycle_hours as module
from yd_producer.raw.cycle_hours import (
    env_cycle_hours_utc,
    normalize_cycle_hours_utc,
    parse_cycle_hours_utc,
)


def test_module_carries_no_environment_surface() -> None:
    # D4 零默认 / DB-free：`os` 已随 `os.getenv` 一并删除，模块里不存在环境读取。
    assert not hasattr(module, "os")


# --- parse_cycle_hours_utc ----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", (0,)),
        ("0,12", (0, 12)),
        ("12,0", (0, 12)),
        (" 12 , 0 ", (0, 12)),
        ("0,0,12", (0, 12)),
        ("23", (23,)),
    ],
)
def test_parse_cycle_hours_utc_normalizes_to_sorted_unique(
    value: str, expected: tuple[int, ...]
) -> None:
    assert parse_cycle_hours_utc(value, "cycle_hours_utc") == expected


def test_parse_cycle_hours_utc_rejects_empty_string() -> None:
    with pytest.raises(
        ValueError, match="cycle_hours_utc must contain at least one UTC cycle hour"
    ):
        parse_cycle_hours_utc("", "cycle_hours_utc")


@pytest.mark.parametrize("value", ["0,", ",12", "0, ,12"])
def test_parse_cycle_hours_utc_rejects_empty_tokens(value: str) -> None:
    with pytest.raises(ValueError, match="must not contain empty cycle hour tokens"):
        parse_cycle_hours_utc(value, "cycle_hours_utc")


@pytest.mark.parametrize("value", ["0,abc", "abc", "0,1.5"])
def test_parse_cycle_hours_utc_rejects_non_integer_tokens(value: str) -> None:
    with pytest.raises(ValueError, match="must contain integer UTC cycle hours"):
        parse_cycle_hours_utc(value, "cycle_hours_utc")


@pytest.mark.parametrize("value", ["0,25", "24", "-1", "0,-12"])
def test_parse_cycle_hours_utc_rejects_out_of_range_hours(value: str) -> None:
    with pytest.raises(ValueError, match="must only contain values in 0..23"):
        parse_cycle_hours_utc(value, "cycle_hours_utc")


# --- normalize_cycle_hours_utc ------------------------------------------------


def test_normalize_cycle_hours_utc_sorts_and_dedups() -> None:
    assert normalize_cycle_hours_utc([12, 0, 12]) == (0, 12)


def test_normalize_cycle_hours_utc_rejects_an_empty_sequence() -> None:
    with pytest.raises(
        ValueError, match="cycle_hours_utc must contain at least one UTC cycle hour"
    ):
        normalize_cycle_hours_utc([])


def test_normalize_cycle_hours_utc_rejects_bool_and_non_int_members() -> None:
    with pytest.raises(ValueError, match="must contain integer UTC cycle hours"):
        normalize_cycle_hours_utc([True])
    with pytest.raises(ValueError, match="must contain integer UTC cycle hours"):
        normalize_cycle_hours_utc(["0"])


def test_normalize_cycle_hours_utc_rejects_a_non_iterable() -> None:
    with pytest.raises(ValueError, match="must contain integer UTC cycle hours"):
        normalize_cycle_hours_utc(0)  # type: ignore[arg-type]


def test_normalize_cycle_hours_utc_field_name_reaches_the_message() -> None:
    with pytest.raises(
        ValueError, match="raw.cycle_hours_utc must only contain values in 0..23"
    ):
        normalize_cycle_hours_utc([25], field_name="raw.cycle_hours_utc")


# --- env_cycle_hours_utc（显式入参，零 os.getenv） -----------------------------


def test_env_cycle_hours_utc_none_value_falls_back_to_the_caller_supplied_default() -> (
    None
):
    assert env_cycle_hours_utc("raw.cycle_hours_utc", [12, 0], None) == (0, 12)


def test_env_cycle_hours_utc_none_value_still_validates_the_default() -> None:
    with pytest.raises(
        ValueError, match="raw.cycle_hours_utc must contain at least one UTC cycle hour"
    ):
        env_cycle_hours_utc("raw.cycle_hours_utc", [], None)


def test_env_cycle_hours_utc_parses_an_explicit_string_value() -> None:
    assert env_cycle_hours_utc("raw.cycle_hours_utc", [0], "12,0") == (0, 12)


def test_env_cycle_hours_utc_propagates_a_malformed_explicit_value() -> None:
    with pytest.raises(
        ValueError, match="raw.cycle_hours_utc must contain integer UTC cycle hours"
    ):
        env_cycle_hours_utc("raw.cycle_hours_utc", [0], "0,abc")


def test_env_cycle_hours_utc_requires_the_value_argument() -> None:
    # D4：形参无缺省，漏传即 fail closed，不会静默回落到某个内置值。
    with pytest.raises(TypeError):
        env_cycle_hours_utc("raw.cycle_hours_utc", [0])  # type: ignore[call-arg]
