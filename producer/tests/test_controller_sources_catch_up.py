"""Issue #28 dual-source multi-round catch-up combinations at the run_sources seam."""

from __future__ import annotations

import pathlib
import threading

import pytest
from controller_sources_fixtures import (
    CYCLE_T,
    CYCLE_T12,
    CYCLE_T24,
    CYCLE_T36,
    GFS_JOB,
    GFS_JOB_T12,
    GFS_JOB_T24,
    IFS_EXIT,
    IFS_JOB,
    IFS_JOB_T12,
    IFS_JOB_T24,
    IFS_RAW_LOG,
    T_PLUS_12_TEXT,
    T_PLUS_24_TEXT,
    T_PLUS_36_TEXT,
    T_TEXT,
    BarrierExecutor,
    DualBarrier,
    FailureLogHook,
    RecordingProvider,
    TerminalHookGate,
    cycle_outcomes,
    done_path,
    failed_header,
    failure_log_path,
    fake_for,
    hooked_success_cycles,
    noop_wait,
    plant_raw_cycles,
    require_source_tuple,
    state_path,
    success_driver,
    work_dir,
    write_dual_tree,
)
from run_once_fixtures import write_raw_cycle

from yd_producer.controller import (
    RunError,
    RunOutcome,
    RunSourcesError,
    StopReason,
    run_sources,
)
from yd_producer.executor import JobState


def _providers():
    return {
        "ifs": RecordingProvider("ifs", IFS_EXIT),
        "gfs": RecordingProvider("gfs", "9:1"),
    }


def test_ifs_first_round_failed_gfs_catches_three_then_gap(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12, CYCLE_T24))
    barrier = DualBarrier()
    gate = TerminalHookGate()
    ifs_driver, _ifs_state, _ifs_slot = success_driver()
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12, CYCLE_T24),
        barrier=barrier,
        gate=gate,
    )
    providers = _providers()
    report = run_sources(
        config=config,
        local=local,
        executors={
            "ifs": BarrierExecutor(
                fake_for("ifs", state=JobState.FAILED, polls=1),
                source="ifs",
                barrier=barrier,
                hook=FailureLogHook(local, "ifs", IFS_RAW_LOG),
            ),
            "gfs": gfs_exec,
        },
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=providers,
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    gfs = require_source_tuple(report.gfs, "gfs")
    assert cycle_outcomes(ifs) == [(CYCLE_T, RunOutcome.JOB_FAILED)]
    assert cycle_outcomes(gfs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.SUCCEEDED),
        (CYCLE_T36, RunOutcome.STOPPED),
    ]
    assert gfs[-1].stop_reason is StopReason.RAW_INCOMPLETE
    assert barrier.max_inflight == 2
    assert barrier.per_source_max == {"ifs": 1, "gfs": 1}
    assert [record.name for record in gfs_exec.submissions] == [
        GFS_JOB,
        GFS_JOB_T12,
        GFS_JOB_T24,
    ]
    assert all(item == () for item in gfs_exec.inflight_before_submit)
    assert done_path(local, "gfs", T_TEXT).is_file()
    assert done_path(local, "gfs", T_PLUS_12_TEXT).is_file()
    assert done_path(local, "gfs", T_PLUS_24_TEXT).is_file()
    assert not done_path(local, "gfs", T_PLUS_36_TEXT).exists()
    assert not done_path(local, "ifs").exists()
    assert state_path(local, "ifs", T_TEXT).is_file()
    assert not state_path(local, "ifs", T_PLUS_12_TEXT).exists()
    expected = failed_header(
        shud_binary=local.shud_binary, source="ifs", exit_code=IFS_EXIT
    )
    assert failure_log_path(local, "ifs").read_bytes() == expected + IFS_RAW_LOG
    assert not work_dir(local, "ifs").exists()
    assert len(providers["ifs"].calls) == 1
    assert providers["ifs"].calls[0].job_id == ifs[0].job.job_id
    assert providers["gfs"].calls == []


def test_sources_may_have_different_catch_up_lengths(tmp_path: pathlib.Path) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12, CYCLE_T24))
    barrier = DualBarrier()
    gate = TerminalHookGate()
    ifs_driver, ifs_exec = hooked_success_cycles(
        "ifs",
        (CYCLE_T,),
        barrier=barrier,
        gate=gate,
    )
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12, CYCLE_T24),
        barrier=barrier,
        gate=gate,
    )
    report = run_sources(
        config=config,
        local=local,
        executors={"ifs": ifs_exec, "gfs": gfs_exec},
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=_providers(),
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    gfs = require_source_tuple(report.gfs, "gfs")
    assert cycle_outcomes(ifs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert cycle_outcomes(gfs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.SUCCEEDED),
        (CYCLE_T36, RunOutcome.STOPPED),
    ]
    assert [record.name for record in ifs_exec.submissions] == [IFS_JOB]
    assert [record.name for record in gfs_exec.submissions] == [
        GFS_JOB,
        GFS_JOB_T12,
        GFS_JOB_T24,
    ]
    assert barrier.max_inflight == 2
    assert barrier.per_source_max == {"ifs": 1, "gfs": 1}
    assert all(item == () for item in ifs_exec.inflight_before_submit)
    assert all(item == () for item in gfs_exec.inflight_before_submit)
    assert done_path(local, "ifs", T_TEXT).is_file()
    assert not done_path(local, "ifs", T_PLUS_12_TEXT).exists()
    assert done_path(local, "gfs", T_PLUS_24_TEXT).is_file()
    assert not done_path(local, "gfs", T_PLUS_36_TEXT).exists()


def test_partial_reports_keep_sibling_complete_sequence(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "ifs", (CYCLE_T12, CYCLE_T24))
    plant_raw_cycles(local, "gfs", (CYCLE_T12, CYCLE_T24))
    barrier = DualBarrier()
    gate = TerminalHookGate()
    ifs_driver, ifs_exec = hooked_success_cycles(
        "ifs",
        (CYCLE_T, CYCLE_T12, CYCLE_T24),
        barrier=barrier,
        gate=gate,
    )
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12, CYCLE_T24),
        barrier=barrier,
        gate=gate,
    )
    injected = RunError(
        "ifs third-round injected",
        phase="poll",
        source="ifs",
        cycle=CYCLE_T24,
    )

    def ifs_wait() -> None:
        if len(ifs_exec.submissions) == 3:
            raise injected

    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={"ifs": ifs_exec, "gfs": gfs_exec},
            drivers={"ifs": ifs_driver, "gfs": gfs_driver},
            poll_waits={"ifs": ifs_wait, "gfs": noop_wait},
            failure_exit_codes=_providers(),
        )
    error = info.value
    assert set(error.reports) == {"ifs", "gfs"}
    assert set(error.errors) == {"ifs"}
    ifs = require_source_tuple(error.reports["ifs"], "ifs", terminal=False)
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(ifs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
    ]
    assert error.errors["ifs"] is injected
    assert cycle_outcomes(gfs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.SUCCEEDED),
        (CYCLE_T36, RunOutcome.STOPPED),
    ]
    assert [record.name for record in ifs_exec.submissions] == [
        IFS_JOB,
        IFS_JOB_T12,
        IFS_JOB_T24,
    ]
    assert [record.name for record in gfs_exec.submissions] == [
        GFS_JOB,
        GFS_JOB_T12,
        GFS_JOB_T24,
    ]
    assert done_path(local, "ifs", T_TEXT).is_file()
    assert done_path(local, "ifs", T_PLUS_12_TEXT).is_file()
    assert not done_path(local, "ifs", T_PLUS_24_TEXT).exists()
    assert done_path(local, "gfs", T_PLUS_24_TEXT).is_file()
    assert "ifs:" in str(error)
    assert "gfs:" not in str(error)


def test_dynamic_raw_arrival_is_not_a_frozen_startup_horizon(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    barrier = DualBarrier()
    gate = TerminalHookGate()
    arrivals: dict[str, list[str]] = {"ifs": [], "gfs": []}

    def on_terminal(request, job_id) -> None:
        if request.cycle == CYCLE_T:
            write_raw_cycle(local, source=request.source, cycle=CYCLE_T12)
            arrivals[request.source].append("t12")
        elif request.cycle == CYCLE_T12:
            write_raw_cycle(local, source=request.source, cycle=CYCLE_T24)
            arrivals[request.source].append("t24")

    ifs_driver, ifs_exec = hooked_success_cycles(
        "ifs",
        (CYCLE_T, CYCLE_T12, CYCLE_T24),
        barrier=barrier,
        gate=gate,
        on_terminal=on_terminal,
    )
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12, CYCLE_T24),
        barrier=barrier,
        gate=gate,
        on_terminal=on_terminal,
    )
    report = run_sources(
        config=config,
        local=local,
        executors={"ifs": ifs_exec, "gfs": gfs_exec},
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=_providers(),
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    gfs = require_source_tuple(report.gfs, "gfs")
    expected = [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.SUCCEEDED),
        (CYCLE_T36, RunOutcome.STOPPED),
    ]
    assert cycle_outcomes(ifs) == expected
    assert cycle_outcomes(gfs) == expected
    assert arrivals == {"ifs": ["t12", "t24"], "gfs": ["t12", "t24"]}
    assert [record.name for record in ifs_exec.submissions] == [
        IFS_JOB,
        IFS_JOB_T12,
        IFS_JOB_T24,
    ]
    assert [record.name for record in gfs_exec.submissions] == [
        GFS_JOB,
        GFS_JOB_T12,
        GFS_JOB_T24,
    ]
    assert barrier.max_inflight == 2
    assert barrier.per_source_max == {"ifs": 1, "gfs": 1}


def test_middle_gap_stops_then_resumes_across_calls(tmp_path: pathlib.Path) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "ifs", (CYCLE_T24,))
    plant_raw_cycles(local, "gfs", (CYCLE_T24,))
    barrier = DualBarrier()
    gate = TerminalHookGate()
    ifs_driver, ifs_exec = hooked_success_cycles(
        "ifs",
        (CYCLE_T, CYCLE_T12, CYCLE_T24),
        barrier=barrier,
        gate=gate,
    )
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12, CYCLE_T24),
        barrier=barrier,
        gate=gate,
    )
    first = run_sources(
        config=config,
        local=local,
        executors={"ifs": ifs_exec, "gfs": gfs_exec},
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=_providers(),
    )
    assert cycle_outcomes(require_source_tuple(first.ifs, "ifs")) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert cycle_outcomes(require_source_tuple(first.gfs, "gfs")) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert [record.name for record in ifs_exec.submissions] == [IFS_JOB]
    assert [record.name for record in gfs_exec.submissions] == [GFS_JOB]
    assert not done_path(local, "ifs", T_PLUS_12_TEXT).exists()
    assert not done_path(local, "gfs", T_PLUS_24_TEXT).exists()

    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    second = run_sources(
        config=config,
        local=local,
        executors={"ifs": ifs_exec, "gfs": gfs_exec},
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=_providers(),
    )
    assert cycle_outcomes(require_source_tuple(second.ifs, "ifs")) == [
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.SUCCEEDED),
        (CYCLE_T36, RunOutcome.STOPPED),
    ]
    assert cycle_outcomes(require_source_tuple(second.gfs, "gfs")) == [
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.SUCCEEDED),
        (CYCLE_T36, RunOutcome.STOPPED),
    ]
    assert [record.name for record in ifs_exec.submissions] == [
        IFS_JOB,
        IFS_JOB_T12,
        IFS_JOB_T24,
    ]
    assert [record.name for record in gfs_exec.submissions] == [
        GFS_JOB,
        GFS_JOB_T12,
        GFS_JOB_T24,
    ]
    assert done_path(local, "ifs", T_PLUS_12_TEXT).is_file()
    assert done_path(local, "gfs", T_PLUS_24_TEXT).is_file()


def test_same_source_rounds_stay_serial_while_first_rounds_overlap(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    barrier = DualBarrier()
    gate = TerminalHookGate()
    ifs_driver, ifs_exec = hooked_success_cycles(
        "ifs",
        (CYCLE_T, CYCLE_T12),
        barrier=barrier,
        gate=gate,
    )
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12),
        barrier=barrier,
        gate=gate,
    )
    report = run_sources(
        config=config,
        local=local,
        executors={"ifs": ifs_exec, "gfs": gfs_exec},
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=_providers(),
    )
    assert cycle_outcomes(require_source_tuple(report.ifs, "ifs")) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.STOPPED),
    ]
    assert cycle_outcomes(require_source_tuple(report.gfs, "gfs")) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.STOPPED),
    ]
    assert barrier.max_inflight == 2
    assert barrier.per_source_max == {"ifs": 1, "gfs": 1}
    assert ifs_exec.inflight_before_submit == [(), ()]
    assert gfs_exec.inflight_before_submit == [(), ()]
    assert gate.max_active == 1


def test_keyboard_interrupt_does_not_cancel_or_wrap_sibling(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    barrier = DualBarrier()
    gate = TerminalHookGate()
    ifs_hold = threading.Event()
    gfs_second_done = threading.Event()
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12),
        barrier=barrier,
        gate=gate,
        wait_for_peer=False,
    )
    original_prepare = gfs_driver.prepare

    def gfs_prepare(*, request):
        if not ifs_hold.wait(timeout=5):
            raise TimeoutError("IFS 未先进入 BaseException 路径")
        return original_prepare(request=request)

    gfs_driver.prepare = gfs_prepare  # type: ignore[method-assign]
    import yd_producer.publish as publish_module

    original_publish = publish_module.publish

    def marking(inputs):
        result = original_publish(inputs)
        if inputs.source == "gfs" and inputs.cycle == CYCLE_T12:
            gfs_second_done.set()
        return result

    publish_module.publish = marking

    class Boom:
        def prepare(self, *, request):
            ifs_hold.set()
            if not gfs_second_done.wait(timeout=5):
                raise TimeoutError("GFS 未完成后续轮")
            raise KeyboardInterrupt()

        def collect(self, *, attempt, terminal_record):  # pragma: no cover
            raise AssertionError("ifs collect")

    try:
        with pytest.raises(KeyboardInterrupt):
            run_sources(
                config=config,
                local=local,
                executors={"ifs": fake_for("ifs"), "gfs": gfs_exec},
                drivers={"ifs": Boom(), "gfs": gfs_driver},
                poll_waits={"ifs": noop_wait, "gfs": noop_wait},
                failure_exit_codes=_providers(),
            )
    finally:
        publish_module.publish = original_publish
    assert done_path(local, "gfs", T_TEXT).is_file()
    assert done_path(local, "gfs", T_PLUS_12_TEXT).is_file()
    assert [record.name for record in gfs_exec.submissions] == [GFS_JOB, GFS_JOB_T12]


class _SubmitBound:
    """Public executor wrapper: extra same-source submit after a terminal outcome is a fail."""

    def __init__(self, inner, *, limit: int) -> None:
        self._inner = inner
        self._limit = limit

    def submit(self, spec):
        if len(self._inner.submissions) >= self._limit:
            raise AssertionError("terminal outcome 后仍提交同源后续轮")
        return self._inner.submit(spec)

    def poll(self, job_id: str):
        return self._inner.poll(job_id)

    @property
    def submissions(self):
        return self._inner.submissions

    @property
    def inflight_before_submit(self):
        return self._inner.inflight_before_submit

    def inflight(self):
        return self._inner.inflight()


def test_job_failed_does_not_resubmit_the_failed_source(tmp_path: pathlib.Path) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    barrier = DualBarrier()
    gate = TerminalHookGate()
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12),
        barrier=barrier,
        gate=gate,
    )
    ifs_inner = BarrierExecutor(
        fake_for("ifs", state=JobState.FAILED, polls=1),
        source="ifs",
        barrier=barrier,
        hook=FailureLogHook(local, "ifs", IFS_RAW_LOG),
    )
    report = run_sources(
        config=config,
        local=local,
        executors={
            "ifs": _SubmitBound(ifs_inner, limit=1),
            "gfs": gfs_exec,
        },
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=_providers(),
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    assert cycle_outcomes(ifs) == [(CYCLE_T, RunOutcome.JOB_FAILED)]
    assert len(ifs_inner.submissions) == 1
    assert cycle_outcomes(require_source_tuple(report.gfs, "gfs")) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.STOPPED),
    ]


def test_stopped_does_not_restart_discovery(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    import yd_producer.rawscan as rawscan_module
    from yd_producer.rawscan import judge as original_judge

    config, local = write_dual_tree(tmp_path)
    barrier = DualBarrier()
    gate = TerminalHookGate()
    drivers = {}
    executors = {}
    for source in ("ifs", "gfs"):
        driver, executor = hooked_success_cycles(
            source,
            (CYCLE_T,),
            barrier=barrier,
            gate=gate,
        )
        drivers[source] = driver
        executors[source] = executor
    judge_calls: list[str] = []

    def counting_judge(*args, **kwargs):
        source = args[1] if len(args) > 1 else kwargs.get("source")
        judge_calls.append(source)
        if judge_calls.count(source) > 2:
            raise AssertionError(f"{source} STOPPED 后仍继续发现")
        return original_judge(*args, **kwargs)

    monkeypatch.setattr(rawscan_module, "judge", counting_judge)
    report = run_sources(
        config=config,
        local=local,
        executors=executors,
        drivers=drivers,
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=_providers(),
    )
    assert cycle_outcomes(require_source_tuple(report.ifs, "ifs")) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert cycle_outcomes(require_source_tuple(report.gfs, "gfs")) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert judge_calls.count("ifs") == 2
    assert judge_calls.count("gfs") == 2


def test_cleanup_pending_stops_that_source(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yd_producer.publish as publish_module

    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "ifs", (CYCLE_T12, CYCLE_T24))
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    barrier = DualBarrier()
    gate = TerminalHookGate()
    ifs_driver, ifs_exec = hooked_success_cycles(
        "ifs",
        (CYCLE_T, CYCLE_T12, CYCLE_T24),
        barrier=barrier,
        gate=gate,
    )
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12),
        barrier=barrier,
        gate=gate,
    )
    original_remove = publish_module.remove_tree_allow_symlinks

    def failing_ifs_t_work(*args, **kwargs):
        parent = str(args[0]) if args else ""
        name = str(args[1]) if len(args) > 1 else ""
        if parent.endswith("/work/ifs") and name == T_TEXT:
            raise OSError(1, "injected IFS T work removal failure")
        return original_remove(*args, **kwargs)

    monkeypatch.setattr(
        publish_module, "remove_tree_allow_symlinks", failing_ifs_t_work
    )
    report = run_sources(
        config=config,
        local=local,
        executors={"ifs": _SubmitBound(ifs_exec, limit=1), "gfs": gfs_exec},
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=_providers(),
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    assert cycle_outcomes(ifs) == [(CYCLE_T, RunOutcome.SUCCEEDED_CLEANUP_PENDING)]
    assert ifs[0].published is None
    assert ifs[0].done_path is not None and ifs[0].done_path.is_file()
    assert done_path(local, "ifs", T_TEXT).is_file()
    assert not done_path(local, "ifs", T_PLUS_12_TEXT).exists()
    assert [record.name for record in ifs_exec.submissions] == [IFS_JOB]
    assert cycle_outcomes(require_source_tuple(report.gfs, "gfs")) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.STOPPED),
    ]
