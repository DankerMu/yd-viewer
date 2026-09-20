"""read_header() validates SHUD v2 structure without reading data bytes."""

from __future__ import annotations

import math
import os
import stat
import struct
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from synthetic import write_dat, write_done, write_geometry
from yd_viewer.dat import DatError, DatHeader, read_header
from yd_viewer.geometry import load_geometry

IDS_1_TO_5 = (1, 2, 3, 4, 5)
AUTHORITY_1_TO_5 = {1, 2, 3, 4, 5}
VALID_HEADER_BYTES = 1080
FIXED_PREFIX_BYTES = 1040
HUGE_NC = float(2**50)


def _fixed_prefix(st: float = 19000101.0, nc: float = 5.0) -> bytes:
    return bytes(1024) + struct.pack("<dd", st, nc)


def _track_reads(monkeypatch: pytest.MonkeyPatch, path: Path) -> list[tuple[int, int]]:
    """Record (position, request_size) for os.read on `path`."""

    tracked: set[int] = set()
    calls: list[tuple[int, int]] = []
    real_open = os.open
    real_read = os.read
    real_close = os.close
    target = os.path.realpath(path)

    def open_path(name, flags, *args, **kwargs):
        fd = real_open(name, flags, *args, **kwargs)
        try:
            opened = os.path.realpath(name)
        except OSError:
            opened = name
        if opened == target:
            tracked.add(fd)
        return fd

    def read_fd(fd, n):
        if fd in tracked:
            position = os.lseek(fd, 0, os.SEEK_CUR)
            calls.append((position, n))
        return real_read(fd, n)

    def close_fd(fd):
        tracked.discard(fd)
        return real_close(fd)

    monkeypatch.setattr(os, "open", open_path)
    monkeypatch.setattr(os, "read", read_fd)
    monkeypatch.setattr(os, "close", close_fd)
    return calls


def test_valid_five_column_header_reads_exactly_1080_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_dat(tmp_path / "yd.rivqdown.dat", column_ids=IDS_1_TO_5)
    calls = _track_reads(monkeypatch, path)

    header = read_header(path, AUTHORITY_1_TO_5)

    assert header.nc == 5
    assert header.column_ids == IDS_1_TO_5
    assert header.row_count == 168
    assert isinstance(header, DatHeader)
    with pytest.raises(FrozenInstanceError):
        header.nc = 0  # type: ignore[misc]
    requested = 0
    for position, size in calls:
        if position < VALID_HEADER_BYTES and position + size > VALID_HEADER_BYTES:
            raise AssertionError(
                f"read crossed header boundary: pos={position} n={size}"
            )
        requested += size
    returned_span = 0
    for position, size in calls:
        returned_span = max(returned_span, position + size)
    assert requested == VALID_HEADER_BYTES
    assert returned_span == VALID_HEADER_BYTES


def test_unsorted_column_ids_preserve_file_order(tmp_path: Path) -> None:
    path = write_dat(tmp_path / "yd.rivqdown.dat", column_ids=(3, 1, 2))

    header = read_header(path, {1, 2, 3})

    assert header.nc == 3
    assert header.column_ids == (3, 1, 2)
    assert header.row_count == 168


def test_wrong_st_is_accepted_and_not_used_as_time(tmp_path: Path) -> None:
    path = write_dat(
        tmp_path / "yd.rivqdown.dat",
        column_ids=IDS_1_TO_5,
        st=19990101.0,
    )

    header = read_header(path, AUTHORITY_1_TO_5)

    assert header.nc == 5
    assert header.column_ids == IDS_1_TO_5
    assert header.row_count == 168
    assert not hasattr(header, "st")
    assert not hasattr(header, "start_date")
    assert "19990101" not in repr(header)


def test_167_rows_fails_with_path_167_and_168(tmp_path: Path) -> None:
    path = write_dat(tmp_path / "short.dat", column_ids=IDS_1_TO_5, rows=167)

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    reason = message.replace(str(path), "")
    assert str(path) in message
    assert "167" in reason
    assert "168" in reason


def test_column_ids_missing_5_extra_6(tmp_path: Path) -> None:
    path = write_dat(tmp_path / "ids.dat", column_ids=(1, 2, 3, 4, 6))

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    reason = message.replace(str(path), "")
    assert str(path) in message
    assert "5" in reason
    assert "6" in reason


def test_trailing_byte_is_rejected(tmp_path: Path) -> None:
    path = write_dat(tmp_path / "trail.dat", column_ids=IDS_1_TO_5)
    path.write_bytes(path.read_bytes() + b"\x00")

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    assert str(path) in str(excinfo.value)


@pytest.mark.parametrize("size", [0, 16, 1023, 1039])
def test_truncated_fixed_header_is_rejected(tmp_path: Path, size: int) -> None:
    path = tmp_path / "trunc-fixed.dat"
    path.write_bytes(b"\x00" * size)

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    assert str(path) in message
    assert not isinstance(excinfo.value, struct.error)


def test_truncated_column_id_table_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "trunc-ids.dat"
    path.write_bytes(_fixed_prefix(nc=5.0) + struct.pack("<dd", 1.0, 2.0))

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    assert str(path) in message
    assert not isinstance(excinfo.value, struct.error)


@pytest.mark.parametrize(
    "nc",
    [math.nan, math.inf, -math.inf, 5.5, 0.0, -3.0],
)
def test_invalid_nc_is_rejected_with_path(tmp_path: Path, nc: float) -> None:
    path = tmp_path / "bad-nc.dat"
    path.write_bytes(_fixed_prefix(nc=nc) + b"\x00" * 64)

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    assert str(path) in message
    assert not isinstance(excinfo.value, (OverflowError, ValueError, struct.error))


def test_forged_huge_nc_is_rejected_without_unbounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "huge-nc.dat"
    path.write_bytes(_fixed_prefix(nc=HUGE_NC))
    calls = _track_reads(monkeypatch, path)

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    assert str(path) in str(excinfo.value)
    assert not isinstance(excinfo.value, (MemoryError, OverflowError, struct.error))
    for position, size in calls:
        assert size <= FIXED_PREFIX_BYTES
        assert position + size <= FIXED_PREFIX_BYTES
    assert sum(size for _position, size in calls) == FIXED_PREFIX_BYTES


def test_malformed_minutes_do_not_fail_header(tmp_path: Path) -> None:
    path = write_dat(
        tmp_path / "minutes.dat",
        column_ids=IDS_1_TO_5,
        minutes=[float(60 + 60 * row) for row in range(168)],
    )

    header = read_header(path, AUTHORITY_1_TO_5)

    assert header.row_count == 168


def test_nan_cells_do_not_fail_header(tmp_path: Path) -> None:
    path = write_dat(
        tmp_path / "nan.dat",
        column_ids=IDS_1_TO_5,
        nan_cells=((5, 0), (7, 3)),
    )

    header = read_header(path, AUTHORITY_1_TO_5)

    assert header.nc == 5
    assert header.row_count == 168


def test_done_regular_file_and_symlink(tmp_path: Path) -> None:
    regular = write_done(tmp_path / "DONE")
    linked = write_done(tmp_path / "linked" / "DONE", symlink=True)

    assert stat.S_ISREG(regular.stat().st_mode)
    assert stat.S_ISLNK(linked.lstat().st_mode)
    assert not stat.S_ISLNK(regular.lstat().st_mode)


@pytest.mark.parametrize("kind", ["Polygon", "MultiPolygon"])
def test_synthetic_geometry_loads_with_real_loader(tmp_path: Path, kind: str) -> None:
    directory = write_geometry(tmp_path / kind, reach_ids=IDS_1_TO_5, boundary=kind)

    geometry = load_geometry(directory)

    assert geometry.reach_ids == AUTHORITY_1_TO_5


def test_missing_file_fails_with_path(tmp_path: Path) -> None:
    path = tmp_path / "absent.dat"

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    assert str(path) in str(excinfo.value)
    assert not path.exists()
