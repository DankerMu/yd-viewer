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
    EPOCH_MINUTES_27_00Z,
    NOW,
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
    """回归行 15：未来 cycle 与 `now - 12h` 并存 -> T 取 `now - 12h`。

    按日期网格枚举、忘了 `cycle <= now` 的实现会把未来 cycle 排在**同一天更早的小时**上
    并先枚举到它（08-27 00Z < 08-28 00Z 不成立时才安全），故本行是那条过滤的直接证据。
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
