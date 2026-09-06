"""Private exact-work ownership: exclusive claim and frozen identity token.

This module is not a public seam. Controller staging, scratch reads, failure
cleanup, and successful publish consume one frozen `WorkClaim`. Pathname shape,
an earlier absence check, or post-hoc `resolve()` equality do not confer
ownership.
"""

from __future__ import annotations

import os
import stat as stat_module
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from yd_producer.store import safe_fs

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW | _CLOEXEC
_FILE_READ_FLAGS = os.O_RDONLY | _NOFOLLOW | _CLOEXEC | getattr(os, "O_NONBLOCK", 0)
_FILE_EXCL_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC


@dataclass(frozen=True, kw_only=True)
class WorkClaim:
    """Frozen ownership of one exclusive `work/<source>/<T>` exact root."""

    work_root: Path
    work_dir: Path
    identity: tuple[int, int]


class ClaimLostError(RuntimeError):
    """The named exact root no longer matches the frozen claim identity."""

    def __init__(self, message: str, *, path: Path) -> None:
        super().__init__(message)
        self.path = path


def _is_plain_dir(info: os.stat_result) -> bool:
    return stat_module.S_ISDIR(info.st_mode) and not stat_module.S_ISLNK(info.st_mode)


def ensure_shared_parents(work_root: Path, source: str) -> Path:
    """Create `work/` and `work/<source>/` without a raw rollback ledger.

    Existing symlink / non-directory components are refused. The returned
    source parent is the configured `work_root / source` path.
    """
    source_parent = work_root / source
    safe_fs.ensure_directory_no_follow(work_root)
    safe_fs.ensure_directory_no_follow(source_parent, containment_root=work_root)
    for component in (work_root, source_parent):
        info = os.lstat(component)
        if not _is_plain_dir(info):
            raise ClaimLostError(
                f"work 路径分量 {component} 不是原样普通目录"
                f"（st_mode={info.st_mode:#o}）",
                path=component,
            )
    return source_parent


def _raw_error(message: str, *, source: str, cycle: datetime, orig=None):
    from yd_producer.controller import RunError

    error = RunError(message, phase="raw", source=source, cycle=cycle)
    if orig is None:
        raise error
    raise error from orig


def require_plain_work_parent(
    *, work_root: Path, work_dir: Path, source: str, cycle: datetime
) -> None:
    """Refuse symlink/non-dir work parents before exclusive claim."""
    expected_parent = work_root / source
    if work_dir.parent != expected_parent:
        _raw_error(
            f"work 目录父路径必须逐字是 {expected_parent}，实得 {work_dir.parent}",
            source=source,
            cycle=cycle,
        )
    if Path(work_dir.parent).resolve() != expected_parent:
        _raw_error(
            f"work 父路径 {work_dir.parent} 存在符号链接/别名/`..`（归一为 "
            f"{Path(work_dir.parent).resolve()}）；staging 前拒绝，禁止把本轮写入指向别处",
            source=source,
            cycle=cycle,
        )
    for component in (work_root, expected_parent):
        if not os.path.lexists(component):
            continue
        info = os.lstat(component)
        if not _is_plain_dir(info):
            _raw_error(
                f"work 路径分量 {component} 不是原样普通目录"
                f"（st_mode={info.st_mode:#o}）；staging 前拒绝",
                source=source,
                cycle=cycle,
            )


def reject_preexisting_work(work_dir: Path, source: str, cycle: datetime) -> None:
    """Readable preexisting exact-root guard. Ownership still requires claim."""
    if os.path.lexists(work_dir):
        _raw_error(
            f"终名 work 已存在（任何形态），拒绝运行：{work_dir}；"
            "work 是一次性隔离单元，不续跑/不覆盖/不采纳",
            source=source,
            cycle=cycle,
        )


def claim_exact_work(
    *,
    work_root: Path,
    source: str,
    cycle: datetime,
    cycle_name: str,
) -> WorkClaim:
    """Exclusively create `work/<source>/<cycle>` relative to a verified parent fd.

    Any preexisting entry (dir/file/symlink/dangling) or competing mkdir is a
    claim failure. The winner's `(st_dev, st_ino)` is frozen as the token.
    """
    try:
        source_parent = ensure_shared_parents(work_root, source)
    except (ClaimLostError, safe_fs.SafeFilesystemError, OSError) as orig:
        _raw_error(
            f"无法准备 work 共享祖先 {work_root / source}：{orig}",
            source=source,
            cycle=cycle,
            orig=orig,
        )
    work_dir = source_parent / cycle_name
    parent_fd: int | None = None
    child_fd: int | None = None
    try:
        parent_fd = safe_fs.open_directory_no_follow(
            source_parent, containment_root=work_root
        )
        try:
            os.mkdir(cycle_name, 0o755, dir_fd=parent_fd)
        except FileExistsError as orig:
            _raw_error(
                f"终名 work 已被竞争者占有，拒绝认领：{work_dir}；"
                "work 是一次性隔离单元，不续跑/不覆盖/不采纳",
                source=source,
                cycle=cycle,
                orig=orig,
            )
        except OSError as orig:
            _raw_error(
                f"无法排他创建精确 work {work_dir}：{orig}",
                source=source,
                cycle=cycle,
                orig=orig,
            )
        try:
            child_fd = os.open(cycle_name, _DIR_FLAGS, dir_fd=parent_fd)
            info = os.fstat(child_fd)
        except OSError as orig:
            _raw_error(
                f"精确 work {work_dir} 创建后无法打开：{orig}",
                source=source,
                cycle=cycle,
                orig=orig,
            )
        if not stat_module.S_ISDIR(info.st_mode):
            _raw_error(
                f"精确 work {work_dir} 创建后不是普通目录",
                source=source,
                cycle=cycle,
            )
        return WorkClaim(
            work_root=work_root,
            work_dir=work_dir,
            identity=(info.st_dev, info.st_ino),
        )
    finally:
        if child_fd is not None:
            os.close(child_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def current_identity(path: Path) -> tuple[int, int]:
    """No-follow `(st_dev, st_ino)` of the named leaf, without re-authorizing parents."""
    info = os.lstat(path)
    if not _is_plain_dir(info):
        raise ClaimLostError(
            f"精确 work {path} 当前不是普通目录（st_mode={info.st_mode:#o}）",
            path=path,
        )
    return info.st_dev, info.st_ino


def validate_claim(claim: WorkClaim, *, path: Path | None = None) -> WorkClaim:
    """Require the named exact root still to be the claimed inode."""
    named = claim.work_dir if path is None else path
    if named != claim.work_dir:
        raise ClaimLostError(
            f"操作路径 {named} 不是本 attempt 认领的精确 work {claim.work_dir}",
            path=named,
        )
    try:
        identity = current_identity(named)
    except ClaimLostError:
        raise
    except OSError as orig:
        raise ClaimLostError(
            f"精确 work {named} 的当前 identity 无法判定（{orig}）",
            path=named,
        ) from orig
    if identity != claim.identity:
        raise ClaimLostError(
            f"精确 work {named} 已不再是本 attempt 认领的 inode "
            f"{claim.identity}，当前为 {identity}",
            path=named,
        )
    return claim


def require_controller_claim(claim: WorkClaim | None, *, path: Path) -> WorkClaim:
    """Controller-owned IO requires a non-None claim at the named exact root."""
    if claim is None:
        raise ClaimLostError(
            f"controller 路径必须携带非空 WorkClaim，才能操作 {path}",
            path=path,
        )
    return validate_claim(claim, path=path)


def _relative_parts(claim: WorkClaim, path: Path) -> tuple[str, ...]:
    try:
        rel = path.relative_to(claim.work_dir)
    except ValueError as orig:
        raise ClaimLostError(
            f"路径 {path} 不在本 attempt 认领的精确 work {claim.work_dir} 内",
            path=path,
        ) from orig
    parts = rel.parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ClaimLostError(f"目标路径分量非法：{path}", path=path)
    return parts


def _close_walk(fd: int, root_fd: int) -> None:
    if fd != root_fd:
        os.close(fd)


def _close_owned(fd: int | None) -> None:
    if fd is not None:
        os.close(fd)


def open_claimed_root(claim: WorkClaim) -> int:
    """Open the claimed exact root no-follow; fstat identity while the fd stays open."""
    fd = os.open(claim.work_dir, _DIR_FLAGS)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != claim.identity or not _is_plain_dir(info):
            raise ClaimLostError(
                f"精确 work {claim.work_dir} 打开后 identity 已漂移"
                f"（期望 {claim.identity}，实得 {(info.st_dev, info.st_ino)}）",
                path=claim.work_dir,
            )
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_dir_child(parent_fd: int, name: str, *, path: Path) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError as orig:
        raise ClaimLostError(
            f"无法无跟随打开 {path} 的目录分量 {name!r}：{orig}",
            path=path,
        ) from orig


def _walk_to_parent(root_fd: int, parts: tuple[str, ...], *, path: Path) -> int:
    """Walk descendants; on failure, close only helper-owned descendant fds."""
    fd = root_fd
    for name in parts[:-1]:
        try:
            next_fd = _open_dir_child(fd, name, path=path)
        except BaseException:
            _close_walk(fd, root_fd)
            raise
        previous = fd
        fd = next_fd
        try:
            _close_walk(previous, root_fd)
        except BaseException:
            _close_walk(fd, root_fd)
            raise
    return fd


def stat_claimed(claim: WorkClaim, path: Path) -> os.stat_result:
    """No-follow lstat of `path` relative to an identity-matched claimed-root fd."""
    parts = _relative_parts(claim, path)
    root_fd = open_claimed_root(claim)
    try:
        if not parts:
            return os.fstat(root_fd)
        parent_fd = _walk_to_parent(root_fd, parts, path=path)
        try:
            info = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        finally:
            _close_walk(parent_fd, root_fd)
        if stat_module.S_ISLNK(info.st_mode):
            raise ClaimLostError(f"{path} 不得是 symlink", path=path)
        return info
    finally:
        os.close(root_fd)


def lexists_claimed(claim: WorkClaim, path: Path) -> bool:
    """Any named entry under the claimed root, including symlink/file/dir."""
    parts = _relative_parts(claim, path)
    if not parts:
        return True
    root_fd = open_claimed_root(claim)
    parent_fd: int | None = None
    try:
        try:
            parent_fd = _walk_to_parent(root_fd, parts, path=path)
        except FileNotFoundError:
            return False
        try:
            os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
    finally:
        if parent_fd is not None:
            _close_walk(parent_fd, root_fd)
        os.close(root_fd)


def open_claimed_file(claim: WorkClaim, path: Path) -> int:
    """Open a regular claimed file; returned fd belongs to the caller."""
    parts = _relative_parts(claim, path)
    if not parts:
        raise ClaimLostError(f"精确 work 根 {path} 不是普通文件", path=path)
    root_fd: int | None = open_claimed_root(claim)
    parent_fd: int | None = None
    file_fd: int | None = None
    try:
        parent_fd = _walk_to_parent(root_fd, parts, path=path)
        named = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        if not stat_module.S_ISREG(named.st_mode):
            raise ClaimLostError(
                f"{path} 不是普通文件（st_mode={named.st_mode:#o}）",
                path=path,
            )
        file_fd = os.open(parts[-1], _FILE_READ_FLAGS, dir_fd=parent_fd)
        opened = os.fstat(file_fd)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise ClaimLostError(f"{path} 在打开窗口内 identity 已漂移", path=path)
        if not stat_module.S_ISREG(opened.st_mode):
            raise ClaimLostError(f"{path} 打开后不是普通文件", path=path)
        close_parent = parent_fd
        parent_fd = None
        _close_walk(close_parent, root_fd)
        close_root = root_fd
        root_fd = None
        os.close(close_root)
        result = file_fd
        file_fd = None
        return result
    finally:
        _close_owned(file_fd)
        if parent_fd is not None and root_fd is not None:
            _close_walk(parent_fd, root_fd)
        _close_owned(root_fd)


def read_claimed_bytes(
    claim: WorkClaim, path: Path, *, max_bytes: int | None = None
) -> bytes:
    """Read a regular claimed file; `max_bytes` keeps one sentinel byte like safe_fs."""
    fd = open_claimed_file(claim, path)
    try:
        if max_bytes is None:
            chunks: list[bytes] = []
            while chunk := os.read(fd, 1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        content = bytearray()
        limit = max_bytes + 1
        while len(content) < limit:
            chunk = os.read(fd, limit - len(content))
            if not chunk:
                break
            content.extend(chunk)
        return bytes(content)
    except OSError as orig:
        raise ClaimLostError(f"无法读取 {path}：{orig}", path=path) from orig
    finally:
        os.close(fd)


def open_claimed_excl(claim: WorkClaim, path: Path, *, mode: int = 0o644) -> int:
    """O_EXCL create a claimed file; returned fd belongs to the caller."""
    parts = _relative_parts(claim, path)
    if not parts:
        raise ClaimLostError(f"不能把精确 work 根 {path} 当成文件创建", path=path)
    root_fd: int | None = open_claimed_root(claim)
    parent_fd: int | None = None
    file_fd: int | None = None
    try:
        parent_fd = _walk_to_parent(root_fd, parts, path=path)
        file_fd = os.open(parts[-1], _FILE_EXCL_FLAGS, mode, dir_fd=parent_fd)
        close_parent = parent_fd
        parent_fd = None
        _close_walk(close_parent, root_fd)
        close_root = root_fd
        root_fd = None
        os.close(close_root)
        result = file_fd
        file_fd = None
        return result
    finally:
        _close_owned(file_fd)
        if parent_fd is not None and root_fd is not None:
            _close_walk(parent_fd, root_fd)
        _close_owned(root_fd)


def unlink_claimed(claim: WorkClaim, path: Path) -> None:
    """Unlink a claimed descendant relative to the identity-matched root fd."""
    parts = _relative_parts(claim, path)
    if not parts:
        raise ClaimLostError(f"不能 unlink 精确 work 根 {path}", path=path)
    root_fd = open_claimed_root(claim)
    try:
        parent_fd = _walk_to_parent(root_fd, parts, path=path)
        try:
            os.unlink(parts[-1], dir_fd=parent_fd)
        finally:
            _close_walk(parent_fd, root_fd)
    finally:
        os.close(root_fd)


def rmdir_claimed(claim: WorkClaim, path: Path) -> None:
    """Remove an empty claimed descendant directory via the identity-matched root fd."""
    parts = _relative_parts(claim, path)
    if not parts:
        raise ClaimLostError(f"不能 rmdir 精确 work 根 {path}", path=path)
    root_fd = open_claimed_root(claim)
    try:
        parent_fd = _walk_to_parent(root_fd, parts, path=path)
        try:
            os.rmdir(parts[-1], dir_fd=parent_fd)
        finally:
            _close_walk(parent_fd, root_fd)
    finally:
        os.close(root_fd)


def mkdir_relative_to_claim(claim: WorkClaim, directory: Path) -> list[Path]:
    """Create `directory` under the claimed exact-root fd. Return missing descendants.

    Shared ancestors above the claimed root are never created or returned.
    """
    parts = _relative_parts(claim, directory)
    missing: list[Path] = []
    probe = directory
    while probe != claim.work_dir:
        if os.path.lexists(probe):
            break
        missing.append(probe)
        probe = probe.parent
    root_fd = open_claimed_root(claim)
    fd = root_fd
    try:
        for part in parts:
            try:
                next_fd = _open_dir_child(fd, part, path=directory)
            except FileNotFoundError:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
                except OSError as orig:
                    raise ClaimLostError(
                        f"无法创建目标目录 {directory}：{orig}", path=directory
                    ) from orig
                next_fd = _open_dir_child(fd, part, path=directory)
            previous = fd
            fd = next_fd
            try:
                _close_walk(previous, root_fd)
            except BaseException:
                _close_walk(fd, root_fd)
                fd = root_fd
                raise
    finally:
        _close_walk(fd, root_fd)
        os.close(root_fd)
    return missing


def delete_claimed_tree(claim: WorkClaim, *, containment_root: Path) -> None:
    """Delete the claimed exact root only if named and opened identity still match."""
    validate_claim(claim)
    safe_fs.remove_tree_allow_symlinks(
        claim.work_dir.parent,
        claim.work_dir.name,
        containment_root=containment_root,
        missing_ok=False,
        expected_root_identity=claim.identity,
    )


def release_empty_claimed_root(claim: WorkClaim) -> None:
    """Rmdir only the empty exact claimed root; never descend or remove parents."""
    parent_fd = safe_fs.open_directory_no_follow(
        claim.work_dir.parent, containment_root=claim.work_root
    )
    try:
        name = claim.work_dir.name
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != claim.identity or not _is_plain_dir(named):
            raise ClaimLostError(
                f"精确 work {claim.work_dir} 在释放前 identity 已漂移",
                path=claim.work_dir,
            )
        child_fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
        try:
            opened = os.fstat(child_fd)
            if (opened.st_dev, opened.st_ino) != claim.identity:
                raise ClaimLostError(
                    f"精确 work {claim.work_dir} 打开后 identity 已漂移",
                    path=claim.work_dir,
                )
            if os.listdir(child_fd):
                raise ClaimLostError(
                    f"精确 work {claim.work_dir} 非空，拒绝递归删除",
                    path=claim.work_dir,
                )
        finally:
            os.close(child_fd)
        named_now = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (named_now.st_dev, named_now.st_ino) != claim.identity:
            raise ClaimLostError(
                f"精确 work {claim.work_dir} 在 rmdir 前 identity 已漂移",
                path=claim.work_dir,
            )
        try:
            os.rmdir(name, dir_fd=parent_fd)
        except OSError as orig:
            try:
                after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                raise ClaimLostError(
                    f"精确 work {claim.work_dir} 在 rmdir 中消失",
                    path=claim.work_dir,
                ) from orig
            if (after.st_dev, after.st_ino) != claim.identity:
                raise ClaimLostError(
                    f"精确 work {claim.work_dir} 在 rmdir 中 identity 已漂移",
                    path=claim.work_dir,
                ) from orig
            raise
    finally:
        os.close(parent_fd)


def release_raw_claim_after_stage_failure(
    claim: WorkClaim, error: BaseException
) -> None:
    """Best-effort exact-root release that preserves the rawcopy exception/cause."""
    try:
        release_empty_claimed_root(claim)
    except Exception as cleanup_error:  # noqa: BLE001 - preserve original BaseException
        error.add_note(f"claimed exact-root cleanup failed: {cleanup_error}")
