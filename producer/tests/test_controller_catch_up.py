"""`controller.catch_up_source`：单源多轮追赶与缺口停等（issue #27 / 任务 14.2）。

期望 cycle / job 名全部本地字面登记，不从被测函数回读。行为测试走真实
`run_once` + `FakeJobExecutor` + 有限 terminal hook；只在组合纯度测试 spy
公开 `run_once` seam。
"""

from __future__ import annotations

import ast
import inspect
import os
import pathlib
import stat
from datetime import UTC, datetime

import pytest
from run_once_fixtures import (
    CYCLE,
    T_PLUS_12,
    T_PLUS_24,
    HookState,
    InProcessDriver,
    bind_terminal_hook,
    success_outcome,
    write_config_local,
    write_raw_cycle,
    write_state,
    write_variant,
)

from yd_producer import _controller_run, controller, runlock
from yd_producer.controller import RunError, RunOutcome, StopReason, catch_up_source
from yd_producer.executor import FakeJobExecutor, FakeOutcome, JobState

SOURCE = "gfs"
JOB_T = "yd-gfs-2026082612"
JOB_T12 = "yd-gfs-2026082700"
JOB_T24 = "yd-gfs-2026082712"
CYCLE_T = datetime(2026, 8, 26, 12, tzinfo=UTC)
CYCLE_T12 = datetime(2026, 8, 27, 0, tzinfo=UTC)
CYCLE_T24 = datetime(2026, 8, 27, 12, tzinfo=UTC)
CYCLE_T36 = datetime(2026, 8, 28, 0, tzinfo=UTC)
CYCLE_T48 = datetime(2026, 8, 28, 12, tzinfo=UTC)
CYCLE_T60 = datetime(2026, 8, 29, 0, tzinfo=UTC)
JOB_T36 = "yd-gfs-2026082800"
JOB_T48 = "yd-gfs-2026082812"
_RUN_ONCE_KW = (
    "config",
    "local",
    "source",
    "executor",
    "driver",
    "poll_wait",
)
_EXTRA_CONTROL = (
    ast.Import,
    ast.ImportFrom,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.For,
    ast.AsyncFor,
    ast.Match,
    ast.Raise,
    ast.Assert,
    ast.Global,
    ast.Nonlocal,
    ast.Delete,
    ast.Pass,
    ast.Break,
    ast.Continue,
)


def _dump(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _is_name(node: ast.AST, ident: str) -> bool:
    return isinstance(node, ast.Name) and node.id == ident


def _is_attr(node: ast.AST, owner: str, attr: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and _is_name(node.value, owner)
        and node.attr == attr
    )


def _is_empty_list(node: ast.AST) -> bool:
    return isinstance(node, ast.List) and node.elts == []


def _is_list_run_report_ann(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and _is_name(node.value, "list")
        and _is_name(node.slice, "RunReport")
    )


def _assert_no_implicit_constructs(node: ast.AST) -> None:
    for child in ast.walk(node):
        assert not isinstance(
            child,
            ast.Lambda
            | ast.ListComp
            | ast.SetComp
            | ast.DictComp
            | ast.GeneratorExp
            | ast.Await
            | ast.Yield
            | ast.YieldFrom
            | ast.NamedExpr
            | ast.Starred
            | ast.JoinedStr
            | ast.FormattedValue,
        ), _dump(child)
        if isinstance(child, ast.Assign):
            for target in child.targets:
                assert not isinstance(target, ast.Attribute | ast.Subscript), _dump(
                    child
                )


def _assert_run_once_call(node: ast.AST) -> None:
    assert isinstance(node, ast.Call), _dump(node)
    assert _is_attr(node.func, "controller", "run_once"), _dump(node)
    assert node.args == []
    assert tuple(kw.arg for kw in node.keywords) == _RUN_ONCE_KW
    for keyword in node.keywords:
        assert keyword.arg is not None
        assert _is_name(keyword.value, keyword.arg), _dump(keyword)


def _assert_append_call(node: ast.AST) -> None:
    assert isinstance(node, ast.Expr), _dump(node)
    call = node.value
    assert isinstance(call, ast.Call), _dump(call)
    assert _is_attr(call.func, "reports", "append"), _dump(call)
    assert len(call.args) == 1 and _is_name(call.args[0], "report")
    assert call.keywords == []


def _assert_terminal_if(node: ast.AST) -> None:
    assert isinstance(node, ast.If), _dump(node)
    test = node.test
    assert isinstance(test, ast.Compare), _dump(test)
    assert _is_attr(test.left, "report", "outcome")
    assert len(test.ops) == 1 and isinstance(test.ops[0], ast.IsNot)
    assert len(test.comparators) == 1
    assert _is_attr(test.comparators[0], "RunOutcome", "SUCCEEDED")
    assert node.orelse == []
    assert len(node.body) == 1
    ret = node.body[0]
    assert isinstance(ret, ast.Return), _dump(ret)
    value = ret.value
    assert isinstance(value, ast.Call), _dump(value)
    assert _is_name(value.func, "tuple")
    assert len(value.args) == 1 and _is_name(value.args[0], "reports")
    assert value.keywords == []


def _assert_catch_up_source_shape() -> None:
    source = inspect.getsource(_controller_run.catch_up_source)
    tree = ast.parse(source)
    assert len(tree.body) == 1
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef) and fn.name == "catch_up_source"
    assert fn.decorator_list == []
    assert fn.args.posonlyargs == [] and fn.args.args == []
    assert fn.args.vararg is None and fn.args.kwarg is None
    assert fn.args.defaults == []
    assert tuple(arg.arg for arg in fn.args.kwonlyargs) == _RUN_ONCE_KW
    assert all(default is None for default in fn.args.kw_defaults)
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body = body[1:]
    assert len(body) == 2, [_dump(stmt) for stmt in body]
    assign, loop = body
    assert isinstance(assign, ast.AnnAssign), _dump(assign)
    assert assign.simple == 1 and _is_name(assign.target, "reports")
    assert _is_list_run_report_ann(assign.annotation)
    assert assign.value is not None and _is_empty_list(assign.value)
    assert isinstance(loop, ast.While), _dump(loop)
    assert isinstance(loop.test, ast.Constant) and loop.test.value is True
    assert loop.orelse == []
    assert len(loop.body) == 3, [_dump(stmt) for stmt in loop.body]
    bind, append, terminal = loop.body
    assert isinstance(bind, ast.Assign), _dump(bind)
    assert len(bind.targets) == 1 and _is_name(bind.targets[0], "report")
    _assert_run_once_call(bind.value)
    _assert_append_call(append)
    _assert_terminal_if(terminal)
    _assert_no_implicit_constructs(fn)
    extra_while = [
        n for n in ast.walk(fn) if isinstance(n, ast.While) and n is not loop
    ]
    extra_if = [n for n in ast.walk(fn) if isinstance(n, ast.If) and n is not terminal]
    extra_fn = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and n is not fn
    ]
    extra_ctrl = [n for n in ast.walk(fn) if isinstance(n, _EXTRA_CONTROL)]
    assert extra_while == []
    assert extra_if == []
    assert extra_fn == []
    assert extra_ctrl == []
    assert "flock" not in source and "run_with_lock" not in source


def _read_regular_nofollow(path: pathlib.Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _tree_snapshot(root: pathlib.Path) -> dict[str, tuple[object, ...]]:
    """lstat 递归快照：不跟随 symlink，收录类型/mode 与普通文件字节。"""
    snapshot: dict[str, tuple[object, ...]] = {}

    def add(rel: str, path: pathlib.Path) -> None:
        st = os.lstat(path)
        kind = stat.S_IFMT(st.st_mode)
        if stat.S_ISLNK(kind):
            snapshot[rel] = ("symlink", st.st_mode, os.readlink(path))
            return
        if stat.S_ISDIR(kind):
            snapshot[rel] = ("dir", st.st_mode)
            for name in sorted(os.listdir(path)):
                child = name if rel == "." else f"{rel}/{name}"
                add(child, path / name)
            return
        if stat.S_ISREG(kind):
            data = _read_regular_nofollow(path)
            snapshot[rel] = ("reg", st.st_mode, len(data), data)
            return
        snapshot[rel] = ("other", kind, st.st_mode, st.st_size)

    add(".", root)
    return snapshot


def _states(local) -> pathlib.Path:
    return pathlib.Path(local.yd_root) / "states" / SOURCE


def _wrap_run_once_snapshot_on_terminal(
    monkeypatch: pytest.MonkeyPatch,
    local,
    *,
    outcome: RunOutcome,
) -> list[tuple[dict[str, tuple[object, ...]], dict[str, tuple[object, ...]]]]:
    captured: list[
        tuple[dict[str, tuple[object, ...]], dict[str, tuple[object, ...]]]
    ] = []
    original = controller.run_once

    def wrapped(**kwargs):
        report = original(**kwargs)
        if report.cycle == CYCLE_T12 and report.outcome is outcome:
            captured.append(
                (
                    _tree_snapshot(_work(local, CYCLE_T12)),
                    _tree_snapshot(_states(local)),
                )
            )
        return report

    monkeypatch.setattr(controller, "run_once", wrapped)
    return captured


def _install_outer_finalizer_sentinel(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    import yd_producer.cleanup as cleanup_module

    def forbidden(*args, **kwargs):
        seen.append("finalize_failed_job")
        raise AssertionError("catch_up_source 外层不得调用 finalize_failed_job")

    monkeypatch.setattr(cleanup_module, "finalize_failed_job", forbidden)
    return seen


def _done(local, cycle: datetime) -> pathlib.Path:
    return (
        pathlib.Path(local.yd_root)
        / "output"
        / cycle.strftime("%Y%m%d%H")
        / SOURCE
        / "DONE"
    )


def _work(local, cycle: datetime) -> pathlib.Path:
    return (
        pathlib.Path(local.scratch_root).resolve()
        / "work"
        / SOURCE
        / cycle.strftime("%Y%m%d%H")
    )


def _log(local, cycle: datetime) -> pathlib.Path:
    return (
        pathlib.Path(local.yd_root)
        / "logs"
        / SOURCE
        / f"{cycle.strftime('%Y%m%d%H')}.log"
    )


def _lock_path(local) -> pathlib.Path:
    path = pathlib.Path(local.cron.lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _prepare(
    tmp_path: pathlib.Path,
    *,
    raw_cycles: tuple[datetime, ...] = (),
    outcomes: dict[str, FakeOutcome] | None = None,
    on_terminal=None,
    driver=None,
):
    config, local = write_config_local(tmp_path)
    write_variant(local, source=SOURCE)
    write_state(local, source=SOURCE, cycle=CYCLE)
    for cycle in raw_cycles:
        write_raw_cycle(local, source=SOURCE, cycle=cycle)
    state = HookState()
    driver = driver or InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes=outcomes or {},
        clock=__import__("run_once_fixtures", fromlist=["step_clock"]).step_clock(),
    )
    executor = bind_terminal_hook(driver, state, fake, on_terminal=on_terminal)
    return config, local, fake, driver, executor


def _catch_up(config, local, executor, driver):
    lock = _lock_path(local)
    result = runlock.run_with_lock(
        lock_path=lock,
        action=lambda: catch_up_source(
            config=config,
            local=local,
            source=SOURCE,
            executor=executor,
            driver=driver,
            poll_wait=lambda: None,
        ),
    )
    assert result.acquired is True
    return result.value, lock


def _success_three() -> dict[str, FakeOutcome]:
    return {
        JOB_T: success_outcome(),
        JOB_T12: success_outcome(),
        JOB_T24: success_outcome(),
    }


def test_three_complete_cycles_stop_at_next_gap(tmp_path: pathlib.Path) -> None:
    config, local, fake, driver, executor = _prepare(
        tmp_path,
        raw_cycles=(CYCLE, T_PLUS_12, T_PLUS_24),
        outcomes=_success_three(),
    )
    reports, _lock = _catch_up(config, local, executor, driver)

    assert isinstance(reports, tuple)
    assert [(r.cycle, r.outcome) for r in reports] == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.SUCCEEDED),
        (CYCLE_T36, RunOutcome.STOPPED),
    ]
    assert reports[-1].stop_reason is StopReason.RAW_INCOMPLETE
    assert [record.name for record in fake.submissions] == [JOB_T, JOB_T12, JOB_T24]
    assert executor.inflight_before_submit == [(), (), ()]
    assert fake.max_inflight == 1
    assert fake.inflight() == ()
    assert _done(local, CYCLE_T).is_file()
    assert _done(local, CYCLE_T12).is_file()
    assert _done(local, CYCLE_T24).is_file()
    assert not _done(local, CYCLE_T36).exists()


def test_five_complete_cycles_stop_at_first_gap(tmp_path: pathlib.Path) -> None:
    config, local, fake, driver, executor = _prepare(
        tmp_path,
        raw_cycles=(CYCLE_T, CYCLE_T12, CYCLE_T24, CYCLE_T36, CYCLE_T48),
        outcomes={
            JOB_T: success_outcome(),
            JOB_T12: success_outcome(),
            JOB_T24: success_outcome(),
            JOB_T36: success_outcome(),
            JOB_T48: success_outcome(),
        },
    )
    reports, _lock = _catch_up(config, local, executor, driver)

    assert [(r.cycle, r.outcome) for r in reports] == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.SUCCEEDED),
        (CYCLE_T36, RunOutcome.SUCCEEDED),
        (CYCLE_T48, RunOutcome.SUCCEEDED),
        (CYCLE_T60, RunOutcome.STOPPED),
    ]
    assert reports[-1].stop_reason is StopReason.RAW_INCOMPLETE
    assert [record.name for record in fake.submissions] == [
        JOB_T,
        JOB_T12,
        JOB_T24,
        JOB_T36,
        JOB_T48,
    ]
    assert executor.inflight_before_submit == [(), (), (), (), ()]
    assert fake.max_inflight == 1
    assert fake.inflight() == ()
    assert _done(local, CYCLE_T).is_file()
    assert _done(local, CYCLE_T12).is_file()
    assert _done(local, CYCLE_T24).is_file()
    assert _done(local, CYCLE_T36).is_file()
    assert _done(local, CYCLE_T48).is_file()
    assert not _done(local, CYCLE_T60).exists()


def test_dynamic_raw_arrival_is_not_a_frozen_startup_horizon(
    tmp_path: pathlib.Path,
) -> None:
    arrivals: list[str] = []

    def on_terminal(request, job_id) -> None:
        if request.cycle == CYCLE_T:
            write_raw_cycle(local, source=SOURCE, cycle=T_PLUS_12)
            arrivals.append("t12")
        elif request.cycle == CYCLE_T12:
            write_raw_cycle(local, source=SOURCE, cycle=T_PLUS_24)
            arrivals.append("t24")

    config, local, fake, driver, executor = _prepare(
        tmp_path,
        raw_cycles=(CYCLE,),
        outcomes=_success_three(),
        on_terminal=on_terminal,
    )
    reports, _lock = _catch_up(config, local, executor, driver)

    assert arrivals == ["t12", "t24"]
    assert [record.name for record in fake.submissions] == [JOB_T, JOB_T12, JOB_T24]
    assert [(r.cycle, r.outcome) for r in reports] == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.SUCCEEDED),
        (CYCLE_T36, RunOutcome.STOPPED),
    ]
    assert reports[-1].stop_reason is StopReason.RAW_INCOMPLETE
    assert fake.max_inflight == 1


def test_middle_gap_stops_then_resumes_across_calls(tmp_path: pathlib.Path) -> None:
    config, local, fake, driver, executor = _prepare(
        tmp_path,
        raw_cycles=(CYCLE, T_PLUS_24),
        outcomes=_success_three(),
    )
    first, _lock = _catch_up(config, local, executor, driver)

    assert [(r.cycle, r.outcome) for r in first] == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.STOPPED),
    ]
    assert first[-1].stop_reason is StopReason.RAW_INCOMPLETE
    assert [record.name for record in fake.submissions] == [JOB_T]
    assert _done(local, CYCLE_T).is_file()
    assert not _done(local, CYCLE_T12).exists()
    assert not _done(local, CYCLE_T24).exists()
    assert not _work(local, CYCLE_T24).exists()

    write_raw_cycle(local, source=SOURCE, cycle=T_PLUS_12)
    second, _lock = _catch_up(config, local, executor, driver)

    assert [(r.cycle, r.outcome) for r in second] == [
        (CYCLE_T12, RunOutcome.SUCCEEDED),
        (CYCLE_T24, RunOutcome.SUCCEEDED),
        (CYCLE_T36, RunOutcome.STOPPED),
    ]
    assert [record.name for record in fake.submissions] == [JOB_T, JOB_T12, JOB_T24]
    assert _done(local, CYCLE_T12).is_file()
    assert _done(local, CYCLE_T24).is_file()
    assert fake.max_inflight == 1


def test_initial_gap_does_not_skip_to_later_complete_raw(
    tmp_path: pathlib.Path,
) -> None:
    config, local, fake, driver, executor = _prepare(
        tmp_path,
        raw_cycles=(T_PLUS_12, T_PLUS_24),
        outcomes=_success_three(),
    )
    reports, _lock = _catch_up(config, local, executor, driver)

    assert isinstance(reports, tuple) and len(reports) == 1
    assert reports[0].cycle == CYCLE_T
    assert reports[0].outcome is RunOutcome.STOPPED
    assert reports[0].stop_reason is StopReason.RAW_INCOMPLETE
    assert fake.submissions == ()
    assert fake.max_inflight == 0
    assert not _done(local, CYCLE_T).exists()
    assert not _done(local, CYCLE_T12).exists()
    assert not _done(local, CYCLE_T24).exists()


def test_job_failed_on_second_round_stops_before_later_cycle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, local, fake, driver, executor = _prepare(
        tmp_path,
        raw_cycles=(CYCLE, T_PLUS_12, T_PLUS_24),
        outcomes={
            JOB_T: success_outcome(),
            JOB_T12: FakeOutcome(
                final_state=JobState.FAILED, polls_until_terminal=1, started=True
            ),
            JOB_T24: success_outcome(),
        },
    )
    snapshots = _wrap_run_once_snapshot_on_terminal(
        monkeypatch, local, outcome=RunOutcome.JOB_FAILED
    )
    finalizer_calls = _install_outer_finalizer_sentinel(monkeypatch)
    reports, _lock = _catch_up(config, local, executor, driver)

    assert [(r.cycle, r.outcome) for r in reports] == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.JOB_FAILED),
    ]
    assert [record.name for record in fake.submissions] == [JOB_T, JOB_T12]
    assert _done(local, CYCLE_T).is_file()
    assert not _done(local, CYCLE_T12).exists()
    assert not _done(local, CYCLE_T24).exists()
    work = _work(local, CYCLE_T12)
    assert work.exists()
    assert not _work(local, CYCLE_T).exists()
    assert not _log(local, CYCLE_T12).exists()
    assert snapshots != []
    work_snap, state_snap = snapshots[0]
    assert _tree_snapshot(work) == work_snap
    assert _tree_snapshot(_states(local)) == state_snap
    assert finalizer_calls == []
    _assert_catch_up_source_shape()


def test_cleanup_pending_on_second_round_stops_and_keeps_evidence(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yd_producer.publish as publish_module

    config, local, fake, driver, executor = _prepare(
        tmp_path,
        raw_cycles=(CYCLE, T_PLUS_12, T_PLUS_24),
        outcomes=_success_three(),
    )
    original_remove = publish_module.remove_tree_allow_symlinks

    def failing_t12_work(*args, **kwargs):
        if any("2026082700" in str(arg) for arg in args):
            raise OSError(1, "injected T+12 work removal failure")
        return original_remove(*args, **kwargs)

    monkeypatch.setattr(publish_module, "remove_tree_allow_symlinks", failing_t12_work)
    snapshots = _wrap_run_once_snapshot_on_terminal(
        monkeypatch, local, outcome=RunOutcome.SUCCEEDED_CLEANUP_PENDING
    )
    finalizer_calls = _install_outer_finalizer_sentinel(monkeypatch)
    reports, _lock = _catch_up(config, local, executor, driver)

    assert [(r.cycle, r.outcome) for r in reports] == [
        (CYCLE_T, RunOutcome.SUCCEEDED),
        (CYCLE_T12, RunOutcome.SUCCEEDED_CLEANUP_PENDING),
    ]
    assert [record.name for record in fake.submissions] == [JOB_T, JOB_T12]
    assert reports[-1].published is None
    assert reports[-1].done_path is not None and reports[-1].done_path.is_file()
    assert _done(local, CYCLE_T12).is_file()
    work = _work(local, CYCLE_T12)
    assert work.exists()
    assert not _done(local, CYCLE_T24).exists()
    assert not _log(local, CYCLE_T12).exists()
    assert snapshots != []
    work_snap, state_snap = snapshots[0]
    assert _tree_snapshot(work) == work_snap
    assert _tree_snapshot(_states(local)) == state_snap
    assert finalizer_calls == []
    _assert_catch_up_source_shape()


def test_second_round_run_error_identity_propagates(tmp_path: pathlib.Path) -> None:
    injected: list[RunError] = []
    config, local, fake, driver, executor = _prepare(
        tmp_path,
        raw_cycles=(CYCLE, T_PLUS_12, T_PLUS_24),
        outcomes=_success_three(),
    )
    cause = RuntimeError("injected poll_wait cause")

    def poll_wait() -> None:
        if len(fake.submissions) == 2:
            error = RunError(
                "second-round injected",
                phase="poll",
                source=SOURCE,
                cycle=CYCLE_T12,
                job_id=fake.submissions[1].job_id,
            )
            injected.append(error)
            raise error from cause

    lock = _lock_path(local)
    with pytest.raises(RunError) as excinfo:
        runlock.run_with_lock(
            lock_path=lock,
            action=lambda: catch_up_source(
                config=config,
                local=local,
                source=SOURCE,
                executor=executor,
                driver=driver,
                poll_wait=poll_wait,
            ),
        )
    assert injected and excinfo.value is injected[0]
    assert excinfo.value.phase == "poll"
    assert excinfo.value.source == SOURCE
    assert excinfo.value.cycle == CYCLE_T12
    assert excinfo.value.job_id == fake.submissions[1].job_id
    assert excinfo.value.__cause__ is cause
    assert [record.name for record in fake.submissions] == [JOB_T, JOB_T12]
    assert _done(local, CYCLE_T).is_file()
    assert not _done(local, CYCLE_T24).exists()


def test_keyboard_interrupt_propagates_unwrapped(tmp_path: pathlib.Path) -> None:
    config, local, fake, driver, executor = _prepare(
        tmp_path,
        raw_cycles=(CYCLE, T_PLUS_12, T_PLUS_24),
        outcomes=_success_three(),
    )

    def poll_wait() -> None:
        if len(fake.submissions) == 2:
            raise KeyboardInterrupt()

    lock = _lock_path(local)
    with pytest.raises(KeyboardInterrupt):
        runlock.run_with_lock(
            lock_path=lock,
            action=lambda: catch_up_source(
                config=config,
                local=local,
                source=SOURCE,
                executor=executor,
                driver=driver,
                poll_wait=poll_wait,
            ),
        )
    assert [record.name for record in fake.submissions] == [JOB_T, JOB_T12]
    assert not _done(local, CYCLE_T24).exists()


def test_lock_covers_entire_catch_up_until_terminal_stop(
    tmp_path: pathlib.Path,
) -> None:
    inner_calls: list[int] = []

    def inner() -> str:
        inner_calls.append(1)
        return "inner-ran"

    config, local, fake, driver, executor = _prepare(
        tmp_path,
        raw_cycles=(CYCLE, T_PLUS_12, T_PLUS_24),
        outcomes={
            JOB_T: success_outcome(),
            JOB_T12: success_outcome(polls_until_terminal=2),
            JOB_T24: success_outcome(),
        },
    )
    lock = _lock_path(local)
    import yd_producer.publish as publish_module

    original_publish = publish_module.publish
    probes: list[str] = []

    def poll_wait() -> None:
        if len(fake.submissions) >= 2:
            probes.append("poll")
            concurrent = runlock.run_with_lock(lock_path=lock, action=inner)
            assert concurrent.acquired is False
            assert concurrent.value is None
            assert inner_calls == []

    def publishing(inputs):
        if inputs.cycle == CYCLE_T12:
            probes.append("publish")
            concurrent = runlock.run_with_lock(lock_path=lock, action=inner)
            assert concurrent.acquired is False
            assert concurrent.value is None
            assert inner_calls == []
        return original_publish(inputs)

    publish_module.publish = publishing
    try:

        def action():
            reports = catch_up_source(
                config=config,
                local=local,
                source=SOURCE,
                executor=executor,
                driver=driver,
                poll_wait=poll_wait,
            )
            still = runlock.run_with_lock(lock_path=lock, action=inner)
            assert still.acquired is False
            assert still.value is None
            assert inner_calls == []
            return reports

        outer = runlock.run_with_lock(lock_path=lock, action=action)
    finally:
        publish_module.publish = original_publish

    assert outer.acquired is True
    reports = outer.value
    assert reports[-1].outcome is RunOutcome.STOPPED
    assert reports[-1].cycle == CYCLE_T36
    assert "poll" in probes and "publish" in probes
    assert inner_calls == []
    assert lock.is_file()
    after = runlock.run_with_lock(lock_path=lock, action=inner)
    assert after.acquired is True and after.value == "inner-ran"
    assert inner_calls == [1]
    assert lock.is_file()


def test_public_api_is_keyword_only_tuple_and_exported() -> None:
    sig = inspect.signature(controller.catch_up_source)
    params = sig.parameters
    assert tuple(params) == (
        "config",
        "local",
        "source",
        "executor",
        "driver",
        "poll_wait",
    )
    for param in params.values():
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty
    raw = controller.catch_up_source.__annotations__
    assert raw["return"] == "tuple[RunReport, ...]"
    assert "catch_up_source" in controller.__all__

    once = inspect.signature(controller.run_once)
    assert tuple(once.parameters) == tuple(params)
    for param in once.parameters.values():
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty
    assert controller.run_once.__annotations__["return"] == "RunReport"

    with pytest.raises(TypeError):
        controller.catch_up_source(  # type: ignore[misc]
            object(), object(), SOURCE, object(), object(), lambda: None
        )


def test_composition_reuses_public_run_once_seam(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    original = controller.run_once

    def spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(controller, "run_once", spy)
    config, local, _fake, driver, executor = _prepare(
        tmp_path,
        raw_cycles=(CYCLE, T_PLUS_12, T_PLUS_24),
        outcomes=_success_three(),
    )
    waits: list[str] = []

    def poll_wait() -> None:
        waits.append("wait")

    lock = _lock_path(local)
    result = runlock.run_with_lock(
        lock_path=lock,
        action=lambda: catch_up_source(
            config=config,
            local=local,
            source=SOURCE,
            executor=executor,
            driver=driver,
            poll_wait=poll_wait,
        ),
    )
    reports = result.value
    assert len(calls) == len(reports) == 4
    for kwargs in calls:
        assert kwargs["config"] is config
        assert kwargs["local"] is local
        assert kwargs["source"] is SOURCE
        assert kwargs["executor"] is executor
        assert kwargs["driver"] is driver
        assert kwargs["poll_wait"] is poll_wait

    _assert_catch_up_source_shape()
