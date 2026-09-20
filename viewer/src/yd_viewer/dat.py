"""Read SHUD v2 DAT structure and converted rivqdown values."""

from __future__ import annotations

import math
import os
import struct
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path

_TEXT_HEADER_BYTES = 1024
_FLOAT64_BYTES = 8
_FIXED_PREFIX_BYTES = _TEXT_HEADER_BYTES + 2 * _FLOAT64_BYTES
_START_DAYS = 0
_END_DAYS = 7
_DT_QR_DOWN_MINUTES = 60
_MINUTES_PER_DAY = 24 * 60
_EXPECTED_ROWS = (_END_DAYS - _START_DAYS) * _MINUTES_PER_DAY // _DT_QR_DOWN_MINUTES
_M3_PER_DAY = 86400.0


class DatError(Exception):
    """DAT files are missing or do not match the viewer structure contract."""


@dataclass(frozen=True, kw_only=True)
class DatHeader:
    nc: int
    column_ids: tuple[int, ...]
    row_count: int


@dataclass(frozen=True, kw_only=True)
class DatFile:
    _values: array
    _stride: int
    _ordered_columns: tuple[int, ...]
    _column_by_reach: dict[int, int]

    def row(self, lead: int) -> tuple[float, ...]:
        base = lead * self._stride + 1
        return tuple(
            self._values[base + index] / _M3_PER_DAY for index in self._ordered_columns
        )

    def column(self, reach_id: int) -> tuple[float, ...]:
        index = self._column_by_reach[reach_id]
        stride = self._stride
        values = self._values
        return tuple(
            values[row * stride + 1 + index] / _M3_PER_DAY
            for row in range(_EXPECTED_ROWS)
        )


def read_header(path: str | Path, reach_ids: set[int]) -> DatHeader:
    dat = Path(path)
    try:
        fd = os.open(dat, os.O_RDONLY)
    except OSError as exc:
        raise DatError(f"无法读取 {dat}（{exc}）") from exc
    primary: BaseException | None = None
    header: DatHeader | None = None
    try:
        try:
            header = _read_header_fd(fd, dat, reach_ids)
        except OSError as exc:
            raise DatError(f"无法读取 {dat}（{exc}）") from exc
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            os.close(fd)
        except OSError as close_error:
            if primary is None:
                raise DatError(f"无法读取 {dat}（{close_error}）") from close_error
            primary.add_note(
                "DAT descriptor close also failed: "
                f"{type(close_error).__name__}: {close_error}"
            )
    assert header is not None
    return header


def read_dat(path: str | Path, reach_ids: set[int]) -> DatFile:
    dat = Path(path)
    try:
        fd = os.open(dat, os.O_RDONLY)
    except OSError as exc:
        raise DatError(f"无法读取 {dat}（{exc}）") from exc
    primary: BaseException | None = None
    result: DatFile | None = None
    try:
        try:
            result = _read_dat_fd(fd, dat, reach_ids)
        except OSError as exc:
            raise DatError(f"无法读取 {dat}（{exc}）") from exc
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            os.close(fd)
        except OSError as close_error:
            if primary is None:
                raise DatError(f"无法读取 {dat}（{close_error}）") from close_error
            primary.add_note(
                "DAT descriptor close also failed: "
                f"{type(close_error).__name__}: {close_error}"
            )
    assert result is not None
    return result


def _read_header_fd(fd: int, dat: Path, reach_ids: set[int]) -> DatHeader:
    size = os.fstat(fd).st_size
    prefix = os.read(fd, _FIXED_PREFIX_BYTES)
    if len(prefix) != _FIXED_PREFIX_BYTES:
        raise DatError(
            f"{dat} 定长头部不足 {_FIXED_PREFIX_BYTES} 字节（实得 {len(prefix)}）"
        )

    _, nc_raw = struct.unpack("<dd", prefix[_TEXT_HEADER_BYTES:_FIXED_PREFIX_BYTES])
    if not math.isfinite(nc_raw) or nc_raw != math.floor(nc_raw) or nc_raw <= 0:
        raise DatError(f"{dat} 的列数 {nc_raw!r} 不是正整数")
    nc = int(nc_raw)

    id_bytes = _FLOAT64_BYTES * nc
    if size < _FIXED_PREFIX_BYTES + id_bytes:
        raise DatError(
            f"{dat} 的列编号表需要 {id_bytes} 字节，"
            f"文件仅剩 {max(size - _FIXED_PREFIX_BYTES, 0)}"
        )

    data_bytes = size - _FIXED_PREFIX_BYTES - id_bytes
    row_stride = _FLOAT64_BYTES * (nc + 1)
    rows, remainder = divmod(data_bytes, row_stride)
    if remainder != 0:
        raise DatError(f"{dat} 的数据区大小 {data_bytes} 不能被 {row_stride} 整除")
    if rows != _EXPECTED_ROWS:
        raise DatError(f"{dat} 的数据区行数为 {rows}，期望 {_EXPECTED_ROWS}")

    raw_ids = os.read(fd, id_bytes)
    if len(raw_ids) != id_bytes:
        raise DatError(
            f"{dat} 的列编号表不完整：需要 {id_bytes} 字节，实得 {len(raw_ids)}"
        )

    column_ids: list[int] = []
    file_ids: set[int] = set()
    for index, raw in enumerate(struct.unpack(f"<{nc}d", raw_ids)):
        if not math.isfinite(raw) or raw != math.floor(raw):
            raise DatError(f"{dat} 的列编号[{index}] {raw!r} 不是整数")
        column_id = int(raw)
        if column_id in file_ids:
            raise DatError(f"{dat} 的列编号重复 {column_id}")
        file_ids.add(column_id)
        column_ids.append(column_id)

    missing = sorted(reach_ids - file_ids)
    extra = sorted(file_ids - reach_ids)
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append("缺少 " + ",".join(str(value) for value in missing))
        if extra:
            parts.append("多出 " + ",".join(str(value) for value in extra))
        raise DatError(f"{dat} 的列编号与权威集合不符（{'；'.join(parts)}）")

    return DatHeader(nc=nc, column_ids=tuple(column_ids), row_count=rows)


def _read_dat_fd(fd: int, dat: Path, reach_ids: set[int]) -> DatFile:
    header = _read_header_fd(fd, dat, reach_ids)
    size = os.fstat(fd).st_size
    os.lseek(fd, 0, os.SEEK_SET)
    payload = os.read(fd, size)
    if len(payload) != size:
        raise DatError(f"{dat} 整读不足 {size} 字节（实得 {len(payload)}）")

    data_offset = _FIXED_PREFIX_BYTES + _FLOAT64_BYTES * header.nc
    values = array("d")
    values.frombytes(memoryview(payload)[data_offset:])
    if sys.byteorder != "little":
        values.byteswap()

    stride = header.nc + 1
    for row in range(_EXPECTED_ROWS):
        expected = float(row * _DT_QR_DOWN_MINUTES)
        got = values[row * stride]
        if got != expected:
            raise DatError(f"{dat} 第 {row} 行分钟列为 {got}，期望 {expected}")

    column_by_reach = {
        reach_id: index for index, reach_id in enumerate(header.column_ids)
    }
    return DatFile(
        _values=values,
        _stride=stride,
        _ordered_columns=tuple(
            column_by_reach[reach_id] for reach_id in sorted(reach_ids)
        ),
        _column_by_reach=column_by_reach,
    )
