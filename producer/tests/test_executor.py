"""`yd_producer.executor` 协议与进程内 fake 的契约测试。

全部时间断言用注入的 `StepClock` 的确定性精确值表达，不做"约等于现在"的挂钟近似；
全部失败路径断言 `ExecutorError` 及其结构化属性 `job_id`，不做消息子串探测（承
`ConfigError.path` 先例）。

记录不变式在**构造点**验证：下方一组用例直接构造 `JobRecord`，不经 fake 的调用路径
——fake 的正常路径永远产生不出这些非法组合，只测 fake 等于该 MUST 无证据。
"""

from __future__ import annotations

import dataclasses
import inspect
import types
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from yd_producer.executor import (
    ExecutorError,
    FakeJobExecutor,
    FakeOutcome,
    JobExecutor,
    JobRecord,
    JobSpec,
    JobState,
    StepClock,
)

# --- 固定时钟与构造助手 ------------------------------------------------------

T0 = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
STEP = timedelta(seconds=10)


def tick(n: int) -> datetime:
    """`StepClock(start=T0, step=STEP)` 的第 n 次取值（n 从 0 起）。"""
    return T0 + n * STEP


def make_clock() -> StepClock:
    return StepClock(start=T0, step=STEP)


#: 取自 `local.toml` 的生产形态资源映射；协议层对键集不作任何解释
SITE_RESOURCES: dict[str, str | int] = {
    "partition": "cpu",
    "account": "a",
    "cpus": 8,
    "memory": "32G",
    "walltime": "04:00:00",
}


def make_spec(name: str, resources: dict[str, str | int] | None = None) -> JobSpec:
    return JobSpec(
        name=name,
        work_dir=Path("/fixture/scratch") / name,
        command=("shud", name),
        log_path=Path("/fixture/log") / f"{name}.log",
        resources=dict(SITE_RESOURCES) if resources is None else resources,
    )


def make_record(**overrides: Any) -> JobRecord:
    """构造一条合法记录，overrides 用于逐条打破某个不变式。"""
    kwargs: dict[str, Any] = {
        "job_id": "fake-1",
        "name": "ifs-2026030100",
        "state": JobState.SUCCEEDED,
        "resources": dict(SITE_RESOURCES),
        "submitted_at": tick(0),
        "started_at": tick(1),
        "ended_at": tick(2),
    }
    kwargs.update(overrides)
    return JobRecord(**kwargs)


#: naive 版本的 T0：由 aware 值去掉 tzinfo 得到，与 T0 逐字段相同
NAIVE = T0.replace(tzinfo=None)

#: 非零 UTC 偏移的时区：aware 但偏移不为 0，同样必须被拒
TZ8 = timezone(timedelta(hours=8))


def shifted(n: int) -> datetime:
    """与 `tick(n)` 同一绝对时刻，但带 +08:00 偏移（故时序不变式不会先行触发）。"""
    return tick(n).astimezone(TZ8)


DATACLASSES = (JobSpec, JobRecord, FakeOutcome)


# --- 协议一致性 --------------------------------------------------------------


def test_fake_satisfies_runtime_checkable_protocol():
    fake = FakeJobExecutor(outcomes={}, clock=make_clock())
    assert isinstance(fake, JobExecutor)


@pytest.mark.parametrize("method", ["submit", "poll"])
def test_fake_method_signature_matches_protocol(method):
    """`runtime_checkable` 只校验方法存在，故按签名逐参数比对。"""
    expected = inspect.signature(getattr(JobExecutor, method))
    actual = inspect.signature(getattr(FakeJobExecutor, method))
    assert actual == expected


# --- 三态可编排与打戳时机 ----------------------------------------------------


@pytest.mark.parametrize(
    "final_state", [JobState.SUCCEEDED, JobState.FAILED, JobState.TIMEOUT]
)
def test_three_terminal_states_are_each_reachable(final_state):
    """SUCCEEDED / FAILED / TIMEOUT 各自可达，且 `state` 精确等于编排值。"""
    fake = FakeJobExecutor(
        outcomes={
            "job": FakeOutcome(
                final_state=final_state, polls_until_terminal=2, started=True
            )
        },
        clock=make_clock(),
    )
    submitted = fake.submit(make_spec("job"))
    assert submitted.state is JobState.PENDING
    assert submitted.started_at is None
    assert submitted.ended_at is None
    assert submitted.submitted_at == tick(0)

    first = fake.poll(submitted.job_id)
    assert first.state is JobState.RUNNING
    second = fake.poll(submitted.job_id)
    assert second.state is JobState.RUNNING

    terminal = fake.poll(submitted.job_id)
    assert terminal.state is final_state
    assert terminal.state.is_terminal
    assert terminal.started_at is not None
    assert terminal.ended_at is not None


def test_timeout_is_not_folded_into_failed():
    """TIMEOUT 与 FAILED 分立：两者互不相等且都是终态。"""
    assert JobState.TIMEOUT is not JobState.FAILED
    assert JobState.TIMEOUT.is_terminal
    assert JobState.FAILED.is_terminal
    assert not JobState.PENDING.is_terminal
    assert not JobState.RUNNING.is_terminal


def test_unchanged_poll_does_not_read_the_clock():
    fake = FakeJobExecutor(
        outcomes={
            "job": FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=3, started=True
            )
        },
        clock=make_clock(),
    )
    job_id = fake.submit(make_spec("job")).job_id

    running = fake.poll(job_id)
    assert running.state is JobState.RUNNING
    assert running.started_at == tick(1)
    assert running.ended_at is None

    for _ in range(2):
        again = fake.poll(job_id)
        assert again.state is JobState.RUNNING
        assert again.started_at == running.started_at
        assert again.ended_at is None

    # 中间两次 poll 未取时钟，故终态时间是紧邻起跑时间的下一次取值
    terminal = fake.poll(job_id)
    assert terminal.state is JobState.SUCCEEDED
    assert terminal.started_at == tick(1)
    assert terminal.ended_at == tick(2)


def test_immediate_terminal_takes_two_distinct_ticks():
    fake = FakeJobExecutor(
        outcomes={
            "job": FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=0, started=True
            )
        },
        clock=make_clock(),
    )
    job_id = fake.submit(make_spec("job")).job_id
    record = fake.poll(job_id)

    assert record.state is JobState.SUCCEEDED
    assert record.submitted_at == tick(0)
    assert record.started_at == tick(1)
    assert record.ended_at == tick(2)
    assert record.started_at < record.ended_at


def test_never_started_job_reaches_terminal_without_start_time():
    fake = FakeJobExecutor(
        outcomes={
            "job": FakeOutcome(
                final_state=JobState.FAILED, polls_until_terminal=1, started=False
            )
        },
        clock=make_clock(),
    )
    job_id = fake.submit(make_spec("job")).job_id

    pending = fake.poll(job_id)
    assert pending.state is JobState.PENDING
    assert pending.started_at is None
    assert pending.ended_at is None

    terminal = fake.poll(job_id)
    assert terminal.state is JobState.FAILED
    assert terminal.started_at is None
    assert terminal.ended_at == tick(1)


def test_terminal_record_is_idempotent():
    fake = FakeJobExecutor(
        outcomes={
            "job": FakeOutcome(
                final_state=JobState.TIMEOUT, polls_until_terminal=0, started=True
            )
        },
        clock=make_clock(),
    )
    job_id = fake.submit(make_spec("job")).job_id
    first = fake.poll(job_id)
    second = fake.poll(job_id)
    third = fake.poll(job_id)

    assert first == second == third
    assert first.state.is_terminal
    assert second.state.is_terminal
    assert third.state.is_terminal


# --- 失败路径 ----------------------------------------------------------------


def test_poll_unknown_job_id_fails_closed():
    fake = FakeJobExecutor(outcomes={}, clock=make_clock())
    with pytest.raises(ExecutorError) as excinfo:
        fake.poll("nosuch")
    assert excinfo.value.job_id == "nosuch"


def test_unorchestrated_name_is_rejected_and_not_recorded():
    fake = FakeJobExecutor(
        outcomes={
            "known": FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=0, started=True
            )
        },
        clock=make_clock(),
    )
    with pytest.raises(ExecutorError) as excinfo:
        fake.submit(make_spec("unknown"))
    assert excinfo.value.job_id is None
    assert fake.submissions == ()

    # 失败提交既不入账也不消耗时钟与 job id
    record = fake.submit(make_spec("known"))
    assert record.submitted_at == tick(0)
    assert record.job_id == "fake-1"
    assert len(fake.submissions) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "final_state": JobState.RUNNING,
            "polls_until_terminal": 0,
            "started": True,
        },
        {
            "final_state": JobState.PENDING,
            "polls_until_terminal": 0,
            "started": True,
        },
        {
            "final_state": JobState.SUCCEEDED,
            "polls_until_terminal": -1,
            "started": True,
        },
        {
            "final_state": JobState.SUCCEEDED,
            "polls_until_terminal": 0,
            "started": False,
        },
    ],
)
def test_illegal_outcome_is_rejected(kwargs):
    with pytest.raises(ExecutorError) as exc:
        FakeOutcome(**kwargs)
    assert exc.value.job_id is None


def test_step_clock_rejects_naive_start():
    with pytest.raises(ExecutorError) as exc:
        StepClock(start=NAIVE, step=STEP)
    assert exc.value.job_id is None


def test_step_clock_rejects_non_utc_aware_start():
    with pytest.raises(ExecutorError) as exc:
        StepClock(start=shifted(0), step=STEP)
    assert exc.value.job_id is None


def test_step_clock_timestamps_are_utc_aware():
    fake = FakeJobExecutor(
        outcomes={
            "job": FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=make_clock(),
    )
    job_id = fake.submit(make_spec("job")).job_id
    fake.poll(job_id)
    record = fake.poll(job_id)

    for stamp in (record.submitted_at, record.started_at, record.ended_at):
        assert stamp is not None
        assert stamp.tzinfo is not None
        assert stamp.utcoffset() == timedelta(0)


# --- 构造点强制的记录不变式 --------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        # ① 非终态却带 ended_at
        {"state": JobState.RUNNING, "started_at": tick(1), "ended_at": tick(2)},
        # ② 终态却缺 ended_at
        {"state": JobState.SUCCEEDED, "started_at": tick(1), "ended_at": None},
        # ③ started_at 早于 submitted_at
        {"submitted_at": tick(2), "started_at": tick(1), "ended_at": tick(3)},
        # ④ ended_at 早于 started_at
        {"submitted_at": tick(0), "started_at": tick(2), "ended_at": tick(1)},
        # ⑤ SUCCEEDED 却没有起跑时间
        {"state": JobState.SUCCEEDED, "started_at": None, "ended_at": tick(2)},
        # ⑦ 未起跑即终止，但 ended_at 早于 submitted_at（负墙钟时长）
        {
            "state": JobState.FAILED,
            "started_at": None,
            "submitted_at": tick(2),
            "ended_at": tick(1),
        },
        # ⑥ naive datetime（逐字段各一条）
        {"submitted_at": NAIVE},
        {"started_at": NAIVE},
        {"ended_at": NAIVE},
    ],
)
def test_illegal_record_cannot_be_constructed(overrides):
    with pytest.raises(ExecutorError) as exc:
        make_record(**overrides)
    assert exc.value.job_id == "fake-1"


@pytest.mark.parametrize("field", ["submitted_at", "started_at", "ended_at"])
def test_non_utc_aware_record_timestamp_is_rejected(field):
    """aware 但偏移非 0 的时间戳与 naive 一样 fail closed，并带上涉事 job_id。"""
    index = {"submitted_at": 0, "started_at": 1, "ended_at": 2}[field]
    with pytest.raises(ExecutorError) as exc:
        make_record(**{field: shifted(index)})
    assert exc.value.job_id == "fake-1"


@pytest.mark.parametrize(
    "state", [JobState.FAILED, JobState.TIMEOUT, JobState.SUCCEEDED]
)
def test_legal_terminal_records_are_constructible(state):
    record = make_record(state=state)
    assert record.state is state
    assert record.submitted_at <= record.started_at <= record.ended_at


@pytest.mark.parametrize("state", [JobState.FAILED, JobState.TIMEOUT])
def test_terminal_without_start_is_allowed_for_failures(state):
    record = make_record(state=state, started_at=None, ended_at=tick(2))
    assert record.started_at is None
    assert record.ended_at == tick(2)


def test_in_flight_records_are_constructible():
    pending = make_record(state=JobState.PENDING, started_at=None, ended_at=None)
    running = make_record(state=JobState.RUNNING, started_at=tick(1), ended_at=None)
    assert pending.ended_at is None
    assert running.started_at == tick(1)


# --- 时间序关系 --------------------------------------------------------------


def test_terminal_record_from_fake_respects_time_ordering():
    fake = FakeJobExecutor(
        outcomes={
            "job": FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=make_clock(),
    )
    job_id = fake.submit(make_spec("job")).job_id
    fake.poll(job_id)
    record = fake.poll(job_id)

    assert record.submitted_at == tick(0)
    assert record.started_at == tick(1)
    assert record.ended_at == tick(2)
    assert record.submitted_at <= record.started_at <= record.ended_at


def test_successive_submissions_have_strictly_increasing_submit_times():
    outcome = FakeOutcome(
        final_state=JobState.SUCCEEDED, polls_until_terminal=0, started=True
    )
    fake = FakeJobExecutor(outcomes={"a": outcome, "b": outcome}, clock=make_clock())
    first = fake.submit(make_spec("a"))
    second = fake.submit(make_spec("b"))

    assert first.submitted_at == tick(0)
    assert second.submitted_at == tick(1)
    assert first.submitted_at < second.submitted_at


# --- resources 快照与不透明性 ------------------------------------------------


def test_resources_are_carried_verbatim_and_snapshotted():
    mutable: dict[str, str | int] = dict(SITE_RESOURCES)
    fake = FakeJobExecutor(
        outcomes={
            "job": FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=0, started=True
            )
        },
        clock=make_clock(),
    )
    spec = make_spec("job", resources=mutable)
    record = fake.submit(spec)

    assert dict(record.resources) == SITE_RESOURCES
    # partition 可入运行报告：调用方按 `required_fields` 的键取值
    assert record.resources["partition"] == "cpu"

    mutable["partition"] = "gpu"
    mutable["extra"] = "x"
    del mutable["account"]

    assert dict(spec.resources) == SITE_RESOURCES
    assert dict(record.resources) == SITE_RESOURCES


@pytest.mark.parametrize("holder", ["spec", "record"])
def test_resources_mapping_is_immutable(holder):
    """两侧都以**普通 dict** 直接构造，不经 fake。

    经 fake 拿到的 `JobRecord.resources` 来自 `spec.resources`（已是 proxy），那样断言
    即便删掉 `JobRecord.__post_init__` 的快照也恒真。
    """
    resources = (
        make_spec("job").resources if holder == "spec" else make_record().resources
    )

    assert isinstance(resources, types.MappingProxyType)
    with pytest.raises(TypeError):
        resources["partition"] = "gpu"  # type: ignore[index]


def test_record_snapshots_resources_at_construction():
    """`JobRecord` 自己快照入参映射：不经 fake 直接构造，事后改原 dict 不影响记录。"""
    mutable: dict[str, str | int] = dict(SITE_RESOURCES)
    record = make_record(resources=mutable)

    mutable["partition"] = "gpu"
    mutable["extra"] = "x"
    del mutable["account"]

    assert dict(record.resources) == SITE_RESOURCES
    assert isinstance(record.resources, types.MappingProxyType)


def test_terminal_record_keeps_submitted_resources():
    fake = FakeJobExecutor(
        outcomes={
            "job": FakeOutcome(
                final_state=JobState.FAILED, polls_until_terminal=1, started=True
            )
        },
        clock=make_clock(),
    )
    job_id = fake.submit(make_spec("job")).job_id
    fake.poll(job_id)
    terminal = fake.poll(job_id)
    assert dict(terminal.resources) == SITE_RESOURCES


def test_executor_source_names_no_scheduler_field():
    """第二权威守卫：键集唯一权威是 `Config.slurm.required_fields`。"""
    from yd_producer import executor

    source = Path(executor.__file__).read_text(encoding="utf-8")
    for literal in ("partition", "account", "cpus", "memory", "walltime"):
        assert literal not in source, f"executor.py 不得出现调度字段字面量 `{literal}`"


# --- job_id 分配与相等/哈希语义 ----------------------------------------------


def test_same_name_submitted_twice_gets_independent_jobs():
    fake = FakeJobExecutor(
        outcomes={
            "ifs-T": FakeOutcome(
                final_state=JobState.FAILED, polls_until_terminal=1, started=True
            )
        },
        clock=make_clock(),
    )
    first = fake.submit(make_spec("ifs-T"))
    second = fake.submit(make_spec("ifs-T"))

    assert first.job_id != second.job_id
    assert len(fake.submissions) == 2
    assert [r.name for r in fake.submissions] == ["ifs-T", "ifs-T"]

    # 各自独立推进：第一个跑到终态时，第二个仍在途
    assert fake.poll(first.job_id).state is JobState.RUNNING
    assert fake.poll(first.job_id).state is JobState.FAILED
    assert fake.poll(second.job_id).state is JobState.RUNNING
    assert fake.poll(first.job_id).state is JobState.FAILED
    assert fake.poll(second.job_id).state is JobState.FAILED


def test_record_is_comparable_but_not_hashable():
    left = make_record()
    right = make_record()
    assert left == right
    with pytest.raises(TypeError):
        hash(left)


def test_spec_is_comparable_but_not_hashable():
    left = make_spec("job")
    right = make_spec("job")
    assert left == right
    with pytest.raises(TypeError):
        hash(left)


def test_records_differing_in_one_field_are_unequal():
    assert make_record() != make_record(job_id="fake-2")


# --- dataclass 结构 ----------------------------------------------------------


@pytest.mark.parametrize("klass", DATACLASSES)
def test_dataclass_is_frozen_and_kw_only(klass):
    """直接断言 dataclass 参数本身。

    不用「位置构造抛 `TypeError`」来间接推断 `kw_only`：`JobSpec`/`JobRecord` 的
    `__post_init__` 会先对哨兵实参 `dict(object())` 抛 `TypeError`，那条断言即便去掉
    `kw_only=True` 也恒真。
    """
    params = klass.__dataclass_params__
    assert params.frozen is True
    assert params.kw_only is True


@pytest.mark.parametrize("klass", DATACLASSES)
def test_dataclass_carries_no_default(klass):
    for field in dataclasses.fields(klass):
        assert field.default is dataclasses.MISSING, (
            f"{klass.__name__}.{field.name} 不得有默认值"
        )
        assert field.default_factory is dataclasses.MISSING, (
            f"{klass.__name__}.{field.name} 不得有 default_factory"
        )


@pytest.mark.parametrize(
    ("klass", "expected"),
    [
        (JobSpec, ("name", "work_dir", "command", "log_path", "resources")),
        (
            JobRecord,
            (
                "job_id",
                "name",
                "state",
                "resources",
                "submitted_at",
                "started_at",
                "ended_at",
            ),
        ),
        (FakeOutcome, ("final_state", "polls_until_terminal", "started")),
    ],
)
def test_dataclass_field_names_are_pinned(klass, expected):
    assert tuple(f.name for f in dataclasses.fields(klass)) == expected


def test_fake_requires_an_injected_clock():
    """`clock` 无默认值，禁止读挂钟。"""
    with pytest.raises(TypeError):
        FakeJobExecutor(outcomes={})  # type: ignore[call-arg]


# --- 在途观测（run-controller「每源至多一个作业」）----------------------------


def run_to_terminal(fake: FakeJobExecutor, job_id: str) -> JobRecord:
    record = fake.poll(job_id)
    while not record.state.is_terminal:
        record = fake.poll(job_id)
    return record


def test_two_sources_in_flight_together():
    outcome = FakeOutcome(
        final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
    )
    fake = FakeJobExecutor(
        outcomes={"ifs-T": outcome, "gfs-T": outcome}, clock=make_clock()
    )
    ifs = fake.submit(make_spec("ifs-T")).job_id
    gfs = fake.submit(make_spec("gfs-T")).job_id

    assert set(fake.inflight()) == {ifs, gfs}
    assert fake.max_inflight == 2

    assert run_to_terminal(fake, ifs).state is JobState.SUCCEEDED
    assert fake.inflight() == (gfs,)
    assert run_to_terminal(fake, gfs).state is JobState.SUCCEEDED
    assert fake.inflight() == ()
    assert fake.max_inflight == 2


def test_single_source_serial_rounds_keep_one_job_in_flight():
    outcome = FakeOutcome(
        final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
    )
    fake = FakeJobExecutor(
        outcomes={"ifs-T": outcome, "ifs-T+12": outcome}, clock=make_clock()
    )
    for name in ("ifs-T", "ifs-T+12"):
        job_id = fake.submit(make_spec(name)).job_id
        assert len(fake.inflight()) <= 1
        run_to_terminal(fake, job_id)
        assert len(fake.inflight()) <= 1

    assert fake.max_inflight == 1
    assert [r.name for r in fake.submissions] == ["ifs-T", "ifs-T+12"]


# --- issue #26 交接：accessor 的三个判别器（PR #39 评论，随 #26 一并落地） ---


def test_max_inflight_survives_full_drain_and_new_submit():
    """历史峰值：A/B 提交 -> 双双排空 -> 再提交 C -> max_inflight 仍为 2。

    旧语义「末次提交计数」在这条下必红：C 提交后 `len(inflight()) == 1`。
    """
    outcome = FakeOutcome(
        final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
    )
    fake = FakeJobExecutor(
        outcomes={"A": outcome, "B": outcome, "C": outcome}, clock=make_clock()
    )
    a = fake.submit(make_spec("A")).job_id
    b = fake.submit(make_spec("B")).job_id
    assert fake.max_inflight == 2
    run_to_terminal(fake, a)
    run_to_terminal(fake, b)
    assert fake.inflight() == ()
    assert fake.max_inflight == 2
    c = fake.submit(make_spec("C")).job_id
    assert fake.max_inflight == 2
    run_to_terminal(fake, c)
    assert fake.max_inflight == 2


def test_inflight_returns_submission_order():
    """两个并发作业下 `inflight()` 的元组顺序即提交序（A 先于 B）。

    逆序实现在这条下必红（提交 A、提交 B 后 inflight 必须 (A, B)）。
    """
    outcome = FakeOutcome(
        final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
    )
    fake = FakeJobExecutor(outcomes={"A": outcome, "B": outcome}, clock=make_clock())
    a = fake.submit(make_spec("A")).job_id
    b = fake.submit(make_spec("B")).job_id
    assert fake.inflight() == (a, b)
    # A 排空后 B 仍在途：顺序保持。
    run_to_terminal(fake, a)
    assert fake.inflight() == (b,)


def test_submissions_are_submission_time_snapshots():
    """`submissions[i]` 是提交时快照：某作业到终态后 `submissions[0].state` 仍 PENDING。

    实时当前记录实现在这条下必红（终态后 `submissions[0].state` 会变成 SUCCEEDED）。
    """
    outcome = FakeOutcome(
        final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
    )
    fake = FakeJobExecutor(outcomes={"A": outcome, "B": outcome}, clock=make_clock())
    fake.submit(make_spec("A"))
    b = fake.submit(make_spec("B")).job_id
    assert fake.submissions[0].state is JobState.PENDING
    run_to_terminal(fake, fake.submissions[0].job_id)
    assert fake.submissions[0].state is JobState.PENDING
    assert fake.submissions[1].state is JobState.PENDING
    run_to_terminal(fake, b)
    assert fake.submissions[0].state is JobState.PENDING
    assert fake.submissions[1].state is JobState.PENDING
