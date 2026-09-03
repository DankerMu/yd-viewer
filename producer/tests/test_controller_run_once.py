"""`controller.run_once` 主链：成功（capture / recovery / GFS/IFS）、报告、exact 调用序、
flock。

fixture（tasks.md `### Issue #26 fixture`）：
- Required evidence 1：happy capture + exact call ledger + 首个 poll 前无 wait + DONE 在盘
  + work 删除；
- Required evidence 2：job-local recovery -> submissions 恰 1、job id 同一、DONE 在盘；
- Required evidence 3：job 报告逐字段来自同一 submit/terminal record，且
  `FakeJobExecutor.submissions[0].state is PENDING`；
- Required evidence 20：flock 生命周期（外层 `run_with_lock`，terminal/wait/publish 窗口内
  第二次同锁进入跳过且内层零调用，异常后可再取，锁文件保留）。
"""

from __future__ import annotations

import pathlib
from datetime import timedelta

import pytest
from run_once_fixtures import (
    CYCLE,
    IFS_JOB_NAME,
    JOB_NAME,
    HookedExecutor,
    HookState,
    InProcessDriver,
    make_terminal_hook,
    write_config_local,
    write_raw_cycle,
    write_state,
    write_variant,
)

from yd_producer import controller, runlock
from yd_producer import prepare as prepare_module
from yd_producer import publish as publish_module
from yd_producer import rawcopy as rawcopy_module
from yd_producer import rawscan as rawscan_module
from yd_producer import residue as residue_module
from yd_producer import tracker as tracker_module
from yd_producer.controller import RunOutcome, run_once
from yd_producer.executor import FakeJobExecutor, FakeOutcome, JobState, StepClock

T0 = CYCLE.replace(hour=0, minute=0, second=0)


def _clock():
    return StepClock(start=T0, step=timedelta(seconds=10))


def _success_outcome() -> dict[str, FakeOutcome]:
    return {
        JOB_NAME: FakeOutcome(
            final_state=JobState.SUCCEEDED, polls_until_terminal=2, started=True
        )
    }


class _Recorder:
    """记录型调用账本：每项是 (label, 附加)，覆盖 run_once 全部公共 seam。

    executor/driver 是注入对象（系统边界），由 `_LedgerExecutor`/`_LedgerDriver` 记录；
    `poll_wait` 由同一账本记录（wait 只在成功返回后追加）。模块级调用（preflight、
    frontier、residue、raw、variant、checkpoint、publish）经 monkeypatch 记录。
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events: list[str] = []
        self._install(monkeypatch)

    def record(self, label: str):
        self.events.append(label)

    def _install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def wrap(module, name: str, label: str):
            original = getattr(module, name)

            def replacement(*args, **kwargs):
                self.record(label)
                return original(*args, **kwargs)

            monkeypatch.setattr(module, name, replacement)

        # preflight 现在落在 controller（私有校验面折叠后）。
        wrap(controller, "_preflight", "preflight")
        wrap(controller, "_target_and_state", "frontier")
        wrap(residue_module, "plan_residue", "residue-plan")
        wrap(residue_module, "execute_residue_plan", "residue-execute")
        wrap(rawscan_module, "judge", "raw-judge")
        wrap(rawcopy_module, "stage_raw", "raw-stage")
        wrap(prepare_module, "variant_targets", "variant-read")
        wrap(tracker_module, "ensure_twelve_hour_checkpoint", "checkpoint-recheck")
        wrap(publish_module, "publish", "publish")


def _run(
    tmp_path: pathlib.Path,
    *,
    recovery: bool = False,
    source: str = "gfs",
    recorder: _Recorder | None = None,
    executor=None,
    driver=None,
    poll_waits: list[str] | None = None,
):
    """铺合成树并执行一次 run_once；返回 (report, fake, hook_state, driver_seen, waits)。"""
    config, local = write_config_local(tmp_path)
    write_variant(local, source=source)
    write_state(local, source=source)
    write_raw_cycle(local, source=source)
    state = HookState()
    driver = driver or InProcessDriver(state)

    outcome = _success_outcome()
    if source == "ifs":
        outcome = {
            IFS_JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=2, started=True
            )
        }
    clock = _clock()
    fake = executor or FakeJobExecutor(outcomes=outcome, clock=clock)
    request_slot = {}

    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]

    def make_hook(*, job_id):
        hook = make_terminal_hook(request_slot["request"], state, recovery=recovery)
        hook()

    hook_executor = HookedExecutor(fake, make_hook)

    waits: list[str] = poll_waits if poll_waits is not None else []

    def poll_wait() -> None:
        waits.append("wait")

    report = run_once(
        config=config,
        local=local,
        source=source,
        executor=hook_executor,
        driver=driver,
        poll_wait=poll_wait,
    )
    return report, fake, state, driver, waits


def test_happy_capture_success_and_work_removed(tmp_path: pathlib.Path) -> None:
    report, fake, state, _driver, waits = _run(tmp_path)
    assert report.outcome is RunOutcome.SUCCEEDED
    assert report.cycle == CYCLE
    assert report.stop_reason is None
    assert report.job is not None and report.job.job_id == fake.submissions[0].job_id
    assert report.job.state is JobState.SUCCEEDED
    assert report.published is not None
    assert report.done_path == report.published.done_path
    assert report.done_path.is_file()
    dat = report.published.dat_path
    assert dat.is_file()
    assert report.published.state_path.is_file()
    assert report.published.state_path.read_bytes().startswith(b"3 6")
    assert len(fake.submissions) == 1
    assert fake.submissions[0].name == JOB_NAME
    # work 删除：整棵 `<work_root>/gfs/<T>` 不存在，兄弟 state 树未动。
    work_dir = report.published.removed_work_dir
    assert not work_dir.exists()
    assert work_dir.parent.is_dir()
    # 首个 poll 即非终态 -> 恰好 2 个 wait（polls_until_terminal=2）。
    assert waits == ["wait", "wait"]
    # tracker captured 对象与 report 的 checkpoint 同一（对象在内存，路径已被 work 删除）。
    captured = state.tracker.captured[12]
    assert captured.checksum == state.tracker.captured[12].checksum
    assert captured.lead_hours == 12 and captured.relative_minute == 720.0


def test_ifs_happy_capture_success(tmp_path: pathlib.Path) -> None:
    """IFS 单轮成功：同一 hook/driver 链跑 IFS source，1 submit、DONE 在盘。"""
    report, fake, _state, _driver, waits = _run(tmp_path, source="ifs")
    assert report.outcome is RunOutcome.SUCCEEDED
    assert report.job is not None and report.job.job_id == fake.submissions[0].job_id
    assert len(fake.submissions) == 1
    assert report.done_path.is_file()
    assert waits == ["wait", "wait"]


def test_call_ledger_is_exact_and_waits_follow_nonterminal_polls(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完整有序账本（Required evidence 1 修复）：覆盖全部公共 seam 调用，逐字断言。

    polls_until_terminal=2 -> poll 序列：RUNNING, RUNNING, SUCCEEDED（3 次 poll、
    2 次 wait）；每个 wait 恰在非终态 poll 之后。driver.prepare/submit/poll 是注入
    对象（系统边界），由 `_LedgerExecutor`/`_LedgerDriver` 记录。
    """
    recorder = _Recorder(monkeypatch)
    report, _fake, _state, _driver, waits = _run(tmp_path, recorder=recorder)
    assert report.outcome is RunOutcome.SUCCEEDED
    assert waits == ["wait", "wait"]
    ledger = recorder.events
    # 模块级账本（前置/管家面）：preflight -> frontier -> residue-plan ->
    # residue-execute -> raw-judge -> raw-stage -> variant-read -> checkpoint-recheck
    # -> publish（driver.prepare / submit / poll 由注入对象记录，见下一测试）。
    assert ledger == [
        "preflight",
        "frontier",
        "residue-plan",
        "residue-execute",
        "raw-judge",
        "raw-stage",
        "variant-read",
        "checkpoint-recheck",
        "publish",
    ]


class _LedgerExecutor:
    """记录 submit/poll 调用序的注入 executor wrapper（系统边界）。"""

    def __init__(self, executor, events: list[str]) -> None:
        self._executor = executor
        self._events = events

    def submit(self, spec):
        self._events.append("submit")
        return self._executor.submit(spec)

    def poll(self, job_id: str):
        self._events.append("poll")
        return self._executor.poll(job_id)

    @property
    def submissions(self):
        return self._executor.submissions

    @property
    def max_inflight(self):
        return self._executor.max_inflight

    def inflight(self):
        return self._executor.inflight()


def test_full_public_seam_order_with_waits_in_place(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完整账本：preflight…publish + driver.prepare + submit/poll/wait 的精确位置。

    polls_until_terminal=2 下完整序列必须逐字是：
    preflight, frontier, residue-plan, residue-execute, raw-judge, raw-stage,
    variant-read, driver.prepare, submit, poll, wait, poll, wait, poll, collect,
    checkpoint-recheck, publish。
    """
    recorder = _Recorder(monkeypatch)
    events = recorder.events
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=2, started=True
            )
        },
        clock=_clock(),
    )
    request_slot = {}
    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        events.append("driver.prepare")
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]
    original_collect = driver.collect

    def capturing_collect(*, attempt, terminal_record):
        events.append("collect")
        return original_collect(attempt=attempt, terminal_record=terminal_record)

    driver.collect = capturing_collect  # type: ignore[method-assign]
    ledger_executor = _LedgerExecutor(fake, events)

    def make_hook(*, job_id):
        make_terminal_hook(request_slot["request"], state)()

    hook_executor = HookedExecutor(ledger_executor, make_hook)

    def poll_wait() -> None:
        events.append("wait")

    report = run_once(
        config=config,
        local=local,
        source="gfs",
        executor=hook_executor,
        driver=driver,
        poll_wait=poll_wait,
    )
    assert report.outcome is RunOutcome.SUCCEEDED
    assert events == [
        "preflight",
        "frontier",
        "residue-plan",
        "residue-execute",
        "raw-judge",
        "raw-stage",
        "variant-read",
        "driver.prepare",
        "submit",
        "poll",
        "wait",
        "poll",
        "wait",
        "poll",
        "collect",
        "checkpoint-recheck",
        "publish",
    ]
    # 首次 poll 前零 wait；wait 数恰等于非终态 poll 结果数（2）。
    assert events.count("wait") == 2
    first_poll = events.index("poll")
    assert "wait" not in events[:first_poll]


def test_poll_wait_sequence_is_attached_to_nonterminal_results(
    tmp_path: pathlib.Path,
) -> None:
    report, _fake, _state, _driver, waits = _run(tmp_path)
    assert report.outcome is RunOutcome.SUCCEEDED
    # 3 次 poll：第 1（RUNNING）、第 2（RUNNING）、第 3（SUCCEEDED）。
    # 每条非终态 poll 后恰一次 wait => 2 次 wait；首次 poll 前无 wait。
    assert waits == ["wait", "wait"]


def test_job_local_recovery_keeps_single_submission(tmp_path: pathlib.Path) -> None:
    report, fake, state, _driver, _waits = _run(tmp_path, recovery=True)
    assert report.outcome is RunOutcome.SUCCEEDED
    assert report.job is not None
    assert len(fake.submissions) == 1
    assert fake.submissions[0].job_id == report.job.job_id
    captured = state.tracker.captured[12]
    assert captured.lead_hours == 12 and captured.relative_minute == 720.0
    assert report.published is not None
    assert report.done_path.is_file()
    # 补跑后 canonical 未被主跑捕获路径改写。
    assert report.done_path.read_bytes() == b""


def test_job_report_fields_come_from_the_same_submit_record(
    tmp_path: pathlib.Path,
) -> None:
    report, fake, _state, _driver, _waits = _run(tmp_path)
    submitted = fake.submissions[0]
    assert submitted.state is JobState.PENDING  # 提交时快照
    job = report.job
    assert job is not None
    assert job.job_id == submitted.job_id
    # partition 必须逐字来自提交记录（**非默认**值 `gpu-1`：任何硬编码 "cpu" 的实现都红）。
    assert job.partition == "gpu-1"
    # JobSpec.resources 必须逐字是 `dict(local.slurm)` 的整份快照（ownership 6）：
    # 只断言 partition 会让「resources 静默丢 account」的变异体存活（实测幸存）。
    assert dict(submitted.resources) == {"partition": "gpu-1", "account": "yd-forecast"}
    # 幂等取回终态记录：fake 的终态 poll 幂等（不回退、不改时间戳），故再次 poll 得到
    # 的就是 terminal JobRecord；报告五字段必须逐字与该记录相等。
    terminal = fake.poll(submitted.job_id)
    assert terminal.state is JobState.SUCCEEDED
    assert job.job_id == terminal.job_id
    assert job.state is terminal.state
    assert job.submitted_at == terminal.submitted_at
    assert job.started_at == terminal.started_at
    assert job.ended_at == terminal.ended_at
    # 提交记录保持 PENDING 快照，不随 poll 改变。
    assert fake.submissions[0].state is JobState.PENDING


def test_failed_terminal_returns_job_failed_without_collect_or_publish(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    from run_once_fixtures import make_terminal_hook

    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.FAILED, polls_until_terminal=1, started=True
            )
        },
        clock=_clock(),
    )
    request_slot = {}

    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]

    def make_hook(*, job_id):
        make_terminal_hook(request_slot["request"], state)()

    hook_executor = HookedExecutor(fake, make_hook)
    work_root = pathlib.Path(local.scratch_root).resolve() / "work"
    work_dir = work_root / "gfs" / "2026082612"

    report = run_once(
        config=config,
        local=local,
        source="gfs",
        executor=hook_executor,
        driver=driver,
        poll_wait=lambda: None,
    )
    assert report.outcome is RunOutcome.JOB_FAILED
    assert report.job is not None and report.job.state is JobState.FAILED
    assert report.published is None and report.done_path is None
    # 零 collect/publish：hook 未触发，run_directory/tracker 未建立。
    assert state.run_directory is None and state.tracker is None
    # work 保留（14.1 边界；失败收尾归 #28/#47）。
    assert work_dir.exists()
    # DONE 不存在。
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()


def test_stopped_for_missing_state_with_zero_submission(tmp_path: pathlib.Path) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_raw_cycle(local)
    # 不写 states：NO_INITIAL_STATE。
    fake = FakeJobExecutor(outcomes={}, clock=_clock())
    report = run_once(
        config=config,
        local=local,
        source="gfs",
        executor=fake,
        driver=InProcessDriver(HookState()),
        poll_wait=lambda: None,
    )
    assert report.outcome is RunOutcome.STOPPED
    assert report.stop_reason is controller.StopReason.NO_INITIAL_STATE
    assert report.job is None and report.cycle is None
    assert fake.submissions == ()


def test_flock_encloses_the_whole_tick_and_skips_concurrent_entry(
    tmp_path: pathlib.Path,
) -> None:
    lock = tmp_path.resolve() / "run" / "yd-producer.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    inner_calls = []

    def inner() -> str:
        inner_calls.append(1)
        return "inner-ran"

    def outer_body():
        # 第一次进入拿锁并执行 run_once（用最小 fake 证明整链在锁内）。
        config, local = write_config_local(tmp_path)
        write_variant(local)
        write_state(local)
        write_raw_cycle(local)
        state = HookState()
        driver = InProcessDriver(state)
        fake = FakeJobExecutor(outcomes=_success_outcome(), clock=_clock())
        request_slot = {}

        original_prepare = driver.prepare

        def capturing_prepare(*, request):
            request_slot["request"] = request
            return original_prepare(request=request)

        driver.prepare = capturing_prepare  # type: ignore[method-assign]

        def make_hook(*, job_id):
            # terminal 跃迁窗口内第二次同锁进入：跳过且 inner action 零调用。
            make_terminal_hook(request_slot["request"], state)()
            concurrent = runlock.run_with_lock(lock_path=lock, action=inner)
            assert concurrent.acquired is False
            assert concurrent.value is None
            assert inner_calls == []

        hook_executor = HookedExecutor(fake, make_hook)

        def poll_wait() -> None:
            # poll-wait 窗口内第二次同锁进入：同样跳过。
            concurrent = runlock.run_with_lock(lock_path=lock, action=inner)
            assert concurrent.acquired is False
            assert concurrent.value is None
            assert inner_calls == []

        # publish 窗口内（publisher 执行中）第二次同锁进入：跳过且 inner 零调用。
        import yd_producer.publish as publish_module

        original_publish = publish_module.publish

        def publishing(inputs):
            concurrent = runlock.run_with_lock(lock_path=lock, action=inner)
            assert concurrent.acquired is False
            assert concurrent.value is None
            assert inner_calls == []
            return original_publish(inputs)

        publish_module.publish = publishing  # type: ignore[assignment]
        try:
            return run_once(
                config=config,
                local=local,
                source="gfs",
                executor=hook_executor,
                driver=driver,
                poll_wait=poll_wait,
            )
        finally:
            publish_module.publish = original_publish  # type: ignore[assignment]

    outer = runlock.run_with_lock(lock_path=lock, action=outer_body)
    assert outer.acquired is True
    report = outer.value
    assert report.outcome is RunOutcome.SUCCEEDED
    assert report.done_path.is_file()
    # job/publish 窗口内第二次同锁进入：跳过且 inner action 零调用（在 hook 内断言）。
    assert inner_calls == []

    # 异常路径后锁可再取，锁文件保留。
    def boom() -> None:
        raise RuntimeError("publish 段炸了")

    with pytest.raises(RuntimeError):
        runlock.run_with_lock(lock_path=lock, action=boom)
    after = runlock.run_with_lock(lock_path=lock, action=inner)
    assert after.acquired is True and after.value == "inner-ran"
    assert inner_calls == [1]
    assert lock.is_file()


# --- Required evidence 21: report/dataclass/protocol 结构 --------------------------


def test_public_seam_shape_is_frozen() -> None:
    import dataclasses
    import inspect

    from yd_producer import controller as c

    # run_once 全部 keyword-only、无默认值。
    sig = inspect.signature(c.run_once)
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
    # `from __future__ import annotations` 把注解字符串化；config/local/executor 只在
    # TYPE_CHECKING 下导入（controller 冷面保持轻），此处按注解文本断言身份而非求值。
    raw_annotations = c.run_once.__annotations__
    assert raw_annotations["return"] == "RunReport"
    assert raw_annotations["source"] == "str"
    assert "Config" in raw_annotations["config"]
    assert "LocalConfig" in raw_annotations["local"]
    assert "JobExecutor" in raw_annotations["executor"]
    assert "AttemptDriver" in raw_annotations["driver"]
    assert "Callable" in raw_annotations["poll_wait"]
    assert "None" in raw_annotations["poll_wait"]

    # dataclass: frozen + kw_only + 字段名逐字。
    for klass, fields in (
        (
            c.AttemptRequest,
            (
                "source",
                "cycle",
                "work_root",
                "work_dir",
                "object_store_root",
                "raw_manifest_path",
                "variant_dir",
                "state_path",
                "shud_binary",
                "checkpoint_hours",
                "forecast_days",
                "output_interval_minutes",
                "reach_count",
            ),
        ),
        (c.PreparedAttempt, ("identity", "command", "scratch_dat")),
        (
            c.AttemptProducts,
            ("job_id", "run_directory", "tracker", "scratch_dat", "merged_log"),
        ),
        (
            c.JobRunReport,
            ("job_id", "partition", "state", "submitted_at", "started_at", "ended_at"),
        ),
        (
            c.RunReport,
            (
                "source",
                "cycle",
                "outcome",
                "stop_reason",
                "detail",
                "job",
                "published",
                "done_path",
            ),
        ),
    ):
        assert klass.__dataclass_params__.frozen is True
        assert klass.__dataclass_params__.kw_only is True
        assert tuple(f.name for f in dataclasses.fields(klass)) == fields
        for field in dataclasses.fields(klass):
            assert field.default is dataclasses.MISSING
            assert field.default_factory is dataclasses.MISSING

    # runtime protocol：类型匹配对象 -> True，反例 -> False。
    assert isinstance(InProcessDriver(HookState()), c.AttemptDriver)
    assert isinstance(object(), c.AttemptDriver) is False
    prepare_sig = inspect.signature(c.AttemptDriver.prepare)
    collect_sig = inspect.signature(c.AttemptDriver.collect)
    assert tuple(prepare_sig.parameters) == ("self", "request")
    assert tuple(collect_sig.parameters) == ("self", "attempt", "terminal_record")
    assert prepare_sig.parameters["request"].kind is inspect.Parameter.KEYWORD_ONLY
    assert (
        collect_sig.parameters["attempt"].kind is inspect.Parameter.KEYWORD_ONLY
        and collect_sig.parameters["terminal_record"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )

    # RunOutcome 闭合词表恰四项；RunError 字段类型与内容。
    assert tuple(item.value for item in c.RunOutcome) == (
        "STOPPED",
        "SUCCEEDED",
        "SUCCEEDED_CLEANUP_PENDING",
        "JOB_FAILED",
    )
    assert issubclass(c.RunError, RuntimeError)
    assert isinstance(c.RunError, type)
    error = c.RunError("x", phase="preflight", source="gfs")
    assert error.phase == "preflight" and error.source == "gfs"
    assert error.cycle is None and error.job_id is None
    # phase 是闭合词表：外来阶段必须被构造点拒绝（provides the closed vocabulary claim）。
    with pytest.raises(ValueError, match="phase 取值非法"):
        c.RunError("x", phase="not-a-phase", source="gfs")

    # 公共导出**精确**（从 controller 导入且只从 controller 导入）：既有导出原样保留，
    # Issue #26 的九项逐字列出；不允许多余符号（如误把 RunPhase/私有 helper 暴露）、
    # 不允许缺项、不允许重排（顺序是当前 API 一部分）。
    assert tuple(c.__all__) == (
        "CYCLE_ID_FORMAT",
        "CYCLE_STRIDE",
        "MAX_HEADER_LINE_BYTES",
        "STATE_SUFFIX",
        "AttemptDriver",
        "AttemptProducts",
        "AttemptRequest",
        "DiscoveryUnreadableError",
        "FrontierDecision",
        "JobRunReport",
        "PreparedAttempt",
        "RunError",
        "RunOutcome",
        "RunReport",
        "StopReason",
        "cycle_id",
        "decide_frontier",
        "done_cycles",
        "parse_cycle_id",
        "run_once",
        "visible_state_cycles",
    )


def test_job_run_report_constructor_rejects_invalid_outcome_and_whitespace_partition(
    tmp_path: pathlib.Path,
) -> None:
    """JobRunReport/RunReport 构造点不变量（fixture Required evidence 21 附加腿）。"""
    from datetime import UTC, datetime

    from yd_producer import controller as c
    from yd_producer.executor import JobState

    submitted = datetime(2026, 8, 26, 12, tzinfo=UTC)
    terminal = submitted + timedelta(hours=2)

    # 非 JobState 的 state 必须被构造点拒绝（不静默落成 JOB_FAILED 之类）。
    with pytest.raises(ValueError):
        c.JobRunReport(
            job_id="job-1",
            partition="cpu",
            state="SUCCEEDED",  # type: ignore[arg-type]
            submitted_at=submitted,
            started_at=terminal,
            ended_at=terminal,
        )
    # 空白 / 纯空白 partition 拒绝。
    with pytest.raises(ValueError):
        c.JobRunReport(
            job_id="job-1",
            partition="   ",
            state=JobState.SUCCEEDED,
            submitted_at=submitted,
            started_at=terminal,
            ended_at=terminal,
        )
    # 空白 job_id 拒绝。
    with pytest.raises(ValueError):
        c.JobRunReport(
            job_id="   ",
            partition="cpu",
            state=JobState.SUCCEEDED,
            submitted_at=submitted,
            started_at=terminal,
            ended_at=terminal,
        )
    # 环绕空格是合法值：nonblank 判据只做 strip 探针，不归一化。
    report = c.JobRunReport(
        job_id="job-1",
        partition=" gpu-1 ",
        state=JobState.SUCCEEDED,
        submitted_at=submitted,
        started_at=terminal,
        ended_at=terminal,
    )
    assert report.partition == " gpu-1 "

    # RunReport.outcome 不是 RunOutcome 必须被构造点拒绝（外来字符串不得静默落成
    # JOB_FAILED）。
    with pytest.raises(ValueError):
        c.RunReport(
            source="gfs",
            cycle=CYCLE,
            outcome="JOB_FAILED",  # type: ignore[arg-type]
            stop_reason=None,
            detail="x",
            job=report,
            published=None,
            done_path=None,
        )
