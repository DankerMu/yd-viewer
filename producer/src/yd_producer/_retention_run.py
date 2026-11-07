"""Private 14-day retention step for `run_sources` workers (issue #372).

Wires the existing `cleanup.plan_retention` / `execute_retention_plan` seam into
the dual-source worker and removes empty `output/<T>/` directories older than the
source's cutoff (compute-loop §12「14 天窗口的执行时机」). Not a public seam; no
`__all__`.
"""

from __future__ import annotations

import errno
import os
import threading
from datetime import datetime
from pathlib import Path

from yd_producer.store import safe_fs

_RETENTION_PREFIX = "retention cleanup:"
#: `output/<T>/` 仍被兄弟源占用，或已不存在：静默保留。
_RMDIR_SKIP_ERRNOS = frozenset({errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT})


def _sweep_empty_cycle_dirs(
    yd_root: Path, cutoff: datetime
) -> tuple[list[Path], BaseException | None]:
    """枚举 `output/` 下早于 cutoff 的 cycle 名，逐个 no-follow `rmdir`。

    返回 (成功删除的目录, 第一个非静默错误)。遇错不中断：其余候选照试，
    中断或部分失败遗留的空目录由下一 tick 收掉。
    """
    from yd_producer.controller import parse_cycle_id

    output_root = yd_root / "output"
    try:
        fd = safe_fs.open_directory_no_follow(output_root, containment_root=yd_root)
    except (safe_fs.SafeFilesystemError, OSError) as error:
        return [], error
    removed: list[Path] = []
    first: BaseException | None = None
    try:
        try:
            names = sorted(os.listdir(fd))
        except OSError as error:
            return [], error
        for name in names:
            cycle = parse_cycle_id(name)
            if cycle is None or cycle >= cutoff:
                continue
            try:
                os.rmdir(name, dir_fd=fd)
            except OSError as error:
                if error.errno not in _RMDIR_SKIP_ERRNOS and first is None:
                    first = error
                continue
            removed.append(output_root / name)
    finally:
        os.close(fd)
    return removed, first


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
    execute 已完成而 rmdir 失败时，已删审计作为 note 挂到该错误上。
    """
    from yd_producer import cleanup
    from yd_producer._controller_sources import _cleanup_run_error

    def mapped(orig: BaseException):
        return _cleanup_run_error(
            f"{source}: retention 失败：{orig}", source=source, cycle=None, orig=orig
        )

    cycle_dirs: list[Path] = []
    failure: BaseException | None = None
    try:
        with publish_lock:
            plan = cleanup.plan_retention(yd_root, source)
            cleanup.execute_retention_plan(plan)
            if plan.cutoff is not None:
                cycle_dirs, failure = _sweep_empty_cycle_dirs(plan.yd_root, plan.cutoff)
    except (cleanup.CleanupError, safe_fs.SafeFilesystemError, OSError) as orig:
        raise mapped(orig) from orig
    audit = _audit_text(plan.output_dirs, plan.log_files, cycle_dirs)
    if failure is not None:
        error = mapped(failure)
        if audit:
            error.add_note(audit)
        raise error from failure
    return audit
