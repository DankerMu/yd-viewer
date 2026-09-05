"""Round 1 ownership: exclusive exact-root claim after the final preexisting guard."""

from __future__ import annotations

import inspect
import pathlib

import pytest
from controller_sources_fixtures import (
    CYCLE_T,
    CYCLE_T12,
    CYCLE_T24,
    FOREIGN_MARKER_BYTES,
    FOREIGN_MARKER_NAME,
    GFS_EXIT,
    IFS_EXIT,
    T_PLUS_12_TEXT,
    RecordingProvider,
    cycle_outcomes,
    done_path,
    hooked_success_cycles,
    inode_pair,
    noop_wait,
    plant_raw_cycles,
    require_source_tuple,
    success_driver,
    work_dir,
    write_dual_tree,
)

from yd_producer import _controller_run as run_mod
from yd_producer import cleanup as cleanup_module
from yd_producer import publish as publish_module
from yd_producer import rawcopy as rawcopy_module
from yd_producer.controller import RunOutcome, RunSourcesError, run_sources
from yd_producer.store import safe_fs


def _gfs_two(local):
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    return hooked_success_cycles("gfs", (CYCLE_T, CYCLE_T12))


def _gfs_two_outcomes():
    return [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.STOPPED),
    ]


def test_final_guard_then_foreign_exact_root_is_raw_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final `_reject_preexisting_work` passed; competitor then wins exact root.

    Ownership is only the exclusive claim, not the earlier absence check.
    """
    config, local = write_dual_tree(tmp_path)
    marker = work_dir(local, "ifs") / FOREIGN_MARKER_NAME
    planted: dict[str, object] = {}
    original = run_mod.reject_preexisting_work

    def plant_after_guard(work_path, source, cycle):
        original(work_path, source, cycle)
        if source == "ifs" and "inode" not in planted:
            work_path.mkdir(parents=True)
            marker.write_bytes(FOREIGN_MARKER_BYTES)
            planted["inode"] = inode_pair(marker)

    monkeypatch.setattr(run_mod, "reject_preexisting_work", plant_after_guard)
    ifs_driver, _, _ = success_driver()
    prepare_calls: list[str] = []
    original_prepare = ifs_driver.prepare

    def counting_prepare(*, request):
        prepare_calls.append(request.source)
        return original_prepare(request=request)

    ifs_driver.prepare = counting_prepare  # type: ignore[method-assign]
    from controller_sources_fixtures import fake_for

    ifs_executor = fake_for("ifs")
    gfs_driver, gfs_exec = _gfs_two(local)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={"ifs": ifs_executor, "gfs": gfs_exec},
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
    assert error.errors["ifs"].cycle == CYCLE_T
    assert marker.is_file()
    assert marker.read_bytes() == FOREIGN_MARKER_BYTES
    assert inode_pair(marker) == planted["inode"]
    assert list(marker.parent.iterdir()) == [marker]
    assert prepare_calls == []
    assert ifs_executor.submissions == ()
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    assert done_path(local, "gfs").is_file()
    assert done_path(local, "gfs", T_PLUS_12_TEXT).is_file()
    assert not done_path(local, "ifs").exists()


def test_additive_claim_parameters_are_keyword_only_last_default_none() -> None:
    raw_params = inspect.signature(rawcopy_module.stage_raw).parameters
    assert tuple(raw_params)[-1] == "claim"
    assert raw_params["claim"].default is None
    assert raw_params["claim"].kind is inspect.Parameter.KEYWORD_ONLY

    fail_params = inspect.signature(cleanup_module.FailureInputs).parameters
    assert tuple(fail_params)[-1] == "claim"
    assert fail_params["claim"].default is None

    pub_params = inspect.signature(publish_module.PublishInputs).parameters
    assert tuple(pub_params)[-1] == "claim"
    assert pub_params["claim"].default is None

    remove_params = inspect.signature(safe_fs.remove_tree_allow_symlinks).parameters
    assert "expected_root_identity" in remove_params
    assert remove_params["expected_root_identity"].default is None
