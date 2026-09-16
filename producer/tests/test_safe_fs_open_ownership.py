"""Pre-return file-descriptor ownership for `open_file_no_follow` (#225, #232).

`open_file_no_follow` used to close the acquired file fd only in
`except Exception`, then close the parent in `finally`. KeyboardInterrupt
and SystemExit skipped file cleanup; a close OSError replaced the original
failure; a parent-close failure after successful validation discarded the
unreturned file fd. A later file-then-parent loop still let a non-OSError
close interrupt replace the primary and skip the remaining descriptor.
Discriminators are the captured file/parent fds, one close attempt each
in file-then-parent order, primary object identity, secondary close notes
with role/type/message in the same note, and `fstat` -> EBADF after a
successful close. Injected close errors never assert liveness of that fd.
Leftover closes are disarmed and separate from the assertions.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

from yd_producer.store.safe_fs import SafeFilesystemError, open_file_no_follow

_PAYLOAD = b"open-ownership-payload"


def _plant(root: Path) -> Path:
    target = root / "payload.bin"
    target.write_bytes(_PAYLOAD)
    return target


def _stat_with(
    base: os.stat_result, *, mode: int | None = None, ino: int | None = None
) -> os.stat_result:
    return os.stat_result(
        (
            base.st_mode if mode is None else mode,
            base.st_ino if ino is None else ino,
            base.st_dev,
            base.st_nlink,
            base.st_uid,
            base.st_gid,
            base.st_size,
            int(base.st_atime),
            int(base.st_mtime),
            int(base.st_ctime),
        )
    )


def _notes_of(error: BaseException) -> list[str]:
    return list(getattr(error, "__notes__", []))


def _assert_close_note(error: BaseException, injected: OSError, *, role: str) -> None:
    joined = "\n".join(_notes_of(error))
    assert role in joined.lower()
    assert type(injected).__name__ in joined
    assert injected.strerror in joined


def _assert_interrupt_note(
    error: BaseException, injected: BaseException, *, role: str
) -> None:
    matches = [
        note
        for note in _notes_of(error)
        if note.startswith(f"{role} descriptor close also failed:")
        and type(injected).__name__ in note
        and str(injected) in note
    ]
    assert matches


def _cleanup_leftovers(captured: dict[str, object]) -> None:
    captured["armed"] = False
    real_close = captured["real_close"]
    assert callable(real_close)
    for key, flag in (
        ("fd", "file_really_closed"),
        ("parent_fd", "parent_really_closed"),
    ):
        fd = captured[key]
        if fd is None or captured[flag]:
            continue
        try:
            real_close(fd)
        except OSError:
            pass


def _install_open_faults(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fstat_error: BaseException | None = None,
    file_mode: int | None = None,
    file_ino: int | None = None,
    parent_identity_mismatch: bool = False,
    file_close_error: BaseException | None = None,
    parent_close_error: BaseException | None = None,
) -> dict[str, object]:
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    close_order: list[int] = []
    captured: dict[str, object] = {
        "fd": None,
        "parent_fd": None,
        "file_close_count": 0,
        "parent_close_count": 0,
        "file_really_closed": False,
        "parent_really_closed": False,
        "armed": True,
        "fstat_injected": False,
        "real_close": real_close,
        "real_fstat": real_fstat,
        "close_order": close_order,
    }

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if dir_fd is not None and not (flags & os.O_DIRECTORY):
            captured["fd"] = fd
            captured["parent_fd"] = dir_fd
        return fd

    def closing(fd: int) -> None:
        if not captured["armed"]:
            real_close(fd)
            return
        if captured["fd"] is not None and fd == captured["fd"]:
            captured["file_close_count"] = int(captured["file_close_count"]) + 1
            close_order.append(fd)
            real_close(fd)
            captured["file_really_closed"] = True
            if file_close_error is not None:
                raise file_close_error
            return
        if captured["parent_fd"] is not None and fd == captured["parent_fd"]:
            captured["parent_close_count"] = int(captured["parent_close_count"]) + 1
            close_order.append(fd)
            real_close(fd)
            captured["parent_really_closed"] = True
            if parent_close_error is not None:
                raise parent_close_error
            return
        real_close(fd)

    def fstating(fd: int) -> os.stat_result:
        if captured["fd"] is not None and fd == captured["fd"]:
            if fstat_error is not None and not captured["fstat_injected"]:
                captured["fstat_injected"] = True
                raise fstat_error
            result = real_fstat(fd)
            if file_mode is not None or file_ino is not None:
                return _stat_with(result, mode=file_mode, ino=file_ino)
            return result
        result = real_fstat(fd)
        if (
            parent_identity_mismatch
            and captured["fd"] is not None
            and captured["parent_fd"] is not None
            and fd == captured["parent_fd"]
        ):
            return _stat_with(result, ino=result.st_ino + 1)
        return result

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "close", closing)
    monkeypatch.setattr(os, "fstat", fstating)
    return captured


def _open(path: Path, root: Path) -> int:
    return open_file_no_follow(path, containment_root=root)


def _assert_one_cleanup(
    captured: dict[str, object],
    *,
    file_close_failed: bool = False,
    parent_close_failed: bool = False,
) -> None:
    assert captured["fd"] is not None
    assert captured["parent_fd"] is not None
    assert captured["file_close_count"] == 1
    assert captured["parent_close_count"] == 1
    real_fstat = captured["real_fstat"]
    assert callable(real_fstat)
    if not file_close_failed:
        with pytest.raises(OSError) as info:
            real_fstat(captured["fd"])
        assert info.value.errno == errno.EBADF
    if not parent_close_failed:
        with pytest.raises(OSError) as info:
            real_fstat(captured["parent_fd"])
        assert info.value.errno == errno.EBADF


@pytest.mark.parametrize(
    ("exc_type", "message"),
    [
        (KeyboardInterrupt, "primary interrupt"),
        (SystemExit, "primary exit"),
    ],
    ids=["keyboardinterrupt", "systemexit"],
)
def test_open_file_interrupt_at_fstat_keeps_identity_and_closes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc_type: type[BaseException],
    message: str,
) -> None:
    root = tmp_path.resolve()
    target = _plant(root)
    injected = exc_type(message)
    captured = _install_open_faults(monkeypatch, fstat_error=injected)
    try:
        with pytest.raises(exc_type) as info:
            _open(target, root)
        assert info.value is injected
        assert not isinstance(info.value, SafeFilesystemError)
        _assert_one_cleanup(captured)
    finally:
        _cleanup_leftovers(captured)


@pytest.mark.parametrize(
    ("exc_type", "message"),
    [
        (KeyboardInterrupt, "primary interrupt"),
        (SystemExit, "primary exit"),
    ],
    ids=["keyboardinterrupt", "systemexit"],
)
def test_open_file_interrupt_keeps_primary_when_file_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc_type: type[BaseException],
    message: str,
) -> None:
    root = tmp_path.resolve()
    target = _plant(root)
    injected = exc_type(message)
    close_error = OSError(errno.ESTALE, "stale file close")
    captured = _install_open_faults(
        monkeypatch, fstat_error=injected, file_close_error=close_error
    )
    try:
        with pytest.raises(exc_type) as info:
            _open(target, root)
        assert info.value is injected
        assert info.value is not close_error
        _assert_close_note(info.value, close_error, role="file")
        _assert_one_cleanup(captured, file_close_failed=True)
    finally:
        _cleanup_leftovers(captured)


def test_open_file_fstat_oserror_keeps_identity_and_closes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = _plant(root)
    injected = OSError(errno.EIO, "injected fstat EIO")
    captured = _install_open_faults(monkeypatch, fstat_error=injected)
    try:
        with pytest.raises(OSError) as info:
            _open(target, root)
        assert info.value is injected
        assert not isinstance(info.value, SafeFilesystemError)
        _assert_one_cleanup(captured)
    finally:
        _cleanup_leftovers(captured)


def test_open_file_post_open_nonregular_refusal_closes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = _plant(root)
    captured = _install_open_faults(monkeypatch, file_mode=stat.S_IFDIR | 0o755)
    try:
        with pytest.raises(SafeFilesystemError) as info:
            _open(target, root)
        assert info.value.kind == "unsafe"
        assert "must be a regular file" in str(info.value)
        _assert_one_cleanup(captured)
    finally:
        _cleanup_leftovers(captured)


def test_open_file_identity_changed_refusal_closes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = _plant(root)
    captured = _install_open_faults(monkeypatch, file_ino=2**31 - 1)
    try:
        with pytest.raises(SafeFilesystemError) as info:
            _open(target, root)
        assert info.value.kind == "identity_changed"
        assert "changed while being opened" in str(info.value)
        _assert_one_cleanup(captured)
    finally:
        _cleanup_leftovers(captured)


def test_open_file_parent_identity_refusal_closes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = _plant(root)
    captured = _install_open_faults(monkeypatch, parent_identity_mismatch=True)
    try:
        with pytest.raises(SafeFilesystemError) as info:
            _open(target, root)
        assert info.value.kind == "unsafe"
        assert "Directory changed while bound" in str(info.value)
        _assert_one_cleanup(captured)
    finally:
        _cleanup_leftovers(captured)


@pytest.mark.parametrize(
    ("file_close", "parent_close"),
    [
        (True, False),
        (False, True),
        (True, True),
    ],
    ids=["file-close", "parent-close", "both-closes"],
)
def test_open_file_validation_and_cleanup_failures_keep_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_close: bool,
    parent_close: bool,
) -> None:
    root = tmp_path.resolve()
    target = _plant(root)
    injected = OSError(errno.EIO, "injected fstat EIO")
    file_error = OSError(errno.ESTALE, "stale file close") if file_close else None
    parent_error = OSError(errno.EBADF, "stale parent close") if parent_close else None
    captured = _install_open_faults(
        monkeypatch,
        fstat_error=injected,
        file_close_error=file_error,
        parent_close_error=parent_error,
    )
    try:
        with pytest.raises(OSError) as info:
            _open(target, root)
        assert info.value is injected
        assert info.value is not file_error
        assert info.value is not parent_error
        if file_error is not None:
            _assert_close_note(info.value, file_error, role="file")
        if parent_error is not None:
            _assert_close_note(info.value, parent_error, role="parent")
        _assert_one_cleanup(
            captured,
            file_close_failed=file_close,
            parent_close_failed=parent_close,
        )
    finally:
        _cleanup_leftovers(captured)


@pytest.mark.parametrize(
    ("file_close", "parent_close"),
    [
        (True, False),
        (False, True),
        (True, True),
    ],
    ids=["file-close", "parent-close", "both-closes"],
)
def test_open_file_keeps_primary_when_cleanup_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_close: bool,
    parent_close: bool,
) -> None:
    root = tmp_path.resolve()
    target = _plant(root)
    injected = KeyboardInterrupt("primary interrupt")
    file_error = (
        KeyboardInterrupt("secondary close interrupt A") if file_close else None
    )
    parent_error = (
        KeyboardInterrupt("secondary close interrupt B") if parent_close else None
    )
    captured = _install_open_faults(
        monkeypatch,
        fstat_error=injected,
        file_close_error=file_error,
        parent_close_error=parent_error,
    )
    try:
        with pytest.raises(BaseException) as info:
            _open(target, root)
        assert info.value is injected
        assert info.value is not file_error
        assert info.value is not parent_error
        if file_error is not None:
            _assert_interrupt_note(info.value, file_error, role="file")
        if parent_error is not None:
            _assert_interrupt_note(info.value, parent_error, role="parent")
        assert captured["close_order"] == [captured["fd"], captured["parent_fd"]]
        _assert_one_cleanup(
            captured,
            file_close_failed=file_close,
            parent_close_failed=parent_close,
        )
    finally:
        _cleanup_leftovers(captured)


def test_open_file_parent_close_after_success_closes_unreturned_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = _plant(root)
    parent_error = OSError(errno.EBADF, "injected parent close failure")
    captured = _install_open_faults(monkeypatch, parent_close_error=parent_error)
    try:
        with pytest.raises(OSError) as info:
            _open(target, root)
        assert info.value is parent_error
        assert not isinstance(info.value, SafeFilesystemError)
        _assert_one_cleanup(captured, parent_close_failed=True)
    finally:
        _cleanup_leftovers(captured)


def test_open_file_parent_close_after_success_keeps_parent_primary_when_file_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = _plant(root)
    parent_error = OSError(errno.EBADF, "injected parent close failure")
    file_error = OSError(errno.ESTALE, "stale file close")
    captured = _install_open_faults(
        monkeypatch,
        parent_close_error=parent_error,
        file_close_error=file_error,
    )
    try:
        with pytest.raises(OSError) as info:
            _open(target, root)
        assert info.value is parent_error
        assert info.value is not file_error
        _assert_close_note(info.value, file_error, role="file")
        _assert_one_cleanup(captured, file_close_failed=True, parent_close_failed=True)
    finally:
        _cleanup_leftovers(captured)


def test_open_file_success_transfers_readable_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = _plant(root)
    captured = _install_open_faults(monkeypatch)
    file_fd: int | None = None
    try:
        file_fd = _open(target, root)
        assert file_fd == captured["fd"]
        assert captured["file_close_count"] == 0
        assert captured["parent_close_count"] == 1
        assert captured["parent_fd"] is not None
        with pytest.raises(OSError) as info:
            captured["real_fstat"](captured["parent_fd"])
        assert info.value.errno == errno.EBADF
        assert os.read(file_fd, len(_PAYLOAD)) == _PAYLOAD
        os.lseek(file_fd, 0, os.SEEK_SET)
        assert os.read(file_fd, len(_PAYLOAD)) == _PAYLOAD
        assert target.read_bytes() == _PAYLOAD
    finally:
        captured["armed"] = False
        if file_fd is not None:
            os.close(file_fd)
        _cleanup_leftovers(captured)
