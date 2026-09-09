"""Issue #108 startup hygiene: identity drift, audit notes, residue polarity."""

from __future__ import annotations

import os
import pathlib
from datetime import UTC, datetime

import pytest
from controller_sources_fixtures import (
    CYCLE_T,
    CYCLE_T12,
    FOREIGN_MARKER_BYTES,
    FOREIGN_MARKER_NAME,
    dual_run_kwargs,
    fake_for,
    hooked_success,
    hooked_success_cycles,
    plant_historical_work,
    plant_raw_cycles,
    plant_regular_done,
    replace_named_directory,
    require_source_tuple,
    success_driver,
    write_dual_tree,
)
from frontier_fixtures import parse_cycle
from run_once_fixtures import CYCLE

from yd_producer import controller, residue
from yd_producer.controller import (
    RunError,
    RunOutcome,
    RunSourcesError,
    run_once,
    run_sources,
)
from yd_producer.store import safe_fs

HIST_EARLY = "2026082500"
HIST_LATE = "2026082600"
HIST_LATER = "2026082612"
CYCLE_LATE = datetime(2026, 8, 26, 0, tzinfo=UTC)


def _gfs_success(local, extra=()):
    if extra:
        plant_raw_cycles(local, "gfs", extra)
        return hooked_success_cycles("gfs", (CYCLE_T, *extra))
    return hooked_success("gfs")


def _idle_ifs():
    return hooked_success("ifs")


def _idle_gfs():
    return hooked_success("gfs")


def _plant_done_work(local, source: str, cycle: str, *, outside=None, marker=b"done\n"):
    planted = plant_historical_work(
        local, source, cycle, marker=marker, outside=outside
    )
    plant_regular_done(local, source, cycle)
    return planted


def test_identity_swap_before_tree_open_keeps_replacement_and_records_prior_delete(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    first = _plant_done_work(local, "ifs", HIST_EARLY, marker=b"first-keep\n")
    second = _plant_done_work(local, "ifs", HIST_LATE, marker=b"second-keep\n")
    third = _plant_done_work(local, "ifs", HIST_LATER, marker=b"third-keep\n")
    original = safe_fs.directory_identity_no_follow
    swapped: list[pathlib.Path] = []

    def swap_after_freeze(path):
        identity = original(path)
        if pathlib.Path(path) == second and not swapped:
            replace_named_directory(
                second,
                marker_name=FOREIGN_MARKER_NAME,
                marker_bytes=FOREIGN_MARKER_BYTES,
            )
            swapped.append(second)
        return identity

    monkeypatch.setattr(safe_fs, "directory_identity_no_follow", swap_after_freeze)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            **dual_run_kwargs(config, local, ifs=_idle_ifs(), gfs=_gfs_success(local))
        )
    error = info.value.errors["ifs"]
    assert error.phase == "cleanup"
    assert error.cycle == CYCLE_LATE
    assert str(first) in str(error)
    assert str(second) in str(error)
    assert not first.exists()
    assert second.exists()
    assert (second / FOREIGN_MARKER_NAME).read_bytes() == FOREIGN_MARKER_BYTES
    assert third.exists()
    gfs = require_source_tuple(info.value.reports["gfs"], "gfs")
    assert gfs[0].outcome is RunOutcome.SUCCEEDED


def test_identity_swap_before_final_rmdir_keeps_replacement(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from controller_sources_fixtures import inode_pair

    config, local = write_dual_tree(tmp_path)
    first = _plant_done_work(local, "ifs", HIST_EARLY, marker=b"first-keep\n")
    second = _plant_done_work(local, "ifs", HIST_LATE, marker=b"second-keep\n")
    original_unlink = os.unlink
    injected = {"ran": False}
    replacement_identity: dict[str, tuple[int, int]] = {}

    def swap_after_last_payload(name, *args, **kwargs):
        result = original_unlink(name, *args, **kwargs)
        if (
            not injected["ran"]
            and kwargs.get("dir_fd") is not None
            and str(name) == "old.bin"
            and second.exists()
            and not (second / "old.bin").exists()
        ):
            injected["ran"] = True
            aside = second.with_name(f"{second.name}.aside-{os.getpid()}")
            second.rename(aside)
            second.mkdir()
            replacement_identity["pair"] = inode_pair(second)
        return result

    monkeypatch.setattr(os, "unlink", swap_after_last_payload)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            **dual_run_kwargs(config, local, ifs=_idle_ifs(), gfs=_gfs_success(local))
        )
    error = info.value.errors["ifs"]
    assert injected["ran"] is True
    assert error.phase == "cleanup"
    assert str(first) in str(error)
    assert str(second) in str(error)
    assert not first.exists()
    assert second.exists()
    assert list(second.iterdir()) == []
    assert inode_pair(second) == replacement_identity["pair"]
    require_source_tuple(info.value.reports["gfs"], "gfs")


def test_internal_symlink_unlinks_link_not_external_target(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    outside = pathlib.Path(local.scratch_root).resolve() / "external-keep"
    outside.mkdir()
    target = outside / "keep.bin"
    target.write_bytes(b"external-keep\n")
    planted = _plant_done_work(
        local, "ifs", HIST_LATE, outside=target, marker=b"inner\n"
    )
    report = run_sources(
        **dual_run_kwargs(config, local, ifs=_idle_ifs(), gfs=_gfs_success(local))
    )
    ifs = require_source_tuple(report.ifs, "ifs")
    assert ifs[0].outcome is RunOutcome.SUCCEEDED
    assert not planted.exists()
    assert target.read_bytes() == b"external-keep\n"


def test_pre_report_run_error_keeps_original_object_and_one_audit_note(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yd_producer._controller_run import run_once as real_run_once

    config, local = write_dual_tree(tmp_path)
    first = _plant_done_work(local, "ifs", HIST_EARLY)
    second = _plant_done_work(local, "ifs", HIST_LATE)
    cause = OSError("IFS-CAUSE-108")
    original = RunError("IFS-BODY-108", phase="prepare", source="ifs", cycle=CYCLE)
    original.__cause__ = cause
    original.add_note("IFS-EXISTING-NOTE")

    def boom(**kwargs):
        if kwargs["source"] == "ifs":
            raise original
        return real_run_once(**kwargs)

    monkeypatch.setattr("yd_producer._controller_run.run_once", boom)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            **dual_run_kwargs(config, local, ifs=_idle_ifs(), gfs=_gfs_success(local))
        )
    caught = info.value.errors["ifs"]
    assert caught is original
    assert caught.__cause__ is cause
    notes = getattr(caught, "__notes__", ())
    assert notes[0] == "IFS-EXISTING-NOTE"
    assert len(notes) == 2
    assert notes[1].startswith("startup cleanup:")
    assert notes[1].index(str(first)) < notes[1].index(str(second))
    assert str(info.value).count("IFS-BODY-108") == 1
    assert str(info.value).count("IFS-EXISTING-NOTE") == 1
    assert str(info.value).count("startup cleanup:") == 1
    assert not first.exists()
    assert not second.exists()
    require_source_tuple(info.value.reports["gfs"], "gfs")


def test_first_succeeded_carries_audit_later_report_and_error_do_not(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yd_producer._controller_run import run_once as real_run_once

    config, local = write_dual_tree(tmp_path)
    first = _plant_done_work(local, "ifs", HIST_EARLY)
    second = _plant_done_work(local, "ifs", HIST_LATE)
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    boom = RunError("IFS-LATER-BODY", phase="prepare", source="ifs", cycle=CYCLE_T12)
    calls = {"n": 0}

    def maybe_boom(**kwargs):
        if kwargs["source"] != "ifs":
            return real_run_once(**kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            return real_run_once(**kwargs)
        raise boom

    monkeypatch.setattr("yd_producer._controller_run.run_once", maybe_boom)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            **dual_run_kwargs(
                config,
                local,
                ifs=hooked_success_cycles("ifs", (CYCLE_T,)),
                gfs=_gfs_success(local),
            )
        )
    reports = require_source_tuple(info.value.reports["ifs"], "ifs", terminal=False)
    assert reports[0].outcome is RunOutcome.SUCCEEDED
    assert reports[0].detail.startswith("startup cleanup:")
    assert str(first) in reports[0].detail
    assert str(second) in reports[0].detail
    later = info.value.errors["ifs"]
    assert later is boom
    assert "startup cleanup:" not in str(later)
    assert getattr(later, "__notes__", ()) == ()
    assert not first.exists()
    assert not second.exists()


def test_second_tick_does_not_repeat_audit_or_delete(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    planted = _plant_done_work(local, "ifs", HIST_LATE)
    first = run_sources(
        **dual_run_kwargs(config, local, ifs=_idle_ifs(), gfs=_gfs_success(local))
    )
    ifs_first = require_source_tuple(first.ifs, "ifs")
    assert ifs_first[0].detail.startswith("startup cleanup:")
    assert not planted.exists()
    second = run_sources(
        **dual_run_kwargs(config, local, ifs=_idle_ifs(), gfs=_idle_gfs())
    )
    ifs_second = require_source_tuple(second.ifs, "ifs")
    assert "startup cleanup:" not in ifs_second[0].detail


def test_hygiene_runs_once_not_per_iteration(
    tmp_path: pathlib.Path,
) -> None:
    from controller_sources_fixtures import work_dir

    config, local = write_dual_tree(tmp_path)
    planted = _plant_done_work(local, "ifs", HIST_LATE)
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    mid_tick = work_dir(local, "ifs", HIST_EARLY)

    def plant_during_first_attempt(request, job_id):
        del request, job_id
        if mid_tick.exists():
            return
        plant_historical_work(local, "ifs", HIST_EARLY, marker=b"mid-tick\n")
        plant_regular_done(local, "ifs", HIST_EARLY)

    ifs = hooked_success_cycles(
        "ifs", (CYCLE_T, CYCLE_T12), on_terminal=plant_during_first_attempt
    )
    report = run_sources(
        **dual_run_kwargs(
            config,
            local,
            ifs=ifs,
            gfs=_gfs_success(local, extra=(CYCLE_T12,)),
        )
    )
    ifs_reports = require_source_tuple(report.ifs, "ifs")
    assert ifs_reports[0].outcome is RunOutcome.SUCCEEDED
    assert ifs_reports[1].outcome is RunOutcome.SUCCEEDED
    assert ifs_reports[0].detail.startswith("startup cleanup:")
    assert "startup cleanup:" not in ifs_reports[1].detail
    assert not planted.exists()
    assert mid_tick.exists()
    assert (mid_tick / "old.bin").read_bytes() == b"mid-tick\n"
    second = run_sources(
        **dual_run_kwargs(config, local, ifs=_idle_ifs(), gfs=_idle_gfs())
    )
    ifs_second = require_source_tuple(second.ifs, "ifs")
    assert ifs_second[0].detail.startswith("startup cleanup:")
    assert str(mid_tick) in ifs_second[0].detail
    assert not mid_tick.exists()


def test_residue_done_polarity_and_direct_run_once_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    from yd_producer.executor import JobState

    config, local = write_dual_tree(tmp_path)
    planted = _plant_done_work(local, "ifs", HIST_LATE)
    t_done = pathlib.Path(local.yd_root).joinpath("output", "2026082612", "ifs", "DONE")
    t_done.parent.mkdir(parents=True, exist_ok=True)
    t_done.write_bytes(b"")
    handed = controller.FrontierDecision(
        source="ifs",
        cycle=parse_cycle("2026082612"),
        stop_reason=None,
        detail="13.2 reuse: handed T already has DONE",
    )
    plan = residue.plan_residue(
        yd_root=pathlib.Path(local.yd_root), source="ifs", decision=handed
    )
    assert plan is not None
    assert plan.empty
    t_done.unlink()
    driver, _, _ = success_driver()
    report = run_once(
        config=config,
        local=local,
        source="ifs",
        executor=fake_for("ifs", state=JobState.FAILED, polls=0),
        driver=driver,
        poll_wait=lambda: None,
    )
    assert report.outcome is RunOutcome.JOB_FAILED
    assert planted.exists()


def test_aggregate_notes_render_each_item_once(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    ifs_work = _plant_done_work(local, "ifs", HIST_LATE)
    gfs_work = _plant_done_work(local, "gfs", HIST_LATE)
    ifs_error = RunError("IFS-AGG-BODY", phase="prepare", source="ifs", cycle=CYCLE)
    ifs_error.add_note("IFS-OWN-NOTE")
    gfs_error = RunError("GFS-AGG-BODY", phase="collect", source="gfs", cycle=CYCLE)
    gfs_error.add_note("GFS-OWN-NOTE")

    def boom(**kwargs):
        if kwargs["source"] == "ifs":
            raise ifs_error
        raise gfs_error

    monkeypatch.setattr("yd_producer._controller_run.run_once", boom)
    with pytest.raises(RunSourcesError) as info:
        run_sources(**dual_run_kwargs(config, local, ifs=_idle_ifs(), gfs=_idle_gfs()))
    text = str(info.value)
    assert info.value.errors["ifs"] is ifs_error
    assert info.value.errors["gfs"] is gfs_error
    assert text.index("ifs:") < text.index("gfs:")
    for token in (
        "IFS-AGG-BODY",
        "IFS-OWN-NOTE",
        "GFS-AGG-BODY",
        "GFS-OWN-NOTE",
        str(ifs_work),
        str(gfs_work),
    ):
        assert text.count(token) == 1
    assert text.count("startup cleanup:") == 2
    assert not ifs_work.exists()
    assert not gfs_work.exists()
