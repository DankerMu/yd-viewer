"""Issue #372: 14-day retention wired into `run_sources` workers.

Exercised only at the `run_sources` seam on a temporary yd_root. Expected
cutoffs, paths and audit texts are hand-derived from compute-loop §12 and the
retention-wiring fixture: per-source anchor = that source's latest regular-file
DONE, cutoff = anchor - 14 days, `cycle < cutoff` is deleted, equal is kept.
"""

from __future__ import annotations

import errno
import os
import pathlib
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Self

import pytest
from controller_sources_fixtures import (
    IFS_RAW_LOG,
    FailureLogHook,
    TerminalHookGate,
    ThrowingProvider,
    done_path,
    dual_run_kwargs,
    fake_for,
    hooked_success,
    plant_unknown_work,
    require_source_tuple,
    success_driver,
    work_dir,
    write_dual_tree,
)
from run_once_fixtures import write_state

from yd_producer import cleanup, controller
from yd_producer.controller import (
    RunError,
    RunOutcome,
    RunSourcesError,
    StopReason,
    run_sources,
)
from yd_producer.executor import JobState

#: gfs 最新 DONE = D；ifs 最新 DONE = D − 17d（两源落后程度不同）。
D = datetime(2026, 8, 26, 12, tzinfo=UTC)
D_IFS = datetime(2026, 8, 9, 12, tzinfo=UTC)
#: 手算 cutoff：gfs 2026-08-12T12，ifs 2026-07-26T12。
GFS_CUTOFF = datetime(2026, 8, 12, 12, tzinfo=UTC)
IFS_CUTOFF = datetime(2026, 7, 26, 12, tzinfo=UTC)
#: 两源都在窗口外。
OLD_BOTH = datetime(2026, 7, 22, 12, tzinfo=UTC)
#: gfs 窗口外、ifs 窗口内（落在 ifs 的 [D−31d, D−14d)）。
MID = datetime(2026, 8, 6, 12, tzinfo=UTC)
#: gfs 窗口内。
RECENT = datetime(2026, 8, 13, 12, tzinfo=UTC)
PREFIX = "retention cleanup:"


def _cid(cycle: datetime) -> str:
    return cycle.strftime("%Y%m%d%H")


def _yd(local) -> pathlib.Path:
    return pathlib.Path(local.yd_root)


def _output(local, source: str, cycle: datetime) -> pathlib.Path:
    return _yd(local) / "output" / _cid(cycle) / source


def _log(local, source: str, cycle: datetime) -> pathlib.Path:
    return _yd(local) / "logs" / source / f"{_cid(cycle)}.log"


def _plant_output(local, source: str, cycle: datetime) -> pathlib.Path:
    directory = _output(local, source, cycle)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "yd.rivqdown.dat").write_bytes(b"dat\n")
    (directory / "DONE").write_bytes(b"")
    return directory


def _plant_log(local, source: str, cycle: datetime) -> pathlib.Path:
    path = _log(local, source, cycle)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"failure log\n")
    return path


def _anchor(local, source: str, latest: datetime) -> None:
    """最新 DONE = latest；下一轮 latest+12h 的状态就绪、raw 缺席 → raw 缺口停源。"""
    _plant_output(local, source, latest)
    write_state(local, source=source, cycle=latest + timedelta(hours=12))


def _assert_waiting_base(text: str, source: str, latest: datetime | None) -> None:
    """raw 缺口停源的原文（compute-loop §10）：单行、无审计。"""
    if latest is None:
        origin = "全新链最早状态文件名 2026082612"
        target = "2026082612"
    else:
        origin = f"最新 DONE cycle {_cid(latest)} + 12h"
        target = _cid(latest + timedelta(hours=12))
    assert "\n" not in text, text
    assert text.startswith(f"{source}: 待跑 T={target}（{origin}）；raw 未齐："), text
    assert text.endswith("停在缺口等待，不跳轮"), text


def _audit(outputs, logs, cycles) -> str:
    paths = (*outputs, *logs, *cycles)
    return (
        f"{PREFIX} 删除 {len(outputs)} 个 output 目录、{len(logs)} 个失败日志、"
        f"{len(cycles)} 个空 cycle 目录：" + ", ".join(str(path) for path in paths)
    )


def _tick(config, local, **kwargs):
    gate = TerminalHookGate()
    return run_sources(
        **dual_run_kwargs(
            config,
            local,
            ifs=hooked_success("ifs", gate=gate),
            gfs=hooked_success("gfs", gate=gate),
            **kwargs,
        )
    )


def _single_waiting(reports, source: str):
    items = require_source_tuple(reports, source)
    assert len(items) == 1
    assert items[0].outcome is RunOutcome.STOPPED
    assert items[0].stop_reason is StopReason.RAW_INCOMPLETE
    return items[0]


def _audit_items(detail: str) -> tuple[str, list[str]]:
    base, _, audit = detail.partition("\n")
    assert audit.startswith(PREFIX)
    _, _, listing = audit.partition("：")
    return base, listing.split(", ")


# --- 2.1 / 2.3：两源锚点各自计算；空 cycle 目录 ------------------------------------------


def test_each_source_deletes_only_outside_its_own_window(tmp_path) -> None:
    config, local = write_dual_tree(tmp_path, raw=False)
    _anchor(local, "gfs", D)
    _anchor(local, "ifs", D_IFS)
    for source in ("gfs", "ifs"):
        for cycle in (OLD_BOTH, MID):
            _plant_output(local, source, cycle)
            _plant_log(local, source, cycle)
    _plant_output(local, "gfs", RECENT)
    _plant_log(local, "gfs", RECENT)
    _plant_output(local, "ifs", IFS_CUTOFF)
    _plant_log(local, "ifs", IFS_CUTOFF)

    report = _tick(config, local)

    gfs = _single_waiting(report.gfs, "gfs")
    ifs = _single_waiting(report.ifs, "ifs")
    # gfs：只删 < D−14d 的 gfs 条目。
    for cycle in (OLD_BOTH, MID):
        assert not _output(local, "gfs", cycle).exists()
        assert not _log(local, "gfs", cycle).exists()
    assert (_output(local, "gfs", RECENT) / "DONE").is_file()
    assert _log(local, "gfs", RECENT).is_file()
    assert (_output(local, "gfs", D) / "DONE").is_file()
    # ifs：只删 < D−31d 的 ifs 条目；[D−31d, D−14d) 保留，不借用 gfs 的锚点。
    assert not _output(local, "ifs", OLD_BOTH).exists()
    assert not _log(local, "ifs", OLD_BOTH).exists()
    for cycle in (MID, IFS_CUTOFF, D_IFS):
        assert (_output(local, "ifs", cycle) / "DONE").is_file()
    for cycle in (MID, IFS_CUTOFF):
        assert _log(local, "ifs", cycle).is_file()
    # 2.3：两源都在窗口外的 T 被 rmdir；只有一源在窗口外的 T 保留另一源目录。
    old_cycle_dir = _yd(local) / "output" / _cid(OLD_BOTH)
    assert not old_cycle_dir.exists()
    assert sorted(os.listdir(_yd(local) / "output" / _cid(MID))) == ["ifs"]

    gfs_base, gfs_items = _audit_items(gfs.detail)
    ifs_base, ifs_items = _audit_items(ifs.detail)
    _assert_waiting_base(gfs_base, "gfs", D)
    _assert_waiting_base(ifs_base, "ifs", D_IFS)
    expected_gfs = {
        str(_output(local, "gfs", OLD_BOTH)),
        str(_output(local, "gfs", MID)),
        str(_log(local, "gfs", OLD_BOTH)),
        str(_log(local, "gfs", MID)),
    }
    expected_ifs = {
        str(_output(local, "ifs", OLD_BOTH)),
        str(_log(local, "ifs", OLD_BOTH)),
    }
    # 空 cycle 目录由两源中后执行 retention 的那一方 rmdir，恰计入一次。
    assert set(gfs_items) - expected_gfs | set(ifs_items) - expected_ifs == {
        str(old_cycle_dir)
    }
    assert expected_gfs <= set(gfs_items)
    assert expected_ifs <= set(ifs_items)
    assert (gfs_items + ifs_items).count(str(old_cycle_dir)) == 1


def test_window_boundary_keeps_cutoff_and_deletes_twelve_hours_earlier(
    tmp_path,
) -> None:
    config, local = write_dual_tree(tmp_path, raw=False)
    _anchor(local, "gfs", D)
    before = GFS_CUTOFF - timedelta(hours=12)
    for cycle in (GFS_CUTOFF, before):
        _plant_output(local, "gfs", cycle)
        _plant_log(local, "gfs", cycle)

    report = _tick(config, local)

    _single_waiting(report.gfs, "gfs")
    assert (_output(local, "gfs", GFS_CUTOFF) / "DONE").is_file()
    assert _log(local, "gfs", GFS_CUTOFF).is_file()
    assert not _output(local, "gfs", before).exists()
    assert not _log(local, "gfs", before).exists()
    assert not (_yd(local) / "output" / _cid(before)).exists()


def test_cycle_dir_with_foreign_content_is_kept_silently(tmp_path) -> None:
    config, local = write_dual_tree(tmp_path, raw=False)
    _anchor(local, "gfs", D)
    old = _plant_output(local, "gfs", OLD_BOTH)
    foreign = old.parent / "residue-marker"
    foreign.write_bytes(b"not ours\n")

    report = _tick(config, local)

    gfs = _single_waiting(report.gfs, "gfs")
    assert not old.exists()
    assert foreign.read_bytes() == b"not ours\n"
    base, _, audit = gfs.detail.partition("\n")
    _assert_waiting_base(base, "gfs", D)
    assert audit == _audit((old,), (), ())


def test_preexisting_empty_cycle_dir_before_cutoff_is_swept_and_counted(
    tmp_path,
) -> None:
    config, local = write_dual_tree(tmp_path, raw=False)
    _anchor(local, "gfs", D)
    output_root = _yd(local) / "output"
    stale = output_root / _cid(OLD_BOTH)
    stale.mkdir()
    in_window = output_root / _cid(RECENT)
    in_window.mkdir()
    not_a_cycle = output_root / "not-a-cycle"
    not_a_cycle.mkdir()

    report = _tick(config, local)

    gfs = _single_waiting(report.gfs, "gfs")
    base, _, audit = gfs.detail.partition("\n")
    _assert_waiting_base(base, "gfs", D)
    assert audit == _audit((), (), (stale,))
    assert not stale.exists()
    assert in_window.is_dir()
    assert not_a_cycle.is_dir()


#: 三个两源都在窗口外的 T（都早于 ifs cutoff 2026-07-26T12）。
SWEEP_TS = (
    datetime(2026, 7, 1, 0, tzinfo=UTC),
    datetime(2026, 7, 2, 0, tzinfo=UTC),
    datetime(2026, 7, 3, 0, tzinfo=UTC),
)


def test_partial_rmdir_failure_keeps_sweeping_notes_audit_and_recovers(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path, raw=False)
    _anchor(local, "gfs", D)
    _anchor(local, "ifs", D_IFS)
    for cycle in SWEEP_TS:
        for source in ("gfs", "ifs"):
            _plant_output(local, source, cycle)
    output_root = _yd(local) / "output"
    first, second, third = (output_root / _cid(cycle) for cycle in SWEEP_TS)
    original_rmdir = os.rmdir

    def rmdir(path, *args, **kwargs):
        if path == first.name and "dir_fd" in kwargs:
            raise OSError(errno.EIO, "injected EIO", path)
        return original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "rmdir", rmdir)
    with pytest.raises(RunSourcesError) as info:
        _tick(config, local)

    assert set(info.value.errors) == {"gfs", "ifs"}
    swept: list[str] = []
    for source in ("gfs", "ifs"):
        error = info.value.errors[source]
        assert error.phase == "cleanup"
        assert str(error).startswith(f"{source}: retention 失败：")
        assert error.__cause__.errno == errno.EIO
        notes = error.__notes__
        assert len(notes) == 1
        assert "\n".join(notes).count(PREFIX) == 1
        assert str(info.value).count(notes[0]) == 1
        _, items = _audit_items("base\n" + notes[0])
        outputs = [str(_output(local, source, cycle)) for cycle in SWEEP_TS]
        assert items[:3] == outputs
        swept += items[3:]
        _single_waiting(info.value.reports[source], source)
        assert PREFIX not in info.value.reports[source][0].detail
    # 其余两个 T 在同一 tick 被删，且恰由一个源计入 k；第一个 T 留下空目录。
    assert sorted(swept) == [str(second), str(third)]
    assert not second.exists()
    assert not third.exists()
    assert first.is_dir()
    assert os.listdir(first) == []

    monkeypatch.setattr(os, "rmdir", original_rmdir)
    report = _tick(config, local)

    assert not first.exists()
    details = [
        _single_waiting(report.gfs, "gfs").detail,
        _single_waiting(report.ifs, "ifs").detail,
    ]
    audits = [detail.partition("\n")[2] for detail in details]
    assert sorted(audits) == ["", _audit((), (), (first,))]


# --- 2.4 / 2.9：报告文本；每 tick 执行且幂等 ------------------------------------------------


def test_audit_is_appended_to_last_report_and_second_tick_adds_nothing(
    tmp_path,
) -> None:
    config, local = write_dual_tree(tmp_path, raw=False)
    _anchor(local, "gfs", D)
    old_dir = _plant_output(local, "gfs", OLD_BOTH)
    old_log = _plant_log(local, "gfs", OLD_BOTH)
    mid_log = _plant_log(local, "gfs", MID)

    first = _tick(config, local)

    gfs = _single_waiting(first.gfs, "gfs")
    ifs = _single_waiting(first.ifs, "ifs")
    assert not old_dir.parent.exists()
    assert not old_log.exists()
    assert not mid_log.exists()

    second = _tick(config, local)

    gfs_again = _single_waiting(second.gfs, "gfs")
    ifs_again = _single_waiting(second.ifs, "ifs")
    # 无删除的 tick：detail 就是原文（单行，不含审计）。
    _assert_waiting_base(gfs_again.detail, "gfs", D)
    _assert_waiting_base(ifs_again.detail, "ifs", None)
    assert ifs.detail == ifs_again.detail
    # 有删除的 tick：原文 + 换行 + 审计（计数与逐条绝对路径）。
    assert gfs.detail == (
        gfs_again.detail
        + "\n"
        + _audit((old_dir,), (old_log, mid_log), (old_dir.parent,))
    )


class _OwnerLock:
    """发布锁替身：记录当前持有者线程。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.owner: int | None = None

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        acquired = self._lock.acquire(blocking, timeout)
        if acquired:
            self.owner = threading.get_ident()
        return acquired

    def release(self) -> None:
        self.owner = None
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


# --- 2.5：plan、execute、rmdir 都在发布锁内 ------------------------------------------------


def test_plan_execute_and_rmdir_run_under_the_shared_publish_lock(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yd_producer import _controller_run, _controller_sources

    config, local = write_dual_tree(tmp_path, raw=False)
    _anchor(local, "gfs", D)
    _anchor(local, "ifs", D_IFS)
    _plant_output(local, "gfs", OLD_BOTH)
    _plant_output(local, "ifs", OLD_BOTH)
    cycle_name = _cid(OLD_BOTH)
    created: list[_OwnerLock] = []
    locks: list = []
    seen: list[tuple[str, bool]] = []
    original_run_once = _controller_run.run_once
    original_plan = cleanup.plan_retention
    original_execute = cleanup.execute_retention_plan
    original_rmdir = os.rmdir

    def make_lock() -> _OwnerLock:
        lock = _OwnerLock()
        created.append(lock)
        return lock

    def capturing_run_once(**kwargs):
        locks.append(kwargs["publish_lock"])
        return original_run_once(**kwargs)

    def held() -> bool:
        # 不只是"有人持锁"：必须是正在执行该钩子的线程自己持有发布锁。
        return len(created) == 1 and created[0].owner == threading.get_ident()

    def plan(yd_root, source):
        seen.append((f"plan:{source}", held()))
        return original_plan(yd_root, source)

    def execute(plan_value):
        seen.append((f"execute:{plan_value.source}", held()))
        return original_execute(plan_value)

    def rmdir(path, *args, **kwargs):
        if path == cycle_name and "dir_fd" in kwargs:
            seen.append(("rmdir", held()))
        return original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(
        _controller_sources, "threading", SimpleNamespace(Lock=make_lock)
    )
    monkeypatch.setattr(_controller_run, "run_once", capturing_run_once)
    monkeypatch.setattr(cleanup, "plan_retention", plan)
    monkeypatch.setattr(cleanup, "execute_retention_plan", execute)
    monkeypatch.setattr(os, "rmdir", rmdir)

    _tick(config, local)

    assert len(created) == 1
    assert locks == [created[0], created[0]]
    assert not created[0].locked()
    assert created[0].owner is None
    assert sorted(name for name, _ in seen) == [
        "execute:gfs",
        "execute:ifs",
        "plan:gfs",
        "plan:ifs",
        "rmdir",
        "rmdir",
    ]
    assert all(flag for _, flag in seen), seen
    assert not (_yd(local) / "output" / cycle_name).exists()


# --- 2.6：失败映射 ------------------------------------------------------------------------


def test_execute_failure_becomes_cleanup_error_and_keeps_published_round(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    injected = cleanup.CleanupError(
        "injected retention execute", phase="retention-execute", path=None
    )
    original_execute = cleanup.execute_retention_plan

    def execute(plan_value):
        if plan_value.source == "ifs":
            raise injected
        return original_execute(plan_value)

    monkeypatch.setattr(cleanup, "execute_retention_plan", execute)

    with pytest.raises(RunSourcesError) as info:
        _tick(config, local)

    assert set(info.value.errors) == {"ifs"}
    error = info.value.errors["ifs"]
    assert isinstance(error, RunError)
    assert error.phase == "cleanup"
    assert error.source == "ifs"
    assert error.cycle is None
    assert str(error) == "ifs: retention 失败：injected retention execute"
    assert error.__cause__ is injected
    ifs = info.value.reports["ifs"]
    assert [item.outcome for item in ifs] == [RunOutcome.SUCCEEDED, RunOutcome.STOPPED]
    assert ifs[1].stop_reason is StopReason.RAW_INCOMPLETE
    assert all(PREFIX not in item.detail for item in ifs)
    assert done_path(local, "ifs").is_file()
    gfs = require_source_tuple(info.value.reports["gfs"], "gfs")
    assert [item.outcome for item in gfs] == [RunOutcome.SUCCEEDED, RunOutcome.STOPPED]
    assert done_path(local, "gfs").is_file()


def test_cycle_dir_rmdir_error_becomes_cleanup_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path, raw=False)
    _anchor(local, "gfs", D)
    old = _plant_output(local, "gfs", OLD_BOTH)
    cycle_name = _cid(OLD_BOTH)
    original_rmdir = os.rmdir
    denied = PermissionError(errno.EACCES, "injected rmdir denial", cycle_name)

    def rmdir(path, *args, **kwargs):
        if path == cycle_name and "dir_fd" in kwargs:
            raise denied
        return original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "rmdir", rmdir)

    with pytest.raises(RunSourcesError) as info:
        _tick(config, local)

    assert set(info.value.errors) == {"gfs"}
    error = info.value.errors["gfs"]
    assert error.phase == "cleanup"
    assert str(error).startswith("gfs: retention 失败：")
    assert error.__cause__ is denied
    # execute 已完成：已删审计作为 note 挂在错误上，不追加到报告。
    assert error.__notes__ == [_audit((old,), (), ())]
    gfs = _single_waiting(info.value.reports["gfs"], "gfs")
    _assert_waiting_base(gfs.detail, "gfs", D)
    assert not old.exists()
    assert old.parent.is_dir()
    _single_waiting(info.value.reports["ifs"], "ifs")


# --- 2.7：RunError 路径跳过；startup hygiene 停源照常执行 -----------------------------------


def _count_plans(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = cleanup.plan_retention

    def plan(yd_root, source):
        calls.append(source)
        return original(yd_root, source)

    monkeypatch.setattr(cleanup, "plan_retention", plan)
    return calls


def test_preflight_run_error_skips_retention(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path, raw=False)
    _anchor(local, "ifs", D)
    ifs_old = _plant_output(local, "ifs", OLD_BOTH)
    calls = _count_plans(monkeypatch)
    original = controller._preflight

    def ifs_fails(*, config, local, source):
        if source == "ifs":
            raise RunError("ifs preflight injected", phase="preflight", source="ifs")
        return original(config=config, local=local, source=source)

    monkeypatch.setattr(controller, "_preflight", ifs_fails)

    with pytest.raises(RunSourcesError) as info:
        _tick(config, local)

    assert info.value.errors["ifs"].phase == "preflight"
    assert info.value.reports["ifs"] == ()
    assert calls == ["gfs"]
    assert (ifs_old / "DONE").is_file()


def test_run_one_run_error_skips_retention(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path)
    ifs_old = _plant_output(local, "ifs", OLD_BOTH)
    _plant_output(local, "ifs", D - timedelta(hours=12))
    write_state(local, source="ifs", cycle=D)
    calls = _count_plans(monkeypatch)
    ifs_driver, _state, _slot = success_driver()
    gate = TerminalHookGate()
    from run_once_fixtures import HookedExecutor

    ifs_exec = HookedExecutor(
        fake_for("ifs", state=JobState.FAILED),
        FailureLogHook(local, "ifs", IFS_RAW_LOG),
    )
    providers = {
        "ifs": ThrowingProvider("ifs", RuntimeError("provider injected")),
        "gfs": ThrowingProvider("gfs", "9:1"),
    }
    with pytest.raises(RunSourcesError) as info:
        run_sources(
            **dual_run_kwargs(
                config,
                local,
                ifs=(ifs_driver, ifs_exec),
                gfs=hooked_success("gfs", gate=gate),
                providers=providers,
            )
        )

    assert info.value.errors["ifs"].phase == "cleanup"
    assert "retention" not in str(info.value.errors["ifs"])
    assert calls == ["gfs"]
    assert (ifs_old / "DONE").is_file()
    assert work_dir(local, "ifs").exists()


def test_startup_hygiene_stop_still_runs_retention_and_deletes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local = write_dual_tree(tmp_path, raw=False)
    latest = D - timedelta(hours=12)
    _plant_output(local, "ifs", latest)
    old = _plant_output(local, "ifs", OLD_BOTH)
    old_log = _plant_log(local, "ifs", OLD_BOTH)
    planted = plant_unknown_work(local, "ifs", shape="dir")
    calls = _count_plans(monkeypatch)

    report = _tick(config, local)

    ifs = require_source_tuple(report.ifs, "ifs")
    assert len(ifs) == 1
    assert ifs[0].stop_reason is StopReason.UNVERIFIED_WORK_RESIDUE
    base = (
        f"ifs: 待跑 T=2026082612 的精确 work {planted} "
        "仍存在（未验证跨进程残留）；保留证据，不读、不删、不提交"
    )
    assert ifs[0].detail == base + "\n" + _audit((old,), (old_log,), (old.parent,))
    assert sorted(calls) == ["gfs", "ifs"]
    assert not old.parent.exists()
    assert not old_log.exists()
    assert (planted / "old.bin").is_file()
    assert (_output(local, "ifs", latest) / "DONE").is_file()
