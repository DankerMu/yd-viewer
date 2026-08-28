r"""`yd_producer.residue` 的行为测试（未提交残留的判定与清理，任务 12.2）。

oracle 纪律：删除集合的期望值是**手算**的——由 `frontier_fixtures` 在写入时记下的那几个
路径，不由被测函数回读。所有断言都落在**真实文件系统**上（`snapshot_tree` 的递归快照），
不用记录型 fake：删除是真实文件系统动作，记录型 fake 会让「删对了没有」退化为永真式。

承重条不是「脏树上算出一个非空清单」——那对「多删一类」的实现同样恒绿。真正判别的是：
边界方向用例钉死 T 自己永不进清单（`>` 而不是 `>=`）；逐源隔离用例钉死清理不越到兄弟源
与 `output/<T>/` 父目录；不可见条目用例钉死只删能被正面识别为残留的路径；两侧 symlink
用例钉死 `remove_tree_allow_symlinks` / `unlink_no_follow` 的不对称策略；containment 用例
钉死 `containment_root` 就是 `YD_ROOT`；幂等用例钉死重复执行同一份清单是 no-op。

全部测试树用 `tmp_path.resolve()`：`safe_fs._open_directory_no_follow` 会把
`containment_root` 自身的每一个祖先分量重新过一遍 `O_NOFOLLOW`（`safe_fs.py:824-843`），
而 macOS 的 `/var` 是 symlink，未 resolve 的 `tmp_path` 会得到与被测逻辑无关的红。
"""

from __future__ import annotations

import pathlib

import pytest
from frontier_fixtures import (
    RecordingRawComplete,
    YdRootBuilder,
    parse_cycle,
    snapshot_tree,
)

from yd_producer import controller, residue
from yd_producer.store.safe_fs import SafeFilesystemError

#: 锚点 cycle（手算：2026-08-26 是 epoch 后第 20691 天）。
D = "2026082600"
T = "2026082612"
T_PLUS_12 = "2026082700"
T_PLUS_24 = "2026082712"
FRESH = "2026082000"
FRESH_NEXT = "2026082012"

#: raw 完整性不在本文件的判别面内：全部 cycle 一律作答「齐」。
_ALL_CYCLES = frozenset({D, T, T_PLUS_12, T_PLUS_24, FRESH, FRESH_NEXT})


def _yd_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """`YD_ROOT`；与 `outside/` 同级，供越界 symlink 用例构造根外目标。"""
    root = tmp_path.resolve() / "yd"
    root.mkdir()
    return root


def _decide(builder: YdRootBuilder, source: str) -> controller.FrontierDecision:
    return controller.decide_frontier(
        yd_root=builder.root,
        source=source,
        raw_complete=RecordingRawComplete(set(_ALL_CYCLES)),
    )


def _plan(builder: YdRootBuilder, source: str) -> residue.ResiduePlan | None:
    return residue.plan_residue(
        yd_root=builder.root,
        source=source,
        decision=_decide(builder, source),
    )


def _crash_residue_tree(root: pathlib.Path, source: str = "ifs") -> YdRootBuilder:
    """spec「崩溃残留恢复」的标准树：`DONE(T-12)` + T/T+12 状态 + 无 `DONE` 的 T 半成品。"""
    builder = YdRootBuilder(root=root)
    builder.write_done(D, source)
    builder.write_output_dat(D, source)
    builder.write_state(T, source)
    builder.write_state(T_PLUS_12, source)
    builder.write_output_dat(T, source)
    return builder


# --- 判定：零写入 ---


def test_plan_residue_touches_nothing(tmp_path: pathlib.Path) -> None:
    """判定 MUST 零写入：整棵树的递归快照（含 mtime）逐项相等（裁决 1）。"""
    builder = _crash_residue_tree(_yd_root(tmp_path))

    before = snapshot_tree(builder.root)
    plan = _plan(builder, "ifs")
    after = snapshot_tree(builder.root)

    assert after == before
    # 清单非空，否则「零写入」是空转出来的
    assert plan is not None
    assert plan.state_files == (builder.state_path(T_PLUS_12, "ifs"),)
    assert plan.half_product_dirs == (builder.source_output_dir(T, "ifs"),)


# --- 保留 T 与边界方向 ---


def test_cleanup_keeps_t_and_frontier_still_returns_t(tmp_path: pathlib.Path) -> None:
    """清理后 T 的状态仍在、更晚状态与半成品已删、`DONE(T-12)` 整棵未动，且 T 不变。"""
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    done_before = snapshot_tree(root / "output" / D)

    plan = _plan(builder, "ifs")
    assert plan is not None
    assert plan.retained_cycle == parse_cycle(T)
    residue.execute_residue_plan(plan)

    assert builder.state_path(T, "ifs").is_file()
    assert not builder.state_path(T_PLUS_12, "ifs").exists()
    assert not builder.source_output_dir(T, "ifs").exists()
    assert (root / "output" / T).is_dir()
    assert snapshot_tree(root / "output" / D) == done_before

    after = _decide(builder, "ifs")
    assert after.cycle == parse_cycle(T)


def test_only_strictly_later_states_are_planned(tmp_path: pathlib.Path) -> None:
    """边界方向：cycle **恰好等于** T 的状态永不进清单；多份更晚状态一次全删。"""
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_state(T_PLUS_24, "ifs")

    plan = _plan(builder, "ifs")
    assert plan is not None
    assert plan.state_files == (
        builder.state_path(T_PLUS_12, "ifs"),
        builder.state_path(T_PLUS_24, "ifs"),
    )
    assert builder.state_path(T, "ifs") not in plan.state_files

    residue.execute_residue_plan(plan)

    assert builder.state_path(T, "ifs").is_file()
    assert not builder.state_path(T_PLUS_12, "ifs").exists()
    assert not builder.state_path(T_PLUS_24, "ifs").exists()


# --- 逐源隔离 ---


def test_cleanup_is_scoped_to_one_source(tmp_path: pathlib.Path) -> None:
    """IFS 与 GFS 在同一 cycle 上各有残留时，只清 IFS；GFS 侧递归快照不变。"""
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root, "ifs")
    builder.write_done(D, "gfs")
    builder.write_state(T, "gfs")
    builder.write_state(T_PLUS_12, "gfs")
    builder.write_output_dat(T, "gfs")

    gfs_states_before = snapshot_tree(builder.states_dir("gfs"))
    gfs_output_before = snapshot_tree(builder.source_output_dir(T, "gfs"))

    plan = _plan(builder, "ifs")
    assert plan is not None
    residue.execute_residue_plan(plan)

    assert not builder.source_output_dir(T, "ifs").exists()
    assert not builder.state_path(T_PLUS_12, "ifs").exists()
    # 父目录 output/<T>/ 在本源子目录删完后仍存在（是否删空目录归 13.3）
    assert (root / "output" / T).is_dir()
    assert snapshot_tree(builder.states_dir("gfs")) == gfs_states_before
    assert snapshot_tree(builder.source_output_dir(T, "gfs")) == gfs_output_before


# --- `DONE` 保护 ---


def test_output_dir_with_done_is_never_planned(tmp_path: pathlib.Path) -> None:
    """`output/<cycle>/<source>/` 下有普通文件 `DONE` 与 DAT 时不在清单内、零删除。"""
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, "ifs")
    builder.write_done(T, "ifs")
    builder.write_output_dat(T, "ifs")
    builder.write_state(T, "ifs")
    builder.write_state(T_PLUS_12, "ifs")

    before = snapshot_tree(root)
    decision = _decide(builder, "ifs")
    # DONE(T) 已在，前沿推进到 T+12：本轮没有任何残留
    assert decision.cycle == parse_cycle(T_PLUS_12)

    plan = _plan(builder, "ifs")
    assert plan is not None
    assert plan.empty
    residue.execute_residue_plan(plan)

    assert snapshot_tree(root) == before


def test_done_is_rechecked_for_the_retained_cycle(tmp_path: pathlib.Path) -> None:
    """裁决 4：`DONE` 是每个 `output/<cycle>/<source>/` 的删除**前置**，逐源 stat 普通文件。

    这里的 `FrontierDecision` 是**直接构造**的，理由是判别力：真实的 `decide_frontier`
    在 `DONE(T)` 存在时会把前沿推到 T+12，于是「被交来的 T 上已经有 `DONE`」这个形态在
    发现路径上不可达——而它恰恰是任务 13.2 的复用姿态（失败/重跑路径按调用方自己的理由
    交来一个 T）。少了这条，把 `DONE` 前置整条删掉的实现在全套用例下恒绿。
    清单 MUST 为空：`DONE` 是唯一完成标志（products-contract §4.1）。
    """
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(T, "ifs")
    builder.write_output_dat(T, "ifs")
    builder.write_state(T, "ifs")
    before = snapshot_tree(root)

    handed = controller.FrontierDecision(
        source="ifs",
        cycle=parse_cycle(T),
        stop_reason=None,
        detail="13.2 复用姿态：调用方交来的待跑 T",
    )
    plan = residue.plan_residue(yd_root=root, source="ifs", decision=handed)

    assert plan is not None
    assert plan.empty
    residue.execute_residue_plan(plan)
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("shape", ["directory", "dangling_symlink"])
def test_non_regular_done_does_not_protect_the_output_dir(
    tmp_path: pathlib.Path, shape: str
) -> None:
    """`DONE` 是目录或断链 symlink -> 按普通文件判据视为无 `DONE`，整棵进清单。

    与 `controller.done_cycles` 的判据一字不差（products-contract §4.1）。「目录非空」
    绝不能代替 `DONE`：这两棵树都非空，却都是半成品。
    """
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    if shape == "directory":
        builder.write_done_as_directory(T, "ifs")
    else:
        builder.write_done_as_dangling_symlink(T, "ifs")

    plan = _plan(builder, "ifs")
    assert plan is not None
    assert plan.half_product_dirs == (builder.source_output_dir(T, "ifs"),)
    residue.execute_residue_plan(plan)

    assert not builder.source_output_dir(T, "ifs").exists()
    assert builder.state_path(T, "ifs").is_file()


def test_empty_output_dir_is_a_half_product(tmp_path: pathlib.Path) -> None:
    """`output/<T>/<source>/` 存在但为空（mkdir 后即崩）-> 判为半成品并删除。"""
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")
    builder.source_output_dir(T, "ifs").mkdir(parents=True)

    plan = _plan(builder, "ifs")
    assert plan is not None
    assert plan.half_product_dirs == (builder.source_output_dir(T, "ifs"),)
    residue.execute_residue_plan(plan)

    assert not builder.source_output_dir(T, "ifs").exists()
    assert (root / "output" / T).is_dir()


# --- 不可见条目 ---


def test_invisible_entries_are_never_deleted(tmp_path: pathlib.Path) -> None:
    """不可解析的条目 ⇒ 无法判定是否比 T 晚 ⇒ 不删（裁决 5 的 fail-closed 形态）。"""
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_states_residue_clutter(T, "ifs")
    builder.write_output_residue_clutter()

    survivors = [
        builder.states_dir("ifs") / f"{T}.cfg.ic.tmp",
        builder.states_dir("ifs") / "nine.cfg.ic",
        builder.states_dir("ifs") / "9999123123.cfg.ic",
        builder.states_dir("ifs") / ".DS_Store",
        root / "output" / "stray",
        root / "output" / "stray" / "keep.txt",
        root / "output" / ".DS_Store",
    ]
    survivor_snapshots = {
        path: snapshot_tree(root)[str(path.relative_to(root))] for path in survivors
    }

    plan = _plan(builder, "ifs")
    assert plan is not None
    assert plan.state_files == (builder.state_path(T_PLUS_12, "ifs"),)
    residue.execute_residue_plan(plan)

    after = snapshot_tree(root)
    for path, entry in survivor_snapshots.items():
        assert after[str(path.relative_to(root))] == entry


# --- symlink 两侧策略 ---


def test_symlink_state_file_stops_the_source(tmp_path: pathlib.Path) -> None:
    """残留状态是 symlink -> `unlink_no_follow` 拒绝、报错指名该路径，链接与目标都还在。

    `states/<source>/<cycle>.cfg.ic` 只由发布器以「普通文件原子 rename」写入，该位置的
    symlink 不是崩溃残留而是异常，按 fail-closed 停该源（裁决 6）。
    """
    root = _yd_root(tmp_path)
    outside = tmp_path.resolve() / "outside"
    outside.mkdir()
    target = outside / "borrowed.cfg.ic"
    target.write_text("outside the yd root\n", encoding="utf-8")

    builder = YdRootBuilder(root=root)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")
    builder.write_output_dat(T, "ifs")
    link = builder.write_state_as_symlink_to(T_PLUS_12, "ifs", target)

    plan = _plan(builder, "ifs")
    assert plan is not None
    assert plan.state_files == (link,)

    with pytest.raises(SafeFilesystemError) as error:
        residue.execute_residue_plan(plan)

    assert str(link) in str(error.value)
    assert link.is_symlink()
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "outside the yd root\n"


def test_symlink_inside_half_product_tree_is_unlinked_not_followed(
    tmp_path: pathlib.Path,
) -> None:
    """半成品树里的越界 symlink -> 该树被删除，链接的**目标**未被删除（裁决 6）。"""
    root = _yd_root(tmp_path)
    outside = tmp_path.resolve() / "outside"
    outside.mkdir()
    target = outside / "raw-original.dat"
    target.write_text("NWM raw original\n", encoding="utf-8")

    builder = _crash_residue_tree(root)
    (builder.source_output_dir(T, "ifs") / "linked.dat").symlink_to(target)

    plan = _plan(builder, "ifs")
    assert plan is not None
    residue.execute_residue_plan(plan)

    assert not builder.source_output_dir(T, "ifs").exists()
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "NWM raw original\n"


# --- containment ---


def test_execute_refuses_targets_outside_the_containment_root(
    tmp_path: pathlib.Path,
) -> None:
    """`containment_root` 就是 `YD_ROOT`：以越界路径构造的执行被 `safe_fs` 拒绝。"""
    root = _yd_root(tmp_path)
    other_root = tmp_path.resolve() / "other"
    other_root.mkdir()
    builder = _crash_residue_tree(root)
    before = snapshot_tree(root)

    trespassing = residue.ResiduePlan(
        yd_root=other_root,
        source="ifs",
        retained_cycle=parse_cycle(T),
        state_files=(builder.state_path(T_PLUS_12, "ifs"),),
        half_product_dirs=(builder.source_output_dir(T, "ifs"),),
    )

    with pytest.raises(SafeFilesystemError):
        residue.execute_residue_plan(trespassing)

    assert snapshot_tree(root) == before


# --- 幂等 ---


def test_plan_and_execute_are_idempotent(tmp_path: pathlib.Path) -> None:
    """连跑两次判定+执行是 no-op；重复执行**同一份旧清单**同样零删除、零异常。"""
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)

    first = _plan(builder, "ifs")
    assert first is not None
    assert not first.empty
    residue.execute_residue_plan(first)
    cleaned = snapshot_tree(root)

    # 旧清单再执行一次（`missing_ok`）
    residue.execute_residue_plan(first)
    assert snapshot_tree(root) == cleaned

    second = _plan(builder, "ifs")
    assert second is not None
    assert second.empty
    residue.execute_residue_plan(second)
    assert snapshot_tree(root) == cleaned


# --- 不可跑源 ---


def test_non_runnable_source_is_never_cleaned(tmp_path: pathlib.Path) -> None:
    """`FrontierDecision` 带 `stop_reason` 时零删除：不知道 T 就无从定义「更晚」。"""
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, "ifs")
    builder.write_output_dat(D, "ifs")
    builder.write_state(T_PLUS_12, "ifs")
    builder.write_output_dat(T, "ifs")

    decision = _decide(builder, "ifs")
    assert decision.stop_reason is controller.StopReason.STATE_MISSING

    before = snapshot_tree(root)
    plan = residue.plan_residue(yd_root=root, source="ifs", decision=decision)

    assert plan is None
    assert snapshot_tree(root) == before


# --- 全新链 ---


def test_fresh_chain_retains_the_earliest_state(tmp_path: pathlib.Path) -> None:
    """无任何 `DONE`、states 有两份 -> T 取最早，更晚的那份是首轮中断的残留（裁决 3）。"""
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_state(FRESH, "ifs")
    builder.write_state(FRESH_NEXT, "ifs")
    builder.write_output_dat(FRESH, "ifs")

    plan = _plan(builder, "ifs")
    assert plan is not None
    assert plan.retained_cycle == parse_cycle(FRESH)
    assert plan.state_files == (builder.state_path(FRESH_NEXT, "ifs"),)
    residue.execute_residue_plan(plan)

    assert builder.state_path(FRESH, "ifs").is_file()
    assert not builder.state_path(FRESH_NEXT, "ifs").exists()
    assert not builder.source_output_dir(FRESH, "ifs").exists()


def test_plan_rejects_a_source_that_disagrees_with_the_decision(
    tmp_path: pathlib.Path,
) -> None:
    """清单必须逐源自洽：`source` 与 `decision.source` 分叉即拒（裁决 2）。"""
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    before = snapshot_tree(root)

    with pytest.raises(ValueError, match="逐源自洽"):
        residue.plan_residue(
            yd_root=root, source="gfs", decision=_decide(builder, "ifs")
        )

    assert snapshot_tree(root) == before
