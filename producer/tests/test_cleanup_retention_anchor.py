r"""`yd_producer.cleanup` current-DONE authority regression tests (issue #25).

The retention authority is independent of controller frontier discovery: every
expectation here is fixed by the constructed real filesystem tree, never by a
returned plan's deletion set.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from cleanup_fixtures import (
    D_MINUS_14D,
    OLDER,
    OLDER_NEXT,
    SOURCE,
    D,
    _assert_not_leaked,
    _write_log,
    _yd_root,
)
from frontier_fixtures import YdRootBuilder, parse_cycle, snapshot_tree

from yd_producer import cleanup, controller
from yd_producer.controller import DiscoveryUnreadableError
from yd_producer.store.safe_fs import SafeFilesystemError

_FORGED_FUTURE = "2026082800"
_PROTECTED_CYCLE = "2026081300"


def _authority_tree(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    """Build one real current DONE plus protected and expired retention objects."""

    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)

    protected = builder.source_output_dir(_PROTECTED_CYCLE, SOURCE)
    protected.mkdir(parents=True)
    (protected / "yd.rivqdown.dat").write_bytes(b"protected-output\xff")
    (protected / "DONE").write_bytes(b"")
    protected_log = _write_log(root, SOURCE, _PROTECTED_CYCLE, b"protected-log\xff")

    expired = builder.source_output_dir(OLDER, SOURCE)
    expired.mkdir(parents=True)
    (expired / "yd.rivqdown.dat").write_bytes(b"expired-output\xff")
    expired_log = _write_log(root, SOURCE, OLDER, b"expired-log\xff")
    return root, protected, protected_log, expired, expired_log


def _windowed_plan_kwargs(
    root: Path, expired: Path, expired_log: Path
) -> dict[str, object]:
    return {
        "yd_root": root,
        "source": SOURCE,
        "latest_done": parse_cycle(D),
        "cutoff": parse_cycle(D_MINUS_14D),
        "output_dirs": (expired,),
        "log_files": (expired_log,),
    }


def _forge_in_root_authority_component(root: Path, component: str) -> tuple[Path, Path]:
    """Replace one authority component with a root-internal symlink target."""

    output = root / "output"
    if component == "output":
        target = root / "authority-output"
        output.rename(target)
        output.symlink_to(target, target_is_directory=True)
        return output, target

    future_cycle = output / _FORGED_FUTURE
    if component == "cycle":
        target = root / "authority-cycle"
        (target / SOURCE).mkdir(parents=True)
        (target / SOURCE / "DONE").write_bytes(b"")
        future_cycle.symlink_to(target, target_is_directory=True)
        return future_cycle, target

    future_cycle.mkdir()
    if component == "source":
        target = root / "authority-source"
        target.mkdir()
        (target / "DONE").write_bytes(b"")
        source = future_cycle / SOURCE
        source.symlink_to(target, target_is_directory=True)
        return source, target

    if component == "done":
        source = future_cycle / SOURCE
        source.mkdir()
        target = root / "authority-DONE"
        target.write_bytes(b"")
        done = source / "DONE"
        done.symlink_to(target)
        return done, target

    raise AssertionError(f"unknown authority component {component!r}")


@pytest.mark.parametrize("component", ["output", "cycle", "source", "done"])
@pytest.mark.parametrize("entrypoint", ["plan", "construct", "execute"])
def test_current_done_authority_rejects_each_in_root_symlink_before_mutation(
    tmp_path: Path, component: str, entrypoint: str
) -> None:
    """Every authority component is no-follow at planner, constructor, and rebind."""

    root, protected, protected_log, expired, expired_log = _authority_tree(tmp_path)
    plan: cleanup.RetentionPlan | None = None
    if entrypoint == "execute":
        plan = cleanup.RetentionPlan(
            **_windowed_plan_kwargs(root, expired, expired_log)
        )

    unsafe, target = _forge_in_root_authority_component(root, component)
    if component == "cycle":
        # This is the exact P1 transport chain: controller intentionally follows
        # the intermediate cycle link for its frontier semantics; cleanup may not.
        assert controller.done_cycles(root / "output", SOURCE) == {
            parse_cycle(_PROTECTED_CYCLE),
            parse_cycle(D),
            parse_cycle(_FORGED_FUTURE),
        }
    before = snapshot_tree(root)
    target_before = snapshot_tree(target) if target.is_dir() else target.read_bytes()

    with pytest.raises(cleanup.CleanupError) as info:
        if entrypoint == "plan":
            cleanup.plan_retention(root, SOURCE)
        elif entrypoint == "construct":
            cleanup.RetentionPlan(**_windowed_plan_kwargs(root, expired, expired_log))
        else:
            assert plan is not None
            cleanup.execute_retention_plan(plan)

    _assert_not_leaked(info.value)
    assert info.value.phase == (
        "retention-plan" if entrypoint == "plan" else "validate"
    )
    assert info.value.path == (unsafe if entrypoint == "plan" else None)
    assert isinstance(info.value.__cause__, SafeFilesystemError)
    assert snapshot_tree(root) == before
    assert unsafe.is_symlink()
    assert target.exists()
    if target.is_dir():
        assert snapshot_tree(target) == target_before
    else:
        assert target.read_bytes() == target_before
    assert (protected / "yd.rivqdown.dat").read_bytes() == b"protected-output\xff"
    assert protected_log.read_bytes() == b"protected-log\xff"
    assert (expired / "yd.rivqdown.dat").read_bytes() == b"expired-output\xff"
    assert expired_log.read_bytes() == b"expired-log\xff"


def test_ordinary_current_done_is_the_retention_authority(tmp_path: Path) -> None:
    root, protected, protected_log, expired, expired_log = _authority_tree(tmp_path)

    plan = cleanup.plan_retention(root, SOURCE)

    assert plan.latest_done == parse_cycle(D)
    assert plan.cutoff == parse_cycle(D_MINUS_14D)
    assert plan.output_dirs == (expired,)
    assert plan.log_files == (expired_log,)
    cleanup.execute_retention_plan(plan)
    assert not expired.exists()
    assert not expired_log.exists()
    assert (protected / "yd.rivqdown.dat").read_bytes() == b"protected-output\xff"
    assert protected_log.read_bytes() == b"protected-log\xff"


def test_planner_refuses_older_parseable_cycle_symlink_before_mutation(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    target = root / "in-window-cycle-target"
    (target / SOURCE).mkdir(parents=True)
    (target / SOURCE / "DONE").write_bytes(b"")
    linked = root / "output" / _PROTECTED_CYCLE
    linked.symlink_to(target, target_is_directory=True)
    before = snapshot_tree(root)
    target_before = snapshot_tree(target)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.plan_retention(root, SOURCE)

    _assert_not_leaked(info.value)
    assert info.value.phase == "retention-plan"
    assert info.value.path == linked
    assert isinstance(info.value.__cause__, SafeFilesystemError)
    assert snapshot_tree(root) == before
    assert linked.is_symlink()
    assert snapshot_tree(target) == target_before
    assert builder.source_output_dir(D, SOURCE).joinpath("DONE").is_file()


@pytest.mark.parametrize("missing", ["output", "source", "done"])
def test_missing_current_done_components_are_not_completions(
    tmp_path: Path, missing: str
) -> None:
    root = _yd_root(tmp_path)
    cycle = root / "output" / D
    if missing == "source":
        cycle.mkdir(parents=True)
    elif missing == "done":
        (cycle / SOURCE).mkdir(parents=True)
    before = snapshot_tree(root)

    plan = cleanup.plan_retention(root, SOURCE)

    assert plan.latest_done is None
    assert plan.cutoff is None
    assert plan.output_dirs == ()
    assert plan.log_files == ()
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("missing", ["source", "done"])
def test_missing_future_components_do_not_shadow_the_real_current_done(
    tmp_path: Path, missing: str
) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    future = root / "output" / _FORGED_FUTURE / SOURCE
    if missing == "source":
        future.parent.mkdir(parents=True)
    else:
        future.mkdir(parents=True)
    before = snapshot_tree(root)

    plan = cleanup.plan_retention(root, SOURCE)

    assert plan.latest_done == parse_cycle(D)
    assert plan.cutoff == parse_cycle(D_MINUS_14D)
    assert plan.output_dirs == ()
    assert plan.log_files == ()
    assert snapshot_tree(root) == before


@pytest.mark.parametrize(
    "shape",
    ["output-file", "cycle-file", "source-file", "done-directory", "done-fifo"],
)
def test_nonregular_current_done_components_fail_during_planning(
    tmp_path: Path, shape: str
) -> None:
    root = _yd_root(tmp_path)
    output = root / "output"
    if shape == "output-file":
        output.write_bytes(b"not-a-directory")
        unsafe = output
    else:
        builder = YdRootBuilder(root=root)
        builder.write_done(D, SOURCE)
        future = output / _FORGED_FUTURE
        if shape == "cycle-file":
            future.write_bytes(b"not-a-directory")
            unsafe = future
        elif shape == "source-file":
            future.mkdir(parents=True)
            unsafe = future / SOURCE
            unsafe.write_bytes(b"not-a-directory")
        else:
            source = future / SOURCE
            source.mkdir(parents=True)
            unsafe = source / "DONE"
            if shape == "done-directory":
                unsafe.mkdir()
            else:
                os.mkfifo(unsafe)
    before = snapshot_tree(root)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.plan_retention(root, SOURCE)

    _assert_not_leaked(info.value)
    assert info.value.phase == "retention-plan"
    assert info.value.path == unsafe
    assert isinstance(info.value.__cause__, SafeFilesystemError)
    assert snapshot_tree(root) == before


def test_current_done_listing_io_is_a_retention_plan_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _protected, _protected_log, _expired, _expired_log = _authority_tree(tmp_path)
    before = snapshot_tree(root)
    injected = OSError("synthetic current-DONE directory read failure")
    original_listdir = os.listdir

    def unreadable_listdir(path: int | str | os.PathLike[str]) -> list[str]:
        if isinstance(path, int):
            raise injected
        return original_listdir(path)

    monkeypatch.setattr(os, "listdir", unreadable_listdir)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.plan_retention(root, SOURCE)

    _assert_not_leaked(info.value)
    assert info.value.phase == "retention-plan"
    assert info.value.path == root / "output"
    assert info.value.__cause__ is injected
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("entrypoint", ["plan", "construct", "execute"])
def test_current_done_discovery_unreadable_maps_at_each_public_entrypoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    root, _protected, _protected_log, expired, expired_log = _authority_tree(tmp_path)
    plan: cleanup.RetentionPlan | None = None
    if entrypoint == "execute":
        plan = cleanup.RetentionPlan(
            **_windowed_plan_kwargs(root, expired, expired_log)
        )
    injected = DiscoveryUnreadableError("synthetic current-DONE discovery failure")
    original_listdir = os.listdir

    def unreadable_listdir(path: int | str | os.PathLike[str]) -> list[str]:
        if isinstance(path, int):
            raise injected
        return original_listdir(path)

    monkeypatch.setattr(os, "listdir", unreadable_listdir)
    before = snapshot_tree(root)

    with pytest.raises(cleanup.CleanupError) as info:
        if entrypoint == "plan":
            cleanup.plan_retention(root, SOURCE)
        elif entrypoint == "construct":
            cleanup.RetentionPlan(**_windowed_plan_kwargs(root, expired, expired_log))
        else:
            assert plan is not None
            cleanup.execute_retention_plan(plan)

    _assert_not_leaked(info.value)
    assert info.value.phase == (
        "retention-plan" if entrypoint == "plan" else "validate"
    )
    assert info.value.path == (root / "output" if entrypoint == "plan" else None)
    assert info.value.__cause__ is injected
    assert snapshot_tree(root) == before


def test_nested_descendant_symlink_refuses_whole_retention_plan_before_deletion(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)

    first = builder.source_output_dir(OLDER, SOURCE)
    first.mkdir(parents=True)
    (first / "first.bin").write_bytes(b"first-output\xff")
    poisoned = builder.source_output_dir(OLDER_NEXT, SOURCE)
    nested = poisoned / "nested" / "deeper"
    nested.mkdir(parents=True)
    (poisoned / "before.bin").write_bytes(b"before\x00")
    (nested / "before-link.bin").write_bytes(b"nested-before\x01")
    outside = tmp_path.resolve() / "outside.bin"
    outside.write_bytes(b"outside\xff")
    linked = nested / "middle-link"
    linked.symlink_to(outside)
    after_link = nested / "z-after-link.bin"
    after_link.write_bytes(b"nested-after\x02")
    (poisoned / "after.bin").write_bytes(b"after\x03")
    first_log = _write_log(root, SOURCE, OLDER, b"first-log\xff")
    poisoned_log = _write_log(root, SOURCE, OLDER_NEXT, b"poisoned-log\xff")

    plan = cleanup.plan_retention(root, SOURCE)
    assert plan.output_dirs == (first, poisoned)
    assert plan.log_files == (first_log, poisoned_log)
    before = snapshot_tree(root)
    outside_before = outside.read_bytes()

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.execute_retention_plan(plan)

    _assert_not_leaked(info.value)
    assert info.value.phase == "retention-execute"
    assert info.value.path == poisoned
    assert snapshot_tree(root) == before
    assert first.is_dir()
    assert (first / "first.bin").read_bytes() == b"first-output\xff"
    assert poisoned.is_dir()
    assert linked.is_symlink()
    assert (nested / "before-link.bin").read_bytes() == b"nested-before\x01"
    assert after_link.read_bytes() == b"nested-after\x02"
    assert first_log.read_bytes() == b"first-log\xff"
    assert poisoned_log.read_bytes() == b"poisoned-log\xff"
    assert outside.read_bytes() == outside_before
