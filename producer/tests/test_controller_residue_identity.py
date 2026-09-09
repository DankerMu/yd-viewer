r"""ResiduePlan 构造/执行身份绑定回归（issue #112）。

oracle 纪律与 `test_controller_residue.py` 相同：期望路径由写入时记下的字面量给出，
断言落在真实递归快照上。本文件覆盖手构越界、构造绕过、规范化合法计划；planner
的 ValueError/ResidueError 与合法 symlink 执行序仍由原文件钉死。
"""

from __future__ import annotations

import pathlib

import pytest
from frontier_fixtures import YdRootBuilder, parse_cycle, snapshot_tree
from test_controller_residue import (
    T_PLUS_12,
    T_PLUS_24,
    D,
    T,
    _crash_residue_tree,
    _plan,
    _yd_root,
)

from yd_producer import residue
from yd_producer.store.safe_fs import SafeFilesystemError

SOURCE = "ifs"
SIBLING = "gfs"


def _legal_kwargs(
    root: pathlib.Path,
    *,
    state_files: tuple[pathlib.Path, ...] | None = None,
    half_product_dirs: tuple[pathlib.Path, ...] | None = None,
) -> dict[str, object]:
    builder = YdRootBuilder(root=root)
    if state_files is None:
        state_files = (builder.state_path(T_PLUS_12, SOURCE),)
    if half_product_dirs is None:
        half_product_dirs = (builder.source_output_dir(T, SOURCE),)
    return {
        "yd_root": root,
        "source": SOURCE,
        "retained_cycle": parse_cycle(T),
        "state_files": state_files,
        "half_product_dirs": half_product_dirs,
    }


def _assert_unsafe(error: SafeFilesystemError) -> None:
    assert error.kind == "unsafe"
    assert not hasattr(error, "phase")


def _bypass_plan(**kwargs: object) -> residue.ResiduePlan:
    plan = residue.ResiduePlan.__new__(residue.ResiduePlan)
    for name, value in kwargs.items():
        object.__setattr__(plan, name, value)
    return plan


def test_hand_built_plan_cannot_delete_sibling_source_output(
    tmp_path: pathlib.Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_done(T, SIBLING)
    builder.write_output_dat(T, SIBLING)
    sibling = builder.source_output_dir(T, SIBLING)
    before = snapshot_tree(root)

    with pytest.raises(SafeFilesystemError) as info:
        residue.ResiduePlan(
            **_legal_kwargs(root, half_product_dirs=(sibling,), state_files=())
        )

    _assert_unsafe(info.value)
    assert snapshot_tree(root) == before
    assert (sibling / "DONE").is_file()


def test_hand_built_plan_cannot_delete_sibling_source_state(
    tmp_path: pathlib.Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_done(D, SIBLING)
    builder.write_state(T, SIBLING)
    builder.write_state(T_PLUS_12, SIBLING)
    sibling_state = builder.state_path(T_PLUS_12, SIBLING)
    before = snapshot_tree(root)

    with pytest.raises(SafeFilesystemError) as info:
        residue.ResiduePlan(
            **_legal_kwargs(root, state_files=(sibling_state,), half_product_dirs=())
        )

    _assert_unsafe(info.value)
    assert snapshot_tree(root) == before
    assert sibling_state.is_file()


@pytest.mark.parametrize(
    "lane",
    ["output-as-state", "state-as-output"],
    ids=["output-as-state", "state-as-output"],
)
def test_hand_built_plan_rejects_lane_swap(tmp_path: pathlib.Path, lane: str) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    half = builder.source_output_dir(T, SOURCE)
    later = builder.state_path(T_PLUS_12, SOURCE)
    before = snapshot_tree(root)
    if lane == "output-as-state":
        kwargs = _legal_kwargs(root, state_files=(half,), half_product_dirs=())
    else:
        kwargs = _legal_kwargs(root, state_files=(), half_product_dirs=(later,))

    with pytest.raises(SafeFilesystemError) as info:
        residue.ResiduePlan(**kwargs)

    _assert_unsafe(info.value)
    assert snapshot_tree(root) == before


def test_hand_built_plan_rejects_output_cycle_other_than_retained(
    tmp_path: pathlib.Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    wrong = builder.source_output_dir(D, SOURCE)
    before = snapshot_tree(root)

    with pytest.raises(SafeFilesystemError) as info:
        residue.ResiduePlan(
            **_legal_kwargs(root, half_product_dirs=(wrong,), state_files=())
        )

    _assert_unsafe(info.value)
    assert snapshot_tree(root) == before
    assert builder.source_output_dir(D, SOURCE).joinpath("DONE").is_file()


@pytest.mark.parametrize(
    "cycle", [T, D], ids=["equal-retained", "earlier-than-retained"]
)
def test_hand_built_plan_rejects_state_cycle_not_strictly_later(
    tmp_path: pathlib.Path, cycle: str
) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_state(D, SOURCE)
    target = builder.state_path(cycle, SOURCE)
    before = snapshot_tree(root)

    with pytest.raises(SafeFilesystemError) as info:
        residue.ResiduePlan(
            **_legal_kwargs(root, state_files=(target,), half_product_dirs=())
        )

    _assert_unsafe(info.value)
    assert snapshot_tree(root) == before
    assert target.is_file()


def test_hand_built_plan_rejects_wrong_state_suffix(
    tmp_path: pathlib.Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    wrong = builder.states_dir(SOURCE) / f"{T_PLUS_12}.ic"
    wrong.write_bytes(b"not-a-state\n")
    before = snapshot_tree(root)

    with pytest.raises(SafeFilesystemError) as info:
        residue.ResiduePlan(
            **_legal_kwargs(root, state_files=(wrong,), half_product_dirs=())
        )

    _assert_unsafe(info.value)
    assert snapshot_tree(root) == before
    assert wrong.read_bytes() == b"not-a-state\n"


@pytest.mark.parametrize(
    "bad_source",
    ["", ".", "..", "a/b", "ifs/", "ifs\x00"],
    ids=["empty", "dot", "dotdot", "slash", "trailing-slash", "nul"],
)
def test_hand_built_plan_rejects_collapsing_source_names(
    tmp_path: pathlib.Path, bad_source: str
) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_done(T, SIBLING)
    builder.write_output_dat(T, SIBLING)
    before = snapshot_tree(root)

    with pytest.raises(SafeFilesystemError) as info:
        residue.ResiduePlan(
            yd_root=root,
            source=bad_source,
            retained_cycle=parse_cycle(T),
            state_files=(),
            half_product_dirs=(),
        )

    _assert_unsafe(info.value)
    assert snapshot_tree(root) == before
    assert builder.source_output_dir(T, SIBLING).joinpath("DONE").is_file()


def test_execute_rebinds_after_setattr_illegal_later_state(
    tmp_path: pathlib.Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_done(D, SIBLING)
    builder.write_state(T_PLUS_12, SIBLING)
    legal_half = builder.source_output_dir(T, SOURCE)
    illegal_state = builder.state_path(T_PLUS_12, SIBLING)
    plan = residue.ResiduePlan(**_legal_kwargs(root))
    object.__setattr__(plan, "state_files", (illegal_state,))
    object.__setattr__(plan, "half_product_dirs", (legal_half,))
    before = snapshot_tree(root)

    with pytest.raises(SafeFilesystemError) as info:
        residue.execute_residue_plan(plan)

    _assert_unsafe(info.value)
    assert snapshot_tree(root) == before
    assert legal_half.is_dir()
    assert illegal_state.is_file()


def test_execute_rebinds_after_setattr_illegal_half_product(
    tmp_path: pathlib.Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_done(T, SIBLING)
    builder.write_output_dat(T, SIBLING)
    legal_state = builder.state_path(T_PLUS_12, SOURCE)
    illegal_half = builder.source_output_dir(T, SIBLING)
    plan = residue.ResiduePlan(**_legal_kwargs(root))
    object.__setattr__(plan, "state_files", (legal_state,))
    object.__setattr__(plan, "half_product_dirs", (illegal_half,))
    before = snapshot_tree(root)

    with pytest.raises(SafeFilesystemError) as info:
        residue.execute_residue_plan(plan)

    _assert_unsafe(info.value)
    assert snapshot_tree(root) == before
    assert legal_state.is_file()
    assert (illegal_half / "DONE").is_file()


def test_execute_rejects_object_new_bypass_before_any_delete(
    tmp_path: pathlib.Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_done(T, SIBLING)
    builder.write_output_dat(T, SIBLING)
    legal_half = builder.source_output_dir(T, SOURCE)
    illegal_state = builder.state_path(T, SOURCE)
    before = snapshot_tree(root)
    plan = _bypass_plan(
        yd_root=root,
        source=SOURCE,
        retained_cycle=parse_cycle(T),
        state_files=(illegal_state,),
        half_product_dirs=(legal_half,),
    )

    with pytest.raises(SafeFilesystemError) as info:
        residue.execute_residue_plan(plan)

    _assert_unsafe(info.value)
    assert snapshot_tree(root) == before
    assert legal_half.is_dir()
    assert illegal_state.is_file()
    assert builder.source_output_dir(T, SIBLING).joinpath("DONE").is_file()


def test_execute_rejects_object_new_unsafe_half_product(
    tmp_path: pathlib.Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_done(T, SIBLING)
    builder.write_output_dat(T, SIBLING)
    legal_state = builder.state_path(T_PLUS_12, SOURCE)
    illegal_half = builder.source_output_dir(T, SIBLING)
    before = snapshot_tree(root)
    plan = _bypass_plan(
        yd_root=root,
        source=SOURCE,
        retained_cycle=parse_cycle(T),
        state_files=(legal_state,),
        half_product_dirs=(illegal_half,),
    )

    with pytest.raises(SafeFilesystemError) as info:
        residue.execute_residue_plan(plan)

    _assert_unsafe(info.value)
    assert snapshot_tree(root) == before
    assert legal_state.is_file()
    assert (illegal_half / "DONE").is_file()


def test_hand_built_sorted_deduplicated_plan_deletes_named_targets_only(
    tmp_path: pathlib.Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_state(T_PLUS_24, SOURCE)
    builder.write_done(D, SIBLING)
    builder.write_state(T, SIBLING)
    builder.write_state(T_PLUS_12, SIBLING)
    builder.write_output_dat(T, SIBLING)
    later_a = builder.state_path(T_PLUS_12, SOURCE)
    later_b = builder.state_path(T_PLUS_24, SOURCE)
    half = builder.source_output_dir(T, SOURCE)
    retained_state = builder.state_path(T, SOURCE)
    retained_output = builder.source_output_dir(D, SOURCE)
    sibling_half = builder.source_output_dir(T, SIBLING)
    sibling_state = builder.state_path(T_PLUS_12, SIBLING)
    keep_before = {
        retained_state: snapshot_tree(retained_state),
        retained_output: snapshot_tree(retained_output),
        sibling_half: snapshot_tree(sibling_half),
        sibling_state: snapshot_tree(sibling_state),
    }

    plan = residue.ResiduePlan(
        **_legal_kwargs(
            root,
            state_files=(later_b, later_a, later_a),
            half_product_dirs=(half, half),
        )
    )
    assert plan.state_files == (later_a, later_b)
    assert plan.half_product_dirs == (half,)
    residue.execute_residue_plan(plan)
    residue.execute_residue_plan(plan)

    assert not later_a.exists()
    assert not later_b.exists()
    assert not half.exists()
    assert snapshot_tree(retained_state) == keep_before[retained_state]
    assert snapshot_tree(retained_output) == keep_before[retained_output]
    assert snapshot_tree(sibling_half) == keep_before[sibling_half]
    assert snapshot_tree(sibling_state) == keep_before[sibling_state]
    assert retained_state.is_file()
    assert (retained_output / "DONE").is_file()
    assert (sibling_half / "yd.rivqdown.dat").is_file()


def test_hand_built_plan_resolves_root_alias_and_deletes_named_targets(
    tmp_path: pathlib.Path,
) -> None:
    real = tmp_path.resolve() / "real"
    real.mkdir()
    link = tmp_path.resolve() / "link"
    link.symlink_to(real, target_is_directory=True)
    root = real / "yd"
    root.mkdir()
    builder = _crash_residue_tree(root)
    unresolved = link / "yd"
    later = builder.state_path(T_PLUS_12, SOURCE)
    half = builder.source_output_dir(T, SOURCE)
    retained = builder.state_path(T, SOURCE)
    done_dir = builder.source_output_dir(D, SOURCE)

    plan = residue.ResiduePlan(
        yd_root=unresolved,
        source=SOURCE,
        retained_cycle=parse_cycle(T),
        state_files=(later,),
        half_product_dirs=(half,),
    )
    assert plan.yd_root == root
    residue.execute_residue_plan(plan)
    residue.execute_residue_plan(plan)

    assert not later.exists()
    assert not half.exists()
    assert retained.is_file()
    assert (done_dir / "DONE").is_file()


def test_planner_product_executes_and_is_idempotent(tmp_path: pathlib.Path) -> None:
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_done(D, SIBLING)
    builder.write_output_dat(T, SIBLING)
    sibling_half = builder.source_output_dir(T, SIBLING)
    sibling_before = snapshot_tree(sibling_half)
    done_before = snapshot_tree(builder.source_output_dir(D, SOURCE))

    plan = _plan(builder, SOURCE)
    assert plan is not None
    residue.execute_residue_plan(plan)
    residue.execute_residue_plan(plan)

    assert not builder.state_path(T_PLUS_12, SOURCE).exists()
    assert not builder.source_output_dir(T, SOURCE).exists()
    assert builder.state_path(T, SOURCE).is_file()
    assert snapshot_tree(builder.source_output_dir(D, SOURCE)) == done_before
    assert snapshot_tree(sibling_half) == sibling_before
