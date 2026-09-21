"""`test_slurm.py` / `test_slurm_executor.py` 共享的固定输入与假 runner。

本模块**不含任何 `test_*` 函数**：只登记现场声明的资源字段、期望 argv、`sacct` 状态
词表等独立 oracle，以及记录型假 runner 与 executor 构造器。原为 `test_slurm.py` 的
「固定输入」段，为满足 1000 行文件上限拆出，内容逐字保留。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from yd_producer.executor import JobSpec, JobState, StepClock
from yd_producer.slurm import SlurmJobExecutor

T0 = datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
STEP = timedelta(seconds=10)

#: 现场声明的五项资源字段（键集权威在 `Config.slurm.required_fields`，测试逐次显式传入）
REQUIRED_FIVE = ("partition", "account", "cpus", "memory", "walltime")

SITE_RESOURCES: dict[str, str | int] = {
    "partition": "cpu",
    "account": "acct",
    "cpus": 8,
    "memory": "32G",
    "walltime": "04:00:00",
}

#: `sacct` 状态串 -> `JobState` 的期望词表（独立于被测模块的 oracle，逐条来自 fixture）
EXPECTED_STATE_MAP = {
    "PENDING": JobState.PENDING,
    "REQUEUED": JobState.PENDING,
    "REQUEUE_HOLD": JobState.PENDING,
    "RUNNING": JobState.RUNNING,
    "CONFIGURING": JobState.RUNNING,
    "COMPLETING": JobState.RUNNING,
    "RESIZING": JobState.RUNNING,
    "SUSPENDED": JobState.RUNNING,
    "COMPLETED": JobState.SUCCEEDED,
    "TIMEOUT": JobState.TIMEOUT,
    "FAILED": JobState.FAILED,
    "CANCELLED": JobState.FAILED,
    "NODE_FAIL": JobState.FAILED,
    "OUT_OF_MEMORY": JobState.FAILED,
    "BOOT_FAIL": JobState.FAILED,
    "DEADLINE": JobState.FAILED,
    "PREEMPTED": JobState.FAILED,
    "REVOKED": JobState.FAILED,
}

WORK_DIR = Path("/fixture/scratch/work")
LOG_PATH = Path("/fixture/logs/yd_ifs/2026082800.log")


def tick(n: int) -> datetime:
    """`StepClock(start=T0, step=STEP)` 的第 n 次取值（n 从 0 起）。"""
    return T0 + n * STEP


def make_spec(
    *,
    name: str = "ifs-2026082800",
    command: tuple[str, ...] = ("shud", "yd"),
    resources: dict[str, str | int] | None = None,
) -> JobSpec:
    return JobSpec(
        name=name,
        work_dir=WORK_DIR,
        command=command,
        log_path=LOG_PATH,
        resources=dict(SITE_RESOURCES) if resources is None else resources,
    )


EXPECTED_SBATCH = (
    "sbatch",
    "--parsable",
    "--job-name",
    "ifs-2026082800",
    "--chdir",
    str(WORK_DIR),
    "--output",
    str(LOG_PATH),
    "--error",
    str(LOG_PATH),
    "--account",
    "acct",
    "--cpus-per-task",
    "8",
    "--mem",
    "32G",
    "--partition",
    "cpu",
    "--time",
    "04:00:00",
    "--wrap",
    "shud yd",
)


class RecordingRunner:
    """记录型假 runner：逐次记下 argv 与 env，按队列吐 stdout 或抛异常。"""

    def __init__(self, outputs: list[str | Exception]) -> None:
        self._outputs = list(outputs)
        self.calls: list[tuple[tuple[str, ...], dict[str, str] | None]] = []

    def __call__(self, argv, *, env):
        self.calls.append((tuple(argv), None if env is None else dict(env)))
        if not self._outputs:
            raise AssertionError(f"假 runner 被多调用了一次：{tuple(argv)}")
        result = self._outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    @property
    def count(self) -> int:
        return len(self.calls)


def make_executor(
    outputs: list[str | Exception],
    *,
    required_fields=REQUIRED_FIVE,
    clock: Callable[[], datetime] | None = None,
) -> tuple[SlurmJobExecutor, RecordingRunner]:
    """默认时钟仍是 `StepClock(start=T0, step=STEP)`；宽限窗口的用例自带步长。"""
    runner = RecordingRunner(outputs)
    executor = SlurmJobExecutor(
        required_fields=required_fields,
        clock=StepClock(start=T0, step=STEP) if clock is None else clock,
        runner=runner,
    )
    return executor, runner
