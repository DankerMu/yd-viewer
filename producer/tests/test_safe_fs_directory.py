# NWM@8ae9b8f2 tests/test_safe_fs.py
"""Directory-mode, path-error, and directory-walk fd scenes for `safe_fs`.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import errno
import json
import os
import resource
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from safe_fs_fixtures import _assert_fd_closed

from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    ensure_directory_no_follow,
    read_bytes_limited_no_follow,
    rmtree_no_follow,
)

# Directory-mode determinism (#1513).
#
# `ensure_directory_no_follow` used to call `os.mkdir` with no mode, so the
# landed permission was `0o777 & ~umask` -- a function of the ambient
# environment rather than of the code.  The upstream lock gate is
# fail-closed on any `0o022` bit in the lock's direct parent, so on a
# umask-0002 host (node-27, the project's backend pytest oracle) every
# safe_fs-created lock parent landed `0o775` and was refused.
#
# Both sides are pinned below on purpose: the upstream repository's pre-existing umask
# tests all pin the STRICT side, which is precisely the coverage shape that let
# the permissive-side bug survive.  The `0o077` case is the guard against the
# tempting "fix" of adding an `fchmod` after `mkdir` -- that would clear the
# umask's influence in BOTH directions and silently widen `0o700` to `0o755`.


def test_ensure_directory_pins_its_mode_under_a_permissive_umask(
    tmp_path: Path,
) -> None:
    target = tmp_path / "permissive" / "child"

    previous_umask = os.umask(0o002)
    try:
        ensure_directory_no_follow(target)
    finally:
        os.umask(previous_umask)

    # Both components are safe_fs-created; the intermediate one matters just as
    # much, because that is the one a lock's direct parent usually is.
    for created in (target.parent, target):
        landed = stat.S_IMODE(created.stat().st_mode)
        assert landed == 0o755, f"{created} landed {landed:#o}"
        assert landed & 0o022 == 0


def test_ensure_directory_is_not_widened_under_a_restrictive_umask(
    tmp_path: Path,
) -> None:
    # The umask may further RESTRICT a safe_fs directory; it may never loosen
    # it.  0o755 & ~0o077 == 0o700, byte-identical to the mode-less behavior.
    target = tmp_path / "restrictive" / "child"

    previous_umask = os.umask(0o077)
    try:
        ensure_directory_no_follow(target)
    finally:
        os.umask(previous_umask)

    for created in (target.parent, target):
        landed = stat.S_IMODE(created.stat().st_mode)
        assert landed == 0o700, f"{created} landed {landed:#o}"


def test_ensure_directory_leaves_an_existing_directory_mode_alone(
    tmp_path: Path,
) -> None:
    # Forward-only: the helper never chmods a prefix it did not create, so
    # directories that predate this change keep their mode and no migration is
    # implied.  Callers that pre-create a directory and own its mode themselves
    # rely on exactly this.  (The upstream caller named here belongs to the
    # state index/copyback surface, which yd does not snapshot.)
    existing = tmp_path / "existing"
    existing.mkdir()
    os.chmod(existing, 0o775)

    ensure_directory_no_follow(existing)

    assert stat.S_IMODE(existing.stat().st_mode) == 0o775


# --- Undeterminable home directory (#1547) -----------------------------------
#
# `Path.expanduser()` throws a bare, errno-less RuntimeError when no home
# directory can be determined.  `_expand_path` is the shared prelude of every
# public entry point here, so that throw used to defeat the module's error
# contract on all of them at once: `SafeFilesystemError` IS a `RuntimeError`
# subclass but not the reverse, so `except SafeFilesystemError` callers missed
# it and `error.kind` readers got an AttributeError.

_UNKNOWN_HOME = "~nosuchuser_zz"


@pytest.mark.parametrize("entry", ["write", "read", "delete"])
def test_undeterminable_home_is_a_structured_unsafe_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    # cwd is pinned explicitly because `_expand_path` anchors relative results at
    # `Path.cwd()`: a regression that kept the literal `~...` component instead
    # of refusing would otherwise create it in the yd repository working tree.
    monkeypatch.chdir(tmp_path)
    target = Path(_UNKNOWN_HOME) / "lane" / "leaf"
    calls = {
        "write": lambda: ensure_directory_no_follow(target),
        "read": lambda: read_bytes_limited_no_follow(target, max_bytes=1024),
        "delete": lambda: rmtree_no_follow(target),
    }

    with pytest.raises(SafeFilesystemError) as excinfo:
        calls[entry]()

    assert excinfo.value.kind == "unsafe"
    assert list(tmp_path.iterdir()) == []
    assert not list(tmp_path.glob("~*"))


def test_undeterminable_home_refusal_is_not_a_bare_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The directionality pin: a caller that only knows the module's own error
    # type still catches this, which is what makes the change a pure narrowing.
    monkeypatch.chdir(tmp_path)
    caught: SafeFilesystemError | None = None
    try:
        ensure_directory_no_follow(Path(_UNKNOWN_HOME) / "lane")
    except SafeFilesystemError as error:
        caught = error

    assert caught is not None
    assert isinstance(caught, RuntimeError)
    assert type(caught) is not RuntimeError
    assert list(tmp_path.iterdir()) == []


# Directory-walk fd cleanup on deep refusals (#55).
#
# `ensure_directory_no_follow` used to leak the current child directory fd
# (and, on a close error after a successful next open, the newly opened
# next fd) whenever a later component was a regular file or a symlink.
# Repeating that refusal under a low RLIMIT_NOFILE then flipped `unsafe`
# into `io` via EMFILE. Discriminators below are the leaked fds
# (`fstat` -> EBADF, not /proc), the public `kind`, and an isolated child
# with a soft limit of 64.

_ENSURE_ATTEMPTS = 200
_ENSURE_CHILD_SOFT_NOFILE = 64


def _capture_directory_open_fds(
    monkeypatch: pytest.MonkeyPatch,
) -> list[int]:
    real_open = os.open
    captured: list[int] = []

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if flags & os.O_DIRECTORY:
            captured.append(fd)
        return fd

    monkeypatch.setattr(os, "open", opening)
    return captured


def _plant_deep_blocker(root: Path, *, kind: str) -> Path:
    lane = root / "lane"
    lane.mkdir()
    blocker = lane / "blocker"
    if kind == "file":
        blocker.write_bytes(b"not-a-directory")
    else:
        blocker.symlink_to(root / "missing-target")
    return lane / "blocker" / "leaf"


def _name_of_open_path(path: object) -> str:
    if isinstance(path, bytes):
        return os.fsdecode(path)
    return os.fspath(path)


_ENSURE_RLIMIT_CHILD = r"""
import json
import os
import resource
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path


def main() -> int:
    payload = json.loads(sys.stdin.read())
    source_path = Path(payload["source_path"])
    target = Path(payload["target"])
    containment_root = Path(payload["containment_root"])
    attempts = int(payload["attempts"])
    soft_limit = int(payload["soft_limit"])
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (soft_limit, hard))
    loader = SourceFileLoader("issue55_safe_fs", str(source_path))
    spec = spec_from_loader(loader.name, loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    kinds = []
    for _ in range(attempts):
        try:
            module.ensure_directory_no_follow(
                target, containment_root=containment_root
            )
        except module.SafeFilesystemError as error:
            kinds.append(error.kind)
        except OSError as error:
            kinds.append(f"oserror:{error.errno}")
    json.dump({"kinds": kinds, "parent_soft": soft}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _run_ensure_rlimit_child(
    *,
    source_path: Path,
    target: Path,
    containment_root: Path,
    attempts: int = _ENSURE_ATTEMPTS,
    soft_limit: int = _ENSURE_CHILD_SOFT_NOFILE,
) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-c", _ENSURE_RLIMIT_CHILD],
        input=json.dumps(
            {
                "source_path": str(source_path),
                "target": str(target),
                "containment_root": str(containment_root),
                "attempts": attempts,
                "soft_limit": soft_limit,
            }
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize("blocker", ["file", "symlink"])
def test_ensure_directory_repeated_deep_refusal_closes_walk_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocker: str
) -> None:
    root = tmp_path.resolve()
    target = _plant_deep_blocker(root, kind=blocker)
    opened = _capture_directory_open_fds(monkeypatch)

    for _ in range(_ENSURE_ATTEMPTS):
        opened.clear()
        with pytest.raises(SafeFilesystemError) as info:
            ensure_directory_no_follow(target, containment_root=root)
        assert info.value.kind == "unsafe"
        assert opened
        for fd in opened:
            _assert_fd_closed(fd)


def test_ensure_directory_depth0_control_closes_root_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    opened = _capture_directory_open_fds(monkeypatch)

    for _ in range(_ENSURE_ATTEMPTS):
        opened.clear()
        returned = ensure_directory_no_follow(root, containment_root=root)
        assert returned == root
        assert opened
        for fd in opened:
            _assert_fd_closed(fd)


@pytest.mark.parametrize("blocker", ["file", "symlink"])
def test_ensure_directory_isolated_rlimit_keeps_deep_refusal_unsafe(
    tmp_path: Path, blocker: str
) -> None:
    root = tmp_path.resolve()
    target = _plant_deep_blocker(root, kind=blocker)
    import yd_producer.store.safe_fs as safe_fs_module

    parent_soft, _parent_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    payload = _run_ensure_rlimit_child(
        source_path=Path(safe_fs_module.__file__),
        target=target,
        containment_root=root,
    )

    kinds = payload["kinds"]
    assert kinds == ["unsafe"] * _ENSURE_ATTEMPTS
    assert payload["parent_soft"] == parent_soft
    after_soft, _after_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    assert after_soft == parent_soft


def test_ensure_directory_injected_open_eio_retains_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    lane = root / "lane"
    lane.mkdir()
    target = lane / "child"
    opened = _capture_directory_open_fds(monkeypatch)
    injected = OSError(errno.EIO, "injected open EIO")
    real_open = os.open

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        name = path if isinstance(path, str) else os.fsdecode(path)
        if name == "child" and dir_fd is not None:
            raise injected
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", opening)

    with pytest.raises(SafeFilesystemError) as info:
        ensure_directory_no_follow(target, containment_root=root)

    assert info.value.kind == "io"
    assert info.value.__cause__ is injected
    assert not target.exists()
    for fd in opened:
        _assert_fd_closed(fd)


def test_ensure_directory_injected_mkdir_eio_retains_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    lane = root / "lane"
    lane.mkdir()
    target = lane / "child"
    opened = _capture_directory_open_fds(monkeypatch)
    injected = OSError(errno.EIO, "injected mkdir EIO")

    def making(path, mode=0o777, *, dir_fd=None):
        raise injected

    monkeypatch.setattr(os, "mkdir", making)

    with pytest.raises(SafeFilesystemError) as info:
        ensure_directory_no_follow(target, containment_root=root)

    assert info.value.kind == "io"
    assert info.value.__cause__ is injected
    assert not target.exists()
    for fd in opened:
        _assert_fd_closed(fd)


def test_ensure_directory_unsafe_cleanup_close_preserves_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = _plant_deep_blocker(root, kind="file")
    opened = _capture_directory_open_fds(monkeypatch)
    close_error = OSError(errno.EIO, "injected walk close failure")
    real_close = os.close
    real_open = os.open
    walk_root: dict[str, int | None] = {"fd": None}

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if _name_of_open_path(path) == "lane" and dir_fd is not None:
            walk_root["fd"] = dir_fd
        return fd

    def closing(fd: int) -> None:
        if walk_root["fd"] is not None and fd == walk_root["fd"]:
            real_close(fd)
            raise close_error
        real_close(fd)

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "close", closing)

    with pytest.raises(SafeFilesystemError) as info:
        ensure_directory_no_follow(target, containment_root=root)

    assert info.value.kind == "unsafe"
    assert info.value.__cause__ is not close_error
    assert opened
    for fd in opened:
        _assert_fd_closed(fd)
    assert walk_root["fd"] is not None
    _assert_fd_closed(walk_root["fd"])
    assert target.parent.is_file()


def test_ensure_directory_io_cleanup_close_preserves_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    lane = root / "lane"
    lane.mkdir()
    target = lane / "child"
    opened = _capture_directory_open_fds(monkeypatch)
    injected = OSError(errno.EIO, "injected open EIO")
    close_error = OSError(errno.EBADF, "injected walk close failure")
    real_open = os.open
    real_close = os.close
    walk_root: dict[str, int | None] = {"fd": None}

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        name = _name_of_open_path(path)
        if name == "child" and dir_fd is not None:
            raise injected
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if name == "lane" and dir_fd is not None:
            walk_root["fd"] = dir_fd
        return fd

    def closing(fd: int) -> None:
        if walk_root["fd"] is not None and fd == walk_root["fd"]:
            real_close(fd)
            raise close_error
        real_close(fd)

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "close", closing)

    with pytest.raises(SafeFilesystemError) as info:
        ensure_directory_no_follow(target, containment_root=root)

    assert info.value.kind == "io"
    assert info.value.__cause__ is injected
    assert info.value.__cause__ is not close_error
    for fd in opened:
        _assert_fd_closed(fd)
    assert walk_root["fd"] is not None
    _assert_fd_closed(walk_root["fd"])
    assert not target.exists()


def test_ensure_directory_advancement_close_error_closes_newly_opened_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    nested = root / "lane" / "nested"
    nested.mkdir(parents=True)
    target = nested / "leaf"
    opened = _capture_directory_open_fds(monkeypatch)
    close_error = OSError(errno.EIO, "injected advancement close failure")
    real_open = os.open
    real_close = os.close
    owned: dict[str, int | None] = {"lane": None, "nested": None}

    def opening(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        name = _name_of_open_path(path)
        if name in owned and dir_fd is not None:
            owned[name] = fd
        return fd

    def closing(fd: int) -> None:
        if owned["lane"] is not None and fd == owned["lane"]:
            real_close(fd)
            raise close_error
        real_close(fd)

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "close", closing)

    with pytest.raises(OSError) as info:
        ensure_directory_no_follow(target, containment_root=root)

    assert info.value is close_error
    assert owned["lane"] is not None
    assert owned["nested"] is not None
    _assert_fd_closed(owned["lane"])
    _assert_fd_closed(owned["nested"])
    for fd in opened:
        _assert_fd_closed(fd)
    assert nested.is_dir()
    assert not target.exists()


def test_ensure_directory_success_still_returns_path_and_closes_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    target = root / "lane" / "child"
    opened = _capture_directory_open_fds(monkeypatch)

    returned = ensure_directory_no_follow(target, containment_root=root)

    assert returned == target
    assert target.is_dir()
    assert stat.S_IMODE(target.stat().st_mode) & 0o022 == 0
    assert opened
    for fd in opened:
        _assert_fd_closed(fd)
