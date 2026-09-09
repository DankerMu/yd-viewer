# NWM@8ae9b8f2 tests/test_safe_fs.py
"""L1 evidence for `directory_identity_no_follow` (#1192).

What this file proves: the helper consumes **inode identity**, not the input
string.  Every case here runs against a real filesystem, needs no root, and is
portable to Linux (CI, node-27) as well as macOS.

HONEST LIMIT -- read before adding cases.  This layer does **not** prove that
two paths existing *at the same time* under different realpaths report one
identity.  That shape is a bind mount or a second mount point of one export,
and there is no portable, root-free construction for it: directories cannot be
hardlinked, `Path.resolve()` folds symlink aliases away, the no-follow walk
refuses a symlink final component outright, and macOS case-folding aliases are
two distinct directories on Linux.  The rename pair below is **sequential** --
one inode seen at two realpaths one after the other -- not concurrent.  The
guard-level claim was carried upstream by injection tests that are not part
of this snapshot (they exercise NWM copyback/publisher lanes), plus the POSIX
same-superblock argument recorded in the upstream change proposal.

The file also carries safe_fs's **directory-mode determinism** cases (#1513) --
see the section comment below.  Upstream they lived here rather than beside a
sibling suite for CI test-selection reasons; in yd they simply belong to the
safe_fs snapshot.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

from yd_producer.store.object_store import LocalObjectStore, ObjectStoreError
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    atomic_write_bytes_no_follow,
    directory_identity_no_follow,
    ensure_directory_no_follow,
    read_bytes_limited_no_follow,
    rmtree_no_follow,
    verify_tree_no_symlinks,
)


def test_directory_identity_is_stable_across_different_input_strings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Normalization layer only: `~` expansion and cwd-relative resolution both
    # land on one directory. A pure-string implementation passes this too --
    # test_directory_identity_survives_rename below is what kills that one.
    home = tmp_path.resolve()
    real = home / "real"
    real.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home)

    absolute = directory_identity_no_follow(real)
    tilde = directory_identity_no_follow(Path("~/real"))
    relative = directory_identity_no_follow(Path("real"))

    assert tilde == absolute
    assert relative == absolute


def test_directory_identity_survives_rename(tmp_path: Path) -> None:
    # The discriminating case: one inode, two genuinely different realpaths,
    # sequentially. `return (0, hash(str(path)))` -- the string implementation
    # this whole change replaces -- fails here and passes everything else.
    original = tmp_path / "before"
    original.mkdir()
    before = directory_identity_no_follow(original)

    renamed = tmp_path / "after"
    os.rename(original, renamed)

    assert directory_identity_no_follow(renamed) == before
    assert str(renamed) != str(original)


def test_directory_identity_equals_the_kernel_stat_pair(tmp_path: Path) -> None:
    # Pins that the returned pair is the kernel's, not a self-invented number.
    target = tmp_path / "dir"
    target.mkdir()
    info = os.stat(target)

    assert directory_identity_no_follow(target) == (info.st_dev, info.st_ino)


def test_directory_identity_differs_between_two_real_directories(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()

    assert directory_identity_no_follow(left) != directory_identity_no_follow(right)


def test_directory_identity_raises_for_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        directory_identity_no_follow(tmp_path / "absent")


@pytest.mark.parametrize("shape", ["final", "ancestor"])
def test_directory_identity_refuses_symlink_components(
    tmp_path: Path, shape: str
) -> None:
    # Type only, never the message: the same final-component symlink surfaces as
    # ENOTDIR on macOS ("Path component is not a directory") and ELOOP on Linux
    # ("Path component must not be a symlink"). Asserting text reds on one of
    # the two platforms this repo runs on.
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    probed = link if shape == "final" else link / "child"
    if shape == "ancestor":
        (real / "child").mkdir()

    with pytest.raises(SafeFilesystemError):
        directory_identity_no_follow(probed)


# Read-only formal-tree preflight (#25).


def _tree_snapshot(root: Path) -> dict[str, tuple[str, int, bytes | str]]:
    snapshot: dict[str, tuple[str, int, bytes | str]] = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        relative = str(path.relative_to(root))
        if stat.S_ISLNK(info.st_mode):
            payload: bytes | str = os.readlink(path)
            kind = "symlink"
        elif stat.S_ISREG(info.st_mode):
            payload = path.read_bytes()
            kind = "file"
        elif stat.S_ISDIR(info.st_mode):
            payload = b""
            kind = "dir"
        else:
            payload = b""
            kind = "special"
        snapshot[relative] = (kind, info.st_mode, payload)
    return snapshot


def test_verify_tree_no_symlinks_accepts_real_nested_tree_without_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    target = root / "nested" / "deeper"
    target.mkdir(parents=True)
    (root / "first.bin").write_bytes(b"first\x00")
    (target / "last.bin").write_bytes(b"last\xff")
    fifo = root / "stream"
    os.mkfifo(fifo)
    before = _tree_snapshot(root)

    verify_tree_no_symlinks(root)

    assert _tree_snapshot(root) == before
    assert stat.S_ISFIFO(fifo.lstat().st_mode)
    assert (target / "last.bin").read_bytes() == b"last\xff"


def test_verify_tree_no_symlinks_refuses_mixed_tree_without_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    before_link = root / "before.bin"
    before_link.write_bytes(b"before\x00")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside\xff")
    linked = root / "middle-link"
    linked.symlink_to(outside)
    after_link = root / "after.bin"
    after_link.write_bytes(b"after\x01")
    before = _tree_snapshot(root)
    outside_before = outside.read_bytes()

    with pytest.raises(SafeFilesystemError) as info:
        verify_tree_no_symlinks(root)

    assert info.value.kind == "unsafe"
    assert _tree_snapshot(root) == before
    assert linked.is_symlink()
    assert before_link.read_bytes() == b"before\x00"
    assert after_link.read_bytes() == b"after\x01"
    assert outside.read_bytes() == outside_before


def test_verify_tree_no_symlinks_refuses_nested_descendant_without_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    nested = root / "first" / "second"
    nested.mkdir(parents=True)
    (root / "before.bin").write_bytes(b"before\x00")
    (nested / "before-link.bin").write_bytes(b"nested-before\x01")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside\xff")
    linked = nested / "middle-link"
    linked.symlink_to(outside)
    (nested / "after-link.bin").write_bytes(b"nested-after\x02")
    (root / "after.bin").write_bytes(b"after\x03")
    before = _tree_snapshot(root)
    outside_before = outside.read_bytes()

    with pytest.raises(SafeFilesystemError) as info:
        verify_tree_no_symlinks(root)

    assert info.value.kind == "unsafe"
    assert _tree_snapshot(root) == before
    assert linked.is_symlink()
    assert (nested / "before-link.bin").read_bytes() == b"nested-before\x01"
    assert (nested / "after-link.bin").read_bytes() == b"nested-after\x02"
    assert outside.read_bytes() == outside_before


@pytest.mark.parametrize("shape", ["root", "intermediate"])
def test_verify_tree_no_symlinks_refuses_symlink_path_components(
    tmp_path: Path, shape: str
) -> None:
    real = tmp_path / "real"
    target = real / "nested"
    target.mkdir(parents=True)
    (target / "payload.bin").write_bytes(b"payload\xff")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    checked = linked if shape == "root" else linked / "nested"
    before = _tree_snapshot(real)

    with pytest.raises(SafeFilesystemError) as info:
        verify_tree_no_symlinks(checked)

    assert info.value.kind == "unsafe"
    assert _tree_snapshot(real) == before
    assert linked.is_symlink()
    assert (target / "payload.bin").read_bytes() == b"payload\xff"


def test_verify_tree_no_symlinks_wraps_traversal_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    payload = root / "payload.bin"
    payload.write_bytes(b"payload")
    before = _tree_snapshot(root)
    original_listdir = os.listdir

    def unreadable_listdir(path: int | str | os.PathLike[str]) -> list[str]:
        if isinstance(path, int):
            raise OSError("synthetic directory read failure")
        return original_listdir(path)

    monkeypatch.setattr(os, "listdir", unreadable_listdir)

    with pytest.raises(SafeFilesystemError) as info:
        verify_tree_no_symlinks(root)

    assert info.value.kind == "io"
    assert _tree_snapshot(root) == before
    assert payload.read_bytes() == b"payload"


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
