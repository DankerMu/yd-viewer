"""Round 1 ownership: shared ancestor rollback must not serialize staging."""

from __future__ import annotations

import pathlib
import threading

import pytest
from controller_sources_fixtures import (
    CYCLE_T,
    CYCLE_T12,
    CYCLE_T24,
    GFS_EXIT,
    IFS_EXIT,
    T_PLUS_12_TEXT,
    RecordingProvider,
    cycle_outcomes,
    done_path,
    fake_for,
    hooked_success_cycles,
    noop_wait,
    plant_raw_cycles,
    require_source_tuple,
    success_driver,
    work_dir,
    write_dual_tree,
)

from yd_producer import _work_claim as claim_mod
from yd_producer import rawcopy as rawcopy_module
from yd_producer.controller import RunOutcome, RunSourcesError, run_sources


def test_gfs_shared_work_pause_survives_ifs_raw_rollback(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GFS creates shared `work/` then pauses before `work/gfs`; IFS copy fails.

    IFS rollback must not remove the shared ancestor. A global staging lock
    deadlocks the walk barrier instead of proving source isolation.
    """
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    work_root = pathlib.Path(local.scratch_root).resolve() / "work"
    first_ensure = {"ifs": True, "gfs": True}
    entered = {source: threading.Event() for source in ("ifs", "gfs")}
    both_walked = threading.Event()
    gfs_paused = threading.Event()
    ifs_stage_done = threading.Event()
    work_after_ifs: list[bool] = []
    original_copy = rawcopy_module._copy_one
    original_stage = rawcopy_module.stage_raw
    original_parents = claim_mod.ensure_shared_parents

    def gated_parents(work_root_path, source):
        if first_ensure.get(source):
            first_ensure[source] = False
            entered[source].set()
            if entered["ifs"].is_set() and entered["gfs"].is_set():
                both_walked.set()
            elif not both_walked.wait(timeout=5):
                raise TimeoutError("both sources did not observe absent shared work")
        if source == "gfs" and not gfs_paused.is_set():
            from yd_producer.store import safe_fs as safe_fs_mod

            safe_fs_mod.ensure_directory_no_follow(work_root_path)
            gfs_paused.set()
            if not ifs_stage_done.wait(timeout=5):
                raise TimeoutError("IFS staging/rollback did not finish")
            work_after_ifs.append(work_root.is_dir())
        return original_parents(work_root_path, source)

    def failing_copy(source_path, target, written, *, claim=None):
        try:
            relative = pathlib.Path(target).relative_to(work_root / "ifs")
        except ValueError:
            relative = None
        if relative is not None:
            if not gfs_paused.wait(timeout=5):
                raise TimeoutError("GFS never paused before work/gfs")
            raise OSError("injected IFS copy failure")
        return original_copy(source_path, target, written, claim=claim)

    def gated_stage(*args, **kwargs):
        source = kwargs.get("source")
        if source is None and len(args) >= 4:
            source = args[3]
        try:
            return original_stage(*args, **kwargs)
        finally:
            if source == "ifs":
                ifs_stage_done.set()

    monkeypatch.setattr(rawcopy_module, "_copy_one", failing_copy)
    monkeypatch.setattr(rawcopy_module, "stage_raw", gated_stage)
    monkeypatch.setattr(claim_mod, "ensure_shared_parents", gated_parents)

    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12),
        wait_for_peer=False,
    )
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={"ifs": fake_for("ifs"), "gfs": gfs_exec},
            drivers={"ifs": ifs_driver, "gfs": gfs_driver},
            poll_waits={"ifs": noop_wait, "gfs": noop_wait},
            failure_exit_codes={
                "ifs": RecordingProvider("ifs", IFS_EXIT),
                "gfs": RecordingProvider("gfs", GFS_EXIT),
            },
        )
    error = info.value
    assert set(error.errors) == {"ifs"}
    assert error.errors["ifs"].phase == "raw"
    assert error.errors["ifs"].source == "ifs"
    assert error.reports["ifs"] == ()
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.STOPPED),
    ]
    assert work_after_ifs == [True]
    assert work_root.is_dir()
    ifs_exact = work_dir(local, "ifs")
    leftovers = list(ifs_exact.rglob("*")) if ifs_exact.exists() else []
    assert leftovers == []
    assert done_path(local, "gfs").is_file()
    assert done_path(local, "gfs", T_PLUS_12_TEXT).is_file()
    assert not done_path(local, "ifs").exists()
    assert gfs_paused.is_set()
    assert ifs_stage_done.is_set()
    assert both_walked.is_set()


def test_claimed_rawcopy_rollback_clears_own_partial_files(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claim-aware rollback must unlink own partial copies; a no-op leaves them."""
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    original_copy = rawcopy_module._copy_one
    copies = {"ifs": 0}

    def failing_second_copy(source_path, target, written, *, claim=None):
        original_copy(source_path, target, written, claim=claim)
        if claim is not None and claim.work_dir.parent.name == "ifs":
            copies["ifs"] += 1
            if copies["ifs"] >= 2:
                raise OSError("injected IFS copy failure after own files landed")

    monkeypatch.setattr(rawcopy_module, "_copy_one", failing_second_copy)
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = hooked_success_cycles("gfs", (CYCLE_T, CYCLE_T12))
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={"ifs": fake_for("ifs"), "gfs": gfs_exec},
            drivers={"ifs": ifs_driver, "gfs": gfs_driver},
            poll_waits={"ifs": noop_wait, "gfs": noop_wait},
            failure_exit_codes={
                "ifs": RecordingProvider("ifs", IFS_EXIT),
                "gfs": RecordingProvider("gfs", GFS_EXIT),
            },
        )
    error = info.value
    assert set(error.errors) == {"ifs"}
    assert error.errors["ifs"].phase == "raw"
    ifs_exact = work_dir(local, "ifs")
    leftovers = (
        [path for path in ifs_exact.rglob("*") if path.is_file()]
        if ifs_exact.exists()
        else []
    )
    assert leftovers == []
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.STOPPED),
    ]
    assert done_path(local, "gfs").is_file()
    assert done_path(local, "gfs", T_PLUS_12_TEXT).is_file()
    assert not done_path(local, "ifs").exists()
