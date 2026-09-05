"""Phase 2 RE29: clean raw retry after claimed stage_raw rollback."""

from __future__ import annotations

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
    RecordingProvider,
    cycle_outcomes,
    done_path,
    fake_for,
    hooked_success_cycles,
    inode_pair,
    noop_wait,
    plant_raw_cycles,
    replace_named_directory,
    require_source_tuple,
    success_driver,
    work_dir,
    write_dual_tree,
)

from yd_producer import _work_claim as claim_mod
from yd_producer import rawcopy as rawcopy_module
from yd_producer.controller import RunOutcome, RunSourcesError, StopReason, run_sources

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


def _raw_error_evidence(error) -> str:
    evidence = " ".join([str(error), *getattr(error, "__notes__", ())])
    if error.__cause__ is not None:
        evidence = " ".join(
            [
                evidence,
                str(error.__cause__),
                *getattr(error.__cause__, "__notes__", ()),
            ]
        )
    return evidence


class _IfsAdmissionFailure:
    """Inject one admission exit only for IFS's raw cycle."""

    def __init__(self, local, cause: BaseException) -> None:
        self._raw_root = pathlib.Path(local.nwm.raw_root)
        self._cause = cause
        self._original = rawcopy_module._load_source_manifest

    def __call__(self, cycle_root):
        if pathlib.Path(cycle_root).parent == self._raw_root / "IFS":
            raise self._cause
        return self._original(cycle_root)


def _assert_raw_error(
    error,
    cause_type: type[BaseException],
    *,
    kind: str | None = None,
    nested_cause_type: type[BaseException] | None = None,
) -> None:
    assert error.phase == "raw"
    assert error.source == "ifs"
    assert error.cycle == CYCLE_T
    assert isinstance(error.__cause__, cause_type)
    if kind is not None:
        assert isinstance(error.__cause__, rawcopy_module.RawStagingError)
        assert error.__cause__.kind == kind
    if nested_cause_type is not None:
        assert error.__cause__ is not None
        assert isinstance(error.__cause__.__cause__, nested_cause_type)


def _remove_ifs_source_manifest(local) -> pathlib.Path:
    path = pathlib.Path(local.nwm.raw_root) / "IFS" / "2026082612" / "manifest.json"
    path.unlink()
    return path


def _fail_after_own_files(monkeypatch, local, *, after_files: int, on_cleanup=None):
    original_copy = rawcopy_module._copy_one
    seen = {"count": 0}

    def failing_copy(source_path, target, written, *, claim=None):
        result = original_copy(source_path, target, written, claim=claim)
        named = pathlib.Path(target)
        root = work_dir(local, "ifs")
        try:
            named.relative_to(root)
        except ValueError:
            return result
        seen["count"] += 1
        if seen["count"] >= after_files:
            if on_cleanup is not None:
                original_rollback = written.rollback

                def hooked_rollback():
                    failures = original_rollback()
                    on_cleanup(named)
                    return failures

                written.rollback = hooked_rollback  # type: ignore[method-assign]
            raise OSError("injected IFS copy failure after own files")
        return result

    monkeypatch.setattr(rawcopy_module, "_copy_one", failing_copy)
    return seen, original_copy


def test_missing_source_manifest_releases_claimed_root_and_allows_next_tick(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    missing_manifest = _remove_ifs_source_manifest(local)
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    work_root = pathlib.Path(local.scratch_root).resolve() / "work"

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
    assert set(error.errors) == {"ifs"}
    raw_error = error.errors["ifs"]
    _assert_raw_error(
        raw_error,
        rawcopy_module.RawStagingError,
        kind="source-manifest",
        nested_cause_type=FileNotFoundError,
    )
    assert not work_dir(local, "ifs").exists()
    assert (work_root / "ifs").is_dir()
    assert work_root.is_dir()
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    assert done_path(local, "gfs").is_file()
    assert done_path(local, "gfs", "2026082700").is_file()

    from run_once_fixtures import write_raw_cycle

    write_raw_cycle(local, source="ifs", cycle=CYCLE_T)
    assert missing_manifest.is_file()
    retry_driver, retry_ifs_exec = hooked_success_cycles("ifs", (CYCLE_T,))
    retry_gfs_driver, retry_gfs_exec = hooked_success_cycles("gfs", ())
    retry = _run_dual(
        config,
        local,
        ifs_exec=retry_ifs_exec,
        gfs_exec=retry_gfs_exec,
        ifs_driver=retry_driver,
        gfs_driver=retry_gfs_driver,
    )
    ifs = require_source_tuple(retry.ifs, "ifs")
    assert cycle_outcomes(ifs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert ifs[-1].stop_reason is not StopReason.UNVERIFIED_WORK_RESIDUE
    assert len(retry_ifs_exec.submissions) == 1


def test_claimed_source_symlink_admission_releases_empty_root(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_dual_tree(tmp_path)
    raw_cycle = pathlib.Path(local.nwm.raw_root) / "IFS" / "2026082612"
    bundle = raw_cycle / "ifs.t12z.f000.bundle.grib2"
    real = raw_cycle / "real-f000.bundle.grib2"
    bundle.rename(real)
    bundle.symlink_to(real)
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    work_root = pathlib.Path(local.scratch_root).resolve() / "work"
    with pytest.raises(RunSourcesError) as info:
        _run_dual(
            config,
            local,
            ifs_exec=fake_for("ifs"),
            gfs_exec=gfs_exec,
            ifs_driver=ifs_driver,
            gfs_driver=gfs_driver,
        )

    raw_error = info.value.errors["ifs"]
    _assert_raw_error(
        raw_error,
        rawcopy_module.RawStagingError,
        kind="source-symlink",
    )
    assert not work_dir(local, "ifs").exists()
    assert (work_root / "ifs").is_dir()
    assert work_root.is_dir()
    gfs = require_source_tuple(info.value.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()


def test_claimed_admission_fallback_releases_root_preserving_public_cause(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, local = write_dual_tree(tmp_path)
    original_render = rawcopy_module._render_manifest

    def fallback(*, source, cycle, leads, entries, cycle_root):
        if source == "ifs":
            raise RuntimeError("injected IFS admission fallback")
        return original_render(
            source=source,
            cycle=cycle,
            leads=leads,
            entries=entries,
            cycle_root=cycle_root,
        )

    monkeypatch.setattr(rawcopy_module, "_render_manifest", fallback)
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    with pytest.raises(RunSourcesError) as info:
        _run_dual(
            config,
            local,
            ifs_exec=fake_for("ifs"),
            gfs_exec=gfs_exec,
            ifs_driver=ifs_driver,
            gfs_driver=gfs_driver,
        )

    raw_error = info.value.errors["ifs"]
    _assert_raw_error(
        raw_error,
        rawcopy_module.RawStagingError,
        kind="source-manifest",
        nested_cause_type=RuntimeError,
    )
    assert not work_dir(local, "ifs").exists()
    work_root = pathlib.Path(local.scratch_root).resolve() / "work"
    assert (work_root / "ifs").is_dir()
    assert work_root.is_dir()
    gfs = require_source_tuple(info.value.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()


@pytest.mark.parametrize("signal_type", [KeyboardInterrupt, SystemExit])
def test_claimed_admission_baseexception_releases_root_unchanged(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    signal_type: type[BaseException],
) -> None:
    config, local = write_dual_tree(tmp_path)
    signal = signal_type("admission interrupt")

    monkeypatch.setattr(
        rawcopy_module,
        "_load_source_manifest",
        _IfsAdmissionFailure(local, signal),
    )
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    with pytest.raises(signal_type) as info:
        _run_dual(
            config,
            local,
            ifs_exec=fake_for("ifs"),
            gfs_exec=gfs_exec,
            ifs_driver=ifs_driver,
            gfs_driver=gfs_driver,
        )
    assert info.value is signal
    assert not work_dir(local, "ifs").exists()
    assert (pathlib.Path(local.scratch_root).resolve() / "work" / "ifs").is_dir()
    assert done_path(local, "gfs").is_file()
    assert done_path(local, "gfs", "2026082700").is_file()


@pytest.mark.parametrize(
    "interference", ["foreign-child", "replacement", "empty-replacement"]
)
def test_claimed_admission_release_refuses_foreign_or_replaced_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    interference: str,
) -> None:
    config, local = write_dual_tree(tmp_path)
    _remove_ifs_source_manifest(local)
    planted: dict[str, object] = {}
    original = claim_mod.release_raw_claim_after_stage_failure

    def interfere(claim, error):
        root = work_dir(local, "ifs")
        if interference == "foreign-child":
            marker = root / FOREIGN_MARKER_NAME
            marker.write_bytes(FOREIGN_MARKER_BYTES)
            planted["identity"] = inode_pair(marker)
        else:
            replacement = replace_named_directory(
                root,
                marker_name=REPLACEMENT_NAME,
                marker_bytes=REPLACEMENT_MARKER,
            )
            if interference == "empty-replacement":
                (root / REPLACEMENT_NAME).unlink()
            planted["identity"] = inode_pair(root)
            planted["aside"] = replacement
        return original(claim, error)

    monkeypatch.setattr(
        rawcopy_module, "release_raw_claim_after_stage_failure", interfere
    )
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    with pytest.raises(RunSourcesError) as info:
        _run_dual(
            config,
            local,
            ifs_exec=fake_for("ifs"),
            gfs_exec=gfs_exec,
            ifs_driver=ifs_driver,
            gfs_driver=gfs_driver,
        )

    raw_error = info.value.errors["ifs"]
    _assert_raw_error(
        raw_error,
        rawcopy_module.RawStagingError,
        kind="source-manifest",
        nested_cause_type=FileNotFoundError,
    )
    assert "claimed exact-root cleanup failed" in _raw_error_evidence(raw_error)
    root = work_dir(local, "ifs")
    if interference == "foreign-child":
        marker = root / FOREIGN_MARKER_NAME
        assert marker.read_bytes() == FOREIGN_MARKER_BYTES
        assert inode_pair(marker) == planted["identity"]
    else:
        assert inode_pair(root) == planted["identity"]
        if interference == "empty-replacement":
            assert list(root.iterdir()) == []
        else:
            assert (root / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER
    work_root = pathlib.Path(local.scratch_root).resolve() / "work"
    assert (work_root / "ifs").is_dir()
    assert work_root.is_dir()
    gfs = require_source_tuple(info.value.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()


def test_raw_staging_failure_releases_empty_exact_root_for_retry(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    work_root = pathlib.Path(local.scratch_root).resolve() / "work"
    _seen, original_copy = _fail_after_own_files(monkeypatch, local, after_files=1)
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
    assert set(error.errors) == {"ifs"}
    assert error.errors["ifs"].phase == "raw"
    assert error.errors["ifs"].source == "ifs"
    assert error.errors["ifs"].cycle == CYCLE_T
    assert not work_dir(local, "ifs").exists()
    assert (work_root / "ifs").is_dir()
    assert work_root.is_dir()
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
    assert done_path(local, "gfs").is_file()
    assert done_path(local, "gfs", "2026082700").is_file()

    monkeypatch.setattr(rawcopy_module, "_copy_one", original_copy)
    retry_driver, retry_ifs_exec = hooked_success_cycles("ifs", (CYCLE_T,))
    retry_gfs_driver, retry_gfs_exec = hooked_success_cycles("gfs", ())
    retry = run_sources(
        config=config,
        local=local,
        executors={"ifs": retry_ifs_exec, "gfs": retry_gfs_exec},
        drivers={"ifs": retry_driver, "gfs": retry_gfs_driver},
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes={
            "ifs": RecordingProvider("ifs", IFS_EXIT),
            "gfs": RecordingProvider("gfs", GFS_EXIT),
        },
    )
    ifs = require_source_tuple(retry.ifs, "ifs")
    assert cycle_outcomes(ifs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert ifs[-1].stop_reason is not StopReason.UNVERIFIED_WORK_RESIDUE


def test_raw_root_release_does_not_swallow_keyboardinterrupt(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config, local = write_dual_tree(tmp_path)
    claim = claim_mod.claim_exact_work(
        work_root=pathlib.Path(local.scratch_root).resolve() / "work",
        source="ifs",
        cycle=CYCLE_T,
        cycle_name="2026082612",
    )

    def interrupt(_claim):
        raise KeyboardInterrupt

    monkeypatch.setattr(claim_mod, "release_empty_claimed_root", interrupt)
    with pytest.raises(KeyboardInterrupt):
        claim_mod.release_raw_claim_after_stage_failure(claim, RuntimeError("raw"))


def test_raw_root_cleanup_preserves_foreign_child_and_keeps_original_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    planted: dict[str, object] = {}

    def plant_foreign(target: pathlib.Path) -> None:
        root = work_dir(local, "ifs")
        (root / FOREIGN_MARKER_NAME).write_bytes(FOREIGN_MARKER_BYTES)
        planted["inode"] = inode_pair(root / FOREIGN_MARKER_NAME)

    _fail_after_own_files(monkeypatch, local, after_files=1, on_cleanup=plant_foreign)
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
    raw_error = error.errors["ifs"]
    assert raw_error.phase == "raw"
    assert raw_error.source == "ifs"
    assert raw_error.cycle == CYCLE_T
    assert isinstance(raw_error.__cause__, rawcopy_module.RawStagingError)
    assert isinstance(raw_error.__cause__.__cause__, OSError)
    notes = getattr(raw_error, "__notes__", ())
    evidence = " ".join([str(raw_error), *notes])
    if raw_error.__cause__ is not None:
        evidence = " ".join(
            [
                evidence,
                str(raw_error.__cause__),
                *getattr(raw_error.__cause__, "__notes__", ()),
            ]
        )
    assert "claimed exact-root cleanup failed" in evidence
    marker = work_dir(local, "ifs") / FOREIGN_MARKER_NAME
    assert marker.is_file()
    assert marker.read_bytes() == FOREIGN_MARKER_BYTES
    assert inode_pair(marker) == planted["inode"]
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()


def test_raw_root_cleanup_preserves_replaced_inode_and_keeps_original_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    ifs_driver, _, _ = success_driver()
    gfs_driver, gfs_exec = _gfs_two(local)
    planted: dict[str, object] = {}

    def replace_root(target: pathlib.Path) -> None:
        root = work_dir(local, "ifs")
        replace_named_directory(
            root, marker_name=REPLACEMENT_NAME, marker_bytes=REPLACEMENT_MARKER
        )
        planted["inode"] = inode_pair(root)
        planted["payload"] = (root / REPLACEMENT_NAME).read_bytes()

    _fail_after_own_files(monkeypatch, local, after_files=1, on_cleanup=replace_root)
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
    raw_error = error.errors["ifs"]
    assert raw_error.phase == "raw"
    assert raw_error.source == "ifs"
    assert raw_error.cycle == CYCLE_T
    assert isinstance(raw_error.__cause__, rawcopy_module.RawStagingError)
    assert isinstance(raw_error.__cause__.__cause__, OSError)
    notes = getattr(raw_error, "__notes__", ())
    evidence = " ".join([str(raw_error), *notes])
    if raw_error.__cause__ is not None:
        evidence = " ".join(
            [
                evidence,
                str(raw_error.__cause__),
                *getattr(raw_error.__cause__, "__notes__", ()),
            ]
        )
    assert "claimed exact-root cleanup failed" in evidence
    replacement = work_dir(local, "ifs")
    assert inode_pair(replacement) == planted["inode"]
    assert (replacement / REPLACEMENT_NAME).read_bytes() == REPLACEMENT_MARKER
    gfs = require_source_tuple(error.reports["gfs"], "gfs")
    assert cycle_outcomes(gfs) == _gfs_two_outcomes()
