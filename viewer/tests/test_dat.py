"""read_header() validates SHUD v2 structure without reading data bytes."""

from __future__ import annotations

import errno
import math
import os
import stat
import struct
from pathlib import Path

import pytest
from synthetic import write_dat, write_done, write_geometry

from yd_viewer.dat import DatError, read_header
from yd_viewer.geometry import load_geometry

IDS_1_TO_5 = (1, 2, 3, 4, 5)
AUTHORITY_1_TO_5 = {1, 2, 3, 4, 5}
VALID_HEADER_BYTES = 1080
FIXED_PREFIX_BYTES = 1040
NC_OFFSET = 1032
HUGE_NC = float(2**50)
ST_A = 19990101.0
ST_B = 19000101.0


def _fixed_prefix(st: float = 19000101.0, nc: float = 5.0) -> bytes:
    return bytes(1024) + struct.pack("<dd", st, nc)


def _overwrite_nc(path: Path, nc: float) -> None:
    payload = bytearray(path.read_bytes())
    payload[NC_OFFSET : NC_OFFSET + 8] = struct.pack("<d", nc)
    path.write_bytes(payload)


def _zero_column_168_row_bytes() -> bytes:
    payload = bytearray(1024)
    payload.extend(struct.pack("<dd", 19000101.0, 0.0))
    for row_index in range(168):
        payload.extend(struct.pack("<d", float(row_index * 60)))
    return bytes(payload)


def _reason(message: str, path: Path) -> str:
    return message.replace(str(path), "")


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


def _fail_close_after_real_close(
    monkeypatch: pytest.MonkeyPatch, path: Path, error: OSError
) -> dict[str, int | None]:
    """Close the target fd once for real, then raise. No retry."""

    real_open = os.open
    real_close = os.close
    target = os.path.realpath(path)
    captured: dict[str, int | None] = {"fd": None, "close_count": 0}

    def open_path(name, flags, *args, **kwargs):
        fd = real_open(name, flags, *args, **kwargs)
        try:
            opened = os.path.realpath(name)
        except OSError:
            opened = name
        if opened == target:
            captured["fd"] = fd
        return fd

    def close_fd(fd):
        if captured["fd"] is not None and fd == captured["fd"]:
            captured["close_count"] = int(captured["close_count"] or 0) + 1
            real_close(fd)
            raise error
        return real_close(fd)

    monkeypatch.setattr(os, "open", open_path)
    monkeypatch.setattr(os, "close", close_fd)
    return captured


def _assert_fd_closed(captured: dict[str, int | None]) -> None:
    fd = captured["fd"]
    assert fd is not None
    assert captured["close_count"] == 1
    with pytest.raises(OSError) as excinfo:
        os.fstat(fd)
    assert excinfo.value.errno == errno.EBADF


def test_valid_five_column_header_reads_exactly_1080_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_dat(tmp_path / "yd.rivqdown.dat", column_ids=IDS_1_TO_5)
    calls = _track_reads(monkeypatch, path)

    header = read_header(path, AUTHORITY_1_TO_5)

    assert header.nc == 5
    assert header.column_ids == IDS_1_TO_5
    assert header.row_count == 168
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
    path_a = write_dat(
        tmp_path / "st-a.dat",
        column_ids=IDS_1_TO_5,
        st=ST_A,
    )
    path_b = write_dat(
        tmp_path / "st-b.dat",
        column_ids=IDS_1_TO_5,
        st=ST_B,
    )

    header_a = read_header(path_a, AUTHORITY_1_TO_5)
    header_b = read_header(path_b, AUTHORITY_1_TO_5)

    assert ST_A != ST_B
    assert header_a.nc == header_b.nc == 5
    assert header_a.column_ids == header_b.column_ids == IDS_1_TO_5
    assert header_a.row_count == header_b.row_count == 168


def test_167_rows_fails_with_path_167_and_168(tmp_path: Path) -> None:
    path = write_dat(tmp_path / "short.dat", column_ids=IDS_1_TO_5, rows=167)

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    reason = _reason(message, path)
    assert str(path) in message
    assert "167" in reason
    assert "168" in reason


def test_column_ids_missing_5_extra_6(tmp_path: Path) -> None:
    path = write_dat(tmp_path / "ids.dat", column_ids=(1, 2, 3, 4, 6))

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    reason = _reason(message, path)
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

    assert str(path) in str(excinfo.value)


def test_truncated_column_id_table_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "trunc-ids.dat"
    path.write_bytes(_fixed_prefix(nc=5.0) + struct.pack("<dd", 1.0, 2.0))

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    assert str(path) in str(excinfo.value)


@pytest.mark.parametrize("nc", [math.nan, math.inf, -math.inf, -3.0])
def test_invalid_nc_is_rejected_with_count_reason(tmp_path: Path, nc: float) -> None:
    path = tmp_path / "bad-nc.dat"
    path.write_bytes(_fixed_prefix(nc=nc) + b"\x00" * 64)

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    reason = _reason(message, path)
    assert str(path) in message
    assert "列数" in reason
    assert repr(nc) in reason


def test_fractional_nc_over_valid_five_column_body_is_rejected(tmp_path: Path) -> None:
    path = write_dat(tmp_path / "frac-nc.dat", column_ids=IDS_1_TO_5)
    _overwrite_nc(path, 5.5)

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    reason = _reason(message, path)
    assert str(path) in message
    assert "列数" in reason
    assert "5.5" in reason


def test_zero_nc_with_168_minute_rows_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "zero-nc.dat"
    path.write_bytes(_zero_column_168_row_bytes())

    with pytest.raises(DatError) as excinfo:
        read_header(path, set())

    message = str(excinfo.value)
    reason = _reason(message, path)
    assert str(path) in message
    assert "列数" in reason
    assert "0.0" in reason


def test_forged_huge_nc_is_rejected_without_unbounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "huge-nc.dat"
    path.write_bytes(_fixed_prefix(nc=HUGE_NC))
    calls = _track_reads(monkeypatch, path)

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    assert str(path) in str(excinfo.value)
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


def test_missing_file_fails_with_path_and_cause(tmp_path: Path) -> None:
    path = tmp_path / "absent.dat"

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    assert str(path) in message
    assert "No such file or directory" in message
    assert not path.exists()


def test_read_oserror_includes_underlying_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_dat(tmp_path / "read-fail.dat", column_ids=IDS_1_TO_5)
    real_open = os.open
    real_read = os.read
    real_close = os.close
    target = os.path.realpath(path)
    tracked: set[int] = set()
    injected = OSError(errno.EIO, "injected header read EIO")

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
            raise injected
        return real_read(fd, n)

    def close_fd(fd):
        tracked.discard(fd)
        return real_close(fd)

    monkeypatch.setattr(os, "open", open_path)
    monkeypatch.setattr(os, "read", read_fd)
    monkeypatch.setattr(os, "close", close_fd)

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    assert str(path) in message
    assert "injected header read EIO" in message


def test_close_only_oserror_after_success_is_dat_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_dat(tmp_path / "close-ok.dat", column_ids=IDS_1_TO_5)
    injected = OSError(errno.EIO, "injected close EIO")
    captured = _fail_close_after_real_close(monkeypatch, path, injected)

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    assert str(path) in message
    assert "injected close EIO" in message
    _assert_fd_closed(captured)


def test_close_oserror_preserves_167_row_dat_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_dat(tmp_path / "close-short.dat", column_ids=IDS_1_TO_5, rows=167)
    injected = OSError(errno.EIO, "injected close EIO")
    captured = _fail_close_after_real_close(monkeypatch, path, injected)

    with pytest.raises(DatError) as excinfo:
        read_header(path, AUTHORITY_1_TO_5)

    message = str(excinfo.value)
    reason = _reason(message, path)
    assert str(path) in message
    assert "167" in reason
    assert "168" in reason
    notes = getattr(excinfo.value, "__notes__", [])
    assert any("injected close EIO" in note for note in notes)
    _assert_fd_closed(captured)
