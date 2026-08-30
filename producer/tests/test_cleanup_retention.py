r"""`yd_producer.cleanup` 保留清理行为测试（任务 13.3，issue #25）。

oracle 纪律：删除集合由构造期登记的 cycle/路径给出，不由被测 `RetentionPlan` 回读；
全部断言落在真实 tmp 目录树上。
"""

from __future__ import annotations

import os
import stat
from datetime import timedelta
from pathlib import Path

import pytest
from cleanup_fixtures import (
    D_MINUS_14D,
    D_MINUS_14D_12H,
    ILLEGAL_CYCLE,
    OLDER,
    OLDER_NEXT,
    SIBLING,
    SOURCE,
    D,
    T,
    _assert_not_leaked,
    _assert_survives,
    _assert_wrapped,
    _base,
    _retention_tree,
    _seed_identity_tree,
    _skip_if_root,
    _unreadable,
    _windowed_plan_kwargs,
    _write_log,
    _yd_root,
)
from frontier_fixtures import YdRootBuilder, parse_cycle, snapshot_tree

from yd_producer import cleanup, controller

# --- spec「14 天窗口」 ---


def test_retention_deletes_only_objects_strictly_older_than_d_minus_14d(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    paths = _retention_tree(root)
    expected_output = (paths[f"ifs-out-{D_MINUS_14D_12H}"],)
    expected_logs = (paths[f"ifs-log-{D_MINUS_14D_12H}"],)
    keep = [
        paths[f"ifs-out-{D_MINUS_14D}"],
        paths[f"ifs-out-{D}"],
        paths[f"ifs-out-{T}"],
        paths[f"ifs-log-{D_MINUS_14D}"],
        paths[f"ifs-log-{D}"],
        paths[f"ifs-log-{T}"],
        paths[f"gfs-out-{D_MINUS_14D_12H}"],
        paths[f"gfs-out-{D_MINUS_14D}"],
        paths[f"gfs-out-{D}"],
        paths[f"gfs-log-{D_MINUS_14D_12H}"],
        paths[f"gfs-out-{T}"],
        paths["illegal-dir"],
        paths["stray-log"],
        paths["bak-log"],
    ]
    keep_before = {path: snapshot_tree(path) for path in keep}
    states_before = snapshot_tree(root / "states")
    before_plan = snapshot_tree(root)

    plan = cleanup.plan_retention(root, SOURCE)
    assert plan.yd_root == root
    assert plan.source == SOURCE
    assert plan.latest_done == parse_cycle(D)
    assert plan.cutoff == parse_cycle(D_MINUS_14D)
    assert plan.output_dirs == expected_output
    assert plan.log_files == expected_logs
    assert snapshot_tree(root) == before_plan

    cleanup.execute_retention_plan(plan)

    assert not expected_output[0].exists()
    assert not expected_logs[0].exists()
    assert (root / "output" / D_MINUS_14D_12H).is_dir()
    for path in keep:
        assert snapshot_tree(path) == keep_before[path]
    assert snapshot_tree(root / "states") == states_before
    decision = controller.decide_frontier(
        yd_root=root, source=SOURCE, raw_complete=lambda _cycle: True
    )
    assert decision.cycle == parse_cycle(T)


def test_retention_without_any_done_is_an_empty_plan(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_state(T, SOURCE)
    builder.write_output_dat(D_MINUS_14D_12H, SOURCE)
    _write_log(root, SOURCE, D_MINUS_14D_12H)
    before = snapshot_tree(root)

    plan = cleanup.plan_retention(root, SOURCE)
    assert plan.latest_done is None
    assert plan.cutoff is None
    assert plan.output_dirs == ()
    assert plan.log_files == ()
    cleanup.execute_retention_plan(plan)

    assert snapshot_tree(root) == before


def test_illegal_cycle_and_log_names_are_not_planned(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)
    illegal_dir = root / "output" / ILLEGAL_CYCLE / SOURCE
    illegal_dir.mkdir(parents=True)
    (illegal_dir / "DONE").write_bytes(b"")
    stray_dir = root / "output" / "stray" / SOURCE
    stray_dir.mkdir(parents=True)
    (stray_dir / "DONE").write_bytes(b"")
    bad_log = _write_log(root, SOURCE, "notes")
    bak = root / "logs" / SOURCE / f"{D_MINUS_14D_12H}.log.bak"
    bak.parent.mkdir(parents=True, exist_ok=True)
    bak.write_text("bak\n", encoding="utf-8")
    before_illegal = snapshot_tree(illegal_dir)
    before_stray = snapshot_tree(stray_dir)

    plan = cleanup.plan_retention(root, SOURCE)
    cleanup.execute_retention_plan(plan)

    assert plan.output_dirs == ()
    assert plan.log_files == ()
    assert snapshot_tree(illegal_dir) == before_illegal
    assert snapshot_tree(stray_dir) == before_stray
    assert bad_log.read_text(encoding="utf-8") == "old\n"
    assert bak.read_text(encoding="utf-8") == "bak\n"


@pytest.mark.parametrize("bad_source", ["", ".", "..", "a/b", "ifs/"])
def test_retention_source_gate_rejects_collapsing_names(
    tmp_path: Path, bad_source: str
) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SIBLING)
    builder.write_output_dat(D, SIBLING)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)
    before = snapshot_tree(root)

    with pytest.raises(cleanup.CleanupError) as planned:
        cleanup.plan_retention(root, bad_source)
    _assert_not_leaked(planned.value)
    assert planned.value.phase == "validate"
    assert planned.value.path is None

    with pytest.raises(cleanup.CleanupError) as handmade:
        cleanup.RetentionPlan(
            yd_root=root,
            source=bad_source,
            latest_done=parse_cycle(D),
            cutoff=parse_cycle(D_MINUS_14D),
            output_dirs=(),
            log_files=(),
        )
    _assert_not_leaked(handmade.value)
    assert handmade.value.phase == "validate"
    assert snapshot_tree(root) == before
    assert builder.source_output_dir(D, SIBLING).joinpath("DONE").is_file()
    assert builder.source_output_dir(D, SOURCE).joinpath("DONE").is_file()


# --- RetentionPlan 身份绑定（Phase 2 P0） ---


def test_hand_built_plan_cannot_delete_sibling_source_output(tmp_path: Path) -> None:
    """P0：`source='ifs'` 夹带 GFS output 必须在构造点拒绝，GFS 目录逐项存活。"""
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    gfs_old = builder.source_output_dir(OLDER, SIBLING)
    gfs_old.mkdir(parents=True)
    (gfs_old / "yd.rivqdown.dat").write_text("gfs-keep\n", encoding="utf-8")
    (gfs_old / "DONE").write_bytes(b"")
    before = snapshot_tree(gfs_old)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.RetentionPlan(
            **_windowed_plan_kwargs(root, output_dirs=(gfs_old,))  # type: ignore[arg-type]
        )

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path == gfs_old
    assert snapshot_tree(gfs_old) == before
    assert (gfs_old / "yd.rivqdown.dat").read_text(encoding="utf-8") == "gfs-keep\n"
    assert (gfs_old / "DONE").is_file()


def test_hand_built_plan_cannot_delete_sibling_source_log(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    _seed_identity_tree(root)
    gfs_log = _write_log(root, SIBLING, OLDER, b"gfs-log\n")
    before = snapshot_tree(gfs_log)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.RetentionPlan(
            **_windowed_plan_kwargs(root, log_files=(gfs_log,))  # type: ignore[arg-type]
        )

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path == gfs_log
    assert snapshot_tree(gfs_log) == before
    assert gfs_log.read_bytes() == b"gfs-log\n"


@pytest.mark.parametrize(
    ("cycle", "lane"),
    [
        (D_MINUS_14D, "output"),
        (D_MINUS_14D, "log"),
        (D, "output"),
        (D, "log"),
    ],
    ids=["cutoff-output", "cutoff-log", "in-window-output", "in-window-log"],
)
def test_hand_built_plan_cannot_delete_in_window_or_cutoff_targets(
    tmp_path: Path, cycle: str, lane: str
) -> None:
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    if lane == "output":
        target = builder.source_output_dir(cycle, SOURCE)
        target.mkdir(parents=True, exist_ok=True)
        (target / "yd.rivqdown.dat").write_text("keep\n", encoding="utf-8")
        kwargs = _windowed_plan_kwargs(root, output_dirs=(target,))
    else:
        target = _write_log(root, SOURCE, cycle, b"keep-log\n")
        kwargs = _windowed_plan_kwargs(root, log_files=(target,))
    before = snapshot_tree(target)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.RetentionPlan(**kwargs)  # type: ignore[arg-type]

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path == target
    assert snapshot_tree(target) == before


def test_hand_built_plan_rejects_lane_swap(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    output_dir = builder.source_output_dir(OLDER, SOURCE)
    output_dir.mkdir(parents=True)
    (output_dir / "yd.rivqdown.dat").write_text("keep\n", encoding="utf-8")
    log_file = _write_log(root, SOURCE, OLDER, b"keep-log\n")
    before_output = snapshot_tree(output_dir)
    before_log = snapshot_tree(log_file)

    with pytest.raises(cleanup.CleanupError) as swapped_output:
        cleanup.RetentionPlan(
            **_windowed_plan_kwargs(root, output_dirs=(log_file,))  # type: ignore[arg-type]
        )
    with pytest.raises(cleanup.CleanupError) as swapped_log:
        cleanup.RetentionPlan(
            **_windowed_plan_kwargs(root, log_files=(output_dir,))  # type: ignore[arg-type]
        )

    _assert_not_leaked(swapped_output.value)
    _assert_not_leaked(swapped_log.value)
    assert swapped_output.value.phase == "validate"
    assert swapped_log.value.phase == "validate"
    assert swapped_output.value.path == log_file
    assert swapped_log.value.path == output_dir
    assert snapshot_tree(output_dir) == before_output
    assert snapshot_tree(log_file) == before_log


def test_hand_built_plan_rejects_illegal_cycle_name(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    illegal = root / "output" / ILLEGAL_CYCLE / SOURCE
    illegal.mkdir(parents=True)
    (illegal / "DONE").write_bytes(b"")
    before = snapshot_tree(illegal)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.RetentionPlan(
            **_windowed_plan_kwargs(root, output_dirs=(illegal,))  # type: ignore[arg-type]
        )

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path == illegal
    assert snapshot_tree(illegal) == before
    assert builder.source_output_dir(D, SIBLING).joinpath("DONE").is_file()


@pytest.mark.parametrize(
    ("cutoff", "case"),
    [
        (parse_cycle(D_MINUS_14D) - timedelta(hours=12), "earlier"),
        (parse_cycle(D_MINUS_14D) + timedelta(hours=12), "later"),
    ],
    ids=["earlier", "later"],
)
def test_hand_built_plan_rejects_mismatched_cutoff_in_both_directions(
    tmp_path: Path, cutoff, case: str
) -> None:
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    doomed = builder.source_output_dir(OLDER, SOURCE)
    doomed.mkdir(parents=True)
    (doomed / "keep.txt").write_text("stay\n", encoding="utf-8")
    before = snapshot_tree(doomed)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.RetentionPlan(
            **_windowed_plan_kwargs(  # type: ignore[arg-type]
                root,
                output_dirs=(doomed,),
                cutoff=cutoff,
            )
        )

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate", case
    assert info.value.path is None
    assert snapshot_tree(doomed) == before


def test_nonempty_hand_built_plan_without_current_done_is_rejected(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    doomed = builder.source_output_dir(OLDER, SOURCE)
    doomed.mkdir(parents=True)
    (doomed / "yd.rivqdown.dat").write_bytes(b"doomed-output\xff")
    doomed_log = _write_log(root, SOURCE, OLDER, b"doomed-log\xff")
    before_root = snapshot_tree(root)
    before_doomed = snapshot_tree(doomed)
    before_log = snapshot_tree(doomed_log)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.RetentionPlan(
            yd_root=root,
            source=SOURCE,
            latest_done=parse_cycle(D),
            cutoff=parse_cycle(D_MINUS_14D),
            output_dirs=(doomed,),
            log_files=(doomed_log,),
        )

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path is None
    assert snapshot_tree(root) == before_root
    assert snapshot_tree(doomed) == before_doomed
    assert snapshot_tree(doomed_log) == before_log


def test_older_hand_built_anchor_remains_a_conservative_replay(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    older_anchor = parse_cycle(D) - timedelta(hours=12)
    conservative_cutoff = older_anchor - timedelta(days=14)
    conservative_cycle = "2026081100"
    conservatively_doomed = builder.source_output_dir(conservative_cycle, SOURCE)
    conservatively_doomed.mkdir(parents=True)
    (conservatively_doomed / "yd.rivqdown.dat").write_bytes(b"drop\xff")
    conservatively_doomed_log = _write_log(
        root, SOURCE, conservative_cycle, b"drop-log\xff"
    )
    current_only = builder.source_output_dir(D_MINUS_14D_12H, SOURCE)
    current_only.mkdir(parents=True)
    (current_only / "yd.rivqdown.dat").write_bytes(b"current-window-only\xff")
    current_only_log = _write_log(
        root, SOURCE, D_MINUS_14D_12H, b"current-window-only-log\xff"
    )
    current_before = snapshot_tree(current_only)
    current_log_before = snapshot_tree(current_only_log)
    sibling_before = snapshot_tree(builder.source_output_dir(D, SIBLING))

    plan = cleanup.RetentionPlan(
        yd_root=root,
        source=SOURCE,
        latest_done=older_anchor,
        cutoff=conservative_cutoff,
        output_dirs=(conservatively_doomed,),
        log_files=(conservatively_doomed_log,),
    )
    cleanup.execute_retention_plan(plan)

    assert not conservatively_doomed.exists()
    assert not conservatively_doomed_log.exists()
    assert snapshot_tree(current_only) == current_before
    assert snapshot_tree(current_only_log) == current_log_before
    assert snapshot_tree(builder.source_output_dir(D, SIBLING)) == sibling_before
    assert (current_only / "yd.rivqdown.dat").read_bytes() == b"current-window-only\xff"
    assert current_only_log.read_bytes() == b"current-window-only-log\xff"
    assert builder.source_output_dir(D, SOURCE).joinpath("DONE").is_file()


def test_empty_window_plan_cannot_carry_targets(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    doomed = builder.source_output_dir(OLDER, SOURCE)
    doomed.mkdir(parents=True)
    (doomed / "keep.txt").write_text("stay\n", encoding="utf-8")
    before = snapshot_tree(doomed)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.RetentionPlan(
            yd_root=root,
            source=SOURCE,
            latest_done=None,
            cutoff=None,
            output_dirs=(doomed,),
            log_files=(),
        )

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path == doomed
    assert snapshot_tree(doomed) == before


def test_hand_built_plan_rejects_unsorted_and_duplicate_targets(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    first = builder.source_output_dir(OLDER, SOURCE)
    second = builder.source_output_dir(OLDER_NEXT, SOURCE)
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "keep.txt").write_text("first\n", encoding="utf-8")
    (second / "keep.txt").write_text("second\n", encoding="utf-8")
    before_first = snapshot_tree(first)
    before_second = snapshot_tree(second)

    with pytest.raises(cleanup.CleanupError) as unsorted:
        cleanup.RetentionPlan(
            **_windowed_plan_kwargs(root, output_dirs=(second, first))  # type: ignore[arg-type]
        )
    with pytest.raises(cleanup.CleanupError) as duplicated:
        cleanup.RetentionPlan(
            **_windowed_plan_kwargs(root, output_dirs=(first, first))  # type: ignore[arg-type]
        )

    _assert_not_leaked(unsorted.value)
    _assert_not_leaked(duplicated.value)
    assert unsorted.value.phase == "validate"
    assert duplicated.value.phase == "validate"
    assert snapshot_tree(first) == before_first
    assert snapshot_tree(second) == before_second


def test_hand_built_plan_rejects_sibling_resolved_root(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    other = _base(tmp_path) / "other"
    other.mkdir()
    builder = _seed_identity_tree(root)
    doomed = builder.source_output_dir(OLDER, SOURCE)
    doomed.mkdir(parents=True)
    (doomed / "keep.txt").write_text("stay\n", encoding="utf-8")
    before = snapshot_tree(doomed)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.RetentionPlan(
            **_windowed_plan_kwargs(other, output_dirs=(doomed,))  # type: ignore[arg-type]
        )

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path == doomed
    assert snapshot_tree(doomed) == before


def test_hand_built_plan_rejects_unsorted_log_files(tmp_path: Path) -> None:
    """两个本源旧日志逆序夹带 -> 构造点 validate 拒绝，两份日志字节逐项存活。"""
    root = _yd_root(tmp_path)
    _seed_identity_tree(root)
    older_log = _write_log(root, SOURCE, OLDER, b"older-log\n")
    older_next_log = _write_log(root, SOURCE, OLDER_NEXT, b"older-next-log\n")
    before_older = snapshot_tree(older_log)
    before_older_next = snapshot_tree(older_next_log)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.RetentionPlan(
            **_windowed_plan_kwargs(  # type: ignore[arg-type]
                root, log_files=(older_next_log, older_log)
            )
        )

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path is None
    assert snapshot_tree(older_log) == before_older
    assert snapshot_tree(older_next_log) == before_older_next
    assert older_log.read_bytes() == b"older-log\n"
    assert older_next_log.read_bytes() == b"older-next-log\n"


def test_hand_built_plan_rejects_duplicate_log_files(tmp_path: Path) -> None:
    """同一有效旧日志路径出现两次 -> validate 拒绝，日志字节逐项存活。"""
    root = _yd_root(tmp_path)
    _seed_identity_tree(root)
    older_log = _write_log(root, SOURCE, OLDER, b"single-log\n")
    before_older = snapshot_tree(older_log)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.RetentionPlan(
            **_windowed_plan_kwargs(  # type: ignore[arg-type]
                root, log_files=(older_log, older_log)
            )
        )

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path is None
    assert snapshot_tree(older_log) == before_older
    assert older_log.read_bytes() == b"single-log\n"


def test_hand_built_sorted_log_plan_executes_and_deletes_exactly_two_logs(
    tmp_path: Path,
) -> None:
    """两个本源合法旧日志升序、无 output -> 恰好删这两份，cutoff/窗口内/兄弟源日志存活。"""
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    older_log = _write_log(root, SOURCE, OLDER, b"older-log\n")
    older_next_log = _write_log(root, SOURCE, OLDER_NEXT, b"older-next-log\n")
    cutoff_log = _write_log(root, SOURCE, D_MINUS_14D, b"cutoff-log\n")
    in_window_log = _write_log(root, SOURCE, D, b"in-window-log\n")
    sibling_log = _write_log(root, SIBLING, OLDER, b"sibling-log\n")
    keep_before = {
        cutoff_log: snapshot_tree(cutoff_log),
        in_window_log: snapshot_tree(in_window_log),
        sibling_log: snapshot_tree(sibling_log),
        builder.source_output_dir(D, SOURCE): snapshot_tree(
            builder.source_output_dir(D, SOURCE)
        ),
        builder.source_output_dir(D, SIBLING): snapshot_tree(
            builder.source_output_dir(D, SIBLING)
        ),
    }

    plan = cleanup.RetentionPlan(
        **_windowed_plan_kwargs(  # type: ignore[arg-type]
            root, log_files=(older_log, older_next_log)
        )
    )
    cleanup.execute_retention_plan(plan)

    assert not older_log.exists()
    assert not older_next_log.exists()
    _assert_survives(tuple(keep_before), keep_before)
    assert cutoff_log.read_bytes() == b"cutoff-log\n"
    assert in_window_log.read_bytes() == b"in-window-log\n"
    assert sibling_log.read_bytes() == b"sibling-log\n"
    assert (root / "logs" / SOURCE).is_dir()


def test_hand_built_exact_ifs_plan_deletes_only_those_two_targets(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    doomed_dir = builder.source_output_dir(OLDER, SOURCE)
    doomed_dir.mkdir(parents=True)
    (doomed_dir / "yd.rivqdown.dat").write_text("drop-me\n", encoding="utf-8")
    doomed_log = _write_log(root, SOURCE, OLDER, b"drop-log\n")
    keep_dir = builder.source_output_dir(D_MINUS_14D, SOURCE)
    keep_dir.mkdir(parents=True)
    (keep_dir / "yd.rivqdown.dat").write_text("keep\n", encoding="utf-8")
    keep_log = _write_log(root, SOURCE, D_MINUS_14D, b"keep-log\n")
    sibling_dir = builder.source_output_dir(OLDER, SIBLING)
    sibling_dir.mkdir(parents=True)
    (sibling_dir / "yd.rivqdown.dat").write_text("gfs\n", encoding="utf-8")
    sibling_log = _write_log(root, SIBLING, OLDER, b"gfs-log\n")
    keep_before = {
        keep_dir: snapshot_tree(keep_dir),
        keep_log: snapshot_tree(keep_log),
        sibling_dir: snapshot_tree(sibling_dir),
        sibling_log: snapshot_tree(sibling_log),
        builder.source_output_dir(D, SOURCE): snapshot_tree(
            builder.source_output_dir(D, SOURCE)
        ),
        builder.source_output_dir(D, SIBLING): snapshot_tree(
            builder.source_output_dir(D, SIBLING)
        ),
    }

    plan = cleanup.RetentionPlan(
        **_windowed_plan_kwargs(  # type: ignore[arg-type]
            root,
            output_dirs=(doomed_dir,),
            log_files=(doomed_log,),
        )
    )
    cleanup.execute_retention_plan(plan)

    assert not doomed_dir.exists()
    assert not doomed_log.exists()
    _assert_survives(tuple(keep_before), keep_before)
    assert (root / "output" / OLDER).is_dir()


def test_execute_rebinds_identity_after_post_construction_tampering(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    first = builder.source_output_dir(OLDER, SOURCE)
    second = builder.source_output_dir(OLDER_NEXT, SOURCE)
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "yd.rivqdown.dat").write_text("first\n", encoding="utf-8")
    (second / "yd.rivqdown.dat").write_text("second\n", encoding="utf-8")
    sibling = builder.source_output_dir(OLDER_NEXT, SIBLING)
    sibling.mkdir(parents=True)
    (sibling / "yd.rivqdown.dat").write_text("gfs\n", encoding="utf-8")
    plan = cleanup.RetentionPlan(
        **_windowed_plan_kwargs(root, output_dirs=(first, second))  # type: ignore[arg-type]
    )
    object.__setattr__(plan, "output_dirs", (first, sibling))
    before_first = snapshot_tree(first)
    before_sibling = snapshot_tree(sibling)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.execute_retention_plan(plan)

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path == sibling
    assert snapshot_tree(first) == before_first
    assert snapshot_tree(sibling) == before_sibling
    assert (first / "yd.rivqdown.dat").read_text(encoding="utf-8") == "first\n"
    assert (sibling / "yd.rivqdown.dat").read_text(encoding="utf-8") == "gfs\n"


@pytest.mark.parametrize("entrypoint", ["construct", "execute"])
def test_future_retention_anchor_is_rejected_before_any_target_mutates(
    tmp_path: Path, entrypoint: str
) -> None:
    root = _yd_root(tmp_path)
    builder = _seed_identity_tree(root)
    protected = builder.source_output_dir("2026081300", SOURCE)
    protected.mkdir(parents=True)
    (protected / "yd.rivqdown.dat").write_bytes(b"protected-output\xff")
    (protected / "DONE").write_bytes(b"")
    protected_log = _write_log(root, SOURCE, "2026081300", b"protected-log\xff")
    sibling = builder.source_output_dir("2026081300", SIBLING)
    sibling.mkdir(parents=True)
    (sibling / "yd.rivqdown.dat").write_bytes(b"sibling-output\xff")
    latest = parse_cycle(D) + timedelta(days=2)
    kwargs = {
        "yd_root": root,
        "source": SOURCE,
        "latest_done": latest,
        "cutoff": latest - timedelta(days=14),
        "output_dirs": (protected,),
        "log_files": (protected_log,),
    }
    before = snapshot_tree(root)

    with pytest.raises(cleanup.CleanupError) as info:
        if entrypoint == "construct":
            cleanup.RetentionPlan(**kwargs)  # type: ignore[arg-type]
        else:
            plan = cleanup.RetentionPlan(
                **_windowed_plan_kwargs(root)  # type: ignore[arg-type]
            )
            for name, value in kwargs.items():
                object.__setattr__(plan, name, value)
            cleanup.execute_retention_plan(plan)

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path is None
    assert snapshot_tree(root) == before
    assert (protected / "yd.rivqdown.dat").read_bytes() == b"protected-output\xff"
    assert (protected / "DONE").is_file()
    assert protected_log.read_bytes() == b"protected-log\xff"
    assert (sibling / "yd.rivqdown.dat").read_bytes() == b"sibling-output\xff"
    assert builder.source_output_dir(D, SOURCE).joinpath("DONE").is_file()


# --- spec「symlink 越界拒删」 ---


def test_plan_refuses_an_outside_symlink_output_dir_and_deletes_nothing(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)
    doomed = builder.source_output_dir(D_MINUS_14D_12H, SOURCE)
    doomed.mkdir(parents=True)
    (doomed / "yd.rivqdown.dat").write_text("drop-me\n", encoding="utf-8")
    outside = _base(tmp_path) / "outside"
    outside.mkdir()
    target = outside / "borrowed"
    target.mkdir()
    (target / "keep.txt").write_text("nwm\n", encoding="utf-8")
    linked = builder.source_output_dir(OLDER, SOURCE)
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(target, target_is_directory=True)
    before_doomed = snapshot_tree(doomed)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.plan_retention(root, SOURCE)

    _assert_not_leaked(info.value)
    assert info.value.phase == "retention-plan"
    assert info.value.path == linked
    assert str(linked) in str(info.value)
    assert linked.is_symlink()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "nwm\n"
    assert snapshot_tree(doomed) == before_doomed


def test_plan_refuses_an_outside_symlink_failure_log_and_deletes_nothing(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)
    doomed = _write_log(root, SOURCE, D_MINUS_14D_12H, b"drop-me\n")
    outside = _base(tmp_path) / "outside"
    outside.mkdir()
    target = outside / "stolen.log"
    target.write_bytes(b"secret\n")
    linked = root / "logs" / SOURCE / f"{OLDER}.log"
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(target)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.plan_retention(root, SOURCE)

    _assert_not_leaked(info.value)
    assert info.value.phase == "retention-plan"
    assert info.value.path == linked
    assert linked.is_symlink()
    assert target.read_bytes() == b"secret\n"
    assert doomed.read_bytes() == b"drop-me\n"


def test_execute_full_precheck_refuses_toctou_swap_before_any_delete(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)
    first = builder.source_output_dir(OLDER, SOURCE)
    second = builder.source_output_dir(OLDER_NEXT, SOURCE)
    first.mkdir(parents=True)
    (first / "yd.rivqdown.dat").write_text("first\n", encoding="utf-8")
    second.mkdir(parents=True)
    (second / "yd.rivqdown.dat").write_text("second\n", encoding="utf-8")
    expected_first = first
    expected_second = second

    plan = cleanup.plan_retention(root, SOURCE)
    assert plan.output_dirs == (expected_first, expected_second)

    outside = _base(tmp_path) / "outside"
    outside.mkdir()
    target = outside / "borrowed"
    target.mkdir()
    (target / "keep.txt").write_text("nwm\n", encoding="utf-8")
    for child in second.iterdir():
        child.unlink()
    second.rmdir()
    second.symlink_to(target, target_is_directory=True)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.execute_retention_plan(plan)

    _assert_not_leaked(info.value)
    assert info.value.phase == "retention-execute"
    assert info.value.path == expected_second
    assert expected_first.is_dir()
    assert (expected_first / "yd.rivqdown.dat").read_text(encoding="utf-8") == "first\n"
    assert expected_second.is_symlink()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "nwm\n"


def test_static_internal_symlink_refuses_full_plan_before_any_deletion(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)
    valid = builder.source_output_dir(OLDER, SOURCE)
    valid.mkdir(parents=True)
    (valid / "yd.rivqdown.dat").write_bytes(b"valid-output\xff")
    poisoned = builder.source_output_dir(D_MINUS_14D_12H, SOURCE)
    poisoned.mkdir(parents=True)
    before_link = poisoned / "before.bin"
    before_link.write_bytes(b"before\x00")
    outside = _base(tmp_path) / "outside"
    outside.mkdir()
    target = outside / "raw.dat"
    target.write_bytes(b"outside\xff")
    linked = poisoned / "linked.dat"
    linked.symlink_to(target)
    after_link = poisoned / "after.bin"
    after_link.write_bytes(b"after\x01")
    doomed_log = _write_log(root, SOURCE, D_MINUS_14D_12H, b"old-log\xff")
    plan = cleanup.plan_retention(root, SOURCE)
    assert plan.output_dirs == (valid, poisoned)
    assert plan.log_files == (doomed_log,)
    valid_before = snapshot_tree(valid)
    poisoned_before = snapshot_tree(poisoned)
    log_before = snapshot_tree(doomed_log)
    outside_before = target.read_bytes()

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.execute_retention_plan(plan)

    _assert_not_leaked(info.value)
    assert info.value.phase == "retention-execute"
    assert info.value.path == poisoned
    assert snapshot_tree(valid) == valid_before
    assert snapshot_tree(poisoned) == poisoned_before
    assert snapshot_tree(doomed_log) == log_before
    assert linked.is_symlink()
    assert before_link.read_bytes() == b"before\x00"
    assert after_link.read_bytes() == b"after\x01"
    assert target.read_bytes() == outside_before


@pytest.mark.parametrize("shape", ["file", "fifo"])
def test_unexpected_output_lane_shape_is_a_stable_error(
    tmp_path: Path, shape: str
) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)
    parent = root / "output" / D_MINUS_14D_12H
    parent.mkdir(parents=True)
    lane = parent / SOURCE
    if shape == "file":
        lane.write_text("not a directory\n", encoding="utf-8")
    else:
        os.mkfifo(lane)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.plan_retention(root, SOURCE)

    _assert_not_leaked(info.value)
    assert info.value.phase == "retention-plan"
    assert info.value.path == lane
    if shape == "file":
        assert lane.read_text(encoding="utf-8") == "not a directory\n"
    else:
        assert stat.S_ISFIFO(lane.lstat().st_mode)


def test_symlinked_yd_root_plans_and_executes_against_the_real_path(
    tmp_path: Path,
) -> None:
    real = _base(tmp_path) / "real"
    real.mkdir()
    link = _base(tmp_path) / "link"
    link.symlink_to(real, target_is_directory=True)
    root = real / "yd"
    root.mkdir()
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)
    doomed_dir = builder.source_output_dir(D_MINUS_14D_12H, SOURCE)
    doomed_dir.mkdir(parents=True)
    (doomed_dir / "yd.rivqdown.dat").write_text("old\n", encoding="utf-8")
    doomed_log = _write_log(root, SOURCE, D_MINUS_14D_12H, b"old-log\n")
    unresolved = link / "yd"

    plan = cleanup.plan_retention(unresolved, SOURCE)
    assert plan.yd_root == root
    assert plan.yd_root != unresolved
    assert plan.output_dirs == (doomed_dir,)
    assert plan.log_files == (doomed_log,)
    cleanup.execute_retention_plan(plan)
    assert not doomed_dir.exists()
    assert not doomed_log.exists()

    cleaned = snapshot_tree(root)
    cleanup.execute_retention_plan(plan)
    assert snapshot_tree(root) == cleaned
    replayed = cleanup.plan_retention(unresolved, SOURCE)
    assert replayed.output_dirs == ()
    assert replayed.log_files == ()
    cleanup.execute_retention_plan(replayed)
    assert snapshot_tree(root) == cleaned


# --- 公共错误域矩阵（保留侧） ---


def test_unreadable_output_dir_is_retention_plan(tmp_path: Path) -> None:
    _skip_if_root()
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    output = root / "output"
    with _unreadable(output), pytest.raises(cleanup.CleanupError) as info:
        cleanup.plan_retention(root, SOURCE)
    _assert_wrapped(info.value)
    assert info.value.phase == "retention-plan"


def test_symlink_done_anchor_is_retention_plan(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_output_dat(D, SOURCE)
    outside = _base(tmp_path) / "outside"
    outside.mkdir()
    target = outside / "DONE"
    target.write_bytes(b"")
    done = builder.source_output_dir(D, SOURCE) / "DONE"
    done.symlink_to(target)
    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.plan_retention(root, SOURCE)
    _assert_not_leaked(info.value)
    assert info.value.phase == "retention-plan"
    assert info.value.path == done
    assert target.exists()


def test_hand_built_plan_with_outside_target_is_validate(tmp_path: Path) -> None:
    """根外路径现在在身份绑定层就被拒，不再等到 retention-execute。"""
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    outside = _base(tmp_path) / "outside"
    outside.mkdir()
    target = outside / "borrowed"
    target.mkdir()
    (target / "keep.txt").write_text("nwm\n", encoding="utf-8")
    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.RetentionPlan(
            yd_root=root,
            source=SOURCE,
            latest_done=parse_cycle(D),
            cutoff=parse_cycle(D_MINUS_14D),
            output_dirs=(target,),
            log_files=(),
        )
    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert info.value.path == target
    assert (target / "keep.txt").read_text(encoding="utf-8") == "nwm\n"


def test_execute_phase_refuses_internal_symlink_after_identity_binds(
    tmp_path: Path,
) -> None:
    """身份绑定通过后，执行期 realpath/type 闸仍把树内越界 symlink 收敛为 retention-execute。"""
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)
    doomed = builder.source_output_dir(OLDER, SOURCE)
    doomed.mkdir(parents=True)
    outside = _base(tmp_path) / "outside"
    outside.mkdir()
    target = outside / "raw.dat"
    target.write_text("nwm\n", encoding="utf-8")
    (doomed / "linked.dat").symlink_to(target)
    plan = cleanup.RetentionPlan(
        **_windowed_plan_kwargs(root, output_dirs=(doomed,))  # type: ignore[arg-type]
    )
    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.execute_retention_plan(plan)
    _assert_not_leaked(info.value)
    assert info.value.phase == "retention-execute"
    assert info.value.path == doomed
    assert doomed.is_dir()
    assert target.read_text(encoding="utf-8") == "nwm\n"
