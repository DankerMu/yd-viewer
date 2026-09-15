# NWM@8ae9b8f2 tests/test_safe_fs.py
"""Atomic-write cleanup scenes for `safe_fs`.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from yd_producer.store.object_store import LocalObjectStore, ObjectStoreError
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    atomic_write_bytes_no_follow,
)

# Atomic-write cleanup on non-OSError / BaseException (#42).
#
# `atomic_write_bytes_no_follow` used to close the write fd and unlink the
# owned temp only in the SafeFilesystemError / OSError arms. TypeError (from
# `memoryview` after O_EXCL create), MemoryError, KeyboardInterrupt and
# SystemExit skipped that cleanup and leaked both the fd and the dotfile.
# Discriminators below are the leaked fd (fstat -> EBADF, not /proc), the
# owned `.{name}.{hex}.{suffix}` name, and a same-shape foreign dotfile that a
# glob sweep would delete.

_ATOMIC_FOREIGN_HEX = "f" * 32
_ATOMIC_OLD = b"old-target-bytes"
_ATOMIC_NEW = b"replacement-payload"
_ATOMIC_FOREIGN = b"foreign-dotfile-bytes"
_ATOMIC_SIBLING = b"sibling-bytes"


def _owned_temp_name(
    directory: Path, target_name: str, suffix: str, opened_name: str
) -> Path:
    temp = directory / opened_name
    assert opened_name.startswith(f".{target_name}.")
    assert opened_name.endswith(f".{suffix}")
    return temp


def _assert_write_fd_closed(file_fd: int) -> None:
    with pytest.raises(OSError) as info:
        os.fstat(file_fd)
    assert info.value.errno == errno.EBADF


def _capture_exclusive_write_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, int | str | None]:
    real_open = os.open
    captured: dict[str, int | str | None] = {
        "fd": None,
        "name": None,
        "parent_fd": None,
    }

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        name = path if isinstance(path, str) else os.fsdecode(path)
        if (
            flags & os.O_CREAT
            and flags & os.O_EXCL
            and flags & os.O_WRONLY
            and name.startswith(".")
        ):
            captured["fd"] = fd
            captured["name"] = name
            if dir_fd is not None:
                captured["parent_fd"] = dir_fd
        return fd

    monkeypatch.setattr(os, "open", opening)
    return captured


def _plant_atomic_neighbors(
    directory: Path, target: Path, suffix: str
) -> tuple[Path, Path]:
    target.write_bytes(_ATOMIC_OLD)
    sibling = directory / "sibling.bin"
    sibling.write_bytes(_ATOMIC_SIBLING)
    foreign = directory / f".{target.name}.{_ATOMIC_FOREIGN_HEX}.{suffix}"
    foreign.write_bytes(_ATOMIC_FOREIGN)
    return sibling, foreign


def _assert_neighbors_untouched(target: Path, sibling: Path, foreign: Path) -> None:
    assert target.read_bytes() == _ATOMIC_OLD
    assert sibling.read_bytes() == _ATOMIC_SIBLING
    assert foreign.read_bytes() == _ATOMIC_FOREIGN


@pytest.mark.parametrize("temp_suffix", ["tmp", "part"])
def test_atomic_write_cleans_owned_temp_when_content_is_not_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temp_suffix: str
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, temp_suffix)
    captured = _capture_exclusive_write_fd(monkeypatch)

    with pytest.raises(TypeError):
        atomic_write_bytes_no_follow(
            target,
            "not-bytes",  # type: ignore[arg-type]
            containment_root=root,
            temp_suffix=temp_suffix,
        )

    assert captured["fd"] is not None
    assert captured["name"] is not None
    _assert_write_fd_closed(captured["fd"])
    owned = _owned_temp_name(root, target.name, temp_suffix, captured["name"])
    assert not owned.exists()
    _assert_neighbors_untouched(target, sibling, foreign)


@pytest.mark.parametrize("temp_suffix", ["tmp", "part"])
@pytest.mark.parametrize("fault", ["write", "fsync"])
@pytest.mark.parametrize("exc_type", [MemoryError, KeyboardInterrupt, SystemExit])
def test_atomic_write_cleans_owned_temp_on_injected_pre_replace_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    temp_suffix: str,
    fault: str,
    exc_type: type[BaseException],
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, temp_suffix)
    captured = _capture_exclusive_write_fd(monkeypatch)
    injected = exc_type("injected atomic-write fault")
    real_write = os.write
    real_fsync = os.fsync

    def writing(fd: int, data: bytes | bytearray | memoryview) -> int:
        if fault == "write" and fd == captured["fd"]:
            raise injected
        return real_write(fd, data)

    def syncing(fd: int) -> None:
        if fault == "fsync" and fd == captured["fd"]:
            raise injected
        real_fsync(fd)

    monkeypatch.setattr(os, "write", writing)
    monkeypatch.setattr(os, "fsync", syncing)

    with pytest.raises(exc_type) as info:
        atomic_write_bytes_no_follow(
            target,
            _ATOMIC_NEW,
            containment_root=root,
            temp_suffix=temp_suffix,
        )

    assert info.value is injected
    assert captured["fd"] is not None
    assert captured["name"] is not None
    _assert_write_fd_closed(captured["fd"])
    owned = _owned_temp_name(root, target.name, temp_suffix, captured["name"])
    assert not owned.exists()
    _assert_neighbors_untouched(target, sibling, foreign)


@pytest.mark.parametrize("temp_suffix", ["tmp", "part"])
def test_atomic_write_cleanup_close_oserror_preserves_primary_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temp_suffix: str
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, temp_suffix)
    captured = _capture_exclusive_write_fd(monkeypatch)
    injected = KeyboardInterrupt("primary interrupt")
    close_error = OSError(errno.EBADF, "injected write-fd close failure")
    real_write = os.write
    real_close = os.close

    def writing(fd: int, data: bytes | bytearray | memoryview) -> int:
        if fd == captured["fd"]:
            raise injected
        return real_write(fd, data)

    def closing(fd: int) -> None:
        if fd == captured["fd"]:
            real_close(fd)
            raise close_error
        real_close(fd)

    monkeypatch.setattr(os, "write", writing)
    monkeypatch.setattr(os, "close", closing)

    with pytest.raises(KeyboardInterrupt) as info:
        atomic_write_bytes_no_follow(
            target,
            _ATOMIC_NEW,
            containment_root=root,
            temp_suffix=temp_suffix,
        )

    assert info.value is injected
    assert info.value is not close_error
    assert captured["fd"] is not None
    assert captured["name"] is not None
    _assert_write_fd_closed(captured["fd"])
    owned = _owned_temp_name(root, target.name, temp_suffix, captured["name"])
    assert not owned.exists()
    _assert_neighbors_untouched(target, sibling, foreign)


@pytest.mark.parametrize("temp_suffix", ["tmp", "part"])
def test_atomic_write_keeps_published_target_when_directory_fsync_is_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temp_suffix: str
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, temp_suffix)
    captured = _capture_exclusive_write_fd(monkeypatch)
    injected = KeyboardInterrupt("post-replace interrupt")
    real_fsync = os.fsync

    def syncing(fd: int) -> None:
        if captured["fd"] is not None and fd != captured["fd"]:
            raise injected
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", syncing)

    with pytest.raises(KeyboardInterrupt) as info:
        atomic_write_bytes_no_follow(
            target,
            _ATOMIC_NEW,
            containment_root=root,
            temp_suffix=temp_suffix,
        )

    assert info.value is injected
    assert captured["fd"] is not None
    assert captured["name"] is not None
    _assert_write_fd_closed(captured["fd"])
    owned = _owned_temp_name(root, target.name, temp_suffix, captured["name"])
    assert not owned.exists()
    assert target.read_bytes() == _ATOMIC_NEW
    assert sibling.read_bytes() == _ATOMIC_SIBLING
    assert foreign.read_bytes() == _ATOMIC_FOREIGN


@pytest.mark.parametrize("temp_suffix", ["tmp", "part"])
def test_atomic_write_success_leaves_complete_bytes_and_no_owned_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temp_suffix: str
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, temp_suffix)
    captured = _capture_exclusive_write_fd(monkeypatch)

    landed = atomic_write_bytes_no_follow(
        target,
        _ATOMIC_NEW,
        containment_root=root,
        temp_suffix=temp_suffix,
    )

    assert landed == target
    assert captured["fd"] is not None
    assert captured["name"] is not None
    _assert_write_fd_closed(captured["fd"])
    owned = _owned_temp_name(root, target.name, temp_suffix, captured["name"])
    assert not owned.exists()
    assert target.read_bytes() == _ATOMIC_NEW
    assert sibling.read_bytes() == _ATOMIC_SIBLING
    assert foreign.read_bytes() == _ATOMIC_FOREIGN


def test_atomic_write_pre_replace_oserror_is_io_and_keeps_old_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, "tmp")
    captured = _capture_exclusive_write_fd(monkeypatch)
    real_write = os.write

    def writing(fd: int, data: bytes | bytearray | memoryview) -> int:
        if fd == captured["fd"]:
            raise OSError(errno.ENOSPC, "injected write ENOSPC")
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", writing)

    with pytest.raises(SafeFilesystemError) as info:
        atomic_write_bytes_no_follow(
            target, _ATOMIC_NEW, containment_root=root, temp_suffix="tmp"
        )

    assert info.value.kind == "io"
    assert captured["fd"] is not None
    assert captured["name"] is not None
    _assert_write_fd_closed(captured["fd"])
    owned = _owned_temp_name(root, target.name, "tmp", captured["name"])
    assert not owned.exists()
    _assert_neighbors_untouched(target, sibling, foreign)


def test_atomic_write_strict_post_replace_oserror_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, "tmp")
    captured = _capture_exclusive_write_fd(monkeypatch)
    real_fsync = os.fsync

    def syncing(fd: int) -> None:
        if captured["fd"] is not None and fd != captured["fd"]:
            raise OSError(errno.EIO, "injected directory fsync EIO")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", syncing)

    with pytest.raises(SafeFilesystemError) as info:
        atomic_write_bytes_no_follow(
            target,
            _ATOMIC_NEW,
            containment_root=root,
            temp_suffix="tmp",
            require_durable_replace=True,
        )

    assert info.value.kind == "indeterminate"
    assert captured["fd"] is not None
    assert captured["name"] is not None
    _assert_write_fd_closed(captured["fd"])
    owned = _owned_temp_name(root, target.name, "tmp", captured["name"])
    assert not owned.exists()
    assert target.read_bytes() == _ATOMIC_NEW
    assert sibling.read_bytes() == _ATOMIC_SIBLING
    assert foreign.read_bytes() == _ATOMIC_FOREIGN


def test_atomic_write_best_effort_directory_fsync_oserror_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, "tmp")
    captured = _capture_exclusive_write_fd(monkeypatch)
    real_fsync = os.fsync

    def syncing(fd: int) -> None:
        if captured["fd"] is not None and fd != captured["fd"]:
            raise OSError(errno.EIO, "injected best-effort directory fsync")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", syncing)

    landed = atomic_write_bytes_no_follow(
        target, _ATOMIC_NEW, containment_root=root, temp_suffix="tmp"
    )

    assert landed == target
    assert captured["fd"] is not None
    assert captured["name"] is not None
    _assert_write_fd_closed(captured["fd"])
    owned = _owned_temp_name(root, target.name, "tmp", captured["name"])
    assert not owned.exists()
    assert target.read_bytes() == _ATOMIC_NEW
    assert sibling.read_bytes() == _ATOMIC_SIBLING
    assert foreign.read_bytes() == _ATOMIC_FOREIGN


@pytest.mark.parametrize("fault", ["type", "interrupt"])
def test_write_bytes_atomic_does_not_wrap_non_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root = tmp_path.resolve()
    store = LocalObjectStore(root=root)
    key = "raw/gfs/2026050700/payload.bin"
    target = root / key
    captured = _capture_exclusive_write_fd(monkeypatch)
    injected = KeyboardInterrupt("store write interrupt")
    real_write = os.write

    def writing(fd: int, data: bytes | bytearray | memoryview) -> int:
        if fd == captured["fd"]:
            raise injected
        return real_write(fd, data)

    if fault == "interrupt":
        monkeypatch.setattr(os, "write", writing)
        with pytest.raises(KeyboardInterrupt) as info:
            store.write_bytes_atomic(key, _ATOMIC_NEW)
        assert info.value is injected
        assert not isinstance(info.value, ObjectStoreError)
    else:
        with pytest.raises(TypeError) as info:
            store.write_bytes_atomic(key, "not-bytes")  # type: ignore[arg-type]
        assert not isinstance(info.value, ObjectStoreError)

    assert captured["fd"] is not None
    assert captured["name"] is not None
    _assert_write_fd_closed(captured["fd"])
    owned = _owned_temp_name(target.parent, target.name, "part", captured["name"])
    assert not owned.exists()
    assert captured["name"].endswith(".part")


def test_atomic_write_parent_close_oserror_preserves_primary_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, "tmp")
    captured = _capture_exclusive_write_fd(monkeypatch)
    injected = KeyboardInterrupt("primary interrupt during write")
    close_error = OSError(errno.EBADF, "injected parent close failure")
    real_write = os.write
    real_close = os.close

    def writing(fd: int, data: bytes | bytearray | memoryview) -> int:
        if fd == captured["fd"]:
            raise injected
        return real_write(fd, data)

    def closing(fd: int) -> None:
        if fd == captured["parent_fd"]:
            real_close(fd)
            raise close_error
        real_close(fd)

    monkeypatch.setattr(os, "write", writing)
    monkeypatch.setattr(os, "close", closing)

    with pytest.raises(KeyboardInterrupt) as info:
        atomic_write_bytes_no_follow(
            target, _ATOMIC_NEW, containment_root=root, temp_suffix="tmp"
        )

    assert info.value is injected
    assert info.value is not close_error
    assert not isinstance(info.value, SafeFilesystemError)
    assert captured["fd"] is not None
    assert captured["name"] is not None
    assert captured["parent_fd"] is not None
    _assert_write_fd_closed(captured["fd"])
    _assert_write_fd_closed(captured["parent_fd"])
    owned = _owned_temp_name(root, target.name, "tmp", captured["name"])
    assert not owned.exists()
    _assert_neighbors_untouched(target, sibling, foreign)


def test_atomic_write_parent_close_oserror_does_not_replace_pre_replace_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, "tmp")
    captured = _capture_exclusive_write_fd(monkeypatch)
    write_error = OSError(errno.ENOSPC, "injected write ENOSPC")
    close_error = OSError(errno.EBADF, "injected parent close failure")
    real_write = os.write
    real_close = os.close

    def writing(fd: int, data: bytes | bytearray | memoryview) -> int:
        if fd == captured["fd"]:
            raise write_error
        return real_write(fd, data)

    def closing(fd: int) -> None:
        if fd == captured["parent_fd"]:
            real_close(fd)
            raise close_error
        real_close(fd)

    monkeypatch.setattr(os, "write", writing)
    monkeypatch.setattr(os, "close", closing)

    with pytest.raises(SafeFilesystemError) as info:
        atomic_write_bytes_no_follow(
            target, _ATOMIC_NEW, containment_root=root, temp_suffix="tmp"
        )

    assert info.value.kind == "io"
    assert info.value.__cause__ is write_error
    assert info.value.__cause__ is not close_error
    assert captured["fd"] is not None
    assert captured["name"] is not None
    assert captured["parent_fd"] is not None
    _assert_write_fd_closed(captured["fd"])
    _assert_write_fd_closed(captured["parent_fd"])
    owned = _owned_temp_name(root, target.name, "tmp", captured["name"])
    assert not owned.exists()
    _assert_neighbors_untouched(target, sibling, foreign)


def test_atomic_write_standalone_parent_close_oserror_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, "tmp")
    captured = _capture_exclusive_write_fd(monkeypatch)
    close_error = OSError(errno.EBADF, "injected standalone parent close failure")
    real_close = os.close

    def closing(fd: int) -> None:
        if fd == captured["parent_fd"]:
            real_close(fd)
            raise close_error
        real_close(fd)

    monkeypatch.setattr(os, "close", closing)

    with pytest.raises(SafeFilesystemError) as info:
        atomic_write_bytes_no_follow(
            target, _ATOMIC_NEW, containment_root=root, temp_suffix="tmp"
        )

    assert info.value.kind == "indeterminate"
    assert info.value.__cause__ is close_error
    assert captured["fd"] is not None
    assert captured["name"] is not None
    assert captured["parent_fd"] is not None
    _assert_write_fd_closed(captured["fd"])
    _assert_write_fd_closed(captured["parent_fd"])
    owned = _owned_temp_name(root, target.name, "tmp", captured["name"])
    assert not owned.exists()
    assert target.read_bytes() == _ATOMIC_NEW
    assert sibling.read_bytes() == _ATOMIC_SIBLING
    assert foreign.read_bytes() == _ATOMIC_FOREIGN


def test_atomic_write_standalone_parent_close_oserror_after_replace_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, "tmp")
    captured = _capture_exclusive_write_fd(monkeypatch)
    close_error = OSError(errno.EBADF, "injected post-replace parent close failure")
    real_close = os.close

    def closing(fd: int) -> None:
        if fd == captured["parent_fd"]:
            real_close(fd)
            raise close_error
        real_close(fd)

    monkeypatch.setattr(os, "close", closing)

    with pytest.raises(SafeFilesystemError) as info:
        atomic_write_bytes_no_follow(
            target,
            _ATOMIC_NEW,
            containment_root=root,
            temp_suffix="tmp",
            require_durable_replace=True,
        )

    assert info.value.kind == "indeterminate"
    assert info.value.__cause__ is close_error
    assert captured["fd"] is not None
    assert captured["name"] is not None
    assert captured["parent_fd"] is not None
    _assert_write_fd_closed(captured["fd"])
    _assert_write_fd_closed(captured["parent_fd"])
    owned = _owned_temp_name(root, target.name, "tmp", captured["name"])
    assert not owned.exists()
    assert target.read_bytes() == _ATOMIC_NEW
    assert sibling.read_bytes() == _ATOMIC_SIBLING
    assert foreign.read_bytes() == _ATOMIC_FOREIGN


def test_atomic_write_parent_close_eio_visible_from_handled_caller_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "payload.bin"
    sibling, foreign = _plant_atomic_neighbors(root, target, "tmp")
    captured = _capture_exclusive_write_fd(monkeypatch)
    close_error = OSError(errno.EIO, "injected parent close error")
    real_close = os.close

    def closing(fd: int) -> None:
        if fd == captured["parent_fd"]:
            real_close(fd)
            raise close_error
        real_close(fd)

    monkeypatch.setattr(os, "close", closing)

    try:
        raise ValueError("already handled caller error")
    except ValueError:
        with pytest.raises(SafeFilesystemError) as info:
            atomic_write_bytes_no_follow(
                target, _ATOMIC_NEW, containment_root=root, temp_suffix="tmp"
            )

    assert info.value.kind == "indeterminate"
    assert info.value.__cause__ is close_error
    assert captured["fd"] is not None
    assert captured["name"] is not None
    assert captured["parent_fd"] is not None
    _assert_write_fd_closed(captured["fd"])
    _assert_write_fd_closed(captured["parent_fd"])
    owned = _owned_temp_name(root, target.name, "tmp", captured["name"])
    assert not owned.exists()
    assert target.read_bytes() == _ATOMIC_NEW
    assert sibling.read_bytes() == _ATOMIC_SIBLING
    assert foreign.read_bytes() == _ATOMIC_FOREIGN
