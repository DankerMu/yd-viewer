"""Issue #28 mapping/provider/cleanup/aggregate error matrix."""

from __future__ import annotations

import pathlib
import shutil
import threading

import pytest
from controller_sources_fixtures import (
    GFS_EXIT,
    IFS_EXIT,
    IFS_RAW_LOG,
    SOURCES,
    T_TEXT,
    BarrierExecutor,
    DualBarrier,
    FailureLogHook,
    RecordingProvider,
    Sentinel,
    TerminalHookGate,
    ThrowingProvider,
    done_path,
    failure_log_path,
    fake_for,
    noop_wait,
    success_driver,
    success_hook,
    work_dir,
    write_dual_tree,
)

from yd_producer import cleanup as cleanup_module
from yd_producer.controller import RunError, RunOutcome, RunSourcesError, run_sources
from yd_producer.executor import JobState


def _success_pair(tmp_path: pathlib.Path):
    config, local = write_dual_tree(tmp_path)
    barrier = DualBarrier()
    gate = TerminalHookGate()
    drivers = {}
    executors = {}
    slots = {}
    states = {}
    for source in SOURCES:
        driver, state, slot = success_driver()
        drivers[source] = driver
        slots[source] = slot
        states[source] = state
        executors[source] = BarrierExecutor(
            fake_for(source, polls=1),
            source=source,
            barrier=barrier,
            hook=success_hook(slot, state, gate=gate),
        )
    return config, local, executors, drivers, barrier, gate


@pytest.mark.parametrize(
    "mapping_name", ["executors", "drivers", "poll_waits", "failure_exit_codes"]
)
@pytest.mark.parametrize("defect", ["missing", "extra", "wrong"])
def test_mapping_keyset_rejects_before_any_discovery(
    tmp_path: pathlib.Path, mapping_name: str, defect: str
) -> None:
    from frontier_fixtures import snapshot_tree

    config, local, executors, drivers, _barrier, _gate = _success_pair(tmp_path)
    waits = {"ifs": noop_wait, "gfs": noop_wait}
    providers = {
        "ifs": RecordingProvider("ifs"),
        "gfs": RecordingProvider("gfs", GFS_EXIT),
    }
    prepare_calls: list[str] = []
    for driver in drivers.values():
        original = driver.prepare

        def counting(*, request, _orig=original):
            prepare_calls.append(request.source)
            return _orig(request=request)

        driver.prepare = counting  # type: ignore[method-assign]
    good = {
        "config": config,
        "local": local,
        "executors": executors,
        "drivers": drivers,
        "poll_waits": waits,
        "failure_exit_codes": providers,
    }
    sample = good[mapping_name]["ifs"]
    if defect == "missing":
        mutated = {"ifs": sample}
    elif defect == "extra":
        mutated = {**good[mapping_name], "era5": sample}
    else:
        mutated = {"ifs": sample, "era5": sample}
    kwargs = {**good, mapping_name: mutated}
    before_yd = snapshot_tree(pathlib.Path(local.yd_root))
    before_scratch = snapshot_tree(pathlib.Path(local.scratch_root))
    with pytest.raises(ValueError):
        run_sources(**kwargs)
    assert snapshot_tree(pathlib.Path(local.yd_root)) == before_yd
    assert snapshot_tree(pathlib.Path(local.scratch_root)) == before_scratch
    assert executors["ifs"].submissions == ()
    assert executors["gfs"].submissions == ()
    assert prepare_calls == []


def test_mapping_value_and_shared_instance_reject_before_any_discovery(
    tmp_path: pathlib.Path,
) -> None:
    from frontier_fixtures import snapshot_tree

    config, local, executors, drivers, _barrier, _gate = _success_pair(tmp_path)
    waits = {"ifs": noop_wait, "gfs": noop_wait}
    providers = {
        "ifs": RecordingProvider("ifs"),
        "gfs": RecordingProvider("gfs", GFS_EXIT),
    }
    prepare_calls: list[str] = []
    for driver in drivers.values():
        original = driver.prepare

        def counting(*, request, _orig=original):
            prepare_calls.append(request.source)
            return _orig(request=request)

        driver.prepare = counting  # type: ignore[method-assign]
    good = {
        "config": config,
        "local": local,
        "executors": executors,
        "drivers": drivers,
        "poll_waits": waits,
        "failure_exit_codes": providers,
    }
    cases = [
        {**good, "poll_waits": {"ifs": noop_wait, "gfs": "nope"}},
        {**good, "failure_exit_codes": {"ifs": providers["ifs"], "gfs": 12}},
        {**good, "executors": {"ifs": executors["ifs"], "gfs": executors["ifs"]}},
        {**good, "drivers": {"ifs": drivers["ifs"], "gfs": drivers["ifs"]}},
        {**good, "executors": {"ifs": object(), "gfs": executors["gfs"]}},
        {**good, "drivers": {"ifs": object(), "gfs": drivers["gfs"]}},
    ]
    before_yd = snapshot_tree(pathlib.Path(local.yd_root))
    before_scratch = snapshot_tree(pathlib.Path(local.scratch_root))
    for kwargs in cases:
        with pytest.raises(ValueError):
            run_sources(**kwargs)
        assert snapshot_tree(pathlib.Path(local.yd_root)) == before_yd
        assert snapshot_tree(pathlib.Path(local.scratch_root)) == before_scratch
        assert executors["ifs"].submissions == ()
        assert executors["gfs"].submissions == ()
        assert prepare_calls == []


def test_mapping_snapshot_ignores_caller_mutation_after_entry(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    entered = DualBarrier()
    gate = TerminalHookGate()
    drivers = {}
    executors = {}
    for source in SOURCES:
        driver, state, slot = success_driver()
        drivers[source] = driver
        executors[source] = BarrierExecutor(
            fake_for(source, polls=1),
            source=source,
            barrier=entered,
            hook=success_hook(slot, state, gate=gate),
            wait_for_release=True,
        )
    waits = {"ifs": noop_wait, "gfs": noop_wait}
    providers = {
        "ifs": RecordingProvider("ifs", IFS_EXIT),
        "gfs": RecordingProvider("gfs", GFS_EXIT),
    }
    original_exec = dict(executors)
    original_drv = dict(drivers)
    original_wait = dict(waits)
    original_prov = dict(providers)
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
    assert report.ifs.outcome is RunOutcome.SUCCEEDED
    assert report.gfs.outcome is RunOutcome.SUCCEEDED
    for sentinel in sentinels.values():
        assert sentinel.calls == []
    assert original_exec["ifs"] is not executors["ifs"]
    assert original_drv["ifs"] is not drivers["ifs"]
    assert original_wait["ifs"] is not waits["ifs"]
    assert original_prov["ifs"] is not providers["ifs"]
    assert entered.max_inflight == 2
    assert gate.max_active == 1


def test_provider_none_empty_whitespace_and_raise_become_cleanup_errors(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    barrier = DualBarrier()
    gfs_driver, gfs_state, gfs_slot = success_driver()
    ifs_driver, _, _ = success_driver()
    cases = [
        ThrowingProvider("ifs", RuntimeError("provider boom")),
        ThrowingProvider("ifs", None),
        ThrowingProvider("ifs", ""),
        ThrowingProvider("ifs", "   "),
    ]
    for provider in cases:
        barrier = DualBarrier()
        with pytest.raises(RunSourcesError) as info:
            run_sources(
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
                    "ifs": provider,
                    "gfs": RecordingProvider("gfs", GFS_EXIT),
                },
            )
        error = info.value
        assert error.errors["ifs"].phase == "cleanup"
        assert error.errors["ifs"].job_id == "fake-1"
        assert error.reports["gfs"].outcome is RunOutcome.SUCCEEDED
        assert done_path(local, "gfs").is_file()
        ifs_work = work_dir(local, "ifs")
        if ifs_work.exists():
            shutil.rmtree(ifs_work)
        done_path(local, "gfs").unlink()
        plus = pathlib.Path(local.yd_root) / "states" / "gfs" / "2026082700.cfg.ic"
        if plus.exists():
            plus.unlink()
        dat = pathlib.Path(local.yd_root) / "output" / T_TEXT / "gfs"
        if dat.exists():
            shutil.rmtree(dat)


def test_timeout_path_uses_provider_and_cleanup(tmp_path: pathlib.Path) -> None:
    config, local = write_dual_tree(tmp_path)
    barrier = DualBarrier()
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_state, gfs_slot = success_driver()
    provider = RecordingProvider("ifs", "0:9")
    report = run_sources(
        config=config,
        local=local,
        executors={
            "ifs": BarrierExecutor(
                fake_for("ifs", state=JobState.TIMEOUT, polls=1, started=False),
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
        failure_exit_codes={"ifs": provider, "gfs": RecordingProvider("gfs", GFS_EXIT)},
    )
    assert report.ifs.outcome is RunOutcome.JOB_FAILED
    assert report.ifs.job.state is JobState.TIMEOUT
    assert len(provider.calls) == 1
    assert provider.calls[0].state is JobState.TIMEOUT
    assert failure_log_path(local, "ifs").is_file()
    assert not work_dir(local, "ifs").exists()


def test_log_commit_failure_keeps_work_and_work_delete_failure_keeps_log(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_state, gfs_slot = success_driver()
    original = cleanup_module.finalize_failed_job

    def log_fail(inputs):
        if inputs.source == "ifs":
            raise cleanup_module.CleanupError(
                "log boom", phase="log", path=inputs.merged_log
            )
        return original(inputs)

    monkeypatch.setattr(cleanup_module, "finalize_failed_job", log_fail)
    barrier = DualBarrier()
    with pytest.raises(RunSourcesError) as info:
        run_sources(
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
                "gfs": RecordingProvider("gfs", GFS_EXIT),
            },
        )
    error = info.value
    assert error.errors["ifs"].phase == "cleanup"
    assert work_dir(local, "ifs").exists()
    assert not failure_log_path(local, "ifs").exists()
    assert error.reports["gfs"].outcome is RunOutcome.SUCCEEDED

    def work_fail(inputs):
        if inputs.source == "ifs":
            original(inputs)
            raise cleanup_module.CleanupError(
                "work boom", phase="work", path=inputs.exact_work_dir
            )
        return original(inputs)

    shutil.rmtree(work_dir(local, "ifs"))
    done_path(local, "gfs").unlink()
    plus = pathlib.Path(local.yd_root) / "states" / "gfs" / "2026082700.cfg.ic"
    if plus.exists():
        plus.unlink()
    gfs_out = pathlib.Path(local.yd_root) / "output" / T_TEXT / "gfs"
    if gfs_out.exists():
        shutil.rmtree(gfs_out)
    monkeypatch.setattr(cleanup_module, "finalize_failed_job", work_fail)
    barrier = DualBarrier()
    with pytest.raises(RunSourcesError) as info:
        run_sources(
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
                "gfs": RecordingProvider("gfs", GFS_EXIT),
            },
        )
    error = info.value
    assert error.errors["ifs"].phase == "cleanup"
    assert failure_log_path(local, "ifs").is_file()


def test_dual_run_error_order_is_ifs_then_gfs(tmp_path: pathlib.Path) -> None:
    config, local = write_dual_tree(tmp_path)

    class Boom:
        def __init__(self, source: str) -> None:
            self.source = source

        def prepare(self, *, request):
            raise RunError(f"{self.source} boom", phase="prepare", source=self.source)

        def collect(self, *, attempt, terminal_record):  # pragma: no cover
            raise AssertionError

    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={
                "ifs": fake_for("ifs"),
                "gfs": fake_for("gfs"),
            },
            drivers={"ifs": Boom("ifs"), "gfs": Boom("gfs")},
            poll_waits={"ifs": noop_wait, "gfs": noop_wait},
            failure_exit_codes={
                "ifs": RecordingProvider("ifs"),
                "gfs": RecordingProvider("gfs", GFS_EXIT),
            },
        )
    text = str(info.value)
    assert text.index("ifs:") < text.index("gfs:")
    assert set(info.value.errors) == {"ifs", "gfs"}
    assert info.value.reports == {}
