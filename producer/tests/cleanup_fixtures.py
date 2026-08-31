r"""`yd_producer.cleanup` 测试共享 oracle 字面量、作业/目录树构造器与断言 helper（issue #25）。

本模块只放独立期望值、JobSpec/JobRecord/目录树构造器、context manager 与断言 helper，
不含任何 `test_*` 用例。拆分自原 `tests/test_cleanup.py`（项目 large-file-guard
1000 行上限的布局纠正，Phase 6.2 收尾）。
"""

from __future__ import annotations

import contextlib
import os
import stat
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from frontier_fixtures import YdRootBuilder, parse_cycle, snapshot_tree

from yd_producer import cleanup
from yd_producer.controller import DiscoveryUnreadableError
from yd_producer.executor import JobRecord, JobSpec, JobState
from yd_producer.store.safe_fs import SafeFilesystemError

SOURCE = "ifs"
SIBLING = "gfs"
T = "2026082612"
D = "2026082600"
D_MINUS_14D = "2026081200"
D_MINUS_14D_12H = "2026081112"
OLDER = "2026081000"
OLDER_NEXT = "2026081012"
ILLEGAL_CYCLE = "2026023100"
OFF_CLOCK = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)

SUBMITTED = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)
STARTED = datetime(2026, 8, 26, 12, 0, 10, tzinfo=UTC)
ENDED = datetime(2026, 8, 26, 12, 1, 0, tzinfo=UTC)
TIMEOUT_SUBMITTED = datetime(2026, 8, 26, 13, 0, 0, tzinfo=UTC)
TIMEOUT_ENDED = datetime(2026, 8, 26, 17, 0, 0, tzinfo=UTC)

FAILED_LOG_BYTES = b"job ran\n\xff\xfe\x00not-utf8\n"
TIMEOUT_LOG_BYTES = b"never started\n\x80\x81"
RESOURCES: dict[str, str | int] = {
    "partition": "cpu",
    "account": "a",
    "cpus": 8,
    "memory": "32G",
    "walltime": "04:00:00",
}

FAILED_HEADER = (
    b'{"command":["shud","ifs","--cycle","2026082612"],'
    b'"cycle":"2026082612",'
    b'"ended_at":"2026-08-26T12:01:00+00:00",'
    b'"exit_code":"1:0",'
    b'"job_id":"fake-7",'
    b'"job_name":"ifs-2026082612",'
    b'"schema":"yd-failure-log-v1",'
    b'"source":"ifs",'
    b'"started_at":"2026-08-26T12:00:10+00:00",'
    b'"state":"FAILED",'
    b'"submitted_at":"2026-08-26T12:00:00+00:00"}'
) + b"\n--- stdout/stderr ---\n"

TIMEOUT_HEADER = (
    b'{"command":["shud","ifs","--cycle","2026082612"],'
    b'"cycle":"2026082612",'
    b'"ended_at":"2026-08-26T17:00:00+00:00",'
    b'"exit_code":"0:9",'
    b'"job_id":"slurm-99",'
    b'"job_name":"ifs-2026082612",'
    b'"schema":"yd-failure-log-v1",'
    b'"source":"ifs",'
    b'"started_at":null,'
    b'"state":"TIMEOUT",'
    b'"submitted_at":"2026-08-26T13:00:00+00:00"}'
) + b"\n--- stdout/stderr ---\n"

WRAP_TYPES = (ValueError, DiscoveryUnreadableError, SafeFilesystemError, OSError)


def _skip_if_root() -> None:
    if os.geteuid() == 0:
        pytest.skip("root 无视 mode 位，本用例无判别力")


@contextlib.contextmanager
def _unreadable(path: Path) -> Iterator[None]:
    original = stat.S_IMODE(path.stat().st_mode)
    path.chmod(0o000)
    try:
        yield
    finally:
        path.chmod(original)


def _base(tmp_path: Path) -> Path:
    return tmp_path.resolve()


def _yd_root(tmp_path: Path) -> Path:
    root = _base(tmp_path) / "yd"
    root.mkdir()
    return root


def _work_root(tmp_path: Path) -> Path:
    path = _base(tmp_path) / "scratch" / "work"
    path.mkdir(parents=True)
    return path


def _make_work(
    work_root: Path,
    *,
    source: str = SOURCE,
    cycle: str = T,
    log_bytes: bytes = FAILED_LOG_BYTES,
    log_name: str = "merged.log",
) -> tuple[Path, Path]:
    work_dir = work_root / source / cycle
    work_dir.mkdir(parents=True)
    (work_dir / "canonical").mkdir()
    (work_dir / "canonical" / "forcing.csv").write_text("x\n", encoding="utf-8")
    log_path = work_dir / log_name
    log_path.write_bytes(log_bytes)
    return work_dir, log_path


def _spec(work_dir: Path, log_path: Path, *, name: str = "ifs-2026082612") -> JobSpec:
    return JobSpec(
        name=name,
        work_dir=work_dir,
        command=("shud", "ifs", "--cycle", T),
        log_path=log_path,
        resources=RESOURCES,
    )


def _failed_record() -> JobRecord:
    return JobRecord(
        job_id="fake-7",
        name="ifs-2026082612",
        state=JobState.FAILED,
        resources=RESOURCES,
        submitted_at=SUBMITTED,
        started_at=STARTED,
        ended_at=ENDED,
    )


def _timeout_record() -> JobRecord:
    return JobRecord(
        job_id="slurm-99",
        name="ifs-2026082612",
        state=JobState.TIMEOUT,
        resources=RESOURCES,
        submitted_at=TIMEOUT_SUBMITTED,
        started_at=None,
        ended_at=TIMEOUT_ENDED,
    )


def _inputs(
    root: Path,
    work_root: Path,
    work_dir: Path,
    log_path: Path,
    *,
    record: JobRecord | None = None,
    exit_code: str = "1:0",
    source: str = SOURCE,
    cycle: datetime | None = None,
    spec: JobSpec | None = None,
) -> cleanup.FailureInputs:
    return cleanup.FailureInputs(
        yd_root=root,
        work_root=work_root,
        source=source,
        cycle=parse_cycle(T) if cycle is None else cycle,
        job_spec=spec if spec is not None else _spec(work_dir, log_path),
        job_record=_failed_record() if record is None else record,
        exit_code=exit_code,
    )


def _seed_states_and_output(root: Path) -> YdRootBuilder:
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)
    builder.write_state(T, SOURCE)
    builder.write_done(D, SIBLING)
    builder.write_output_dat(D, SIBLING)
    builder.write_state(T, SIBLING)
    return builder


def _write_log(root: Path, source: str, cycle: str, payload: bytes = b"old\n") -> Path:
    path = root / "logs" / source / f"{cycle}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _assert_not_leaked(exc: BaseException) -> None:
    assert type(exc) is cleanup.CleanupError
    assert not isinstance(exc, WRAP_TYPES)


def _assert_wrapped(exc: cleanup.CleanupError) -> None:
    _assert_not_leaked(exc)
    if exc.__cause__ is not None:
        assert isinstance(exc.__cause__, WRAP_TYPES)


def _retention_tree(root: Path) -> dict[str, Path]:
    builder = YdRootBuilder(root=root)
    paths: dict[str, Path] = {}
    for cycle in (D_MINUS_14D_12H, D_MINUS_14D, D):
        builder.write_done(cycle, SOURCE)
        builder.write_output_dat(cycle, SOURCE)
        paths[f"ifs-out-{cycle}"] = builder.source_output_dir(cycle, SOURCE)
        paths[f"ifs-log-{cycle}"] = _write_log(
            root, SOURCE, cycle, f"ifs-{cycle}\n".encode()
        )
        builder.write_done(cycle, SIBLING)
        builder.write_output_dat(cycle, SIBLING)
        paths[f"gfs-out-{cycle}"] = builder.source_output_dir(cycle, SIBLING)
        paths[f"gfs-log-{cycle}"] = _write_log(
            root, SIBLING, cycle, f"gfs-{cycle}\n".encode()
        )
    # D 之后：无 DONE 的半成品与失败日志，不得进入删除清单。
    paths[f"ifs-out-{T}"] = builder.write_output_dat(T, SOURCE).parent
    paths[f"ifs-log-{T}"] = _write_log(root, SOURCE, T, f"ifs-{T}\n".encode())
    paths[f"gfs-out-{T}"] = builder.write_output_dat(T, SIBLING).parent
    paths[f"gfs-log-{T}"] = _write_log(root, SIBLING, T, f"gfs-{T}\n".encode())
    builder.write_state(T, SOURCE)
    builder.write_state(T, SIBLING)
    illegal_dir = root / "output" / ILLEGAL_CYCLE / SOURCE
    illegal_dir.mkdir(parents=True)
    (illegal_dir / "DONE").write_bytes(b"")
    paths["illegal-dir"] = illegal_dir
    stray_log = root / "logs" / SOURCE / "notes.log"
    stray_log.write_text("clutter\n", encoding="utf-8")
    paths["stray-log"] = stray_log
    bak = root / "logs" / SOURCE / f"{D_MINUS_14D_12H}.log.bak"
    bak.write_text("bak\n", encoding="utf-8")
    paths["bak-log"] = bak
    return paths


def _windowed_plan_kwargs(
    root: Path,
    *,
    output_dirs: tuple[Path, ...] = (),
    log_files: tuple[Path, ...] = (),
    latest_done: datetime | None = None,
    cutoff: datetime | None = None,
    source: str = SOURCE,
) -> dict[str, object]:
    return {
        "yd_root": root,
        "source": source,
        "latest_done": parse_cycle(D) if latest_done is None else latest_done,
        "cutoff": parse_cycle(D_MINUS_14D) if cutoff is None else cutoff,
        "output_dirs": output_dirs,
        "log_files": log_files,
    }


def _seed_identity_tree(root: Path) -> YdRootBuilder:
    builder = YdRootBuilder(root=root)
    builder.write_done(D, SOURCE)
    builder.write_output_dat(D, SOURCE)
    builder.write_done(D, SIBLING)
    builder.write_output_dat(D, SIBLING)
    builder.write_state(T, SOURCE)
    builder.write_state(T, SIBLING)
    return builder


def _assert_survives(paths: tuple[Path, ...], before: dict[Path, object]) -> None:
    for path in paths:
        assert path.exists()
        assert snapshot_tree(path) == before[path]
