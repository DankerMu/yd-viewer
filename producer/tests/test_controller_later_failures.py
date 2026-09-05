"""Round 1 evidence: later-round failures keep prior success identity."""

from __future__ import annotations

import json
import pathlib
import threading

import pytest
from controller_sources_fixtures import (
    CYCLE_T,
    CYCLE_T12,
    CYCLE_T24,
    CYCLE_T36,
    GFS_EXIT,
    IFS_EXIT,
    IFS_JOB_T12,
    IFS_RAW_LOG,
    SOURCES,
    T_PLUS_12_TEXT,
    BarrierExecutor,
    DualBarrier,
    FailureLogHook,
    RecordingProvider,
    Sentinel,
    TerminalHookGate,
    ThrowingProvider,
    cycle_outcomes,
    done_path,
    failure_log_path,
    hooked_success_cycles,
    noop_wait,
    plant_raw_cycles,
    require_source_tuple,
    success_driver,
    work_dir,
    write_dual_tree,
)
from run_once_fixtures import HookedExecutor, job_name_for, step_clock

from yd_producer import cleanup as cleanup_module
from yd_producer.controller import RunOutcome, RunSourcesError, run_sources
from yd_producer.executor import FakeJobExecutor, FakeOutcome, JobState


def _gfs_catch_up(local, *, barrier: DualBarrier | None = None):
    plant_raw_cycles(local, "gfs", (CYCLE_T12, CYCLE_T24))
    return hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12, CYCLE_T24),
        barrier=barrier,
        wait_for_peer=barrier is not None,
    )


def _gfs_full_outcomes():
    return [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.SUCCEEDED),
        (CYCLE_T36, RunOutcome.STOPPED),
    ]


def _log_fields(local, source: str, cycle: str) -> tuple[dict, bytes]:
    raw = failure_log_path(local, source, cycle).read_bytes()
    header, body = raw.split(b"\n--- stdout/stderr ---\n", 1)
    return json.loads(header), body


def _later_fail_fake(source: str, *, state: JobState) -> FakeJobExecutor:
    return FakeJobExecutor(
        outcomes={
            job_name_for(source, CYCLE_T): FakeOutcome(
                final_state=JobState.SUCCEEDED,
                polls_until_terminal=1,
                started=True,
            ),
            job_name_for(source, CYCLE_T12): FakeOutcome(
                final_state=state,
                polls_until_terminal=1,
                started=state is not JobState.TIMEOUT,
            ),
        },
        clock=step_clock(),
    )


def _later_fail_executor(
    local,
    source: str,
    *,
    state: JobState,
    barrier: DualBarrier | None = None,
):
    fake = _later_fail_fake(source, state=state)
    driver, hook_state, slot = success_driver()
    from controller_sources_fixtures import success_hook

    success = success_hook(slot, hook_state)
    fail = FailureLogHook(
        local,
        source,
        IFS_RAW_LOG if source == "ifs" else b"gfs later\n",
        cycle=T_PLUS_12_TEXT,
    )

    def hook(*, job_id, record=None):
        if record is not None and record.state in (JobState.FAILED, JobState.TIMEOUT):
            fail(job_id=job_id, record=record)
            return
        success(job_id=job_id, record=record)

    if barrier is None:
        return driver, HookedExecutor(fake, hook)
    return driver, BarrierExecutor(
        fake,
        source=source,
        barrier=barrier,
        hook=hook,
        wait_for_peer=True,
    )


@pytest.mark.parametrize("final_state", [JobState.FAILED, JobState.TIMEOUT])
def test_later_round_job_failure_keeps_prior_success(
    tmp_path: pathlib.Path, final_state: JobState
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    barrier = DualBarrier()
    ifs_driver, ifs_exec = _later_fail_executor(
        local, "ifs", state=final_state, barrier=barrier
    )
    gfs_driver, gfs_exec = _gfs_catch_up(local, barrier=barrier)
    provider = RecordingProvider("ifs", "77:1")
    report = run_sources(
        config=config,
        local=local,
        executors={"ifs": ifs_exec, "gfs": gfs_exec},
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes={"ifs": provider, "gfs": RecordingProvider("gfs", GFS_EXIT)},
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    gfs = require_source_tuple(report.gfs, "gfs")
    assert cycle_outcomes(ifs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.JOB_FAILED),
    ]
    assert (
        ifs[0].job is not None and ifs[0].job.job_id == ifs_exec.submissions[0].job_id
    )
    assert (
        ifs[1].job is not None and ifs[1].job.job_id == ifs_exec.submissions[1].job_id
    )
    assert ifs[1].job.job_id != ifs[0].job.job_id
    assert ifs[1].cycle == CYCLE_T12
    assert ifs[1].job.state is final_state
    assert [record.name for record in ifs_exec.submissions] == [
        job_name_for("ifs", CYCLE_T),
        IFS_JOB_T12,
    ]
    assert len(provider.calls) == 1
    assert provider.calls[0].job_id == ifs[1].job.job_id
    header, body = _log_fields(local, "ifs", T_PLUS_12_TEXT)
    assert header["source"] == "ifs"
    assert header["cycle"] == T_PLUS_12_TEXT
    assert header["job_id"] == ifs[1].job.job_id
    assert header["exit_code"] == "77:1"
    assert header["state"] == final_state.value
    assert body == IFS_RAW_LOG
    assert not work_dir(local, "ifs", T_PLUS_12_TEXT).exists()
    assert done_path(local, "ifs").is_file()
    assert not done_path(local, "ifs", T_PLUS_12_TEXT).exists()
    assert cycle_outcomes(gfs) == _gfs_full_outcomes()


@pytest.mark.parametrize(
    "provider",
    [
        ThrowingProvider("ifs", RuntimeError("later provider boom")),
        ThrowingProvider("ifs", None),
        ThrowingProvider("ifs", ""),
        ThrowingProvider("ifs", "   "),
    ],
)
def test_later_round_invalid_provider_keeps_prior_success(
    tmp_path: pathlib.Path, provider: ThrowingProvider
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    barrier = DualBarrier()
    ifs_driver, ifs_exec = _later_fail_executor(
        local, "ifs", state=JobState.FAILED, barrier=barrier
    )
    gfs_driver, gfs_exec = _gfs_catch_up(local, barrier=barrier)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={"ifs": ifs_exec, "gfs": gfs_exec},
            drivers={"ifs": ifs_driver, "gfs": gfs_driver},
            poll_waits={"ifs": noop_wait, "gfs": noop_wait},
            failure_exit_codes={
                "ifs": provider,
                "gfs": RecordingProvider("gfs", GFS_EXIT),
            },
        )
    error = info.value
    ifs = require_source_tuple(error.reports["ifs"], "ifs", terminal=False)
    assert cycle_outcomes(ifs) == [(CYCLE_T, RunOutcome.SUCCEEDED)]
    assert error.errors["ifs"].phase == "cleanup"
    assert error.errors["ifs"].source == "ifs"
    assert error.errors["ifs"].cycle == CYCLE_T12
    assert error.errors["ifs"].job_id == ifs_exec.submissions[1].job_id
    assert error.errors["ifs"].job_id != ifs_exec.submissions[0].job_id
    assert done_path(local, "ifs").is_file()
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_full_outcomes()


def test_later_round_log_and_delete_failures_keep_later_job_identity(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    original = cleanup_module.finalize_failed_job

    def log_fail(inputs):
        if inputs.source == "ifs":
            raise cleanup_module.CleanupError(
                "later log boom", phase="log", path=inputs.merged_log
            )
        return original(inputs)

    monkeypatch.setattr(cleanup_module, "finalize_failed_job", log_fail)
    barrier = DualBarrier()
    ifs_driver, ifs_exec = _later_fail_executor(
        local, "ifs", state=JobState.FAILED, barrier=barrier
    )
    gfs_driver, gfs_exec = _gfs_catch_up(local, barrier=barrier)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={"ifs": ifs_exec, "gfs": gfs_exec},
            drivers={"ifs": ifs_driver, "gfs": gfs_driver},
            poll_waits={"ifs": noop_wait, "gfs": noop_wait},
            failure_exit_codes={
                "ifs": RecordingProvider("ifs", IFS_EXIT),
                "gfs": RecordingProvider("gfs", GFS_EXIT),
            },
        )
    error = info.value
    ifs = require_source_tuple(error.reports["ifs"], "ifs", terminal=False)
    assert cycle_outcomes(ifs) == [(CYCLE_T, RunOutcome.SUCCEEDED)]
    assert error.errors["ifs"].cycle == CYCLE_T12
    assert error.errors["ifs"].job_id == ifs_exec.submissions[1].job_id
    assert work_dir(local, "ifs", T_PLUS_12_TEXT).exists()
    assert not failure_log_path(local, "ifs", T_PLUS_12_TEXT).exists()
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_full_outcomes()


def test_later_round_work_delete_failure_keeps_later_log(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    original = cleanup_module.finalize_failed_job

    def work_fail(inputs):
        if inputs.source == "ifs":
            original(inputs)
            raise cleanup_module.CleanupError(
                "later work boom", phase="work", path=inputs.exact_work_dir
            )
        return original(inputs)

    monkeypatch.setattr(cleanup_module, "finalize_failed_job", work_fail)
    barrier = DualBarrier()
    ifs_driver, ifs_exec = _later_fail_executor(
        local, "ifs", state=JobState.FAILED, barrier=barrier
    )
    gfs_driver, gfs_exec = _gfs_catch_up(local, barrier=barrier)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={"ifs": ifs_exec, "gfs": gfs_exec},
            drivers={"ifs": ifs_driver, "gfs": gfs_driver},
            poll_waits={"ifs": noop_wait, "gfs": noop_wait},
            failure_exit_codes={
                "ifs": RecordingProvider("ifs", IFS_EXIT),
                "gfs": RecordingProvider("gfs", GFS_EXIT),
            },
        )
    error = info.value
    ifs = require_source_tuple(error.reports["ifs"], "ifs", terminal=False)
    assert cycle_outcomes(ifs) == [(CYCLE_T, RunOutcome.SUCCEEDED)]
    assert error.errors["ifs"].cycle == CYCLE_T12
    assert error.errors["ifs"].job_id == ifs_exec.submissions[1].job_id
    assert failure_log_path(local, "ifs", T_PLUS_12_TEXT).is_file()
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_full_outcomes()


def test_two_sources_fail_at_different_rounds_keep_independent_identity(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    plant_raw_cycles(local, "gfs", (CYCLE_T12, CYCLE_T24))
    barrier = DualBarrier()
    ifs_driver, ifs_exec = _later_fail_executor(
        local, "ifs", state=JobState.FAILED, barrier=barrier
    )
    gfs_fake = FakeJobExecutor(
        outcomes={
            job_name_for("gfs", CYCLE_T): FakeOutcome(
                final_state=JobState.FAILED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    gfs_driver, _, _ = success_driver()
    gfs_exec = BarrierExecutor(
        gfs_fake,
        source="gfs",
        barrier=barrier,
        hook=FailureLogHook(local, "gfs", b"gfs first-round\n"),
    )
    providers = {
        "ifs": RecordingProvider("ifs", IFS_EXIT),
        "gfs": RecordingProvider("gfs", GFS_EXIT),
    }
    report = run_sources(
        config=config,
        local=local,
        executors={"ifs": ifs_exec, "gfs": gfs_exec},
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=providers,
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    gfs = require_source_tuple(report.gfs, "gfs")
    assert cycle_outcomes(ifs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.JOB_FAILED),
    ]
    assert cycle_outcomes(gfs) == [(CYCLE_T, RunOutcome.JOB_FAILED)]
    ifs_log = failure_log_path(local, "ifs", T_PLUS_12_TEXT).read_bytes()
    gfs_log = failure_log_path(local, "gfs").read_bytes()
    assert ifs[1].job.job_id.encode() in ifs_log
    assert gfs[0].job.job_id.encode() in gfs_log
    assert ifs[1].job.job_id != gfs[0].job.job_id
    assert ifs[1].cycle != gfs[0].cycle
    assert IFS_EXIT.encode() in ifs_log
    assert GFS_EXIT.encode() in gfs_log
    assert not work_dir(local, "ifs", T_PLUS_12_TEXT).exists()
    assert not work_dir(local, "gfs").exists()
    assert done_path(local, "ifs").is_file()
    assert not done_path(local, "gfs").exists()


def test_mapping_snapshot_uses_original_provider_on_later_failure(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    plant_raw_cycles(local, "gfs", (CYCLE_T12, CYCLE_T24))
    entered = DualBarrier()
    gate = TerminalHookGate()
    ifs_driver, ifs_exec = _later_fail_executor(
        local, "ifs", state=JobState.FAILED, barrier=entered
    )
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12, CYCLE_T24),
        barrier=entered,
        gate=gate,
        wait_for_release=True,
    )
    waits = {"ifs": noop_wait, "gfs": noop_wait}
    original_provider = RecordingProvider("ifs", "55:9")
    providers = {
        "ifs": original_provider,
        "gfs": RecordingProvider("gfs", GFS_EXIT),
    }
    executors = {"ifs": ifs_exec, "gfs": gfs_exec}
    drivers = {"ifs": ifs_driver, "gfs": gfs_driver}
    sentinels = {
        "ifs-ex": Sentinel("ifs-ex"),
        "gfs-ex": Sentinel("gfs-ex"),
        "ifs-dr": Sentinel("ifs-dr"),
        "gfs-dr": Sentinel("gfs-dr"),
        "ifs-w": Sentinel("ifs-w"),
        "gfs-w": Sentinel("gfs-w"),
        "ifs-p": Sentinel("ifs-p"),
        "gfs-p": Sentinel("gfs-p"),
    }

    def mutator():
        for source in SOURCES:
            if not entered.entered[source].wait(timeout=5):
                raise TimeoutError(source)
        executors["ifs"] = sentinels["ifs-ex"]
        executors["gfs"] = sentinels["gfs-ex"]
        drivers["ifs"] = sentinels["ifs-dr"]
        drivers["gfs"] = sentinels["gfs-dr"]
        waits["ifs"] = sentinels["ifs-w"]
        waits["gfs"] = sentinels["gfs-w"]
        providers["ifs"] = sentinels["ifs-p"]
        providers["gfs"] = sentinels["gfs-p"]
        entered.release.set()

    thread = threading.Thread(target=mutator, daemon=True)
    thread.start()
    report = run_sources(
        config=config,
        local=local,
        executors=executors,
        drivers=drivers,
        poll_waits=waits,
        failure_exit_codes=providers,
    )
    thread.join(timeout=5)
    ifs = require_source_tuple(report.ifs, "ifs")
    gfs = require_source_tuple(report.gfs, "gfs")
    assert cycle_outcomes(ifs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.JOB_FAILED),
    ]
    assert cycle_outcomes(gfs) == _gfs_full_outcomes()
    assert len(original_provider.calls) == 1
    assert original_provider.calls[0].job_id == ifs[1].job.job_id
    header, body = _log_fields(local, "ifs", T_PLUS_12_TEXT)
    assert header["source"] == "ifs"
    assert header["cycle"] == T_PLUS_12_TEXT
    assert header["job_id"] == ifs[1].job.job_id
    assert header["exit_code"] == "55:9"
    assert body == IFS_RAW_LOG
    for sentinel in sentinels.values():
        assert sentinel.calls == []
