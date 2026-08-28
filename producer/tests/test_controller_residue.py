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

import contextlib
import os
import pathlib
import stat
from collections.abc import Iterator

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


def _skip_if_root() -> None:
    if os.geteuid() == 0:
        pytest.skip("root 无视 mode 位，`chmod 0o000` 仍可枚举，本用例无判别力")


@contextlib.contextmanager
def _unreadable(path: pathlib.Path) -> Iterator[None]:
    """把 `path` 临时置为 `chmod 0o000`，退出时**一定**恢复。

    不恢复的话，`snapshot_tree` 的 `rglob` 下降与 pytest 的 tmp 清理都会踩到不可读目录。
    """
    original = stat.S_IMODE(path.stat().st_mode)
    path.chmod(0o000)
    try:
        yield
    finally:
        path.chmod(original)


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
    """端到端形态：`DONE(T)` 已在 -> 前沿推进到 T+12 -> 该树上没有任何残留、零删除。

    这条**不是** `DONE` 前置的判别器（把守卫整条删掉它照样绿：`decision.cycle` 是 T+12，
    而 `output/<T+12>/` 根本不存在，清单无论如何都是空的）。它钉的是发现层与清理层串起来
    之后的整体形状：一棵刚发布完的干净树上，清理是 no-op。`DONE` 前置本身由
    `test_done_is_rechecked_for_the_retained_cycle` 与
    `test_handed_cycle_with_done_empties_the_whole_plan` 承担。
    """
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


def test_handed_cycle_with_done_empties_the_whole_plan(tmp_path: pathlib.Path) -> None:
    """交来的 T 已有 `DONE` -> **两类**清单都为空（裁决 4 增补，round 1 A1）。

    上一条同姿态用例只放了半成品而没放 `states/<T+12>`，恰好绕开了这条：`DONE` 闸只挡
    半成品那一半时，`states/<T+12>.cfg.ic`——publish 刚提交的**下一环**——会被判为残留
    删掉，此后 `decide_frontier` 在 T+12 上永久 `STATE_MISSING`，违反 Must-preserve
    「清理前后 T 不变」。故断言分两层：清单两臂皆空 + 执行后前沿仍能推进到 T+12 且可跑。
    """
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(T, "ifs")
    builder.write_output_dat(T, "ifs")
    builder.write_state(T, "ifs")
    builder.write_state(T_PLUS_12, "ifs")
    before = snapshot_tree(root)

    handed = controller.FrontierDecision(
        source="ifs",
        cycle=parse_cycle(T),
        stop_reason=None,
        detail="13.2 复用姿态：DONE(T) 已写、states/<T+12> 已提交，其后某步失败",
    )
    plan = residue.plan_residue(yd_root=root, source="ifs", decision=handed)

    assert plan is not None
    assert plan.state_files == ()
    assert plan.half_product_dirs == ()
    assert plan.empty
    residue.execute_residue_plan(plan)

    assert snapshot_tree(root) == before
    after = _decide(builder, "ifs")
    assert after.cycle == parse_cycle(T_PLUS_12)
    assert after.stop_reason is None


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


def test_plan_with_only_a_half_product_is_not_empty(tmp_path: pathlib.Path) -> None:
    """半成品**独臂**的清单 `empty is False`（round 2：`empty` 的半成品臂此前无判别器）。

    `ResiduePlan.empty` 是公开 API，且任务 13.2 只消费清单、不执行——把它退化成
    `not self.state_files` 时全套仍绿（既有断言用的树两臂要么同空、要么同非空），而按
    `empty` 分支的调用方会把一个真实半成品静默跳过，残留留在 NFS 上被下一轮当成正常
    产物。故这条用例的承重断言是「`state_files` 为空**而** `empty is False`」。
    """
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")
    builder.source_output_dir(T, "ifs").mkdir(parents=True)

    plan = _plan(builder, "ifs")

    assert plan is not None
    assert plan.state_files == ()
    assert plan.half_product_dirs == (builder.source_output_dir(T, "ifs"),)
    assert plan.empty is False


@pytest.mark.parametrize("shape", ["symlink", "regular_file", "fifo"])
def test_non_directory_half_product_location_is_never_planned(
    tmp_path: pathlib.Path, shape: str
) -> None:
    """`output/<T>/<source>` 是 symlink / 普通文件 / FIFO -> 都不入清单，执行后仍在。

    半成品的类型判据（`os.lstat` + `S_ISDIR`）是裁决 5/6 的 fail-closed 方向：只删能被
    **正面**识别为残留目录的路径。去掉这道门的话，`remove_tree_allow_symlinks` 会把这三
    种形态一律 unlink 掉——symlink 那一支尤其危险，它删的是一条**指向别处**的链接，而那
    绝不是「被杀死的发布尝试留下的树」。
    树里同时放了 `states/<T+12>`：它必须照常被删，否则本条会退化成「清单本来就是空的」
    这种空转断言。
    """
    root = _yd_root(tmp_path)
    outside = tmp_path.resolve() / "outside"
    (outside / "keep").mkdir(parents=True)
    (outside / "keep" / "payload.txt").write_text("not residue\n", encoding="utf-8")

    builder = YdRootBuilder(root=root)
    builder.write_done(D, "ifs")
    builder.write_output_dat(D, "ifs")
    builder.write_state(T, "ifs")
    builder.write_state(T_PLUS_12, "ifs")
    entry = builder.source_output_dir(T, "ifs")
    entry.parent.mkdir(parents=True)
    if shape == "symlink":
        entry.symlink_to(outside / "keep", target_is_directory=True)
    elif shape == "regular_file":
        entry.write_text("half-written\n", encoding="utf-8")
    else:
        os.mkfifo(entry)

    plan = _plan(builder, "ifs")
    assert plan is not None
    assert plan.half_product_dirs == ()
    assert plan.state_files == (builder.state_path(T_PLUS_12, "ifs"),)
    residue.execute_residue_plan(plan)

    # 条目本身还在（`lexists`：symlink 那一支不能靠 `exists()` 判）
    assert os.path.lexists(entry)
    assert (outside / "keep" / "payload.txt").is_file()
    # 执行确实跑过：更晚状态被删、T 自己的状态保留
    assert not builder.state_path(T_PLUS_12, "ifs").exists()
    assert builder.state_path(T, "ifs").is_file()


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


def test_plan_carries_the_yd_root_as_containment_root(tmp_path: pathlib.Path) -> None:
    """判别器之一：`plan.yd_root` **就是**传入的 `YD_ROOT`，不是它的祖先。

    上一条只证明「越界的 `containment_root` 会被拒」，不证明生产路径上填进去的是哪一个
    根。把 `plan.yd_root` 悄悄放宽成 `root.parent`（容纳域随即覆盖 `outside/`、兄弟项目、
    NWM raw 根）在其余全部用例下恒绿，因为那些用例的删除目标本来就在更宽的域里。
    """
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)

    plan = _plan(builder, "ifs")

    assert plan is not None
    assert plan.yd_root == root
    assert plan.yd_root != root.parent


def test_execute_refuses_a_state_file_outside_the_containment_root(
    tmp_path: pathlib.Path,
) -> None:
    """判别器之二：清单**只带状态文件**时越界仍被拒（状态那条臂也传 `containment_root`）。

    上面的越界用例的清单两臂俱全，而执行顺序是「先半成品树、后状态文件」——它在第一臂
    就抛了，状态那条臂的 `containment_root` 单独掉了也走不到、也不会红。故这一条把半成品
    臂清空，逼执行走到 `unlink_no_follow`。
    """
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
        half_product_dirs=(),
    )

    with pytest.raises(SafeFilesystemError):
        residue.execute_residue_plan(trespassing)

    assert builder.state_path(T_PLUS_12, "ifs").is_file()
    assert snapshot_tree(root) == before


def test_symlinked_yd_root_is_resolved_before_use(tmp_path: pathlib.Path) -> None:
    """`YD_ROOT` 经 symlink 到达时判定与执行都成功（裁决 6 增补，round 1 B1）。

    `safe_fs._open_directory_no_follow` 把 `containment_root` **自身**的每个分量从 `/`
    重新过一遍 `O_NOFOLLOW`，而判定侧（`os.stat` / `iterdir`）跟随 symlink。不 `resolve()`
    的话，根上任一 symlink 分量（NFS 挂载点、macOS 的 `/var`）会让每个 tick 都「判定出
    非空清单、执行必抛」——带误导消息的永久停源。删除结果 MUST 与直接用实路径一致。
    """
    real = tmp_path.resolve() / "real"
    real.mkdir()
    link = tmp_path.resolve() / "link"
    link.symlink_to(real, target_is_directory=True)
    root = real / "yd"
    root.mkdir()
    builder = _crash_residue_tree(root)

    unresolved = link / "yd"
    plan = residue.plan_residue(
        yd_root=unresolved,
        source="ifs",
        decision=controller.decide_frontier(
            yd_root=unresolved,
            source="ifs",
            raw_complete=RecordingRawComplete(set(_ALL_CYCLES)),
        ),
    )

    assert plan is not None
    assert plan.yd_root == root
    assert plan.state_files == (builder.state_path(T_PLUS_12, "ifs"),)
    assert plan.half_product_dirs == (builder.source_output_dir(T, "ifs"),)
    residue.execute_residue_plan(plan)

    assert not builder.state_path(T_PLUS_12, "ifs").exists()
    assert not builder.source_output_dir(T, "ifs").exists()
    assert builder.state_path(T, "ifs").is_file()
    assert builder.source_output_dir(D, "ifs").joinpath("DONE").is_file()


def test_execute_refuses_a_dotdot_entry_name(tmp_path: pathlib.Path) -> None:
    """清单里带 `..` 的半成品条目 -> `SafeFilesystemError(kind="unsafe")`，树逐字不变。

    这是**消费者侧**的钉子：`safe_fs.remove_tree_allow_symlinks` 第一行的
    `_reject_unsafe_entry_name` 是「`output/<T>/..` 不会真被删」这条保证的唯一承载物，
    而仓内没有任何用例钉住它（round 2 实测：把该行删掉，全套仍绿，随后 `..` 清单会真的
    删掉另一源已提交的 `DONE` 产物）。`store/safe_fs.py` 在本 issue 零改动，故义务落在
    这里：本模块交出去的清单形态一旦退化，拒绝行为 MUST 仍然可观测。
    """
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    builder.write_done(T, "gfs")
    builder.write_output_dat(T, "gfs")
    before = snapshot_tree(root)

    degenerate = residue.ResiduePlan(
        yd_root=root,
        source="ifs",
        retained_cycle=parse_cycle(T),
        state_files=(),
        half_product_dirs=(root / "output" / T / "..",),
    )

    with pytest.raises(SafeFilesystemError) as error:
        residue.execute_residue_plan(degenerate)

    assert error.value.kind == "unsafe"
    assert snapshot_tree(root) == before
    assert builder.source_output_dir(T, "gfs").joinpath("DONE").is_file()


def test_half_products_are_removed_before_state_files(tmp_path: pathlib.Path) -> None:
    """执行序钉死「先半成品树、后更晚状态」（模块头逐字写着这条）。

    判别器：`states/<source>/<T+12>.cfg.ic` 是 symlink（`unlink_no_follow` 必抛）且同时
    存在半成品树。两种顺序都抛 `SafeFilesystemError`，但只有钉死的顺序会在抛之前把半成品
    删掉；顺序对调则半成品每 tick 都原地不动。round 2 实测：**在本用例存在之前**，对调
    两个循环后全套仍绿——这正是变异 (af) 被登记、本用例被补写的理由。round 3 复现 (af)
    时，它失败在下面断言块的**第一条**（`assert not builder.source_output_dir(T, "ifs").exists()`）
    并就地中止；另两条若被执行同样会过——`unlink_no_follow` 的拒绝既不动链接也不动目标。
    本用例即是它的判别器。
    """
    root = _yd_root(tmp_path)
    outside = tmp_path.resolve() / "outside"
    outside.mkdir()
    target = outside / "state-target.cfg.ic"
    target.write_text("outside the yd root\n", encoding="utf-8")

    builder = YdRootBuilder(root=root)
    builder.write_done(D, "ifs")
    builder.write_output_dat(D, "ifs")
    builder.write_state(T, "ifs")
    builder.write_output_dat(T, "ifs")
    link = builder.write_state_as_symlink_to(T_PLUS_12, "ifs", target)

    plan = _plan(builder, "ifs")
    assert plan is not None
    assert plan.state_files == (link,)
    assert plan.half_product_dirs == (builder.source_output_dir(T, "ifs"),)

    with pytest.raises(SafeFilesystemError):
        residue.execute_residue_plan(plan)

    # 半成品先删：抛出发生在**状态那条臂**，故半成品已不在。
    assert not builder.source_output_dir(T, "ifs").exists()
    assert link.is_symlink()
    assert target.is_file()


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


@pytest.mark.parametrize("bad_source", ["", ".", "..", "a/b", "ifs/"])
def test_empty_source_fails_closed(tmp_path: pathlib.Path, bad_source: str) -> None:
    """越界的 `source` -> 报错；`output/<T>/` 与另一源的 `DONE` 产物零改动（裁决 2 增补）。

    空串不是「没有源」而是一次**粒度塌陷**：`Path("/a/b") / ""` 就是 `/a/b`，于是
    `output/<T>/<source>/` 塌回 `output/<T>/`，删除的是整个 cycle 目录，连同 gfs 已经
    带 `DONE` 的正式产物。`safe_fs` 在这条路上帮不上忙——它看到的条目名是一个合法的
    10 位 cycle id，完全在容纳域之内。故闸必须在判定入口。

    五个形态各是闸里一个合取项的**唯一**判别器（round 2：整道闸退化成 `if not source:`
    时全套仍绿，说明单分量那一项此前无判别器）：
    - `""` 只被 `not source` 挡（`Path("").name` 是空串，单分量判据放行它）；
    - `"."` 只被显式点名集挡（`Path(".").name` 是空串，看似被单分量判据挡住，但那是
      pathlib 的顺带效果；放行后 `output/<T>/.` 被 pathlib 归一成 `output/<T>`，删的是
      整个 cycle 目录）；
    - `".."` 只被显式点名集挡（`Path("..").name` 就是 `".."`，单分量判据放行它），放行后
      清单是 `output/<T>/..`——整棵 `output/`；
    - `"a/b"` 与 `"ifs/"` 只被单分量判据挡（`Path("ifs/").name` 是 `"ifs"`）。
    """
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    builder.write_done(T, "gfs")
    builder.write_output_dat(T, "gfs")
    builder.write_output_dat(T, "ifs")  # 无 DONE 的本源半成品
    builder.write_state(T, "ifs")
    before = snapshot_tree(root)

    handed = controller.FrontierDecision(
        source=bad_source,
        cycle=parse_cycle(T),
        stop_reason=None,
        detail="配置里 source 写坏",
    )

    with pytest.raises(ValueError, match="单个非空路径分量"):
        residue.plan_residue(yd_root=root, source=bad_source, decision=handed)

    assert snapshot_tree(root) == before
    assert builder.source_output_dir(T, "gfs").joinpath("DONE").is_file()
    assert (root / "output" / T).is_dir()


# --- 判定侧的 fail-closed 收敛 ---


def test_unreadable_states_dir_raises_residue_error(tmp_path: pathlib.Path) -> None:
    """`chmod 0o000` 掉 `states/<source>/` -> `ResidueError`，MUST NOT 返回空清单。

    空清单会让残留留在树上被下一轮当成正常产物；`DiscoveryUnreadableError` 在判定层被
    吞掉是本模块最危险的 fail-open 形态（裁决 7 同向）。`decision` 刻意在 `chmod` **之前**
    算好：否则 `decide_frontier` 自己就会收敛成 `DISCOVERY_UNREADABLE`，`plan_residue`
    直接返回 `None`，用例根本走不到不可读目录，变成空转。
    """
    _skip_if_root()
    root = _yd_root(tmp_path)
    builder = _crash_residue_tree(root)
    decision = _decide(builder, "ifs")
    assert decision.cycle == parse_cycle(T)
    before = snapshot_tree(root)

    with (
        _unreadable(builder.states_dir("ifs")),
        pytest.raises(residue.ResidueError, match="残留判定无法完成"),
    ):
        residue.plan_residue(yd_root=root, source="ifs", decision=decision)

    assert snapshot_tree(root) == before
