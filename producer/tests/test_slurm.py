"""`yd_producer.slurm` 的 sbatch 装配与 sacct 解析契约测试。

argv 一律**逐元素精确比对**，不做"含某个 flag"式的弱断言——弱断言对顺序错、多一项、
少一项全都放行。时间断言用注入的 `StepClock` 的确定性精确值，不做"约等于现在"。全部
失败路径以 `pytest.raises(ExecutorError)` 表达并机检 `exc.job_id`；只有 fixture 明确
要求"消息含某键名/原异常文本"的几条才探测消息子串。

executor 组装与协议一致性（原 E 段）见 `test_slurm_executor.py`，共享固定输入与假
runner 见 `slurm_fixtures.py`——两者为满足 1000 行文件上限从本文件拆出。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from slurm_fixtures import (
    EXPECTED_SBATCH,
    EXPECTED_STATE_MAP,
    LOG_PATH,
    REQUIRED_FIVE,
    SITE_RESOURCES,
    WORK_DIR,
    make_spec,
)

from yd_producer.executor import ExecutorError, JobState
from yd_producer.slurm import (
    SACCT_ENV,
    SLURM_STATE_MAP,
    build_sacct_command,
    build_sbatch_command,
    parse_sacct_record,
    parse_sbatch_job_id,
)

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


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("12345 \n", "12345"),
        ("12345 ; cluster0\n", "12345"),
    ],
)
def test_parse_sbatch_job_id_strips_surrounding_whitespace(stdout, expected):
    """接受态钉死 `strip()`：去掉它这两条即变红（`"12345 "` 不是全数字，会抛错）。"""
    assert parse_sbatch_job_id(stdout) == expected


def test_parse_sbatch_job_id_skips_whitespace_only_line():
    """「非空行」判据是「`strip()` 后非空」：换成 `if not line:` 即变红。"""
    assert parse_sbatch_job_id("   \n12345\n") == "12345"


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


def test_parse_sacct_record_skips_whitespace_only_line():
    """纯空白行不计入行数：过滤器换成 `if line:` 会误判 2 行而变红。"""
    state, _, _ = parse_sacct_record(
        "   \n12345|COMPLETED|2026-08-28T00:00:00|2026-08-28T01:00:00", "12345"
    )
    assert state is JobState.SUCCEEDED


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


def test_parse_sacct_record_empty_output_wording_is_unchanged():
    """accounting 宽限只落在 `poll` 里：纯函数对 0 行的措辞与绑定 id 一字不改。"""
    with pytest.raises(ExecutorError) as excinfo:
        parse_sacct_record("", "12345")
    assert excinfo.value.job_id == "12345"
    assert "期望恰好 1 行记录，实际 0 行" in str(excinfo.value)
