"""`init.bootstrap` 阶段 A 的拒绝守卫（tasks.md 任务 11.1、issue #21）。

覆盖「已有状态 / 已有 DONE」「率定末态定位」与「枚举失败 ≠ 不存在」三组拒绝；合成树、
锚点常量与期望值口径见 `init_bootstrap_fixtures`。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from init_bootstrap_fixtures import (
    EPOCH_MINUTES_25_00Z,
    STATE_SUFFIX,
    WRITE_ORDER,
    Tree,
    all_files,
    assert_zero_write,
    expected_bytes,
    skip_if_root,
    snapshot,
    stat_hostile,
    two_token_payload,
    unreadable,
)

from yd_producer import controller
from yd_producer.init import InitRefusal
from yd_producer.state import MAX_STATE_IC_BYTES

# --- 阶段 A 拒绝守卫 ---------------------------------------------------------


def test_existing_state_file_refuses_without_touching_anything(
    tmp_path: Path,
) -> None:
    """回归行 2 / spec「已有状态即拒绝」：含 mtime 不变的可断言证据。"""
    tree = Tree(tmp_path)
    residual = tree.states / "gfs" / ("2026082700" + STATE_SUFFIX)
    residual.parent.mkdir(parents=True)
    residual.write_bytes(b"residual\n")
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.STATES_NOT_EMPTY
    assert report.written == ()
    assert str(residual) in report.detail
    assert snapshot(tree.states) == before_states
    assert snapshot(tree.output) == before_output


def test_residual_file_with_unparsable_name_still_refuses(tmp_path: Path) -> None:
    """裁决 8 的「宽」：不合命名规则的残留同样算「已有状态」（与 #22 的可见集刻意不同）。

    `controller.visible_state_cycles` 对该条目判**不可见**；本模块必须判拒绝，否则一个
    带残留的根会被重新建链。
    """
    tree = Tree(tmp_path)
    residual = tree.states / "ifs" / "partial.tmp"
    residual.parent.mkdir(parents=True)
    residual.write_bytes(b"x")
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))

    assert controller.visible_state_cycles(tree.states / "ifs") == set()

    report = tree.run()

    assert report.refusal is InitRefusal.STATES_NOT_EMPTY
    assert all_files(tree.states) == [residual]


def test_existing_done_refuses_with_zero_writes(tmp_path: Path) -> None:
    """回归行 3 / spec「已有 DONE 即拒绝」。"""
    tree = Tree(tmp_path)
    done = tree.output / "2026082400" / "gfs" / "DONE"
    done.parent.mkdir(parents=True)
    done.write_bytes(b"")
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.DONE_PRESENT
    assert str(done) in report.detail
    assert_zero_write(tree, before_states, before_output)


# --- 率定末态定位（裁决 2 补齐的 seam）--------------------------------------


def test_missing_variant_directory_refuses(tmp_path: Path) -> None:
    """回归行 5a / spec「率定末态定位」：目录不存在 -> `VARIANT_MISSING`。"""
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    for path in sorted(tree.variant_dir("gfs").iterdir()):
        path.unlink()
    tree.variant_dir("gfs").rmdir()
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.VARIANT_MISSING
    assert str(tree.variant_dir("gfs")) in report.detail
    assert_zero_write(tree, before_states, before_output)


def test_variant_path_that_is_a_regular_file_refuses_as_variant_missing(
    tmp_path: Path,
) -> None:
    """「不是目录」与「不存在」同归 `VARIANT_MISSING`（裁决 2 逐字）。"""
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    variant = tree.variant_dir("ifs")
    for path in sorted(variant.iterdir()):
        path.unlink()
    variant.rmdir()
    variant.write_bytes(b"not a directory\n")

    report = tree.run()

    assert report.refusal is InitRefusal.VARIANT_MISSING


@pytest.mark.parametrize("hits", [0, 2])
def test_variant_without_exactly_one_calibration_state_refuses(
    tmp_path: Path, hits: int
) -> None:
    """回归行 5b：顶层 `.cfg.ic` 命中数 0 / 2 -> `CALIBRATION_STATE_AMBIGUOUS`，可区分。"""
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    variant = tree.variant_dir("ifs")
    if hits == 0:
        tree.calibration["ifs"].unlink()
    else:
        (variant / ("second" + STATE_SUFFIX)).write_bytes(tree.payloads["ifs"])
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.CALIBRATION_STATE_AMBIGUOUS
    assert f"命中 {hits} 个" in report.detail
    assert str(variant) in report.detail
    assert_zero_write(tree, before_states, before_output)


def test_calibration_state_lookup_is_top_level_only(tmp_path: Path) -> None:
    """裁决 2：不递归——运行期衍生物（子目录里的 `cfg.ic`）MUST NOT 进候选集。"""
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    nested = tree.variant_dir("gfs") / "output" / "run-1"
    nested.mkdir(parents=True)
    (nested / ("derived" + STATE_SUFFIX)).write_bytes(tree.payloads["gfs"])
    (tree.variant_dir("gfs") / "yd_gfs.cfg.ic.update").write_bytes(b"derived\n")

    report = tree.run()

    assert report.refusal is None
    assert report.written[1].read_bytes() == expected_bytes(
        tree.payloads["gfs"], EPOCH_MINUTES_25_00Z
    )


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("truncated", b"3 6 27000000.000000\n"),
        ("not-utf8", b"3 6 27000000.000000\n\xff\xfe\n"),
    ],
)
def test_unparsable_calibration_state_refuses_distinctly(
    tmp_path: Path, label: str, payload: bytes
) -> None:
    """回归行 10：`state.parse` 抛 `ValueError` -> `CALIBRATION_STATE_UNREADABLE`。"""
    tree = Tree(tmp_path, payloads={"ifs": payload})
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.CALIBRATION_STATE_UNREADABLE
    assert str(tree.calibration["ifs"]) in report.detail
    assert_zero_write(tree, before_states, before_output)


def test_oversized_calibration_state_refuses_as_unreadable(tmp_path: Path) -> None:
    """回归行 10 的超界分支：`state.parse` 的 `MAX_STATE_IC_BYTES` 有界读收敛为 `ValueError`。

    文件以 `truncate` 造成稀疏（不真写 64 MiB 到盘），但 `parse` 仍会有界读到上界 +1 字节
    并拒绝——这正是「资源上界由 `state.parse` 承担」这条风险包的可断言证据。
    """
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    with open(tree.calibration["ifs"], "wb") as handle:
        handle.truncate(MAX_STATE_IC_BYTES + 1)
    before_states = snapshot(tree.states)

    report = tree.run()

    assert report.refusal is InitRefusal.CALIBRATION_STATE_UNREADABLE
    assert snapshot(tree.states) == before_states
    assert all_files(tree.states) == []


def test_mode_000_calibration_state_is_not_a_discovery_failure(
    tmp_path: Path,
) -> None:
    """裁决 7 的分层切分：率定末态**定位成功**后的读失败归 `CALIBRATION_STATE_UNREADABLE`。

    `DISCOVERY_UNREADABLE` 专指「集合无法枚举 / 条目无法判定」；这里 `stat` 完全可行，
    失败发生在 `state.parse` 的读取上，故 MUST NOT 落到 discovery 那一类。
    """
    skip_if_root()
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))

    with unreadable(tree.calibration["gfs"]):
        report = tree.run()

    assert report.refusal is InitRefusal.CALIBRATION_STATE_UNREADABLE
    assert report.refusal is not InitRefusal.DISCOVERY_UNREADABLE
    assert all_files(tree.states) == []


def test_two_token_header_refuses_at_the_restamp_shape_gate(tmp_path: Path) -> None:
    """回归行 6：header 只有 2 个数值 token -> `HEADER_SHAPE_INVALID`，零写入。"""
    tree = Tree(tmp_path, payloads={"gfs": two_token_payload()})
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.HEADER_SHAPE_INVALID
    assert "STATE_SAVE_CHECKPOINT_IC_HEADER_SHAPE_INVALID" in report.detail
    assert_zero_write(tree, before_states, before_output)


# --- 枚举失败：不存在 ≠ 不可确定（裁决 7）-----------------------------------


def test_unlistable_states_dir_refuses_instead_of_looking_empty(
    tmp_path: Path,
) -> None:
    """回归行 7：`chmod 0o000 states/` -> `DISCOVERY_UNREADABLE`，MUST NOT 判空后放行。"""
    skip_if_root()
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    before_output = snapshot(tree.output)

    with unreadable(tree.states):
        report = tree.run()

    assert report.refusal is InitRefusal.DISCOVERY_UNREADABLE
    assert str(tree.states) in report.detail
    assert report.written == ()
    assert all_files(tree.states) == []
    assert snapshot(tree.output) == before_output


def test_unlistable_variant_dir_is_not_confused_with_missing_or_ambiguous(
    tmp_path: Path,
) -> None:
    """回归行 8：变体目录不可枚举 -> `DISCOVERY_UNREADABLE`（不是另外两种），零写入。"""
    skip_if_root()
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))

    with unreadable(tree.variant_dir("gfs")):
        report = tree.run()

    assert report.refusal is InitRefusal.DISCOVERY_UNREADABLE
    assert report.refusal is not InitRefusal.VARIANT_MISSING
    assert report.refusal is not InitRefusal.CALIBRATION_STATE_AMBIGUOUS
    assert all_files(tree.states) == []


def test_unlistable_output_cycle_dir_refuses(tmp_path: Path) -> None:
    """回归行 9：`chmod 0o000 output/<cycle>` -> `DISCOVERY_UNREADABLE`，零写入。"""
    skip_if_root()
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    cycle_dir = tree.output / "2026082400"
    (cycle_dir / "gfs").mkdir(parents=True)

    with unreadable(cycle_dir):
        report = tree.run()

    assert report.refusal is InitRefusal.DISCOVERY_UNREADABLE
    assert str(cycle_dir) in report.detail
    assert all_files(tree.states) == []


# --- 探测失败的 stat 层（round 1 cand-06）------------------------------------
#
# 上面三条 `chmod 0o000` 都让**目录本身**不可列，只行使 `_entry_names`。目录置 `0o444`
# 时 `listdir` 仍成功、但子项的 `lstat`/`stat` 抛 `EACCES`（darwin/Linux 上「有 r 无 x」
# 即此语义），这才行使 `_entry_kind` / `_is_directory` 这一层——而它正是「判空即放行写入」
# 这条防线的最后一层。


def test_unstatable_state_entry_refuses_instead_of_looking_absent(
    tmp_path: Path,
) -> None:
    """`states/gfs/` 置 `0o444` 且内含残留文件 -> `DISCOVERY_UNREADABLE`，**不放行写入**。

    `_entry_kind` 吞掉 `OSError` 的实现会把残留判成「既不是文件也不是目录」，于是守卫放行、
    init 往一个**已有状态**的根上写首态——断链的直接入口。
    """
    skip_if_root()
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    residual_dir = tree.states / "gfs"
    residual_dir.mkdir(parents=True)
    (residual_dir / ("2026082700" + STATE_SUFFIX)).write_bytes(b"residual\n")

    with stat_hostile(residual_dir):
        report = tree.run()

    assert report.refusal is InitRefusal.DISCOVERY_UNREADABLE
    assert report.written == ()
    assert str(residual_dir) in report.detail


def test_unstatable_variant_entry_is_not_read_as_zero_hits(tmp_path: Path) -> None:
    """变体目录置 `0o444`（内含率定末态）-> `DISCOVERY_UNREADABLE`，**不是** ambiguous。

    `_entry_kind` 吞掉 `OSError` 的实现会把唯一的 `.cfg.ic` 判成「不是普通文件」、命中数
    退化为 0，于是报 `CALIBRATION_STATE_AMBIGUOUS`——一条把权限故障说成配置问题的错误
    诊断。名字仍列得出来（`listdir` 成功），故本行行使的确实是 stat 层。
    """
    skip_if_root()
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    variant = tree.variant_dir("ifs")

    with stat_hostile(variant):
        report = tree.run()

    assert report.refusal is InitRefusal.DISCOVERY_UNREADABLE
    assert report.refusal is not InitRefusal.CALIBRATION_STATE_AMBIGUOUS
    assert all_files(tree.states) == []


def test_unstatable_variant_directory_itself_is_not_read_as_missing(
    tmp_path: Path,
) -> None:
    """变体目录的**父目录**置 `0o444` -> `_is_directory` 无法判定 -> `DISCOVERY_UNREADABLE`。

    与上一条分工：那条打的是 `_entry_kind`（子项判定），这条打的是 `_is_directory`（变体
    目录**自身**的判定）。`_is_directory` 吞掉 `OSError` 的实现会返回 `False` 并报
    `VARIANT_MISSING`，把一个权限故障说成「prepare 没跑」。
    """
    skip_if_root()
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    models_dir = tree.variant_dir("ifs").parent

    with stat_hostile(models_dir):
        report = tree.run()

    assert report.refusal is InitRefusal.DISCOVERY_UNREADABLE
    assert report.refusal is not InitRefusal.VARIANT_MISSING
    assert str(tree.variant_dir("ifs")) in report.detail
    assert all_files(tree.states) == []


# --- `_entry_kind` 的 `os.stat` FOLLOW 臂（round 2 cand-R2-05）----------------
#
# 上面三条 `0o444` 只行使 `_entry_kind` 的 **`os.lstat`** 臂与 `_is_directory`。条目本身是
# **symlink** 时 `lstat` 成功（它不是目录，也不需要解析目标），随后的 `os.stat` 才去跟随
# 链接、才可能拿到 `EACCES`——那条 FOLLOW 臂此前没有任何用例。把它的两条 except 收成
# `except OSError: return (False, False)` 的实现在下面两行必红：实测该变异下守卫会放行，
# init 往一个**已持有可达前态**的根上写两份首态，正是裁决 7 禁止的断链。
# symlink 只是本地的差分手段；NFS 发布根上 `ESTALE`/`EIO` 无需任何 symlink 即可到达同一臂。


def test_state_symlink_into_an_unreadable_vault_refuses(tmp_path: Path) -> None:
    """`states/ifs/<T>.cfg.ic` 是指向 `0o000` 目录内真实前态的 symlink -> `DISCOVERY_UNREADABLE`。"""
    skip_if_root()
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    vault = tree.root / "vault"  # 刻意放在 yd_root **之外**：它自身不是守卫的输入
    vault.mkdir()
    prior = vault / ("2026082400" + STATE_SUFFIX)
    prior.write_bytes(b"prior state\n")
    residual = tree.states / "ifs" / ("2026082400" + STATE_SUFFIX)
    residual.parent.mkdir(parents=True)
    residual.symlink_to(prior)
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    with unreadable(vault):
        report = tree.run()

    assert report.refusal is InitRefusal.DISCOVERY_UNREADABLE
    assert report.written == ()
    assert str(residual) in report.detail
    # `states/` 逐字节不变。此处不能用 `assert_zero_write`：它断言 `states/` 下零普通
    # 文件，而本构造刻意在那里放了一条**指向**普通文件的 symlink。
    assert snapshot(tree.states) == before_states
    assert snapshot(tree.output) == before_output
    assert list(tree.states.rglob("*")) == [residual.parent, residual]


def test_calibration_symlink_into_an_unreadable_vault_refuses(tmp_path: Path) -> None:
    """兄弟面：`_locate_calibration_state` 同样经 `_entry_kind`，同样必须 fail closed。

    变体顶层唯一的 `.cfg.ic` 换成指向 `0o000` 目录内真实文件的 symlink -> 判
    `DISCOVERY_UNREADABLE`，**不是** `CALIBRATION_STATE_AMBIGUOUS`（吞掉 `OSError` 的实现
    会把命中数读成 0，把权限故障说成「prepare 提交形态不对」）。
    """
    skip_if_root()
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    vault = tree.root / "vault"
    vault.mkdir()
    prior = vault / ("baseline" + STATE_SUFFIX)
    prior.write_bytes(tree.payloads["ifs"])
    calibration = tree.calibration["ifs"]
    calibration.unlink()
    calibration.symlink_to(prior)
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    with unreadable(vault):
        report = tree.run()

    assert report.refusal is InitRefusal.DISCOVERY_UNREADABLE
    assert report.refusal is not InitRefusal.CALIBRATION_STATE_AMBIGUOUS
    assert report.written == ()
    assert str(calibration) in report.detail
    assert_zero_write(tree, before_states, before_output)
