"""`init.bootstrap` 阶段 A 的拒绝守卫（tasks.md 任务 11.1、issue #21）。

覆盖「已有状态 / 已有 DONE」「率定末态定位」与「枚举失败 ≠ 不存在」三组拒绝；合成树、
锚点常量与期望值口径见 `init_bootstrap_fixtures`。
"""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from init_bootstrap_fixtures import (
    DEFAULT_VARIANTS,
    EPOCH_MINUTES_25_00Z,
    STATE_SUFFIX,
    WRITE_ORDER,
    Tree,
    all_files,
    assert_zero_write,
    default_payload,
    expected_bytes,
    make_config,
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


# --- 变体路径的相对性闸门（[桶 C-5]，裁决 2 的 round 3 补正）----------------
#
# 闸门跑在 `yd_root / getattr(config.variants, source)` 这个 join **之前**：绝对路径与含
# `..` 的取值一旦被拼接后读取，整条状态链的**起点**就取自 `YD_ROOT` 之外。判据与
# `prepare._resolve_variant_relative` 共用同一份实现（`config.variant_relative_violation`）。


@pytest.mark.parametrize("shape", ["absolute", "pardir"])
@pytest.mark.parametrize("bad_source", WRITE_ORDER)
def test_variant_path_outside_yd_root_refuses_with_its_own_reason(
    tmp_path: Path, bad_source: str, shape: str
) -> None:
    """[桶 C-5]：绝对路径 / 含 `..` 的变体取值 -> `VARIANT_PATH_INVALID`，零写入。

    两种越界形态 × 两个 source **各自单独**越界（另一源保持合法默认值），故任何只守住
    一个 source 或只守住一种形态的实现都会在某条参数上必红。

    构造刻意让越界目录**真的存在且持恰一份率定末态**（`Tree` 对绝对值/含 `..` 的值做同一
    个 join，于是它就落在 `YD_ROOT` 之外）：删掉闸门恢复裸 join 时，实测得 `refusal=None`
    且两份首态照写、链起点解析到 `YD_ROOT` 之外——本行因此在拒绝枚举项与「零写入」两侧
    同时必红，而不是只在错误码上必红。

    拒绝理由与「变体缺失」「率定末态不唯一」逐项可区分：这里断言的是**新增的第十项**
    枚举值，且 `detail` 带 source 与**原始取值**（运维要能一眼看出是哪条配置项写错）。
    """
    outside = tmp_path.resolve() / "outside-yd-root" / bad_source
    value = str(outside) if shape == "absolute" else f"../outside-yd-root/{bad_source}"
    variants = dict(DEFAULT_VARIANTS)
    variants[bad_source] = value
    tree = Tree(tmp_path, config=make_config(variants=variants))
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    # 越界目录确实存在且持恰一份率定末态：拒绝的原因只能是闸门，不是「找不到」。
    assert tree.calibration[bad_source].is_file()
    assert not tree.calibration[bad_source].resolve().is_relative_to(tree.yd_root)
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.VARIANT_PATH_INVALID
    assert report.refusal is not InitRefusal.VARIANT_MISSING
    assert report.refusal is not InitRefusal.CALIBRATION_STATE_AMBIGUOUS
    assert f"variants.{bad_source}" in report.detail
    assert value in report.detail
    assert_zero_write(tree, before_states, before_output)


def test_shared_variant_dir_with_two_calibration_states_is_ambiguous(
    tmp_path: Path,
) -> None:
    """[桶 C-5] 的对照行（上半）：`variants.gfs == variants.ifs` 指向同一**合法相对**目录。

    共享目录本身不是越界——闸门只管相对性。逐源命名的两份 `.cfg.ic` 落进同一个顶层，命中
    数为 2，故判 `CALIBRATION_STATE_AMBIGUOUS` 而**不是** `VARIANT_PATH_INVALID`：这条对照
    钉死两条判据不得互相顶替。
    """
    shared = "input/models/shared"
    tree = Tree(tmp_path, config=make_config(variants={"gfs": shared, "ifs": shared}))
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.CALIBRATION_STATE_AMBIGUOUS
    assert "命中 2 个" in report.detail
    assert_zero_write(tree, before_states, before_output)


def test_shared_variant_dir_with_one_calibration_state_is_accepted(
    tmp_path: Path,
) -> None:
    """[桶 C-5] 的对照行（下半）：同一合法相对目录内**恰一份** `.cfg.ic` -> 被接受。

    两个源因此共用同一个链起点文件。**MUST NOT** 把这条写成「共享变体目录被接受」——被接受
    的是「顶层恰一份率定末态」这条判据的结果，共享目录只是它的一个实例（上半那条同样是共享
    目录，却被拒）。
    """
    shared = "input/models/shared"
    payload = default_payload()
    tree = Tree(
        tmp_path,
        config=make_config(variants={"gfs": shared, "ifs": shared}),
        payloads={source: payload for source in WRITE_ORDER},
        calibration_names={source: "yd" + STATE_SUFFIX for source in WRITE_ORDER},
    )
    cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    for source in WRITE_ORDER:
        tree.write_cycle(source, cycle)
    # 两条链的起点是**同一个**文件。
    assert tree.calibration["ifs"] == tree.calibration["gfs"]

    report = tree.run()

    assert report.refusal is None
    assert report.written == tuple(tree.state_path(name, cycle) for name in WRITE_ORDER)
    for path in report.written:
        assert path.read_bytes() == expected_bytes(payload, EPOCH_MINUTES_25_00Z)


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


# --- #96：state symlink 在 FOLLOW stat 之前 fail closed ---------------------------
#
# `states/<source>` 自身或其树内任一 symlink 都是已有状态条目：阶段 A 以
# `STATES_NOT_EMPTY` 拒绝、点名该链、两源零写入，且 MUST NOT FOLLOW `stat` 目标。
# 普通（非 symlink）空目录仍放行。`output/` DONE 与率定末态的 FOLLOW 语义不在本策略内。
# 旧 round 2 cand-R2-05 把 state 不可读目标链判成 `DISCOVERY_UNREADABLE`；#96 撤销该
# oracle。率定末态侧的 FOLLOW 失败仍归 `DISCOVERY_UNREADABLE`。


_STATE_LINK_KINDS = ("file", "dir", "dangling", "fifo", "unreadable", "loop")
_STATE_LINK_PLACEMENTS = ("source-root", "nested")
_FRONTIER = datetime(2026, 8, 25, 0, tzinfo=UTC)


def _lex_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    """No-follow 树快照：只 `lstat`/`readlink`/`listdir`，不解析 symlink 目标。"""
    result: dict[str, tuple[object, ...]] = {}
    if not os.path.lexists(root):
        return result
    pending = [root]
    while pending:
        directory = pending.pop(0)
        try:
            names = sorted(os.listdir(directory))
        except (FileNotFoundError, NotADirectoryError):
            continue
        for name in names:
            path = directory / name
            key = str(path.relative_to(root))
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                result[key] = ("symlink", os.readlink(path))
            elif stat.S_ISDIR(mode):
                result[key] = ("dir",)
                pending.append(path)
            elif stat.S_ISFIFO(mode):
                result[key] = ("fifo",)
            elif stat.S_ISREG(mode):
                result[key] = ("file", path.read_bytes())
            else:
                result[key] = ("other", stat.S_IFMT(mode))
    return result


def _forbid_follow_stat(
    monkeypatch: pytest.MonkeyPatch, link: Path, target: Path | None
) -> list[bool]:
    """OS 边界哨兵：对 state 链门面的 FOLLOW `stat`、以及对目标的任何 `stat` 都失败。

    返回 `armed` 开关：`run()` 返回后必须关掉，避免事后 `read_bytes` 误伤。
    """
    real_stat = os.stat
    link_abs = os.path.abspath(str(link))
    target_abs = os.path.abspath(str(target)) if target is not None else None
    armed = [True]

    def wrapped(*args, **kwargs):  # type: ignore[no-untyped-def]
        if not armed[0]:
            return real_stat(*args, **kwargs)
        follow = kwargs.get("follow_symlinks", True)
        path = args[0] if args else kwargs.get("path")
        abs_path = os.path.abspath(os.fspath(path))
        if abs_path == link_abs:
            if follow:
                raise AssertionError(f"FOLLOW stat of state-lane symlink {path}")
            return real_stat(*args, **kwargs)
        if target_abs is not None and (
            abs_path == target_abs or abs_path.startswith(target_abs + os.sep)
        ):
            raise AssertionError(f"stat of state-link target {path}")
        return real_stat(*args, **kwargs)

    monkeypatch.setattr(os, "stat", wrapped)
    return armed


def _plant_state_symlink(
    tree: Tree, source: str, placement: str, kind: str
) -> tuple[Path, Path | None, Path | None]:
    """在 `states/<source>` 或其后裔上种一条 symlink；目标放在 yd_root 之外。"""
    if placement == "source-root":
        link = tree.states / source
    else:
        link = tree.states / source / "old"
        link.parent.mkdir(parents=True)
    outside = tree.root / f"outside-{source}-{placement}-{kind}"
    outside.mkdir()
    vault: Path | None = None
    if kind == "file":
        target: Path | None = outside / "prior.cfg.ic"
        target.write_bytes(b"prior-state")
        link.symlink_to(target)
    elif kind == "dir":
        target = outside / "prior-dir"
        target.mkdir()
        (target / "prior.cfg.ic").write_bytes(b"prior-state")
        link.symlink_to(target)
    elif kind == "dangling":
        target = outside / "never-created"
        link.symlink_to(target)
    elif kind == "fifo":
        target = outside / "prior.fifo"
        os.mkfifo(target)
        link.symlink_to(target)
    elif kind == "unreadable":
        vault = outside / "vault"
        vault.mkdir()
        target = vault / "prior.cfg.ic"
        target.write_bytes(b"prior-state")
        link.symlink_to(target)
    elif kind == "loop":
        target = link
        os.symlink(link.name, link)
    else:
        raise AssertionError(kind)
    return link, target, vault


def _assert_target_untouched(kind: str, link: Path, target: Path | None) -> None:
    assert link.is_symlink()
    if kind == "loop":
        assert os.readlink(link) == link.name
        return
    assert target is not None
    assert os.readlink(link) == str(target)
    if kind == "file":
        assert target.read_bytes() == b"prior-state"
    elif kind == "dir":
        assert (target / "prior.cfg.ic").read_bytes() == b"prior-state"
    elif kind == "dangling":
        assert not os.path.lexists(target)
    elif kind == "fifo":
        assert stat.S_ISFIFO(os.lstat(target).st_mode)
    elif kind == "unreadable":
        assert target.read_bytes() == b"prior-state"


def test_state_symlink_into_an_unreadable_vault_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """migrate：`states/ifs/<T>.cfg.ic` 指向 `0o000` 目录内真实前态 -> `STATES_NOT_EMPTY`。

    旧 oracle 是 FOLLOW 失败后的 `DISCOVERY_UNREADABLE`。#96 在 `lstat` 身份上拒绝，
    MUST NOT 先 `stat` 目标，也不得因目标不可访问而改判探测失败。
    """
    skip_if_root()
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, _FRONTIER)
    vault = tree.root / "vault"  # 刻意放在 yd_root **之外**：它自身不是守卫的输入
    vault.mkdir()
    prior = vault / ("2026082400" + STATE_SUFFIX)
    prior.write_bytes(b"prior state\n")
    residual = tree.states / "ifs" / ("2026082400" + STATE_SUFFIX)
    residual.parent.mkdir(parents=True)
    residual.symlink_to(prior)
    before_states = _lex_snapshot(tree.states)
    before_output = snapshot(tree.output)
    armed = _forbid_follow_stat(monkeypatch, residual, prior)

    with unreadable(vault):
        report = tree.run()
    armed[0] = False

    assert report.refusal is InitRefusal.STATES_NOT_EMPTY
    assert report.refusal is not InitRefusal.DISCOVERY_UNREADABLE
    assert report.written == ()
    assert str(residual) in report.detail
    assert _lex_snapshot(tree.states) == before_states
    assert snapshot(tree.output) == before_output
    assert os.readlink(residual) == str(prior)
    assert prior.read_bytes() == b"prior state\n"
    assert not (tree.states / "gfs").exists()


@pytest.mark.parametrize("source", WRITE_ORDER)
@pytest.mark.parametrize("placement", _STATE_LINK_PLACEMENTS)
@pytest.mark.parametrize("kind", _STATE_LINK_KINDS)
def test_state_lane_symlink_refuses_before_follow_stat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    placement: str,
    kind: str,
) -> None:
    """#96 矩阵：两源 × 源根/后裔 × 目标类型 -> 阶段 A `STATES_NOT_EMPTY`，零写入、不跟随。"""
    if kind == "unreadable":
        skip_if_root()
    tree = Tree(tmp_path)
    for name in WRITE_ORDER:
        tree.write_cycle(name, _FRONTIER)
    link, target, vault = _plant_state_symlink(tree, source, placement, kind)
    before_states = _lex_snapshot(tree.states)
    before_output = snapshot(tree.output)
    armed = _forbid_follow_stat(monkeypatch, link, target)

    if vault is None:
        report = tree.run()
    else:
        with unreadable(vault):
            report = tree.run()
    armed[0] = False

    assert report.refusal is InitRefusal.STATES_NOT_EMPTY
    assert report.written == ()
    assert str(link) in report.detail
    assert _lex_snapshot(tree.states) == before_states
    assert snapshot(tree.output) == before_output
    _assert_target_untouched(kind, link, target)
    other = "gfs" if source == "ifs" else "ifs"
    assert not (tree.states / other).exists()


@pytest.mark.parametrize("source", WRITE_ORDER)
@pytest.mark.parametrize("placement", _STATE_LINK_PLACEMENTS)
def test_real_empty_directory_at_state_lane_still_bootstraps(
    tmp_path: Path, source: str, placement: str
) -> None:
    """反向钉死：同一位置换成普通空目录 -> 两源成功建链，重戳字节与写入序不变。"""
    tree = Tree(tmp_path)
    for name in WRITE_ORDER:
        tree.write_cycle(name, _FRONTIER)
    if placement == "source-root":
        (tree.states / source).mkdir()
    else:
        (tree.states / source / "old").mkdir(parents=True)

    report = tree.run()

    assert report.refusal is None
    expected = tuple(tree.state_path(name, _FRONTIER) for name in WRITE_ORDER)
    assert report.written == expected
    for name in WRITE_ORDER:
        assert tree.state_path(name, _FRONTIER).read_bytes() == expected_bytes(
            tree.payloads[name], EPOCH_MINUTES_25_00Z
        )


def test_states_root_symlink_containing_prior_state_still_refuses(
    tmp_path: Path,
) -> None:
    """`states/` 自身是指向含既有状态文件的目录的 symlink -> 仍 `STATES_NOT_EMPTY`。"""
    tree = Tree(tmp_path)
    for name in WRITE_ORDER:
        tree.write_cycle(name, _FRONTIER)
    foreign = tree.root / "foreign_states"
    foreign.mkdir()
    leftover = foreign / ("2026082400" + STATE_SUFFIX)
    leftover.write_bytes(b"prior-root-state\n")
    tree.states.rmdir()
    tree.states.symlink_to(foreign)
    before_output = snapshot(tree.output)

    report = tree.run()

    named = tree.states / leftover.name
    assert report.refusal is InitRefusal.STATES_NOT_EMPTY
    assert report.written == ()
    assert str(named) in report.detail
    assert os.readlink(tree.states) == str(foreign)
    assert leftover.read_bytes() == b"prior-root-state\n"
    assert snapshot(tree.output) == before_output


def test_output_done_symlink_to_regular_file_is_done_present(tmp_path: Path) -> None:
    """兄弟面：名为 `DONE` 的 symlink→普通文件仍走默认 FOLLOW，判 `DONE_PRESENT`。"""
    tree = Tree(tmp_path)
    for name in WRITE_ORDER:
        tree.write_cycle(name, _FRONTIER)
    target = tree.root / "done-bytes"
    target.write_bytes(b"")
    done = tree.output / "2026082400" / "gfs" / "DONE"
    done.parent.mkdir(parents=True)
    done.symlink_to(target)
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.DONE_PRESENT
    assert report.written == ()
    assert str(done) in report.detail
    assert_zero_write(tree, before_states, before_output)
    assert os.readlink(done) == str(target)


@pytest.mark.parametrize("shape", ["dangling-done", "hidden-dir", "non-done-link"])
def test_output_symlink_shapes_that_are_not_done_do_not_block(
    tmp_path: Path, shape: str
) -> None:
    """兄弟面：悬垂 DONE、藏 DONE 的目录链、非 DONE 链都不扩大为拒绝。"""
    tree = Tree(tmp_path)
    for name in WRITE_ORDER:
        tree.write_cycle(name, _FRONTIER)
    if shape == "dangling-done":
        done = tree.output / "2026082400" / "gfs" / "DONE"
        done.parent.mkdir(parents=True)
        done.symlink_to(tree.root / "missing-DONE")
    elif shape == "hidden-dir":
        hidden = tree.root / "hidden-output"
        hidden.mkdir()
        (hidden / "DONE").write_bytes(b"")
        cycle_dir = tree.output / "2026082400"
        cycle_dir.symlink_to(hidden)
    else:
        residue = tree.output / "2026082400" / "gfs" / "yd.rivqdown.dat"
        residue.parent.mkdir(parents=True)
        target = tree.root / "stale-product"
        target.write_bytes(b"stale product\n")
        residue.symlink_to(target)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is None
    expected = tuple(tree.state_path(name, _FRONTIER) for name in WRITE_ORDER)
    assert report.written == expected
    assert snapshot(tree.output) == before_output


def test_readable_calibration_symlink_to_regular_file_parses_and_restamps(
    tmp_path: Path,
) -> None:
    """兄弟面：可读的率定末态普通文件链仍定位、解析并重戳成功。"""
    tree = Tree(tmp_path)
    for name in WRITE_ORDER:
        tree.write_cycle(name, _FRONTIER)
    prior = tree.root / ("baseline" + STATE_SUFFIX)
    prior.write_bytes(tree.payloads["ifs"])
    calibration = tree.calibration["ifs"]
    calibration.unlink()
    calibration.symlink_to(prior)

    report = tree.run()

    assert report.refusal is None
    expected = tuple(tree.state_path(name, _FRONTIER) for name in WRITE_ORDER)
    assert report.written == expected
    assert tree.state_path("ifs", _FRONTIER).read_bytes() == expected_bytes(
        tree.payloads["ifs"], EPOCH_MINUTES_25_00Z
    )
    assert tree.state_path("gfs", _FRONTIER).read_bytes() == expected_bytes(
        tree.payloads["gfs"], EPOCH_MINUTES_25_00Z
    )
    assert os.readlink(calibration) == str(prior)
    assert prior.read_bytes() == tree.payloads["ifs"]


def test_calibration_symlink_into_an_unreadable_vault_refuses(tmp_path: Path) -> None:
    """兄弟面：`_locate_calibration_state` 同样经 `_entry_kind`，同样必须 fail closed。

    变体顶层唯一的 `.cfg.ic` 换成指向 `0o000` 目录内真实文件的 symlink -> 判
    `DISCOVERY_UNREADABLE`，**不是** `CALIBRATION_STATE_AMBIGUOUS`（吞掉 `OSError` 的实现
    会把命中数读成 0，把权限故障说成「prepare 提交形态不对」）。#96 不改变这一侧。
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
