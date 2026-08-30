"""`init` 阶段 B 收尾话术的第三路与「列出全部已落盘 source」（issue #21，round 5）。

与 `test_init_write_phase.py` 分文件只因行数闸门（单文件 1000 行即拒绝提交）；口径、
常量与合成树完全共用 `init_bootstrap_fixtures`，收尾话术常量在两侧各自逐字写死（从被测
模块 import 会让断言变成恒真式）。

本文件承担两条 round 4 的工作项：

- `[桶 C-3]` 的**父目录腿**（R4-A）：外来条目占住 `states/<source>` 这个**父目录分量**，
  `ensure_directory_no_follow` 抛 `NotADirectoryError`/`ELOOP`。既有的 `[桶 C-3]` 只覆盖
  终名腿（`O_EXCL` 撞 `EEXIST`）。
- `[桶 C-7]` 的**单元级**钉死（R4-E）：`detail` 的「列出**全部**已落盘 source」在端到端层
  结构性不可达（`rawscan.SOURCES` 是 2 元组且写入循环内三条失败腿均 `return`，故
  `len(written) ∈ {0, 1}`），只能直调纯函数 `_write_failed` 来行使。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from init_bootstrap_fixtures import (
    EPOCH_MINUTES_25_00Z,
    STATE_SUFFIX,
    WRITE_ORDER,
    Tree,
    all_files,
    expected_bytes,
    snapshot,
)

from yd_producer import init
from yd_producer.init import InitRefusal

#: 与 `test_init_write_phase.py` 同一份逐字常量（两侧各自写死，不互相 import）。
CLEANUP_CLAIM = "根已非全新，重跑 init 前需人工清理 `states/`"
FRESH_CLAIM = "零写入，根仍是全新根"
PARTIAL_CLAIM = "可能已被部分写入"
FOREIGN_ENTRY_CLAIM = "非本次写入产生"
REMOVE_DEMAND = "重跑 init 之前须人工确认该条目的来源并移除它"


def _plant_parent_blocker(tree: Tree, carrier: str) -> Path:
    """把 `states/ifs` 这个**父目录分量**预置成一个非目录的外来条目，返回该路径。

    三种载体都过得了阶段 A 的守卫：`_entry_kind` 对 symlink→目录 / FIFO / 悬垂 symlink
    都返回「既不是普通文件、也不是真实目录」，故它们既不算「已有状态」、也不被遍历进入。
    **普通文件不在这张表里**：它会被守卫认成已有状态而判 `STATES_NOT_EMPTY`，端到端到
    不了阶段 B。
    """
    blocker = tree.states / "ifs"
    if carrier == "symlink-to-dir":
        foreign_dir = tree.root / "foreign_dir"
        foreign_dir.mkdir()
        blocker.symlink_to(foreign_dir)
    elif carrier == "fifo":
        os.mkfifo(blocker)
    else:
        blocker.symlink_to(tree.root / "never-created")
    return blocker


@pytest.mark.parametrize("carrier", ["symlink-to-dir", "fifo", "dangling-symlink"])
def test_foreign_entry_at_the_parent_component_is_named_and_must_be_removed(
    tmp_path: Path, carrier: str
) -> None:
    """[桶 C-3] 父目录腿的第三路话术（round 4 R4-A CONFIRMED/P2）。

    构造：写入序**首位**（ifs）的**父目录分量** `states/ifs` 上预置一个外来条目，其余为
    合法全新根。`ensure_directory_no_follow` 的 `mkdir` 撞 `EEXIST` 后走 `_open_child_dir`
    （`O_DIRECTORY|O_NOFOLLOW`），symlink 得 `ELOOP`、FIFO 得 `ENOTDIR`，两者都被
    `safe_fs` 包成 `SafeFilesystemError`。

    盘上终态与终名腿**逐字节同构**：零普通文件残留、写入路径被一个不是本次写入产生的持久
    条目挡住、不移除它重跑必然以同样理由再次失败。故 MUST 走**同一路**（第三路），判据是
    「阻塞物是否为持久外来条目」而不是「哪条腿抛的异常」。

    话术点名的 MUST 是**被占住的那个路径本身**（`states/ifs`），不是终名
    `states/ifs/<T>.cfg.ic`——后者在这条腿上根本没被创建过。断言刻意带上「写入路径 … 上」
    这段前后文：裸的 `str(blocker) in detail` 在插值 `target` 的实现下**恒真**（终名路径
    以 `states/ifs/` 为前缀），钉不住本行要修的正是那处。

    判别变异体：(i) 把 ensure 腿的 `blocked_by_foreign_entry` 固定为 `False`（即恢复修复前
    的现状）-> 本行必红；(ii) 把第三路合并回第二路 -> 本行必红；(iii) 第三路改插值 `target`
    -> 本行必红。
    """
    tree = Tree(tmp_path)
    cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    for source in WRITE_ORDER:
        tree.write_cycle(source, cycle)
    blocker = _plant_parent_blocker(tree, carrier)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.WRITE_FAILED
    assert report.written == ()
    assert "（无）" in report.detail
    # 第三路：点名**被占住的那个路径本身**，并要求先确认来源、移除它。
    assert f"写入路径 {blocker} 上" in report.detail
    assert FOREIGN_ENTRY_CLAIM in report.detail
    assert REMOVE_DEMAND in report.detail
    # 三句不适用/被证伪的话术都 MUST NOT 出现。
    assert FRESH_CLAIM not in report.detail
    assert PARTIAL_CLAIM not in report.detail
    assert CLEANUP_CLAIM not in report.detail
    assert all_files(tree.states) == []
    assert snapshot(tree.output) == before_output

    # 首位失败即整体停手：第二个源的目录从未被创建。
    assert not (tree.states / "gfs").exists()

    # 不移除该条目就重跑：`detail` 逐字节复现——这正是「根仍是全新根」在本腿上为假的直接
    # 证据，也是本腿与终名腿同路的运维依据。
    repeat = tree.run()
    assert repeat.detail == report.detail

    # 补救（只移除该条目、不动 `states/` 其余部分）之后重跑 MUST 成功。
    blocker.unlink()

    again = tree.run()

    assert again.refusal is None
    assert again.written == tuple(tree.state_path(name, cycle) for name in WRITE_ORDER)
    for name in WRITE_ORDER:
        assert tree.state_path(name, cycle).read_bytes() == expected_bytes(
            tree.payloads[name], EPOCH_MINUTES_25_00Z
        )


def test_write_failed_lists_every_landed_source_not_just_the_first(
    tmp_path: Path,
) -> None:
    """[桶 C-7] 「列出**全部**已落盘 source」的单元级钉死（round 4 R4-E CONFIRMED/major）。

    `init.py` 的 `WRITE_FAILED` 契约写的是「`detail` 列出**全部**已落盘 source 的路径」，
    但端到端层结构性走不到 `len(written) >= 2`：`rawscan.SOURCES` 是 2 元组，写入循环内
    三条失败腿全部 `return`，故失败时 `written` 至多一个元素。实测把 join 换成
    `str(written[0])` 后全套 1235 全绿——端到端用例证不了这条契约，`SOURCES` 一旦扩到三源
    就会静默丢源。

    故本行直调纯函数 `_write_failed`（无 I/O），传入**伪造的两元 `written`**。这既不弱化
    契约为单数（那与 fixture 行 18 逐字冲突），也不 monkeypatch `SOURCES`（`rawscan` 属
    Must-preserve 面）。

    判别变异体：把 join 换成 `str(written[0]) if written else "（无）"` -> 本行必红。
    """
    states = tmp_path / "states"
    first = states / "ifs" / ("2026082500" + STATE_SUFFIX)
    second = states / "gfs" / ("2026082500" + STATE_SUFFIX)
    third = states / "era5" / ("2026082500" + STATE_SUFFIX)
    target = states / "nope" / ("2026082500" + STATE_SUFFIX)

    report = init._write_failed(
        "nope",
        target,
        [first, second, third],
        OSError(28, "No space left on device"),
        partial_note=None,
    )

    assert report.refusal is InitRefusal.WRITE_FAILED
    assert report.written == (first, second, third)
    landed = report.detail.split("已落盘的首态：")[1]
    # 三条路径**全部**出现，且出现在「已落盘的首态」那一段里。
    for path in (first, second, third):
        assert str(path) in landed
    # `written` 非空 -> 第一路，MUST NOT 退化成「根仍是全新根」。
    assert CLEANUP_CLAIM in report.detail
    assert FRESH_CLAIM not in report.detail


@pytest.mark.parametrize("carrier", ["symlink-to-dir", "fifo"])
def test_foreign_entry_higher_up_the_write_path_is_named_at_its_own_level(
    tmp_path: Path, carrier: str
) -> None:
    """[桶 C-13] 阻塞物在 `states/` **这一级**时同样走第三路，且点名的是这一级
    （round 5 R5-G CONFIRMED/P2）。

    构造：把 `states/` **自身**（而非 `states/ifs`）预置成 symlink→空目录 / FIFO，其余为
    合法全新根。阶段 A 放行——`_entry_names` 对 `FileNotFoundError`/`NotADirectoryError`
    一律返回空集，symlink 载体 `os.listdir` 跟随后得空集，FIFO 载体得 `ENOTDIR` 视作空集。

    本行钉死的是**探测面必须与失败面同宽**：`ensure_directory_no_follow` 逐 `part` 做
    `O_DIRECTORY|O_NOFOLLOW` 的 open，可在**任一**分量上失败；而只 `lstat` 末分量的探测
    比失败面窄一层，且 `os.lstat` 对中间分量是**跟随** symlink 的——对 `states/ifs` 的
    lstat 会穿过 `states` 拿到 `FileNotFoundError`（symlink 载体）或 `ENOTDIR`（FIFO 载
    体），两者都被判成「无外来条目」而落到第二路，而重跑逐字节复现同一失败。

    点名的 MUST 是 `states` 这一级：`states/ifs` 在这两个载体上盘上根本不存在，点名它会让
    「点名被占住的那个路径本身」在下一层再次为假。

    判别变异体：把逐级走查退回只 `lstat(target_dir)` 的单分量探测 -> 本行必红。
    """
    tree = Tree(tmp_path)
    cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    for source in WRITE_ORDER:
        tree.write_cycle(source, cycle)
    tree.states.rmdir()
    if carrier == "symlink-to-dir":
        foreign_dir = tree.root / "foreign_states"
        foreign_dir.mkdir()
        tree.states.symlink_to(foreign_dir)
    else:
        os.mkfifo(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.WRITE_FAILED
    assert report.written == ()
    # 第三路，且点名的是**被占住的那一级**（`states`），不是它下面那个并不存在的分量。
    assert f"写入路径 {tree.states} 上" in report.detail
    assert f"写入路径 {tree.states / 'ifs'} 上" not in report.detail
    assert FOREIGN_ENTRY_CLAIM in report.detail
    assert REMOVE_DEMAND in report.detail
    assert FRESH_CLAIM not in report.detail
    assert PARTIAL_CLAIM not in report.detail
    assert CLEANUP_CLAIM not in report.detail
    assert snapshot(tree.output) == before_output
