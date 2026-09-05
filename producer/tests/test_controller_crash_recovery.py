"""Issue #28 crash recovery: unknown work, lstat IO, NFS residue, operator retry."""

from __future__ import annotations

import errno
import os
import pathlib
import shutil

import pytest
from controller_sources_fixtures import (
    CYCLE_T,
    CYCLE_T12,
    CYCLE_T24,
    GFS_EXIT,
    IFS_EXIT,
    OLD_WORK_MARKER,
    T_PLUS_12_TEXT,
    T_TEXT,
    RecordingProvider,
    TerminalHookGate,
    cycle_outcomes,
    done_path,
    fake_for,
    hooked_success,
    hooked_success_cycles,
    noop_wait,
    plant_nfs_crash,
    plant_raw_cycles,
    plant_unknown_work,
    require_source_tuple,
    state_path,
    success_driver,
    work_dir,
    work_snapshot,
    write_dual_tree,
)

from yd_producer.controller import RunOutcome, RunSourcesError, StopReason, run_sources


def _success_gfs(*, gate: TerminalHookGate | None = None, extra=()):
    if extra:
        return hooked_success_cycles("gfs", (CYCLE_T, *extra), gate=gate)
    return hooked_success("gfs", gate=gate)


def _gfs_two(local, *, gate: TerminalHookGate | None = None):
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    return hooked_success_cycles("gfs", (CYCLE_T, CYCLE_T12), gate=gate)


def _gfs_two_outcomes():
    return [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.STOPPED),
    ]


@pytest.mark.parametrize("shape", ["dir", "file", "symlink", "dangling"])
@pytest.mark.parametrize("raw_complete", [True, False])
def test_unknown_work_stops_before_raw_and_leaves_work_untouched(
    tmp_path: pathlib.Path,
    shape: str,
    raw_complete: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, local = write_dual_tree(tmp_path, raw=True)
    if not raw_complete:
        ifs_raw = pathlib.Path(local.nwm.raw_root) / "IFS" / T_TEXT
        shutil.rmtree(ifs_raw)
    planted = plant_unknown_work(local, "ifs", shape=shape)
    before = work_snapshot(planted)
    import yd_producer.rawscan as rawscan_module
    from yd_producer.rawscan import judge as original_judge

    calls: list[str] = []
    content_reads: list[str] = []
    original_read = pathlib.Path.read_bytes

    def counting_judge(*args, **kwargs):
        source = args[1] if len(args) > 1 else kwargs.get("source")
        calls.append(source)
        return original_judge(*args, **kwargs)

    def counting_read(self):
        if planted == self or planted in self.parents:
            content_reads.append(str(self))
        return original_read(self)

    monkeypatch.setattr(rawscan_module, "judge", counting_judge)
    monkeypatch.setattr(pathlib.Path, "read_bytes", counting_read)
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    report = run_sources(
        config=config,
        local=local,
        executors={
            "ifs": fake_for("ifs"),
            "gfs": gfs_exec,
        },
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes={
            "ifs": RecordingProvider("ifs", IFS_EXIT),
            "gfs": RecordingProvider("gfs", GFS_EXIT),
        },
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    gfs = require_source_tuple(report.gfs, "gfs")
    assert cycle_outcomes(ifs) == [(CYCLE_T, RunOutcome.STOPPED)]
    assert ifs[0].stop_reason is StopReason.UNVERIFIED_WORK_RESIDUE
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    assert "ifs" not in calls
    assert content_reads == []
    monkeypatch.setattr(pathlib.Path, "read_bytes", original_read)
    assert work_snapshot(planted) == before
    assert not done_path(local, "ifs").exists()
    assert ifs[0].job is None
    if shape == "symlink":
        target = pathlib.Path(local.scratch_root).resolve() / "outside-work" / "old.bin"
        assert target.read_bytes() == OLD_WORK_MARKER


def test_post_check_work_marker_is_raw_error_and_preserves_foreign_tree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Race/tamper: exact work appears after UNVERIFIED check, before stage_raw.

    This is not the designed preexisting-work STOPPED case. The marker is
    planted at public `rawscan.judge` return for IFS only, after the early
    lstat-absent path, so a skipped point-of-use `_reject_preexisting_work`
    would stage into that tree and later delete the foreign evidence.
    """
    from controller_sources_fixtures import FOREIGN_MARKER_BYTES, FOREIGN_MARKER_NAME

    config, local = write_dual_tree(tmp_path)
    marker = work_dir(local, "ifs") / FOREIGN_MARKER_NAME
    import yd_producer.rawscan as rawscan_module
    from yd_producer.rawscan import judge as original_judge

    def planting_judge(*args, **kwargs):
        source = args[1] if len(args) > 1 else kwargs.get("source")
        verdict = original_judge(*args, **kwargs)
        if source == "ifs":
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_bytes(FOREIGN_MARKER_BYTES)
        return verdict

    monkeypatch.setattr(rawscan_module, "judge", planting_judge)
    ifs_driver, _, _ = success_driver()
    prepare_calls: list[str] = []
    original_prepare = ifs_driver.prepare

    def counting_prepare(*, request):
        prepare_calls.append(request.source)
        return original_prepare(request=request)

    ifs_driver.prepare = counting_prepare  # type: ignore[method-assign]
    ifs_executor = fake_for("ifs")
    gfs_driver, gfs_exec = _gfs_two(local)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={
                "ifs": ifs_executor,
                "gfs": gfs_exec,
            },
            drivers={"ifs": ifs_driver, "gfs": gfs_driver},
            poll_waits={"ifs": noop_wait, "gfs": noop_wait},
            failure_exit_codes={
                "ifs": RecordingProvider("ifs", IFS_EXIT),
                "gfs": RecordingProvider("gfs", GFS_EXIT),
            },
        )
    error = info.value
    assert set(error.reports) == {"ifs", "gfs"}
    assert error.reports["ifs"] == ()
    assert error.errors["ifs"].phase == "raw"
    assert error.errors["ifs"].source == "ifs"
    assert marker.is_file()
    assert marker.read_bytes() == FOREIGN_MARKER_BYTES
    assert list(marker.parent.iterdir()) == [marker]
    assert prepare_calls == []
    assert ifs_executor.submissions == ()
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    assert done_path(local, "gfs").is_file()
    assert done_path(local, "gfs", T_PLUS_12_TEXT).is_file()
    assert not done_path(local, "ifs").exists()


@pytest.mark.parametrize("err", [errno.EACCES, errno.EIO])
def test_lstat_io_error_is_residue_run_error_not_absent(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, err: int
) -> None:
    config, local = write_dual_tree(tmp_path)
    target = work_dir(local, "ifs")
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    original = os.lstat

    def flaky(path, *args, **kwargs):
        if pathlib.Path(path) == target:
            raise OSError(err, "io", path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", flaky)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={
                "ifs": fake_for("ifs"),
                "gfs": gfs_exec,
            },
            drivers={"ifs": ifs_driver, "gfs": gfs_driver},
            poll_waits={"ifs": noop_wait, "gfs": noop_wait},
            failure_exit_codes={
                "ifs": RecordingProvider("ifs", IFS_EXIT),
                "gfs": RecordingProvider("gfs", GFS_EXIT),
            },
        )
    error = info.value
    assert set(error.reports) == {"ifs", "gfs"}
    assert error.reports["ifs"] == ()
    assert error.errors["ifs"].phase == "residue"
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    assert done_path(local, "gfs").is_file()


def test_nfs_crash_residue_rebuilds_without_work_and_stops_when_work_present(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_nfs_crash(local, "ifs")
    plus = state_path(local, "ifs", T_PLUS_12_TEXT)
    half = pathlib.Path(local.yd_root) / "output" / T_TEXT / "ifs" / "yd.rivqdown.dat"
    assert plus.is_file()
    assert half.is_file()
    t_state = state_path(local, "ifs", T_TEXT)
    assert t_state.is_file()
    residue_plus = plus.read_bytes()
    residue_dat = half.read_bytes()
    gate = TerminalHookGate()
    ifs_driver, ifs_exec = hooked_success("ifs", gate=gate)
    gfs_driver, gfs_exec = _success_gfs(gate=gate)
    report = run_sources(
        config=config,
        local=local,
        executors={
            "ifs": ifs_exec,
            "gfs": gfs_exec,
        },
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes={
            "ifs": RecordingProvider("ifs", IFS_EXIT),
            "gfs": RecordingProvider("gfs", GFS_EXIT),
        },
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    gfs = require_source_tuple(report.gfs, "gfs")
    assert cycle_outcomes(ifs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert done_path(local, "ifs").is_file()
    rebuilt = state_path(local, "ifs", T_PLUS_12_TEXT)
    assert rebuilt.is_file()
    assert rebuilt.read_bytes() != residue_plus
    published_dat = (
        pathlib.Path(local.yd_root) / "output" / T_TEXT / "ifs" / "yd.rivqdown.dat"
    )
    assert published_dat.is_file()
    assert published_dat.read_bytes() != residue_dat
    assert t_state.is_file()
    assert not work_dir(local, "ifs").exists()
    assert cycle_outcomes(gfs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert gate.max_active == 1
    assert len(ifs_exec.submissions) == 1
    assert len(gfs_exec.submissions) == 1
    assert (
        ifs[0].job is not None and ifs[0].job.job_id == ifs_exec.submissions[0].job_id
    )
    assert (
        gfs[0].job is not None and gfs[0].job.job_id == gfs_exec.submissions[0].job_id
    )
    assert ifs_exec.submissions[0].name != gfs_exec.submissions[0].name
    assert ifs[0].source != gfs[0].source
    assert ifs[0].cycle == gfs[0].cycle
    nwm_raw = pathlib.Path(local.nwm.raw_root)
    assert nwm_raw.exists()

    # Counter-example: same crash plus unknown work -> STOPPED, no deletion of work.
    config, local = write_dual_tree(tmp_path / "counter")
    plant_nfs_crash(local, "ifs")
    planted = plant_unknown_work(local, "ifs", shape="dir")
    before = work_snapshot(planted)
    plus = state_path(local, "ifs", T_PLUS_12_TEXT)
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _success_gfs()
    report = run_sources(
        config=config,
        local=local,
        executors={
            "ifs": fake_for("ifs"),
            "gfs": gfs_exec,
        },
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes={
            "ifs": RecordingProvider("ifs", IFS_EXIT),
            "gfs": RecordingProvider("gfs", GFS_EXIT),
        },
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    assert cycle_outcomes(ifs) == [(CYCLE_T, RunOutcome.STOPPED)]
    assert ifs[0].stop_reason is StopReason.UNVERIFIED_WORK_RESIDUE
    assert work_snapshot(planted) == before
    assert not done_path(local, "ifs").exists()
    # NFS residue is still cleaned first.
    assert not plus.exists()


def test_operator_removal_allows_next_tick_to_submit_once(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    planted = plant_unknown_work(local, "ifs", shape="dir")
    marker = planted / "old.bin"
    assert marker.read_bytes() == OLD_WORK_MARKER
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _success_gfs()
    first = run_sources(
        config=config,
        local=local,
        executors={
            "ifs": fake_for("ifs"),
            "gfs": gfs_exec,
        },
        drivers={"ifs": ifs_driver, "gfs": gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes={
            "ifs": RecordingProvider("ifs", IFS_EXIT),
            "gfs": RecordingProvider("gfs", GFS_EXIT),
        },
    )
    first_ifs = require_source_tuple(first.ifs, "ifs")
    assert cycle_outcomes(first_ifs) == [(CYCLE_T, RunOutcome.STOPPED)]
    assert first_ifs[0].stop_reason is StopReason.UNVERIFIED_WORK_RESIDUE
    assert marker.read_bytes() == OLD_WORK_MARKER
    shutil.rmtree(planted)
    ifs_driver2, ifs_exec = hooked_success("ifs")
    gfs_driver2, _, _ = success_driver()
    second = run_sources(
        config=config,
        local=local,
        executors={
            "ifs": ifs_exec,
            "gfs": fake_for("gfs"),
        },
        drivers={"ifs": ifs_driver2, "gfs": gfs_driver2},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes={
            "ifs": RecordingProvider("ifs", IFS_EXIT),
            "gfs": RecordingProvider("gfs", GFS_EXIT),
        },
    )
    second_ifs = require_source_tuple(second.ifs, "ifs")
    assert cycle_outcomes(second_ifs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert len(second_ifs[0].job.job_id) > 0
    assert done_path(local, "ifs").is_file()
    assert not work_dir(local, "ifs").exists()
