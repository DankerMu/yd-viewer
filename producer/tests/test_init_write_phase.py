"""`init.bootstrap` 阶段 B 的落盘与 CLI 端到端（tasks.md 任务 11.1、issue #21）。

覆盖成功路径的重戳字节、写入序、部分落盘的收尾、「不跑 SHUD / 不写 DONE / 不碰
`output/`」的负面证据、与 #22 前沿函数的跨 issue 兼容，以及 `cli.main(["init", ...])`；
合成树、锚点常量与期望值口径见 `init_bootstrap_fixtures`。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from init_bootstrap_fixtures import (
    CYCLE_DIR_FORMAT,
    DEFAULT_VARIANTS,
    DIR_SEGMENTS,
    EPOCH_MINUTES_22_00Z,
    EPOCH_MINUTES_24_12Z,
    EPOCH_MINUTES_25_00Z,
    EPOCH_MINUTES_26_12Z,
    STATE_SUFFIX,
    WRITE_ORDER,
    Tree,
    all_files,
    compat_four_token_payload,
    default_payload,
    expected_bytes,
    large_payload,
    skip_if_root,
    snapshot,
)

from yd_producer import controller, init
from yd_producer.init import InitRefusal
from yd_producer.store import safe_fs

#: 收尾话术的三段常量，逐字写死在测试侧（从被测模块 import 会让断言变成恒真式）。
CLEANUP_CLAIM = "根已非全新，重跑 init 前需人工清理 `states/`"
FRESH_CLAIM = "零写入，根仍是全新根"
PARTIAL_CLAIM = "可能已被部分写入"

# --- 成功路径 ----------------------------------------------------------------


def test_fresh_root_writes_one_restamped_state_per_source(tmp_path: Path) -> None:
    """回归行 1：两源各自最早完整 cycle 不同 -> 两个文件落盘、`output/` 逐字节不变。"""
    tree = Tree(tmp_path)
    (tree.output / "2026082400" / "gfs").mkdir(parents=True)  # 无 DONE 的空壳
    before_output = snapshot(tree.output)
    # ifs 首轮 = 08-22 00Z（更早的 08-21 00Z 不完整）；gfs 首轮 = 08-24 12Z。
    tree.write_cycle("ifs", datetime(2026, 8, 21, 0, tzinfo=UTC), complete=False)
    tree.write_cycle("ifs", datetime(2026, 8, 22, 0, tzinfo=UTC))
    tree.write_cycle("ifs", datetime(2026, 8, 25, 0, tzinfo=UTC))
    tree.write_cycle("gfs", datetime(2026, 8, 24, 12, tzinfo=UTC))

    report = tree.run()

    assert report.refusal is None
    ifs_target = tree.state_path("ifs", datetime(2026, 8, 22, 0, tzinfo=UTC))
    gfs_target = tree.state_path("gfs", datetime(2026, 8, 24, 12, tzinfo=UTC))
    assert report.written == (ifs_target, gfs_target)
    assert ifs_target.read_bytes() == expected_bytes(
        tree.payloads["ifs"], EPOCH_MINUTES_22_00Z
    )
    assert gfs_target.read_bytes() == expected_bytes(
        tree.payloads["gfs"], EPOCH_MINUTES_24_12Z
    )
    assert all_files(tree.states) == [gfs_target, ifs_target]
    assert snapshot(tree.output) == before_output


def test_written_header_minute_token_matches_the_filename_cycle(
    tmp_path: Path,
) -> None:
    """契约身份：文件名 T 与 header 的分钟 token 互相对应（期望值取自锚点常量）。"""
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))

    report = tree.run()

    assert report.refusal is None
    for path in report.written:
        assert path.name == "2026082500" + STATE_SUFFIX
        header = path.read_bytes().splitlines()[0].decode()
        assert header.split()[-1] == f"{float(EPOCH_MINUTES_25_00Z):.6f}"


def test_four_token_compat_header_is_restamped_byte_faithfully(
    tmp_path: Path,
) -> None:
    """回归行 17：4 token 兼容 header（含 lake 段）同样重戳，其余字节逐字不变。"""
    payload = compat_four_token_payload()
    tree = Tree(tmp_path, payloads={"ifs": payload, "gfs": payload})
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 26, 12, tzinfo=UTC))

    report = tree.run()

    assert report.refusal is None
    for path in report.written:
        assert path.read_bytes() == expected_bytes(payload, EPOCH_MINUTES_26_12Z)


# --- 阶段 B：部分落盘的收尾（裁决 5）----------------------------------------


def test_partial_write_reports_landed_paths_without_rolling_back(
    tmp_path: Path,
) -> None:
    """回归行 19 / spec「写入阶段失败的收尾可观测」。

    构造：把 `states/gfs/<T_gfs>.cfg.ic` 预置为**空目录**——它不是普通文件，故过得了阶段 A
    的守卫；而 `write_bytes_no_follow_exclusive` 的 `O_CREAT|O_EXCL` 对任何已存在条目都得
    `EEXIST`。预置**普通文件**会在阶段 A 就命中 `STATES_NOT_EMPTY`、永远走不到阶段 B，那样
    构造出的用例是假绿；用 monkeypatch 注入写入失败同样不行——那会让「阶段 B 真的用了
    `O_EXCL`」退化成永真式。
    """
    tree = Tree(tmp_path)
    cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    for source in WRITE_ORDER:
        tree.write_cycle(source, cycle)
    blocker = tree.state_path("gfs", cycle)
    blocker.mkdir(parents=True)
    before_output = snapshot(tree.output)

    report = tree.run()

    ifs_target = tree.state_path("ifs", cycle)
    assert report.refusal is InitRefusal.WRITE_FAILED
    assert report.written == (ifs_target,)  # 前序已落盘的**全部** source
    assert str(ifs_target) in report.detail
    assert CLEANUP_CLAIM in report.detail
    assert FRESH_CLAIM not in report.detail
    # 类一（`EEXIST`）盘上零残留：MUST NOT 声称目标可能被部分写入。
    assert PARTIAL_CLAIM not in report.detail
    # 已落盘文件仍在且内容不变；本函数不回滚删除。
    assert ifs_target.read_bytes() == expected_bytes(
        tree.payloads["ifs"], EPOCH_MINUTES_25_00Z
    )
    assert blocker.is_dir() and list(blocker.iterdir()) == []
    assert snapshot(tree.output) == before_output


def test_phase_b_write_order_is_ifs_then_gfs(tmp_path: Path) -> None:
    """裁决 5：写入序钉死为 `rawscan.SOURCES` 的迭代序，不依赖 dict/set 的偶然序。"""
    tree = Tree(tmp_path)
    cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    for source in WRITE_ORDER:
        tree.write_cycle(source, cycle)

    report = tree.run()

    assert [path.parent.name for path in report.written] == list(WRITE_ORDER)


def test_exclusive_write_refuses_a_target_planted_after_phase_a(
    tmp_path: Path, monkeypatch
) -> None:
    """裁决 5「写用 `O_EXCL` 而非 `atomic_write_bytes_no_follow`」唯一的判别构造。

    既有的「预置空目录」构造对**两个** helper 都失败（`os.replace` 落到目录同样报错），
    故它对这条选择毫无判别力。这里在**另一个** seam（`ensure_directory_no_follow`）上包
    一层：目录**真的**被建出来，同时在 gfs 目标路径植入一个带哨兵字节的**真实普通文件**。
    该文件在阶段 A 之后才出现，正是裁决 5 要 fail closed 的 TOCTOU 窗；`O_EXCL` 得
    `EEXIST` 而拒绝，覆盖写则会把哨兵字节抹掉。

    禁令「MUST NOT 用 monkeypatch 伪造写入失败」不覆盖本构造：写入结果没有被伪造，失败
    由真实的 `O_CREAT|O_EXCL` 在真实的普通文件上自然产生。
    """
    tree = Tree(tmp_path)
    cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    for source in WRITE_ORDER:
        tree.write_cycle(source, cycle)
    gfs_target = tree.state_path("gfs", cycle)
    sentinel = b"sentinel-planted-inside-the-toctou-window\n"
    real_ensure = safe_fs.ensure_directory_no_follow

    def planting_ensure(path: Path, **kwargs):
        created = real_ensure(path, **kwargs)
        if Path(path) == gfs_target.parent:
            gfs_target.write_bytes(sentinel)
        return created

    monkeypatch.setattr(safe_fs, "ensure_directory_no_follow", planting_ensure)

    report = tree.run()

    ifs_target = tree.state_path("ifs", cycle)
    assert report.refusal is InitRefusal.WRITE_FAILED
    assert report.written == (ifs_target,)
    # 哨兵逐字节不变：覆盖写的实现在这一行必红。
    assert gfs_target.read_bytes() == sentinel
    assert CLEANUP_CLAIM in report.detail
    assert PARTIAL_CLAIM not in report.detail


def test_write_failure_with_zero_landed_states_does_not_claim_cleanup(
    tmp_path: Path,
) -> None:
    """写入序**首位**（ifs）即失败 -> `written == ()`，收尾 MUST NOT 宣称需要清理。

    同时钉死 `detail` 的「列出**全部**前序已落盘 source」不是硬编码某一个源：既有构造只
    堵第二个源，`written` 恒为单元素，把 join 换成 `str(written[0])` 在那条用例上看不出
    区别，本行的空元组会让它直接 `IndexError`。
    """
    tree = Tree(tmp_path)
    cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    for source in WRITE_ORDER:
        tree.write_cycle(source, cycle)
    blocker = tree.state_path("ifs", cycle)
    blocker.mkdir(parents=True)  # 非普通文件：过得了阶段 A 守卫，挡得住 `O_EXCL`

    report = tree.run()

    assert report.refusal is InitRefusal.WRITE_FAILED
    assert report.written == ()
    assert "（无）" in report.detail
    assert FRESH_CLAIM in report.detail
    assert CLEANUP_CLAIM not in report.detail
    assert PARTIAL_CLAIM not in report.detail
    # 首位失败即整体停手：第二个源的目录从未被创建。
    assert not (tree.states / "gfs").exists()
    assert all_files(tree.states) == []


def test_unwritable_states_dir_reports_root_cause_without_cleanup_claim(
    tmp_path: Path,
) -> None:
    """阶段 B 首个 `ensure_directory_no_follow` 抛 `EACCES`（`chmod 0o500 states/`）。

    阶段 A 的枚举全过（`states/` 可读可搜索、且为空），阶段 B 的 `mkdir` 失败，终态是**真**
    零写入。`safe_fs` 把这次 `mkdir` 失败同样包成 `kind="io"`，故实现若把两次调用合进
    一个 `try`，就会对一个从未被 `os.open` 过的路径宣称「可能已被部分写入」。
    """
    skip_if_root()
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))
    original = tree.states.stat().st_mode
    tree.states.chmod(0o500)
    try:
        report = tree.run()
    finally:
        tree.states.chmod(original)

    assert report.refusal is InitRefusal.WRITE_FAILED
    assert report.written == ()
    assert FRESH_CLAIM in report.detail
    assert CLEANUP_CLAIM not in report.detail
    assert PARTIAL_CLAIM not in report.detail
    assert all_files(tree.states) == []


def test_mid_write_io_failure_names_the_possibly_partial_target(
    tmp_path: Path,
) -> None:
    """裁决 5 类二：`O_EXCL` 建成文件后 `os.write` 中途真实失败 -> 收尾点名半写产物。

    构造用真实的 `RLIMIT_FSIZE`（4096 字节）而非注入 fake：`safe_fs` 的
    `write_bytes_no_follow_exclusive` 失败路径**不 unlink**，盘上因此留下一份 header 合法、
    body 截断的普通文件。它既不在 `written` 内、也不在「已落盘的首态」列表内，运维照类一
    的话术清理会漏掉它。

    ifs 的率定末态小于上限（先正常落盘），gfs 的远大于上限：`written` 因此非空，
    「该目标不在已落盘列表内」这条断言才有判别力，而不是在空元组上恒真。
    """
    resource = pytest.importorskip("resource")
    import signal

    limit = 4096
    tree = Tree(tmp_path, payloads={"ifs": default_payload(), "gfs": large_payload()})
    assert len(tree.payloads["ifs"]) < limit < len(tree.payloads["gfs"])
    cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    for source in WRITE_ORDER:
        tree.write_cycle(source, cycle)

    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    previous_handler = signal.getsignal(signal.SIGXFSZ)
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, hard))
        try:
            report = tree.run()
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
    finally:
        signal.signal(signal.SIGXFSZ, previous_handler)

    ifs_target = tree.state_path("ifs", cycle)
    gfs_target = tree.state_path("gfs", cycle)
    assert report.refusal is InitRefusal.WRITE_FAILED
    assert report.written == (ifs_target,)
    assert gfs_target not in report.written
    assert PARTIAL_CLAIM in report.detail
    assert str(gfs_target) in report.detail
    # 「已落盘的首态」列表只列 ifs——半写的 gfs 目标由上一条话术单独点名。
    landed = report.detail.split("已落盘的首态：")[1]
    assert str(ifs_target) in landed
    assert str(gfs_target) not in landed
    # 真实的半写产物：文件存在、且短于完整字节。
    assert gfs_target.is_file()
    assert 0 < gfs_target.stat().st_size < len(tree.payloads["gfs"])


# --- 负面证据：不跑 SHUD、不写 DONE、不碰 output/ ---------------------------


def test_init_module_has_no_subprocess_surface() -> None:
    """裁决 9 的可断言负面证据：模块内不存在任何进程派生面（MUST NOT 运行 SHUD）。

    判据走 AST 而非文本子串：模块头本身要谈论「不跑 SHUD」，文本探测会被自己的文档打红。
    """
    import ast

    tree = ast.parse(Path(init.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                called.add(target.attr)
            elif isinstance(target, ast.Name):
                called.add(target.id)

    assert imported & {"subprocess", "multiprocessing", "shutil", "pty"} == set()
    forbidden = {
        name
        for name in called
        if name.startswith(("exec", "spawn", "fork", "posix_spawn"))
        or name in {"system", "popen", "run"}
    }
    assert forbidden == set()


def test_success_writes_no_done_and_leaves_output_untouched(tmp_path: Path) -> None:
    """成功路径同样不写任何 `DONE`、不新建 `output/` 下的任何条目。"""
    tree = Tree(tmp_path)
    (tree.output / "2026082400" / "gfs").mkdir(parents=True)
    before_output = snapshot(tree.output)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))

    report = tree.run()

    assert report.refusal is None
    assert snapshot(tree.output) == before_output
    assert list(tree.output.rglob("DONE")) == []


def test_non_done_regular_file_under_output_does_not_block_init(
    tmp_path: Path,
) -> None:
    """裁决 8 的两侧**不对称**：`output/` 侧只认名为 `DONE` 的普通文件。

    `output/<cycle>/gfs/yd.rivqdown.dat` 是一次崩溃发布留下的最常见残留。把守卫的
    `name=DONE_NAME` 去掉的实现在本行必红，而放宽后的守卫会让任何带残留的全新根永久无法
    建链，与「无 DONE 残留必须可干净重跑」直接冲突。`states/` 侧的「认任一普通文件」由
    `test_residual_file_with_unparsable_name_still_refuses` 把守，两条合起来才钉死不对称。
    """
    tree = Tree(tmp_path)
    residue = tree.output / "2026082400" / "gfs" / "yd.rivqdown.dat"
    residue.parent.mkdir(parents=True)
    residue.write_bytes(b"stale product\n")
    before_output = snapshot(tree.output)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))

    report = tree.run()

    assert report.refusal is None
    assert len(report.written) == len(WRITE_ORDER)
    assert snapshot(tree.output) == before_output


def test_bootstrap_is_not_idempotent_and_refuses_on_second_run(
    tmp_path: Path,
) -> None:
    """幂等边界：init **非幂等**且刻意如此——第二次执行必然 `STATES_NOT_EMPTY`。"""
    tree = Tree(tmp_path)
    for source in WRITE_ORDER:
        tree.write_cycle(source, datetime(2026, 8, 25, 0, tzinfo=UTC))

    first = tree.run()
    before_states = snapshot(tree.states)
    second = tree.run()

    assert first.refusal is None
    assert second.refusal is InitRefusal.STATES_NOT_EMPTY
    assert snapshot(tree.states) == before_states


# --- 跨 issue 兼容：#22 的前沿函数读 init 写出的首态 ------------------------


@pytest.mark.parametrize("source", WRITE_ORDER)
def test_decide_frontier_accepts_the_state_init_writes(
    tmp_path: Path, source: str
) -> None:
    """回归行 20：未改动的 `controller.decide_frontier` 判「全新链、待跑 T = 该文件名」。

    这是跨 issue 的端到端兼容性回归：#22 的 header 判据是**绝对**时间，重戳写错即
    `HEADER_TIME_MISMATCH`（断链的入口）。
    """
    tree = Tree(tmp_path)
    cycle = datetime(2026, 8, 25, 0, tzinfo=UTC)
    for name in WRITE_ORDER:
        tree.write_cycle(name, cycle)

    report = tree.run()
    assert report.refusal is None

    decision = controller.decide_frontier(
        yd_root=tree.yd_root, source=source, raw_complete=lambda _cycle: True
    )

    assert decision.stop_reason is None
    assert decision.cycle == cycle
    assert decision.cycle.strftime(CYCLE_DIR_FORMAT) + STATE_SUFFIX == (
        tree.state_path(source, cycle).name
    )


# --- CLI 入口：经**真实** bootstrap 走通一次成功与一次拒绝 ------------------
#
# MUST NOT 用 fake 替换 `bootstrap`：否则 spec 的 MUST（拒绝退出码、落盘路径可见）没有
# 任何用例把守。本 lane 用 `cli_fixtures` 的真实 TOML，`now` 由 `cli.init()` 取
# `datetime.now(UTC)`，故 raw 树按**真实当前时刻**铺。

CLI_LEADS = {"ifs": (0, 3, 6), "gfs": (0, 6, 12)}
CLI_BUNDLES = {"ifs": "fixture-ifs-{lead}.grib2", "gfs": "fixture-gfs-{lead}.grib2"}


def _recent_cycle() -> datetime:
    """一个恒在扫描窗内、且恒 `<= now` 的 00Z/12Z cycle：`now - 24h` 向下取到 12 小时网格。"""
    moment = datetime.now(UTC) - timedelta(hours=24)
    return moment.replace(
        hour=0 if moment.hour < 12 else 12, minute=0, second=0, microsecond=0
    )


def _write_cli_raw(raw_root: Path, cycle: datetime) -> None:
    for source, leads in CLI_LEADS.items():
        base = raw_root / DIR_SEGMENTS[source] / cycle.strftime(CYCLE_DIR_FORMAT)
        base.mkdir(parents=True, exist_ok=True)
        for lead in leads:
            (base / CLI_BUNDLES[source].format(lead=lead)).write_bytes(
                b"GRIB\xff\x00stub"
            )


def _write_cli_root(tmp_path: Path) -> tuple[Path, list[str]]:
    """铺一棵能让 `yd-producer init` 成功的根，返回 `(yd_root, argv)`。"""
    from cli_fixtures import write_config, write_local

    root = tmp_path.resolve()
    yd_root = root / "yd"
    for source in WRITE_ORDER:
        variant = yd_root / DEFAULT_VARIANTS[source]
        variant.mkdir(parents=True)
        (variant / f"yd_{source}{STATE_SUFFIX}").write_bytes(default_payload())
    (yd_root / "output").mkdir(parents=True)
    config_path = write_config(tmp_path)
    local_path = write_local(tmp_path)
    return yd_root, ["init", "--config", str(config_path), "--local", str(local_path)]


def test_cli_init_success_prints_written_paths_and_unblocks_run(
    capsys, tmp_path: Path
) -> None:
    """回归行 21：`cli.main(["init", ...])` 成功 -> 退出码 0；此后 `run` 的守卫不再拒绝。"""
    from yd_producer import cli

    yd_root, argv = _write_cli_root(tmp_path)
    cycle = _recent_cycle()
    _write_cli_raw(Path(tmp_path.resolve() / "nwm" / "raw"), cycle)

    assert cli.main(argv, env={}) == 0

    out = capsys.readouterr().out
    name = cycle.strftime(CYCLE_DIR_FORMAT) + STATE_SUFFIX
    for source in WRITE_ORDER:
        target = yd_root / "states" / source / name
        assert target.is_file()
        assert str(target) in out

    # `run` 的 `_check_states_dir`（#3，未改动）在 init 之后不再拒绝：退出码不再是
    # `EXIT_GUARD`，而是 `run` 自己的分阶段未实现。
    run_argv = ["run"] + argv[1:]
    assert cli.main(run_argv, env={}) == 3
    assert "状态目录" not in capsys.readouterr().err


def test_cli_init_refusal_exits_guard_with_reason_on_stderr(
    capsys, tmp_path: Path
) -> None:
    """回归行 21 的拒绝侧：raw 窗内无完整 cycle -> `EXIT_GUARD` 且 stderr 带闭合词表项。"""
    from yd_producer import cli

    yd_root, argv = _write_cli_root(tmp_path)

    assert cli.main(argv, env={}) == cli.EXIT_GUARD

    err = capsys.readouterr().err
    assert InitRefusal.NO_COMPLETE_RAW_CYCLE.value in err
    assert not (yd_root / "states").exists() or all_files(yd_root / "states") == []


def test_cli_init_turns_a_judge_config_error_into_exit_one(
    capsys, tmp_path: Path
) -> None:
    """回归行 11 的 CLI 侧：`judge` 的 `ConfigError` 经 `cli.main` 转成退出码 `1`，零写入。

    ifs 的 raw **必须铺完整**：ifs 先被判定，缺它就会先命中 `NO_COMPLETE_RAW_CYCLE`，
    gfs 的模式碰撞永远不会被行使——那样的用例是假绿。
    """
    from cli_fixtures import render_config, write_local

    from yd_producer import cli

    root = tmp_path.resolve()
    yd_root = root / "yd"
    for source in WRITE_ORDER:
        variant = yd_root / DEFAULT_VARIANTS[source]
        variant.mkdir(parents=True)
        (variant / f"yd_{source}{STATE_SUFFIX}").write_bytes(default_payload())
    (yd_root / "output").mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        render_config().replace(
            'bundles = ["fixture-gfs-{lead}.grib2"]',
            'bundles = ["fixture-gfs-{lead}.grib2", "fixture-gfs-{lead}.grib2"]',
        ),
        encoding="utf-8",
    )
    local_path = write_local(tmp_path)
    cycle = _recent_cycle()
    base = root / "nwm" / "raw" / DIR_SEGMENTS["ifs"] / cycle.strftime(CYCLE_DIR_FORMAT)
    base.mkdir(parents=True)
    for lead in CLI_LEADS["ifs"]:
        (base / CLI_BUNDLES["ifs"].format(lead=lead)).write_bytes(b"GRIB\xff\x00stub")

    argv = ["init", "--config", str(config_path), "--local", str(local_path)]
    assert cli.main(argv, env={}) == cli.EXIT_GUARD

    err = capsys.readouterr().err
    assert "raw.gfs.bundles" in err
    # 配置错误 MUST NOT 被伪装成「等 raw 补齐」。
    assert InitRefusal.NO_COMPLETE_RAW_CYCLE.value not in err
    assert not (yd_root / "states").exists()


def test_cli_init_turns_an_out_of_range_cycle_hour_into_a_clean_refusal(
    capsys, tmp_path: Path
) -> None:
    """spec「非法小时不得以未分类异常逃逸」的 **CLI 侧**：无 traceback、零写入。

    `config` 的 TOML 加载只校验「是整数列表」（`_require_int_list`），`hours = [0, 25]`
    因此真的进得来；`_candidate_cycles` 里 `datetime(..., hour=25)` 的裸 `ValueError`
    接不住于 `cli.main` 的 `except ConfigError`，异常会穿透出入口。本行在**入口**这一层
    把该 MUST 钉死——spec 的 WHEN 说的就是 CLI。
    """
    from cli_fixtures import render_config, write_local

    from yd_producer import cli

    root = tmp_path.resolve()
    yd_root = root / "yd"
    for source in WRITE_ORDER:
        variant = yd_root / DEFAULT_VARIANTS[source]
        variant.mkdir(parents=True)
        (variant / f"yd_{source}{STATE_SUFFIX}").write_bytes(default_payload())
    (yd_root / "output").mkdir(parents=True)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        render_config().replace("hours = [0, 12]", "hours = [0, 25]"),
        encoding="utf-8",
    )
    local_path = write_local(tmp_path)
    _write_cli_raw(root / "nwm" / "raw", _recent_cycle())

    argv = ["init", "--config", str(config_path), "--local", str(local_path)]
    assert cli.main(argv, env={}) == cli.EXIT_GUARD

    err = capsys.readouterr().err
    assert "cycle.hours" in err
    assert "Traceback" not in err
    assert not (yd_root / "states").exists()
