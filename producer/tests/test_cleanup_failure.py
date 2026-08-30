r"""`yd_producer.cleanup` 失败收尾行为测试（任务 13.2，issue #25）。

oracle 纪律：失败日志 JSON 用手写字面量；删除集合由构造期登记的 cycle/路径给出，
不由被测模块回读。全部断言落在真实 tmp 目录树上。
"""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

import pytest
from cleanup_fixtures import (
    ENDED,
    FAILED_HEADER,
    FAILED_LOG_BYTES,
    OFF_CLOCK,
    RESOURCES,
    SIBLING,
    SOURCE,
    STARTED,
    SUBMITTED,
    TIMEOUT_HEADER,
    TIMEOUT_LOG_BYTES,
    D,
    T,
    _assert_not_leaked,
    _assert_wrapped,
    _base,
    _failed_record,
    _inputs,
    _make_work,
    _seed_states_and_output,
    _skip_if_root,
    _spec,
    _timeout_record,
    _work_root,
    _write_log,
    _yd_root,
)
from frontier_fixtures import parse_cycle, snapshot_tree

from yd_producer import cleanup, controller, residue
from yd_producer.executor import JobRecord, JobState

# --- 公开符号 ---


def test_public_symbols_are_exactly_the_seven_named_seams() -> None:
    assert cleanup.__all__ == [
        "CleanupError",
        "FailureInputs",
        "FailureResult",
        "RetentionPlan",
        "execute_retention_plan",
        "finalize_failed_job",
        "plan_retention",
    ]


# --- spec「失败轮产物」 ---


@pytest.mark.parametrize(
    ("record", "exit_code", "raw", "header"),
    [
        (_failed_record(), "1:0", FAILED_LOG_BYTES, FAILED_HEADER),
        (_timeout_record(), "0:9", TIMEOUT_LOG_BYTES, TIMEOUT_HEADER),
    ],
    ids=["FAILED", "TIMEOUT"],
)
def test_failed_cycle_leaves_one_log_and_does_not_move_the_state_chain(
    tmp_path: Path,
    record: JobRecord,
    exit_code: str,
    raw: bytes,
    header: bytes,
) -> None:
    root = _yd_root(tmp_path)
    builder = _seed_states_and_output(root)
    work_root = _work_root(tmp_path)
    work_dir, log_path = _make_work(work_root, log_bytes=raw)
    sibling_work = work_root / SIBLING / T
    sibling_work.mkdir(parents=True)
    (sibling_work / "keep.txt").write_text("gfs\n", encoding="utf-8")
    older_work = work_root / SOURCE / D
    older_work.mkdir(parents=True)
    (older_work / "keep.txt").write_text("older\n", encoding="utf-8")
    outside = _base(tmp_path) / "outside"
    outside.mkdir()
    (outside / "raw.dat").write_text("nwm\n", encoding="utf-8")
    before_output = snapshot_tree(root / "output")
    before_states = snapshot_tree(root / "states")

    result = cleanup.finalize_failed_job(
        _inputs(root, work_root, work_dir, log_path, record=record, exit_code=exit_code)
    )

    log = root / "logs" / SOURCE / f"{T}.log"
    assert result.log_path == log
    assert result.removed_work_dir == work_dir
    assert result.job_id == record.job_id
    assert log.read_bytes() == header + raw
    assert list(log.parent.iterdir()) == [log]
    assert not work_dir.exists()
    assert (sibling_work / "keep.txt").read_text(encoding="utf-8") == "gfs\n"
    assert (older_work / "keep.txt").read_text(encoding="utf-8") == "older\n"
    assert snapshot_tree(root / "output") == before_output
    assert snapshot_tree(root / "states") == before_states
    assert not (root / "output" / T / SOURCE / "DONE").exists()
    assert not list(root.rglob("status.json"))
    decision = controller.decide_frontier(
        yd_root=root, source=SOURCE, raw_complete=lambda _cycle: True
    )
    assert decision.cycle == parse_cycle(T)
    plan = residue.plan_residue(yd_root=root, source=SOURCE, decision=decision)
    assert plan is not None
    assert plan.empty
    assert builder.source_output_dir(D, SOURCE).joinpath("DONE").is_file()


def test_repeat_failure_on_the_same_cycle_atomically_replaces_the_old_log(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    _seed_states_and_output(root)
    old = _write_log(root, SOURCE, T, b"previous job\n")
    work_root = _work_root(tmp_path)
    work_dir, log_path = _make_work(work_root)
    before_output = snapshot_tree(root / "output")
    before_states = snapshot_tree(root / "states")

    cleanup.finalize_failed_job(_inputs(root, work_root, work_dir, log_path))

    log_dir = root / "logs" / SOURCE
    names = sorted(path.name for path in log_dir.iterdir())
    assert names == [f"{T}.log"]
    assert not any(name.endswith(".tmp") or ".tmp." in name for name in names)
    assert (log_dir / f"{T}.log").read_bytes() == FAILED_HEADER + FAILED_LOG_BYTES
    assert old.read_bytes() == FAILED_HEADER + FAILED_LOG_BYTES
    assert snapshot_tree(root / "output") == before_output
    assert snapshot_tree(root / "states") == before_states


@pytest.mark.parametrize("shape", ["symlink", "directory"])
def test_log_commit_failure_keeps_work_and_the_state_chain(
    tmp_path: Path, shape: str
) -> None:
    root = _yd_root(tmp_path)
    _seed_states_and_output(root)
    work_root = _work_root(tmp_path)
    work_dir, log_path = _make_work(work_root)
    outside = _base(tmp_path) / "outside"
    outside.mkdir()
    target = outside / "stolen.log"
    target.write_bytes(b"outside\n")
    log_dir = root / "logs" / SOURCE
    log_dir.mkdir(parents=True)
    occupied = log_dir / f"{T}.log"
    if shape == "symlink":
        occupied.symlink_to(target)
    else:
        occupied.mkdir()
        (occupied / "inside.txt").write_text("nope\n", encoding="utf-8")
    before_root = snapshot_tree(root)
    before_work = snapshot_tree(work_root)

    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.finalize_failed_job(_inputs(root, work_root, work_dir, log_path))

    _assert_not_leaked(info.value)
    assert info.value.phase == "log"
    assert info.value.path == occupied
    assert snapshot_tree(root) == before_root
    assert snapshot_tree(work_root) == before_work
    assert work_dir.is_dir()
    assert target.read_bytes() == b"outside\n"


def test_work_tree_symlink_is_unlinked_without_following_the_target(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    _seed_states_and_output(root)
    work_root = _work_root(tmp_path)
    work_dir, log_path = _make_work(work_root)
    outside = _base(tmp_path) / "outside"
    outside.mkdir()
    target = outside / "raw-original.dat"
    payload = b"NWM raw original\n"
    target.write_bytes(payload)
    (work_dir / "linked.dat").symlink_to(target)

    cleanup.finalize_failed_job(_inputs(root, work_root, work_dir, log_path))

    assert not work_dir.exists()
    assert target.read_bytes() == payload


@pytest.mark.parametrize(
    "make_wrong",
    [
        lambda work_root: work_root / SOURCE,
        lambda work_root: work_root / SOURCE / T / "nested",
        lambda work_root: work_root / SIBLING / T,
        lambda work_root: work_root / SOURCE / D,
    ],
    ids=["missing-cycle", "extra-level", "sibling-source", "sibling-cycle"],
)
def test_malformed_work_dir_is_rejected_and_sibling_work_survives(
    tmp_path: Path, make_wrong
) -> None:
    root = _yd_root(tmp_path)
    _seed_states_and_output(root)
    work_root = _work_root(tmp_path)
    exact, log_path = _make_work(work_root)
    sibling = work_root / SIBLING / T
    sibling.mkdir(parents=True)
    (sibling / "keep.txt").write_text("gfs\n", encoding="utf-8")
    wrong = make_wrong(work_root)
    wrong.mkdir(parents=True, exist_ok=True)
    (wrong / "wrong.txt").write_text("stay\n", encoding="utf-8")
    spec = _spec(wrong, log_path)
    before = snapshot_tree(work_root)

    with pytest.raises(cleanup.CleanupError) as info:
        _inputs(root, work_root, wrong, log_path, spec=spec)

    _assert_not_leaked(info.value)
    assert info.value.phase == "validate"
    assert snapshot_tree(work_root) == before
    assert exact.is_dir()
    assert (sibling / "keep.txt").read_text(encoding="utf-8") == "gfs\n"
    assert (wrong / "wrong.txt").read_text(encoding="utf-8") == "stay\n"


def test_merged_log_symlink_leaf_is_refused_without_following(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    _seed_states_and_output(root)
    work_root = _work_root(tmp_path)
    work_dir = work_root / SOURCE / T
    work_dir.mkdir(parents=True)
    outside = _base(tmp_path) / "outside"
    outside.mkdir()
    target = outside / "job.log"
    payload = b"secret\n\xff"
    target.write_bytes(payload)
    log_path = work_dir / "merged.log"
    log_path.symlink_to(target)
    before_work = snapshot_tree(work_root)

    with pytest.raises(cleanup.CleanupError) as info:
        _inputs(root, work_root, work_dir, log_path)

    _assert_not_leaked(info.value)
    assert info.value.phase == "log"
    assert info.value.path == log_path
    assert snapshot_tree(work_root) == before_work
    assert target.read_bytes() == payload
    assert log_path.is_symlink()


def test_large_merged_log_is_copied_byte_exact_without_linear_peak(
    tmp_path: Path,
) -> None:
    block = b"\xff\xfe" + b"x" * 1022
    small_size = 512 * 1024
    huge_size = 2 * 1024 * 1024
    assert small_size % len(block) == 0
    assert huge_size % len(block) == 0

    def run_once(size: int) -> int:
        scene = _base(tmp_path) / f"size-{size}"
        scene.mkdir()
        local_root = scene / "yd"
        local_root.mkdir()
        _seed_states_and_output(local_root)
        local_work = scene / "work"
        local_work.mkdir()
        work_dir, log_path = _make_work(local_work, log_bytes=b"")
        with open(log_path, "wb") as handle:
            handle.writelines(block for _ in range(size // len(block)))
        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            cleanup.finalize_failed_job(
                _inputs(local_root, local_work, work_dir, log_path)
            )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        landed = (local_root / "logs" / SOURCE / f"{T}.log").read_bytes()
        assert landed.startswith(FAILED_HEADER)
        assert landed[len(FAILED_HEADER) :] == block * (size // len(block))
        assert not work_dir.exists()
        return peak

    small_peak = run_once(small_size)
    huge_peak = run_once(huge_size)
    assert huge_peak < huge_size // 2, (huge_peak, huge_size)
    assert huge_peak - small_peak < (huge_size - small_size) // 4, (
        huge_peak,
        small_peak,
        huge_size,
        small_size,
    )


# --- 公共错误域矩阵（失败侧） ---


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source": ""},
        {"source": ".."},
        {"exit_code": ""},
        {"cycle": OFF_CLOCK},
    ],
    ids=["empty-source", "dotdot-source", "empty-exit", "unroundtrippable-cycle"],
)
def test_illegal_failure_inputs_map_to_validate(tmp_path: Path, kwargs: dict) -> None:
    root = _yd_root(tmp_path)
    work_root = _work_root(tmp_path)
    work_dir, log_path = _make_work(work_root)
    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.FailureInputs(
            yd_root=root,
            work_root=work_root,
            source=kwargs.get("source", SOURCE),
            cycle=kwargs.get("cycle", parse_cycle(T)),
            job_spec=_spec(work_dir, log_path),
            job_record=_failed_record(),
            exit_code=kwargs.get("exit_code", "1:0"),
        )
    _assert_wrapped(info.value)
    assert info.value.phase == "validate"


def test_succeeded_record_is_validate(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    work_root = _work_root(tmp_path)
    work_dir, log_path = _make_work(work_root)
    record = JobRecord(
        job_id="fake-1",
        name="ifs-2026082612",
        state=JobState.SUCCEEDED,
        resources=RESOURCES,
        submitted_at=SUBMITTED,
        started_at=STARTED,
        ended_at=ENDED,
    )
    with pytest.raises(cleanup.CleanupError) as info:
        _inputs(root, work_root, work_dir, log_path, record=record)
    _assert_wrapped(info.value)
    assert info.value.phase == "validate"


def test_mismatched_job_name_is_validate(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    work_root = _work_root(tmp_path)
    work_dir, log_path = _make_work(work_root)
    spec = _spec(work_dir, log_path, name="gfs-2026082612")
    with pytest.raises(cleanup.CleanupError) as info:
        _inputs(root, work_root, work_dir, log_path, spec=spec)
    _assert_wrapped(info.value)
    assert info.value.phase == "validate"


def test_log_target_directory_is_phase_log(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    work_root = _work_root(tmp_path)
    work_dir, log_path = _make_work(work_root)
    occupied = root / "logs" / SOURCE / f"{T}.log"
    occupied.mkdir(parents=True)
    with pytest.raises(cleanup.CleanupError) as info:
        cleanup.finalize_failed_job(_inputs(root, work_root, work_dir, log_path))
    _assert_not_leaked(info.value)
    assert info.value.phase == "log"
    assert info.value.path == occupied
    assert work_dir.is_dir()


def test_work_delete_failure_keeps_the_committed_log(tmp_path: Path) -> None:
    _skip_if_root()
    root = _yd_root(tmp_path)
    _seed_states_and_output(root)
    work_root = _work_root(tmp_path)
    work_dir, log_path = _make_work(work_root)
    locked = work_dir / "locked"
    locked.mkdir()
    (locked / "x.bin").write_bytes(b"x")
    inputs = _inputs(root, work_root, work_dir, log_path)
    locked.chmod(0o000)
    try:
        with pytest.raises(cleanup.CleanupError) as info:
            cleanup.finalize_failed_job(inputs)
    finally:
        locked.chmod(0o755)

    _assert_wrapped(info.value)
    assert info.value.phase == "work"
    assert info.value.path == work_dir
    log = root / "logs" / SOURCE / f"{T}.log"
    assert log.read_bytes() == FAILED_HEADER + FAILED_LOG_BYTES
    assert work_dir.exists()


def test_error_domain_never_leaks_underlying_types(tmp_path: Path) -> None:
    root = _yd_root(tmp_path)
    work_root = _work_root(tmp_path)
    work_dir, log_path = _make_work(work_root)
    cases = []

    def collect(fn) -> None:  # type: ignore[no-untyped-def]
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 本用例就是要证明漏出的类型
            cases.append(exc)

    collect(
        lambda: cleanup.FailureInputs(
            yd_root=root,
            work_root=work_root,
            source="",
            cycle=parse_cycle(T),
            job_spec=_spec(work_dir, log_path),
            job_record=_failed_record(),
            exit_code="1:0",
        )
    )
    collect(lambda: cleanup.plan_retention(root, ".."))
    collect(
        lambda: cleanup.RetentionPlan(
            yd_root=root,
            source="a/b",
            latest_done=None,
            cutoff=None,
            output_dirs=(),
            log_files=(),
        )
    )
    occupied = root / "logs" / SOURCE / f"{T}.log"
    occupied.mkdir(parents=True)
    collect(
        lambda: cleanup.finalize_failed_job(
            _inputs(root, work_root, work_dir, log_path)
        )
    )
    assert cases
    for exc in cases:
        _assert_not_leaked(exc)


def test_iso_literals_match_datetime_isoformat() -> None:
    """手写 JSON 时间字面量与 tz-aware isoformat 对齐，避免测试自己编造错值。"""
    assert SUBMITTED.isoformat() == "2026-08-26T12:00:00+00:00"
    assert STARTED.isoformat() == "2026-08-26T12:00:10+00:00"
    assert ENDED.isoformat() == "2026-08-26T12:01:00+00:00"
    assert json.loads(FAILED_HEADER.split(b"\n", 1)[0])["job_id"] == "fake-7"
