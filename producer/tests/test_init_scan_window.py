"""`init.bootstrap` 的扫描窗与候选 cycle 语义（tasks.md 任务 11.1、issue #21）。

覆盖窗下界、未来 cycle、跳过不完整候选、`NO_COMPLETE_RAW_CYCLE`、`now` 的时区处理与
`judge` 的 `ConfigError` 上抛；合成树、锚点常量与期望值口径见 `init_bootstrap_fixtures`。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from init_bootstrap_fixtures import (
    EPOCH_MINUTES_20_12Z,
    EPOCH_MINUTES_21_12Z,
    EPOCH_MINUTES_26_12Z,
    EPOCH_MINUTES_27_00Z,
    EPOCH_MINUTES_27_12Z,
    NOW,
    NOW_OFF_GRID,
    STATE_SUFFIX,
    WINDOW_START,
    WRITE_ORDER,
    Tree,
    all_files,
    assert_zero_write,
    expected_bytes,
    make_config,
    snapshot,
)

from yd_producer.config import ConfigError
from yd_producer.init import InitRefusal

# --- 非默认配置取值 ----------------------------------------------------------


def test_non_default_config_values_drive_variant_and_cycle_lookup(
    tmp_path: Path,
) -> None:
    """回归行 18：`cycle.hours=[12]` 单值 + 非默认变体目录名。

    硬编码 `[0, 12]` 的实现会把 08-25 00Z 当候选并取它作首轮（本行因此必红）；硬编码
    `input/models/yd_<source>` 的实现连率定末态都定位不到。
    """
    config = make_config(
        cycle_hours=(12,),
        variants={"ifs": "input/models/alt_ifs", "gfs": "input/models/alt_gfs"},
    )
    tree = Tree(tmp_path, config=config)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))  # 非 12Z
        tree.write_cycle(source, datetime(2026, 8, 26, 12, tzinfo=UTC))

    report = tree.run()

    assert report.refusal is None
    assert [path.name for path in report.written] == [
        "2026082612" + STATE_SUFFIX,
        "2026082612" + STATE_SUFFIX,
    ]
    assert tree.variant_dir("gfs").name == "alt_gfs"


# --- 扫描窗 ------------------------------------------------------------------


def test_one_source_without_complete_cycle_refuses_the_whole_run(
    tmp_path: Path,
) -> None:
    """回归行 4 / spec「单源窗内无完整 raw 即整体拒绝」：`states/` 下**无任何文件**。"""
    tree = Tree(tmp_path)
    tree.write_cycle("gfs", datetime(2026, 8, 25, 0, tzinfo=UTC))
    tree.write_cycle("ifs", datetime(2026, 8, 25, 0, tzinfo=UTC), complete=False)
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run()

    assert report.refusal is InitRefusal.NO_COMPLETE_RAW_CYCLE
    assert "ifs" in report.detail
    assert WINDOW_START.isoformat() in report.detail
    assert NOW.isoformat() in report.detail
    assert_zero_write(tree, before_states, before_output)


def test_window_lower_bound_is_closed(tmp_path: Path) -> None:
    """回归行 13a：唯一完整 cycle 恰好落在 `now - 7 天` -> 被接受。"""
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, WINDOW_START)

    report = tree.run()

    assert report.refusal is None
    for path in report.written:
        assert path.name == "2026082012" + STATE_SUFFIX
        assert path.read_bytes() == expected_bytes(
            tree.payloads[path.parent.name], EPOCH_MINUTES_20_12Z
        )


def test_cycle_one_step_before_the_window_is_excluded(tmp_path: Path) -> None:
    """回归行 13b：把同一 cycle 整体前移**一个 cycle 步长**（12h）-> 落到窗外即拒绝。

    位移取整 cycle 步长而非 1 小时：`cycle.hours=(0, 12)` 下 08-20 00Z 仍是合法候选，只是
    落在 `now - 7 天` 之前，故本行钉死的是**窗下界**本身；一个回扫 30 天的实现在此必红。
    """
    tree = Tree(tmp_path)
    shifted = WINDOW_START - timedelta(hours=12)
    assert shifted.hour in tree.config.cycle.hours  # 仍在候选网格上
    for source in WRITE_ORDER:
        tree.write_cycle(source, shifted)

    report = tree.run()

    assert report.refusal is InitRefusal.NO_COMPLETE_RAW_CYCLE
    assert all_files(tree.states) == []


def test_future_cycle_is_not_a_candidate(tmp_path: Path) -> None:
    """回归行 14：唯一的完整 cycle 落在 `now + 12 小时` -> 不进候选集。"""
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, NOW + timedelta(hours=12))

    report = tree.run()

    assert report.refusal is InitRefusal.NO_COMPLETE_RAW_CYCLE
    assert all_files(tree.states) == []


def test_future_cycle_does_not_win_the_first_frontier(tmp_path: Path) -> None:
    """回归行 15 的**日期网格上界**侧：`now + 12h` 与 `now - 12h` 并存 -> T 取 `now - 12h`。

    本行约束的**不是** `cycle <= now` 这个比较：`NOW` 是 12Z、恰在候选网格最高点上，
    `NOW + 12h` 落到 08-28，而 `_candidate_cycles` 的 `span = (now.date() - start_date)`
    根本不枚举那一天。枚举序也不作数——`_candidate_cycles` 返回 `tuple(sorted(...))`。
    真正钉死 `cycle <= now` 的是下面两条 off-grid 用例。
    """
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, NOW + timedelta(hours=12))
        tree.write_cycle(source, NOW - timedelta(hours=12))

    report = tree.run()

    assert report.refusal is None
    for path in report.written:
        assert path.name == "2026082700" + STATE_SUFFIX
        header = path.read_bytes().splitlines()[0].decode()
        assert header.split()[-1] == f"{float(EPOCH_MINUTES_27_00Z):.6f}"


def test_first_complete_cycle_skips_incomplete_candidates(tmp_path: Path) -> None:
    """回归行 16：窗内最早的两个候选不完整、第三个完整 -> T 取第三个。"""
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, WINDOW_START, complete=False)
        tree.write_cycle(source, datetime(2026, 8, 21, 0, tzinfo=UTC), complete=False)
        tree.write_cycle(source, datetime(2026, 8, 21, 12, tzinfo=UTC))
        tree.write_cycle(source, datetime(2026, 8, 22, 0, tzinfo=UTC))

    report = tree.run()

    assert report.refusal is None
    for path in report.written:
        assert path.name == "2026082112" + STATE_SUFFIX
        header = path.read_bytes().splitlines()[0].decode()
        assert header.split()[-1] == f"{float(EPOCH_MINUTES_21_12Z):.6f}"


def test_config_error_from_judge_propagates_untouched(tmp_path: Path) -> None:
    """回归行 11：`judge` 的 `ConfigError` 原样上抛，MUST NOT 收敛成「不完整」。

    两个 bundle 模式渲染出同名 -> 预期集不再单射，`rawscan` fail closed。若被吞成
    `NO_COMPLETE_RAW_CYCLE`，运维会永远等一个补不齐的 raw。
    """
    config = make_config(
        gfs_bundles=(
            "gfs.f{lead:03d}.grib2",
            "gfs.f{lead:03d}.grib2",
        )
    )
    tree = Tree(tmp_path, config=config)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    with pytest.raises(ConfigError) as excinfo:
        tree.run()

    assert "raw.gfs.bundles" in str(excinfo.value)
    assert_zero_write(tree, before_states, before_output)


def test_naive_now_is_refused_before_any_filesystem_access(tmp_path: Path) -> None:
    """回归行 12：naive `now` -> `ConfigError`，零写入，MUST NOT 按宿主时区静默重释。"""
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    with pytest.raises(ConfigError):
        tree.run(now=NOW.replace(tzinfo=None))

    assert_zero_write(tree, before_states, before_output)


def test_non_utc_aware_now_is_normalised_not_rejected(tmp_path: Path) -> None:
    """aware 但非 UTC 的 `now` 归一为 UTC（与 naive 的拒绝分流）。"""
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))

    report = tree.run(now=NOW.astimezone(timezone(timedelta(hours=8))))

    assert report.refusal is None
    assert [path.name for path in report.written] == [
        "2026082500" + STATE_SUFFIX,
        "2026082500" + STATE_SUFFIX,
    ]


# --- `cycle <= now` 与窗上端点（round 1 cand-02）------------------------------
#
# 上面那条 `NOW ± 12h` 的用例只约束**日期网格上界**：`NOW` 是 12Z、落在候选网格最高点，
# `NOW + 12h` 因此跨到一个从不被枚举的日期上。下面三条改用 off-grid 的 `now`，把未来
# cycle 放到**同一个被枚举的日期**上，才真正行使 `cycle <= now` 这个比较；第三条独立钉死
# 上端点的**闭**合。


def test_same_day_future_cycle_is_excluded_by_the_now_comparison(
    tmp_path: Path,
) -> None:
    """off-grid `now = 08-27T06:00Z`，窗内唯一完整 cycle 落在**同日** 12Z -> 整体拒绝。

    08-27 是被枚举的日期（`span` 算到 `now.date()`），故排除它的**只能**是
    `cycle <= now`。把该比较删掉的实现在本行必红。
    """
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 27, 12, tzinfo=UTC))
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    report = tree.run(now=NOW_OFF_GRID)

    assert report.refusal is InitRefusal.NO_COMPLETE_RAW_CYCLE
    assert NOW_OFF_GRID.isoformat() in report.detail
    assert_zero_write(tree, before_states, before_output)


def test_same_day_future_cycle_does_not_win_over_an_earlier_one(
    tmp_path: Path,
) -> None:
    """同一 off-grid `now`：08-26T12Z 与同日 08-27T12Z 并存 -> T 取 08-26T12Z。

    与上一条互补——上一条证明未来 cycle 不被**接受**，本条证明它不被**优先**，即
    `cycle <= now` 是过滤而不是排序副作用。
    """
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 27, 12, tzinfo=UTC))
        tree.write_cycle(source, datetime(2026, 8, 26, 12, tzinfo=UTC))

    report = tree.run(now=NOW_OFF_GRID)

    assert report.refusal is None
    for path in report.written:
        assert path.name == "2026082612" + STATE_SUFFIX
        header = path.read_bytes().splitlines()[0].decode()
        assert header.split()[-1] == f"{float(EPOCH_MINUTES_26_12Z):.6f}"


def test_window_upper_bound_is_closed(tmp_path: Path) -> None:
    """窗**上**端点闭：窗内唯一完整 cycle **恰好等于** `now` -> 被接受为首轮 T。

    没有这一行，把判据写成 `window_start <= cycle < now` 的实现全套仍全绿——上面两条
    只需要「未来 cycle 被排除」，对 `cycle == now` 无约束。
    """
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, NOW)

    report = tree.run()

    assert report.refusal is None
    for path in report.written:
        assert path.name == "2026082712" + STATE_SUFFIX
        assert path.read_bytes() == expected_bytes(
            tree.payloads[path.parent.name], EPOCH_MINUTES_27_12Z
        )


# --- `cycle.hours` 取值域自查（round 1 cand-01）------------------------------
#
# `_candidate_cycles` 是本路径上 `config.cycle.hours` 的第一个消费者、跑在任何
# `rawscan.judge` 之前，故全仓唯一的域校验 `rawscan._validate_config_domain`（在 `judge`
# 体内）对下面两个输入**结构性不可达**。


def test_empty_cycle_hours_is_a_config_error_not_a_missing_raw_refusal(
    tmp_path: Path,
) -> None:
    """`hours = ()` + raw **齐备** -> `ConfigError` 点名 `cycle.hours`。

    MUST NOT 退化成 `NO_COMPLETE_RAW_CYCLE`：候选集为空使 `judge` 一次都不调，
    「等待 raw 补齐后重跑 init」把一个配置错误伪装成缺数据，运维会永远重跑。
    """
    tree = Tree(tmp_path, config=make_config(cycle_hours=()))
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    with pytest.raises(ConfigError) as excinfo:
        tree.run()

    assert "cycle.hours" in str(excinfo.value)
    assert InitRefusal.NO_COMPLETE_RAW_CYCLE.value not in str(excinfo.value)
    assert "等待 raw 补齐" not in str(excinfo.value)
    assert_zero_write(tree, before_states, before_output)


@pytest.mark.parametrize("hours", [(25,), (0, 25), (-1,), (24,)])
def test_out_of_range_cycle_hours_raise_config_error_not_bare_value_error(
    tmp_path: Path, hours: tuple[int, ...]
) -> None:
    """`hours` 含 `0..23` 之外的值 -> `ConfigError`，**不是**裸 `ValueError`。

    `datetime(..., hour=25)` 抛的裸 `ValueError` 接不住于 `cli.main` 的
    `except ConfigError`，traceback 会逃逸出 CLI，违反裁决 6「MUST NOT 以异常逃逸」。
    `ConfigError` 是 `ValueError` 的子类，故本行断言 `type` 而不是 `isinstance`。
    """
    tree = Tree(tmp_path, config=make_config(cycle_hours=hours))
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    with pytest.raises(ConfigError) as excinfo:
        tree.run()

    assert type(excinfo.value) is ConfigError
    assert "cycle.hours" in str(excinfo.value)
    assert_zero_write(tree, before_states, before_output)


def test_in_range_but_off_domain_cycle_hour_still_comes_from_rawscan(
    tmp_path: Path,
) -> None:
    """控制行：`hours = (13,)` 网格可构造 -> 域校验仍由 `rawscan.judge` 施加。

    本模块 MUST NOT 重新声明 `{0, 12}` 这个域；新增的自查只补「网格不可构造」的两个洞，
    `rawscan` 仍是取值域的唯一权威。把 `{0, 12}` 抄进 `init` 的实现在本行看不出区别，
    但会在 `rawscan` 改域时静默分叉——故断言异常文本来自 `rawscan` 的措辞。
    """
    tree = Tree(tmp_path, config=make_config(cycle_hours=(13,)))
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 13, tzinfo=UTC))

    with pytest.raises(ConfigError) as excinfo:
        tree.run()

    assert "cycle.hours" in str(excinfo.value)
    assert all_files(tree.states) == []
