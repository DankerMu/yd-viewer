# NWM@8ae9b8f2 tests/test_safe_fs.py
"""Bounded-read close-causality scenes for `safe_fs`.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest
from safe_fs_fixtures import _assert_fd_closed

from yd_producer.store.object_store import LocalObjectStore, ObjectStoreError
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    read_bytes_limited_no_follow,
    read_bytes_no_follow,
    read_tail_bytes_limited_no_follow,
)

# Bounded-read close causality (#122).
#
# The three public readers used to call `os.close(file_fd)` directly in
# `finally`. A close OSError then replaced the read primary (or cancellation)
# and escaped as a bare OSError. Discriminators are the file-fd close count,
# cause / object identity, the close note, and `fstat` -> EBADF after a
# successful close. The open wrapper records the file fd only after the real
# open returns, so directory fds are never the injected target.

_READ_PAYLOAD = b"abcdef"
_STORE_READ_KEY = "raw/gfs/2026050700/payload.bin"
_READERS = ("full", "limited", "tail")


def _read_with(
    reader: str, path: Path, root: Path, *, max_bytes: int | None = None
) -> bytes:
    if reader == "full":
        return read_bytes_no_follow(path, containment_root=root)
    if reader == "limited":
        return read_bytes_limited_no_follow(
            path,
            max_bytes=3 if max_bytes is None else max_bytes,
            containment_root=root,
        )
    return read_tail_bytes_limited_no_follow(
        path,
        max_bytes=3 if max_bytes is None else max_bytes,
        containment_root=root,
    )


def _plant_read_target(root: Path) -> Path:
    target = root / "payload.bin"
    target.write_bytes(_READ_PAYLOAD)
    return target


def _close_note_text(error: OSError) -> str:
    return f"file descriptor close also failed: {type(error).__name__}: {error}"


def _install_read_faults(
    monkeypatch: pytest.MonkeyPatch,
    *,
    read_error: BaseException | None = None,
    fstat_error: OSError | None = None,
    lseek_error: OSError | None = None,
    close_error: OSError | None = None,
) -> dict[str, int | None]:
    real_open = os.open
    real_close = os.close
    real_read = os.read
    real_fstat = os.fstat
    real_lseek = os.lseek
    captured: dict[str, int | None] = {"fd": None, "close_count": 0}
    file_fstats = {"count": 0}

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if dir_fd is not None and not (flags & os.O_DIRECTORY):
            captured["fd"] = fd
        return fd

    def closing(fd: int) -> None:
        if captured["fd"] is not None and fd == captured["fd"]:
            captured["close_count"] += 1
            real_close(fd)
            if close_error is not None:
                raise close_error
            return
        real_close(fd)

    def reading(fd: int, n: int) -> bytes:
        if (
            read_error is not None
            and captured["fd"] is not None
            and fd == captured["fd"]
        ):
            raise read_error
        return real_read(fd, n)

    def fstating(fd: int) -> os.stat_result:
        if captured["fd"] is not None and fd == captured["fd"]:
            file_fstats["count"] += 1
            if fstat_error is not None and file_fstats["count"] == 2:
                raise fstat_error
        return real_fstat(fd)

    def seeking(fd: int, pos: int, how: int) -> int:
        if (
            lseek_error is not None
            and captured["fd"] is not None
            and fd == captured["fd"]
        ):
            raise lseek_error
        return real_lseek(fd, pos, how)

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "close", closing)
    if read_error is not None:
        monkeypatch.setattr(os, "read", reading)
    if fstat_error is not None:
        monkeypatch.setattr(os, "fstat", fstating)
    if lseek_error is not None:
        monkeypatch.setattr(os, "lseek", seeking)
    return captured


@pytest.mark.parametrize("reader", _READERS)
def test_bounded_read_eio_and_close_estale_keeps_read_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root = tmp_path.resolve()
    target = _plant_read_target(root)
    read_error = OSError(errno.EIO, "injected read EIO")
    close_error = OSError(errno.ESTALE, "stale close")
    captured = _install_read_faults(
        monkeypatch, read_error=read_error, close_error=close_error
    )

    with pytest.raises(SafeFilesystemError) as info:
        _read_with(reader, target, root)

    assert info.value.kind == "io"
    assert info.value.__cause__ is read_error
    assert info.value.__cause__ is not close_error
    assert _close_note_text(close_error) in getattr(info.value, "__notes__", [])
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])
    assert target.read_bytes() == _READ_PAYLOAD


@pytest.mark.parametrize("reader", _READERS)
def test_bounded_read_close_estale_alone_is_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root = tmp_path.resolve()
    target = _plant_read_target(root)
    close_error = OSError(errno.ESTALE, "stale close")
    captured = _install_read_faults(monkeypatch, close_error=close_error)

    with pytest.raises(SafeFilesystemError) as info:
        _read_with(reader, target, root)

    assert info.value.kind == "io"
    assert info.value.__cause__ is close_error
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


@pytest.mark.parametrize("reader", _READERS)
def test_bounded_read_eio_alone_is_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root = tmp_path.resolve()
    target = _plant_read_target(root)
    read_error = OSError(errno.EIO, "injected read EIO")
    captured = _install_read_faults(monkeypatch, read_error=read_error)

    with pytest.raises(SafeFilesystemError) as info:
        _read_with(reader, target, root)

    assert info.value.kind == "io"
    assert info.value.__cause__ is read_error
    assert getattr(info.value, "__notes__", []) == []
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


@pytest.mark.parametrize("reader", _READERS)
@pytest.mark.parametrize(
    ("exc_type", "message"),
    [
        (KeyboardInterrupt, "primary interrupt"),
        (SystemExit, "primary exit"),
    ],
    ids=["keyboardinterrupt", "systemexit"],
)
def test_bounded_read_cancellation_keeps_identity_when_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader: str,
    exc_type: type[BaseException],
    message: str,
) -> None:
    root = tmp_path.resolve()
    target = _plant_read_target(root)
    injected = exc_type(message)
    close_error = OSError(errno.ESTALE, "stale close")
    captured = _install_read_faults(
        monkeypatch, read_error=injected, close_error=close_error
    )

    with pytest.raises(exc_type) as info:
        _read_with(reader, target, root)

    assert info.value is injected
    assert not isinstance(info.value, SafeFilesystemError)
    assert info.value is not close_error
    assert _close_note_text(close_error) in getattr(info.value, "__notes__", [])
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


@pytest.mark.parametrize(
    ("reader", "max_bytes", "expected"),
    [
        ("full", None, b"abcdef"),
        ("limited", 3, b"abcd"),
        ("tail", 3, b"def"),
    ],
)
def test_bounded_read_success_returns_literal_bytes_and_closes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader: str,
    max_bytes: int | None,
    expected: bytes,
) -> None:
    root = tmp_path.resolve()
    target = _plant_read_target(root)
    captured = _install_read_faults(monkeypatch)

    assert _read_with(reader, target, root, max_bytes=max_bytes) == expected

    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])
    assert target.read_bytes() == _READ_PAYLOAD


@pytest.mark.parametrize(
    ("reader", "expected"),
    [
        ("limited", b"a"),
        ("tail", b""),
    ],
)
def test_bounded_read_zero_byte_limit_returns_sentinel_or_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader: str,
    expected: bytes,
) -> None:
    root = tmp_path.resolve()
    target = _plant_read_target(root)
    captured = _install_read_faults(monkeypatch)

    assert _read_with(reader, target, root, max_bytes=0) == expected

    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


@pytest.mark.parametrize("reader", _READERS)
def test_bounded_read_missing_file_is_file_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root = tmp_path.resolve()
    missing = root / "absent.bin"
    captured = _install_read_faults(monkeypatch)

    with pytest.raises(FileNotFoundError):
        _read_with(reader, missing, root)

    assert captured["fd"] is None
    assert captured["close_count"] == 0


@pytest.mark.parametrize("reader", _READERS)
def test_bounded_read_symlink_is_refused_before_file_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root = tmp_path.resolve()
    real = _plant_read_target(root)
    linked = root / "payload.link"
    linked.symlink_to(real)
    captured = _install_read_faults(monkeypatch)

    with pytest.raises(SafeFilesystemError) as info:
        _read_with(reader, linked, root)

    assert info.value.kind == "unsafe"
    assert captured["fd"] is None
    assert captured["close_count"] == 0
    assert real.read_bytes() == _READ_PAYLOAD


@pytest.mark.parametrize("reader", _READERS)
def test_bounded_read_fifo_is_refused_before_file_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root = tmp_path.resolve()
    fifo = root / "payload.fifo"
    os.mkfifo(fifo)
    captured = _install_read_faults(monkeypatch)
    reader_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    writer_fd = os.open(fifo, os.O_WRONLY)
    try:
        with pytest.raises(SafeFilesystemError) as info:
            _read_with(reader, fifo, root)
    finally:
        os.close(writer_fd)
        os.close(reader_fd)

    assert info.value.kind == "unsafe"
    assert captured["fd"] is None
    assert captured["close_count"] == 0


def test_read_tail_fstat_eio_and_close_estale_keeps_fstat_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = _plant_read_target(root)
    fstat_error = OSError(errno.EIO, "injected fstat EIO")
    close_error = OSError(errno.ESTALE, "stale close")
    captured = _install_read_faults(
        monkeypatch, fstat_error=fstat_error, close_error=close_error
    )

    with pytest.raises(SafeFilesystemError) as info:
        read_tail_bytes_limited_no_follow(target, max_bytes=3, containment_root=root)

    assert info.value.kind == "io"
    assert info.value.__cause__ is fstat_error
    assert info.value.__cause__ is not close_error
    assert _close_note_text(close_error) in getattr(info.value, "__notes__", [])
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


def test_read_tail_lseek_eio_and_close_estale_keeps_lseek_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = _plant_read_target(root)
    lseek_error = OSError(errno.EIO, "injected lseek EIO")
    close_error = OSError(errno.ESTALE, "stale close")
    captured = _install_read_faults(
        monkeypatch, lseek_error=lseek_error, close_error=close_error
    )

    with pytest.raises(SafeFilesystemError) as info:
        read_tail_bytes_limited_no_follow(target, max_bytes=3, containment_root=root)

    assert info.value.kind == "io"
    assert info.value.__cause__ is lseek_error
    assert info.value.__cause__ is not close_error
    assert _close_note_text(close_error) in getattr(info.value, "__notes__", [])
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


@pytest.mark.parametrize("reader", _READERS)
def test_bounded_read_close_failure_does_not_note_handled_caller_valueerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root = tmp_path.resolve()
    target = _plant_read_target(root)
    close_error = OSError(errno.ESTALE, "stale close")
    captured = _install_read_faults(monkeypatch, close_error=close_error)
    handled = ValueError("already handled caller error")

    try:
        raise handled
    except ValueError:
        with pytest.raises(SafeFilesystemError) as info:
            _read_with(reader, target, root)

    assert info.value.kind == "io"
    assert info.value.__cause__ is close_error
    assert getattr(handled, "__notes__", []) == []
    assert handled.args == ("already handled caller error",)
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


@pytest.mark.parametrize("method", ["read_bytes", "read_bytes_limited"])
def test_object_store_read_wrappers_preserve_read_eio_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    root = tmp_path.resolve()
    store = LocalObjectStore(root=root)
    store.write_bytes_atomic(_STORE_READ_KEY, _READ_PAYLOAD)
    read_error = OSError(errno.EIO, "injected read EIO")
    close_error = OSError(errno.ESTALE, "stale close")
    captured = _install_read_faults(
        monkeypatch, read_error=read_error, close_error=close_error
    )

    with pytest.raises(ObjectStoreError) as info:
        if method == "read_bytes":
            store.read_bytes(_STORE_READ_KEY)
        else:
            store.read_bytes_limited(_STORE_READ_KEY, max_bytes=1024)

    wrapped = info.value.__cause__
    assert isinstance(wrapped, SafeFilesystemError)
    assert wrapped.kind == "io"
    assert wrapped.__cause__ is read_error
    assert wrapped.__cause__ is not close_error
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])
