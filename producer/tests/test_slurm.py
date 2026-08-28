"""`yd_producer.slurm` 的装配 / 解析 / 协议一致性契约测试。

argv 一律**逐元素精确比对**，不做"含某个 flag"式的弱断言——弱断言对顺序错、多一项、
少一项全都放行。时间断言用注入的 `StepClock` 的确定性精确值，不做"约等于现在"。全部
失败路径以 `pytest.raises(ExecutorError)` 表达并机检 `exc.job_id`；只有 fixture 明确
要求"消息含某键名/原异常文本"的几条才探测消息子串。
"""

from __future__ import annotations

import inspect
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from yd_producer.executor import (
    ExecutorError,
    JobExecutor,
    JobSpec,
    JobState,
    StepClock,
)
from yd_producer.slurm import (
    SACCT_ENV,
    SBATCH_FLAGS,
    SLURM_STATE_MAP,
    SlurmJobExecutor,
    build_sacct_command,
    build_sbatch_command,
    parse_sacct_record,
    parse_sbatch_job_id,
    subprocess_runner,
)

# --- 固定输入 ----------------------------------------------------------------

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
) -> tuple[SlurmJobExecutor, RecordingRunner]:
    runner = RecordingRunner(outputs)
    executor = SlurmJobExecutor(
        required_fields=required_fields,
        clock=StepClock(start=T0, step=STEP),
        runner=runner,
    )
    return executor, runner


# --- B. sbatch 装配 ----------------------------------------------------------


def test_sbatch_command_is_exactly_the_pinned_argv():
    """验收 2：装配产物含全部五项资源参数，且整条 argv 逐元素精确相等。"""
    assert (
        build_sbatch_command(make_spec(), required_fields=REQUIRED_FIVE)
        == EXPECTED_SBATCH
    )


def test_sbatch_command_ignores_required_fields_written_order():
    """产物只依赖键集，不依赖 `config.toml` 的行序（资源段按字段名升序）。"""
    shuffled = ("walltime", "cpus", "partition", "memory", "account")
    assert (
        build_sbatch_command(make_spec(), required_fields=shuffled) == EXPECTED_SBATCH
    )


@pytest.mark.parametrize("missing", REQUIRED_FIVE)
def test_sbatch_command_rejects_missing_resource_field(missing):
    """验收 1：缺任一 Slurm 字段时装配报错，消息指名该键。"""
    resources = dict(SITE_RESOURCES)
    del resources[missing]
    with pytest.raises(ExecutorError) as excinfo:
        build_sbatch_command(
            make_spec(resources=resources), required_fields=REQUIRED_FIVE
        )
    assert missing in str(excinfo.value)
    # 装配期失败尚未产生 job id
    assert excinfo.value.job_id is None


def test_sbatch_command_rejects_extra_resource_field():
    """键集相等语义是双向的：多余键同样报错（承 `load_local`）。"""
    resources = dict(SITE_RESOURCES) | {"nodes": 2}
    with pytest.raises(ExecutorError) as excinfo:
        build_sbatch_command(
            make_spec(resources=resources), required_fields=REQUIRED_FIVE
        )
    assert "nodes" in str(excinfo.value)
    assert excinfo.value.job_id is None


def test_sbatch_command_rejects_field_without_flag():
    """现场声明了本模块翻译不了的字段：必须报错，MUST NOT 静默丢弃该约束。"""
    required = (*REQUIRED_FIVE, "gres")
    resources = dict(SITE_RESOURCES) | {"gres": "gpu:1"}
    with pytest.raises(ExecutorError) as excinfo:
        build_sbatch_command(make_spec(resources=resources), required_fields=required)
    assert "gres" in str(excinfo.value)
    assert excinfo.value.job_id is None


def test_sbatch_command_allows_surplus_flags_in_translation_table():
    """config 是键集权威：翻译表富余条目不进产物，也不报错。"""
    required = ("cpus", "memory", "partition")
    resources: dict[str, str | int] = {
        "cpus": 8,
        "memory": "32G",
        "partition": "cpu",
    }
    argv = build_sbatch_command(
        make_spec(resources=resources), required_fields=required
    )
    assert argv == (
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
        "--cpus-per-task",
        "8",
        "--mem",
        "32G",
        "--partition",
        "cpu",
        "--wrap",
        "shud yd",
    )
    assert "--account" not in argv
    assert "--time" not in argv


def test_sbatch_wrap_is_a_single_shell_quoted_argument():
    """`--wrap` 后恒为单个字符串，含空格的元素被 `shlex.join` 引起来。"""
    spec = make_spec(command=("shud", "--in", "a b"))
    argv = build_sbatch_command(spec, required_fields=REQUIRED_FIVE)
    assert len(argv) == len(EXPECTED_SBATCH)
    assert argv[-2] == "--wrap"
    assert argv[-1] == "shud --in 'a b'"


def test_sbatch_merges_stdout_and_stderr_into_one_log():
    """`--output` 与 `--error` 都显式给出且同指一份日志（不依赖隐式并流）。"""
    argv = build_sbatch_command(make_spec(), required_fields=REQUIRED_FIVE)
    assert argv[argv.index("--output") + 1] == str(LOG_PATH)
    assert argv[argv.index("--error") + 1] == str(LOG_PATH)


# --- C. sbatch 输出解析 ------------------------------------------------------


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("12345\n", "12345"),
        ("12345;cluster0\n", "12345"),
        ("\n12345\n", "12345"),
    ],
)
def test_parse_sbatch_job_id_accepts_parsable_forms(stdout, expected):
    assert parse_sbatch_job_id(stdout) == expected


@pytest.mark.parametrize("stdout", ["", "   \n", "abc", ";cluster0"])
def test_parse_sbatch_job_id_rejects_malformed_output(stdout):
    """此刻还没有 id，故 `exc.job_id` 必须是 `None`。"""
    with pytest.raises(ExecutorError) as excinfo:
        parse_sbatch_job_id(stdout)
    assert excinfo.value.job_id is None


# --- D. sacct 命令与输出解析 -------------------------------------------------


def test_sacct_command_is_exactly_the_pinned_argv():
    assert build_sacct_command("12345") == (
        "sacct",
        "-j",
        "12345",
        "-X",
        "--noheader",
        "--parsable2",
        "--format=JobID,State,Start,End",
    )


def test_sacct_env_pins_timezone_and_time_format():
    assert SACCT_ENV["TZ"] == "UTC"
    assert SACCT_ENV["SLURM_TIME_FORMAT"] == "standard"


def test_parse_sacct_record_returns_utc_aware_times():
    state, started_at, ended_at = parse_sacct_record(
        "12345|COMPLETED|2026-08-28T00:00:00|2026-08-28T01:00:00", "12345"
    )
    assert state is JobState.SUCCEEDED
    assert started_at == datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
    assert ended_at == datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
    for value in (started_at, ended_at):
        assert value.tzinfo is not None
        assert value.utcoffset() == timedelta(0)


def test_slurm_state_map_is_exactly_the_pinned_table():
    """词表逐条钉死：期望值写在测试里，不从被测模块反推（否则漏条目也全绿）。"""
    assert dict(SLURM_STATE_MAP) == EXPECTED_STATE_MAP


@pytest.mark.parametrize("raw_state", sorted(EXPECTED_STATE_MAP))
def test_parse_sacct_record_maps_every_known_state(raw_state):
    """词表逐条可达；`TIMEOUT` 断言不是 `FAILED`（终态三分不得折叠）。"""
    expected = EXPECTED_STATE_MAP[raw_state]
    # 终态需带 End、非终态不得带 End，否则会先被 `JobRecord` 的不变式挡住——但本函数
    # 只做解析，不构造记录，故两列统一给值即可逐条覆盖词表
    state, _, _ = parse_sacct_record(
        f"12345|{raw_state}|2026-08-28T00:00:00|2026-08-28T01:00:00", "12345"
    )
    assert state is expected
    if raw_state == "TIMEOUT":
        assert state is JobState.TIMEOUT
        assert state is not JobState.FAILED


def test_parse_sacct_record_truncates_state_suffix():
    """`CANCELLED by 1234` 归一为 `CANCELLED`。"""
    state, _, _ = parse_sacct_record(
        "12345|CANCELLED by 1234|2026-08-28T00:00:00|2026-08-28T01:00:00", "12345"
    )
    assert state is JobState.FAILED


@pytest.mark.parametrize("absent", ["Unknown", "None", ""])
def test_parse_sacct_record_absent_times_become_none(absent):
    state, started_at, ended_at = parse_sacct_record(
        f"12345|PENDING|{absent}|{absent}", "12345"
    )
    assert state is JobState.PENDING
    assert started_at is None
    assert ended_at is None


@pytest.mark.parametrize(
    "stdout",
    [
        pytest.param("12345|BOGUS_STATE|Unknown|Unknown", id="unknown-state"),
        pytest.param("", id="empty-output"),
        pytest.param(
            "12345|COMPLETED|2026-08-28T00:00:00|2026-08-28T01:00:00\n"
            "12345|FAILED|2026-08-28T00:00:00|2026-08-28T01:00:00",
            id="two-rows",
        ),
        pytest.param("12345|COMPLETED|2026-08-28T00:00:00", id="three-columns"),
        pytest.param(
            "12345|COMPLETED|2026-08-28T00:00:00|2026-08-28T01:00:00|extra",
            id="five-columns",
        ),
        pytest.param(
            "99999|COMPLETED|2026-08-28T00:00:00|2026-08-28T01:00:00",
            id="job-id-mismatch",
        ),
        pytest.param(
            "12345|COMPLETED|28/08/2026 00:00|2026-08-28T01:00:00",
            id="bad-time-format",
        ),
    ],
)
def test_parse_sacct_record_fails_closed(stdout):
    """未知状态串不兜底为 FAILED、空输出不兜底为 PENDING、多行不静默取首行。"""
    with pytest.raises(ExecutorError) as excinfo:
        parse_sacct_record(stdout, "12345")
    assert excinfo.value.job_id == "12345"


def test_parse_sacct_record_extra_columns_reports_arity():
    """多列必须命中列数守卫本身，而非解包时的 ValueError。

    共享的 fails_closed 参数化只断言"抛了 ExecutorError"，多列这一例在 `columns`
    改成 `columns[:4]` 之类的写法下会退化成静默错解析。这里单列一个用例，直接钉
    住列数契约的措辞（期望 4 列 / 实际 5 列），把 oracle 从"抛了点什么"提升到
    "抛的是列数不符"。
    """
    with pytest.raises(ExecutorError) as excinfo:
        parse_sacct_record(
            "12345|COMPLETED|2026-08-28T00:00:00|2026-08-28T01:00:00|extra", "12345"
        )
    assert excinfo.value.job_id == "12345"
    assert "期望 4 列" in str(excinfo.value)
    assert "实际 5 列" in str(excinfo.value)


# --- E. 协议一致性与组装 -----------------------------------------------------


def test_executor_satisfies_runtime_checkable_protocol():
    executor, _ = make_executor([])
    assert isinstance(executor, JobExecutor)


@pytest.mark.parametrize("method", ["submit", "poll"])
def test_executor_method_signature_matches_protocol(method):
    """`runtime_checkable` 只校验方法存在，故按签名逐参数比对。"""
    expected = inspect.signature(getattr(JobExecutor, method))
    actual = inspect.signature(getattr(SlurmJobExecutor, method))
    assert actual == expected


def test_submit_builds_argv_and_stamps_with_injected_clock():
    executor, runner = make_executor(["12345\n"])
    spec = make_spec()
    record = executor.submit(spec)

    assert runner.calls[0][0] == build_sbatch_command(
        spec, required_fields=REQUIRED_FIVE
    )
    assert record.job_id == "12345"
    assert record.name == "ifs-2026082800"
    assert record.state is JobState.PENDING
    assert record.started_at is None
    assert record.ended_at is None
    assert record.submitted_at == tick(0)
    assert dict(record.resources) == SITE_RESOURCES


def test_poll_queries_sacct_and_rebuilds_record_from_submission():
    executor, runner = make_executor(
        ["12345\n", "12345|RUNNING|2026-08-28T00:00:00|Unknown"]
    )
    submitted = executor.submit(make_spec())
    record = executor.poll("12345")

    assert runner.calls[1][0] == build_sacct_command("12345")
    assert record.job_id == "12345"
    assert record.state is JobState.RUNNING
    assert record.started_at == datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
    assert record.ended_at is None
    assert record.name == submitted.name
    assert record.submitted_at == submitted.submitted_at
    assert dict(record.resources) == dict(submitted.resources)


def test_terminal_poll_is_idempotent_and_stops_calling_runner():
    executor, runner = make_executor(
        [
            "12345\n",
            "12345|RUNNING|2026-08-28T00:00:00|Unknown",
            "12345|COMPLETED|2026-08-28T00:00:00|2026-08-28T01:00:00",
        ]
    )
    executor.submit(make_spec())
    executor.poll("12345")
    terminal = executor.poll("12345")
    assert terminal.state is JobState.SUCCEEDED

    calls_at_terminal = runner.count
    assert executor.poll("12345") == terminal
    assert executor.poll("12345") == terminal
    assert runner.count == calls_at_terminal


def test_poll_unknown_job_id_raises_before_touching_runner():
    executor, runner = make_executor([])
    with pytest.raises(ExecutorError) as excinfo:
        executor.poll("nosuch")
    assert excinfo.value.job_id == "nosuch"
    assert runner.count == 0


def test_poll_env_overlays_sacct_env_onto_process_env(monkeypatch):
    """叠加而非替换，且 `SACCT_ENV` 在后：两条断言各挡一种错法。"""
    monkeypatch.setenv("YD_SENTINEL", "1")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    executor, runner = make_executor(
        ["12345\n", "12345|RUNNING|2026-08-28T00:00:00|Unknown"]
    )
    executor.submit(make_spec())
    executor.poll("12345")

    submit_env = runner.calls[0][1]
    poll_env = runner.calls[1][1]
    assert submit_env is None
    assert poll_env is not None
    assert poll_env["YD_SENTINEL"] == "1"
    assert poll_env["TZ"] == "UTC"
    assert poll_env["SLURM_TIME_FORMAT"] == "standard"
    assert poll_env["PATH"] == os.environ["PATH"]


@pytest.mark.parametrize(
    ("error", "diagnostic"),
    [
        pytest.param(
            OSError("sbatch: command not found"),
            "sbatch: command not found",
            id="oserror",
        ),
        pytest.param(
            subprocess.CalledProcessError(1, ["sbatch"], stderr="invalid partition"),
            "invalid partition",
            # `str(CalledProcessError)` 只有"退出码非零"，诊断原文只在 `stderr` 里
            id="called-process-error",
        ),
    ],
)
def test_submit_translates_runner_exception(error, diagnostic):
    """原生异常 MUST NOT 外泄，且转译消息须带诊断原文（否则只剩无信息消息）。"""
    executor, _ = make_executor([error])
    with pytest.raises(ExecutorError) as excinfo:
        executor.submit(make_spec())
    assert str(error) in str(excinfo.value)
    assert diagnostic in str(excinfo.value)
    # 提交尚未拿到 id，失败与具体作业无关
    assert excinfo.value.job_id is None


@pytest.mark.parametrize(
    ("error", "diagnostic"),
    [
        pytest.param(
            OSError("sacct: connection refused"),
            "sacct: connection refused",
            id="oserror",
        ),
        pytest.param(
            subprocess.CalledProcessError(1, ["sacct"], stderr="db down"),
            "db down",
            id="called-process-error",
        ),
    ],
)
def test_poll_translates_runner_exception(error, diagnostic):
    executor, _ = make_executor(["12345\n", error])
    executor.submit(make_spec())
    with pytest.raises(ExecutorError) as excinfo:
        executor.poll("12345")
    assert str(error) in str(excinfo.value)
    assert diagnostic in str(excinfo.value)
    # 轮询失败涉事作业明确，转译 MUST 带上该 id
    assert excinfo.value.job_id == "12345"


def test_poll_lets_job_record_invariant_reject_backwards_start():
    """`Start` 早于 `submitted_at` 由 `JobRecord.__post_init__` 拦下（无第二权威）。"""
    before_submit = (T0 - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    executor, _ = make_executor(
        ["12345\n", f"12345|COMPLETED|{before_submit}|2026-08-28T01:00:00"]
    )
    executor.submit(make_spec())
    with pytest.raises(ExecutorError) as excinfo:
        executor.poll("12345")
    assert excinfo.value.job_id == "12345"


@pytest.mark.parametrize("omitted", ["required_fields", "clock", "runner"])
def test_executor_constructor_has_no_defaults(omitted):
    """验收 3：三个构造参数均不可省（零内置默认）。"""
    kwargs = {
        "required_fields": REQUIRED_FIVE,
        "clock": StepClock(start=T0, step=STEP),
        "runner": RecordingRunner([]),
    }
    del kwargs[omitted]
    with pytest.raises(TypeError):
        SlurmJobExecutor(**kwargs)


def test_executor_constructor_params_are_keyword_only():
    params = inspect.signature(SlurmJobExecutor.__init__).parameters
    for name in ("required_fields", "clock", "runner"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[name].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        SlurmJobExecutor(REQUIRED_FIVE, StepClock(start=T0, step=STEP), lambda *_: "")


def test_subprocess_runner_signature_matches_runner_contract():
    """符号必须存在且签名对得上——否则它可以完全不存在而全绿（仓库无类型检查闸）。"""
    params = list(inspect.signature(subprocess_runner).parameters.values())
    assert [p.name for p in params] == ["argv", "env"]
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[1].kind is inspect.Parameter.KEYWORD_ONLY
    assert params[1].default is inspect.Parameter.empty


def test_translation_table_covers_the_five_site_fields():
    """翻译表是 flag 权威：五项字段的长选项逐条钉死（`sbatch` flag 无法从键名推导）。"""
    assert SBATCH_FLAGS["partition"] == "--partition"
    assert SBATCH_FLAGS["account"] == "--account"
    assert SBATCH_FLAGS["cpus"] == "--cpus-per-task"
    assert SBATCH_FLAGS["memory"] == "--mem"
    assert SBATCH_FLAGS["walltime"] == "--time"
