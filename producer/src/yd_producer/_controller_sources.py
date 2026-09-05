"""Private dual-source combiner for `controller.run_sources` (issue #28).

Public types `RunSourcesReport` / `RunSourcesError` and the `run_sources` entry
are re-exported from `yd_producer.controller`. This module has no `__all__` and
is not a second public seam.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from yd_producer.controller import AttemptDriver, RunError, RunOutcome, RunReport
from yd_producer.executor import JobExecutor, JobRecord, JobSpec

if TYPE_CHECKING:
    from yd_producer.config import Config, LocalConfig

_SOURCE_ORDER: tuple[str, ...] = ("ifs", "gfs")
_SOURCE_KEYS = frozenset(_SOURCE_ORDER)


def classify_unverified_work(
    work_dir: Path, *, source: str, cycle: datetime
) -> RunReport | None:
    """NFS residue 之后、raw 之前：精确 work 的 lstat 语义。"""
    from yd_producer import controller
    from yd_producer._controller_run import _stopped_report

    try:
        os.lstat(work_dir)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as orig:
        raise RunError(
            f"精确 work {work_dir} 的存在性无法判定（{orig}）",
            phase="residue",
            source=source,
            cycle=cycle,
        ) from orig
    return _stopped_report(
        source,
        cycle=cycle,
        stop_reason=controller.StopReason.UNVERIFIED_WORK_RESIDUE,
        detail=(
            f"{source}: 待跑 T={controller.cycle_id(cycle)} 的精确 work {work_dir} "
            "仍存在（未验证跨进程残留）；保留证据，不读、不删、不提交"
        ),
    )


def finalize_failed_attempt(
    *,
    local: LocalConfig,
    source: str,
    cycle: datetime,
    job_spec: JobSpec,
    submission: JobRecord,
    terminal: JobRecord,
    work_root: Path,
    provider: Callable[[JobRecord], str],
) -> RunReport:
    """`run_sources` 路径：FAILED/TIMEOUT 用本源 provider + 真实 cleanup 收尾。"""
    from yd_producer import cleanup as cleanup_module
    from yd_producer import controller

    job_id = terminal.job_id
    try:
        exit_code = provider(terminal)
    except Exception as orig:
        raise RunError(
            f"失败退出码 provider 失败：{orig}",
            phase="cleanup",
            source=source,
            cycle=cycle,
            job_id=job_id,
        ) from orig
    if not isinstance(exit_code, str) or not exit_code.strip():
        raise RunError(
            f"失败退出码必须是 nonblank str，实得 {exit_code!r}",
            phase="cleanup",
            source=source,
            cycle=cycle,
            job_id=job_id,
        )
    try:
        result = cleanup_module.finalize_failed_job(
            cleanup_module.FailureInputs(
                yd_root=Path(local.yd_root),
                work_root=work_root,
                source=source,
                cycle=cycle,
                job_spec=job_spec,
                job_record=terminal,
                exit_code=exit_code,
            )
        )
    except Exception as orig:
        raise RunError(
            f"失败收尾失败：{orig}",
            phase="cleanup",
            source=source,
            cycle=cycle,
            job_id=job_id,
        ) from orig
    return RunReport(
        source=source,
        cycle=cycle,
        outcome=RunOutcome.JOB_FAILED,
        stop_reason=None,
        detail=(
            f"{source}: 作业 {terminal.job_id} 终态 {terminal.state.value}；"
            f"失败日志已提交 {result.log_path}，精确 work {result.removed_work_dir} 已删除"
        ),
        job=controller._job_report(submission, terminal),
        published=None,
        done_path=None,
    )


@dataclass(frozen=True, kw_only=True)
class RunSourcesReport:
    """一次 `run_sources` 的双源结论。字段与报告内 source 一一对应。"""

    ifs: RunReport
    gfs: RunReport

    def __post_init__(self) -> None:
        if self.ifs.source != "ifs":
            raise ValueError(
                f"RunSourcesReport.ifs.source 必须是 'ifs'，实得 {self.ifs.source!r}"
            )
        if self.gfs.source != "gfs":
            raise ValueError(
                f"RunSourcesReport.gfs.source 必须是 'gfs'，实得 {self.gfs.source!r}"
            )


class RunSourcesError(RuntimeError):
    """双源 worker 的 `RunError` 聚合。`reports`/`errors` 是构造时不可变快照。"""

    def __init__(
        self,
        reports: Mapping[str, RunReport],
        errors: Mapping[str, RunError],
    ) -> None:
        reports_snap = dict(reports)
        errors_snap = dict(errors)
        report_keys = set(reports_snap)
        error_keys = set(errors_snap)
        if report_keys & error_keys:
            raise ValueError("RunSourcesError.reports 与 errors 的键集必须互斥")
        if report_keys | error_keys != _SOURCE_KEYS or not error_keys:
            raise ValueError(
                "RunSourcesError 的 reports/errors 键集必须互斥且并集恰为 {ifs,gfs}，"
                f"且 errors 非空；实得 reports={sorted(report_keys)} "
                f"errors={sorted(error_keys)}"
            )
        for source, report in reports_snap.items():
            if not isinstance(report, RunReport) or report.source != source:
                raise ValueError(
                    f"reports[{source!r}] 必须是 source={source!r} 的 RunReport，"
                    f"实得 {report!r}"
                )
        for source, error in errors_snap.items():
            if not isinstance(error, RunError) or error.source != source:
                raise ValueError(
                    f"errors[{source!r}] 必须是 source={source!r} 的 RunError，"
                    f"实得 {error!r}"
                )
        parts = [
            f"{source}: phase={errors_snap[source].phase} {errors_snap[source]}"
            for source in _SOURCE_ORDER
            if source in errors_snap
        ]
        super().__init__("; ".join(parts))
        self._reports = MappingProxyType(reports_snap)
        self._errors = MappingProxyType(errors_snap)

    @property
    def reports(self) -> Mapping[str, RunReport]:
        return self._reports

    @property
    def errors(self) -> Mapping[str, RunError]:
        return self._errors


def _require_source_mapping(name: str, mapping: object) -> dict[str, object]:
    if not isinstance(mapping, Mapping):
        raise ValueError(  # noqa: TRY004
            f"{name} 必须是键集恰为 {{ifs,gfs}} 的 mapping，实得 {type(mapping).__name__}"
        )
    keys = set(mapping)
    if keys != _SOURCE_KEYS:
        raise ValueError(f"{name} 的键集必须恰为 {{ifs,gfs}}，实得 {sorted(keys)}")
    return {source: mapping[source] for source in _SOURCE_ORDER}


def _require_protocol(
    name: str, source: str, value: object, protocol, label: str
) -> None:
    if not isinstance(value, protocol):
        raise ValueError(  # noqa: TRY004
            f"{name}[{source!r}] 必须满足 {label}，实得 {type(value).__name__}"
        )


def _require_callable(name: str, source: str, value: object) -> None:
    if not callable(value):
        raise ValueError(  # noqa: TRY004
            f"{name}[{source!r}] 必须是 callable，实得 {type(value).__name__}"
        )


def _snapshot_inputs(
    *,
    executors: Mapping[str, JobExecutor],
    drivers: Mapping[str, AttemptDriver],
    poll_waits: Mapping[str, Callable[[], None]],
    failure_exit_codes: Mapping[str, Callable[[JobRecord], str]],
) -> tuple[
    dict[str, JobExecutor],
    dict[str, AttemptDriver],
    dict[str, Callable[[], None]],
    dict[str, Callable[[JobRecord], str]],
]:
    exec_snap = _require_source_mapping("executors", executors)
    driver_snap = _require_source_mapping("drivers", drivers)
    wait_snap = _require_source_mapping("poll_waits", poll_waits)
    provider_snap = _require_source_mapping("failure_exit_codes", failure_exit_codes)
    for source in _SOURCE_ORDER:
        _require_protocol(
            "executors", source, exec_snap[source], JobExecutor, "JobExecutor"
        )
        _require_protocol(
            "drivers", source, driver_snap[source], AttemptDriver, "AttemptDriver"
        )
        _require_callable("poll_waits", source, wait_snap[source])
        _require_callable("failure_exit_codes", source, provider_snap[source])
    if exec_snap["ifs"] is exec_snap["gfs"]:
        raise ValueError("executors 的 ifs/gfs 必须是两个不同实例")
    if driver_snap["ifs"] is driver_snap["gfs"]:
        raise ValueError("drivers 的 ifs/gfs 必须是两个不同实例")
    return exec_snap, driver_snap, wait_snap, provider_snap  # type: ignore[return-value]


def run_sources(
    *,
    config: Config,
    local: LocalConfig,
    executors: Mapping[str, JobExecutor],
    drivers: Mapping[str, AttemptDriver],
    poll_waits: Mapping[str, Callable[[], None]],
    failure_exit_codes: Mapping[str, Callable[[JobRecord], str]],
) -> RunSourcesReport:
    """组合 IFS/GFS 各一轮 `run_once`。全部 keyword-only、无默认值。"""
    from yd_producer._controller_run import run_once as run_one

    exec_snap, driver_snap, wait_snap, provider_snap = _snapshot_inputs(
        executors=executors,
        drivers=drivers,
        poll_waits=poll_waits,
        failure_exit_codes=failure_exit_codes,
    )
    publish_lock = threading.Lock()

    def _worker(source: str) -> RunReport:
        class ExecutorView:
            def submit(self, spec):
                return exec_snap[source].submit(spec)

            def poll(self, job_id: str):
                return exec_snap[source].poll(job_id)

        class DriverView:
            def prepare(self, *, request):
                return driver_snap[source].prepare(request=request)

            def collect(self, *, attempt, terminal_record):
                return driver_snap[source].collect(
                    attempt=attempt, terminal_record=terminal_record
                )

        def poll_wait() -> None:
            wait_snap[source]()

        def failure_exit_code(record: JobRecord) -> str:
            return provider_snap[source](record)

        return run_one(
            config=config,
            local=local,
            source=source,
            executor=ExecutorView(),
            driver=DriverView(),
            poll_wait=poll_wait,
            failure_exit_code=failure_exit_code,
            publish_lock=publish_lock,
        )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="yd-source") as pool:
        futures = {source: pool.submit(_worker, source) for source in _SOURCE_ORDER}
        wait(tuple(futures.values()), return_when=ALL_COMPLETED)
        collected: dict[str, RunReport | Exception] = {}
        for source in _SOURCE_ORDER:
            try:
                collected[source] = futures[source].result()
            except Exception as orig:  # noqa: BLE001
                collected[source] = orig

    reports: dict[str, RunReport] = {}
    errors: dict[str, RunError] = {}
    stray: Exception | None = None
    for source in _SOURCE_ORDER:
        item = collected[source]
        if isinstance(item, RunError):
            errors[source] = item
        elif isinstance(item, Exception):
            if stray is None:
                stray = item
        else:
            reports[source] = item
    if stray is not None:
        raise stray
    if errors:
        raise RunSourcesError(reports, errors)
    return RunSourcesReport(ifs=reports["ifs"], gfs=reports["gfs"])
