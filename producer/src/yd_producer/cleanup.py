"""失败收尾与 14 天保留清理（任务 13.2/13.3，issue #25）。

契约来源：`docs/compute-loop-design.md` §11.3、§12，
`openspec/changes/m2-producer-core/specs/run-controller/spec.md` 的
「失败处理」与「保留窗口与安全清理」两条 Requirement。

本模块是独立 seam：不改 `controller` / `residue` / `publish` / `executor` /
`slurm` / `cli`，主循环接线归 #26/#28。公开失败一律 `CleanupError`，底层
`ValueError` / `DiscoveryUnreadableError` / `SafeFilesystemError` / `OSError`
不得穿透；`MemoryError` / `KeyboardInterrupt` / `SystemExit` 不包。

失败收尾顺序固定为「先原子提交唯一失败日志，成功后才删除当前精确 work」。
保留清理以该源最新普通文件 `DONE` 锚定 cutoff = D-14d，只删严格早于 cutoff
且经 realpath 确认位于已解析 `YD_ROOT` 内的本源对象。两条路径都不推进状态链。
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from yd_producer.controller import (
    DiscoveryUnreadableError,
    cycle_id,
    done_cycles,
    parse_cycle_id,
)
from yd_producer.executor import JobRecord, JobSpec, JobState
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    ensure_directory_no_follow,
    open_directory_no_follow,
    open_file_no_follow,
    remove_tree_allow_symlinks,
    rmtree_no_follow,
    stat_no_follow,
    unlink_no_follow,
    verify_tree_no_symlinks,
)

__all__ = [
    "CleanupError",
    "FailureInputs",
    "FailureResult",
    "RetentionPlan",
    "execute_retention_plan",
    "finalize_failed_job",
    "plan_retention",
]

CleanupPhase = Literal["validate", "log", "work", "retention-plan", "retention-execute"]

_FAILURE_LOG_SCHEMA = "yd-failure-log-v1"
_STDOUT_STDERR_SEPARATOR = b"--- stdout/stderr ---\n"
_LOG_COPY_CHUNK_BYTES = 64 * 1024
_RETENTION_WINDOW = timedelta(days=14)
_DONE_NAME = "DONE"
_TEMP_FILE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_TERMINAL_FAILURE_STATES = frozenset({JobState.FAILED, JobState.TIMEOUT})
_WRAP_ERRORS = (
    ValueError,
    DiscoveryUnreadableError,
    SafeFilesystemError,
    OSError,
)


class CleanupError(RuntimeError):
    """公开边界上的唯一失败类型。

    `phase` 区分入口校验、失败日志提交、work 删除、retention 计划与 retention 执行。
    `path` 在涉事时指向具体路径，否则为 `None`。
    """

    def __init__(
        self,
        message: str,
        *,
        phase: CleanupPhase,
        path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.path = path


def _require_source_component(source: str) -> None:
    """与 residue/publish 同构的单分量闸：任何路径构造之前 fail closed。"""

    separators = {"/", os.sep}
    if os.altsep:
        separators.add(os.altsep)
    if (
        not source
        or source in {".", ".."}
        or any(sep in source for sep in separators)
        or "\x00" in source
        or source != Path(source).name
    ):
        raise ValueError(
            "source 必须是单个非空路径分量"
            "（不得为 ''、'.'、'..'、含 '/' 或 NUL），"
            f"实得 {source!r}"
        )


def _normalize_cycle(cycle: datetime) -> datetime:
    normalized = (
        cycle.replace(tzinfo=UTC) if cycle.tzinfo is None else cycle.astimezone(UTC)
    )
    parsed = parse_cycle_id(cycle_id(normalized))
    if parsed != normalized:
        raise ValueError(f"cycle 必须可经 cycle_id/parse_cycle_id 往返，实得 {cycle!r}")
    return normalized


def _resolved_leaf(path: Path) -> Path:
    target = Path(path)
    return target.parent.resolve() / target.name


def _wrap_validate(error: BaseException, *, path: Path | None = None) -> CleanupError:
    return CleanupError(str(error), phase="validate", path=path)


def _require_regular_merged_log(path: Path, work_root: Path) -> None:
    try:
        info = stat_no_follow(path, containment_root=work_root)
    except FileNotFoundError as error:
        raise CleanupError(
            f"作业合并日志 {path} 不存在",
            phase="log",
            path=path,
        ) from error
    except (SafeFilesystemError, OSError) as error:
        raise CleanupError(
            f"作业合并日志 {path} 不可用（{error}）",
            phase="log",
            path=path,
        ) from error
    if not stat.S_ISREG(info.st_mode):
        raise CleanupError(
            f"作业合并日志必须是普通文件：{path}（st_mode={info.st_mode:#o}）",
            phase="log",
            path=path,
        )


@dataclass(frozen=True, kw_only=True)
class FailureInputs:
    """一轮失败收尾的全部输入。入口在写入之前 fail closed。"""

    yd_root: Path
    work_root: Path
    source: str
    cycle: datetime
    job_spec: JobSpec
    job_record: JobRecord
    exit_code: str

    root: Path = field(init=False)
    resolved_work_root: Path = field(init=False)
    exact_work_dir: Path = field(init=False)
    merged_log: Path = field(init=False)

    def __post_init__(self) -> None:
        try:
            _require_source_component(self.source)
            cycle = _normalize_cycle(self.cycle)
            object.__setattr__(self, "cycle", cycle)
            if not self.exit_code:
                raise ValueError("exit_code 必须非空")
            if self.job_record.name != self.job_spec.name:
                raise ValueError(
                    "job_record.name 必须等于 job_spec.name，"
                    f"实得 record={self.job_record.name!r} spec={self.job_spec.name!r}"
                )
            if self.job_record.state not in _TERMINAL_FAILURE_STATES:
                raise ValueError(
                    "失败收尾只接受 FAILED 或 TIMEOUT，"
                    f"实得 {self.job_record.state.value}"
                )
            root = Path(self.yd_root).resolve()
            work_root = Path(self.work_root).resolve()
            object.__setattr__(self, "root", root)
            object.__setattr__(self, "resolved_work_root", work_root)
            exact = _resolved_leaf(self.job_spec.work_dir)
            expected = work_root / self.source / cycle_id(cycle)
            if exact != expected:
                raise CleanupError(
                    f"job_spec.work_dir 必须恰好等于 {expected}，实得 {exact}",
                    phase="validate",
                    path=exact,
                )
            object.__setattr__(self, "exact_work_dir", exact)
            merged = _resolved_leaf(self.job_spec.log_path)
            try:
                merged.relative_to(exact)
            except ValueError as error:
                raise CleanupError(
                    f"job_spec.log_path 必须位于 {exact} 内，实得 {merged}",
                    phase="validate",
                    path=merged,
                ) from error
            if merged == exact:
                raise CleanupError(
                    f"job_spec.log_path 不得就是 work 目录本身：{merged}",
                    phase="validate",
                    path=merged,
                )
            object.__setattr__(self, "merged_log", merged)
            _require_regular_merged_log(merged, work_root)
        except CleanupError:
            raise
        except _WRAP_ERRORS as error:
            raise _wrap_validate(error) from error


@dataclass(frozen=True)
class FailureResult:
    """失败收尾交回的终名：唯一失败日志与已删除的精确 work。"""

    source: str
    cycle: datetime
    log_path: Path
    removed_work_dir: Path
    job_id: str


def _require_representable_cycle(value: datetime, *, label: str) -> datetime:
    try:
        normalized = _normalize_cycle(value)
    except ValueError as error:
        raise CleanupError(
            f"{label} 必须是可由 parse_cycle_id 认回的 UTC 整点 cycle，实得 {value!r}",
            phase="validate",
            path=None,
        ) from error
    return normalized


def _require_sorted_unique(paths: tuple[Path, ...], *, label: str) -> None:
    if len(set(paths)) != len(paths):
        raise CleanupError(
            f"{label} 不得含重复路径",
            phase="validate",
            path=None,
        )
    if tuple(sorted(paths)) != paths:
        raise CleanupError(
            f"{label} 必须按路径排序且无重复",
            phase="validate",
            path=None,
        )


def _require_output_identity(
    path: Path, root: Path, source: str, cutoff: datetime
) -> None:
    expected_parent = root / "output"
    if path.parent.parent != expected_parent or path.name != source:
        raise CleanupError(
            f"output 删除目标必须词法精确等于 {expected_parent}/<cycle>/{source}，实得 {path}",
            phase="validate",
            path=path,
        )
    cycle = parse_cycle_id(path.parent.name)
    if cycle is None:
        raise CleanupError(
            f"output 删除目标的 cycle 名不可解析：{path}",
            phase="validate",
            path=path,
        )
    if path != expected_parent / path.parent.name / source:
        raise CleanupError(
            f"output 删除目标必须词法精确等于 {expected_parent}/{path.parent.name}/{source}，实得 {path}",
            phase="validate",
            path=path,
        )
    if cycle >= cutoff:
        raise CleanupError(
            f"output 删除目标 cycle 必须严格早于 cutoff {cycle_id(cutoff)}：{path}",
            phase="validate",
            path=path,
        )


def _require_log_identity(
    path: Path, root: Path, source: str, cutoff: datetime
) -> None:
    expected_parent = root / "logs" / source
    if path.parent != expected_parent or not path.name.endswith(".log"):
        raise CleanupError(
            f"失败日志删除目标必须词法精确等于 {expected_parent}/<cycle>.log，实得 {path}",
            phase="validate",
            path=path,
        )
    cycle_name = path.name[: -len(".log")]
    cycle = parse_cycle_id(cycle_name)
    if cycle is None:
        raise CleanupError(
            f"失败日志删除目标的 cycle 名不可解析：{path}",
            phase="validate",
            path=path,
        )
    if path != expected_parent / f"{cycle_name}.log":
        raise CleanupError(
            f"失败日志删除目标必须词法精确等于 {expected_parent}/{cycle_name}.log，实得 {path}",
            phase="validate",
            path=path,
        )
    if cycle >= cutoff:
        raise CleanupError(
            f"失败日志删除目标 cycle 必须严格早于 cutoff {cycle_id(cutoff)}：{path}",
            phase="validate",
            path=path,
        )


def _require_current_retention_anchor(
    root: Path, source: str, latest: datetime
) -> None:
    """拒绝晚于当前该源普通文件 DONE 集合的公开 plan 锚点。"""

    output_root = root / "output"
    try:
        current_done = done_cycles(output_root, source)
    except (DiscoveryUnreadableError, OSError, ValueError) as error:
        raise CleanupError(
            f"{source}: 当前 DONE 锚点无法确定（{error}）",
            phase="validate",
            path=None,
        ) from error
    if not current_done:
        raise CleanupError(
            f"{source}: 当前没有可用的普通文件 DONE 锚点",
            phase="validate",
            path=None,
        )
    current_latest = max(current_done)
    try:
        _precheck_anchor(output_root, source, current_latest, root)
    except CleanupError as error:
        raise CleanupError(
            f"{source}: 当前 DONE 锚点不安全（{error}）",
            phase="validate",
            path=None,
        ) from error
    if latest > current_latest:
        raise CleanupError(
            f"latest_done {cycle_id(latest)} 晚于当前 DONE {cycle_id(current_latest)}",
            phase="validate",
            path=None,
        )


def _bind_retention_plan(plan: RetentionPlan) -> None:
    """把公开 RetentionPlan 的每一个字段绑定到 (root, source, cutoff) 身份。"""

    try:
        _require_source_component(plan.source)
    except ValueError as error:
        raise CleanupError(str(error), phase="validate", path=None) from error
    try:
        resolved = Path(plan.yd_root).resolve()
    except (OSError, ValueError) as error:
        raise CleanupError(
            f"yd_root 无法 resolve：{error}",
            phase="validate",
            path=Path(plan.yd_root),
        ) from error
    object.__setattr__(plan, "yd_root", resolved)

    latest = plan.latest_done
    cutoff = plan.cutoff
    if (latest is None) != (cutoff is None):
        raise CleanupError(
            "latest_done 与 cutoff 必须同时为 None 或同时为 UTC 整点 cycle",
            phase="validate",
            path=None,
        )
    if latest is None:
        if plan.output_dirs or plan.log_files:
            raise CleanupError(
                "空窗口的 RetentionPlan 不得带删除目标",
                phase="validate",
                path=(plan.output_dirs or plan.log_files)[0],
            )
        _require_sorted_unique(plan.output_dirs, label="output_dirs")
        _require_sorted_unique(plan.log_files, label="log_files")
        return

    latest = _require_representable_cycle(latest, label="latest_done")
    cutoff = _require_representable_cycle(cutoff, label="cutoff")
    object.__setattr__(plan, "latest_done", latest)
    object.__setattr__(plan, "cutoff", cutoff)
    expected_cutoff = latest - _RETENTION_WINDOW
    if cutoff != expected_cutoff:
        raise CleanupError(
            f"cutoff 必须恰好等于 latest_done - 14 days（期望 {cycle_id(expected_cutoff)}，实得 {cycle_id(cutoff)}）",
            phase="validate",
            path=None,
        )
    for directory in plan.output_dirs:
        _require_output_identity(directory, resolved, plan.source, cutoff)
    for log_file in plan.log_files:
        _require_log_identity(log_file, resolved, plan.source, cutoff)
    _require_sorted_unique(plan.output_dirs, label="output_dirs")
    _require_sorted_unique(plan.log_files, label="log_files")
    _require_current_retention_anchor(resolved, plan.source, latest)


@dataclass(frozen=True, kw_only=True)
class RetentionPlan:
    """单源保留清理的不可变删除清单。判定零写入；执行只删这里列出的路径。"""

    yd_root: Path
    source: str
    latest_done: datetime | None
    cutoff: datetime | None
    output_dirs: tuple[Path, ...]
    log_files: tuple[Path, ...]

    def __post_init__(self) -> None:
        _bind_retention_plan(self)


def _failure_log_header(inputs: FailureInputs) -> bytes:
    record = inputs.job_record
    payload = {
        "command": list(inputs.job_spec.command),
        "cycle": cycle_id(inputs.cycle),
        "ended_at": None if record.ended_at is None else record.ended_at.isoformat(),
        "exit_code": inputs.exit_code,
        "job_id": record.job_id,
        "job_name": record.name,
        "schema": _FAILURE_LOG_SCHEMA,
        "source": inputs.source,
        "started_at": (
            None if record.started_at is None else record.started_at.isoformat()
        ),
        "state": record.state.value,
        "submitted_at": record.submitted_at.isoformat(),
    }
    line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return line.encode("utf-8") + b"\n" + _STDOUT_STDERR_SEPARATOR


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _close_fd(fd: int | None) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _unlink_temp(parent_fd: int | None, temp_name: str) -> None:
    if parent_fd is None:
        return
    try:
        os.unlink(temp_name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except OSError:
        return


def _reject_non_regular_log_target(parent_fd: int, log_path: Path) -> None:
    try:
        info = os.stat(log_path.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise CleanupError(
            f"失败日志目标 {log_path} 无法判定（{error}）",
            phase="log",
            path=log_path,
        ) from error
    if stat.S_ISLNK(info.st_mode):
        raise CleanupError(
            f"失败日志目标不得是 symlink：{log_path}",
            phase="log",
            path=log_path,
        )
    if not stat.S_ISREG(info.st_mode):
        raise CleanupError(
            f"失败日志目标必须是普通文件：{log_path}（st_mode={info.st_mode:#o}）",
            phase="log",
            path=log_path,
        )


def _commit_failure_log(inputs: FailureInputs) -> Path:
    log_path = inputs.root / "logs" / inputs.source / f"{cycle_id(inputs.cycle)}.log"
    parent = log_path.parent
    try:
        ensure_directory_no_follow(parent, containment_root=inputs.root)
    except (SafeFilesystemError, OSError) as error:
        raise CleanupError(
            f"无法创建失败日志目录 {parent}：{error}",
            phase="log",
            path=parent,
        ) from error

    header = _failure_log_header(inputs)
    temp_name = f".{log_path.name}.{uuid.uuid4().hex}.tmp"
    parent_fd: int | None = None
    dest_fd: int | None = None
    src_fd: int | None = None
    replaced = False
    try:
        parent_fd = open_directory_no_follow(parent, containment_root=inputs.root)
        _reject_non_regular_log_target(parent_fd, log_path)
        dest_fd = os.open(temp_name, _TEMP_FILE_FLAGS, 0o666, dir_fd=parent_fd)
        _write_all(dest_fd, header)
        src_fd = open_file_no_follow(
            inputs.merged_log, containment_root=inputs.resolved_work_root
        )
        while True:
            chunk = os.read(src_fd, _LOG_COPY_CHUNK_BYTES)
            if not chunk:
                break
            _write_all(dest_fd, chunk)
        os.fsync(dest_fd)
        os.close(dest_fd)
        dest_fd = None
        os.close(src_fd)
        src_fd = None
        os.replace(temp_name, log_path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        replaced = True
        try:
            os.fsync(parent_fd)
        except OSError:
            pass
    except CleanupError:
        if not replaced:
            _unlink_temp(parent_fd, temp_name)
        raise
    except (SafeFilesystemError, OSError, ValueError) as error:
        if not replaced:
            _unlink_temp(parent_fd, temp_name)
        raise CleanupError(
            f"失败日志提交失败：{error}",
            phase="log",
            path=log_path,
        ) from error
    finally:
        _close_fd(src_fd)
        _close_fd(dest_fd)
        _close_fd(parent_fd)
    return log_path


def _delete_work(inputs: FailureInputs) -> None:
    try:
        remove_tree_allow_symlinks(
            inputs.exact_work_dir.parent,
            inputs.exact_work_dir.name,
            containment_root=inputs.resolved_work_root,
            missing_ok=True,
        )
    except (SafeFilesystemError, OSError) as error:
        raise CleanupError(
            f"失败日志已提交，但 work 删除失败：{error}",
            phase="work",
            path=inputs.exact_work_dir,
        ) from error


def finalize_failed_job(inputs: FailureInputs) -> FailureResult:
    """先提交 `logs/<source>/<T>.log`，成功后才删除精确当前 work。"""

    try:
        log_path = _commit_failure_log(inputs)
    except CleanupError:
        raise
    except _WRAP_ERRORS as error:
        raise CleanupError(
            str(error),
            phase="log",
            path=inputs.merged_log,
        ) from error
    try:
        _delete_work(inputs)
    except CleanupError:
        raise
    except _WRAP_ERRORS as error:
        raise CleanupError(
            str(error),
            phase="work",
            path=inputs.exact_work_dir,
        ) from error
    return FailureResult(
        source=inputs.source,
        cycle=inputs.cycle,
        log_path=log_path,
        removed_work_dir=inputs.exact_work_dir,
        job_id=inputs.job_record.job_id,
    )


def _lstat_determined(path: Path, *, phase: CleanupPhase) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as error:
        raise CleanupError(
            f"{path} 的存在性无法确定（{error}）",
            phase=phase,
            path=path,
        ) from error


def _symlink_target_text(path: Path) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return "<unreadable>"


def _require_realpath_contained(path: Path, root: Path, *, phase: CleanupPhase) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, ValueError) as error:
        raise CleanupError(
            f"{path} 的 realpath 无法确定（{error}）",
            phase=phase,
            path=path,
        ) from error
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise CleanupError(
            f"路径 {path} 的 realpath {resolved} 不在 {root} 内",
            phase=phase,
            path=path,
        ) from error
    if resolved == root:
        raise CleanupError(
            f"路径 {path} 的 realpath 等于容纳根 {root}，拒绝操作根本身",
            phase=phase,
            path=path,
        )
    return resolved


def _precheck_output_dir(path: Path, root: Path, *, phase: CleanupPhase) -> None:
    info = _lstat_determined(path, phase=phase)
    if info is None:
        return
    if stat.S_ISLNK(info.st_mode):
        raise CleanupError(
            f"拒绝删除 symlink {path}（目标 {_symlink_target_text(path)}）",
            phase=phase,
            path=path,
        )
    if not stat.S_ISDIR(info.st_mode):
        raise CleanupError(
            f"output 删除目标必须是真目录：{path}（st_mode={info.st_mode:#o}）",
            phase=phase,
            path=path,
        )
    _require_realpath_contained(path, root, phase=phase)


def _precheck_log_file(path: Path, root: Path, *, phase: CleanupPhase) -> None:
    info = _lstat_determined(path, phase=phase)
    if info is None:
        return
    if stat.S_ISLNK(info.st_mode):
        raise CleanupError(
            f"拒绝删除 symlink {path}（目标 {_symlink_target_text(path)}）",
            phase=phase,
            path=path,
        )
    if not stat.S_ISREG(info.st_mode):
        raise CleanupError(
            f"失败日志删除目标必须是普通文件：{path}（st_mode={info.st_mode:#o}）",
            phase=phase,
            path=path,
        )
    _require_realpath_contained(path, root, phase=phase)


def _precheck_anchor(
    output_root: Path, source: str, latest: datetime, root: Path
) -> None:
    source_dir = output_root / cycle_id(latest) / source
    done_path = source_dir / _DONE_NAME
    info = _lstat_determined(source_dir, phase="retention-plan")
    if info is None:
        raise CleanupError(
            f"最新 DONE 的 source 目录不存在：{source_dir}",
            phase="retention-plan",
            path=source_dir,
        )
    if stat.S_ISLNK(info.st_mode):
        raise CleanupError(
            f"拒绝用 symlink 锚定保留窗口：{source_dir}"
            f"（目标 {_symlink_target_text(source_dir)}）",
            phase="retention-plan",
            path=source_dir,
        )
    if not stat.S_ISDIR(info.st_mode):
        raise CleanupError(
            f"最新 DONE 的 source 路径必须是真目录：{source_dir}",
            phase="retention-plan",
            path=source_dir,
        )
    _require_realpath_contained(source_dir, root, phase="retention-plan")
    done_info = _lstat_determined(done_path, phase="retention-plan")
    if done_info is None:
        raise CleanupError(
            f"最新 DONE 不存在：{done_path}",
            phase="retention-plan",
            path=done_path,
        )
    if stat.S_ISLNK(done_info.st_mode):
        raise CleanupError(
            f"拒绝用 symlink DONE 锚定保留窗口：{done_path}"
            f"（目标 {_symlink_target_text(done_path)}）",
            phase="retention-plan",
            path=done_path,
        )
    if not stat.S_ISREG(done_info.st_mode):
        raise CleanupError(
            f"最新 DONE 必须是普通文件：{done_path}（st_mode={done_info.st_mode:#o}）",
            phase="retention-plan",
            path=done_path,
        )
    _require_realpath_contained(done_path, root, phase="retention-plan")


def _list_names(directory: Path, *, phase: CleanupPhase) -> list[str]:
    try:
        return [entry.name for entry in directory.iterdir()]
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as error:
        raise CleanupError(
            f"目录 {directory} 无法枚举（{error}）",
            phase=phase,
            path=directory,
        ) from error


def _plan_retention(root: Path, source: str) -> RetentionPlan:
    output_root = root / "output"
    try:
        completed = done_cycles(output_root, source)
    except DiscoveryUnreadableError as error:
        raise CleanupError(
            f"{source}: 保留窗口无法锚定——{error.detail}",
            phase="retention-plan",
            path=output_root,
        ) from error
    if not completed:
        return RetentionPlan(
            yd_root=root,
            source=source,
            latest_done=None,
            cutoff=None,
            output_dirs=(),
            log_files=(),
        )

    latest = max(completed)
    _precheck_anchor(output_root, source, latest, root)
    cutoff = latest - _RETENTION_WINDOW

    output_dirs: list[Path] = []
    for name in _list_names(output_root, phase="retention-plan"):
        cycle = parse_cycle_id(name)
        if cycle is None or cycle >= cutoff:
            continue
        candidate = output_root / name / source
        info = _lstat_determined(candidate, phase="retention-plan")
        if info is None:
            continue
        _precheck_output_dir(candidate, root, phase="retention-plan")
        output_dirs.append(candidate)

    log_dir = root / "logs" / source
    log_files: list[Path] = []
    for name in _list_names(log_dir, phase="retention-plan"):
        if not name.endswith(".log"):
            continue
        cycle = parse_cycle_id(name[: -len(".log")])
        if cycle is None or cycle >= cutoff:
            continue
        candidate = log_dir / name
        info = _lstat_determined(candidate, phase="retention-plan")
        if info is None:
            continue
        _precheck_log_file(candidate, root, phase="retention-plan")
        log_files.append(candidate)

    return RetentionPlan(
        yd_root=root,
        source=source,
        latest_done=latest,
        cutoff=cutoff,
        output_dirs=tuple(sorted(output_dirs)),
        log_files=tuple(sorted(log_files)),
    )


def plan_retention(yd_root: Path, source: str) -> RetentionPlan:
    """零写入：按该源最新 `DONE` 锚定 14 天窗口，给出删除清单。"""

    try:
        _require_source_component(source)
    except ValueError as error:
        raise CleanupError(str(error), phase="validate", path=None) from error
    try:
        return _plan_retention(Path(yd_root).resolve(), source)
    except CleanupError:
        raise
    except _WRAP_ERRORS as error:
        raise CleanupError(str(error), phase="retention-plan", path=None) from error


def _precheck_existing_targets(plan: RetentionPlan, *, phase: CleanupPhase) -> None:
    for directory in plan.output_dirs:
        info = _lstat_determined(directory, phase=phase)
        if info is None:
            continue
        _precheck_output_dir(directory, plan.yd_root, phase=phase)
        try:
            verify_tree_no_symlinks(directory, containment_root=plan.yd_root)
        except (SafeFilesystemError, OSError) as error:
            raise CleanupError(
                f"output 删除目标树不可安全预检：{error}",
                phase=phase,
                path=directory,
            ) from error
    for log_file in plan.log_files:
        info = _lstat_determined(log_file, phase=phase)
        if info is None:
            continue
        _precheck_log_file(log_file, plan.yd_root, phase=phase)


def execute_retention_plan(plan: RetentionPlan) -> None:
    """删除清单列出的窗口外本源 output 树与失败日志；先全量预检再删。"""

    try:
        _bind_retention_plan(plan)
    except CleanupError:
        raise
    except _WRAP_ERRORS as error:
        raise CleanupError(str(error), phase="validate", path=None) from error
    try:
        _precheck_existing_targets(plan, phase="retention-execute")
        for directory in plan.output_dirs:
            try:
                rmtree_no_follow(
                    directory,
                    containment_root=plan.yd_root,
                    missing_ok=True,
                )
            except (SafeFilesystemError, OSError) as error:
                raise CleanupError(
                    f"删除 output 目录失败：{error}",
                    phase="retention-execute",
                    path=directory,
                ) from error
        for log_file in plan.log_files:
            try:
                unlink_no_follow(
                    log_file,
                    containment_root=plan.yd_root,
                    missing_ok=True,
                )
            except (SafeFilesystemError, OSError) as error:
                raise CleanupError(
                    f"删除失败日志失败：{error}",
                    phase="retention-execute",
                    path=log_file,
                ) from error
    except CleanupError:
        raise
    except _WRAP_ERRORS as error:
        raise CleanupError(
            str(error),
            phase="retention-execute",
            path=None,
        ) from error
