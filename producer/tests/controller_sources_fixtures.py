"""Issue #28 dual-source fixtures: per-source driver/executor/barrier/events.

This module contains no `test_*` functions. Expected log headers are hand-authored
JSON with only the tmp-dependent `shud_binary` path spliced in.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from frontier_fixtures import YdRootBuilder
from run_once_fixtures import (
    CYCLE,
    T_PLUS_12,
    T_PLUS_24,
    T_PLUS_36,
    HookedExecutor,
    HookState,
    InProcessDriver,
    job_name_for,
    make_terminal_hook,
    step_clock,
    write_raw_cycle,
    write_state,
    write_variant,
)
from run_once_fixtures import write_config_local as _write_config_local

from yd_producer.controller import RunError, RunOutcome, RunReport
from yd_producer.executor import FakeJobExecutor, FakeOutcome, JobRecord, JobState

SOURCES = ("ifs", "gfs")
CYCLE_T = CYCLE
CYCLE_T12 = T_PLUS_12
CYCLE_T24 = T_PLUS_24
CYCLE_T36 = T_PLUS_36
T_TEXT = "2026082612"
T_PLUS_12_TEXT = "2026082700"
T_PLUS_24_TEXT = "2026082712"
T_PLUS_36_TEXT = "2026082800"
IFS_JOB = "yd-ifs-2026082612"
IFS_JOB_T12 = "yd-ifs-2026082700"
IFS_JOB_T24 = "yd-ifs-2026082712"
GFS_JOB = "yd-gfs-2026082612"
GFS_JOB_T12 = "yd-gfs-2026082700"
GFS_JOB_T24 = "yd-gfs-2026082712"
GFS_JOB_T36 = "yd-gfs-2026082800"
GFS_NEXT_JOB = "yd-gfs-2026082700"
GFS_NEXT_MINUTE = "29796480.000000"
IFS_EXIT = "42:7"
GFS_EXIT = "9:1"
IFS_RAW_LOG = b"ifs job stdout\n\xffraw-bytes\n"
GFS_RAW_LOG = b"gfs job stdout\n\x80raw-bytes\n"
SUBMITTED_AT = "2026-08-26T00:00:00+00:00"
STARTED_AT = "2026-08-26T00:00:10+00:00"
ENDED_AT = "2026-08-26T00:00:20+00:00"
TIMEOUT_ENDED_AT = "2026-08-26T00:00:10+00:00"
OLD_WORK_MARKER = b"old-work-authority-must-not-be-read\n"
FOREIGN_MARKER_NAME = "foreign-marker"
FOREIGN_MARKER_BYTES = b"foreign-orphan-evidence-must-not-be-deleted\n"


def write_dual_tree(
    tmp_path: Path, *, sources: tuple[str, ...] = SOURCES, raw: bool = True
):
    config, local = _write_config_local(tmp_path)
    for source in sources:
        write_variant(local, source=source)
        write_state(local, source=source)
        if raw:
            write_raw_cycle(local, source=source)
    return config, local


def work_dir(local, source: str, cycle: str = T_TEXT) -> Path:
    return Path(local.scratch_root).resolve() / "work" / source / cycle


def done_path(local, source: str, cycle: str = T_TEXT) -> Path:
    return Path(local.yd_root) / "output" / cycle / source / "DONE"


def state_path(local, source: str, cycle: str) -> Path:
    return Path(local.yd_root) / "states" / source / f"{cycle}.cfg.ic"


def failure_log_path(local, source: str, cycle: str = T_TEXT) -> Path:
    return Path(local.yd_root) / "logs" / source / f"{cycle}.log"


def job_name(source: str) -> str:
    return IFS_JOB if source == "ifs" else GFS_JOB


def plant_raw_cycles(local, source: str, cycles: tuple) -> None:
    for cycle in cycles:
        write_raw_cycle(local, source=source, cycle=cycle)


def outcomes_for(
    source: str,
    cycles: tuple,
    *,
    states: dict | None = None,
    polls: int = 1,
) -> dict[str, FakeOutcome]:
    outcomes: dict[str, FakeOutcome] = {}
    for cycle in cycles:
        job_state = (
            JobState.SUCCEEDED
            if states is None
            else states.get(cycle, JobState.SUCCEEDED)
        )
        outcomes[job_name_for(source, cycle)] = FakeOutcome(
            final_state=job_state,
            polls_until_terminal=polls,
            started=job_state is not JobState.TIMEOUT,
        )
    return outcomes


def fake_for_cycles(
    source: str,
    cycles: tuple,
    *,
    states: dict | None = None,
    polls: int = 1,
) -> FakeJobExecutor:
    return FakeJobExecutor(
        outcomes=outcomes_for(source, cycles, states=states, polls=polls),
        clock=step_clock(),
    )


def cycle_outcomes(reports) -> list[tuple]:
    return [(item.cycle, item.outcome) for item in reports]


def require_source_tuple(
    reports, source: str, *, terminal: bool = True
) -> tuple[RunReport, ...]:
    assert isinstance(reports, tuple), type(reports)
    for item in reports:
        assert isinstance(item, RunReport)
        assert item.source == source
    if terminal:
        assert reports, f"{source} 正常 tuple 必须至少一项"
        for item in reports[:-1]:
            assert item.outcome is RunOutcome.SUCCEEDED
        assert reports[-1].outcome is not RunOutcome.SUCCEEDED
    else:
        for item in reports:
            assert item.outcome is RunOutcome.SUCCEEDED
    return reports


def failed_header(*, shud_binary: str, source: str, exit_code: str) -> bytes:
    name = job_name(source)
    payload = (
        '{"command":['
        f"{json.dumps(shud_binary)},"
        '"--cycle","2026082612"],'
        '"cycle":"2026082612",'
        f'"ended_at":"{ENDED_AT}",'
        f'"exit_code":{json.dumps(exit_code)},'
        '"job_id":"fake-1",'
        f'"job_name":"{name}",'
        '"schema":"yd-failure-log-v1",'
        f'"source":"{source}",'
        f'"started_at":"{STARTED_AT}",'
        '"state":"FAILED",'
        f'"submitted_at":"{SUBMITTED_AT}"}}'
    )
    return payload.encode("utf-8") + b"\n--- stdout/stderr ---\n"


def timeout_header(*, shud_binary: str, source: str, exit_code: str) -> bytes:
    name = job_name(source)
    payload = (
        '{"command":['
        f"{json.dumps(shud_binary)},"
        '"--cycle","2026082612"],'
        '"cycle":"2026082612",'
        f'"ended_at":"{TIMEOUT_ENDED_AT}",'
        f'"exit_code":{json.dumps(exit_code)},'
        '"job_id":"fake-1",'
        f'"job_name":"{name}",'
        '"schema":"yd-failure-log-v1",'
        f'"source":"{source}",'
        '"started_at":null,'
        '"state":"TIMEOUT",'
        f'"submitted_at":"{SUBMITTED_AT}"}}'
    )
    return payload.encode("utf-8") + b"\n--- stdout/stderr ---\n"


class RecordingProvider:
    def __init__(self, source: str, value: str = IFS_EXIT) -> None:
        self.source = source
        self.value = value
        self.calls: list[JobRecord] = []

    def __call__(self, record: JobRecord) -> str:
        self.calls.append(record)
        return self.value


class ThrowingProvider:
    def __init__(self, source: str, result: object | BaseException) -> None:
        self.source = source
        self.result = result
        self.calls: list[JobRecord] = []

    def __call__(self, record: JobRecord):
        self.calls.append(record)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class Sentinel:
    """Cross-source throwing sentinel for mapping-snapshot tests."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    def submit(self, spec):
        self.calls.append("submit")
        raise AssertionError(f"sentinel {self.label} submit")

    def poll(self, job_id):
        self.calls.append("poll")
        raise AssertionError(f"sentinel {self.label} poll")

    def prepare(self, *, request):
        self.calls.append("prepare")
        raise AssertionError(f"sentinel {self.label} prepare")

    def collect(self, *, attempt, terminal_record):
        self.calls.append("collect")
        raise AssertionError(f"sentinel {self.label} collect")

    def __call__(self, *args, **kwargs):
        self.calls.append("call")
        raise AssertionError(f"sentinel {self.label} call")


@dataclass
class DualBarrier:
    submitted: dict[str, threading.Event] = field(
        default_factory=lambda: {source: threading.Event() for source in SOURCES}
    )
    entered: dict[str, threading.Event] = field(
        default_factory=lambda: {source: threading.Event() for source in SOURCES}
    )
    release: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    inflight: int = 0
    max_inflight: int = 0
    per_source_inflight: dict[str, int] = field(
        default_factory=lambda: {source: 0 for source in SOURCES}
    )
    per_source_max: dict[str, int] = field(
        default_factory=lambda: {source: 0 for source in SOURCES}
    )
    submissions: list[str] = field(default_factory=list)
    wait_timeout: float = 5.0


@dataclass
class TerminalHookGate:
    """Per-scenario lock for in-process `make_terminal_hook` bodies only.

    Job submit/poll, controller collect, failure cleanup, and `publish.publish`
    stay concurrent. Not module-global: one instance per `run_sources` scenario.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    _count_lock: threading.Lock = field(default_factory=threading.Lock)
    _active: int = 0
    max_active: int = 0

    def run(self, body: Callable[[], None]) -> None:
        with self.lock:
            with self._count_lock:
                self._active += 1
                self.max_active = max(self.max_active, self._active)
            try:
                body()
            finally:
                with self._count_lock:
                    self._active -= 1


class BarrierExecutor:
    """Per-source fake wrapper: first poll waits until both sources have submitted."""

    def __init__(
        self,
        inner: FakeJobExecutor,
        *,
        source: str,
        barrier: DualBarrier,
        hook: Callable | None = None,
        wait_for_release: bool = False,
        wait_for_peer: bool = True,
    ) -> None:
        self._inner = inner
        self._source = source
        self._barrier = barrier
        self._hook = hook
        self._wait_for_release = wait_for_release
        self._wait_for_peer = wait_for_peer
        self._first_poll = True
        self._previous: JobRecord | None = None
        self._fired_jobs: set[str] = set()
        self.inflight_before_submit: list[tuple[str, ...]] = []

    def submit(self, spec):
        barrier = self._barrier
        barrier.entered[self._source].set()
        if self._wait_for_release and not barrier.release.wait(
            timeout=barrier.wait_timeout
        ):
            raise TimeoutError(f"{self._source} release 未到达")
        self.inflight_before_submit.append(self._inner.inflight())
        record = self._inner.submit(spec)
        with barrier.lock:
            barrier.inflight += 1
            barrier.max_inflight = max(barrier.max_inflight, barrier.inflight)
            barrier.per_source_inflight[self._source] += 1
            barrier.per_source_max[self._source] = max(
                barrier.per_source_max[self._source],
                barrier.per_source_inflight[self._source],
            )
            barrier.submissions.append(self._source)
        barrier.submitted[self._source].set()
        self._previous = record
        return record

    def poll(self, job_id: str) -> JobRecord:
        barrier = self._barrier
        if self._first_poll and self._wait_for_peer:
            self._first_poll = False
            for source in SOURCES:
                if not barrier.submitted[source].wait(timeout=barrier.wait_timeout):
                    raise TimeoutError(
                        f"{self._source} 首次 poll 时 {source} 尚未 submit（串行实现）"
                    )
        record = self._inner.poll(job_id)
        first_for_job = (
            job_id not in self._fired_jobs
            and self._previous is not None
            and not self._previous.state.is_terminal
        )
        if (
            self._hook is not None
            and first_for_job
            and record.state is JobState.SUCCEEDED
        ):
            self._fired_jobs.add(job_id)
            self._hook(job_id=job_id)
        if (
            self._hook is not None
            and first_for_job
            and record.state in (JobState.FAILED, JobState.TIMEOUT)
        ):
            self._fired_jobs.add(job_id)
            self._hook(job_id=job_id, record=record)
        if (
            record.state.is_terminal
            and self._previous is not None
            and not self._previous.state.is_terminal
        ):
            with barrier.lock:
                barrier.inflight -= 1
                barrier.per_source_inflight[self._source] -= 1
        self._previous = record
        return record

    @property
    def submissions(self):
        return self._inner.submissions

    @property
    def max_inflight(self):
        return self._inner.max_inflight

    def inflight(self):
        return self._inner.inflight()


class FailureLogHook:
    """Write only the raw job.log on FAILED/TIMEOUT; never collect/publish products."""

    def __init__(
        self, local, source: str, payload: bytes, *, cycle: str = T_TEXT
    ) -> None:
        self.local = local
        self.source = source
        self.payload = payload
        self.cycle = cycle

    def __call__(self, *, job_id, record=None):
        path = work_dir(self.local, self.source, self.cycle) / "job.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.payload)


def fake_for(
    source: str,
    *,
    state: JobState = JobState.SUCCEEDED,
    polls: int = 1,
    started: bool = True,
    job: str | None = None,
) -> FakeJobExecutor:
    name = job_name(source) if job is None else job
    return FakeJobExecutor(
        outcomes={
            name: FakeOutcome(
                final_state=state, polls_until_terminal=polls, started=started
            )
        },
        clock=step_clock(),
    )


def success_driver() -> tuple[InProcessDriver, HookState, dict]:
    state = HookState()
    driver = InProcessDriver(state)
    slot: dict = {}
    original = driver.prepare

    def capturing(*, request):
        slot["request"] = request
        return original(request=request)

    driver.prepare = capturing  # type: ignore[method-assign]
    return driver, state, slot


def success_hook(slot: dict, state: HookState, *, gate: TerminalHookGate | None = None):
    def hook(*, job_id, record=None):
        def body() -> None:
            make_terminal_hook(slot["request"], state)()

        if gate is None:
            body()
        else:
            gate.run(body)

    return hook


def hooked_success(source: str, *, gate: TerminalHookGate | None = None):
    driver, state, slot = success_driver()
    executor = HookedExecutor(
        fake_for(source, polls=1), success_hook(slot, state, gate=gate)
    )
    return driver, executor


def hooked_success_cycles(
    source: str,
    cycles: tuple,
    *,
    barrier: DualBarrier | None = None,
    gate: TerminalHookGate | None = None,
    states: dict | None = None,
    wait_for_peer: bool = True,
    wait_for_release: bool = False,
    on_terminal: Callable | None = None,
):
    driver, hook_state, slot = success_driver()
    fake = fake_for_cycles(source, cycles, states=states)
    original_hook = success_hook(slot, hook_state, gate=gate)

    def hook(*, job_id, record=None):
        original_hook(job_id=job_id, record=record)
        if on_terminal is not None:
            on_terminal(slot["request"], job_id)

    if barrier is None:
        return driver, HookedExecutor(fake, hook)
    return driver, BarrierExecutor(
        fake,
        source=source,
        barrier=barrier,
        hook=hook,
        wait_for_peer=wait_for_peer,
        wait_for_release=wait_for_release,
    )


def noop_wait() -> None:
    return None


def plant_unknown_work(local, source: str, *, shape: str) -> Path:
    path = work_dir(local, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    if shape == "dir":
        path.mkdir()
        (path / "old.bin").write_bytes(OLD_WORK_MARKER)
    elif shape == "file":
        path.write_bytes(OLD_WORK_MARKER)
    elif shape == "symlink":
        target = Path(local.scratch_root).resolve() / "outside-work"
        target.mkdir(parents=True, exist_ok=True)
        (target / "old.bin").write_bytes(OLD_WORK_MARKER)
        path.symlink_to(target)
    elif shape == "dangling":
        path.symlink_to(Path(local.scratch_root).resolve() / "missing-work")
    else:  # pragma: no cover
        raise ValueError(shape)
    return path


def work_snapshot(path: Path) -> tuple[str, int, bytes | str]:
    info = path.lstat()
    mode = info.st_mode
    if stat.S_ISLNK(mode):
        return ("symlink", info.st_mtime_ns, os.readlink(path))
    if stat.S_ISREG(mode):
        return ("file", info.st_mtime_ns, path.read_bytes())
    if stat.S_ISDIR(mode):
        marker = path / "old.bin"
        payload = marker.read_bytes() if marker.is_file() else b""
        return ("dir", info.st_mtime_ns, payload)
    return ("other", info.st_mtime_ns, b"")


def plant_nfs_crash(local, source: str) -> None:
    builder = YdRootBuilder(root=Path(local.yd_root))
    builder.write_state(T_PLUS_12_TEXT, source)
    builder.write_output_dat(T_TEXT, source)


def raise_run_error(source: str, *, phase: str = "prepare") -> RunError:
    return RunError(f"{source} injected", phase=phase, source=source, cycle=CYCLE)
