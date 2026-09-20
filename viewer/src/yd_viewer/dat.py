"""Read SHUD v2 DAT structure without loading the data region."""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path

_TEXT_HEADER_BYTES = 1024
_FLOAT64_BYTES = 8
_FIXED_PREFIX_BYTES = _TEXT_HEADER_BYTES + 2 * _FLOAT64_BYTES
_EXPECTED_ROWS = 168


class DatError(Exception):
    """DAT files are missing or do not match the viewer structure contract."""


@dataclass(frozen=True, kw_only=True)
class DatHeader:
    nc: int
    column_ids: tuple[int, ...]
    row_count: int


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
    for index, raw in enumerate(struct.unpack(f"<{nc}d", raw_ids)):
        if not math.isfinite(raw) or raw != math.floor(raw):
            raise DatError(f"{dat} 的列编号[{index}] {raw!r} 不是整数")
        column_ids.append(int(raw))

    file_ids = set(column_ids)
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
