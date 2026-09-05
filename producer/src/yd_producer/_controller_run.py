"""`controller.run_once` / `catch_up_source` 的私有支撑（issue #26/#27）。

公开 seam 只有 `yd_producer.controller` 上的惰性转发；本模块不导出公共符号。
`catch_up_source` 只组合公开 `controller.run_once`，不复制 14.1 状态机。"""

from __future__ import annotations

import os
import stat as stat_module
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from yd_producer import controller
from yd_producer import prepare as prepare_module
from yd_producer import publish as publish_module
from yd_producer import rawcopy as rawcopy_module
from yd_producer import rawscan as rawscan_module
from yd_producer import residue as residue_module
from yd_producer import state as state_module
from yd_producer import tracker as tracker_module
from yd_producer.assemble import RunDirectory
from yd_producer.config import Config, LocalConfig
from yd_producer.controller import (
    AttemptDriver,
    AttemptProducts,
    AttemptRequest,
    PreparedAttempt,
    RunError,
    RunOutcome,
    RunReport,
)
from yd_producer.executor import JobExecutor, JobRecord, JobSpec, JobState
from yd_producer.store import safe_fs
from yd_producer.store.object_store import LocalObjectStore
from yd_producer.tracker import CheckpointTracker

_SOURCES = ("ifs", "gfs")  # rawscan.SOURCES 同源
RUN_DIRECTORY_NAME = "model"  # assemble 自有字面量
JOB_LOG_NAME = "job.log"  # compute-loop §3.3
CHECKPOINT_DIR_NAME = "state_checkpoints"  # tracker.CHECKPOINT_DIR_NAME 同源
JOB_NAME_TEMPLATE = "yd-{source}-{cycle}"  # ownership 6
CHECKPOINT_TARGET_HOUR = 12  # preflight 已钉死 checkpoint_hours == (12,)


@dataclass
class _Context:
    """phase/holder：未预期异常由外层兜底给出此刻阶段（RunError 的 phase/cycle/job_id）。"""

    phase: str = "preflight"
    cycle: datetime | None = None
    job_id: str | None = None


def _stopped_report(
    source: str,
    *,
    cycle: datetime | None,
    stop_reason: controller.StopReason,
    detail: str,
) -> RunReport:
    """STOPPED 报告的单一构造点：stop_reason 恰有、job/published/done_path 恒空。"""
    return RunReport(
        source=source,
        cycle=cycle,
        outcome=RunOutcome.STOPPED,
        stop_reason=stop_reason,
        detail=detail,
        job=None,
        published=None,
        done_path=None,
    )


def _discovery_unreadable_stop(
    source: str, error: controller.DiscoveryUnreadableError
) -> RunReport:
    """探测「无法确定」收敛为本源 STOPPED，与 `decide_frontier` 同一 detail 形态
    （Required evidence 7 计入既有 contract）；单源探测失败不得放大成整 tick 的
    `RunError(frontier)`，也不得吞成「空集合」。"""
    return _stopped_report(
        source,
        cycle=None,
        stop_reason=controller.StopReason.DISCOVERY_UNREADABLE,
        detail=f"{source}: {error.detail}",
    )


def _require_exact_work_parent(
    *, work_root: Path, work_dir: Path, source: str, cycle: datetime
) -> None:
    """写前证明 work 父路径逐字等于 `work_root/source`（无 symlink/`..`/别名）。

    `stage_raw` 的 `mkdir(parents=True)` 是裸创建；若 `work_root` 或 `work_root/source`
    是已存在 symlink/别名（T leaf 缺席时 `lexists(work_dir)` 为假、拦不住），副本会写进
    目标树。本闸在 `stage_raw` 之前以 raw phase 拒绝；**缺失的普通父目录仍然合法**。"""

    expected_parent = work_root / source
    if work_dir.parent != expected_parent:
        raise RunError(
            f"work 目录父路径必须逐字是 {expected_parent}，实得 {work_dir.parent}",
            phase="raw",
            source=source,
            cycle=cycle,
        )
    if Path(work_dir.parent).resolve() != expected_parent:
        raise RunError(
            f"work 父路径 {work_dir.parent} 存在符号链接/别名/`..`（归一为 "
            f"{Path(work_dir.parent).resolve()}）；staging 前拒绝，禁止把本轮写入指向别处",
            phase="raw",
            source=source,
            cycle=cycle,
        )
    for component in (work_root, expected_parent):
        if not os.path.lexists(component):
            continue
        info = os.lstat(component)
        if stat_module.S_ISLNK(info.st_mode) or not stat_module.S_ISDIR(info.st_mode):
            raise RunError(
                f"work 路径分量 {component} 不是原样普通目录"
                f"（st_mode={info.st_mode:#o}）；staging 前拒绝",
                phase="raw",
                source=source,
                cycle=cycle,
            )


def _reject_preexisting_work(work_dir: Path, source: str, cycle: datetime) -> None:
    """终名 work 预存任何形态都拒绝：不续跑、不覆盖、不采纳（ownership 4）。"""
    if os.path.lexists(work_dir):
        raise RunError(
            f"终名 work 已存在（任何形态），拒绝运行：{work_dir}；"
            "work 是一次性隔离单元，不续跑/不覆盖/不采纳",
            phase="raw",
            source=source,
            cycle=cycle,
        )


def _phase_error(
    message: str, phase: controller.RunPhase, source: str, cycle: datetime, job_id=None
):
    """抛一个 phase 类型化 RunError 的便捷入口（私有结构简化，字段逐字保留）。"""
    raise RunError(message, phase=phase, source=source, cycle=cycle, job_id=job_id)


def _verify_raw_fanout(
    *,
    staged: rawcopy_module.StagedRaw,
    store: LocalObjectStore,
    source: str,
    cycle: datetime,
) -> None:
    """逐变量扇出的集合/成员关系校验（raw-scan spec「逐变量扇出」）。

    `entries` 按 `(lead, variable)` 扇出（同一 bundle 副本被多变量 entry 共享），
    `copied_files` 按 lead/bundle 复制、无顺序保证；只允许：每 entry 解析到恰好一个
    copied 成员，每副本至少被一 entry 引用。位置/等基数 `zip` 是 contract-1 已确认
    缺陷，禁止恢复。
    """
    raw_error = lambda message: _phase_error(message, "raw", source, cycle)
    copied_set = set(staged.copied_files)
    repeats = len(staged.copied_files) - len(copied_set)
    if repeats:
        raw_error(f"copied_files 含重复路径（{repeats} 项）；raw 副本必须逐 lead 唯一")
    raw_root = Path(store.root) / "raw"
    for copied in staged.copied_files:
        if not Path(copied).is_absolute() or not Path(copied).is_relative_to(raw_root):
            raw_error(f"copied 路径 {copied} 不在 object-store raw 子树 {raw_root} 内")
    referenced: set[Path] = set()
    for entry in staged.entries:
        resolved = store.resolve_path(entry.local_key)
        if resolved not in copied_set:
            raw_error(
                f"entry.local_key {entry.local_key!r} 解析到 {resolved}，"
                f"不在本轮 copied 副本集合（{len(copied_set)} 项）内"
            )
        referenced.add(resolved)
    orphaned = copied_set - referenced
    if orphaned:
        raw_error(f"copied 副本 {next(iter(orphaned))} 未被任何 entry 引用（orphan）")


def _variant_reach_count(*, source: str, cycle: datetime, variant_dir: Path) -> int:
    """独立 reach 数（ownership 5）：率定态 river 行数，不信 driver 自报/不从 DAT 反推。"""
    calibrated = prepare_module.calibrated_state_path(variant_dir)
    try:
        raw = safe_fs.read_bytes_limited_no_follow(
            calibrated, max_bytes=state_module.MAX_STATE_IC_BYTES
        )
    except (OSError, safe_fs.SafeFilesystemError, ValueError) as exc:
        raise RunError(
            f"率定状态不可读：{calibrated}（{exc}）",
            phase="prepare",
            source=source,
            cycle=cycle,
        ) from exc
    try:
        document = state_module.parse(raw)
    except ValueError as exc:
        raise RunError(
            f"率定状态不可解析：{calibrated}（{exc}）",
            phase="prepare",
            source=source,
            cycle=cycle,
        ) from exc
    if document.river is None:
        raise RunError(
            f"率定状态 {calibrated} 缺 river 段（不是 0 条河段，是缺段）",
            phase="prepare",
            source=source,
            cycle=cycle,
        )
    count = document.river.row_count
    if not isinstance(count, int):
        raise RunError(
            f"率定状态 {calibrated} 的 river 行数不是 int：{count!r}",
            phase="prepare",
            source=source,
            cycle=cycle,
        )
    return count


def _canonical_checkpoint_path(*, attempt: PreparedAttempt, work_dir: Path) -> Path:
    """canonical 未来终名：`<work>/model/state_checkpoints/<project>.f012.cfg.ic.update`。

    由 tracker 的 `checkpoint_dir / f"{project}.f{12:03d}.cfg.ic.update"` 唯一确定性推导；
    controller 自己推导并点用重验，不靠扫描规范文件名寻找 authority。"""
    return (
        work_dir
        / RUN_DIRECTORY_NAME
        / CHECKPOINT_DIR_NAME
        / (
            f"{attempt.identity.project_name}.f{CHECKPOINT_TARGET_HOUR:03d}.cfg.ic.update"
        )
    )


def _require_absent_before_submit(
    items: tuple[tuple[str, Path], ...], *, source: str, cycle: datetime
) -> None:
    """提交前 DAT/job log/canonical 三者任何形态（含断链 symlink）都不存在：`lexists`
    语义下普通文件/目录/symlink 一律算「存在」，任何预埋形态都不被采纳（evidence 15）。"""
    for label, path in items:
        if os.path.lexists(path):
            raise RunError(
                f"{label} 提交前必须不存在（已存在任何形态）：{path}",
                phase="submit",
                source=source,
                cycle=cycle,
            )


def _require_terminal_artifacts_pre_collect(
    items: tuple[tuple[str, Path], ...],
    *,
    work_root: Path,
    work_dir: Path,
    source: str,
    cycle: datetime,
    job_id: str,
) -> None:
    """SUCCEEDED 后、`driver.collect` 之前：三件产物必须已是 exact work 内普通文件。

    时序闸（evidence 15）：collect 只许交接、不许制造；本闸零 collect、零 publish/DONE
    之前拒绝，不可由 collect 后 stat 替代。"""
    collect_error = lambda message: _phase_error(
        message, "collect", source, cycle, job_id
    )
    for label, path in items:
        candidate = Path(path)
        if not candidate.is_relative_to(work_dir):
            collect_error(f"{label} 不在精确 work {work_dir} 内：{candidate}")
        try:
            info = safe_fs.stat_no_follow(candidate, containment_root=work_root)
        except FileNotFoundError as exc:
            raise RunError(
                f"{label} 在 collect 前不存在：{candidate}（terminal hook 未在"
                "SUCCEEDED 跃迁内完整产出）",
                phase="collect",
                source=source,
                cycle=cycle,
                job_id=job_id,
            ) from exc
        except (OSError, safe_fs.SafeFilesystemError) as exc:
            raise RunError(
                f"{label} 在 collect 前不是 no-follow 普通文件：{candidate}（{exc}）",
                phase="collect",
                source=source,
                cycle=cycle,
                job_id=job_id,
            ) from exc
        if not stat_module.S_ISREG(info.st_mode):
            collect_error(
                f"{label} 在 collect 前不是普通文件：{candidate}"
                f"（st_mode={info.st_mode:#o}）"
            )


def _make_job_spec(
    *,
    attempt: PreparedAttempt,
    source: str,
    cycle: datetime,
    work_dir: Path,
    local: LocalConfig,
) -> JobSpec:
    """controller 唯一构造 JobSpec（ownership 6：driver 无权选 name/work/log/resources）。"""
    return JobSpec(
        name=JOB_NAME_TEMPLATE.format(source=source, cycle=controller.cycle_id(cycle)),
        work_dir=work_dir,
        command=attempt.command,
        log_path=work_dir / JOB_LOG_NAME,
        resources=dict(local.slurm),
    )


def _validate_prepared(
    attempt: PreparedAttempt,
    source: str,
    cycle: datetime,
    work_dir: Path,
    variant_dir: Path,
) -> None:
    """prepared-attempt 矩阵（ownership 5）：identity/command/DAT 的一把闸。"""
    prepared_error = lambda message: _phase_error(message, "prepare", source, cycle)

    identity = attempt.identity
    if identity.source_id != source:
        prepared_error(
            f"identity.source_id={identity.source_id!r} 不等于 source={source!r}"
        )
    if identity.cycle_time != cycle:
        prepared_error(
            f"identity.cycle_time={identity.cycle_time!r} 不等于 cycle={cycle!r}"
        )
    calibrated = prepare_module.calibrated_state_path(variant_dir)
    expected_state_name = f"{identity.project_name}{controller.STATE_SUFFIX}"
    if calibrated.name != expected_state_name:
        prepared_error("identity.project_name 与率定状态项目名不符")
    command = attempt.command
    if not isinstance(command, tuple) or not command:
        prepared_error(f"command 必须是非空 tuple[str, ...]，实得 {command!r}")
    for item in command:
        if not isinstance(item, str) or not item:
            prepared_error(f"command 的每项必须是非空 str，实得 {item!r}")
        if "\x00" in item:
            prepared_error(f"command 含 NUL 字节：{item!r}")
    scratch_dat = attempt.scratch_dat
    if not isinstance(scratch_dat, Path) or not scratch_dat.is_absolute():
        prepared_error(f"scratch_dat 必须是绝对路径，实得 {scratch_dat!r}")
    leaf = scratch_dat.name
    if leaf in {"", ".", ".."} or "/" in leaf or "\\" in leaf or "\x00" in leaf:
        prepared_error(f"scratch DAT 必须是一个安全未来 leaf：{scratch_dat}")
    parent = scratch_dat.parent
    # 词法与解析后的父路径必须一致：`.`/`..` 段与 symlink 祖先都会让 `resolve()` 与
    # 词法路径分叉，届时 `stage_raw` 的裸 `mkdir(parents=True)` 沿别名写到 work 之外。
    if not parent.is_relative_to(work_dir) or Path(parent).resolve() != parent:
        prepared_error(
            f"scratch DAT {scratch_dat} 必须位于 exact work {work_dir} 内的无歧义"
            f"未来父目录（母路径 {parent} 不得含 `..`/symlink/别名）"
        )
    if os.path.lexists(scratch_dat):
        prepared_error(f"scratch DAT 提交前必须不存在：{scratch_dat}")


def _verify_canonical_is_regular(
    canonical: Path, work_root: Path, source: str, cycle: datetime, *, job_id: str
) -> None:
    try:
        info = safe_fs.stat_no_follow(canonical, containment_root=work_root)
    except (OSError, safe_fs.SafeFilesystemError) as exc:
        raise RunError(
            f"checkpoint canonical 不是可读无 follow 普通文件：{canonical}（{exc}）",
            phase="collect",
            source=source,
            cycle=cycle,
            job_id=job_id,
        ) from exc
    if not stat_module.S_ISREG(info.st_mode):
        raise RunError(
            f"checkpoint canonical 不是普通文件：{canonical}",
            phase="collect",
            source=source,
            cycle=cycle,
            job_id=job_id,
        )


def _validate_products(
    products: object,
    *,
    attempt: PreparedAttempt,
    terminal: JobRecord,
    job_spec: JobSpec,
    work_dir: Path,
    work_root: Path,
    canonical: Path,
    source: str,
    cycle: datetime,
) -> None:
    """products 矩阵（ownership 8）：job/DAT/log/RunDirectory/tracker 逐字段绑定。"""
    collect_error = lambda message: _phase_error(
        message, "collect", source, cycle, terminal.job_id
    )

    if not isinstance(products, AttemptProducts):
        collect_error(f"driver.collect 返回类型错误：{type(products).__name__}")
    if products.job_id != terminal.job_id:
        collect_error(
            f"products.job_id {products.job_id!r} != terminal job_id {terminal.job_id!r}"
        )
    if products.scratch_dat != attempt.scratch_dat:
        collect_error(
            f"products.scratch_dat {products.scratch_dat!r} 不等于 prepare 声明的 "
            f"{attempt.scratch_dat!r}"
        )
    if products.merged_log != job_spec.log_path:
        collect_error(
            f"products.merged_log {products.merged_log!r} 不等于 JobSpec.log_path "
            f"{job_spec.log_path!r}"
        )
    run_directory = products.run_directory
    if not isinstance(run_directory, RunDirectory):
        collect_error(
            f"products.run_directory 类型错误：{type(run_directory).__name__}"
        )
    expected_run_dir = work_dir / RUN_DIRECTORY_NAME
    if run_directory.path != expected_run_dir:
        collect_error(
            f"RunDirectory.path {run_directory.path!r} 必须等于 {expected_run_dir!r}"
        )
    if run_directory.identity != attempt.identity:
        collect_error(
            f"RunDirectory.identity {run_directory.identity!r} 不等于 "
            f"attempt.identity {attempt.identity!r}"
        )
    tracker = products.tracker
    if not isinstance(tracker, CheckpointTracker):
        collect_error(f"products.tracker 类型错误：{type(tracker).__name__}")
    if tracker.run_dir != run_directory.path:
        collect_error(
            f"tracker.run_dir {tracker.run_dir!r} 必须等于 RunDirectory.path "
            f"{run_directory.path!r}"
        )
    if tracker.project_name != run_directory.project_name:
        collect_error(
            f"tracker.project_name {tracker.project_name!r} 必须等于 "
            f"RunDirectory.project_name {run_directory.project_name!r}"
        )
    if tracker.targets != (12,):
        collect_error(f"tracker.targets 必须恰为 (12,)，实得 {tracker.targets!r}")
    # canonical 终名必须与 tracker 自己的派生逐字一致（控制器推导 <> tracker 派生的
    # 绑定重验；规范文件名从不自证 authority，这里是契约绑定不是扫描）。
    tracker_canonical = (
        tracker.checkpoint_dir
        / f"{tracker.project_name}.f{CHECKPOINT_TARGET_HOUR:03d}.cfg.ic.update"
    )
    if canonical != tracker_canonical:
        collect_error(
            f"canonical 终名 {canonical} 必须逐字等于 tracker 派生 {tracker_canonical}"
        )
    # DAT/log/checkpoint 在终态后为 no-follow 普通文件（pre-collect 闸在先，此处是
    # collect 之后的复验，防 collect 在窗口内替换）。
    for label, path in (
        ("DAT", products.scratch_dat),
        ("merged log", products.merged_log),
    ):
        try:
            info = safe_fs.stat_no_follow(path, containment_root=work_root)
        except (OSError, safe_fs.SafeFilesystemError) as exc:
            raise RunError(
                f"{label} 在终态后不是可读 no-follow 普通文件：{path}（{exc}）",
                phase="collect",
                source=source,
                cycle=cycle,
                job_id=terminal.job_id,
            ) from exc
        if not stat_module.S_ISREG(info.st_mode):
            raise RunError(
                f"{label} 在终态后不是普通文件：{path}",
                phase="collect",
                source=source,
                cycle=cycle,
                job_id=terminal.job_id,
            )


def run_once(
    *,
    config: Config,
    local: LocalConfig,
    source: str,
    executor: JobExecutor,
    driver: AttemptDriver,
    poll_wait: Callable[[], None],
) -> RunReport:
    """单源单轮骨架；全部 keyword-only、无默认值（tasks.md「公开面」逐字冻结）。

    普通异常一律 `RunError(phase/source/cycle/job_id)` 并保留 `__cause__`；`RunError`
    原样穿透；`BaseException`（KeyboardInterrupt/SystemExit）不包。"""
    ctx = _Context()
    try:
        return _run_once(
            config=config,
            local=local,
            source=source,
            executor=executor,
            driver=driver,
            poll_wait=poll_wait,
            ctx=ctx,
        )
    except RunError:
        raise
    except Exception as exc:
        raise RunError(
            f"run_once 在阶段 `{ctx.phase}` 出现未预期异常：{exc}",
            phase=ctx.phase,
            source=source,
            cycle=ctx.cycle,
            job_id=ctx.job_id,
        ) from exc


def _run_once(
    *,
    config: Config,
    local: LocalConfig,
    source: str,
    executor: JobExecutor,
    driver: AttemptDriver,
    poll_wait: Callable[[], None],
    ctx: _Context,
) -> RunReport:
    # 1. preflight（私有校验面在 controller）
    controller._preflight(config=config, local=local, source=source)

    # 2. 前沿前半段（DONE/状态 -> T；不含 raw 判定）
    ctx.phase = "frontier"
    try:
        gathered = controller._target_and_state(Path(local.yd_root), source)
    except controller.DiscoveryUnreadableError as exc:
        # 探测「无法确定」= 该源 STOPPED，不与 `decide_frontier` 既有契约分叉。
        return _discovery_unreadable_stop(source, exc)
    except Exception as exc:
        raise RunError(
            f"前沿判定失败：{exc}",
            phase="frontier",
            source=source,
        ) from exc
    if isinstance(gathered, controller.FrontierDecision):
        return _stopped_report(
            source,
            cycle=None,
            stop_reason=gathered.stop_reason,
            detail=gathered.detail,
        )
    target, origin, _state_path = gathered
    ctx.cycle = target

    # 越域小时：residue/raw/work/submit 前收敛 RAW_INCOMPLETE（ownership 2）。
    if target.hour not in config.cycle.hours:
        return _stopped_report(
            source,
            cycle=target,
            stop_reason=controller.StopReason.RAW_INCOMPLETE,
            detail=(
                f"{source}: 待跑 T={controller.cycle_id(target)}（{origin}）；"
                f"cycle 小时 {target.hour:02d}Z 不在 config.cycle.hours 声明（"
                + "、".join(f"{hour:02d}Z" for hour in config.cycle.hours)
                + "）内，按 raw 未齐停在缺口等待，不跳轮"
            ),
        )

    # 3. 合法 T：residue plan/execute，然后 rawscan.judge
    ctx.phase = "residue"
    decision = controller.FrontierDecision(
        source=source,
        cycle=target,
        stop_reason=None,
        detail=f"{source}: 待跑 T={controller.cycle_id(target)}（{origin}）",
    )
    try:
        plan = residue_module.plan_residue(
            yd_root=Path(local.yd_root), source=source, decision=decision
        )
        if plan is not None:
            residue_module.execute_residue_plan(plan)
    except Exception as exc:
        raise RunError(
            f"残留清理失败：{exc}",
            phase="residue",
            source=source,
            cycle=target,
        ) from exc

    ctx.phase = "raw"
    try:
        verdict = rawscan_module.judge(local.nwm.raw_root, source, target, config)
    except Exception as exc:
        raise RunError(
            f"raw 完整性判定失败：{exc}",
            phase="raw",
            source=source,
            cycle=target,
        ) from exc
    if not verdict.complete:
        missing = verdict.missing_files
        unreadable = verdict.unreadable_files
        return _stopped_report(
            source,
            cycle=target,
            stop_reason=controller.StopReason.RAW_INCOMPLETE,
            detail=(
                f"{source}: 待跑 T={controller.cycle_id(target)}（{origin}）；"
                f"raw 未齐：缺 {len(missing)} 件"
                + (f"（首件 {missing[0]}）" if missing else "")
                + f"，不可读 {len(unreadable)} 件"
                + (f"（首件 {unreadable[0]}）" if unreadable else "")
                + "；停在缺口等待，不跳轮"
            ),
        )

    # --- 4. work 派生：写前证明 exact 父路径、拒绝预存、stage_raw 落 object-store 根 ---
    try:
        work_root = Path(local.scratch_root).resolve() / "work"
        work_dir = work_root / source / controller.cycle_id(target)
        object_store_root = work_dir / "object-store"
        _require_exact_work_parent(
            work_root=work_root, work_dir=work_dir, source=source, cycle=target
        )
        _reject_preexisting_work(work_dir, source, target)
        staged = rawcopy_module.stage_raw(
            verdict=verdict,
            raw_root=local.nwm.raw_root,
            work_dir=object_store_root,
            source=source,
            cycle=target,
            config=config,
        )
        expected_manifest = object_store_root / rawcopy_module.MANIFEST_FILENAME
        if staged.manifest_path != expected_manifest:
            raise RunError(
                f"stage_raw 返回的 manifest 不在 object-store 根："
                f"{staged.manifest_path} != {expected_manifest}",
                phase="raw",
                source=source,
                cycle=target,
            )
    except RunError:
        raise
    except Exception as exc:
        raise RunError(
            f"raw staging 失败：{exc}",
            phase="raw",
            source=source,
            cycle=target,
        ) from exc

    store_obj = LocalObjectStore(object_store_root)
    _verify_raw_fanout(
        staged=staged,
        store=store_obj,
        source=source,
        cycle=target,
    )

    # 5. 变体/率定状态 + driver.prepare + prepared 校验
    ctx.phase = "prepare"
    try:
        variants = prepare_module.variant_targets(local, config)
        variant_dir = variants[source]
    except Exception as exc:
        raise RunError(
            f"变体终名解析失败：{exc}",
            phase="prepare",
            source=source,
            cycle=target,
        ) from exc
    variant_reach_count = _variant_reach_count(
        source=source, cycle=target, variant_dir=variant_dir
    )
    if config.reach_count != variant_reach_count:
        raise RunError(
            f"reach_count {config.reach_count} 与模型变体 reach 数 "
            f"{variant_reach_count} 不相等",
            phase="prepare",
            source=source,
            cycle=target,
        )
    state_path = (
        Path(local.yd_root)
        / "states"
        / source
        / (f"{controller.cycle_id(target)}{controller.STATE_SUFFIX}")
    )
    request = AttemptRequest(
        source=source,
        cycle=target,
        work_root=work_root,
        work_dir=work_dir,
        object_store_root=object_store_root,
        raw_manifest_path=staged.manifest_path,
        variant_dir=variant_dir,
        state_path=state_path,
        shud_binary=local.shud_binary,
        checkpoint_hours=config.checkpoint_hours,
        forecast_days=config.forecast_days,
        output_interval_minutes=config.output_interval_minutes,
        reach_count=config.reach_count,
    )
    try:
        attempt = driver.prepare(request=request)
    except Exception as exc:
        raise RunError(
            f"driver.prepare 失败：{exc}",
            phase="prepare",
            source=source,
            cycle=target,
        ) from exc
    if not isinstance(attempt, PreparedAttempt):
        raise RunError(
            f"driver.prepare 返回类型错误：{type(attempt).__name__}",
            phase="prepare",
            source=source,
            cycle=target,
        )
    _validate_prepared(attempt, source, target, work_dir, variant_dir)
    canonical = _canonical_checkpoint_path(attempt=attempt, work_dir=work_dir)

    # 6. 唯一构造 JobSpec；submit 前三件终态产物必须不存在
    ctx.phase = "submit"
    job_spec = _make_job_spec(
        attempt=attempt,
        source=source,
        cycle=target,
        work_dir=work_dir,
        local=local,
    )
    _require_absent_before_submit(
        (
            ("DAT", attempt.scratch_dat),
            ("job log", job_spec.log_path),
            ("checkpoint", canonical),
        ),
        source=source,
        cycle=target,
    )
    try:
        submission = executor.submit(job_spec)
    except Exception as exc:
        raise RunError(
            f"作业提交失败：{exc}",
            phase="submit",
            source=source,
            cycle=target,
        ) from exc
    ctx.job_id = submission.job_id
    controller._require_spec_record(
        submission, job_spec, "submit", source=source, cycle=target
    )

    # 7. 首次 poll 立即；每条非终态 poll 后恰一次 poll_wait
    ctx.phase = "poll"
    terminal: JobRecord | None = None
    previous = submission
    while True:
        try:
            record = executor.poll(submission.job_id)
        except Exception as exc:
            raise RunError(
                f"作业轮询失败：{exc}",
                phase="poll",
                source=source,
                cycle=target,
                job_id=submission.job_id,
            ) from exc
        controller._require_poll_record(
            record,
            submission=submission,
            spec=job_spec,
            previous=previous,
            source=source,
            cycle=target,
        )
        if record.state.is_terminal:
            terminal = record
            break
        poll_wait()
        previous = record
    assert terminal is not None

    # 8. 终态三分：FAILED/TIMEOUT -> JOB_FAILED（零 collect/publish）
    if terminal.state is not JobState.SUCCEEDED:
        return RunReport(
            source=source,
            cycle=target,
            outcome=RunOutcome.JOB_FAILED,
            stop_reason=None,
            detail=(
                f"{source}: 作业 {terminal.job_id} 终态 {terminal.state.value}，"
                "本轮失败；work 按 14.1 边界保留，失败收尾归 #28/#47"
            ),
            job=controller._job_report(submission, terminal),
            published=None,
            done_path=None,
        )

    # 8b. SUCCEEDED：先证明三件产物存在，再 collect 恰一次
    ctx.phase = "collect"
    _require_terminal_artifacts_pre_collect(
        (
            ("DAT", attempt.scratch_dat),
            ("job log", job_spec.log_path),
            ("checkpoint", canonical),
        ),
        work_root=work_root,
        work_dir=work_dir,
        source=source,
        cycle=target,
        job_id=submission.job_id,
    )
    try:
        products = driver.collect(attempt=attempt, terminal_record=terminal)
    except Exception as exc:
        raise RunError(
            f"driver.collect 失败：{exc}",
            phase="collect",
            source=source,
            cycle=target,
            job_id=submission.job_id,
        ) from exc
    _validate_products(
        products,
        attempt=attempt,
        terminal=terminal,
        job_spec=job_spec,
        work_dir=work_dir,
        work_root=work_root,
        canonical=canonical,
        source=source,
        cycle=target,
    )

    # 9. checkpoint point-of-use 重验（runner 零调用）
    try:
        captured = tracker_module.ensure_twelve_hour_checkpoint(
            tracker=products.tracker,
            run_directory=products.run_directory,
            runner=controller._require_no_recovery,
        )
    except Exception as exc:
        raise RunError(
            f"checkpoint 重验失败：{exc}",
            phase="collect",
            source=source,
            cycle=target,
            job_id=submission.job_id,
        ) from exc
    record = products.tracker.captured.get(12)
    if record is None or captured is not record:
        raise RunError(
            "checkpoint 重验结果必须逐字是 tracker.captured[12] 的同一对象/path/checksum",
            phase="collect",
            source=source,
            cycle=target,
            job_id=submission.job_id,
        )
    if record.path != canonical:
        raise RunError(
            f"checkpoint 记录路径 {record.path} 必须逐字等于控制器推导终名 {canonical}",
            phase="collect",
            source=source,
            cycle=target,
            job_id=submission.job_id,
        )
    _verify_canonical_is_regular(
        canonical, work_root, source, target, job_id=submission.job_id
    )

    # 10. publish 三态
    ctx.phase = "publish"
    publish_inputs = publish_module.PublishInputs(
        yd_root=local.yd_root,
        source=source,
        cycle=target,
        scratch_dat=products.scratch_dat,
        scratch_checkpoint=canonical,
        merged_log=products.merged_log,
        work_dir=work_dir,
        work_root=work_root,
        expected_rows=config.forecast_days * 24,
        reach_count=config.reach_count,
        variant_reach_count=variant_reach_count,
    )
    try:
        result = publish_module.publish(publish_inputs)
    except publish_module.PublishCleanupError as exc:
        return RunReport(
            source=source,
            cycle=target,
            outcome=RunOutcome.SUCCEEDED_CLEANUP_PENDING,
            stop_reason=None,
            detail=f"{source}: DONE 已承诺本轮，但清理失败：{exc}",
            job=controller._job_report(submission, terminal),
            published=None,
            done_path=exc.done_path,
        )
    except Exception as exc:
        raise RunError(
            f"发布失败：{exc}",
            phase="publish",
            source=source,
            cycle=target,
            job_id=submission.job_id,
        ) from exc

    # 11. 正常成功：SUCCEEDED，work 已删除
    return RunReport(
        source=source,
        cycle=target,
        outcome=RunOutcome.SUCCEEDED,
        stop_reason=None,
        detail=f"{source}: 一轮成功发布完成（{controller.cycle_id(target)}）",
        job=controller._job_report(submission, terminal),
        published=result,
        done_path=result.done_path,
    )


def catch_up_source(
    *,
    config: Config,
    local: LocalConfig,
    source: str,
    executor: JobExecutor,
    driver: AttemptDriver,
    poll_wait: Callable[[], None],
) -> tuple[RunReport, ...]:
    """单源多轮追赶：每轮只调用公开 `controller.run_once`，仅 SUCCEEDED 继续。

    不取锁、不预扫 raw、不自增 cycle、不复制 14.1 内部逻辑。STOPPED /
    JOB_FAILED / SUCCEEDED_CLEANUP_PENDING 把当前报告作为末项后立即返回；
    RunError 与 BaseException 原样外传。
    """
    reports: list[RunReport] = []
    while True:
        report = controller.run_once(
            config=config,
            local=local,
            source=source,
            executor=executor,
            driver=driver,
            poll_wait=poll_wait,
        )
        reports.append(report)
        if report.outcome is not RunOutcome.SUCCEEDED:
            return tuple(reports)
