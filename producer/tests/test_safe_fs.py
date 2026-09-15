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

This file also keeps the read-only formal-tree preflight (#25). Directory-mode
determinism, atomic-write cleanup, directory-walk fd, and bounded-read scenes
live in sibling modules.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    directory_identity_no_follow,
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
