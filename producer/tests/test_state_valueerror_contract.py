"""`state` 文档边界的单一 `ValueError` 契约（任务 4.5）。

期望值来自合成 `cfg.ic` 构造记录与固定 UTC 字面量，不由被测模块回算。
"""

from __future__ import annotations

import ast
import os
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest
from cfg_ic_fixtures import build_cfg_ic

from yd_producer.state import cfg_ic, restamp

_NO_RESULT = object()


class _UnknownOffsetTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> None:
        return None


class _InvalidOffsetTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | int:
        return 1


class _AstimezoneForbiddenDatetime(datetime):
    def astimezone(self, tz: tzinfo | None = None) -> datetime:
        raise AssertionError("unknown-offset datetime must not reach astimezone")


class _CopyForbiddenBytearray(bytearray):
    def __bytes__(self) -> bytes:
        raise AssertionError("over-limit bytearray was copied before its size check")


class _ChangingItemsMapping(Mapping[int, str]):
    def __init__(self, index: int) -> None:
        self.index = index
        self.items_calls = 0

    def __getitem__(self, key: int) -> str:
        if key != self.index:
            raise KeyError(key)
        return "3 6 29453760.000000"

    def __iter__(self) -> Iterator[int]:
        yield self.index

    def __len__(self) -> int:
        return 1

    def items(self) -> tuple[tuple[int, str | bytes], ...]:
        self.items_calls += 1
        if self.items_calls == 1:
            return ((self.index, "3 6 29453760.000000"),)
        return ((self.index, b"invalid-second-read"),)


def _source_document() -> cfg_ic.CfgIcDocument:
    return cfg_ic.parse(build_cfg_ic(mesh_count=3, river_count=2).payload)


def _assert_restamp_rejected(
    target: object,
    *,
    leaked_type: type[BaseException],
    cause_type: type[BaseException] | None,
) -> None:
    source = _source_document()
    before = cfg_ic.render(source)
    result = _NO_RESULT

    with pytest.raises(ValueError) as excinfo:
        result = restamp.restamp_to_absolute_time(source, target)  # type: ignore[arg-type]

    assert result is _NO_RESULT
    assert type(excinfo.value) is ValueError
    assert not isinstance(excinfo.value, leaked_type)
    if cause_type is not None:
        assert isinstance(excinfo.value.__cause__, cause_type)
    assert cfg_ic.render(source) == before


def _assert_replacement_rejected(
    replacements: object, *, leaked_type: type[BaseException]
) -> None:
    source = _source_document()
    before = cfg_ic.render(source)
    result = _NO_RESULT

    with pytest.raises(ValueError) as excinfo:
        result = source.with_replaced_lines(replacements)  # type: ignore[arg-type]

    assert result is _NO_RESULT
    assert type(excinfo.value) is ValueError
    assert not isinstance(excinfo.value, leaked_type)
    assert cfg_ic.render(source) == before


@pytest.mark.parametrize("host_timezone", ("UTC", "EST", "Asia/Shanghai"))
def test_unknown_offset_datetime_is_rejected_independently_of_host_timezone(
    host_timezone: str,
) -> None:
    if not hasattr(time, "tzset"):
        pytest.skip(
            "platform genuinely lacks time.tzset required for host-TZ isolation"
        )

    previous_timezone = os.environ.get("TZ")
    try:
        os.environ["TZ"] = host_timezone
        time.tzset()
        value = datetime(2026, 1, 1, tzinfo=_UnknownOffsetTimezone())
        with pytest.raises(ValueError) as excinfo:
            restamp._ensure_utc(value)
    finally:
        if previous_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_timezone
        time.tzset()

    assert type(excinfo.value) is ValueError


def test_unknown_offset_datetime_never_calls_astimezone() -> None:
    value = _AstimezoneForbiddenDatetime(2026, 1, 1, tzinfo=_UnknownOffsetTimezone())

    with pytest.raises(ValueError) as excinfo:
        restamp._ensure_utc(value)

    assert type(excinfo.value) is ValueError


def test_ensure_utc_preserves_naive_and_valid_offset_datetime_semantics() -> None:
    naive = datetime(2026, 1, 1)  # noqa: DTZ001
    offset = datetime(2026, 1, 1, 7, tzinfo=timezone(-timedelta(hours=5)))

    assert restamp._ensure_utc(naive) == datetime(2026, 1, 1, tzinfo=UTC)
    assert restamp._ensure_utc(offset) == datetime(2026, 1, 1, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("target", "leaked_type", "cause_type"),
    (
        pytest.param(None, AttributeError, None, id="none"),
        pytest.param(date(2026, 1, 1), AttributeError, None, id="date"),
        pytest.param(1.0, AttributeError, None, id="float"),
        pytest.param(
            datetime(2026, 1, 1, tzinfo=_InvalidOffsetTimezone()),
            TypeError,
            TypeError,
            id="invalid-offset-type",
        ),
        pytest.param(
            datetime.max.replace(tzinfo=timezone(-timedelta(hours=14))),
            OverflowError,
            OverflowError,
            id="maximum-at-negative-fourteen-hours",
        ),
        pytest.param(
            datetime.min.replace(tzinfo=timezone(timedelta(hours=14))),
            OverflowError,
            OverflowError,
            id="minimum-at-positive-fourteen-hours",
        ),
    ),
)
def test_restamp_rejects_invalid_targets_without_mutating_source_document(
    target: object,
    leaked_type: type[BaseException],
    cause_type: type[BaseException] | None,
) -> None:
    _assert_restamp_rejected(
        target,
        leaked_type=leaked_type,
        cause_type=cause_type,
    )


@pytest.mark.parametrize(
    ("replacements", "leaked_type"),
    (
        pytest.param(None, AttributeError, id="none"),
        pytest.param([(0, "x")], AttributeError, id="sequence-not-mapping"),
        pytest.param({0: None}, TypeError, id="none-value"),
        pytest.param({0: b"x"}, TypeError, id="bytes-value"),
        pytest.param({0: 1}, TypeError, id="integer-value"),
    ),
)
def test_replacement_boundary_rejects_invalid_containers_and_values(
    replacements: object, leaked_type: type[BaseException]
) -> None:
    _assert_replacement_rejected(replacements, leaked_type=leaked_type)


@pytest.mark.parametrize(
    "surrogate",
    (pytest.param("\ud800", id="high"), pytest.param("\udfff", id="low")),
)
def test_replacement_boundary_rejects_unrenderable_surrogates(surrogate: str) -> None:
    source = _source_document()
    before = cfg_ic.render(source)
    result = _NO_RESULT

    with pytest.raises(ValueError) as excinfo:
        result = source.with_replaced_lines(
            {source.header_index: f"3 6 27000000.000000{surrogate}"}
        )

    assert result is _NO_RESULT
    assert type(excinfo.value) is ValueError
    assert not isinstance(excinfo.value, UnicodeEncodeError)
    assert isinstance(excinfo.value.__cause__, UnicodeEncodeError)
    assert cfg_ic.render(source) == before


def test_legal_replacement_still_preserves_the_original_line_ending() -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2, eol="\r\n")
    source = cfg_ic.parse(built.payload)

    result = source.with_replaced_lines({source.header_index: "3 6 29453760.000000"})

    assert result.lines[source.header_index] == "3 6 29453760.000000\r\n"
    assert cfg_ic.render(result).endswith(b"\r\n")
    for index, line in enumerate(source.lines):
        if index != source.header_index:
            assert result.lines[index] == line


def test_replacement_mapping_is_snapshotted_before_application() -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2, eol="\r\n")
    source = cfg_ic.parse(built.payload)
    replacements = _ChangingItemsMapping(source.header_index)

    result = source.with_replaced_lines(replacements)

    assert replacements.items_calls == 1
    assert result.lines[source.header_index] == "3 6 29453760.000000\r\n"
    assert cfg_ic.render(result).endswith(b"\r\n")
    assert cfg_ic.render(source) == built.payload


def test_parse_equates_bytes_bytearray_and_memoryview_content() -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2, lake_count=1, eol="\r\n")
    expected = cfg_ic.parse(built.payload)

    for source in (built.payload, bytearray(built.payload), memoryview(built.payload)):
        document = cfg_ic.parse(source)
        assert document == expected
        assert document.header_index == built.header_index
        assert document.mesh.data_line_indices == built.mesh_data_indices
        assert document.river is not None
        assert document.river.data_line_indices == built.river_data_indices
        assert cfg_ic.render(document) == built.payload


@pytest.mark.parametrize(
    "as_memoryview", (False, True), ids=("bytearray", "memoryview")
)
def test_parse_snapshots_mutable_bytes_like_input(as_memoryview: bool) -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2)
    backing = bytearray(built.payload)
    source = memoryview(backing) if as_memoryview else backing

    document = cfg_ic.parse(source)
    backing[0] = ord("9")

    assert cfg_ic.render(document) == built.payload


def test_parse_keeps_path_string_and_bytes_compatibility(tmp_path: Path) -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2)
    source_path = built.write(tmp_path / "state.cfg.ic")

    for source in (source_path, str(source_path), built.payload):
        assert cfg_ic.render(cfg_ic.parse(source)) == built.payload


@pytest.mark.parametrize(
    "as_memoryview", (False, True), ids=("bytearray", "memoryview")
)
def test_parse_rejects_overlimit_bytes_like_content(
    as_memoryview: bool,
) -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2)
    backing = bytearray(built.payload)
    source = memoryview(backing) if as_memoryview else backing
    result = _NO_RESULT
    max_bytes = len(built.payload) - 1

    with pytest.raises(ValueError) as excinfo:
        result = cfg_ic.parse(source, max_bytes=max_bytes)

    assert result is _NO_RESULT
    assert type(excinfo.value) is ValueError
    assert f"exceeds size limit of {max_bytes} bytes" in str(excinfo.value)
    assert bytes(backing) == built.payload


def test_parse_uses_memoryview_nbytes_for_the_size_limit() -> None:
    payload = build_cfg_ic(mesh_count=3, river_count=2).payload
    if len(payload) % 2:
        payload += b" "
    view = memoryview(bytearray(payload)).cast("B", shape=(2, len(payload) // 2))
    result = _NO_RESULT

    assert len(view) == 2
    assert view.nbytes == len(payload)
    with pytest.raises(ValueError) as excinfo:
        result = cfg_ic.parse(view, max_bytes=2)

    assert result is _NO_RESULT
    assert type(excinfo.value) is ValueError
    assert "exceeds size limit of 2 bytes" in str(excinfo.value)


def test_parse_rejects_an_overlimit_bytearray_before_copying_it() -> None:
    source = _CopyForbiddenBytearray(b"too large")
    result = _NO_RESULT

    with pytest.raises(ValueError, match="exceeds size limit of 8 bytes") as excinfo:
        result = cfg_ic.parse(source, max_bytes=8)

    assert result is _NO_RESULT
    assert type(excinfo.value) is ValueError


def test_bytes_like_snapshot_helper_checks_its_bound_before_copying() -> None:
    tree = ast.parse(Path(cfg_ic.__file__).read_text(encoding="utf-8"))
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_snapshot_bytes_like"
    )
    guard = next(
        node
        for node in helper.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.ops[0], ast.Gt)
    )
    copies = [
        node
        for node in ast.walk(helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "bytes"
    ]

    assert len(copies) == 1
    assert guard.lineno < copies[0].lineno


def test_parse_documents_bytes_like_inputs_as_in_memory_content() -> None:
    documentation = cfg_ic.parse.__doc__ or ""

    assert "Path" in documentation and "str" in documentation
    for name in ("bytes", "bytearray", "memoryview"):
        assert name in documentation
    assert "文件内容" in documentation
