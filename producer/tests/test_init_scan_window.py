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
    skip_if_root,
    snapshot,
    unreadable,
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
    # 确为**缺文件**时才提示等 raw 补齐——这是不可读腿 MUST NOT 复用的那句话。
    assert "等待 raw 补齐后重跑 init" in report.detail
    assert_zero_write(tree, before_states, before_output)


def test_unreadable_raw_is_not_disguised_as_a_missing_raw_refusal(
    tmp_path: Path,
) -> None:
    """[桶 C-4] `NO_COMPLETE_RAW_CYCLE` MUST 区分「缺数据」与「不可读」（cand-R3-02）。

    构造：ifs 窗内**唯一**的完整 cycle 的两个 bundle 文件 `chmod 0o000`——`rawscan.judge`
    因此返回 `missing_files == 0`、`unreadable_files == 2`，cycle 判不完整。方向不变（仍
    整体拒绝、仍零写入、仍 fail closed），但 `detail` MUST 点明存在**不可读**的 raw 文件，
    MUST NOT 出现「等待 raw 补齐后重跑 init」：生产 raw 根是 NFS 上由 NWM 以另一 uid 写入
    的树，权限故障最现实；把它伪装成缺数据，运维会对着**已在盘上**的数据永远重跑。同一
    伪装本模块已在 `cycle.hours` 路径上禁止（见上面的空 `hours` 一行），本行只是把同一条
    理由施加到未被守卫的 raw 权限面上。

    判别变异体：把 detail 退回不区分的原话术（即 `_first_complete_cycle` 只返回
    `.complete`、丢弃 `unreadable_files`）-> 本行必红。
    """
    skip_if_root()
    tree = Tree(tmp_path)
    cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    tree.write_cycle("gfs", cycle)
    bundle_dir = tree.write_cycle("ifs", cycle)
    bundles = sorted(path for path in bundle_dir.iterdir() if path.is_file())
    assert len(bundles) == 2
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    with unreadable(bundles[0]), unreadable(bundles[1]):
        report = tree.run()

    assert report.refusal is InitRefusal.NO_COMPLETE_RAW_CYCLE
    assert "ifs" in report.detail
    # 点明「存在但不可读」，并带上数目与路径。
    assert "不可读" in report.detail
    assert "2 个 raw 文件" in report.detail
    assert str(bundles[0]) in report.detail
    # 缺数据的补救话术 MUST NOT 被复用到权限故障上。
    assert "等待 raw 补齐" not in report.detail
    assert_zero_write(tree, before_states, before_output)


def test_mixed_missing_and_unreadable_raw_are_both_named(tmp_path: Path) -> None:
    """[桶 C-9] 缺文件与不可读**同时存在**时 MUST 并列点名（round 4 R4-B）。

    构造：ifs 窗内唯一存在的候选 `complete=False`（少铺最后一个预期文件 -> `missing_files`
    非空），再把它剩下的那个文件 `chmod 0o000`（-> `unreadable_files` 非空）。`judge` 因此
    同时返回两类非空集合，这是生产 NFS 上的主导形态（`rawscan.py` 自陈 7 天窗的绝大多数
    请求落在缺文件一侧）。

    方向不变（仍整体拒绝、仍零写入），但 detail MUST 并列点名两者：
    - MUST NOT 出现「不是缺数据」这类**全称否定**——混合态下它字面为假；
    - 仍 MUST NOT 出现「等待 raw 补齐后重跑」——`§6.2` 逐字限定该提示只在**确为纯缺文件**
      时才允许，给混合态补一句「补齐 missing」会把权限故障的补救指令稀释掉。

    判别变异体：把并列措辞退回全称否定（即 `_first_complete_cycle` 丢弃 `missing_files`、
    分支固定说「不是缺数据」）-> 本行必红。
    """
    skip_if_root()
    tree = Tree(tmp_path)
    cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    tree.write_cycle("gfs", cycle)
    bundle_dir = tree.write_cycle("ifs", cycle, complete=False)
    present = sorted(path for path in bundle_dir.iterdir() if path.is_file())
    assert len(present) == 1  # 少铺的那个即 `missing_files`
    before_states = snapshot(tree.states)
    before_output = snapshot(tree.output)

    with unreadable(present[0]):
        report = tree.run()

    assert report.refusal is InitRefusal.NO_COMPLETE_RAW_CYCLE
    assert "ifs" in report.detail
    # 并列点名：不可读侧带数目与路径，缺失侧同样带数目。
    assert "不可读" in report.detail
    assert "1 个 raw 文件" in report.detail
    assert str(present[0]) in report.detail
    assert "缺失" in report.detail
    assert "1 个预期 raw 文件" in report.detail
    # 混合态下这两句都 MUST NOT 出现。
    assert "不是缺数据" not in report.detail
    assert "等待 raw 补齐" not in report.detail
    assert_zero_write(tree, before_states, before_output)


def test_unreadable_raw_on_a_skipped_candidate_is_named_in_the_success_detail(
    tmp_path: Path,
) -> None:
    """[桶 C-8] 被跳过候选上的不可读 raw MUST 在**成功**理由中点名（round 4 R4-C）。

    构造：ifs 在 `T0 = 08-25 00Z` 与 `T0 + 12h` 各有一轮完整 raw，但把 `T0` 的一个 bundle
    文件 `chmod 0o000` —— 该候选因此判不完整而被跳过，ifs 首轮 T 落在 `T0 + 12h`。

    方向 MUST NOT 变：init **照样建链**（改成 fail-closed 拒绝会让一次 raw 权限故障阻断
    整次建链，与 `§6.2` 的「方向不变、区别只在给运维的下一步动作」冲突），也 MUST NOT 扩
    词表。但链起点被静默推后 12h **并落盘**之后根已非全新，重跑必被 `STATES_NOT_EMPTY`
    拒绝——静默偏移没有自愈路径，故成功 detail MUST 点名这些不可读文件。

    判别变异体：恢复 `_first_complete_cycle` 命中时的 `return cycle, ()` 丢弃行为 -> 本行
    必红。
    """
    skip_if_root()
    tree = Tree(tmp_path)
    skipped_cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    chosen_cycle = datetime(2026, 8, 25, 12, tzinfo=UTC)
    tree.write_cycle("gfs", chosen_cycle)
    skipped_dir = tree.write_cycle("ifs", skipped_cycle)
    tree.write_cycle("ifs", chosen_cycle)
    bundles = sorted(path for path in skipped_dir.iterdir() if path.is_file())
    assert len(bundles) == 2

    with unreadable(bundles[0]):
        report = tree.run()

    # 方向不变：照样建链，两源首态都落盘，T 取更晚的那个 cycle。
    assert report.refusal is None
    assert [path.name for path in report.written] == [
        "2026082512" + STATE_SUFFIX,
        "2026082512" + STATE_SUFFIX,
    ]
    # 成功理由 MUST 点名被跳过候选上的不可读文件（数目 + 路径）。
    assert "不可读" in report.detail
    assert "1 个 raw 文件" in report.detail
    assert str(bundles[0]) in report.detail
    # 只有 ifs 被跳过；gfs 一次跳过也没有，MUST NOT 被牵连点名。
    assert report.detail.count("存在但不可读") == 1
    assert "ifs 的链起点跳过了更早的候选" in report.detail


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
    """回归行 15 的**日期网格上界**侧：`now + 12h`、`now - 12h`、`now` 并存 -> T 取 `now - 12h`。

    本行约束的**不是** `cycle <= now` 这个比较：`NOW` 是 12Z、恰在候选网格最高点上，
    `NOW + 12h` 落到 08-28，而 `_candidate_cycles` 的 `span = (now.date() - start_date)`
    根本不枚举那一天。枚举序也不作数——`_candidate_cycles` 返回 `tuple(sorted(...))`。
    真正钉死 `cycle <= now` 的是下面两条 off-grid 用例。

    [桶 C-6]（round 4 R4-D）：本构造此前只铺 `NOW ± 12h`，而 `NOW + 12h` 在**进入选取
    循环之前**就被日期网格排除，剩下的 complete 候选集是**单元素** `{NOW - 12h}`——「升序
    取第一个」与「取窗内最晚」在单元素集上恒等，本行对选取分支毫无判别力。故额外铺一轮
    `NOW`（它落在候选网格最高点上、`cycle <= now` 取等号，是合法候选），使 complete 候选
    集含**两个**元素，本行才真正行使选取分支。判别变异体：把「升序取第一个 complete」改为
    「取窗内最晚 complete」-> 本行必红（T 会变成 `NOW`）。
    """
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, NOW + timedelta(hours=12))
        tree.write_cycle(source, NOW - timedelta(hours=12))
        tree.write_cycle(source, NOW)

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

    本条钉死的是**选取序**：候选集升序、取第一个完整 cycle，故更晚的 cycle 不可能夺走首轮。
    它**不是** `cycle <= now` 过滤器的证据——`_candidate_cycles` 返回 `tuple(sorted(...))`，
    把该比较整条删掉后 08-26T12Z 仍排在 08-27T12Z 之前，本用例仍绿（实测该变异下唯一转红的
    是上一条）。过滤器的判别证据由兄弟用例
    `test_same_day_future_cycle_is_excluded_by_the_now_comparison` 单独承担。

    [桶 C-6]（round 4 R4-D）：本构造此前只铺 08-27T12Z 与 08-26T12Z，而前者被 `cycle <= now`
    在**进入选取循环之前**滤掉，剩下的 complete 候选集是**单元素**，本行对选取分支毫无判别
    力。故额外铺一轮 08-27T00Z（`<= now = 08-27T06Z`，是合法候选），使 complete 候选集含
    **两个**元素。判别变异体：把「升序取第一个 complete」改为「取窗内最晚 complete」-> 本行
    必红（T 会变成 08-27T00Z）。
    """
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 27, 12, tzinfo=UTC))
        tree.write_cycle(source, datetime(2026, 8, 27, 0, tzinfo=UTC))
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
