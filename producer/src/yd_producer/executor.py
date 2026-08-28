"""作业执行器抽象：`JobExecutor` 协议、作业记录，与进程内 fake。

控制器（compute-loop §10）只通过本协议提交与查询作业：`submit` 交出一个 `JobRecord`，
`poll` 按 `job_id` 取回该作业的当前记录。生产实现（Slurm 封装）与进程内 fake 共用同
一套记录 schema，故记录字段即下游运行报告与 receipt 的字段来源。

设计约束（design.md D3/D5）：

* **只用 stdlib**，不新增依赖。
* **调度参数不透明**：`resources` 是一份普通映射，本模块既不解释它的键、也不声明键集
  ——键集的唯一权威是 `Config.slurm.required_fields`（`yd_producer.config`），在协议层
  再写一份就是第二权威。作业身份四元组里的队列名由调用方按该权威的键从
  `JobRecord.resources` 取出，因此队列名可入运行报告而协议无须写死键名。
* **终态三分**：`SUCCEEDED` / `FAILED` / `TIMEOUT` 分立。墙钟时限触发的终止在调度器
  查询结果里本就是独立终态，运维据此判断是"放宽时限"还是"查作业本身"；把它折进
  `FAILED` 就销毁了这条判据。
* **时间一律 tz-aware UTC**：receipt 与 cycle 用绝对时间比较，naive 值与非零偏移值都会
  静默错位而不报错，故 naive `datetime` 与 UTC 偏移非 0 的 `datetime` 进入记录即 fail
  closed——偏移在构造点强制，不指望调用方的时钟。
* **不变式在构造点强制**：非终态带结束时间、终态缺结束时间、时间倒序、成功却没有起跑
  时间——这些组合在 `JobRecord.__post_init__` 就不可构造，而不是靠调用路径碰巧不产生。
* **单一公开异常**：本模块对外只有 `ExecutorError`，并以结构化属性 `job_id` 暴露涉事
  作业，供调用方与测试机检定位（承 `ConfigError.path` 先例）。
* **零内置默认**：全部 dataclass 字段无默认值、一律 `kw_only`；fake 的时钟必填（禁止读
  挂钟），未被编排的作业名一律报错（禁止"默认成功"）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable

__all__ = [
    "ExecutorError",
    "FakeJobExecutor",
    "FakeOutcome",
    "JobExecutor",
    "JobRecord",
    "JobSpec",
    "JobState",
    "StepClock",
]


class ExecutorError(Exception):
    """作业执行器失败。

    本模块的全部失败路径都收敛到本类型：未被编排的作业名、未知 `job_id`、非法编排、
    naive `datetime`、被打破的记录不变式。

    `job_id` 是涉事作业的 id；与具体作业无关的失败（提交被拒、编排非法、时钟构造非法）
    为 `None`。
    """

    def __init__(self, message: str, job_id: str | None = None) -> None:
        super().__init__(message)
        self.job_id = job_id


class JobState(Enum):
    """作业状态。终态三分，`TIMEOUT` 不与 `FAILED` 合并。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES


_TERMINAL_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.TIMEOUT})


# --- 时间与资源映射的共用原语 ------------------------------------------------


def _require_utc(value: datetime, label: str, job_id: str | None) -> datetime:
    """拒绝 naive `datetime`，也拒绝 UTC 偏移非 0 的 aware `datetime`。"""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ExecutorError(f"`{label}` 必须是带时区的 datetime，实际为 naive", job_id)
    offset = value.utcoffset()
    if offset != timedelta(0):
        raise ExecutorError(f"`{label}` 的 UTC 偏移必须为 0，实际为 {offset}", job_id)
    return value


def _snapshot(resources: Mapping[str, str | int]) -> Mapping[str, str | int]:
    """把入参资源映射复制为不可变视图：调用方事后改原 dict 不影响已构造对象。"""
    return MappingProxyType(dict(resources))


# --- 协议数据结构 ------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class JobSpec:
    """一次提交的全部输入。

    `resources` 是不透明的调度参数映射，构造时快照为不可变视图。
    """

    name: str
    work_dir: Path
    command: tuple[str, ...]
    log_path: Path
    resources: Mapping[str, str | int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "resources", _snapshot(self.resources))


@dataclass(frozen=True, kw_only=True)
class JobRecord:
    """一个作业的当前记录，承载身份四元组：id、调度参数、终态、起止时间。

    `resources` 是**提交时**的整份快照，故调用方能按 `Config.slurm.required_fields`
    的键取出任一调度参数入运行报告。

    实例不可哈希：不可变映射不可哈希，本模块不提供哈希语义（需要作 dict 键时用
    `job_id`）；相等性照常可用。
    """

    job_id: str
    name: str
    state: JobState
    resources: Mapping[str, str | int]
    submitted_at: datetime
    started_at: datetime | None
    ended_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "resources", _snapshot(self.resources))

        job_id = self.job_id
        _require_utc(self.submitted_at, "submitted_at", job_id)
        if self.started_at is not None:
            _require_utc(self.started_at, "started_at", job_id)
        if self.ended_at is not None:
            _require_utc(self.ended_at, "ended_at", job_id)

        if not self.state.is_terminal and self.ended_at is not None:
            raise ExecutorError(
                f"非终态 `{self.state.value}` 的记录不得带 `ended_at`", job_id
            )
        if self.state.is_terminal and self.ended_at is None:
            raise ExecutorError(
                f"终态 `{self.state.value}` 的记录必须带 `ended_at`", job_id
            )
        if self.started_at is not None and self.submitted_at > self.started_at:
            raise ExecutorError("`started_at` 不得早于 `submitted_at`", job_id)
        if self.ended_at is not None and self.submitted_at > self.ended_at:
            # 未起跑即终止（`started_at is None` 的 FAILED/TIMEOUT）时，这是唯一挡住
            # 负墙钟时长的守卫；`started_at` 存在时它由上下两条推出，故无条件成立
            raise ExecutorError("`ended_at` 不得早于 `submitted_at`", job_id)
        if (
            self.started_at is not None
            and self.ended_at is not None
            and self.started_at > self.ended_at
        ):
            raise ExecutorError("`ended_at` 不得早于 `started_at`", job_id)
        if (
            self.state.is_terminal
            and self.started_at is None
            and self.state is not JobState.FAILED
            and self.state is not JobState.TIMEOUT
        ):
            raise ExecutorError(
                f"终态 `{self.state.value}` 必须带 `started_at`（未启动即终止只能是"
                "失败或超时）",
                job_id,
            )


@runtime_checkable
class JobExecutor(Protocol):
    """作业提交与查询的公共契约；生产实现与 fake 共用。"""

    def submit(self, spec: JobSpec) -> JobRecord:
        """提交一个作业，返回其初始记录（含新分配的 `job_id`）。"""
        ...

    def poll(self, job_id: str) -> JobRecord:
        """查询一个作业的当前记录；未知 `job_id` 抛 `ExecutorError`。"""
        ...


# --- 确定性时钟 --------------------------------------------------------------


class StepClock:
    """确定性时钟：每次调用返回当前时刻并推进固定步长。

    首次调用返回 `start`。测试据此对时间戳作精确值断言，而非挂钟近似。
    """

    def __init__(self, *, start: datetime, step: timedelta) -> None:
        _require_utc(start, "start", None)
        self._next = start
        self._step = step

    def __call__(self) -> datetime:
        value = self._next
        self._next = value + self._step
        return value


# --- 进程内 fake -------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class FakeOutcome:
    """一个作业名的编排：前 `polls_until_terminal` 次 `poll` 停在途中，其后进终态。

    `started` 为假表示作业始终没有起跑（在途期间停在 `PENDING`，终态没有起跑时间），
    因此它与 `SUCCEEDED` 不相容。
    """

    final_state: JobState
    polls_until_terminal: int
    started: bool

    def __post_init__(self) -> None:
        if not self.final_state.is_terminal:
            raise ExecutorError(
                f"`final_state` 必须是终态，实际 {self.final_state.value}"
            )
        if self.polls_until_terminal < 0:
            raise ExecutorError(
                f"`polls_until_terminal` 不得为负，实际 {self.polls_until_terminal}"
            )
        if not self.started and self.final_state is JobState.SUCCEEDED:
            raise ExecutorError(
                "未起跑的作业不可能成功：`started=False` 与 SUCCEEDED 不相容"
            )


class _FakeJob:
    """fake 的单作业可变状态：编排、已轮询次数、当前记录。"""

    def __init__(self, outcome: FakeOutcome, record: JobRecord) -> None:
        self.outcome = outcome
        self.polls = 0
        self.record = record


class FakeJobExecutor:
    """进程内 fake：按 `JobSpec.name` 编排，按注入时钟打戳。

    零内置默认：未被编排的名字提交即报错（不"默认成功"），`clock` 必填（不读挂钟）。
    `JobSpec.name` 是编排键，`job_id` 是查询键，二者不等同——同一个名字可重复提交
    （控制器重试路径），每次得到不同的 `job_id`，各自独立推进。
    """

    def __init__(
        self,
        *,
        outcomes: Mapping[str, FakeOutcome],
        clock: Callable[[], datetime],
    ) -> None:
        self._outcomes = dict(outcomes)
        self._clock = clock
        self._jobs: dict[str, _FakeJob] = {}
        self._submissions: list[JobRecord] = []
        self._max_inflight = 0

    # -- 在途观测（run-controller「每源至多一个作业」的断言面）--

    @property
    def submissions(self) -> tuple[JobRecord, ...]:
        """按提交序的提交时记录；失败提交不入账。"""
        return tuple(self._submissions)

    @property
    def max_inflight(self) -> int:
        """历史同时在途作业数峰值。"""
        return self._max_inflight

    def inflight(self) -> tuple[str, ...]:
        """当前非终态作业的 id，按提交序。"""
        return tuple(
            job_id
            for job_id, job in self._jobs.items()
            if not job.record.state.is_terminal
        )

    # -- 协议实现 --

    def submit(self, spec: JobSpec) -> JobRecord:
        outcome = self._outcomes.get(spec.name)
        if outcome is None:
            # 先于取时钟与分配 id 判定：失败提交既不入账，也不消耗时钟与 id 序列
            raise ExecutorError(f"作业名 `{spec.name}` 未被编排，fake 不提供默认行为")

        job_id = f"fake-{len(self._jobs) + 1}"
        record = JobRecord(
            job_id=job_id,
            name=spec.name,
            state=JobState.PENDING,
            resources=spec.resources,
            submitted_at=self._clock(),
            started_at=None,
            ended_at=None,
        )
        self._jobs[job_id] = _FakeJob(outcome, record)
        self._submissions.append(record)
        self._max_inflight = max(self._max_inflight, len(self.inflight()))
        return record

    def poll(self, job_id: str) -> JobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise ExecutorError(f"未知 job id `{job_id}`", job_id)

        record = job.record
        if record.state.is_terminal:
            # 终态幂等：不取时钟、不迁回非终态
            return record

        job.polls += 1
        if job.polls <= job.outcome.polls_until_terminal:
            if job.outcome.started and record.state is JobState.PENDING:
                job.record = self._with_state(
                    record, JobState.RUNNING, started_at=self._clock()
                )
            # 状态不变的 poll 不取时钟
            return job.record

        started_at = record.started_at
        if job.outcome.started and started_at is None:
            started_at = self._clock()
        job.record = self._with_state(
            record,
            job.outcome.final_state,
            started_at=started_at,
            ended_at=self._clock(),
        )
        return job.record

    @staticmethod
    def _with_state(
        record: JobRecord,
        state: JobState,
        *,
        started_at: datetime | None,
        ended_at: datetime | None = None,
    ) -> JobRecord:
        """所有状态推进都重走 `JobRecord` 构造，以便不变式在构造点复检。"""
        return JobRecord(
            job_id=record.job_id,
            name=record.name,
            state=state,
            resources=record.resources,
            submitted_at=record.submitted_at,
            started_at=started_at,
            ended_at=ended_at,
        )
