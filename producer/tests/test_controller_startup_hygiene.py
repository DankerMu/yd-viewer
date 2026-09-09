"""Issue #108 startup hygiene: gates, full scan, polarity, authority, names."""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
from datetime import UTC, datetime

import pytest
from controller_sources_fixtures import (
    CYCLE_T,
    CYCLE_T12,
    OLD_WORK_MARKER,
    T_PLUS_12_TEXT,
    T_TEXT,
    TerminalHookGate,
    cycle_outcomes,
    done_path,
    dual_run_kwargs,
    hooked_success,
    hooked_success_cycles,
    plant_historical_work,
    plant_raw_cycles,
    plant_regular_done,
    require_source_tuple,
    work_dir,
    work_snapshot,
    write_dual_tree,
)
from frontier_fixtures import snapshot_tree

from yd_producer import controller
from yd_producer.controller import (
    RunError,
    RunOutcome,
    RunSourcesError,
    StopReason,
    run_sources,
)
from yd_producer.store import safe_fs

HIST_EARLY = "2026082500"
HIST_LATE = "2026082600"
UNKNOWN_T = "2026082400"
CYCLE_UNKNOWN = datetime(2026, 8, 24, 0, tzinfo=UTC)
CYCLE_EARLY = datetime(2026, 8, 25, 0, tzinfo=UTC)


def _gfs_success(local, extra=(), *, gate):
    if extra:
        plant_raw_cycles(local, "gfs", extra)
        return hooked_success_cycles("gfs", (CYCLE_T, *extra), gate=gate)
    return hooked_success("gfs", gate=gate)


def _idle_ifs(*, gate):
    return hooked_success("ifs", gate=gate)


def _idle_gfs(*, gate):
    return hooked_success("gfs", gate=gate)


def _plant_done_work(local, source: str, cycle: str = HIST_LATE):
    planted = plant_historical_work(local, source, cycle)
    plant_regular_done(local, source, cycle)
    return planted


def test_invalid_mapping_prevents_all_startup_scans_and_deletes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    planted = _plant_done_work(local, "ifs")
    _plant_done_work(local, "gfs")
    before_yd = snapshot_tree(pathlib.Path(local.yd_root))
    before_scratch = snapshot_tree(pathlib.Path(local.scratch_root))
    list_calls: list[str] = []
    original_list = safe_fs.list_directory_no_follow

    def counting_list(path, *args, **kwargs):
        list_calls.append(str(path))
        return original_list(path, *args, **kwargs)

    gate = TerminalHookGate()
    kwargs = dual_run_kwargs(
        config, local, ifs=_idle_ifs(gate=gate), gfs=_gfs_success(local, gate=gate)
    )

    kwargs["executors"] = {"ifs": kwargs["executors"]["ifs"]}
    with pytest.raises(ValueError):
        run_sources(**kwargs)
    assert snapshot_tree(pathlib.Path(local.yd_root)) == before_yd
    assert snapshot_tree(pathlib.Path(local.scratch_root)) == before_scratch
    assert planted.exists()
    assert list_calls == []


def test_ifs_preflight_failure_leaves_work_while_gfs_cleans(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    ifs_work = _plant_done_work(local, "ifs")
    gfs_work = _plant_done_work(local, "gfs")
    original = controller._preflight

    def ifs_only(*, config, local, source):
        if source == "ifs":
            raise RunError(
                "配置项 `yd_root` 必须是绝对路径文本，实得 'relative-yd'",
                phase="preflight",
                source="ifs",
            )
        return original(config=config, local=local, source=source)

    monkeypatch.setattr(controller, "_preflight", ifs_only)
    gate = TerminalHookGate()
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            **dual_run_kwargs(
                config,
                local,
                ifs=_idle_ifs(gate=gate),
                gfs=_gfs_success(local, gate=gate),
            )
        )

    assert "ifs" in info.value.errors
    assert "gfs" not in info.value.errors
    assert info.value.errors["ifs"].phase == "preflight"
    assert ifs_work.exists()
    assert not gfs_work.exists()

    gfs = require_source_tuple(info.value.reports["gfs"], "gfs")
    assert gfs[0].outcome is RunOutcome.SUCCEEDED
    assert gfs[0].detail.startswith("startup cleanup:")
    assert str(gfs_work) in gfs[0].detail
    assert "startup cleanup:" not in gfs[1].detail


@pytest.mark.parametrize("occupy", ["missing", "file"])
def test_output_root_failure_stops_without_second_scan_or_delete(
    tmp_path: pathlib.Path, occupy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yd_producer import residue as residue_module
    from yd_producer._controller_run import run_once as original_run_once

    config, local = write_dual_tree(tmp_path)
    ifs_work = _plant_done_work(local, "ifs")
    gfs_work = _plant_done_work(local, "gfs")
    output = pathlib.Path(local.yd_root) / "output"
    shutil.rmtree(output)
    if occupy == "file":
        output.write_bytes(b"not-a-directory")
    before_yd = snapshot_tree(pathlib.Path(local.yd_root))
    before_scratch = snapshot_tree(pathlib.Path(local.scratch_root))
    iter_calls: list[tuple[str, bool]] = []
    original_iter = controller._iter_entry_names

    def counting_iter(directory, *, missing_is_empty):
        iter_calls.append((str(directory), missing_is_empty))
        return original_iter(directory, missing_is_empty=missing_is_empty)

    list_calls: list[str] = []
    original_list = safe_fs.list_directory_no_follow

    def counting_list(path, *args, **kwargs):
        list_calls.append(str(path))
        return original_list(path, *args, **kwargs)

    run_once_calls: list[str] = []

    def counting_run_once(**kwargs):
        run_once_calls.append(kwargs["source"])
        return original_run_once(**kwargs)

    residue_calls: list[str] = []
    original_plan = residue_module.plan_residue

    def counting_plan(*args, **kwargs):
        residue_calls.append("plan")
        return original_plan(*args, **kwargs)

    monkeypatch.setattr(controller, "_iter_entry_names", counting_iter)
    monkeypatch.setattr(safe_fs, "list_directory_no_follow", counting_list)
    monkeypatch.setattr("yd_producer._controller_run.run_once", counting_run_once)
    monkeypatch.setattr(residue_module, "plan_residue", counting_plan)
    gate = TerminalHookGate()
    report = run_sources(
        **dual_run_kwargs(
            config, local, ifs=_idle_ifs(gate=gate), gfs=_idle_gfs(gate=gate)
        )
    )

    for source, reports in (("ifs", report.ifs), ("gfs", report.gfs)):
        items = require_source_tuple(reports, source)
        assert cycle_outcomes(items) == [(None, RunOutcome.STOPPED)]
        assert items[0].stop_reason is StopReason.DISCOVERY_UNREADABLE
        assert items[0].cycle is None
        assert str(output) in items[0].detail
        assert "startup cleanup:" not in items[0].detail
    assert run_once_calls == []
    assert residue_calls == []
    assert list_calls == []
    assert iter_calls == [(str(output), False), (str(output), False)]
    assert snapshot_tree(pathlib.Path(local.yd_root)) == before_yd
    assert snapshot_tree(pathlib.Path(local.scratch_root)) == before_scratch
    assert ifs_work.exists()
    assert gfs_work.exists()


def test_out_of_order_done_directories_delete_then_earliest_unknown_stops(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yd_producer import rawscan as rawscan_module

    config, local = write_dual_tree(tmp_path)
    outside = pathlib.Path(local.scratch_root).resolve() / "nfs-outside"
    outside.mkdir()
    sentinel = outside / "keep.bin"
    sentinel.write_bytes(b"nfs-keep\n")
    unknown = plant_historical_work(local, "ifs", UNKNOWN_T, marker=b"unknown-keep\n")
    later = plant_historical_work(
        local, "ifs", HIST_LATE, marker=b"later-done\n", outside=sentinel
    )
    earlier = plant_historical_work(
        local, "ifs", HIST_EARLY, marker=b"earlier-done\n", outside=sentinel
    )
    plant_regular_done(local, "ifs", HIST_EARLY)
    plant_regular_done(local, "ifs", HIST_LATE)
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    ifs_hist_output = snapshot_tree(
        pathlib.Path(local.yd_root) / "output" / HIST_EARLY / "ifs"
    )
    ifs_hist_late = snapshot_tree(
        pathlib.Path(local.yd_root) / "output" / HIST_LATE / "ifs"
    )
    ifs_states_before = snapshot_tree(pathlib.Path(local.yd_root) / "states" / "ifs")
    frontier_calls: list[str] = []
    original_target = controller._target_and_state

    def counting_target(yd_root, source):
        frontier_calls.append(source)
        return original_target(yd_root, source)

    judge_calls: list[str] = []
    original_judge = rawscan_module.judge

    def counting_judge(*args, **kwargs):
        judge_calls.append(kwargs.get("source") or args[1])
        return original_judge(*args, **kwargs)

    monkeypatch.setattr(controller, "_target_and_state", counting_target)
    monkeypatch.setattr(rawscan_module, "judge", counting_judge)
    gate = TerminalHookGate()
    report = run_sources(
        **dual_run_kwargs(
            config,
            local,
            ifs=_idle_ifs(gate=gate),
            gfs=_gfs_success(local, extra=(CYCLE_T12,), gate=gate),
        )
    )

    ifs = require_source_tuple(report.ifs, "ifs")
    assert cycle_outcomes(ifs) == [(CYCLE_UNKNOWN, RunOutcome.STOPPED)]
    assert ifs[0].stop_reason is StopReason.UNVERIFIED_WORK_RESIDUE
    assert ifs[0].detail.startswith("startup cleanup:")
    assert ifs[0].detail.index(str(earlier)) < ifs[0].detail.index(str(later))
    assert str(unknown) not in ifs[0].detail.split("\n", 1)[0]
    assert not earlier.exists()
    assert not later.exists()
    assert unknown.exists()
    assert (unknown / "old.bin").read_bytes() == b"unknown-keep\n"
    assert sentinel.read_bytes() == b"nfs-keep\n"
    assert (
        snapshot_tree(pathlib.Path(local.yd_root) / "output" / HIST_EARLY / "ifs")
        == ifs_hist_output
    )
    assert (
        snapshot_tree(pathlib.Path(local.yd_root) / "output" / HIST_LATE / "ifs")
        == ifs_hist_late
    )
    assert (
        snapshot_tree(pathlib.Path(local.yd_root) / "states" / "ifs")
        == ifs_states_before
    )
    assert "ifs" not in frontier_calls
    assert "ifs" not in judge_calls
    gfs = require_source_tuple(report.gfs, "gfs")
    assert gfs[0].outcome is RunOutcome.SUCCEEDED


def test_unknown_work_shapes_stop_at_earliest_without_reading(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    planted = {}
    cycles = {
        "dir": UNKNOWN_T,
        "file": HIST_EARLY,
        "fifo": HIST_LATE,
        "symlink": T_TEXT,
        "dangling": T_PLUS_12_TEXT,
    }
    for shape, cycle in cycles.items():
        path = work_dir(local, "ifs", cycle)
        path.parent.mkdir(parents=True, exist_ok=True)
        if shape == "dir":
            plant_historical_work(local, "ifs", cycle, marker=b"dir-keep\n")
        elif shape == "file":
            path.write_bytes(b"file-keep\n")
        elif shape == "fifo":
            os.mkfifo(path)
        elif shape == "symlink":
            target = pathlib.Path(local.scratch_root).resolve() / "outside-work"
            target.mkdir(parents=True, exist_ok=True)
            (target / "old.bin").write_bytes(OLD_WORK_MARKER)
            path.symlink_to(target)
        else:
            path.symlink_to(pathlib.Path(local.scratch_root).resolve() / "missing-work")
        planted[shape] = path
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    before = {
        shape: work_snapshot(path) for shape, path in planted.items() if shape != "fifo"
    }
    fifo_before = planted["fifo"].lstat()
    content_reads: list[str] = []
    original_read = pathlib.Path.read_bytes

    def counting_read(self):
        work_root = pathlib.Path(local.scratch_root).resolve() / "work" / "ifs"
        if work_root in self.parents or self.parent == work_root:
            content_reads.append(str(self))
        return original_read(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", counting_read)
    gate = TerminalHookGate()
    report = run_sources(
        **dual_run_kwargs(
            config, local, ifs=_idle_ifs(gate=gate), gfs=_gfs_success(local, gate=gate)
        )
    )

    ifs = require_source_tuple(report.ifs, "ifs")
    assert ifs[0].cycle == CYCLE_UNKNOWN
    assert ifs[0].stop_reason is StopReason.UNVERIFIED_WORK_RESIDUE
    assert ifs[0].job is None
    assert content_reads == []
    for shape, path in planted.items():
        if shape == "fifo":
            now = path.lstat()
            assert stat.S_ISFIFO(now.st_mode)
            assert (now.st_dev, now.st_ino) == (fifo_before.st_dev, fifo_before.st_ino)
        else:
            assert work_snapshot(path) == before[shape]


@pytest.mark.parametrize(
    "shape",
    ["missing", "directory", "fifo", "symlink", "dangling", "parent-symlink"],
)
def test_nonregular_or_missing_done_is_unknown_not_delete(
    tmp_path: pathlib.Path, shape: str
) -> None:
    config, local = write_dual_tree(tmp_path)
    planted = plant_historical_work(local, "ifs", HIST_EARLY, marker=b"keep-unknown\n")
    done = done_path(local, "ifs", HIST_EARLY)
    done.parent.mkdir(parents=True, exist_ok=True)
    if shape == "directory":
        done.mkdir()
    elif shape == "fifo":
        os.mkfifo(done)
    elif shape == "symlink":
        real = done.parent / "real-done"
        real.write_bytes(b"")
        done.symlink_to(real)
    elif shape == "dangling":
        done.symlink_to(done.parent / "missing-done")
    elif shape == "parent-symlink":
        real_parent = done.parent.parent / "real-ifs"
        real_parent.mkdir()
        (real_parent / "DONE").write_bytes(b"")
        done.parent.rmdir()
        done.parent.symlink_to(real_parent)
    gate = TerminalHookGate()
    report = run_sources(
        **dual_run_kwargs(
            config, local, ifs=_idle_ifs(gate=gate), gfs=_gfs_success(local, gate=gate)
        )
    )

    ifs = require_source_tuple(report.ifs, "ifs")
    assert ifs[0].stop_reason is StopReason.UNVERIFIED_WORK_RESIDUE
    assert ifs[0].cycle == CYCLE_EARLY
    assert planted.exists()
    assert (planted / "old.bin").read_bytes() == b"keep-unknown\n"


@pytest.mark.parametrize("kind", ["io", "identity_changed", "indeterminate"])
def test_uncertain_done_stat_is_cleanup_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    config, local = write_dual_tree(tmp_path)
    planted = _plant_done_work(local, "ifs")
    original = safe_fs.stat_no_follow

    def boom(path, *, containment_root=None):
        if path.name == "DONE" and "ifs" in path.parts:
            raise safe_fs.SafeFilesystemError("done uncertain", kind=kind)
        return original(path, containment_root=containment_root)

    monkeypatch.setattr(safe_fs, "stat_no_follow", boom)
    gate = TerminalHookGate()
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            **dual_run_kwargs(
                config,
                local,
                ifs=_idle_ifs(gate=gate),
                gfs=_gfs_success(local, gate=gate),
            )
        )

    error = info.value.errors["ifs"]
    assert error.phase == "cleanup"
    assert planted.exists()
    assert "gfs" not in info.value.errors
    require_source_tuple(info.value.reports["gfs"], "gfs")


@pytest.mark.parametrize("shape", ["file", "fifo", "symlink", "dangling"])
def test_regular_done_does_not_unlink_nondirectory_work(
    tmp_path: pathlib.Path, shape: str
) -> None:
    config, local = write_dual_tree(tmp_path)
    path = work_dir(local, "ifs", HIST_EARLY)
    path.parent.mkdir(parents=True, exist_ok=True)
    target = pathlib.Path(local.scratch_root).resolve() / "outside-nondir"
    target.mkdir(parents=True, exist_ok=True)
    payload = target / "keep.bin"
    payload.write_bytes(b"keep-target\n")
    if shape == "file":
        path.write_bytes(b"keep-file\n")
    elif shape == "fifo":
        os.mkfifo(path)
    elif shape == "symlink":
        path.symlink_to(target)
    else:
        path.symlink_to(target / "missing")
    plant_regular_done(local, "ifs", HIST_EARLY)
    before = work_snapshot(path)
    identity = path.lstat()
    gate = TerminalHookGate()
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            **dual_run_kwargs(
                config,
                local,
                ifs=_idle_ifs(gate=gate),
                gfs=_gfs_success(local, gate=gate),
            )
        )

    error = info.value.errors["ifs"]
    assert error.phase == "cleanup"
    assert error.cycle == CYCLE_EARLY
    assert str(path) in str(error)
    now = path.lstat()
    assert (now.st_dev, now.st_ino, now.st_mode) == (
        identity.st_dev,
        identity.st_ino,
        identity.st_mode,
    )
    if shape != "fifo":
        assert work_snapshot(path) == before
    assert payload.read_bytes() == b"keep-target\n"
    if shape == "symlink":
        assert os.readlink(path) == str(target)
    elif shape == "dangling":
        assert os.readlink(path) == str(target / "missing")
    require_source_tuple(info.value.reports["gfs"], "gfs")


def test_illegal_names_are_ignored_without_nfs_probe(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    source_work = pathlib.Path(local.scratch_root).resolve() / "work" / "ifs"
    source_work.mkdir(parents=True)
    illegal = {
        "２０２６０８２５００": b"fullwidth\n",
        "202608250": b"short\n",
        "2026023100": b"bad-date\n",
        "9999123123": b"overflow\n",
        "2026082506": b"hour-06\n",
    }
    for name, payload in illegal.items():
        path = source_work / name
        path.mkdir()
        (path / "old.bin").write_bytes(payload)
    legal = _plant_done_work(local, "ifs")
    probed: list[str] = []
    original = safe_fs.stat_no_follow

    def counting(path, *, containment_root=None):
        probed.append(str(path))
        return original(path, containment_root=containment_root)

    monkeypatch.setattr(safe_fs, "stat_no_follow", counting)
    gate = TerminalHookGate()
    report = run_sources(
        **dual_run_kwargs(
            config, local, ifs=_idle_ifs(gate=gate), gfs=_gfs_success(local, gate=gate)
        )
    )

    ifs = require_source_tuple(report.ifs, "ifs")
    assert ifs[0].outcome is RunOutcome.SUCCEEDED
    assert not legal.exists()
    for name, payload in illegal.items():
        path = source_work / name
        assert path.is_dir()
        assert (path / "old.bin").read_bytes() == payload
        assert not any(name in item for item in probed)


def test_missing_work_root_is_empty_unreadable_root_is_cleanup_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    gate = TerminalHookGate()
    report = run_sources(
        **dual_run_kwargs(
            config, local, ifs=_idle_ifs(gate=gate), gfs=_gfs_success(local, gate=gate)
        )
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    assert ifs[0].outcome is RunOutcome.SUCCEEDED
    assert "startup cleanup:" not in ifs[0].detail

    config2, local2 = write_dual_tree(tmp_path / "blocked")
    source_work = pathlib.Path(local2.scratch_root).resolve() / "work" / "ifs"
    source_work.mkdir(parents=True)
    original = safe_fs.list_directory_no_follow

    def boom(path, *args, **kwargs):
        if pathlib.Path(path) == source_work:
            raise safe_fs.SafeFilesystemError("cannot list", kind="io")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(safe_fs, "list_directory_no_follow", boom)
    blocked_gate = TerminalHookGate()
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            **dual_run_kwargs(
                config2,
                local2,
                ifs=_idle_ifs(gate=blocked_gate),
                gfs=_gfs_success(local2, gate=blocked_gate),
            )
        )

    assert info.value.errors["ifs"].phase == "cleanup"
    require_source_tuple(info.value.reports["gfs"], "gfs")
