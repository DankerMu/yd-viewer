"""No-follow race, identity and returned-fd lifetime for the three #63 readers."""

from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest
from cfg_ic_fixtures import build_cfg_ic
from safe_fs_fixtures import _assert_fd_closed

from yd_producer import controller, rawscan
from yd_producer.state import cfg_ic
from yd_producer.store.safe_fs import SafeFilesystemError


@pytest.fixture
def tmp_path(tmp_path: Path) -> Path:
    return tmp_path.resolve()


_HEADER = b"1 6 0\n"
_CFG_IC_PAYLOAD = build_cfg_ic(mesh_count=1, river_count=1).payload
_CFG_IC_REPLACEMENT_PAYLOAD = build_cfg_ic(mesh_count=2, river_count=1).payload
_CHILD_TIMEOUT_SECONDS = 5
_FIFO_RACE_CHILD = r"""
from pathlib import Path
import os, sys

mode, kind, raw_path, raw_size, raw_tests_dir = sys.argv[1:]
path = Path(raw_path)
size = int(raw_size)
tests_dir = Path(raw_tests_dir)
retained = path.with_name(path.name + ".retained")

if kind == "controller":
    from yd_producer import controller

    def exercise():
        result = controller._read_header_line(path, size=size)
        assert result is controller.StopReason.STATE_UNREADABLE, result
elif kind == "cfg_ic":
    from yd_producer.state import cfg_ic
    from yd_producer.store.safe_fs import SafeFilesystemError

    def exercise():
        try:
            cfg_ic.parse(path, max_bytes=1024)
        except ValueError as error:
            assert isinstance(error.__cause__, (SafeFilesystemError, OSError)), error
        else:
            raise AssertionError("FIFO cfg.ic accepted")
else:
    from yd_producer import rawscan

    def exercise():
        assert rawscan._is_readable(path) is False

def announce_swap():
    print("SWAPPED", kind, flush=True)

if mode == "prestat":
    path.rename(retained)
    os.mkfifo(path)
    announce_swap()
    exercise()
elif mode == "poststat":
    # Append only test helpers: inherited PYTHONPATH keeps source-copy precedence.
    if str(tests_dir) not in sys.path:
        sys.path.append(str(tests_dir))
    import pytest
    from test_bind_state_raw_reads import _replace_after_expected_stat

    with pytest.MonkeyPatch.context() as monkeypatch:
        race = _replace_after_expected_stat(
            monkeypatch,
            path,
            kind="fifo",
            replacement_bytes=b"",
            on_swap=announce_swap,
        )
        exercise()
        assert race["swapped"] is True, "expected-stat FIFO swap did not fire"
else:
    raise AssertionError(f"unsupported FIFO race mode: {mode}")

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


def _race_payload(reader: str, *, replacement: bool = False) -> bytes:
    if reader != "cfg_ic":
        return _HEADER
    return _CFG_IC_REPLACEMENT_PAYLOAD if replacement else _CFG_IC_PAYLOAD


def _identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _run_fifo_race_in_child(reader: str, *, after_expected_stat: bool) -> None:
    """Run a FIFO replacement behind a bounded direct child."""
    with tempfile.TemporaryDirectory(prefix="yd63-") as directory:
        target = Path(directory).resolve() / "candidate.cfg.ic"
        target.write_bytes(_race_payload(reader))
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _FIFO_RACE_CHILD,
                "poststat" if after_expected_stat else "prestat",
                reader,
                str(target),
                str(target.stat().st_size),
                str(Path(__file__).resolve().parent),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Preserve a parent source-copy mutation's import precedence verbatim.
            env=os.environ.copy(),
        )
        try:
            stdout, stderr = process.communicate(timeout=_CHILD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            pytest.fail(
                f"FIFO race child timed out after {_CHILD_TIMEOUT_SECONDS}s; "
                f"stdout={stdout!r}; stderr={stderr!r}"
            )
        assert f"SWAPPED {reader}" in stdout, stderr
        assert f"PASS {reader}" in stdout, stderr
        assert process.returncode == 0, stderr


def _replace_after_expected_stat(
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    *,
    kind: str,
    replacement_bytes: bytes,
    on_swap: Callable[[], None] | None = None,
) -> dict[str, bool | tuple[int, int] | None]:
    """Swap only after safe_fs's expected stat and retain the original inode."""
    real_stat = os.stat
    real_open = os.open
    expected_name = target.name
    state: dict[str, bool | tuple[int, int] | None] = {
        "armed": False,
        "swapped": False,
        "expected_identity": None,
        "retained_identity": None,
        "replacement_identity": None,
    }

    def stating(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if (
            kwargs.get("dir_fd") is not None
            and path == expected_name
            and kwargs.get("follow_symlinks") is False
            and stat.S_ISREG(result.st_mode)
        ):
            state["armed"] = True
            state["expected_identity"] = _identity(result)
        return result

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        if (
            state["armed"]
            and dir_fd is not None
            and path == expected_name
            and _is_file_open(flags)
        ):
            state["armed"] = False
            expected_identity = state["expected_identity"]
            assert isinstance(expected_identity, tuple)
            assert _identity(real_stat(target)) == expected_identity
            retained = target.with_name(target.name + ".retained")
            if kind == "symlink":
                target.rename(retained)
                state["retained_identity"] = _identity(real_stat(retained))
                target.symlink_to(retained)
                assert _identity(real_stat(target)) == expected_identity
            elif kind == "inode":
                replacement = target.with_name(target.name + ".replacement")
                replacement.write_bytes(replacement_bytes)
                replacement_identity = _identity(real_stat(replacement))
                assert replacement_identity != expected_identity
                target.rename(retained)
                state["retained_identity"] = _identity(real_stat(retained))
                assert state["retained_identity"] == expected_identity
                os.rename(replacement, target)
                assert _identity(real_stat(target)) == replacement_identity
                state["replacement_identity"] = replacement_identity
            elif kind == "fifo":
                target.rename(retained)
                state["retained_identity"] = _identity(real_stat(retained))
                assert state["retained_identity"] == expected_identity
                os.mkfifo(target)
            else:
                raise AssertionError(f"unsupported replacement kind: {kind}")
            state["swapped"] = True
            if on_swap is not None:
                on_swap()
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "stat", stating)
    monkeypatch.setattr(os, "open", opening)
    return state


@pytest.mark.parametrize("reader", ["controller", "cfg_ic", "rawscan"])
def test_prestat_regular_replaced_with_fifo_refuses_within_timeout(
    reader: str,
) -> None:
    _run_fifo_race_in_child(reader, after_expected_stat=False)


@pytest.mark.parametrize("kind", ["symlink", "inode"])
@pytest.mark.parametrize("reader", ["controller", "cfg_ic", "rawscan"])
def test_expected_stat_then_os_open_swap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, reader: str
) -> None:
    target = tmp_path.resolve() / "candidate.cfg.ic"
    target.write_bytes(_race_payload(reader))
    size = target.stat().st_size
    race = _replace_after_expected_stat(
        monkeypatch,
        target,
        kind=kind,
        replacement_bytes=_race_payload(reader, replacement=kind == "inode"),
    )

    if reader == "controller":
        result = controller._read_header_line(target, size=size)
        assert result is controller.StopReason.STATE_UNREADABLE
    elif reader == "cfg_ic":
        with pytest.raises(ValueError) as excinfo:
            cfg_ic.parse(target, max_bytes=1024)
        assert isinstance(excinfo.value.__cause__, (SafeFilesystemError, OSError))
    else:
        assert rawscan._is_readable(target) is False

    assert race["swapped"] is True
    if kind == "symlink":
        assert race["retained_identity"] == race["expected_identity"]
    else:
        assert race["replacement_identity"] != race["expected_identity"]


@pytest.mark.parametrize("reader", ["controller", "cfg_ic", "rawscan"])
def test_expected_stat_then_fifo_open_is_refused_without_blocking(reader: str) -> None:
    _run_fifo_race_in_child(reader, after_expected_stat=True)


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
