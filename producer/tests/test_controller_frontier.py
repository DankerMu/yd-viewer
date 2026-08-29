r"""`yd_producer.controller.decide_frontier` 的行为测试（严格前沿，任务 12.1）。

oracle 纪律：待跑 cycle 的期望值是**手算**的（`DONE` 的最大 cycle + 12h，或全新链的最早
状态文件名），header 分钟时标的期望值由 `frontier_fixtures` 在**写入时**记录，两者都不由
被测函数回读。手算交叉校验：1970-01-01 到 2026-08-26 共 20691 天 →
20691*1440 = 29795040 分钟（2026-08-26T00Z），+720 → 29795760（12Z）。

承重条不是「干净树上算出 T」——那对「取最晚状态文件名」的错误实现同样恒绿。真正判别的是：
崩溃残留（更晚状态）用例钉死「前沿只由 DONE 推进」；相对分钟 `0.000000` / `720.000000`
用例钉死「只接受绝对分钟」；不互借另一源用例钉死状态路径不回退到兄弟目录；缺轮阻塞用例
钉死不跳轮；判定顺序用例用记录型 fake 钉死状态判据先于 raw。

每次调用都跑一次**递归树快照**前后比对（相对路径 + 条目类型 + `st_mode` + size +
内容摘要），把「本模块零写入」变成可断言的负面证据。
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import pathlib
import stat
import tracemalloc
from collections.abc import Iterator

import pytest
from frontier_fixtures import (
    RecordingRawComplete,
    YdRootBuilder,
    absolute_minute,
    absolute_minute_text,
    parse_cycle,
    shift,
    snapshot_tree,
    state_payload,
)

from yd_producer import controller
from yd_producer.state.cfg_ic import MAX_STATE_IC_BYTES

#: 全套用例的锚点 cycle（手算：2026-08-26 是 epoch 后第 20691 天）。
D = "2026082600"
T = "2026082612"
T_PLUS_12 = "2026082700"
T_PLUS_24 = "2026082712"
FRESH = "2026082000"
FRESH_NEXT = "2026082012"

ALL_SOURCES = ("ifs", "gfs")


def _decide(
    builder: YdRootBuilder,
    source: str,
    raw: RecordingRawComplete,
    *,
    check_writes: bool = True,
) -> controller.FrontierDecision:
    """调用被测函数，并顺带证明它没有动过树上任何一个字节。"""
    before = snapshot_tree(builder.root) if check_writes else None
    decision = controller.decide_frontier(
        yd_root=builder.root, source=source, raw_complete=raw
    )
    if before is not None:
        assert snapshot_tree(builder.root) == before
    return decision


def _all_complete() -> RecordingRawComplete:
    return RecordingRawComplete(
        {D, T, T_PLUS_12, T_PLUS_24, FRESH, FRESH_NEXT, "2026082512", "2026082712"}
    )


def _assert_runnable(decision: controller.FrontierDecision, cycle_text: str) -> None:
    assert decision.stop_reason is None
    assert decision.cycle == parse_cycle(cycle_text)
    assert decision.runnable is True


def _assert_stopped(
    decision: controller.FrontierDecision, reason: controller.StopReason
) -> None:
    assert decision.stop_reason is reason
    assert decision.cycle is None
    assert decision.runnable is False
    assert decision.detail


# --- 手算 oracle 的自校验 ---


def test_absolute_minute_matches_hand_computed_epoch_arithmetic() -> None:
    """20691 天 * 1440 分钟/天 = 29795040（2026-08-26T00Z）；+720 = 29795760（12Z）。"""
    assert absolute_minute(parse_cycle(D)) == 20691 * 1440 == 29795040
    assert absolute_minute(parse_cycle(T)) == 29795760


# --- 全新链 ---


def test_fresh_chain_takes_the_earliest_state_file_name(tmp_path: pathlib.Path) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_state(FRESH, "ifs")
    raw = _all_complete()
    _assert_runnable(_decide(builder, "ifs", raw), FRESH)


def test_fresh_chain_with_several_states_still_takes_the_earliest(
    tmp_path: pathlib.Path,
) -> None:
    builder = YdRootBuilder(tmp_path)
    # 逆序写入：结论 MUST 取最早的 cycle，不是最后写入的那份
    builder.write_state(FRESH_NEXT, "ifs")
    builder.write_state(FRESH, "ifs")
    _assert_runnable(_decide(builder, "ifs", _all_complete()), FRESH)


@pytest.mark.parametrize("layout", ["empty_dir", "missing_dir", "illegal_names_only"])
def test_no_done_and_no_legal_state_stops_with_no_initial_state(
    tmp_path: pathlib.Path, layout: str
) -> None:
    builder = YdRootBuilder(tmp_path)
    if layout == "empty_dir":
        builder.states_dir("ifs").mkdir(parents=True)
    elif layout == "illegal_names_only":
        builder.write_states_clutter("ifs")
    raw = _all_complete()
    decision = _decide(builder, "ifs", raw)
    _assert_stopped(decision, controller.StopReason.NO_INITIAL_STATE)
    assert raw.asked == []


# --- 前沿由 DONE 推进 ---


def test_frontier_advances_twelve_hours_past_the_latest_done(
    tmp_path: pathlib.Path,
) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state(D, "ifs")
    builder.write_state(T, "ifs")
    _assert_runnable(_decide(builder, "ifs", _all_complete()), T)


def test_latest_done_is_the_largest_cycle_not_the_newest_mtime(
    tmp_path: pathlib.Path,
) -> None:
    """mtime 逆序写入：D MUST 是最大 cycle `2026082700`，于是 T=`2026082712`。"""
    builder = YdRootBuilder(tmp_path)
    for index, cycle_text in enumerate((T_PLUS_12, D, "2026082512")):
        done = builder.write_done(cycle_text, "gfs")
        # 越晚的 cycle mtime 越旧
        os.utime(done, (1_600_000_000 + index * 3600,) * 2)
    builder.write_state(T_PLUS_24, "gfs")
    _assert_runnable(_decide(builder, "gfs", _all_complete()), T_PLUS_24)


def test_done_sets_are_per_source(tmp_path: pathlib.Path) -> None:
    """`output/<cycle>/gfs/DONE` MUST NOT 为 ifs 计数（同一棵树上交叉断言）。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "gfs")
    builder.write_state(T, "gfs")
    builder.write_state(FRESH, "ifs")
    raw = _all_complete()
    _assert_runnable(_decide(builder, "gfs", raw), T)
    # ifs 无自己的 DONE → 走全新链，取最早状态名，而不是 gfs 的 D+12h
    _assert_runnable(_decide(builder, "ifs", raw), FRESH)


def test_later_crash_residue_state_does_not_advance_the_frontier(
    tmp_path: pathlib.Path,
) -> None:
    """崩溃残留（更晚状态 + 无 DONE 的半成品 output 目录）MUST NOT 改变待跑 T。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")
    builder.write_state(T_PLUS_12, "ifs")  # 上次发布中断留下的更晚状态
    builder.write_output_dat(T, "ifs")  # 只有 DAT、无 DONE 的半成品目录
    decision = _decide(builder, "ifs", _all_complete())
    _assert_runnable(decision, T)
    # 本函数不删除任何路径（零写入已由 `_decide` 的快照比对断言）
    assert builder.state_path(T_PLUS_12, "ifs").exists()
    assert (builder.source_output_dir(T, "ifs") / "yd.rivqdown.dat").exists()


# --- 状态缺失 / 不互借另一源 ---


def test_missing_state_stops_the_source_and_never_borrows_the_sibling(
    tmp_path: pathlib.Path,
) -> None:
    builder = YdRootBuilder(tmp_path)
    for source in ALL_SOURCES:
        builder.write_done(D, source)
    builder.write_state(D, "ifs")  # ifs 只有更旧的状态，T 的缺失
    builder.write_state(T, "gfs")  # gfs 的同名状态存在且 header 正确
    raw = _all_complete()

    ifs_decision = _decide(builder, "ifs", raw)
    _assert_stopped(ifs_decision, controller.StopReason.STATE_MISSING)
    # detail MUST 指向 ifs 自己的状态路径，不是 gfs 的同名状态
    normalised = ifs_decision.detail.replace(os.sep, "/")
    assert "states/ifs" in normalised
    assert "states/gfs" not in normalised
    # 同一棵树上 gfs 得到正常结论
    _assert_runnable(_decide(builder, "gfs", raw), T)


# --- header 时间：只接受绝对分钟 ---


@pytest.mark.parametrize("layout", ["native", "compat"])
def test_absolute_header_minute_passes_for_both_layouts(
    tmp_path: pathlib.Path, layout: str
) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs", layout=layout)
    _assert_runnable(_decide(builder, "ifs", _all_complete()), T)


@pytest.mark.parametrize(
    ("case", "minute_text"),
    [
        # 拿 T-12h 的旧状态改名冒充
        ("stale_absolute", absolute_minute_text(parse_cycle(D))),
        # 相对分钟：移植了 pin 的 `_valid_time_from_header_minute` 就会在这两条上放行
        ("relative_zero", "0.000000"),
        ("relative_720", "720.000000"),
        # 非有限值：`_as_float` 接受它们，`round()` 会抛 —— MUST NOT 外泄
        ("nan", "nan"),
        ("inf", "inf"),
        ("negative_inf", "-inf"),
    ],
)
def test_header_minute_that_is_not_absolute_t_stops_the_source(
    tmp_path: pathlib.Path, case: str, minute_text: str
) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs", minute_text=minute_text)
    decision = _decide(builder, "ifs", _all_complete())
    _assert_stopped(decision, controller.StopReason.HEADER_TIME_MISMATCH)


@pytest.mark.parametrize(
    ("case", "payload"),
    [
        # pin issue #1197 的 `23106\t6` 形态：2 个数值 token
        ("two_numeric_tokens", b"23106\t6\n1\t0.1\n"),
        # 5 个数值 token：未知布局 fail-closed，MUST NOT 退化为「取最后一个数值 token」
        (
            "five_numeric_tokens",
            f"23106\t413\t1\t2\t{absolute_minute(parse_cycle(T))}.000000\n".encode(),
        ),
        ("non_numeric_header", b"Index\tCanopy\tSnow\n1\t0.1\t0.2\n"),
        ("empty_file", b""),
        ("blank_lines_only", b"\n\n   \n"),
    ],
)
def test_header_with_invalid_shape_stops_the_source(
    tmp_path: pathlib.Path, case: str, payload: bytes
) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state_bytes(T, "ifs", payload)
    decision = _decide(builder, "ifs", _all_complete())
    _assert_stopped(decision, controller.StopReason.HEADER_TIME_MISMATCH)


def test_two_token_header_is_refused_even_though_its_last_token_matches_t(
    tmp_path: pathlib.Path,
) -> None:
    """判别条：`23106\t<T 的绝对分钟>` 的末位 token 正确，仍必须因形状非法而停。

    没有这一条，shape 门被拆掉后 2-token 形态仍会因「6 != T 的分钟数」偶然变红，
    看起来把守住了，实际上守的是别的东西。
    """
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    minute = absolute_minute_text(parse_cycle(T))
    builder.write_state_bytes(T, "ifs", f"23106\t{minute}\n1\t0.1\n".encode())
    decision = _decide(builder, "ifs", _all_complete())
    _assert_stopped(decision, controller.StopReason.HEADER_TIME_MISMATCH)
    assert "2 numeric token(s)" in decision.detail


def test_five_token_header_is_refused_even_though_its_last_token_matches_t(
    tmp_path: pathlib.Path,
) -> None:
    """判别条：末位 token 就是正确的绝对分钟，仍必须因形状非法而停。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    minute = absolute_minute_text(parse_cycle(T))
    builder.write_state_bytes(
        T, "ifs", f"23106\t413\t1\t2\t{minute}\n1\t0.1\n".encode()
    )
    decision = _decide(builder, "ifs", _all_complete())
    _assert_stopped(decision, controller.StopReason.HEADER_TIME_MISMATCH)
    assert "5 numeric token(s)" in decision.detail


# --- 可读性分类 ---


def test_state_symlink_to_a_valid_state_is_followed_and_passes(
    tmp_path: pathlib.Path,
) -> None:
    """裁决 4：可读性判定 MUST 跟随 symlink（macOS `/tmp` 本身就是 symlink）。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    target = tmp_path / "elsewhere.cfg.ic"
    target.write_bytes(state_payload(absolute_minute_text(parse_cycle(T))))
    builder.write_state_as_symlink_to(T, "ifs", target)
    _assert_runnable(_decide(builder, "ifs", _all_complete()), T)


@pytest.mark.parametrize(
    "kind", ["directory", "fifo", "dangling_symlink", "invalid_utf8"]
)
def test_unreadable_state_is_classified_not_raised(
    tmp_path: pathlib.Path, kind: str
) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    if kind == "directory":
        builder.write_state_as_directory(T, "ifs")
    elif kind == "fifo":
        builder.write_state_as_fifo(T, "ifs")
    elif kind == "dangling_symlink":
        builder.write_state_as_dangling_symlink(T, "ifs")
    else:
        builder.write_state_invalid_utf8(T, "ifs")
    decision = _decide(builder, "ifs", _all_complete())
    _assert_stopped(decision, controller.StopReason.STATE_UNREADABLE)


def test_permission_denied_state_is_classified_not_raised(
    tmp_path: pathlib.Path,
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root 无视 mode 位，`chmod 0o000` 仍可读，本用例无判别力")
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state_unreadable(T, "ifs")
    decision = _decide(builder, "ifs", _all_complete())
    _assert_stopped(decision, controller.StopReason.STATE_UNREADABLE)


def test_oversized_state_is_refused_without_unbounded_read(
    tmp_path: pathlib.Path,
) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    path = builder.write_state_oversized(T, "ifs", limit_bytes=MAX_STATE_IC_BYTES)
    assert path.stat().st_size > MAX_STATE_IC_BYTES
    # 超大稀疏文件上跳过快照比对：摘要要读满 64 MiB，与本条的判别力无关
    decision = _decide(builder, "ifs", _all_complete(), check_writes=False)
    _assert_stopped(decision, controller.StopReason.STATE_UNREADABLE)


def test_state_exactly_at_the_byte_limit_is_still_read(tmp_path: pathlib.Path) -> None:
    """恰好上界这一侧的分类不变（与「上界 +1」成对，钉死超界判据用的是 `st_size`）。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    path = builder.write_state_at_size(T, "ifs", size_bytes=MAX_STATE_IC_BYTES)
    assert path.stat().st_size == MAX_STATE_IC_BYTES
    # 稀疏文件上跳过快照比对：摘要要读满 64 MiB，与本条的判别力无关
    _assert_runnable(_decide(builder, "ifs", _all_complete(), check_writes=False), T)


def test_first_line_read_stays_bounded_on_a_large_valid_state(
    tmp_path: pathlib.Path,
) -> None:
    """裁决 4 增补：16 MiB 合法状态的 traced peak 与文件大小同量级，不得是其数倍。

    判别构造是「纯换行在前、header 行在后」：`read(MAX+1)` + `decode()` + `splitlines()`
    的旧实现在这条上峰值会放大到文件大小的十倍量级（round 1 实测 16 MiB → 168 MiB）。
    这里取一个**保守**上界（峰值 < 文件大小），分块实现的实测峰值只有百 KiB 量级。
    """
    builder = YdRootBuilder(tmp_path)
    blank_bytes = 16 * 1024 * 1024
    path = builder.write_state_trailing_header(T, "ifs", blank_bytes=blank_bytes)
    size = path.stat().st_size
    assert size > blank_bytes

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        line = controller._read_header_line(path, size=size)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert isinstance(line, str)
    assert line.split()[-1] == absolute_minute_text(parse_cycle(T))
    assert peak < size, (peak, size)


@pytest.mark.parametrize("payload", ["printable", "nul"])
def test_newline_free_giant_first_line_is_refused_within_bounded_memory(
    tmp_path: pathlib.Path, payload: str
) -> None:
    """裁决 4 二次增补：整篇无 `\\n` 的 64 MiB 状态 -> `STATE_UNREADABLE`，峰值有界。

    两种载荷各一条：可打印字节，以及**全 NUL**（NUL 是合法 UTF-8 且不是 `str.strip()`
    的空白，所以整个文件构成一条巨大的非空 header 行——round 2 验证闸门 cand-12 实测
    端到端 traced peak 576 MiB）。字节预算只约束「读了多少」，判别力全在候选行上界：
    去掉 `MAX_HEADER_LINE_BYTES` 截断后，这条会同时在**结论**（变成
    `HEADER_TIME_MISMATCH`）和**峰值**两处变红。
    """
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    path = builder.write_state_newline_free(
        T, "ifs", size_bytes=MAX_STATE_IC_BYTES, payload=payload
    )
    size = path.stat().st_size
    assert size == MAX_STATE_IC_BYTES  # 恰好在字节上界内：超界闸不承担本条的判别力
    raw = _all_complete()

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        line = controller._read_header_line(path, size=size)
        _, read_peak = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        # 64 MiB 文件上跳过快照比对：摘要要读满整份文件，与本条的判别力无关
        decision = _decide(builder, "ifs", raw, check_writes=False)
        _, end_to_end_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert line is controller.StopReason.STATE_UNREADABLE
    _assert_stopped(decision, controller.StopReason.STATE_UNREADABLE)
    assert raw.asked == []
    # 读取本身与上界同量级（实测 197 KiB ≈ 3x cap）；端到端另留一档余量
    assert read_peak < 8 * controller.MAX_HEADER_LINE_BYTES, (read_peak, size)
    assert end_to_end_peak < 32 * controller.MAX_HEADER_LINE_BYTES, (
        end_to_end_peak,
        size,
    )
    assert end_to_end_peak < size // 32, (end_to_end_peak, size)


@pytest.mark.parametrize("over_cap", [False, True], ids=["at_cap", "cap_plus_one"])
def test_header_line_length_boundary_is_exactly_the_cap(
    tmp_path: pathlib.Path, over_cap: bool
) -> None:
    """候选 header 行**恰好** `MAX_HEADER_LINE_BYTES` 仍可读，`cap + 1` 判
    `STATE_UNREADABLE`。

    fixture 的措辞是「累计**超过** `MAX_HEADER_LINE_BYTES`」，判别力全在这一对上：只有
    ~30 字节与 64 MiB 两档行长的话，`newline > cap` 改成 `>=` 恒绿。行尾用空格垫到目标
    长度（`split()` 会丢掉尾随空格），所以接受的那一侧是真的解析出了 header 并走到
    runnable。`_READ_CHUNK_BYTES` 与上界同为 64 KiB，恰好上界这一档必然跨 chunk 边界，
    连带把 chunk 末尾那道 `len(pending) > cap` 也钉在等号上。
    """
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    line_bytes = controller.MAX_HEADER_LINE_BYTES + (1 if over_cap else 0)
    path = builder.write_state_header_padded(T, "ifs", line_bytes=line_bytes)
    assert path.read_bytes().index(b"\n") == line_bytes  # 首行长度不由被测实现回读

    decision = _decide(builder, "ifs", _all_complete())
    if over_cap:
        _assert_stopped(decision, controller.StopReason.STATE_UNREADABLE)
    else:
        _assert_runnable(decision, T)


def test_long_blank_prefix_before_the_header_is_still_read(
    tmp_path: pathlib.Path,
) -> None:
    """跳过的前导空行 MUST NOT 计入候选行长度：数 MB 空行之后的首行仍被正确读出。

    与上一条成对：只看「无换行 -> 拒绝」的话，一个把**已丢弃的空白**也计入上界的实现
    同样恒绿，却会把合法状态误判成 `STATE_UNREADABLE`。
    """
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    blank_bytes = 4 * 1024 * 1024  # 远超 MAX_HEADER_LINE_BYTES 的空行前缀
    assert blank_bytes > controller.MAX_HEADER_LINE_BYTES
    path = builder.write_state_trailing_header(T, "ifs", blank_bytes=blank_bytes)
    assert path.stat().st_size > blank_bytes
    # 4 MiB 文件上跳过快照比对：摘要要读满整份文件，与本条的判别力无关
    _assert_runnable(_decide(builder, "ifs", _all_complete(), check_writes=False), T)


# --- 枚举/探测失败：不存在 ≠ 不可确定（裁决 9） ---


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


def _decide_while_unreadable(
    builder: YdRootBuilder,
    source: str,
    raw: RecordingRawComplete,
    target: pathlib.Path,
) -> controller.FrontierDecision:
    """在 `target` 不可读期间调用被测函数，并仍然证明零写入。"""
    before = snapshot_tree(builder.root)
    with _unreadable(target):
        decision = controller.decide_frontier(
            yd_root=builder.root, source=source, raw_complete=raw
        )
    assert snapshot_tree(builder.root) == before
    return decision


def test_unlistable_output_root_stops_instead_of_looking_like_a_fresh_chain(
    tmp_path: pathlib.Path,
) -> None:
    """fail-open 的方向性：`output/` 判空会把前沿倒退到已发布 cycle（裁决 9）。"""
    _skip_if_root()
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")
    builder.write_state(FRESH, "ifs")  # fail-open 时会被当成全新链的起点

    raw = _all_complete()
    decision = _decide_while_unreadable(builder, "ifs", raw, tmp_path / "output")
    _assert_stopped(decision, controller.StopReason.DISCOVERY_UNREADABLE)
    assert str(tmp_path / "output") in decision.detail
    assert raw.asked == []


def test_unreadable_latest_done_cycle_dir_does_not_roll_the_frontier_back(
    tmp_path: pathlib.Path,
) -> None:
    """单个 cycle 目录不可读 -> 停；MUST NOT 悄悄退回更旧的 `DONE`。"""
    _skip_if_root()
    builder = YdRootBuilder(tmp_path)
    builder.write_done("2026082512", "ifs")  # 更旧的 DONE：回退的话会落到它 +12h
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")

    raw = _all_complete()
    decision = _decide_while_unreadable(
        builder, "ifs", raw, tmp_path / "output" / D / "ifs"
    )
    _assert_stopped(decision, controller.StopReason.DISCOVERY_UNREADABLE)
    assert "DONE" in decision.detail
    assert raw.asked == []


def test_unlistable_states_dir_stops_with_discovery_unreadable(
    tmp_path: pathlib.Path,
) -> None:
    """不再是 `NO_INITIAL_STATE`：列不出来与「一份状态都没有」不是同一件事。"""
    _skip_if_root()
    builder = YdRootBuilder(tmp_path)
    builder.write_state(FRESH, "ifs")

    raw = _all_complete()
    decision = _decide_while_unreadable(builder, "ifs", raw, builder.states_dir("ifs"))
    _assert_stopped(decision, controller.StopReason.DISCOVERY_UNREADABLE)
    assert str(builder.states_dir("ifs")) in decision.detail
    assert raw.asked == []


def test_unreadable_states_parent_dir_is_classified_not_raised(
    tmp_path: pathlib.Path,
) -> None:
    """状态**存在性**探测遇 `EACCES` 也 MUST NOT 抛 `PermissionError`（裁决 9）。

    与既有的「状态文件 `chmod 0o000`」用例不同：那条里 `stat()` 仍然成功，失败落在已被
    guard 的读取处，对本条没有判别力。这里让**父目录**不可读，`os.stat` 自身就失败。
    """
    _skip_if_root()
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")

    raw = _all_complete()
    decision = _decide_while_unreadable(builder, "ifs", raw, builder.states_dir("ifs"))
    _assert_stopped(decision, controller.StopReason.DISCOVERY_UNREADABLE)
    assert str(builder.state_path(T, "ifs")) in decision.detail
    assert raw.asked == []


def test_discovery_unreadable_is_isolated_per_source(tmp_path: pathlib.Path) -> None:
    """新停止原因同样逐源隔离（裁决 9 的遗漏行，round 2 cand-14）。

    同一棵树、同一个不可读窗口内问两次：`output/<cycle>/gfs/` 不可读 -> gfs
    `DISCOVERY_UNREADABLE`，ifs 仍得到正常可跑结论。既有的逐源行只覆盖「缺失」与
    `STATE_MISSING`，四条 discovery 用例又全是单源；把 `done_cycles` 改成枚举
    `output/<cycle>/` 的目录项（而非逐源 stat `DONE`）会让 gfs 的 EACCES 漏给 ifs。
    """
    _skip_if_root()
    builder = YdRootBuilder(tmp_path)
    for source in ALL_SOURCES:
        builder.write_done(T, source)
        builder.write_state(T_PLUS_12, source)

    raw = _all_complete()
    before = snapshot_tree(builder.root)
    with _unreadable(tmp_path / "output" / T / "gfs"):
        gfs = controller.decide_frontier(
            yd_root=builder.root, source="gfs", raw_complete=raw
        )
        ifs = controller.decide_frontier(
            yd_root=builder.root, source="ifs", raw_complete=raw
        )
    assert snapshot_tree(builder.root) == before

    _assert_stopped(gfs, controller.StopReason.DISCOVERY_UNREADABLE)
    assert "DONE" in gfs.detail
    assert str(tmp_path / "output" / T / "gfs" / "DONE") in gfs.detail
    _assert_runnable(ifs, T_PLUS_12)
    assert raw.asked == [T_PLUS_12]  # gfs 停在探测层，根本没走到 raw


def test_absent_directories_still_mean_empty_sets(tmp_path: pathlib.Path) -> None:
    """errno 分流没有把「不存在」一起判成不可读：`output/` 缺 -> 全新链。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_state(FRESH, "ifs")
    assert not (tmp_path / "output").exists()
    _assert_runnable(_decide(builder, "ifs", _all_complete()), FRESH)


def test_absent_states_dir_still_means_no_initial_state(
    tmp_path: pathlib.Path,
) -> None:
    builder = YdRootBuilder(tmp_path)
    (tmp_path / "output").mkdir()
    assert not builder.states_dir("ifs").exists()
    _assert_stopped(
        _decide(builder, "ifs", _all_complete()),
        controller.StopReason.NO_INITIAL_STATE,
    )


# --- 可表示性门（裁决 5 增补） ---

#: 10 位、`%Y%m%d%H` 可解析，但 `+12h` 溢出 `datetime` 值域。
UNREPRESENTABLE = "9999123123"


def test_unrepresentable_done_cycle_is_invisible(tmp_path: pathlib.Path) -> None:
    """`datetime(9999,12,31,23) + 12h` 抛 `OverflowError`：该条目对前沿不可见。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")
    raw = _all_complete()
    baseline = _decide(builder, "ifs", raw)

    builder.write_done(UNREPRESENTABLE, "ifs")
    polluted = _decide(builder, "ifs", raw)

    _assert_runnable(polluted, T)
    assert dataclasses.astuple(polluted) == dataclasses.astuple(baseline)


def test_unrepresentable_state_name_is_invisible(tmp_path: pathlib.Path) -> None:
    """`states/` 侧对称：唯一的状态名不可表示 -> 该源没有合法首态。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_state(UNREPRESENTABLE, "ifs")
    _assert_stopped(
        _decide(builder, "ifs", _all_complete()),
        controller.StopReason.NO_INITIAL_STATE,
    )


def test_unrepresentable_state_name_does_not_change_a_healthy_verdict(
    tmp_path: pathlib.Path,
) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")
    raw = _all_complete()
    baseline = _decide(builder, "ifs", raw)

    builder.write_state(UNREPRESENTABLE, "ifs")
    polluted = _decide(builder, "ifs", raw)

    _assert_runnable(polluted, T)
    assert dataclasses.astuple(polluted) == dataclasses.astuple(baseline)


# --- raw 缺口 ---


def test_incomplete_raw_stops_at_t_without_submitting(tmp_path: pathlib.Path) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")
    raw = RecordingRawComplete(set())
    decision = _decide(builder, "ifs", raw)
    _assert_stopped(decision, controller.StopReason.RAW_INCOMPLETE)
    assert raw.asked == [T]


def test_permanent_gap_blocks_and_never_skips_to_a_later_complete_cycle(
    tmp_path: pathlib.Path,
) -> None:
    """T 的 raw 缺、T+12h/T+24h 齐：结论仍是停在 T，返回值里 MUST NOT 出现更晚 cycle。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    for cycle_text in (T, T_PLUS_12, T_PLUS_24):
        builder.write_state(cycle_text, "ifs")
    raw = RecordingRawComplete({T_PLUS_12, T_PLUS_24})
    decision = _decide(builder, "ifs", raw)
    _assert_stopped(decision, controller.StopReason.RAW_INCOMPLETE)
    assert raw.asked == [T]
    assert T_PLUS_12 not in decision.detail
    assert T_PLUS_24 not in decision.detail
    assert decision.cycle is None


def test_state_check_runs_before_raw_and_raw_is_not_consulted(
    tmp_path: pathlib.Path,
) -> None:
    """状态缺失**且** raw 也未齐 → `STATE_MISSING`，且 `raw_complete` 一次都不被调用。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state(D, "ifs")
    raw = RecordingRawComplete(set())
    decision = _decide(builder, "ifs", raw)
    _assert_stopped(decision, controller.StopReason.STATE_MISSING)
    assert raw.calls == []


# --- 非法条目不砖化（`states/` 与 `output/` 对称） ---


def _clean_tree(root: pathlib.Path) -> YdRootBuilder:
    builder = YdRootBuilder(root)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")
    return builder


def test_illegal_entries_on_both_sides_do_not_change_the_verdict(
    tmp_path: pathlib.Path,
) -> None:
    clean = _clean_tree(tmp_path / "clean")
    cluttered = _clean_tree(tmp_path / "cluttered")
    cluttered.write_states_clutter("ifs")
    cluttered.write_output_clutter()

    raw = _all_complete()
    clean_decision = _decide(clean, "ifs", raw)
    cluttered_decision = _decide(cluttered, "ifs", raw)

    _assert_runnable(clean_decision, T)
    _assert_runnable(cluttered_decision, T)
    assert cluttered_decision.stop_reason == clean_decision.stop_reason
    assert cluttered_decision.source == clean_decision.source


def test_illegal_ten_digit_names_are_invisible_to_the_frontier(
    tmp_path: pathlib.Path,
) -> None:
    """`2026023100`（2 月 31 日）与 `9999999999` 是 10 位数字却非法 → 不计入 DONE 集合。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_output_clutter()  # 这两个非法 cycle 下都写了 gfs/DONE
    builder.write_state(FRESH, "gfs")
    # 若非法名被当成 DONE，结论会变成某个荒谬的 D+12h 而不是全新链
    _assert_runnable(_decide(builder, "gfs", _all_complete()), FRESH)


# --- `DONE` 必须是普通文件 ---


def test_done_must_be_a_regular_file(tmp_path: pathlib.Path) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_done_as_directory(D, "ifs")
    builder.write_done_as_dangling_symlink("2026082512", "ifs")
    builder.write_state(FRESH, "ifs")
    # 两个伪 DONE 都不计入 → 走全新链，而不是 D+12h
    _assert_runnable(_decide(builder, "ifs", _all_complete()), FRESH)


def test_pseudo_done_entries_fall_back_to_no_initial_state(
    tmp_path: pathlib.Path,
) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_done_as_directory(D, "ifs")
    builder.write_done_as_dangling_symlink("2026082512", "ifs")
    raw = _all_complete()
    decision = _decide(builder, "ifs", raw)
    _assert_stopped(decision, controller.StopReason.NO_INITIAL_STATE)
    assert raw.asked == []


# --- 返回值不变量与词表 ---


def test_stop_reason_vocabulary_is_closed() -> None:
    """裁决 9 起词表由 5 项扩为 6 项：新增 `DISCOVERY_UNREADABLE`。"""
    assert {reason.name for reason in controller.StopReason} == {
        "NO_INITIAL_STATE",
        "DISCOVERY_UNREADABLE",
        "STATE_MISSING",
        "STATE_UNREADABLE",
        "HEADER_TIME_MISMATCH",
        "RAW_INCOMPLETE",
    }


@pytest.mark.parametrize(
    ("cycle", "stop_reason"),
    [
        (None, None),
        (parse_cycle(T), controller.StopReason.RAW_INCOMPLETE),
    ],
)
def test_decision_requires_exactly_one_of_cycle_and_stop_reason(
    cycle: object, stop_reason: object
) -> None:
    with pytest.raises(ValueError, match="恰有一个"):
        controller.FrontierDecision(
            source="ifs",
            cycle=cycle,  # type: ignore[arg-type]
            stop_reason=stop_reason,  # type: ignore[arg-type]
            detail="",
        )


def test_returned_cycle_is_utc_aware(tmp_path: pathlib.Path) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_state(FRESH, "ifs")
    decision = _decide(builder, "ifs", _all_complete())
    assert decision.cycle is not None
    assert decision.cycle.utcoffset() == shift(FRESH, 0).utcoffset()
    assert decision.cycle.tzinfo is not None


def test_decide_frontier_declares_the_raw_complete_input_domain() -> None:
    """裁决 10：注入的 `raw_complete` 的可接受输入域是**声明的前置条件**，钉在源码里。

    本函数返回的 T 可能带任意可解析的 cycle 小时（裁决 5 刻意如此），而 `rawscan.judge`
    只对 `config.cycle.hours` 全域——接线方（#26 组 14）不知道这条就会让一个 stray 的
    18Z 状态名把「停一个源」放大成「整个 tick 崩」。这条断言让后续编辑无法悄悄删掉它。
    """
    doc = controller.decide_frontier.__doc__ or ""
    for phrase in ("raw_complete", "任意可解析", "全域", "ConfigError"):
        assert phrase in doc, phrase


def test_raw_complete_exceptions_are_the_callers_responsibility(
    tmp_path: pathlib.Path,
) -> None:
    """与上一条配对的行为证据：本函数不守卫注入判定，异常原样穿透（归 #26）。"""
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "ifs")
    builder.write_state(T, "ifs")

    class _Boom(Exception):
        pass

    def _raising(_cycle: object) -> bool:
        raise _Boom("调用方的输入域责任")

    with pytest.raises(_Boom):
        controller.decide_frontier(
            yd_root=builder.root,
            source="ifs",
            raw_complete=_raising,  # type: ignore[arg-type]
        )


def test_frontier_stride_is_twelve_hours() -> None:
    assert controller.CYCLE_STRIDE.total_seconds() == 12 * 3600


# --- 溯源与隔离 ---


def test_controller_module_stays_nwm_and_db_free() -> None:
    source = pathlib.Path(controller.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "import packages",
        "from packages",
        "psycopg",
        "sqlalchemy",
        "DATABASE_URL",
        "sacct",
        "sbatch",
    ):
        assert forbidden not in source


def test_controller_module_does_not_port_the_relative_minute_reader() -> None:
    """裁决 2：`_valid_time_from_header_minute` 只允许作为「刻意不移植」出现在模块头。"""
    source = pathlib.Path(controller.__file__).read_text(encoding="utf-8")
    head = source[: source.index('"""', 3) + 3]
    assert source.count("_valid_time_from_header_minute") == 1
    assert "_valid_time_from_header_minute" in head


def test_controller_module_declares_zero_writes_and_carries_no_write_calls() -> None:
    source = pathlib.Path(controller.__file__).read_text(encoding="utf-8")
    head = source[: source.index('"""', 3) + 3]
    assert "零写入" in head
    body = source[source.index('"""', 3) + 3 :]
    for forbidden in (
        ".unlink(",
        ".rmdir(",
        ".mkdir(",
        ".rename(",
        ".write_text(",
        ".write_bytes(",
        "shutil.",
        "os.remove",
        "os.replace",
    ):
        assert forbidden not in body, forbidden
