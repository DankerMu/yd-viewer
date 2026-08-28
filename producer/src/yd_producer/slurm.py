"""`JobExecutor` 的 Slurm 生产实现：argv 装配、`sacct` 输出解析与注入式运行器。

`yd_producer.executor` 是**不透明协议层**：它既不解释 `resources` 的键、也不声明键
集。本模块是那份不透明契约的**受权解释点**——把 `local.toml` 的资源字段名翻译成
`sbatch` 长选项（`cpus`→`--cpus-per-task` 这类映射无法从键名推导），并把 `sacct` 的
输出翻回 `JobState` 与 tz-aware UTC 时间。故调度字段的字面量出现在本模块，而 protocol
层保持零字面量（`test_executor.py` 的源码机检守的正是那一层）。

设计约束：

* **只用 stdlib**，不新增依赖。
* **键集权威唯一**：必需字段清单由调用方每次传入（其唯一权威是
  `Config.slurm.required_fields`）。本模块 MUST NOT 写死一份"必需五项"，也 MUST NOT
  拿 `SBATCH_FLAGS` 的键集反推必需性——那是第二权威。翻译表允许有富余条目（现场没
  声明的字段就是不装配），但反过来 `required_fields` 里出现无 flag 的字段名必须报错：
  静默丢弃等于把一条现场配置的资源约束扔掉后照常提交。
* **零内置默认**：任何资源参数都没有 fallback 取值；`required_fields`/`clock`/`runner`
  一律 keyword-only 且无默认。未知 `sacct` 状态串不兜底为 `FAILED`（那会把"没见过的
  调度器状态"伪装成"作业自身失败"，销毁 `TIMEOUT`/`FAILED` 分立要保的运维判据），空
  `sacct` 输出不兜底为 `PENDING`。
* **不变式不复制**：记录的时序不变式仍由 `JobRecord.__post_init__` 在构造点独家强制，
  本模块只负责把解析结果交给构造器，不自己再写一份。
* **时区在子进程侧钉死**：`sacct` 默认吐集群本地时间，而 `JobRecord` 对 naive 与非零
  偏移一律 fail closed，故 `poll` 以 `{**os.environ, **SACCT_ENV}` 调用 runner——叠加
  而非替换：只传 `SACCT_ENV` 会让子进程丢掉 `PATH` 与 Slurm 客户端环境。
* **进程边界注入**：`runner` 是本模块自身的进程边界，注入它测的是 argv 装配与输出解析
  的组装；真实 `sbatch`/`sacct` 行为归 M4 现场（`subprocess_runner` 不测行为）。
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from .executor import ExecutorError, JobRecord, JobSpec, JobState

__all__ = [
    "SACCT_ENV",
    "SBATCH_FLAGS",
    "SLURM_STATE_MAP",
    "SlurmJobExecutor",
    "build_sacct_command",
    "build_sbatch_command",
    "parse_sacct_record",
    "parse_sbatch_job_id",
    "subprocess_runner",
]


#: 资源字段名 -> `sbatch` 长选项。**翻译**权威，不是**键集**权威。
SBATCH_FLAGS: Mapping[str, str] = {
    "account": "--account",
    "cpus": "--cpus-per-task",
    "memory": "--mem",
    "partition": "--partition",
    "walltime": "--time",
}

#: `sacct` 子进程的环境叠加项：钉死时区与时间格式，使输出可按固定格式解析为 UTC。
SACCT_ENV: Mapping[str, str] = {
    "TZ": "UTC",
    "SLURM_TIME_FORMAT": "standard",
}

#: `sacct` 状态串 -> `JobState`。`TIMEOUT` 不折叠进 `FAILED`；重排队是健康中间态。
SLURM_STATE_MAP: Mapping[str, JobState] = {
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

#: `sacct` 的时间列格式（`SLURM_TIME_FORMAT=standard` 下的形态）。
_SACCT_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"

#: 时间列的"无值"写法；`sacct` 对尚未起跑/尚未结束的作业吐这些。
_SACCT_TIME_ABSENT = frozenset({"", "Unknown", "None"})

#: `sacct --format` 的列顺序，即本模块的解析 schema。
_SACCT_FORMAT = "JobID,State,Start,End"


# --- 纯函数：命令装配与输出解析 ----------------------------------------------


def build_sbatch_command(
    spec: JobSpec, *, required_fields: Sequence[str]
) -> tuple[str, ...]:
    """把一次提交装配为 `sbatch` argv。

    `required_fields` 由调用方按 `Config.slurm.required_fields` 传入，是键集的唯一
    权威；`spec.resources` 的键集必须与之完全相等（缺项或多余项都是现场配置与声明
    不一致，承 `load_local` 的键集相等语义）。

    stdout 与 stderr 都指向 `spec.log_path`：「失败处理」要求合成一份日志，两个 flag
    都显式给出，不依赖"省略 `--error` 时 Slurm 隐式并流"这一隐含默认。

    资源段按字段名 `sorted()` 升序展开，使产物只依赖键集而不依赖 `config.toml` 里的
    行序。
    """
    declared = set(required_fields)
    present = set(spec.resources)
    missing = sorted(declared - present)
    extra = sorted(present - declared)
    if missing:
        raise ExecutorError(
            f"作业 `{spec.name}` 的资源参数缺少 `{missing[0]}`（共缺 "
            f"{len(missing)} 项，键集必须与 `slurm.required_fields` 完全一致）"
        )
    if extra:
        raise ExecutorError(
            f"作业 `{spec.name}` 的资源参数多出 `{extra[0]}`（共多 "
            f"{len(extra)} 项，键集必须与 `slurm.required_fields` 完全一致）"
        )

    argv: list[str] = [
        "sbatch",
        "--parsable",
        "--job-name",
        spec.name,
        "--chdir",
        str(spec.work_dir),
        "--output",
        str(spec.log_path),
        "--error",
        str(spec.log_path),
    ]
    for name in sorted(required_fields):
        flag = SBATCH_FLAGS.get(name)
        if flag is None:
            # 静默丢弃等于把一条现场声明的资源约束扔掉后照常提交
            raise ExecutorError(
                f"资源字段 `{name}` 没有对应的 sbatch 选项，无法装配（本模块的翻译表"
                "需要先支持该字段）"
            )
        argv.extend((flag, str(spec.resources[name])))
    argv.extend(("--wrap", shlex.join(spec.command)))
    return tuple(argv)


def parse_sbatch_job_id(stdout: str) -> str:
    """从 `sbatch --parsable` 的输出取 job id。

    `--parsable` 下输出为 `<jobid>` 或 `<jobid>;<cluster>`。取首个非空行、`;` 前首段。
    此刻还没有 id，故失败时 `ExecutorError.job_id` 为 `None`。
    """
    for line in stdout.splitlines():
        if not line.strip():
            continue
        job_id = line.split(";", 1)[0].strip()
        if not job_id.isdigit():
            raise ExecutorError(f"sbatch 输出的 job id 非全数字：{line.strip()!r}")
        return job_id
    raise ExecutorError(f"sbatch 没有输出 job id：{stdout!r}")


def build_sacct_command(job_id: str) -> tuple[str, ...]:
    """装配单作业查询命令。

    `-X` 不可省：缺它 `sacct` 会连作业步（`.batch`/`.extern`）一起吐，解析会拿到多行
    且首行未必是分配本体。不取 `ExitCode`（`JobRecord` 没有承载它的字段，加字段即改
    协议层 schema），不取 `Submit`（`submitted_at` 由 `submit` 一次性写定，同一字段不
    设第二权威）。
    """
    return (
        "sacct",
        "-j",
        job_id,
        "-X",
        "--noheader",
        "--parsable2",
        f"--format={_SACCT_FORMAT}",
    )


def _parse_sacct_time(raw: str, column: str, job_id: str) -> datetime | None:
    value = raw.strip()
    if value in _SACCT_TIME_ABSENT:
        return None
    try:
        # 时区由 `SACCT_ENV` 在子进程侧钉死为 UTC，此处紧接着补挂 tzinfo
        parsed = datetime.strptime(value, _SACCT_TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise ExecutorError(
            f"sacct 的 `{column}` 列无法按 `{_SACCT_TIME_FORMAT}` 解析：{value!r}",
            job_id,
        ) from exc
    return parsed


def parse_sacct_record(
    stdout: str, job_id: str
) -> tuple[JobState, datetime | None, datetime | None]:
    """解析 `sacct` 的单行记录，返回 `(state, started_at, ended_at)`。

    fail closed 的三处：非空行数不为 1（`-X` 下多行意味着出现了未预期的作业副本，静默
    取首行会让 `poll` 报告一个没被查询的实体）、首列 JobID 串台、未知状态串。
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ExecutorError(
            f"sacct 对 job `{job_id}` 期望恰好 1 行记录，实际 {len(lines)} 行",
            job_id,
        )

    columns = lines[0].split("|")
    if len(columns) != 4:
        raise ExecutorError(
            f"sacct 记录期望 4 列（{_SACCT_FORMAT}），实际 {len(columns)} 列："
            f"{lines[0]!r}",
            job_id,
        )

    reported_id, raw_state, raw_start, raw_end = columns
    if reported_id.strip() != job_id:
        raise ExecutorError(
            f"sacct 返回的 JobID `{reported_id.strip()}` 与查询的 `{job_id}` 不符",
            job_id,
        )

    # `CANCELLED by 1234` 一类形态先截首词再查表
    token = raw_state.strip().split(" ", 1)[0]
    state = SLURM_STATE_MAP.get(token)
    if state is None:
        raise ExecutorError(
            f"未知的 sacct 状态串 `{token}`（不兜底映射为 FAILED：那会把未见过的调度器"
            "状态伪装成作业自身失败）",
            job_id,
        )

    started_at = _parse_sacct_time(raw_start, "Start", job_id)
    ended_at = _parse_sacct_time(raw_end, "End", job_id)
    return state, started_at, ended_at


# --- 真实进程边界 ------------------------------------------------------------


def subprocess_runner(argv: Sequence[str], *, env: Mapping[str, str] | None) -> str:
    """真实运行器：执行 argv 并返回 stdout；非零退出码抛 `CalledProcessError`。

    本函数是真实进程边界，其行为归 M4 现场验证；`SlurmJobExecutor` 把它抛出的任何
    异常转译为 `ExecutorError`。
    """
    completed = subprocess.run(
        list(argv),
        check=True,
        capture_output=True,
        text=True,
        env=None if env is None else dict(env),
    )
    return completed.stdout


# --- 生产执行器 --------------------------------------------------------------


class SlurmJobExecutor:
    """`JobExecutor` 的 Slurm 实现：`sbatch` 提交、`sacct` 轮询。

    实例内按 `job_id` 记住提交记录：`poll` 需要 `name`/`resources`/`submitted_at`，而
    `sacct` 不提供它们。故只支持"同一 run 进程内提交后轮询"——这正是「并发与锁」的
    形态（单进程持 flock 覆盖提交→等待→发布全生命周期），不做跨进程持久化。

    三个构造参数均 keyword-only 且无默认值：键集权威在外，时钟必须可注入（不读挂钟），
    进程边界必须可替换。
    """

    def __init__(
        self,
        *,
        required_fields: Sequence[str],
        clock: Callable[[], datetime],
        runner: Callable[..., str],
    ) -> None:
        self._required_fields = tuple(required_fields)
        self._clock = clock
        self._runner = runner
        self._records: dict[str, JobRecord] = {}

    def submit(self, spec: JobSpec) -> JobRecord:
        argv = build_sbatch_command(spec, required_fields=self._required_fields)
        stdout = self._run(argv, env=None, job_id=None)
        job_id = parse_sbatch_job_id(stdout)
        record = JobRecord(
            job_id=job_id,
            name=spec.name,
            state=JobState.PENDING,
            resources=spec.resources,
            submitted_at=self._clock(),
            started_at=None,
            ended_at=None,
        )
        self._records[job_id] = record
        return record

    def poll(self, job_id: str) -> JobRecord:
        record = self._records.get(job_id)
        if record is None:
            raise ExecutorError(
                f"未知 job id `{job_id}`：本执行器实例没有提交过该作业", job_id
            )
        if record.state.is_terminal:
            # 终态幂等：不再查 sacct，也不迁回非终态
            return record

        # 叠加而非替换：子进程仍需 `PATH` 与 Slurm 客户端环境；在调用点取 `os.environ`
        env = {**os.environ, **SACCT_ENV}
        stdout = self._run(build_sacct_command(job_id), env=env, job_id=job_id)
        state, started_at, ended_at = parse_sacct_record(stdout, job_id)
        # 不变式由 `JobRecord.__post_init__` 复检；构造失败即不落库，记录不半更新
        updated = JobRecord(
            job_id=record.job_id,
            name=record.name,
            state=state,
            resources=record.resources,
            submitted_at=record.submitted_at,
            started_at=started_at,
            ended_at=ended_at,
        )
        self._records[job_id] = updated
        return updated

    def _run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None,
        job_id: str | None,
    ) -> str:
        """调用注入的 runner，并把它抛出的任何异常转译为 `ExecutorError`。

        转译带上原异常文本：`sbatch`/`sacct` 的诊断信息走 stderr 而 runner 只回
        stdout，丢掉原文就只剩一条无信息的消息。
        """
        try:
            return self._runner(argv, env=env)
        except ExecutorError:
            raise
        except Exception as exc:
            message = f"执行 `{argv[0]}` 失败：{exc.__class__.__name__}: {exc}"
            # `CalledProcessError` 的 `str()` 只有退出码，诊断原文在 `stderr` 上；而
            # `OSError` 一类根本没有该属性，故用 `getattr` 取。stdout 不并入：失败时
            # 它要么为空要么是半截输出，只会稀释诊断。
            stderr = getattr(exc, "stderr", None)
            if stderr:
                message = f"{message}\nstderr: {str(stderr).strip()}"
            raise ExecutorError(message, job_id) from exc
