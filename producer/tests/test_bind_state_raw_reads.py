"""No-follow race, identity and returned-fd lifetime for the three #63 readers."""

from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from safe_fs_fixtures import _assert_fd_closed

from yd_producer import controller, rawscan
from yd_producer.state import cfg_ic


@pytest.fixture
def tmp_path(tmp_path: Path) -> Path:
    return tmp_path.resolve()


_HEADER = b"1 6 0\n"
_CHILD = r"""
from pathlib import Path
import os, sys, tempfile

kind = sys.argv[1]
with tempfile.TemporaryDirectory(prefix="yd63-") as directory:
    path = Path(directory).resolve() / "candidate"
    path.write_bytes(b"1 6 0\n")
    size = path.stat().st_size
    path.unlink()
    os.mkfifo(path)
    print("SWAPPED", kind, flush=True)
    if kind == "controller":
        from yd_producer import controller
        result = controller._read_header_line(path, size=size)
        assert result is controller.StopReason.STATE_UNREADABLE, result
    elif kind == "cfg_ic":
        from yd_producer.state import cfg_ic
        try:
            cfg_ic.parse(path, max_bytes=1024)
        except ValueError:
            pass
        else:
            raise AssertionError("FIFO cfg.ic accepted")
    else:
        from yd_producer import rawscan
        assert rawscan._is_readable(path) is False
    print("PASS", kind, flush=True)
"""


def _is_file_open(flags: int) -> bool:
    return not (flags & getattr(os, "O_DIRECTORY", 0))


def _install_file_fd_tracker(
    monkeypatch: pytest.MonkeyPatch,
    *,
    read_error: BaseException | None = None,
    close_error: OSError | None = None,
) -> dict[str, int | None]:
    real_open = os.open
    real_close = os.close
    real_read = os.read
    captured: dict[str, int | None] = {"fd": None, "close_count": 0}

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if dir_fd is not None and _is_file_open(flags):
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

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "close", closing)
    if read_error is not None:
        monkeypatch.setattr(os, "read", reading)
    return captured


def _replace_after_expected_stat(
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    *,
    kind: str,
) -> None:
    real_stat = os.stat
    real_open = os.open
    expected_name = target.name
    armed = {"ready": False}

    def stating(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if (
            kwargs.get("dir_fd") is not None
            and path == expected_name
            and kwargs.get("follow_symlinks") is False
            and stat.S_ISREG(result.st_mode)
        ):
            armed["ready"] = True
        return result

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        if (
            armed["ready"]
            and dir_fd is not None
            and path == expected_name
            and _is_file_open(flags)
        ):
            armed["ready"] = False
            target.unlink()
            if kind == "fifo":
                os.mkfifo(target)
            elif kind == "symlink":
                other = target.with_name(target.name + ".other")
                other.write_bytes(_HEADER)
                target.symlink_to(other)
            else:
                other = target.with_name(target.name + ".other")
                other.write_bytes(_HEADER)
                os.rename(other, target)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "stat", stating)
    monkeypatch.setattr(os, "open", opening)


@pytest.mark.parametrize("kind", ["controller", "cfg_ic", "rawscan"])
def test_prestat_regular_replaced_with_fifo_refuses_within_timeout(kind: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD, kind],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert "SWAPPED" in completed.stdout
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("kind", ["symlink", "inode"])
@pytest.mark.parametrize("reader", ["controller", "cfg_ic", "rawscan"])
def test_expected_stat_then_os_open_swap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, reader: str
) -> None:
    root = tmp_path.resolve()
    target = root / "candidate.cfg.ic"
    target.write_bytes(_HEADER)
    size = target.stat().st_size
    _replace_after_expected_stat(monkeypatch, target, kind=kind)

    if reader == "controller":
        result = controller._read_header_line(target, size=size)
        assert result is controller.StopReason.STATE_UNREADABLE
    elif reader == "cfg_ic":
        with pytest.raises(ValueError):
            cfg_ic.parse(target, max_bytes=1024)
    else:
        assert rawscan._is_readable(target) is False


@pytest.mark.parametrize("reader", ["controller", "cfg_ic", "rawscan"])
def test_expected_stat_then_fifo_open_is_refused_without_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root = tmp_path.resolve()
    target = root / "candidate.cfg.ic"
    target.write_bytes(_HEADER)
    size = target.stat().st_size
    _replace_after_expected_stat(monkeypatch, target, kind="fifo")

    if reader == "controller":
        result = controller._read_header_line(target, size=size)
        assert result is controller.StopReason.STATE_UNREADABLE
    elif reader == "cfg_ic":
        with pytest.raises(ValueError):
            cfg_ic.parse(target, max_bytes=1024)
    else:
        assert rawscan._is_readable(target) is False


def test_header_success_and_early_return_close_the_returned_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "state.cfg.ic"
    target.write_bytes(_HEADER + b"Index Canopy\n")
    captured = _install_file_fd_tracker(monkeypatch)

    line = controller._read_header_line(target, size=target.stat().st_size)

    assert line == "1 6 0"
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


@pytest.mark.parametrize(
    ("exc_type", "message"),
    [(KeyboardInterrupt, "primary interrupt"), (SystemExit, "primary exit")],
    ids=["keyboardinterrupt", "systemexit"],
)
def test_header_cancellation_keeps_primary_when_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc_type: type[BaseException],
    message: str,
) -> None:
    root = tmp_path.resolve()
    target = root / "state.cfg.ic"
    target.write_bytes(_HEADER)
    injected = exc_type(message)
    close_error = OSError(errno.ESTALE, "stale close")
    captured = _install_file_fd_tracker(
        monkeypatch, read_error=injected, close_error=close_error
    )

    with pytest.raises(exc_type) as info:
        controller._read_header_line(target, size=target.stat().st_size)

    assert info.value is injected
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


def test_header_read_eio_and_close_failure_is_state_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "state.cfg.ic"
    target.write_bytes(_HEADER)
    captured = _install_file_fd_tracker(
        monkeypatch,
        read_error=OSError(errno.EIO, "injected read EIO"),
        close_error=OSError(errno.ESTALE, "stale close"),
    )

    result = controller._read_header_line(target, size=target.stat().st_size)

    assert result is controller.StopReason.STATE_UNREADABLE
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


def test_header_close_only_failure_is_state_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "state.cfg.ic"
    target.write_bytes(_HEADER)
    captured = _install_file_fd_tracker(
        monkeypatch, close_error=OSError(errno.ESTALE, "stale close")
    )

    result = controller._read_header_line(target, size=target.stat().st_size)

    assert result is controller.StopReason.STATE_UNREADABLE
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


def test_header_success_inside_unrelated_except_handler_does_not_use_ambient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "state.cfg.ic"
    target.write_bytes(_HEADER)
    captured = _install_file_fd_tracker(monkeypatch)
    seen: list[object] = []
    try:
        raise RuntimeError("ambient")
    except RuntimeError:
        seen.append(controller._read_header_line(target, size=target.stat().st_size))
    assert seen == ["1 6 0"]
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


def test_cfg_ic_limited_read_closes_returned_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    built = Path(root / "bound.cfg.ic")
    built.write_bytes(_HEADER)
    captured = _install_file_fd_tracker(monkeypatch)

    data = cfg_ic._read_bytes_limited(built, max_bytes=64)

    assert data == _HEADER
    assert captured["fd"] is not None
    assert captured["close_count"] == 1
    _assert_fd_closed(captured["fd"])


def test_raw_empty_regular_file_is_readable(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    target = root / "empty.grib2"
    target.write_bytes(b"")
    assert rawscan._is_readable(target) is True
    assert rawscan._check(target) == "ok"


def test_raw_leaf_and_ancestor_symlinks_are_unreadable(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    real = root / "real"
    real.mkdir()
    payload = real / "payload.grib2"
    payload.write_bytes(b"GRIB\xff")
    leaf = root / "leaf.grib2"
    leaf.symlink_to(payload)
    alias = root / "alias"
    alias.symlink_to(real, target_is_directory=True)
    assert rawscan._is_readable(leaf) is False
    assert rawscan._is_readable(alias / "payload.grib2") is False
    assert os.readlink(leaf) == str(payload)
    assert payload.read_bytes() == b"GRIB\xff"


def test_cfg_ic_leaf_and_ancestor_symlinks_raise_value_error(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    real = root / "real"
    real.mkdir()
    payload = real / "state.cfg.ic"
    payload.write_bytes(_HEADER)
    leaf = root / "leaf.cfg.ic"
    leaf.symlink_to(payload)
    alias = root / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        cfg_ic.parse(leaf)
    with pytest.raises(ValueError):
        cfg_ic.parse(alias / "state.cfg.ic")
    assert payload.read_bytes() == _HEADER


def test_header_leaf_and_ancestor_symlinks_are_state_unreadable(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    real = root / "real"
    real.mkdir()
    payload = real / "state.cfg.ic"
    payload.write_bytes(_HEADER)
    leaf = root / "leaf.cfg.ic"
    leaf.symlink_to(payload)
    alias = root / "alias"
    alias.symlink_to(real, target_is_directory=True)
    size = payload.stat().st_size
    assert (
        controller._read_header_line(leaf, size=size)
        is controller.StopReason.STATE_UNREADABLE
    )
    assert (
        controller._read_header_line(alias / "state.cfg.ic", size=size)
        is controller.StopReason.STATE_UNREADABLE
    )
    assert payload.read_bytes() == _HEADER
