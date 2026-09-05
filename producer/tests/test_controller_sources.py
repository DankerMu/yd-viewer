"""Issue #28 dual-source happy path: parallel jobs, isolation, publish lock, structure."""

from __future__ import annotations

import dataclasses
import inspect
import json
import pathlib
import stat
import threading

import pytest
from controller_sources_fixtures import (
    GFS_JOB,
    IFS_EXIT,
    IFS_JOB,
    IFS_RAW_LOG,
    SOURCES,
    T_PLUS_12_TEXT,
    T_TEXT,
    BarrierExecutor,
    DualBarrier,
    FailureLogHook,
    RecordingProvider,
    TerminalHookGate,
    done_path,
    failed_header,
    failure_log_path,
    fake_for,
    noop_wait,
    state_path,
    success_driver,
    success_hook,
    work_dir,
    write_dual_tree,
)

from yd_producer import controller as c
from yd_producer import publish as publish_module
from yd_producer.controller import RunOutcome, RunSourcesError, run_once, run_sources
from yd_producer.executor import JobState


def _dual_success(tmp_path: pathlib.Path, *, barrier: DualBarrier | None = None):
    config, local = write_dual_tree(tmp_path)
    barrier = barrier or DualBarrier()
    gate = TerminalHookGate()
    drivers = {}
    executors = {}
    for source in SOURCES:
        driver, state, slot = success_driver()
        drivers[source] = driver
        executors[source] = BarrierExecutor(
            fake_for(source, polls=1),
            source=source,
            barrier=barrier,
            hook=success_hook(slot, state, gate=gate),
        )
    providers = {
        "ifs": RecordingProvider("ifs", IFS_EXIT),
        "gfs": RecordingProvider("gfs", "9:1"),
    }
    waits = {"ifs": noop_wait, "gfs": noop_wait}
    return config, local, executors, drivers, waits, providers, barrier, gate


def test_dual_source_jobs_overlap_before_either_poll(tmp_path: pathlib.Path) -> None:
    config, local, executors, drivers, waits, providers, barrier, gate = _dual_success(
        tmp_path
    )
    report = run_sources(
        config=config,
        local=local,
        executors=executors,
        drivers=drivers,
        poll_waits=waits,
        failure_exit_codes=providers,
    )
    assert report.ifs.source == "ifs" and report.gfs.source == "gfs"
    assert report.ifs.outcome is RunOutcome.SUCCEEDED
    assert report.gfs.outcome is RunOutcome.SUCCEEDED
    assert barrier.max_inflight == 2
    assert gate.max_active == 1
    assert set(barrier.submissions) == {"ifs", "gfs"}
    assert len(executors["ifs"].submissions) == 1
    assert len(executors["gfs"].submissions) == 1
    assert executors["ifs"].submissions[0].name == IFS_JOB
    assert executors["gfs"].submissions[0].name == GFS_JOB
    assert done_path(local, "ifs").is_file()
    assert done_path(local, "gfs").is_file()
    assert not work_dir(local, "ifs").exists()
    assert not work_dir(local, "gfs").exists()
    assert providers["ifs"].calls == []
    assert providers["gfs"].calls == []


def test_different_cycle_identity_does_not_cross_sources(
    tmp_path: pathlib.Path,
) -> None:
    from datetime import UTC, datetime

    from cfg_ic_fixtures import build_cfg_ic
    from controller_sources_fixtures import GFS_NEXT_JOB, GFS_NEXT_MINUTE
    from frontier_fixtures import snapshot_tree
    from run_once_fixtures import write_raw_cycle

    config, local = write_dual_tree(tmp_path)
    gfs_old = pathlib.Path(local.yd_root) / "states" / "gfs" / "2026082612.cfg.ic"
    gfs_old.unlink()
    gfs_state = pathlib.Path(local.yd_root) / "states" / "gfs" / "2026082700.cfg.ic"
    gfs_state.write_bytes(
        build_cfg_ic(mesh_count=2, river_count=8, minute=GFS_NEXT_MINUTE).payload
    )
    write_raw_cycle(local, source="gfs", cycle=datetime(2026, 8, 27, 0, tzinfo=UTC))
    nwm_before = snapshot_tree(pathlib.Path(local.nwm.raw_root))
    barrier = DualBarrier()
    gate = TerminalHookGate()
    drivers = {}
    executors = {}
    for source, job in (("ifs", IFS_JOB), ("gfs", GFS_NEXT_JOB)):
        driver, state, slot = success_driver()
        drivers[source] = driver
        executors[source] = BarrierExecutor(
            fake_for(source, polls=1, job=job),
            source=source,
            barrier=barrier,
            hook=success_hook(slot, state, gate=gate),
        )
    report = run_sources(
        config=config,
        local=local,
        executors=executors,
        drivers=drivers,
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes={
            "ifs": RecordingProvider("ifs", IFS_EXIT),
            "gfs": RecordingProvider("gfs", "9:1"),
        },
    )
    assert report.ifs.cycle == datetime(2026, 8, 26, 12, tzinfo=UTC)
    assert report.gfs.cycle == datetime(2026, 8, 27, 0, tzinfo=UTC)
    assert report.ifs.source == "ifs" and report.gfs.source == "gfs"
    assert report.ifs.outcome is RunOutcome.SUCCEEDED
    assert report.gfs.outcome is RunOutcome.SUCCEEDED
    assert executors["ifs"].submissions[0].name == IFS_JOB
    assert executors["gfs"].submissions[0].name == GFS_NEXT_JOB
    assert done_path(local, "ifs", "2026082612").is_file()
    assert done_path(local, "gfs", "2026082700").is_file()
    assert not done_path(local, "ifs", "2026082700").exists()
    assert not done_path(local, "gfs", "2026082612").exists()
    assert not work_dir(local, "ifs", "2026082612").exists()
    assert not work_dir(local, "gfs", "2026082700").exists()
    assert state_path(local, "ifs", "2026082612").is_file()
    assert state_path(local, "ifs", "2026082700").is_file()
    assert not state_path(local, "ifs", "2026082712").exists()
    assert state_path(local, "gfs", "2026082700").is_file()
    assert state_path(local, "gfs", "2026082712").is_file()
    assert not state_path(local, "gfs", "2026082612").exists()
    assert barrier.max_inflight == 2
    assert gate.max_active == 1
    assert snapshot_tree(pathlib.Path(local.nwm.raw_root)) == nwm_before


def test_ifs_failed_gfs_succeeded_isolates_logs_and_products(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    barrier = DualBarrier()
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_state, gfs_slot = success_driver()
    executors = {
        "ifs": BarrierExecutor(
            fake_for("ifs", state=JobState.FAILED, polls=1),
            source="ifs",
            barrier=barrier,
            hook=FailureLogHook(local, "ifs", IFS_RAW_LOG),
        ),
        "gfs": BarrierExecutor(
            fake_for("gfs", polls=1),
            source="gfs",
            barrier=barrier,
            hook=success_hook(gfs_slot, gfs_state),
        ),
    }
    providers = {
        "ifs": RecordingProvider("ifs", IFS_EXIT),
        "gfs": RecordingProvider("gfs", "9:1"),
    }
    report = run_sources(
        config=config,
        local=local,
        executors=executors,
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=providers,
    )
    assert report.ifs.outcome is RunOutcome.JOB_FAILED
    assert report.gfs.outcome is RunOutcome.SUCCEEDED
    assert barrier.max_inflight == 2
    assert not done_path(local, "ifs").exists()
    assert done_path(local, "gfs").is_file()
    assert state_path(local, "ifs", T_TEXT).is_file()
    assert not state_path(local, "ifs", T_PLUS_12_TEXT).exists()
    assert state_path(local, "gfs", T_PLUS_12_TEXT).is_file()
    assert not work_dir(local, "ifs").exists()
    assert not work_dir(local, "gfs").exists()
    expected = failed_header(
        shud_binary=local.shud_binary, source="ifs", exit_code=IFS_EXIT
    )
    raw = failure_log_path(local, "ifs").read_bytes()
    assert raw == expected + IFS_RAW_LOG
    assert not failure_log_path(local, "gfs").exists()
    assert len(providers["ifs"].calls) == 1
    terminal = providers["ifs"].calls[0]
    assert terminal.job_id == report.ifs.job.job_id
    assert terminal.name == IFS_JOB
    assert terminal.state is JobState.FAILED
    assert providers["gfs"].calls == []
    payload = json.loads(raw.split(b"\n", 1)[0])
    assert payload["exit_code"] == "42:7"
    assert payload["job_id"] == report.ifs.job.job_id
    assert payload["source"] == "ifs"


def test_failure_cleanup_does_not_block_sibling_publish(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    barrier = DualBarrier()
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_state, gfs_slot = success_driver()
    block = threading.Event()
    gfs_publish_done = threading.Event()
    entered_cleanup = threading.Event()
    from yd_producer import cleanup as cleanup_module

    original = cleanup_module.finalize_failed_job

    def wrapped(inputs):
        if inputs.source == "ifs":
            entered_cleanup.set()
            if not block.wait(timeout=5):
                raise TimeoutError("IFS cleanup 未被放行")
        return original(inputs)

    monkeypatch.setattr(cleanup_module, "finalize_failed_job", wrapped)

    original_publish = publish_module.publish
    publish_calls: list[str] = []

    def recording_publish(inputs):
        publish_calls.append(f"enter:{inputs.source}")
        result = original_publish(inputs)
        publish_calls.append(f"exit:{inputs.source}")
        if inputs.source == "gfs":
            gfs_publish_done.set()
        return result

    monkeypatch.setattr(publish_module, "publish", recording_publish)

    def waiter():
        if not entered_cleanup.wait(timeout=5):
            raise TimeoutError("IFS 未进入 cleanup")
        if not gfs_publish_done.wait(timeout=5):
            raise TimeoutError("GFS publish 未在 IFS cleanup 放行前完成")
        assert done_path(local, "gfs").is_file()
        assert not failure_log_path(local, "ifs").exists()
        block.set()

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
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
            "gfs": BarrierExecutor(
                fake_for("gfs", polls=1),
                source="gfs",
                barrier=barrier,
                hook=success_hook(gfs_slot, gfs_state),
            ),
        },
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes={
            "ifs": RecordingProvider("ifs", IFS_EXIT),
            "gfs": RecordingProvider("gfs", "9:1"),
        },
    )
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert report.gfs.outcome is RunOutcome.SUCCEEDED
    assert report.ifs.outcome is RunOutcome.JOB_FAILED
    assert done_path(local, "gfs").is_file()
    assert failure_log_path(local, "ifs").is_file()
    assert not work_dir(local, "ifs").exists()
    assert "enter:gfs" in publish_calls
    assert "exit:gfs" in publish_calls


def test_both_sources_fail_keep_own_logs_and_delete_own_work(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    barrier = DualBarrier()
    ifs_driver, _, _ = success_driver()
    gfs_driver, _, _ = success_driver()
    providers = {
        "ifs": RecordingProvider("ifs", IFS_EXIT),
        "gfs": RecordingProvider("gfs", "9:1"),
    }
    executors = {
        "ifs": BarrierExecutor(
            fake_for("ifs", state=JobState.FAILED, polls=1),
            source="ifs",
            barrier=barrier,
            hook=FailureLogHook(local, "ifs", IFS_RAW_LOG),
        ),
        "gfs": BarrierExecutor(
            fake_for("gfs", state=JobState.FAILED, polls=1),
            source="gfs",
            barrier=barrier,
            hook=FailureLogHook(local, "gfs", b"gfs job stdout\n"),
        ),
    }
    report = run_sources(
        config=config,
        local=local,
        executors=executors,
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes=providers,
    )
    assert report.ifs.outcome is RunOutcome.JOB_FAILED
    assert report.gfs.outcome is RunOutcome.JOB_FAILED
    ifs_log = json.loads(failure_log_path(local, "ifs").read_bytes().split(b"\n", 1)[0])
    gfs_log = json.loads(failure_log_path(local, "gfs").read_bytes().split(b"\n", 1)[0])
    assert ifs_log["source"] == "ifs" and gfs_log["source"] == "gfs"
    assert ifs_log["exit_code"] == IFS_EXIT
    assert gfs_log["exit_code"] == "9:1"
    assert ifs_log["job_id"] == report.ifs.job.job_id
    assert gfs_log["job_id"] == report.gfs.job.job_id
    assert ifs_log["job_id"] == executors["ifs"].submissions[0].job_id
    assert gfs_log["job_id"] == executors["gfs"].submissions[0].job_id
    assert ifs_log["exit_code"] != gfs_log["exit_code"]
    assert not work_dir(local, "ifs").exists()
    assert not work_dir(local, "gfs").exists()
    assert not done_path(local, "ifs").exists()
    assert not done_path(local, "gfs").exists()


def test_publish_calls_never_overlap_and_keep_output_mode(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local, executors, drivers, waits, providers, barrier, gate = _dual_success(
        tmp_path
    )
    output_root = pathlib.Path(local.yd_root) / "output"
    output_root.mkdir(parents=True, exist_ok=True)
    output_root.chmod(0o2750)
    assert stat.S_IMODE(output_root.lstat().st_mode) == 0o2750

    collect_barrier = threading.Barrier(2, timeout=5)
    lock = threading.Lock()
    active = 0
    max_active = 0
    ledger: list[str] = []
    original = publish_module.publish

    for source in SOURCES:
        driver = drivers[source]
        original_collect = driver.collect

        def wrapping_collect(*, attempt, terminal_record, _orig=original_collect):
            products = _orig(attempt=attempt, terminal_record=terminal_record)
            collect_barrier.wait()
            return products

        driver.collect = wrapping_collect  # type: ignore[method-assign]

    def gated(inputs):
        nonlocal active, max_active
        source = inputs.source
        with lock:
            active += 1
            max_active = max(max_active, active)
            ledger.append(f"enter:{source}")
        try:
            return original(inputs)
        finally:
            with lock:
                ledger.append(f"exit:{source}")
                active -= 1

    monkeypatch.setattr(publish_module, "publish", gated)
    report = run_sources(
        config=config,
        local=local,
        executors=executors,
        drivers=drivers,
        poll_waits=waits,
        failure_exit_codes=providers,
    )
    assert report.ifs.outcome is RunOutcome.SUCCEEDED
    assert report.gfs.outcome is RunOutcome.SUCCEEDED
    assert max_active == 1
    assert barrier.max_inflight == 2
    assert gate.max_active == 1
    assert {item.split(":", 1)[1] for item in ledger if item.startswith("enter:")} == {
        "ifs",
        "gfs",
    }
    assert done_path(local, "ifs").is_file()
    assert done_path(local, "gfs").is_file()
    assert stat.S_IMODE(output_root.lstat().st_mode) == 0o2750
    # Distinguish fixture hook serialization from production publish serialization:
    # jobs overlapped (inflight=2) while synthetic terminal hooks did not (hook=1)
    # and production publish also did not (publish=1).
    assert (barrier.max_inflight, gate.max_active, max_active) == (2, 1, 1)


def test_join_before_error_keeps_gfs_report_and_done(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    barrier = DualBarrier()
    ifs_hold = threading.Event()
    gfs_hold = threading.Event()
    _ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_state, gfs_slot = success_driver()
    original_publish = publish_module.publish

    def gfs_done_publish(inputs):
        result = original_publish(inputs)
        if inputs.source == "gfs":
            gfs_hold.set()
        return result

    class DelayedIfsDriver:
        def prepare(self, *, request):
            ifs_hold.set()
            if not gfs_hold.wait(timeout=5):
                raise TimeoutError("GFS 未获准完成")
            raise c.RunError("ifs injected", phase="prepare", source="ifs")

        def collect(self, *, attempt, terminal_record):  # pragma: no cover
            raise AssertionError("ifs collect")

    original_prepare = gfs_driver.prepare

    def gfs_prepare(*, request):
        if not ifs_hold.wait(timeout=5):
            raise TimeoutError("IFS 未先进入错误路径")
        return original_prepare(request=request)

    gfs_driver.prepare = gfs_prepare  # type: ignore[method-assign]
    monkeypatch.setattr(publish_module, "publish", gfs_done_publish)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={
                "ifs": fake_for("ifs"),
                "gfs": BarrierExecutor(
                    fake_for("gfs", polls=1),
                    source="gfs",
                    barrier=barrier,
                    hook=success_hook(gfs_slot, gfs_state),
                    wait_for_peer=False,
                ),
            },
            drivers={"ifs": DelayedIfsDriver(), "gfs": gfs_driver},
            poll_waits={"ifs": noop_wait, "gfs": noop_wait},
            failure_exit_codes={
                "ifs": RecordingProvider("ifs"),
                "gfs": RecordingProvider("gfs", "9:1"),
            },
        )
    error = info.value
    assert set(error.errors) == {"ifs"}
    assert set(error.reports) == {"gfs"}
    assert error.errors["ifs"].phase == "prepare"
    assert error.reports["gfs"].outcome is RunOutcome.SUCCEEDED
    assert done_path(local, "gfs").is_file()
    assert (
        str(error).index("ifs:") < str(error).index("gfs:")
        if "gfs:" in str(error)
        else True
    )
    assert "ifs:" in str(error)
    assert "gfs:" not in str(error)


@pytest.mark.parametrize("final_state", [JobState.FAILED, JobState.TIMEOUT])
def test_direct_run_once_failed_keeps_work_without_provider(
    tmp_path: pathlib.Path, final_state: JobState
) -> None:
    from run_once_fixtures import (
        JOB_NAME,
        HookState,
        InProcessDriver,
        step_clock,
        write_config_local,
        write_raw_cycle,
        write_state,
        write_variant,
    )

    from yd_producer.executor import FakeJobExecutor, FakeOutcome

    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=final_state,
                polls_until_terminal=1,
                started=final_state is JobState.FAILED,
            )
        },
        clock=step_clock(),
    )
    report = run_once(
        config=config,
        local=local,
        source="gfs",
        executor=fake,
        driver=InProcessDriver(HookState()),
        poll_wait=lambda: None,
    )
    assert report.outcome is RunOutcome.JOB_FAILED
    path = pathlib.Path(local.scratch_root).resolve() / "work" / "gfs" / T_TEXT
    assert path.exists()
    assert not (pathlib.Path(local.yd_root) / "logs" / "gfs" / f"{T_TEXT}.log").exists()
    sig = inspect.signature(c.run_once)
    assert tuple(sig.parameters) == (
        "config",
        "local",
        "source",
        "executor",
        "driver",
        "poll_wait",
    )
    assert "failure_exit" not in sig.parameters


def test_public_structure_is_frozen_keyword_only() -> None:
    sig = inspect.signature(c.run_sources)
    assert tuple(sig.parameters) == (
        "config",
        "local",
        "executors",
        "drivers",
        "poll_waits",
        "failure_exit_codes",
    )
    for param in sig.parameters.values():
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty
    assert c.RunSourcesReport.__dataclass_params__.frozen is True
    assert c.RunSourcesReport.__dataclass_params__.kw_only is True
    assert tuple(f.name for f in dataclasses.fields(c.RunSourcesReport)) == (
        "ifs",
        "gfs",
    )
    from yd_producer.controller import RunReport

    def _stopped(source: str) -> RunReport:
        return RunReport(
            source=source,
            cycle=None,
            outcome=RunOutcome.STOPPED,
            stop_reason=c.StopReason.NO_INITIAL_STATE,
            detail="x",
            job=None,
            published=None,
            done_path=None,
        )

    with pytest.raises(ValueError, match="ifs"):
        c.RunSourcesReport(ifs=_stopped("gfs"), gfs=_stopped("gfs"))
    with pytest.raises(ValueError, match="gfs"):
        c.RunSourcesReport(ifs=_stopped("ifs"), gfs=_stopped("ifs"))
    ifs_err = c.RunError("boom", phase="cleanup", source="ifs")
    gfs_err = c.RunError("nope", phase="cleanup", source="gfs")
    with pytest.raises(ValueError, match="互斥"):
        c.RunSourcesError({"ifs": _stopped("ifs")}, {"ifs": ifs_err})
    with pytest.raises(ValueError, match="并集"):
        c.RunSourcesError({}, {"ifs": ifs_err})
    with pytest.raises(ValueError, match="并集"):
        c.RunSourcesError(
            {"ifs": _stopped("ifs"), "era5": _stopped("gfs")}, {"gfs": gfs_err}
        )
    with pytest.raises(ValueError, match="errors 非空"):
        c.RunSourcesError({"ifs": _stopped("ifs"), "gfs": _stopped("gfs")}, {})
    with pytest.raises(ValueError, match="reports"):
        c.RunSourcesError({"gfs": _stopped("ifs")}, {"ifs": ifs_err})
    with pytest.raises(ValueError, match="errors"):
        c.RunSourcesError({"gfs": _stopped("gfs")}, {"ifs": gfs_err})
    reports = {"gfs": _stopped("gfs")}
    errors = {"ifs": ifs_err}
    wrapped = c.RunSourcesError(reports, errors)
    reports["ifs"] = _stopped("ifs")
    errors["gfs"] = gfs_err
    assert set(wrapped.reports) == {"gfs"}
    assert set(wrapped.errors) == {"ifs"}
    assert "gfs" not in wrapped.errors
    assert wrapped.errors["ifs"].phase == "cleanup"
    for phase in (
        "preflight",
        "frontier",
        "residue",
        "raw",
        "prepare",
        "submit",
        "poll",
        "collect",
        "publish",
        "cleanup",
    ):
        assert c.RunError("x", phase=phase, source="ifs").phase == phase
    with pytest.raises(ValueError, match="phase 取值非法"):
        c.RunError("x", phase="not-a-phase", source="gfs")
    assert {reason.name: reason.value for reason in c.StopReason} == {
        "NO_INITIAL_STATE": "no_initial_state",
        "DISCOVERY_UNREADABLE": "discovery_unreadable",
        "STATE_MISSING": "state_missing",
        "STATE_UNREADABLE": "state_unreadable",
        "HEADER_TIME_MISMATCH": "header_time_mismatch",
        "RAW_INCOMPLETE": "raw_incomplete",
        "UNVERIFIED_WORK_RESIDUE": "unverified_work_residue",
    }
    annotations = c.run_sources.__annotations__
    assert annotations["return"] == "RunSourcesReport"
    assert "Config" in annotations["config"]
    assert "LocalConfig" in annotations["local"]
    assert "JobExecutor" in annotations["executors"]
    assert "AttemptDriver" in annotations["drivers"]
    assert "Callable" in annotations["poll_waits"]
    assert "JobRecord" in annotations["failure_exit_codes"]
    ok = c.RunError("x", phase="cleanup", source="ifs")
    assert ok.phase == "cleanup"
