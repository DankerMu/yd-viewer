"""Issue #86：共享 `output/` 根 ENOENT/ENOTDIR 是根异常，不是全新链。

公开 seam：`decide_frontier`、`plan_residue`（停止 decision vs 手交可跑 T）、`run_once`。
真实树快照 + raw/plan/execute/submit 零调用。不得在产品代码或 `YdRootBuilder`
构造器里偷偷 mkdir `output/`。
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest
from frontier_fixtures import (
    RecordingRawComplete,
    YdRootBuilder,
    parse_cycle,
    snapshot_tree,
)
from run_once_fixtures import (
    HookState,
    InProcessDriver,
    step_clock,
    write_config_local,
    write_state,
    write_variant,
)

from yd_producer import controller, residue
from yd_producer.controller import RunError, RunOutcome, run_once
from yd_producer.executor import FakeJobExecutor

D = "2026082600"
T = "2026082612"
T_PLUS_12 = "2026082700"
T_PLUS_24 = "2026082712"
FRESH = "2026082000"
FRESH_NEXT = "2026082012"
ALL_CYCLES = frozenset({D, T, T_PLUS_12, T_PLUS_24, FRESH, FRESH_NEXT})
ALL_SOURCES = ("ifs", "gfs")


def _all_complete() -> RecordingRawComplete:
    return RecordingRawComplete(set(ALL_CYCLES))


def _yd_root(tmp_path: Path) -> Path:
    root = tmp_path.resolve() / "yd"
    root.mkdir()
    return root


def _occupy_output_as_file(root: Path) -> Path:
    output = root / "output"
    output.write_bytes(b"not-a-directory")
    return output


def _multi_state_tree(root: Path, *, occupy: str) -> YdRootBuilder:
    builder = YdRootBuilder(root=root)
    for source in ALL_SOURCES:
        builder.write_state(T, source)
        builder.write_state(T_PLUS_12, source)
        builder.write_state(T_PLUS_24, source)
    if occupy == "file":
        _occupy_output_as_file(root)
    else:
        assert not (root / "output").exists()
    return builder


def _handed_runnable(source: str, cycle_text: str) -> controller.FrontierDecision:
    return controller.FrontierDecision(
        source=source,
        cycle=parse_cycle(cycle_text),
        stop_reason=None,
        detail=f"{source}: 手交可跑 T={cycle_text}",
    )


def _assert_stopped_unreadable(
    decision: controller.FrontierDecision, *, source: str, output: Path
) -> None:
    assert decision.source == source
    assert decision.stop_reason is controller.StopReason.DISCOVERY_UNREADABLE
    assert decision.cycle is None
    assert decision.runnable is False
    assert str(output) in decision.detail


def _probe_residue_and_raw() -> tuple[list[str], object, object]:
    import yd_producer.rawscan as rawscan_module
    import yd_producer.residue as residue_module

    probes: list[str] = []
    original_plan = residue_module.plan_residue
    original_execute = residue_module.execute_residue_plan
    original_judge = rawscan_module.judge

    def probe_plan(*args, **kwargs):
        probes.append("plan")
        return original_plan(*args, **kwargs)

    def probe_execute(*args, **kwargs):
        probes.append("execute")
        return original_execute(*args, **kwargs)

    def probe_judge(*args, **kwargs):
        probes.append("judge")
        return original_judge(*args, **kwargs)

    residue_module.plan_residue = probe_plan  # type: ignore[assignment]
    residue_module.execute_residue_plan = probe_execute  # type: ignore[assignment]
    rawscan_module.judge = probe_judge  # type: ignore[assignment]
    return (
        probes,
        residue_module,
        rawscan_module,
        original_plan,
        original_execute,
        original_judge,
    )


class _CountingDriver:
    def __init__(self) -> None:
        self.prepare_calls = 0
        self.collect_calls = 0

    def prepare(self, *, request):
        self.prepare_calls += 1
        return InProcessDriver(HookState()).prepare(request=request)

    def collect(self, *, attempt, terminal_record):
        self.collect_calls += 1
        raise RuntimeError("collect 未编排")


class _DisappearingIterdir:
    """入口时 `output/` 仍是真实目录；实际 `iterdir()` 或懒迭代窗口抛 ENOENT/ENOTDIR。"""

    def __init__(self, target: Path, *, mode: str, original) -> None:
        self.target = target.resolve()
        self.mode = mode
        self.original = original
        self.calls = 0

    def __call__(self, directory: Path):
        if directory.resolve() != self.target:
            return self.original(directory)
        self.calls += 1
        errno_code = errno.ENOENT if self.mode.endswith("enoent") else errno.ENOTDIR
        error_type = (
            FileNotFoundError if errno_code == errno.ENOENT else NotADirectoryError
        )
        error = error_type(errno_code, os.strerror(errno_code), str(directory))
        if self.mode.startswith("call"):
            raise error

        def _lazy():
            real = self.original(directory)
            first = next(iter(real), None)
            if first is not None:
                yield first
            raise error

        return _lazy()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        target = self

        def fake_iterdir(directory: Path):
            return target(directory)

        monkeypatch.setattr(Path, "iterdir", fake_iterdir)


@pytest.mark.parametrize("occupy", ["missing", "file"])
@pytest.mark.parametrize("source", ALL_SOURCES)
def test_missing_or_file_output_stops_each_source_before_raw(
    tmp_path: Path, occupy: str, source: str
) -> None:
    root = _yd_root(tmp_path)
    _multi_state_tree(root, occupy=occupy)
    output = root / "output"
    raw = _all_complete()
    before = snapshot_tree(root)

    decision = controller.decide_frontier(yd_root=root, source=source, raw_complete=raw)

    _assert_stopped_unreadable(decision, source=source, output=output)
    assert raw.asked == []
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("occupy", ["missing", "file"])
def test_plan_residue_returns_none_for_stopped_root_decision(
    tmp_path: Path, occupy: str
) -> None:
    root = _yd_root(tmp_path)
    builder = _multi_state_tree(root, occupy=occupy)
    raw = _all_complete()
    before = snapshot_tree(root)
    execute_calls: list[object] = []
    original_execute = residue.execute_residue_plan

    def probe_execute(plan):
        execute_calls.append(plan)
        return original_execute(plan)

    residue.execute_residue_plan = probe_execute  # type: ignore[assignment]
    try:
        decision = controller.decide_frontier(
            yd_root=root, source="ifs", raw_complete=raw
        )
        _assert_stopped_unreadable(decision, source="ifs", output=root / "output")
        plan = residue.plan_residue(yd_root=root, source="ifs", decision=decision)
        assert plan is None
        assert execute_calls == []
        assert snapshot_tree(root) == before
        assert builder.state_path(T, "ifs").is_file()
        assert builder.state_path(T_PLUS_12, "ifs").is_file()
        assert builder.state_path(T_PLUS_24, "ifs").is_file()
    finally:
        residue.execute_residue_plan = original_execute  # type: ignore[assignment]


@pytest.mark.parametrize("occupy", ["missing", "file"])
def test_handed_runnable_plan_raises_residue_error_without_plan(
    tmp_path: Path, occupy: str
) -> None:
    root = _yd_root(tmp_path)
    builder = _multi_state_tree(root, occupy=occupy)
    before = snapshot_tree(root)
    execute_calls: list[object] = []
    original_execute = residue.execute_residue_plan

    def probe_execute(plan):
        execute_calls.append(plan)
        return original_execute(plan)

    residue.execute_residue_plan = probe_execute  # type: ignore[assignment]
    try:
        handed = _handed_runnable("ifs", T)
        with pytest.raises(residue.ResidueError, match="残留判定无法完成") as excinfo:
            residue.plan_residue(yd_root=root, source="ifs", decision=handed)
        assert execute_calls == []
        assert snapshot_tree(root) == before
        assert builder.state_path(T_PLUS_12, "ifs").is_file()
        assert builder.state_path(T_PLUS_24, "ifs").is_file()
        assert str(root / "output") in str(excinfo.value)
    finally:
        residue.execute_residue_plan = original_execute  # type: ignore[assignment]


@pytest.mark.parametrize("occupy", ["missing", "file"])
@pytest.mark.parametrize("source", ALL_SOURCES)
def test_run_once_stops_at_frontier_not_residue_error(
    tmp_path: Path, occupy: str, source: str
) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local, source=source)
    write_state(local, source=source)
    write_state(
        local,
        source=source,
        cycle=parse_cycle(T_PLUS_12),
    )
    root = Path(local.yd_root)
    output = root / "output"
    if occupy == "missing":
        output.rmdir()
        assert not output.exists()
    else:
        output.rmdir()
        _occupy_output_as_file(root)
    before = snapshot_tree(root)

    (
        probes,
        residue_module,
        rawscan_module,
        original_plan,
        original_execute,
        original_judge,
    ) = _probe_residue_and_raw()
    driver = _CountingDriver()
    fake = FakeJobExecutor(outcomes={}, clock=step_clock())
    try:
        report = run_once(
            config=config,
            local=local,
            source=source,
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    except RunError as error:
        pytest.fail(f"root ENOENT/ENOTDIR 不得迟到 residue：{error!r}")
    finally:
        residue_module.plan_residue = original_plan  # type: ignore[assignment]
        residue_module.execute_residue_plan = original_execute  # type: ignore[assignment]
        rawscan_module.judge = original_judge  # type: ignore[assignment]

    assert report.outcome is RunOutcome.STOPPED
    assert report.stop_reason is controller.StopReason.DISCOVERY_UNREADABLE
    assert report.cycle is None
    assert report.job is None
    assert str(output) in report.detail
    assert probes == []
    assert driver.prepare_calls == 0
    assert fake.submissions == ()
    assert snapshot_tree(root) == before


@pytest.mark.parametrize(
    "mode", ["call_enoent", "call_enotdir", "lazy_enoent", "lazy_enotdir"]
)
def test_output_disappearing_during_iterdir_is_root_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """入口时根仍存在；exists()/is_dir() 预检会放过，实际枚举窗口必须 STOP。"""
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    output = root / "output"
    output.mkdir()
    (output / "keep-me").write_bytes(b"present-at-entry")
    builder.write_state(T, "ifs")
    builder.write_state(T_PLUS_12, "ifs")
    before = snapshot_tree(root)
    assert output.is_dir()

    disappearing = _DisappearingIterdir(output, mode=mode, original=Path.iterdir)
    disappearing.install(monkeypatch)

    raw = _all_complete()
    decision = controller.decide_frontier(yd_root=root, source="ifs", raw_complete=raw)
    _assert_stopped_unreadable(decision, source="ifs", output=output)
    assert raw.asked == []
    assert disappearing.calls >= 1

    monkeypatch.setattr(Path, "iterdir", disappearing.original)
    handed = _handed_runnable("ifs", T)
    disappearing.install(monkeypatch)
    with pytest.raises(residue.ResidueError, match="残留判定无法完成"):
        residue.plan_residue(yd_root=root, source="ifs", decision=handed)

    monkeypatch.setattr(Path, "iterdir", disappearing.original)
    assert snapshot_tree(root) == before
    assert builder.state_path(T, "ifs").is_file()
    assert builder.state_path(T_PLUS_12, "ifs").is_file()


@pytest.mark.parametrize("mode", ["call_enoent", "lazy_enotdir"])
def test_run_once_stops_when_output_disappears_during_iterdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    root = Path(local.yd_root)
    output = root / "output"
    assert output.is_dir()
    before = snapshot_tree(root)

    disappearing = _DisappearingIterdir(output, mode=mode, original=Path.iterdir)
    disappearing.install(monkeypatch)
    (
        probes,
        residue_module,
        rawscan_module,
        original_plan,
        original_execute,
        original_judge,
    ) = _probe_residue_and_raw()
    driver = _CountingDriver()
    fake = FakeJobExecutor(outcomes={}, clock=step_clock())
    try:
        report = run_once(
            config=config,
            local=local,
            source="gfs",
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    except RunError as error:
        pytest.fail(f"枚举窗口根消失不得迟到 residue：{error!r}")
    finally:
        residue_module.plan_residue = original_plan  # type: ignore[assignment]
        residue_module.execute_residue_plan = original_execute  # type: ignore[assignment]
        rawscan_module.judge = original_judge  # type: ignore[assignment]
        monkeypatch.setattr(Path, "iterdir", disappearing.original)

    assert report.outcome is RunOutcome.STOPPED
    assert report.stop_reason is controller.StopReason.DISCOVERY_UNREADABLE
    assert report.cycle is None
    assert probes == []
    assert driver.prepare_calls == 0
    assert fake.submissions == ()
    assert snapshot_tree(root) == before


def test_enumerable_empty_output_with_one_state_takes_earliest(
    tmp_path: Path,
) -> None:
    builder = YdRootBuilder(tmp_path)
    (tmp_path / "output").mkdir()
    builder.write_state(FRESH, "ifs")
    raw = _all_complete()
    decision = controller.decide_frontier(
        yd_root=builder.root, source="ifs", raw_complete=raw
    )
    assert decision.stop_reason is None
    assert decision.cycle == parse_cycle(FRESH)
    assert raw.asked == [FRESH]


def test_enumerable_empty_output_with_later_states_still_cleans_residue(
    tmp_path: Path,
) -> None:
    root = _yd_root(tmp_path)
    builder = YdRootBuilder(root=root)
    (root / "output").mkdir()
    builder.write_state(FRESH, "ifs")
    builder.write_state(FRESH_NEXT, "ifs")
    decision = controller.decide_frontier(
        yd_root=root, source="ifs", raw_complete=_all_complete()
    )
    assert decision.cycle == parse_cycle(FRESH)
    plan = residue.plan_residue(yd_root=root, source="ifs", decision=decision)
    assert plan is not None
    assert plan.state_files == (builder.state_path(FRESH_NEXT, "ifs"),)
    residue.execute_residue_plan(plan)
    assert builder.state_path(FRESH, "ifs").is_file()
    assert not builder.state_path(FRESH_NEXT, "ifs").exists()


def test_missing_states_root_is_still_no_initial_state(tmp_path: Path) -> None:
    builder = YdRootBuilder(tmp_path)
    (tmp_path / "output").mkdir()
    assert not builder.states_dir("ifs").exists()
    raw = _all_complete()
    decision = controller.decide_frontier(
        yd_root=builder.root, source="ifs", raw_complete=raw
    )
    assert decision.stop_reason is controller.StopReason.NO_INITIAL_STATE
    assert decision.cycle is None
    assert raw.asked == []


def test_subordinate_done_missing_or_not_dir_does_not_count_or_stop_sibling(
    tmp_path: Path,
) -> None:
    builder = YdRootBuilder(tmp_path)
    builder.write_done(D, "gfs")
    builder.write_state(T, "gfs")
    builder.write_state(FRESH, "ifs")
    cycle_file = tmp_path / "output" / T
    cycle_file.write_bytes(b"not-a-cycle-dir")
    raw = _all_complete()

    gfs = controller.decide_frontier(
        yd_root=builder.root, source="gfs", raw_complete=raw
    )
    ifs = controller.decide_frontier(
        yd_root=builder.root, source="ifs", raw_complete=raw
    )
    assert gfs.cycle == parse_cycle(T)
    assert ifs.cycle == parse_cycle(FRESH)
    assert raw.asked == [T, FRESH]
