"""Private 14-day retention step for `run_sources` workers (issue #372).

Wires the existing `cleanup.plan_retention` / `execute_retention_plan` seam into
the dual-source worker and removes `output/<T>/` once its last source is gone
(compute-loop §12「14 天窗口的执行时机」). Not a public seam; no `__all__`.
"""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path

from yd_producer.store import safe_fs

_RETENTION_PREFIX = "retention cleanup:"
#: `output/<T>/` 仍被兄弟源占用，或已不存在：静默保留。
_RMDIR_SKIP_ERRNOS = frozenset({errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT})


def _rmdir_empty_cycle_dirs(yd_root: Path, output_dirs: tuple[Path, ...]) -> list[Path]:
    """对被删 `output/<T>/<source>` 的父目录做 no-follow `rmdir`；返回成功删除的目录。"""
    cycle_dirs = sorted({directory.parent for directory in output_dirs})
    if not cycle_dirs:
        return []
    output_root = yd_root / "output"
    removed: list[Path] = []
    fd = safe_fs.open_directory_no_follow(output_root, containment_root=yd_root)
    try:
        for cycle_dir in cycle_dirs:
            try:
                os.rmdir(cycle_dir.name, dir_fd=fd)
            except OSError as error:
                if error.errno in _RMDIR_SKIP_ERRNOS:
                    continue
                raise
            removed.append(cycle_dir)
    finally:
        os.close(fd)
    return removed


def _audit_text(
    output_dirs: tuple[Path, ...],
    log_files: tuple[Path, ...],
    cycle_dirs: list[Path],
) -> str:
    paths = (*output_dirs, *log_files, *cycle_dirs)
    if not paths:
        return ""
    return (
        f"{_RETENTION_PREFIX} 删除 {len(output_dirs)} 个 output 目录、"
        f"{len(log_files)} 个失败日志、{len(cycle_dirs)} 个空 cycle 目录："
        + ", ".join(str(path) for path in paths)
    )


def run_retention(yd_root: Path, source: str, *, publish_lock: threading.Lock) -> str:
    """持发布锁执行本源 plan → execute → rmdir 空 cycle 目录；返回审计文本（无删除为空）。

    失败一律转为本源 `RunError(phase="cleanup")`，原异常为 cause；已删条目不回滚。
    """
    from yd_producer import cleanup
    from yd_producer._controller_sources import _cleanup_run_error

    try:
        with publish_lock:
            plan = cleanup.plan_retention(yd_root, source)
            cleanup.execute_retention_plan(plan)
            cycle_dirs = _rmdir_empty_cycle_dirs(plan.yd_root, plan.output_dirs)
    except (cleanup.CleanupError, safe_fs.SafeFilesystemError, OSError) as orig:
        raise _cleanup_run_error(
            f"{source}: retention 失败：{orig}",
            source=source,
            cycle=None,
            orig=orig,
        ) from orig
    return _audit_text(plan.output_dirs, plan.log_files, cycle_dirs)
