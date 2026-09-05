"""Round 1 ownership: identity drift after terminal, log commit, and DONE."""

from __future__ import annotations

import os
import pathlib
import shutil
import threading

import pytest
from controller_sources_fixtures import (
    CYCLE_T,
    CYCLE_T12,
    CYCLE_T24,
    GFS_EXIT,
    IFS_EXIT,
    IFS_RAW_LOG,
    T_PLUS_12_TEXT,
    T_TEXT,
    BarrierExecutor,
    DualBarrier,
    FailureLogHook,
    RecordingProvider,
    copy_replace_named_directory,
    cycle_outcomes,
    done_path,
    fake_for,
    hooked_success_cycles,
    inode_pair,
    noop_wait,
    plant_raw_cycles,
    rebind_work_parent,
    replace_named_directory,
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
from yd_producer.executor import JobState
from yd_producer.store import safe_fs

EXTERNAL_LOG = b"external-job-log-must-not-be-ingested\n"
REPLACEMENT_MARKER = b"replacement-work-must-stay\n"
REPLACEMENT_NAME = "replacement-marker"


def _gfs_two(local):
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    return hooked_success_cycles("gfs", (CYCLE_T, CYCLE_T12))


def _gfs_two_outcomes():
    return [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.STOPPED),
    ]


def _failed_ifs_executor(local, barrier: DualBarrier):
    return BarrierExecutor(
        fake_for("ifs", state=JobState.FAILED, polls=1),
        source="ifs",
        barrier=barrier,
        hook=FailureLogHook(local, "ifs", IFS_RAW_LOG),
        wait_for_peer=False,
    )


def _tree_payloads(root: pathlib.Path) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = pathlib.Path(dirpath) / name
            if path.is_symlink():
                continue
            payloads[path.relative_to(root).as_posix()] = path.read_bytes()
    return payloads


def _run_dual(config, local, *, ifs_exec, gfs_exec, ifs_driver, gfs_driver):
    return run_sources(
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


def test_work_parent_rebind_before_failure_inputs_preserves_external_tree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    ifs_driver, _, _ = success_driver()
    work_root = pathlib.Path(local.scratch_root).resolve() / "work"
    external = pathlib.Path(local.scratch_root).resolve() / "external-work"
    original_inputs = cleanup_module.FailureInputs
    original_publish = publish_module.publish
    original_remove_work = publish_module._remove_work
    ifs_terminal = threading.Event()
    gfs_terminal = threading.Event()
    gfs_cleanup_done = threading.Event()
    planted_external = threading.Event()
    swapped = {"done": False}
    external_leaf = external / "ifs" / T_TEXT
    planted: dict[str, object] = {}

    def swapping_inputs(**kwargs):
        if kwargs.get("source") == "ifs" and not swapped["done"]:
            swapped["done"] = True
            ifs_terminal.set()
            if not gfs_cleanup_done.wait(timeout=5):
                raise TimeoutError("GFS 未在 FailureInputs 前完成全部 publish/cleanup")
            if work_root.exists() and not external.exists():
                shutil.copytree(work_root, external, symlinks=True)
            if (external_leaf / "job.log").is_file():
                (external_leaf / "job.log").unlink()
            (external_leaf / "job.log").write_bytes(EXTERNAL_LOG)
            (external_leaf / "keep.bin").write_bytes(b"external-tree-payload\n")
            planted["inode"] = inode_pair(external_leaf)
            rebind_work_parent(work_root, external)
            planted_external.set()
        return original_inputs(**kwargs)

    def gated_publish(inputs):
        if inputs.source == "gfs":
            if not ifs_terminal.wait(timeout=5):
                raise TimeoutError("IFS 未到达 FailureInputs 边界")
            return original_publish(inputs)
        if inputs.source == "ifs":
            raise AssertionError("IFS sibling 不得在父根重绑后继续 publish")
        return original_publish(inputs)

    def gated_remove_work(inputs):
        if planted_external.is_set():
            raise AssertionError("sibling 不得在 copied/rebound tree 上继续删除")
        result = original_remove_work(inputs)
        if inputs.source == "gfs" and inputs.cycle == CYCLE_T12:
            gfs_cleanup_done.set()
        return result

    def on_gfs_terminal(request, job_id):
        if request.cycle == CYCLE_T12:
            gfs_terminal.set()

    monkeypatch.setattr(cleanup_module, "FailureInputs", swapping_inputs)
    monkeypatch.setattr(publish_module, "publish", gated_publish)
    monkeypatch.setattr(publish_module, "_remove_work", gated_remove_work)
    gfs_driver, gfs_exec = hooked_success_cycles(
        "gfs",
        (CYCLE_T, CYCLE_T12),
        on_terminal=on_gfs_terminal,
    )
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={
                "ifs": _failed_ifs_executor(local, DualBarrier()),
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
    assert error.errors["ifs"].phase == "cleanup"
    assert error.errors["ifs"].source == "ifs"
    assert error.errors["ifs"].cycle == CYCLE_T
    assert error.errors["ifs"].job_id == "fake-1"
    assert error.reports["ifs"] == ()
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    external_leaf = external / "ifs" / T_TEXT
    assert (external_leaf / "job.log").read_bytes() == EXTERNAL_LOG
    assert (external_leaf / "keep.bin").read_bytes() == b"external-tree-payload\n"
    assert inode_pair(external_leaf) == planted["inode"]
    assert not failure_log_contains_external(local)
    assert os.path.islink(work_root)
    assert planted_external.is_set()
    assert gfs_cleanup_done.is_set()
    assert gfs_terminal.is_set()


def failure_log_contains_external(local) -> bool:
    path = pathlib.Path(local.yd_root) / "logs" / "ifs" / f"{T_TEXT}.log"
    if not path.exists():
        return False
    return EXTERNAL_LOG in path.read_bytes()


def test_failure_log_commit_then_root_replacement_keeps_log_and_replacement(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    barrier = DualBarrier()
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    original_remove = cleanup_module.remove_tree_allow_symlinks
    planted: dict[str, object] = {}

    def swapping_remove(parent, name, **kwargs):
        target = pathlib.Path(parent) / name
        if name == T_TEXT and target.parent.name == "ifs" and "inode" not in planted:
            planted["log"] = (
                pathlib.Path(local.yd_root) / "logs" / "ifs" / f"{T_TEXT}.log"
            ).read_bytes()
            replace_named_directory(
                target, marker_name=REPLACEMENT_NAME, marker_bytes=REPLACEMENT_MARKER
            )
            planted["inode"] = inode_pair(target)
        return original_remove(parent, name, **kwargs)

    monkeypatch.setattr(cleanup_module, "remove_tree_allow_symlinks", swapping_remove)
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            config=config,
            local=local,
            executors={
                "ifs": _failed_ifs_executor(local, barrier),
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
    assert error.errors["ifs"].phase == "cleanup"
    assert error.errors["ifs"].job_id == "fake-1"
    log_path = pathlib.Path(local.yd_root) / "logs" / "ifs" / f"{T_TEXT}.log"
    assert log_path.is_file()
    assert log_path.read_bytes() == planted["log"]
    replacement = work_dir(local, "ifs")
    assert (replacement / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER
    assert inode_pair(replacement) == planted["inode"]
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()


def test_publish_scratch_read_before_done_rejects_replaced_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "ifs", (CYCLE_T12,))
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    original = publish_module.publish
    planted: dict[str, object] = {}

    def swapping_publish(inputs):
        if inputs.source == "ifs" and "inode" not in planted:
            copy_replace_named_directory(pathlib.Path(inputs.work_dir))
            planted["inode"] = inode_pair(pathlib.Path(inputs.work_dir))
        return original(inputs)

    monkeypatch.setattr(publish_module, "publish", swapping_publish)
    ifs_driver, ifs_exec = hooked_success_cycles("ifs", (CYCLE_T, CYCLE_T12))
    gfs_driver, gfs_exec = hooked_success_cycles("gfs", (CYCLE_T, CYCLE_T12))
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
    assert error.errors["ifs"].phase == "publish"
    assert error.errors["ifs"].source == "ifs"
    assert error.errors["ifs"].cycle == CYCLE_T
    assert not done_path(local, "ifs").exists()
    replacement = work_dir(local, "ifs")
    assert replacement.is_dir()
    assert inode_pair(replacement) == planted["inode"]
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    assert done_path(local, "gfs").is_file()
    assert done_path(local, "gfs", T_PLUS_12_TEXT).is_file()


def test_done_then_root_replacement_is_cleanup_pending(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    original = publish_module._remove_work
    planted: dict[str, object] = {}

    def swapping_remove(inputs):
        if inputs.source == "ifs" and "inode" not in planted:
            replace_named_directory(
                pathlib.Path(inputs.work_dir),
                marker_name=REPLACEMENT_NAME,
                marker_bytes=REPLACEMENT_MARKER,
            )
            planted["inode"] = inode_pair(pathlib.Path(inputs.work_dir))
        return original(inputs)

    monkeypatch.setattr(publish_module, "_remove_work", swapping_remove)
    ifs_driver, ifs_exec = hooked_success_cycles("ifs", (CYCLE_T,))
    gfs_driver, gfs_exec = hooked_success_cycles("gfs", (CYCLE_T, CYCLE_T12))
    report = run_sources(
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
    ifs = require_source_tuple(report.ifs, "ifs")
    gfs = require_source_tuple(report.gfs, "gfs")
    assert cycle_outcomes(ifs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED_CLEANUP_PENDING),
    ]
    assert done_path(local, "ifs").is_file()
    replacement = work_dir(local, "ifs")
    assert (replacement / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER
    assert inode_pair(replacement) == planted["inode"]
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()


def test_remove_tree_identity_mismatch_is_kind_identity_changed(
    tmp_path: pathlib.Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "leaf"
    root.mkdir()
    (root / "keep.bin").write_bytes(b"claimed\n")
    expected = safe_fs.directory_identity_no_follow(root)
    replace_named_directory(
        root, marker_name=REPLACEMENT_NAME, marker_bytes=REPLACEMENT_MARKER
    )
    with pytest.raises(safe_fs.SafeFilesystemError) as info:
        safe_fs.remove_tree_allow_symlinks(
            parent,
            "leaf",
            containment_root=parent,
            missing_ok=False,
            expected_root_identity=expected,
        )
    assert info.value.kind == "identity_changed"
    assert (root / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER


def test_remove_tree_rechecks_named_identity_before_final_rmdir(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "leaf"
    root.mkdir()
    (root / "keep.bin").write_bytes(b"claimed\n")
    expected = safe_fs.directory_identity_no_follow(root)
    original = os.rmdir
    swapped = {"done": False}

    def swapping_rmdir(name, *args, **kwargs):
        if not swapped["done"] and (
            name == "leaf" or str(name).endswith("/leaf") or str(name).endswith("leaf")
        ):
            swapped["done"] = True
            replace_named_directory(
                root, marker_name=REPLACEMENT_NAME, marker_bytes=REPLACEMENT_MARKER
            )
        return original(name, *args, **kwargs)

    monkeypatch.setattr(os, "rmdir", swapping_rmdir)
    with pytest.raises(safe_fs.SafeFilesystemError) as info:
        safe_fs.remove_tree_allow_symlinks(
            parent,
            "leaf",
            containment_root=parent,
            missing_ok=False,
            expected_root_identity=expected,
        )
    assert info.value.kind == "identity_changed"
    assert (root / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER


def test_remove_tree_unchanged_identity_rmdir_failure_is_kind_io(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "leaf"
    root.mkdir()
    (root / "keep.bin").write_bytes(b"claimed\n")
    expected = safe_fs.directory_identity_no_follow(root)
    original = os.rmdir

    def failing_rmdir(name, *args, **kwargs):
        if name == "leaf" or str(name).endswith("/leaf") or str(name).endswith("leaf"):
            raise OSError(getattr(os, "ENOTEMPTY", 66), "Directory not empty")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(os, "rmdir", failing_rmdir)
    with pytest.raises(safe_fs.SafeFilesystemError) as info:
        safe_fs.remove_tree_allow_symlinks(
            parent,
            "leaf",
            containment_root=parent,
            missing_ok=False,
            expected_root_identity=expected,
        )
    assert info.value.kind == "io"
    assert root.is_dir()
    assert inode_pair(root) == expected


def test_failure_log_open_after_validate_does_not_read_replaced_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FailureInputs already validated; swap exact root before merged-log source open."""
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    original_open = cleanup_module.open_claimed_file
    planted: dict[str, object] = {}

    def swapping_open(claim, path):
        named = pathlib.Path(path)
        if (
            named.name == "job.log"
            and named.parent.name == T_TEXT
            and named.parent.parent.name == "ifs"
            and "inode" not in planted
        ):
            target = named.parent
            copy_replace_named_directory(target)
            (target / "job.log").write_bytes(EXTERNAL_LOG)
            (target / REPLACEMENT_NAME).write_bytes(REPLACEMENT_MARKER)
            planted["inode"] = inode_pair(target)
            planted["payloads"] = _tree_payloads(target)
        return original_open(claim, path)

    monkeypatch.setattr(cleanup_module, "open_claimed_file", swapping_open)
    with pytest.raises(RunSourcesError) as info:
        _run_dual(
            config,
            local,
            ifs_exec=_failed_ifs_executor(local, DualBarrier()),
            gfs_exec=gfs_exec,
            ifs_driver=ifs_driver,
            gfs_driver=gfs_driver,
        )
    error = info.value
    assert error.errors["ifs"].phase == "cleanup"
    assert error.errors["ifs"].source == "ifs"
    assert error.errors["ifs"].cycle == CYCLE_T
    assert error.errors["ifs"].job_id == "fake-1"
    replacement = work_dir(local, "ifs")
    assert inode_pair(replacement) == planted["inode"]
    assert _tree_payloads(replacement) == planted["payloads"]
    assert (replacement / "job.log").read_bytes() == EXTERNAL_LOG
    assert (replacement / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER
    log_path = pathlib.Path(local.yd_root) / "logs" / "ifs" / f"{T_TEXT}.log"
    if log_path.exists():
        assert EXTERNAL_LOG not in log_path.read_bytes()
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()


def test_failure_log_metadata_stat_after_bind_claim_does_not_touch_replacement(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swap exact root after `_bind_claim` validation, before merged-log metadata stat."""
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    original_validate = cleanup_module.validate_claim
    original_stat = cleanup_module.stat_no_follow
    planted: dict[str, object] = {}
    statted_replacement = {"done": False}

    def swapping_validate(claim, *, path=None):
        result = original_validate(claim, path=path)
        named = claim.work_dir if path is None else path
        if (
            named.parent.name == "ifs"
            and named.name == T_TEXT
            and "inode" not in planted
        ):
            copy_replace_named_directory(named)
            (named / "job.log").write_bytes(EXTERNAL_LOG)
            (named / REPLACEMENT_NAME).write_bytes(REPLACEMENT_MARKER)
            planted["inode"] = inode_pair(named)
            planted["payloads"] = _tree_payloads(named)
            planted["stat"] = (named / "job.log").lstat()
        return result

    def tracking_stat(path, **kwargs):
        named = pathlib.Path(path)
        work = work_dir(local, "ifs")
        if "inode" in planted and named.is_relative_to(work):
            statted_replacement["done"] = True
        return original_stat(path, **kwargs)

    monkeypatch.setattr(cleanup_module, "validate_claim", swapping_validate)
    monkeypatch.setattr(cleanup_module, "stat_no_follow", tracking_stat)
    with pytest.raises(RunSourcesError) as info:
        _run_dual(
            config,
            local,
            ifs_exec=_failed_ifs_executor(local, DualBarrier()),
            gfs_exec=gfs_exec,
            ifs_driver=ifs_driver,
            gfs_driver=gfs_driver,
        )
    error = info.value
    assert error.errors["ifs"].phase == "cleanup"
    assert error.errors["ifs"].source == "ifs"
    assert error.errors["ifs"].cycle == CYCLE_T
    assert error.errors["ifs"].job_id == "fake-1"
    replacement = work_dir(local, "ifs")
    assert inode_pair(replacement) == planted["inode"]
    assert _tree_payloads(replacement) == planted["payloads"]
    assert (replacement / "job.log").read_bytes() == EXTERNAL_LOG
    assert (replacement / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER
    current = (replacement / "job.log").lstat()
    planted_stat = planted["stat"]
    assert (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) == (
        planted_stat.st_dev,
        planted_stat.st_ino,
        planted_stat.st_size,
        planted_stat.st_mtime_ns,
    )
    log_path = pathlib.Path(local.yd_root) / "logs" / "ifs" / f"{T_TEXT}.log"
    if log_path.exists():
        assert EXTERNAL_LOG not in log_path.read_bytes()
    assert statted_replacement["done"] is False
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()


def test_publish_first_scratch_read_after_validate_does_not_ingest_replacement(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swap exact root after initial publish claim validation, before first scratch read."""
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    original_restamp = publish_module._restamped_bytes
    original_read = publish_module.read_bytes_limited_no_follow
    planted: dict[str, object] = {}
    pathname_read = {"done": False}

    def swapping_restamp(inputs):
        work = pathlib.Path(inputs.work_dir)
        if inputs.source == "ifs" and "inode" not in planted:
            copy_replace_named_directory(work)
            (work / REPLACEMENT_NAME).write_bytes(REPLACEMENT_MARKER)
            planted["inode"] = inode_pair(work)
            planted["payloads"] = _tree_payloads(work)
        return original_restamp(inputs)

    def tracking_read(path, **kwargs):
        named = pathlib.Path(path)
        work = work_dir(local, "ifs")
        if "inode" in planted and named.is_relative_to(work):
            pathname_read["done"] = True
        return original_read(path, **kwargs)

    monkeypatch.setattr(publish_module, "_restamped_bytes", swapping_restamp)
    monkeypatch.setattr(publish_module, "read_bytes_limited_no_follow", tracking_read)
    ifs_driver, ifs_exec = hooked_success_cycles("ifs", (CYCLE_T,))
    gfs_driver, gfs_exec = hooked_success_cycles("gfs", (CYCLE_T, CYCLE_T12))
    with pytest.raises(RunSourcesError) as info:
        _run_dual(
            config,
            local,
            ifs_exec=ifs_exec,
            gfs_exec=gfs_exec,
            ifs_driver=ifs_driver,
            gfs_driver=gfs_driver,
        )
    error = info.value
    assert error.errors["ifs"].phase == "publish"
    assert error.errors["ifs"].source == "ifs"
    assert error.errors["ifs"].cycle == CYCLE_T
    assert not done_path(local, "ifs").exists()
    replacement = work_dir(local, "ifs")
    assert inode_pair(replacement) == planted["inode"]
    assert (replacement / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER
    assert _tree_payloads(replacement) == planted["payloads"]
    assert pathname_read["done"] is False
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    assert done_path(local, "gfs").is_file()
    assert done_path(local, "gfs", T_PLUS_12_TEXT).is_file()


def test_rawcopy_target_open_after_mkdir_does_not_write_replaced_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After claimed descendant mkdir, swap exact root before first target O_EXCL."""
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    original_copy = rawcopy_module._copy_one
    original_os_open = os.open
    planted: dict[str, object] = {}

    def swapping_copy(source_path, target, written, *args, **kwargs):
        named = pathlib.Path(target)
        work = work_dir(local, "ifs")
        if named.is_relative_to(work) and "inode" not in planted:
            copy_replace_named_directory(work)
            (work / REPLACEMENT_NAME).write_bytes(REPLACEMENT_MARKER)
            planted["inode"] = inode_pair(work)
            planted["payloads"] = _tree_payloads(work)
        return original_copy(source_path, target, written, *args, **kwargs)

    def tracking_os_open(path, flags, *args, **kwargs):
        named = pathlib.Path(path) if not isinstance(path, int) else None
        work = work_dir(local, "ifs")
        if (
            named is not None
            and "inode" in planted
            and (flags & os.O_CREAT)
            and named.is_relative_to(work)
        ):
            planted["wrote"] = True
        return original_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(rawcopy_module, "_copy_one", swapping_copy)
    monkeypatch.setattr(rawcopy_module.os, "open", tracking_os_open)
    with pytest.raises(RunSourcesError) as info:
        _run_dual(
            config,
            local,
            ifs_exec=fake_for("ifs"),
            gfs_exec=gfs_exec,
            ifs_driver=ifs_driver,
            gfs_driver=gfs_driver,
        )
    error = info.value
    assert error.errors["ifs"].phase == "raw"
    assert error.errors["ifs"].source == "ifs"
    assert error.errors["ifs"].cycle == CYCLE_T
    replacement = work_dir(local, "ifs")
    assert inode_pair(replacement) == planted["inode"]
    assert (replacement / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER
    assert _tree_payloads(replacement) == planted["payloads"]
    assert planted.get("wrote") is None
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    assert not done_path(local, "ifs").exists()


def test_rawcopy_manifest_open_after_copies_does_not_write_or_rollback_replacement(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After claimed copies, swap exact root before manifest create/rollback."""
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    original_write = rawcopy_module._write_manifest
    original_os_open = os.open
    planted: dict[str, object] = {}

    def swapping_write(*, manifest_path, payload, written, claim=None):
        work = work_dir(local, "ifs")
        if claim is not None and claim.work_dir == work and "inode" not in planted:
            copy_replace_named_directory(work)
            (work / REPLACEMENT_NAME).write_bytes(REPLACEMENT_MARKER)
            planted["inode"] = inode_pair(work)
            planted["payloads"] = _tree_payloads(work)
        return original_write(
            manifest_path=manifest_path,
            payload=payload,
            written=written,
            claim=claim,
        )

    def tracking_os_open(path, flags, *args, **kwargs):
        named = pathlib.Path(path) if not isinstance(path, int) else None
        work = work_dir(local, "ifs")
        if (
            named is not None
            and "inode" in planted
            and (flags & os.O_CREAT)
            and named.is_relative_to(work)
        ):
            planted["wrote"] = True
        return original_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(rawcopy_module, "_write_manifest", swapping_write)
    monkeypatch.setattr(rawcopy_module.os, "open", tracking_os_open)
    with pytest.raises(RunSourcesError) as info:
        _run_dual(
            config,
            local,
            ifs_exec=fake_for("ifs"),
            gfs_exec=gfs_exec,
            ifs_driver=ifs_driver,
            gfs_driver=gfs_driver,
        )
    error = info.value
    assert error.errors["ifs"].phase == "raw"
    assert error.errors["ifs"].source == "ifs"
    assert error.errors["ifs"].cycle == CYCLE_T
    replacement = work_dir(local, "ifs")
    assert inode_pair(replacement) == planted["inode"]
    assert (replacement / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER
    assert _tree_payloads(replacement) == planted["payloads"]
    assert planted.get("wrote") is None
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    assert not done_path(local, "ifs").exists()


def test_collect_stat_after_terminal_does_not_touch_replaced_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swap exact root after terminal, before controller collect/terminal stat."""
    config, local = write_dual_tree(tmp_path)
    plant_raw_cycles(local, "gfs", (CYCLE_T12,))
    original_require = run_mod._require_terminal_artifacts_pre_collect
    original_stat = run_mod.safe_fs.stat_no_follow
    planted: dict[str, object] = {}
    statted_replacement = {"done": False}

    def swapping_require(*args, **kwargs):
        work = pathlib.Path(kwargs["work_dir"])
        if kwargs.get("source") == "ifs" and "inode" not in planted:
            copy_replace_named_directory(work)
            (work / REPLACEMENT_NAME).write_bytes(REPLACEMENT_MARKER)
            planted["inode"] = inode_pair(work)
            planted["payloads"] = _tree_payloads(work)
        return original_require(*args, **kwargs)

    def tracking_stat(path, **kwargs):
        named = pathlib.Path(path)
        work = work_dir(local, "ifs")
        if "inode" in planted and named.is_relative_to(work):
            statted_replacement["done"] = True
        return original_stat(path, **kwargs)

    monkeypatch.setattr(
        run_mod, "_require_terminal_artifacts_pre_collect", swapping_require
    )
    monkeypatch.setattr(run_mod.safe_fs, "stat_no_follow", tracking_stat)
    ifs_driver, ifs_exec = hooked_success_cycles("ifs", (CYCLE_T,))
    gfs_driver, gfs_exec = hooked_success_cycles("gfs", (CYCLE_T, CYCLE_T12))
    with pytest.raises(RunSourcesError) as info:
        _run_dual(
            config,
            local,
            ifs_exec=ifs_exec,
            gfs_exec=gfs_exec,
            ifs_driver=ifs_driver,
            gfs_driver=gfs_driver,
        )
    error = info.value
    assert error.errors["ifs"].phase == "collect"
    assert error.errors["ifs"].source == "ifs"
    assert error.errors["ifs"].cycle == CYCLE_T
    replacement = work_dir(local, "ifs")
    assert inode_pair(replacement) == planted["inode"]
    assert (replacement / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER
    assert _tree_payloads(replacement) == planted["payloads"]
    assert statted_replacement["done"] is False
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    assert not done_path(local, "ifs").exists()
