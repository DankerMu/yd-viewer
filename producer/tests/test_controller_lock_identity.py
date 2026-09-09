"""`run_with_lock` 持锁 fd 与命名路径 identity 矩阵（issue #85）。

公开 seam 是 `runlock.run_with_lock`。本文件用真实临时文件、独立 OFD 与子进程
证明入口/退出核对、至多一次完整重取、retry 不 `O_CREAT`、以及原异常 + 一条
漂移 note。tmp 目录只证明本地文件系统上的 per-OFD 行为，不是 node-22/M4 receipt。

第三次 `os.open` 硬失败，防止无界重取把测试挂死。fd 生命周期只跟踪本调用打开的
fd；旧 fd 必须在第二次 open 前关闭（EBADF），子进程 pipe fd 不计为 runlock 资源。
"""

from __future__ import annotations

import errno
import fcntl
import os
import pathlib
import stat as stat_module
import subprocess
import sys
import textwrap
from collections.abc import Callable
from typing import Any

import pytest
from test_controller_lock import RecordingAction, _deadline

from yd_producer import runlock

_MAX_OPENS = 2
_EXISTING_NOTE = "ACTION-EXISTING-NOTE"
_CAUSE_TEXT = "ACTION-CAUSE"
_EX_NB = fcntl.LOCK_EX | fcntl.LOCK_NB


@pytest.fixture
def lock_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """与 `test_controller_lock.lock_path` 同形：父目录已存在的绝对哨兵路径。"""
    run_dir = tmp_path.resolve() / "run"
    run_dir.mkdir()
    return run_dir / "yd-producer.lock"


def _pair(path: pathlib.Path) -> tuple[int, int]:
    info = os.lstat(path)
    return info.st_dev, info.st_ino


def _write_regular(path: pathlib.Path, payload: bytes) -> tuple[int, int]:
    path.write_bytes(payload)
    return _pair(path)


def _replace_regular(path: pathlib.Path, payload: bytes) -> tuple[int, int]:
    if path.exists() or path.is_symlink():
        path.unlink()
    return _write_regular(path, payload)


def _assert_closed(fd: int, *, fstat: Callable[[int], os.stat_result]) -> None:
    with pytest.raises(OSError) as caught:
        fstat(fd)
    assert caught.value.errno == errno.EBADF


def _assert_lock_note(
    text: str,
    *,
    lock_path: pathlib.Path,
    expected: tuple[int, int],
    actual_pair: tuple[int, int] | None = None,
    actual_reason: str | None = None,
) -> None:
    """Contract facts only: named path, expected pair, actual pair or reason."""
    assert "cron.lock_path" in text
    assert str(lock_path) in text
    assert str(expected) in text
    if actual_pair is not None:
        assert str(actual_pair) in text
    if actual_reason is not None:
        assert actual_reason in text


def _shift_dev(info: os.stat_result) -> os.stat_result:
    values = list(info)
    values[stat_module.ST_DEV] = info.st_dev + 1
    return os.stat_result(values)


class LockEvents:
    """记录本调用 open/flock/unlock/close；只跟踪 runlock 打开的 fd。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.opens: list[int] = []
        self.flocks: list[int] = []
        self.unlocks = 0
        self.owned: set[int] = set()
        self.held_fds: list[int] = []
        self.closed_owned: list[int] = []
        self.old_closed_before_second_open: bool | None = None
        self.before_open: Callable[[object, int, int], None] | None = None
        self.before_flock: Callable[[int, int], None] | None = None
        self._open = runlock.os.open
        self._flock = runlock.fcntl.flock
        self._close = runlock.os.close
        self._fstat = runlock.os.fstat
        monkeypatch.setattr(runlock.os, "open", self.open)
        monkeypatch.setattr(runlock.fcntl, "flock", self.flock)
        monkeypatch.setattr(runlock.os, "close", self.close)

    def open(
        self, path: str | bytes | os.PathLike[str], flags: int, mode: int = 0o777
    ) -> int:
        if len(self.opens) >= _MAX_OPENS:
            raise AssertionError("第三次 open：identity 重取必须有上限")
        if len(self.opens) == 1:
            old = self.held_fds[0]
            self.old_closed_before_second_open = old in self.closed_owned
            if self.old_closed_before_second_open:
                _assert_closed(old, fstat=self._fstat)
        self.opens.append(flags)
        if self.before_open is not None:
            self.before_open(path, flags, mode)
        fd = self._open(path, flags, mode)
        self.owned.add(fd)
        self.held_fds.append(fd)
        return fd

    def flock(self, fd: int, operation: int) -> None:
        self.flocks.append(operation)
        if operation == fcntl.LOCK_UN:
            self.unlocks += 1
        if self.before_flock is not None:
            self.before_flock(fd, operation)
        self._flock(fd, operation)

    def close(self, fd: int) -> None:
        if fd in self.owned:
            self.closed_owned.append(fd)
            self.owned.discard(fd)
        self._close(fd)

    def assert_released(self) -> None:
        assert self.owned == set()
        assert self.closed_owned == self.held_fds
        if len(self.held_fds) == 2:
            assert self.old_closed_before_second_open is True
        for fd in self.held_fds:
            _assert_closed(fd, fstat=self._fstat)


def test_first_replacement_retries_once_then_runs_action(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第一次 flock 后替换 inode，完整释放旧 fd 后重取稳定，action 恰一次。"""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    action = RecordingAction(result="ok")
    replacement: list[tuple[int, int]] = []

    def fstat(fd: int) -> os.stat_result:
        info = original_fstat(fd)
        if len(events.opens) == 1 and not replacement:
            replacement.append(_replace_regular(lock_path, b"replacement-one\n"))
        return info

    monkeypatch.setattr(runlock.os, "fstat", fstat)

    with _deadline():
        result = runlock.run_with_lock(lock_path=lock_path, action=action)

    assert result.acquired is True
    assert result.value == "ok"
    assert result.lock_path == lock_path
    assert action.calls == 1
    assert len(events.opens) == 2
    assert events.opens[0] & os.O_CREAT
    assert not events.opens[1] & os.O_CREAT
    assert events.unlocks == 2
    events.assert_released()
    assert replacement[0] == _pair(lock_path)
    assert lock_path.read_bytes() == b"replacement-one\n"


def test_persistent_replacement_fails_after_two_attempts(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两次取得后 pathname 都换 inode：RunLockError，action 0，replacement 保留。"""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    action = RecordingAction()
    last_pair: list[tuple[int, int]] = []

    def fstat(fd: int) -> os.stat_result:
        info = original_fstat(fd)
        last_pair.append(
            _replace_regular(lock_path, f"swap-{len(events.opens)}\n".encode())
        )
        return info

    monkeypatch.setattr(runlock.os, "fstat", fstat)

    with (
        _deadline(),
        pytest.raises(runlock.RunLockError, match="cron.lock_path") as caught,
    ):
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert action.calls == 0
    assert len(events.opens) == 2
    assert not events.opens[1] & os.O_CREAT
    assert events.unlocks == 2
    events.assert_released()
    assert last_pair[-1] == _pair(lock_path)
    assert lock_path.read_bytes() == b"swap-2\n"
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("shape", ["missing", "symlink", "directory", "fifo"])
def test_second_path_is_not_ordinary_file(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """第二次 path 为缺失/symlink/目录/FIFO：RunLockError，不修补，action 0。"""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    action = RecordingAction()
    sibling = lock_path.parent / "other-regular"
    sibling.write_bytes(b"other\n")

    def occupy() -> None:
        if lock_path.exists() or lock_path.is_symlink():
            lock_path.unlink()
        if shape == "missing":
            return
        if shape == "symlink":
            lock_path.symlink_to(sibling)
            return
        if shape == "directory":
            lock_path.mkdir()
            return
        os.mkfifo(lock_path)

    def fstat(fd: int) -> os.stat_result:
        info = original_fstat(fd)
        if len(events.opens) == 1:
            occupy()
        return info

    monkeypatch.setattr(runlock.os, "fstat", fstat)

    with _deadline(), pytest.raises(runlock.RunLockError, match="cron.lock_path"):
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert action.calls == 0
    assert len(events.opens) == 2
    assert not events.opens[1] & os.O_CREAT
    events.assert_released()
    if shape == "missing":
        assert not os.path.lexists(lock_path)
    elif shape == "symlink":
        assert lock_path.is_symlink()
        assert sibling.read_bytes() == b"other\n"
    elif shape == "directory":
        assert lock_path.is_dir()
    else:
        assert stat_module.S_ISFIFO(os.lstat(lock_path).st_mode)


def test_second_path_stat_io_is_true_error(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第二次 no-follow path stat IO 是真错，不是竞争跳过。"""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    original_stat = runlock.os.stat
    action = RecordingAction()
    io_error = OSError(errno.EIO, os.strerror(errno.EIO))

    def fstat(fd: int) -> os.stat_result:
        info = original_fstat(fd)
        if len(events.opens) == 1:
            _replace_regular(lock_path, b"first-swap\n")
        return info

    def path_stat(
        path: str | bytes | os.PathLike[str], *args: Any, **kwargs: Any
    ) -> os.stat_result:
        if kwargs.get("follow_symlinks") is False and len(events.opens) == 2:
            raise io_error
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(runlock.os, "fstat", fstat)
    monkeypatch.setattr(runlock.os, "stat", path_stat)

    with (
        _deadline(),
        pytest.raises(runlock.RunLockError, match="cron.lock_path") as caught,
    ):
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert caught.value.__cause__ is io_error
    assert action.calls == 0
    events.assert_released()


def test_retry_contention_skips_without_action(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重取遇真实 flock 竞争：acquired=False，action 0，不报 identity 错。"""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    action = RecordingAction()
    script = textwrap.dedent(
        f"""
        import fcntl, os, sys
        fd = os.open({str(lock_path)!r}, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        sys.stdout.write("ready\\n")
        sys.stdout.flush()
        sys.stdin.readline()
        """
    )
    holder: subprocess.Popen[str] | None = None

    def fstat(fd: int) -> os.stat_result:
        info = original_fstat(fd)
        if len(events.opens) == 1:
            _replace_regular(lock_path, b"contended\n")
        return info

    def before_flock(fd: int, operation: int) -> None:
        nonlocal holder
        if operation == _EX_NB and len(events.opens) == 2:
            holder = subprocess.Popen(
                [sys.executable, "-c", script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "ready"

    events.before_flock = before_flock
    monkeypatch.setattr(runlock.os, "fstat", fstat)

    try:
        with _deadline():
            result = runlock.run_with_lock(lock_path=lock_path, action=action)
    finally:
        if holder is not None:
            assert holder.stdin is not None
            holder.stdin.close()
            holder.wait(timeout=10)

    assert result.acquired is False
    assert result.value is None
    assert action.calls == 0
    assert len(events.opens) == 2
    events.assert_released()


def test_entry_fstat_io_retries_then_runs(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次成功 flock 后 fstat IO，再一次完整重取稳定后 action 恰一次。"""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    action = RecordingAction(result=None)
    io_error = OSError(errno.EIO, "injected fstat")

    def fstat(fd: int) -> os.stat_result:
        if len(events.opens) == 1:
            raise io_error
        return original_fstat(fd)

    monkeypatch.setattr(runlock.os, "fstat", fstat)

    with _deadline():
        result = runlock.run_with_lock(lock_path=lock_path, action=action)

    assert result.acquired is True
    assert result.value is None
    assert action.calls == 1
    assert len(events.opens) == 2
    assert not events.opens[1] & os.O_CREAT
    events.assert_released()


def test_entry_path_stat_io_retries_then_runs(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次 path-stat IO 后完整重取，稳定第二次才执行 action。"""
    events = LockEvents(monkeypatch)
    original_stat = runlock.os.stat
    action = RecordingAction(result="after-path-io")
    io_error = OSError(errno.EIO, "injected path stat")
    path_probes = {"n": 0}

    def path_stat(
        path: str | bytes | os.PathLike[str], *args: Any, **kwargs: Any
    ) -> os.stat_result:
        if kwargs.get("follow_symlinks") is False:
            path_probes["n"] += 1
            if path_probes["n"] == 1:
                raise io_error
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(runlock.os, "stat", path_stat)

    with _deadline():
        result = runlock.run_with_lock(lock_path=lock_path, action=action)

    assert result.acquired is True
    assert result.value == "after-path-io"
    assert action.calls == 1
    assert len(events.opens) == 2
    assert not events.opens[1] & os.O_CREAT
    events.assert_released()


def test_entry_temporary_missing_path_retries_then_runs(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次 path 缺失探测后文件仍在，完整重取稳定后才执行 action。"""
    events = LockEvents(monkeypatch)
    original_stat = runlock.os.stat
    action = RecordingAction(result="after-missing")
    path_probes = {"n": 0}

    def path_stat(
        path: str | bytes | os.PathLike[str], *args: Any, **kwargs: Any
    ) -> os.stat_result:
        if kwargs.get("follow_symlinks") is False:
            path_probes["n"] += 1
            if path_probes["n"] == 1:
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(runlock.os, "stat", path_stat)

    with _deadline():
        result = runlock.run_with_lock(lock_path=lock_path, action=action)

    assert result.acquired is True
    assert result.value == "after-missing"
    assert action.calls == 1
    assert len(events.opens) == 2
    assert not events.opens[1] & os.O_CREAT
    assert lock_path.is_file()
    events.assert_released()


def test_directory_may_fail_at_open(lock_path: pathlib.Path) -> None:
    """目录可在初次 open 失败；保持 OSError，action 0，不修补。"""
    lock_path.mkdir()
    action = RecordingAction()

    with _deadline(), pytest.raises(OSError) as caught:
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert action.calls == 0
    assert caught.value.errno in {errno.EISDIR, errno.ENOTDIR, errno.EPERM}
    assert not isinstance(caught.value, runlock.RunLockError)
    assert lock_path.is_dir()


def test_retry_directory_open_is_run_lock_error(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重取遇到目录：open 真错收敛为 RunLockError，保留 cause。"""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    action = RecordingAction()

    def fstat(fd: int) -> os.stat_result:
        info = original_fstat(fd)
        if len(events.opens) == 1:
            lock_path.unlink()
            lock_path.mkdir()
        return info

    monkeypatch.setattr(runlock.os, "fstat", fstat)

    with (
        _deadline(),
        pytest.raises(runlock.RunLockError, match="cron.lock_path") as caught,
    ):
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert action.calls == 0
    assert isinstance(caught.value.__cause__, OSError)
    assert lock_path.is_dir()
    events.assert_released()


def test_first_fifo_flock_injected_oserror_stays_raw(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create an actual FIFO and inject flock OSError on that fd.

    Native FIFO flock errno is not portable (Darwin ENOTSUP vs Linux may
    succeed then fail at fstat). The injected original OSError is the public
    OS-boundary oracle: first attempt stays the original, not RunLockError.
    """
    os.mkfifo(lock_path)
    action = RecordingAction()
    original_flock = runlock.fcntl.flock
    injected = OSError(errno.EOPNOTSUPP, os.strerror(errno.EOPNOTSUPP))
    fifo_fds: set[int] = set()
    original_open = runlock.os.open

    def open_and_note(
        path: str | bytes | os.PathLike[str], flags: int, mode: int = 0o777
    ) -> int:
        fd = original_open(path, flags, mode)
        if pathlib.Path(os.fspath(path)) == lock_path:
            fifo_fds.add(fd)
        return fd

    def flock(fd: int, operation: int) -> None:
        if fd in fifo_fds and operation == _EX_NB:
            raise injected
        original_flock(fd, operation)

    monkeypatch.setattr(runlock.os, "open", open_and_note)
    monkeypatch.setattr(runlock.fcntl, "flock", flock)

    with _deadline(), pytest.raises(OSError) as caught:
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert caught.value is injected
    assert action.calls == 0
    assert not isinstance(caught.value, runlock.RunLockError)
    assert stat_module.S_ISFIFO(os.lstat(lock_path).st_mode)


def test_retry_fifo_flock_injected_oserror_is_run_lock_error(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry flock on an actual FIFO: injected original becomes RunLockError cause."""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    action = RecordingAction()
    injected = OSError(errno.ENOTSUP, os.strerror(errno.ENOTSUP))

    def fstat(fd: int) -> os.stat_result:
        info = original_fstat(fd)
        if len(events.opens) == 1:
            lock_path.unlink()
            os.mkfifo(lock_path)
        return info

    def before_flock(fd: int, operation: int) -> None:
        if operation == _EX_NB and len(events.opens) == 2:
            raise injected

    events.before_flock = before_flock
    monkeypatch.setattr(runlock.os, "fstat", fstat)

    with (
        _deadline(),
        pytest.raises(runlock.RunLockError, match="cron.lock_path") as caught,
    ):
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert action.calls == 0
    assert caught.value.__cause__ is injected
    assert "cron.lock_path" in str(caught.value)
    assert str(lock_path) in str(caught.value)
    events.assert_released()
    assert stat_module.S_ISFIFO(os.lstat(lock_path).st_mode)


def test_first_open_eacces_stays_oserror(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """初次 open EACCES 保持原 OSError，不得包成 RunLockError 或跳过。"""
    action = RecordingAction()
    eacces = PermissionError(errno.EACCES, os.strerror(errno.EACCES))

    def refuse(
        path: str | bytes | os.PathLike[str], flags: int, mode: int = 0o777
    ) -> int:
        raise eacces

    monkeypatch.setattr(runlock.os, "open", refuse)

    with _deadline(), pytest.raises(PermissionError) as caught:
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert caught.value is eacces
    assert action.calls == 0
    assert not os.path.lexists(lock_path)


def test_retry_open_eacces_is_run_lock_error(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重取 open EACCES 为指名 cron.lock_path 的 RunLockError，cause 原样。"""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    action = RecordingAction()
    eacces = PermissionError(errno.EACCES, os.strerror(errno.EACCES))

    def fstat(fd: int) -> os.stat_result:
        info = original_fstat(fd)
        if len(events.opens) == 1:
            _replace_regular(lock_path, b"gone\n")
        return info

    def before_open(path: object, flags: int, mode: int) -> None:
        if not flags & os.O_CREAT:
            raise eacces

    events.before_open = before_open
    monkeypatch.setattr(runlock.os, "fstat", fstat)

    with (
        _deadline(),
        pytest.raises(runlock.RunLockError, match="cron.lock_path") as caught,
    ):
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert caught.value.__cause__ is eacces
    assert action.calls == 0
    assert len(events.opens) == 2
    assert not events.opens[1] & os.O_CREAT
    events.assert_released()


def test_retry_flock_eacces_is_run_lock_error(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重取 flock EACCES 为 RunLockError，不得裸漏或 acquired=False。"""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    action = RecordingAction()
    eacces = PermissionError(errno.EACCES, os.strerror(errno.EACCES))

    def fstat(fd: int) -> os.stat_result:
        info = original_fstat(fd)
        if len(events.opens) == 1:
            _replace_regular(lock_path, b"retry-eacces\n")
        return info

    def before_flock(fd: int, operation: int) -> None:
        if operation == _EX_NB and len(events.opens) == 2:
            raise eacces

    events.before_flock = before_flock
    monkeypatch.setattr(runlock.os, "fstat", fstat)

    with (
        _deadline(),
        pytest.raises(runlock.RunLockError, match="cron.lock_path") as caught,
    ):
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert caught.value.__cause__ is eacces
    assert action.calls == 0
    events.assert_released()


def test_initial_dangling_symlink_does_not_create_target(
    tmp_path: pathlib.Path,
) -> None:
    """初次 dangling symlink：O_NOFOLLOW 拒绝，不造 target。"""
    run_dir = tmp_path.resolve() / "run"
    run_dir.mkdir()
    target = run_dir / "missing-sentinel"
    lock_path = run_dir / "yd-producer.lock"
    lock_path.symlink_to(target)
    action = RecordingAction()

    with _deadline(), pytest.raises(OSError) as caught:
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert caught.value.errno == errno.ELOOP
    assert action.calls == 0
    assert lock_path.is_symlink()
    assert not target.exists()


def test_symlink_to_held_inode_is_rejected(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """指向原 inode 的 symlink 仍拒绝：证明 no-follow，不是跟随后只比 inode。"""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    action = RecordingAction()
    alias = lock_path.parent / "held-alias"

    def fstat(fd: int) -> os.stat_result:
        info = original_fstat(fd)
        if len(events.opens) == 1:
            os.link(lock_path, alias)
            lock_path.unlink()
            lock_path.symlink_to(alias)
        return info

    monkeypatch.setattr(runlock.os, "fstat", fstat)

    with _deadline(), pytest.raises(runlock.RunLockError, match="cron.lock_path"):
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert action.calls == 0
    assert lock_path.is_symlink()
    assert alias.is_file()
    events.assert_released()


def test_same_ino_different_dev_is_mismatch(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相同 ino、不同 dev 的 pair 必须判失配。"""
    events = LockEvents(monkeypatch)
    original_fstat = runlock.os.fstat
    action = RecordingAction()

    def fstat(fd: int) -> os.stat_result:
        return _shift_dev(original_fstat(fd))

    monkeypatch.setattr(runlock.os, "fstat", fstat)

    with _deadline(), pytest.raises(runlock.RunLockError, match="cron.lock_path"):
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert action.calls == 0
    assert len(events.opens) == 2
    assert lock_path.is_file()
    events.assert_released()


def test_fstat_error_is_not_contention(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两次 fstat 都 IO 失败：RunLockError，不是 acquired=False。"""
    action = RecordingAction()
    io_error = OSError(errno.EIO, "fstat injected")

    def fstat(fd: int) -> os.stat_result:
        raise io_error

    monkeypatch.setattr(runlock.os, "fstat", fstat)

    with (
        _deadline(),
        pytest.raises(runlock.RunLockError, match="cron.lock_path") as caught,
    ):
        runlock.run_with_lock(lock_path=lock_path, action=action)

    assert caught.value.__cause__ is io_error
    assert action.calls == 0


def test_action_unlink_then_new_inode_fails_loudly(lock_path: pathlib.Path) -> None:
    """action 内 unlink + 新 inode 后正常返回：退出闸 RunLockError，action 恰一次。"""
    frozen: dict[str, tuple[int, int]] = {}
    replacement: dict[str, tuple[int, int]] = {}
    calls = {"n": 0}

    def body() -> str:
        calls["n"] += 1
        frozen["pair"] = _pair(lock_path)
        replacement["pair"] = _replace_regular(lock_path, b"action-replacement\n")
        return "did-run"

    with (
        _deadline(),
        pytest.raises(runlock.RunLockError, match="cron.lock_path") as caught,
    ):
        runlock.run_with_lock(lock_path=lock_path, action=body)

    assert calls["n"] == 1
    assert frozen["pair"] != replacement["pair"]
    assert _pair(lock_path) == replacement["pair"]
    assert lock_path.read_bytes() == b"action-replacement\n"
    _assert_lock_note(
        str(caught.value),
        lock_path=lock_path,
        expected=frozen["pair"],
        actual_pair=replacement["pair"],
    )


def test_exit_identity_check_still_holds_lock(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """退出 identity 核对时仍持有 flock：嵌套进入必须跳过。"""
    original_stat = runlock.os.stat
    inner = RecordingAction()
    nested: list[runlock.RunLockResult] = []
    action = RecordingAction(result="stable")
    probing = {"exit": False, "inner": False}

    def body() -> str:
        probing["exit"] = True
        return action()

    def path_stat(
        path: str | bytes | os.PathLike[str], *args: Any, **kwargs: Any
    ) -> os.stat_result:
        info = original_stat(path, *args, **kwargs)
        if (
            probing["exit"]
            and not probing["inner"]
            and kwargs.get("follow_symlinks") is False
            and not nested
        ):
            probing["inner"] = True
            nested.append(runlock.run_with_lock(lock_path=lock_path, action=inner))
            probing["inner"] = False
        return info

    monkeypatch.setattr(runlock.os, "stat", path_stat)

    with _deadline():
        result = runlock.run_with_lock(lock_path=lock_path, action=body)

    assert result.acquired is True
    assert result.value == "stable"
    assert action.calls == 1
    assert nested[0].acquired is False
    assert inner.calls == 0


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: RuntimeError("发布段炸了"),
        lambda: KeyboardInterrupt("cancel"),
        lambda: SystemExit(2),
    ],
)
def test_action_error_keeps_object_and_adds_one_lock_note(
    lock_path: pathlib.Path, make_error: Callable[[], BaseException]
) -> None:
    """action 换 inode 再抛：同一对象/cause/旧 notes，恰一条锁漂移 note。"""
    cause = RuntimeError(_CAUSE_TEXT)
    error = make_error()
    error.__cause__ = cause
    error.add_note(_EXISTING_NOTE)
    frozen: dict[str, tuple[int, int]] = {}
    replacement: dict[str, tuple[int, int]] = {}

    def body() -> None:
        frozen["pair"] = _pair(lock_path)
        replacement["pair"] = _replace_regular(lock_path, b"error-replacement\n")
        raise error

    with _deadline(), pytest.raises(type(error)) as caught:
        runlock.run_with_lock(lock_path=lock_path, action=body)

    caught_exc = caught.value
    assert caught_exc is error
    assert caught_exc.__cause__ is cause
    notes = getattr(caught_exc, "__notes__", ())
    assert notes[0] == _EXISTING_NOTE
    assert len(notes) == 2
    _assert_lock_note(
        notes[1],
        lock_path=lock_path,
        expected=frozen["pair"],
        actual_pair=replacement["pair"],
    )
    assert _pair(lock_path) == replacement["pair"]
    assert lock_path.read_bytes() == b"error-replacement\n"


def test_exit_unavailable_note_when_path_missing(lock_path: pathlib.Path) -> None:
    """action 正常返回但路径缺失：note 含 configured path 与 unavailable。"""
    frozen: dict[str, tuple[int, int]] = {}

    def body() -> None:
        frozen["pair"] = _pair(lock_path)
        lock_path.unlink()

    with _deadline(), pytest.raises(runlock.RunLockError) as caught:
        runlock.run_with_lock(lock_path=lock_path, action=body)

    _assert_lock_note(
        str(caught.value),
        lock_path=lock_path,
        expected=frozen["pair"],
        actual_reason="unavailable",
    )
    assert not os.path.lexists(lock_path)


def test_exit_type_note_when_path_is_symlink(lock_path: pathlib.Path) -> None:
    """退出时 pathname 变成 symlink：note 含 configured path 与 type=symlink。"""
    frozen: dict[str, tuple[int, int]] = {}
    other = lock_path.parent / "other"
    other.write_bytes(b"other\n")

    def body() -> None:
        frozen["pair"] = _pair(lock_path)
        lock_path.unlink()
        lock_path.symlink_to(other)

    with _deadline(), pytest.raises(runlock.RunLockError) as caught:
        runlock.run_with_lock(lock_path=lock_path, action=body)

    _assert_lock_note(
        str(caught.value),
        lock_path=lock_path,
        expected=frozen["pair"],
        actual_reason="type=symlink",
    )
    assert lock_path.is_symlink()
    assert other.read_bytes() == b"other\n"


def test_exit_error_note_when_path_stat_fails(
    lock_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """退出 path-stat IO：note 含 error=...，不替换 action 异常。"""
    original_stat = runlock.os.stat
    io_error = OSError(errno.EIO, "exit path stat")
    cause = RuntimeError(_CAUSE_TEXT)
    error = RuntimeError("发布段炸了")
    error.__cause__ = cause
    error.add_note(_EXISTING_NOTE)
    frozen: dict[str, tuple[int, int]] = {}
    after_action = {"yes": False}

    def body() -> None:
        frozen["pair"] = _pair(lock_path)
        after_action["yes"] = True
        raise error

    def path_stat(
        path: str | bytes | os.PathLike[str], *args: Any, **kwargs: Any
    ) -> os.stat_result:
        if after_action["yes"] and kwargs.get("follow_symlinks") is False:
            raise io_error
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(runlock.os, "stat", path_stat)

    with _deadline(), pytest.raises(RuntimeError) as caught:
        runlock.run_with_lock(lock_path=lock_path, action=body)

    assert caught.value is error
    notes = getattr(error, "__notes__", ())
    assert notes[0] == _EXISTING_NOTE
    assert len(notes) == 2
    _assert_lock_note(
        notes[1],
        lock_path=lock_path,
        expected=frozen["pair"],
        actual_reason=f"error={io_error}",
    )


def test_stable_none_result_and_lock_file_survive(lock_path: pathlib.Path) -> None:
    """稳定普通文件：None 返回值与 acquired=True 可区分，锁文件保留。"""
    action = RecordingAction(result=None)
    with _deadline():
        result = runlock.run_with_lock(lock_path=lock_path, action=action)
    assert result.acquired is True
    assert result.value is None
    assert action.calls == 1
    assert lock_path.is_file()
