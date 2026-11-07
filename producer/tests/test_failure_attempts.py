r"""失败重试上限（issue #370；compute-loop §11.3；run-controller「失败处理」）。

oracle 纪律：header 字节取自 `cleanup_fixtures` / `controller_sources_fixtures` 的手写
字面量；计数期望由构造期写入的记录条数给出；停源 detail 由规格文本逐字拼出。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from cleanup_fixtures import (
    FAILED_HEADER,
    FAILED_LOG_BYTES,
    TIMEOUT_HEADER,
    TIMEOUT_LOG_BYTES,
    T,
    _inputs,
    _make_work,
    _seed_states_and_output,
    _skip_if_root,
    _unreadable,
    _work_root,
    _write_log,
    _yd_root,
)
from controller_sources_fixtures import (
    CYCLE_T,
    CYCLE_T12,
    IFS_EXIT,
    IFS_RAW_LOG,
    SOURCES,
    T_TEXT,
    BarrierExecutor,
    DualBarrier,
    FailureLogHook,
    RecordingProvider,
    TerminalHookGate,
    cycle_outcomes,
    done_path,
    failed_header,
    failure_log_path,
    fake_for,
    noop_wait,
    plant_unknown_work,
    require_source_tuple,
    success_driver,
    success_hook,
    work_dir,
    write_dual_tree,
)
from frontier_fixtures import parse_cycle, snapshot_tree

import yd_producer.rawscan as rawscan_module
from yd_producer import _failure_attempts as attempts
from yd_producer import cleanup
from yd_producer.controller import RunOutcome, StopReason, run_sources
from yd_producer.executor import JobState

ONE_RECORD = FAILED_HEADER + FAILED_LOG_BYTES
UNDETERMINABLE = "失败日志不可判定："


def _count(root: Path, source: str = "ifs", cycle: str = T) -> int:
    return attempts.count_failure_attempts(root, source, parse_cycle(cycle))


# --- 2.1 / 2.2：计数函数 -----------------------------------------------------------


def test_limit_and_schema_are_the_versioned_constants() -> None:
    assert attempts.FAILURE_ATTEMPT_LIMIT == 3
    assert attempts.FAILURE_LOG_MAX_BYTES == 64 * 1024 * 1024
    assert attempts.FAILURE_LOG_SCHEMA == "yd-failure-log-v1"
    assert cleanup.FAILURE_LOG_SCHEMA is attempts.FAILURE_LOG_SCHEMA


@pytest.mark.parametrize("missing", ["logs", "source-dir", "file"])
def test_absent_log_counts_zero(tmp_path: Path, missing: str) -> None:
    root = _yd_root(tmp_path)
    if missing != "logs":
        (root / "logs").mkdir()
    if missing == "file":
        (root / "logs" / "ifs").mkdir()
        _write_log(root, "ifs", "2026082600", ONE_RECORD)
    assert _count(root) == 0


@pytest.mark.parametrize("records", [1, 2, 3, 4])
def test_each_header_line_counts_once(tmp_path: Path, records: int) -> None:
    root = _yd_root(tmp_path)
    _write_log(root, "ifs", T, ONE_RECORD * records)
    assert _count(root) == records


@pytest.mark.parametrize(
    "payload",
    [FAILED_HEADER + FAILED_LOG_BYTES, TIMEOUT_HEADER + TIMEOUT_LOG_BYTES],
    ids=["legacy-failed-non-utf8", "legacy-timeout-no-trailing-newline"],
)
def test_legacy_single_record_log_counts_one(tmp_path: Path, payload: bytes) -> None:
    root = _yd_root(tmp_path)
    _write_log(root, "ifs", T, payload)
    assert _count(root) == 1


def test_non_header_json_and_undecodable_lines_are_not_counted(
    tmp_path: Path,
) -> None:
    noise = b"".join(
        [
            b'{"schema":"yd-failure-log-v2"}\n',
            b'{"note":"schema yd-failure-log-v1"}\n',
            b'{"nested":{"schema":"yd-failure-log-v1"}}\n',
            b'["yd-failure-log-v1"]\n',
            b'"yd-failure-log-v1"\n',
            b'{"schema":"yd-failure-log-v1"\n',
            b'{"schema":"yd-failure-log-v1"} trailing\n',
            b'prefix {"schema":"yd-failure-log-v1"}\n',
            b'{"schema":"yd-failure-log-v1","x":"\xff"}\n',
            b"\xff\xfe\x00\n",
            b"{" * 100_000 + b"\n",
        ]
    )
    root = _yd_root(tmp_path)
    _write_log(root, "ifs", T, FAILED_HEADER + noise + FAILED_LOG_BYTES)
    assert _count(root) == 1


def _plant_symlink(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.log"
    outside.write_bytes(b"no headers here\n")
    (root / "logs" / "ifs").mkdir(parents=True)
    (root / "logs" / "ifs" / f"{T}.log").symlink_to(outside)


def _plant_dangling(root: Path, tmp_path: Path) -> None:
    (root / "logs" / "ifs").mkdir(parents=True)
    (root / "logs" / "ifs" / f"{T}.log").symlink_to(tmp_path / "missing.log")


def _plant_directory(root: Path, tmp_path: Path) -> None:
    (root / "logs" / "ifs" / f"{T}.log").mkdir(parents=True)


def _plant_parent_symlink(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside-logs"
    outside.mkdir()
    (outside / f"{T}.log").write_bytes(b"no headers here\n")
    (root / "logs").mkdir()
    (root / "logs" / "ifs").symlink_to(outside)


@pytest.mark.parametrize(
    ("plant", "reason"),
    [
        (_plant_symlink, "symlink"),
        (_plant_dangling, "symlink"),
        (_plant_directory, "不是普通文件"),
        (_plant_parent_symlink, "无法安全读取"),
    ],
    ids=["symlink", "dangling-symlink", "directory", "parent-symlink"],
)
def test_unsafe_log_shapes_are_undeterminable(
    tmp_path: Path, plant, reason: str
) -> None:
    base = tmp_path.resolve()
    root = _yd_root(base)
    plant(root, base)
    with pytest.raises(attempts.FailureLogUndeterminable) as info:
        _count(root)
    assert reason in info.value.reason
    assert info.value.path == root / "logs" / "ifs" / f"{T}.log"


def test_byte_bound_is_inclusive_and_over_bound_is_undeterminable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _yd_root(tmp_path)
    _write_log(root, "ifs", T, ONE_RECORD)
    monkeypatch.setattr(attempts, "FAILURE_LOG_MAX_BYTES", len(ONE_RECORD))
    assert _count(root) == 1
    monkeypatch.setattr(attempts, "FAILURE_LOG_MAX_BYTES", len(ONE_RECORD) - 1)
    with pytest.raises(attempts.FailureLogUndeterminable) as info:
        _count(root)
    assert "超出字节上界" in info.value.reason


def test_unreadable_log_is_undeterminable(tmp_path: Path) -> None:
    _skip_if_root()
    root = _yd_root(tmp_path)
    log = _write_log(root, "ifs", T, ONE_RECORD)
    with _unreadable(log), pytest.raises(attempts.FailureLogUndeterminable) as info:
        _count(root)
    assert "无法安全读取" in info.value.reason


# --- 2.4 / 2.5：失败收尾追加写 ------------------------------------------------------


def _finalize(tmp_path: Path, root: Path, scene: str = "a") -> Path:
    work_root = _work_root(tmp_path / scene)
    work_dir, merged = _make_work(work_root)
    cleanup.finalize_failed_job(_inputs(root, work_root, work_dir, merged))
    assert not work_dir.exists()
    return root / "logs" / "ifs" / f"{T}.log"


@pytest.mark.parametrize(
    ("previous", "joiner"),
    [
        (FAILED_HEADER + FAILED_LOG_BYTES, b""),
        (TIMEOUT_HEADER + TIMEOUT_LOG_BYTES, b"\n"),
    ],
    ids=["trailing-newline", "no-trailing-newline"],
)
def test_append_keeps_old_log_as_byte_exact_prefix(
    tmp_path: Path, previous: bytes, joiner: bytes
) -> None:
    root = _yd_root(tmp_path)
    _seed_states_and_output(root)
    _write_log(root, "ifs", T, previous)
    assert _count(root) == 1

    log = _finalize(tmp_path, root)

    assert log.read_bytes() == previous + joiner + FAILED_HEADER + FAILED_LOG_BYTES
    assert log.read_bytes().startswith(previous)
    assert _count(root) == 2
    assert sorted(path.name for path in log.parent.iterdir()) == [f"{T}.log"]


def test_three_finalized_failures_count_three(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    _seed_states_and_output(root)
    for index, scene in enumerate(("a", "b", "c"), start=1):
        log = _finalize(tmp_path, root, scene)
        assert _count(root) == index
    assert log.read_bytes() == ONE_RECORD * 3


def test_append_over_bound_old_log_fails_commit_and_keeps_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _yd_root(tmp_path)
    _seed_states_and_output(root)
    old = _write_log(root, "ifs", T, ONE_RECORD)
    work_root = _work_root(tmp_path)
    work_dir, merged = _make_work(work_root)
    before_logs = snapshot_tree(root / "logs")
    before_work = snapshot_tree(work_root)
    monkeypatch.setattr(attempts, "FAILURE_LOG_MAX_BYTES", len(ONE_RECORD) - 1)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.finalize_failed_job(_inputs(root, work_root, work_dir, merged))

    assert type(info.value) is cleanup.CleanupError
    assert info.value.phase == "log"
    assert info.value.path == old
    assert "超出字节上界" in str(info.value)
    assert snapshot_tree(root / "logs") == before_logs
    assert snapshot_tree(work_root) == before_work
    assert old.read_bytes() == ONE_RECORD


def test_retention_plan_still_lists_an_appended_log(tmp_path: Path) -> None:
    from frontier_fixtures import YdRootBuilder

    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    old_cycle = "2026081000"
    for cycle in (old_cycle, "2026082600"):
        builder.write_done(cycle, "ifs")
        builder.write_output_dat(cycle, "ifs")
    log = _write_log(root, "ifs", old_cycle, ONE_RECORD * 2 + TIMEOUT_LOG_BYTES)
    plan = cleanup.plan_retention(root, "ifs")
    assert plan.log_files == (log,)


# --- 2.3 / 2.5 / 2.7：控制器计数门（run_sources seam）-------------------------------


def _run(config, local, monkeypatch, *, ifs_state=JobState.SUCCEEDED):
    judged: list[str] = []
    original = rawscan_module.judge

    def spying(raw_root, source, cycle, config):
        judged.append(source)
        return original(raw_root, source, cycle, config)

    monkeypatch.setattr(rawscan_module, "judge", spying)
    gate = TerminalHookGate()
    barrier = DualBarrier()
    executors = {}
    drivers = {}
    for source in SOURCES:
        driver, state, slot = success_driver()
        failing = source == "ifs" and ifs_state is not JobState.SUCCEEDED
        hook = (
            FailureLogHook(local, "ifs", IFS_RAW_LOG)
            if failing
            else success_hook(slot, state, gate=gate)
        )
        drivers[source] = driver
        executors[source] = BarrierExecutor(
            fake_for(source, state=ifs_state if failing else JobState.SUCCEEDED),
            source=source,
            barrier=barrier,
            hook=hook,
            wait_for_peer=False,
        )
    report = run_sources(
        config=config,
        local=local,
        executors=executors,
        drivers=drivers,
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes={
            "ifs": RecordingProvider("ifs", IFS_EXIT),
            "gfs": RecordingProvider("gfs", "9:1"),
        },
    )
    return report, executors, judged


def _seed_controller_log(local, payload: bytes) -> Path:
    path = failure_log_path(local, "ifs")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _assert_gfs_published(report, executors) -> None:
    gfs = require_source_tuple(report.gfs, "gfs")
    assert cycle_outcomes(gfs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert len(executors["gfs"].submissions) == 1


def _assert_ifs_stopped(report, executors, judged, local):
    ifs = require_source_tuple(report.ifs, "ifs")
    assert len(ifs) == 1
    stopped = ifs[0]
    assert stopped.cycle == CYCLE_T
    assert stopped.outcome is RunOutcome.STOPPED
    assert stopped.stop_reason is StopReason.RETRY_EXHAUSTED
    assert stopped.job is None and stopped.published is None
    assert executors["ifs"].submissions == ()
    assert "ifs" not in judged
    source_work = work_dir(local, "ifs").parent
    assert not source_work.exists() or not any(source_work.iterdir())
    assert not done_path(local, "ifs").exists()
    return stopped


@pytest.mark.parametrize("records", [0, 1, 2])
def test_below_limit_submits_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, records: int
) -> None:
    config, local = write_dual_tree(tmp_path)
    if records:
        log = _seed_controller_log(local, ONE_RECORD * records)
    report, executors, judged = _run(config, local, monkeypatch)
    ifs = require_source_tuple(report.ifs, "ifs")
    assert cycle_outcomes(ifs) == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert ifs[-1].stop_reason is StopReason.RAW_INCOMPLETE
    assert len(executors["ifs"].submissions) == 1
    assert judged.count("ifs") == 2
    assert done_path(local, "ifs").is_file()
    if records:
        assert log.read_bytes() == ONE_RECORD * records
    _assert_gfs_published(report, executors)


@pytest.mark.parametrize("records", [3, 4])
def test_limit_reached_stops_source_with_zero_staging_and_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, records: int
) -> None:
    config, local = write_dual_tree(tmp_path)
    log = _seed_controller_log(local, ONE_RECORD * records)
    report, executors, judged = _run(config, local, monkeypatch)
    stopped = _assert_ifs_stopped(report, executors, judged, local)
    _assert_gfs_published(report, executors)
    assert done_path(local, "gfs").is_file()
    assert log.is_absolute()
    assert stopped.detail == (
        f"ifs: 待跑 T={T_TEXT}；同一 cycle 已失败 {records} 次（上限 3），"
        f"停源待人工；确认根因后把 {log} 移出 logs/ifs/ 再等下一 tick"
    )
    assert log.read_bytes() == ONE_RECORD * records


@pytest.mark.parametrize(
    "shape", ["symlink", "directory", "over-bound"], ids=lambda shape: shape
)
def test_undeterminable_log_stops_source_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    config, local = write_dual_tree(tmp_path)
    log = failure_log_path(local, "ifs")
    log.parent.mkdir(parents=True)
    if shape == "symlink":
        outside = tmp_path.resolve() / "outside.log"
        outside.write_bytes(b"zero headers: following would submit\n")
        log.symlink_to(outside)
        reason = "symlink"
    elif shape == "directory":
        log.mkdir()
        reason = "不是普通文件"
    else:
        log.write_bytes(ONE_RECORD)
        monkeypatch.setattr(attempts, "FAILURE_LOG_MAX_BYTES", 16)
        reason = "超出字节上界"
    report, executors, judged = _run(config, local, monkeypatch)
    stopped = _assert_ifs_stopped(report, executors, judged, local)
    _assert_gfs_published(report, executors)
    assert done_path(local, "gfs").is_file()
    assert stopped.detail.startswith(f"ifs: 待跑 T={T_TEXT}；{UNDETERMINABLE}")
    assert reason in stopped.detail
    assert str(log) in stopped.detail


def test_unverified_work_residue_takes_priority_over_retry_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    _seed_controller_log(local, ONE_RECORD * 3)
    plant_unknown_work(local, "ifs", shape="dir")
    report, executors, judged = _run(config, local, monkeypatch)
    ifs = require_source_tuple(report.ifs, "ifs")
    assert [item.stop_reason for item in ifs] == [StopReason.UNVERIFIED_WORK_RESIDUE]
    assert executors["ifs"].submissions == ()
    assert "ifs" not in judged


def test_repeated_failures_append_then_stop_then_release_by_moving_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非 UTF-8、无尾换行的旧单记录日志：收尾前计 1 照常提交，之后逐次追加到 3 停源；
    人工把日志移出 `logs/ifs/` 后下一 tick 从 0 计数重跑。"""
    config, local = write_dual_tree(tmp_path, sources=("ifs",))
    log = _seed_controller_log(local, TIMEOUT_HEADER + TIMEOUT_LOG_BYTES)
    root = Path(local.yd_root)
    assert _count(root, cycle=T_TEXT) == 1
    header = failed_header(
        shud_binary=local.shud_binary, source="ifs", exit_code=IFS_EXIT
    )
    expected = TIMEOUT_HEADER + TIMEOUT_LOG_BYTES + b"\n"
    for attempt in (2, 3):
        report, executors, _ = _run(
            config, local, monkeypatch, ifs_state=JobState.FAILED
        )
        ifs = require_source_tuple(report.ifs, "ifs")
        assert cycle_outcomes(ifs) == [(CYCLE_T, RunOutcome.JOB_FAILED)]
        assert len(executors["ifs"].submissions) == 1
        expected += header + IFS_RAW_LOG
        assert log.read_bytes() == expected
        assert _count(root, cycle=T_TEXT) == attempt
        assert not work_dir(local, "ifs").exists()

    report, executors, judged = _run(
        config, local, monkeypatch, ifs_state=JobState.FAILED
    )
    _assert_ifs_stopped(report, executors, judged, local)
    assert log.read_bytes() == expected

    receipt = tmp_path.resolve() / "receipt"
    receipt.mkdir()
    shutil.move(log, receipt / log.name)
    report, executors, _ = _run(config, local, monkeypatch)
    assert cycle_outcomes(require_source_tuple(report.ifs, "ifs"))[0] == (
        CYCLE_T,
        RunOutcome.SUCCEEDED,
    )
    assert len(executors["ifs"].submissions) == 1
    assert (receipt / log.name).read_bytes() == expected
