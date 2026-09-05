"""Private identity-conditional quarantine-tree deletion for `safe_fs`."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    _open_child_dir,
    _reject_unsafe_entry_name,
    _remove_tree_contents_allow_symlinks_fd,
    open_directory_no_follow,
)


def remove_tree_allow_symlinks(
    parent: Path,
    name: str,
    *,
    expand_path,
    containment_root: Path | None = None,
    missing_ok: bool = True,
    expected_root_identity: tuple[int, int] | None = None,
) -> None:
    """See `safe_fs.remove_tree_allow_symlinks` for the public contract."""
    _reject_unsafe_entry_name(name)
    target = expand_path(parent) / name
    try:
        parent_fd = open_directory_no_follow(parent, containment_root=containment_root)
    except FileNotFoundError:
        if missing_ok and expected_root_identity is None:
            return
        if expected_root_identity is not None:
            raise SafeFilesystemError(
                f"Expected tree root {target} is absent", kind="identity_changed"
            )
        raise
    try:
        _remove_named_tree(
            parent_fd,
            name,
            target,
            missing_ok=missing_ok,
            expected_root_identity=expected_root_identity,
        )
    except SafeFilesystemError:
        raise
    except OSError as error:
        raise SafeFilesystemError(
            f"Failed to remove tree {target}: {error}", kind="io"
        ) from error
    finally:
        os.close(parent_fd)


def _remove_named_tree(
    parent_fd: int,
    name: str,
    target: Path,
    *,
    missing_ok: bool,
    expected_root_identity: tuple[int, int] | None,
) -> None:
    try:
        entry_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok and expected_root_identity is None:
            return
        if expected_root_identity is not None:
            raise SafeFilesystemError(
                f"Expected tree root {target} is absent", kind="identity_changed"
            )
        raise
    named = (entry_stat.st_dev, entry_stat.st_ino)
    if expected_root_identity is not None and (
        not stat.S_ISDIR(entry_stat.st_mode) or named != expected_root_identity
    ):
        raise SafeFilesystemError(
            f"Tree root {target} is not the expected identity {expected_root_identity}",
            kind="identity_changed",
        )
    if not stat.S_ISDIR(entry_stat.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    child_fd = _open_child_dir(parent_fd, name, target)
    try:
        opened = os.fstat(child_fd)
        if (
            expected_root_identity is not None
            and (
                opened.st_dev,
                opened.st_ino,
            )
            != expected_root_identity
        ):
            raise SafeFilesystemError(
                f"Opened tree root {target} is not the expected identity "
                f"{expected_root_identity}",
                kind="identity_changed",
            )
        _remove_tree_contents_allow_symlinks_fd(child_fd, target)
        if expected_root_identity is not None:
            opened_now = os.fstat(child_fd)
            if (opened_now.st_dev, opened_now.st_ino) != expected_root_identity:
                raise SafeFilesystemError(
                    f"Opened tree root {target} changed before final rmdir",
                    kind="identity_changed",
                )
            try:
                named_now = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError as orig:
                raise SafeFilesystemError(
                    f"Tree root {target} disappeared before final rmdir",
                    kind="identity_changed",
                ) from orig
            if (named_now.st_dev, named_now.st_ino) != expected_root_identity:
                raise SafeFilesystemError(
                    f"Tree root {target} changed before final rmdir",
                    kind="identity_changed",
                )
    finally:
        os.close(child_fd)
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError as error:
        if expected_root_identity is None:
            raise
        try:
            named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as orig:
            raise SafeFilesystemError(
                f"Tree root {target} disappeared during final rmdir",
                kind="identity_changed",
            ) from orig
        if (named_after.st_dev, named_after.st_ino) != expected_root_identity:
            raise SafeFilesystemError(
                f"Tree root {target} changed during final rmdir",
                kind="identity_changed",
            ) from error
        raise
