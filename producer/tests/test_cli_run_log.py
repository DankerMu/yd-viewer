"""`yd-producer run` 的 stderr 行格式（issue #347；compute-loop §6「`run` stderr 行格式」）。

一切经 `cli.main(argv, env=...)` 行使（`_run_exit` 的穷举标签表除外）。期望值取自规范
文本：每个物理行以 `YYYY-MM-DDTHH:MM:SSZ ` 开头；结果行 `<标签>：<detail>`，
`SUCCEEDED`→完成、`STOPPED`+`raw_incomplete`→等待、其余→错误；退出码只由结果决定。
非 run 输出（prepare/init/`DATABASE_URL` 守卫/argparse）MUST NOT 带前缀。
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from cli_fixtures import write_config, write_fake_interpreter, write_local

from yd_producer import cli
from yd_producer import prepare as prepare_module
from yd_producer.config import ConfigError, load_config, load_local
from yd_producer.controller import (
    JobRunReport,
    RunError,
    RunOutcome,
    RunReport,
    RunSourcesError,
    RunSourcesReport,
    StopReason,
)
from yd_producer.executor import ExecutorError, JobState
from yd_producer.publish import PublishResult
from yd_producer.runlock import RunLockError, RunLockResult

STAMPED = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ")
STAMP_WIDTH = len("2026-09-24T00:00:00Z ")
CYCLE = datetime(2026, 1, 2, 0, tzinfo=UTC)
NEXT_CYCLE = datetime(2026, 1, 2, 12, tzinfo=UTC)
_DB_URL = "postgresql://user:secret@db.example:5432/nwm"


# --- 报告构造（真实 RunReport，经其 __post_init__ 校验）---------------------------


def _job(state=JobState.SUCCEEDED) -> JobRunReport:
    return JobRunReport(
        job_id="1",
        partition="cpu",
        state=state,
        submitted_at=CYCLE,
        started_at=CYCLE,
        ended_at=CYCLE,
    )


def _succeeded(source: str, detail: str) -> RunReport:
    done = Path(f"/tmp/{source}/DONE")
    published = PublishResult(
        source=source,
        cycle=CYCLE,
        next_cycle=NEXT_CYCLE,
        dat_path=Path(f"/tmp/{source}/yd.rivqdown.dat"),
        state_path=Path(f"/tmp/{source}/state.cfg.ic"),
        done_path=done,
        removed_state_files=(),
        removed_work_dir=Path(f"/tmp/{source}/work"),
    )
    return RunReport(
        source=source,
        cycle=CYCLE,
        outcome=RunOutcome.SUCCEEDED,
        stop_reason=None,
        detail=detail,
        job=_job(),
        published=published,
        done_path=done,
    )


def _stopped(source: str, detail: str, reason=StopReason.RAW_INCOMPLETE) -> RunReport:
    return RunReport(
        source=source,
        cycle=NEXT_CYCLE,
        outcome=RunOutcome.STOPPED,
        stop_reason=reason,
        detail=detail,
        job=None,
        published=None,
        done_path=None,
    )


def _submitted_failure(source: str, detail: str, outcome: RunOutcome) -> RunReport:
    pending = outcome is RunOutcome.SUCCEEDED_CLEANUP_PENDING
    return RunReport(
        source=source,
        cycle=CYCLE,
        outcome=outcome,
        stop_reason=None,
        detail=detail,
        job=_job(JobState.SUCCEEDED if pending else JobState.FAILED),
        published=None,
        done_path=Path(f"/tmp/{source}/DONE") if pending else None,
    )


def _pair(ifs, gfs):
    # 末项为 SUCCEEDED 的组合过不了 RunSourcesReport 的校验，但 `_run_exit` 只读
    # `.ifs`/`.gfs`；需要"全成功"或"某源单独成功"时用同形命名空间承载真实 RunReport。
    return SimpleNamespace(ifs=tuple(ifs), gfs=tuple(gfs))


# --- 入口装配 ---------------------------------------------------------------------


def _exit_code(argv, env):
    try:
        return cli.main(argv, env=env)
    except SystemExit as exc:
        return exc.code


def _argv(command, tmp_path, **local_kwargs):
    config_path = write_config(tmp_path)
    local_path = write_local(tmp_path, **local_kwargs)
    local = load_local(local_path, load_config(config_path))
    for path in (local.yd_root, local.scratch_root, Path(local.cron.lock_path).parent):
        Path(path).mkdir(parents=True, exist_ok=True)
    argv = [command, "--config", str(config_path), "--local", str(local_path)]
    if command == "prepare":
        argv += ["--baseline", str(tmp_path / "baseline")]
    return argv, local


def _ready_run_argv(tmp_path):
    argv, local = _argv("run", tmp_path)
    source_states = Path(local.yd_root) / "states" / "gfs"
    source_states.mkdir(parents=True, exist_ok=True)
    (source_states / "2026010200.cfg.ic").write_bytes(b"presence")
    return argv


def _prepare_argv(tmp_path):
    script = write_fake_interpreter(
        tmp_path.resolve() / "fake-python", tmp_path.resolve() / "record.json"
    )
    return _argv("prepare", tmp_path, python=script)[0]


def _hold_lock(monkeypatch):
    monkeypatch.setattr(
        cli,
        "run_with_lock",
        lambda **kw: RunLockResult(
            acquired=True, lock_path=Path(kw["lock_path"]), value=kw["action"]()
        ),
    )


def _run_with_report(monkeypatch, tmp_path, report):
    _hold_lock(monkeypatch)
    monkeypatch.setattr(cli, "run_sources", lambda **_: report)
    return _exit_code(_ready_run_argv(tmp_path), env={})


def _unstamped(err: str) -> list[str]:
    """断言每个物理行都带时间前缀，返回去掉前缀后的行。"""
    assert err.endswith("\n")
    lines = err.split("\n")[:-1]
    assert lines
    for line in lines:
        assert STAMPED.match(line), line
    return [line[STAMP_WIDTH:] for line in lines]


def _stamps(err: str) -> set[str]:
    return {line[: STAMP_WIDTH - 1] for line in err.split("\n")[:-1]}


# --- 2.1 / 2.2 / 2.3：结果行 ----------------------------------------------------------


def test_published_and_waiting_sources_are_two_labelled_lines(
    monkeypatch, capsys, tmp_path
):
    report = _pair(
        [_succeeded("ifs", "ifs: 一轮成功发布完成（T=2026010200）")],
        [_stopped("gfs", "gfs: raw 未齐（T=2026010212）")],
    )
    assert _run_with_report(monkeypatch, tmp_path, report) == 3
    err = capsys.readouterr().err
    lines = err.splitlines()
    assert len(lines) == 2
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z 完成：ifs: ", lines[0])
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z 等待：gfs: ", lines[1])
    assert _unstamped(err) == [
        "完成：ifs: 一轮成功发布完成（T=2026010200）",
        "等待：gfs: raw 未齐（T=2026010212）",
    ]


def test_all_success_writes_two_completed_lines_in_utc(monkeypatch, capsys, tmp_path):
    report = _pair([_succeeded("ifs", "ifs ok")], [_succeeded("gfs", "gfs ok")])
    before = datetime.now(UTC).replace(microsecond=0)
    assert _run_with_report(monkeypatch, tmp_path, report) == 0
    after = datetime.now(UTC)
    err = capsys.readouterr().err
    assert _unstamped(err) == ["完成：ifs ok", "完成：gfs ok"]
    for stamp in _stamps(err):
        written = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        assert before <= written <= after + timedelta(seconds=1)


@pytest.mark.parametrize(
    "failure",
    [
        _stopped("ifs", "ifs: 状态缺失", StopReason.STATE_MISSING),
        _submitted_failure("ifs", "ifs: 作业失败", RunOutcome.JOB_FAILED),
        _submitted_failure(
            "ifs", "ifs: 清理待办", RunOutcome.SUCCEEDED_CLEANUP_PENDING
        ),
    ],
    ids=["stopped-state-missing", "job-failed", "cleanup-pending"],
)
def test_failure_outcomes_are_labelled_error_and_exit_three(
    monkeypatch, capsys, tmp_path, failure
):
    report = _pair([failure], [_succeeded("gfs", "gfs ok")])
    assert _run_with_report(monkeypatch, tmp_path, report) == 3
    assert _unstamped(capsys.readouterr().err) == [
        f"错误：{failure.detail}",
        "完成：gfs ok",
    ]


def test_empty_detail_falls_back_to_outcome_name(monkeypatch, capsys, tmp_path):
    report = RunSourcesReport(
        ifs=(
            _succeeded("ifs", ""),
            _submitted_failure("ifs", "", RunOutcome.JOB_FAILED),
        ),
        gfs=(_stopped("gfs", ""),),
    )
    assert _run_with_report(monkeypatch, tmp_path, report) == 3
    assert _unstamped(capsys.readouterr().err) == [
        "完成：SUCCEEDED",
        "错误：JOB_FAILED",
        "等待：STOPPED",
    ]


@pytest.mark.parametrize("reason", list(StopReason), ids=lambda r: r.value)
def test_only_raw_incomplete_stop_is_waiting(capsys, reason):
    code = cli._run_exit(_pair([_stopped("ifs", "x", reason)], []))
    label = "等待" if reason is StopReason.RAW_INCOMPLETE else "错误"
    assert code == 3
    assert _unstamped(capsys.readouterr().err) == [f"{label}：x"]


# --- 2.5b / 2.5c：多行 detail 与多条结果 ------------------------------------------------


def test_multiline_detail_and_multiple_reports_per_source(
    monkeypatch, capsys, tmp_path
):
    audit = "startup cleanup: ifs/2026010100 /scratch/yd/ifs/2026010100"
    report = RunSourcesReport(
        ifs=(
            _succeeded("ifs", f"{audit}\nifs: 一轮成功发布完成（T=2026010200）"),
            _stopped("ifs", "ifs: raw 未齐（T=2026010212）"),
        ),
        gfs=(_stopped("gfs", "gfs: raw 未齐（T=2026010212）"),),
    )
    assert _run_with_report(monkeypatch, tmp_path, report) == 3
    err = capsys.readouterr().err
    assert _unstamped(err) == [
        f"完成：{audit}",
        "ifs: 一轮成功发布完成（T=2026010200）",
        "等待：ifs: raw 未齐（T=2026010212）",
        "等待：gfs: raw 未齐（T=2026010212）",
    ]
    assert err.count(audit) == 1
    first_two = err.split("\n")[:2]
    assert first_two[0][:STAMP_WIDTH] == first_two[1][:STAMP_WIDTH]


def test_multi_report_source_writes_one_line_per_report_in_order(
    monkeypatch, capsys, tmp_path
):
    report = RunSourcesReport(
        ifs=(
            _succeeded("ifs", "ifs: 发布 T"),
            _stopped("ifs", "ifs: 等 T+12"),
        ),
        gfs=(_stopped("gfs", "gfs: 等 T"),),
    )
    assert _run_with_report(monkeypatch, tmp_path, report) == 3
    assert _unstamped(capsys.readouterr().err) == [
        "完成：ifs: 发布 T",
        "等待：ifs: 等 T+12",
        "等待：gfs: 等 T",
    ]


# --- 2.4：RunSourcesError 聚合文本 ---------------------------------------------------


def test_run_sources_error_is_printed_once_with_every_line_stamped(
    monkeypatch, capsys, tmp_path
):
    ifs_error = RunError("ifs boom", phase="cleanup", source="ifs", job_id="11")
    ifs_error.add_note("startup deleted ifs/2026010200 /scratch/a")
    gfs_error = RunError("gfs boom", phase="submit", source="gfs", job_id="22")
    gfs_error.add_note("gfs note unique")
    error = RunSourcesError(
        {"ifs": (), "gfs": ()}, {"ifs": ifs_error, "gfs": gfs_error}
    )

    def fake_lock(*, lock_path, action):
        raise error

    monkeypatch.setattr(cli, "run_with_lock", fake_lock)
    assert _exit_code(_ready_run_argv(tmp_path), env={}) == 3
    err = capsys.readouterr().err
    assert "\n".join(_unstamped(err)) == f"错误：{error}"
    assert len(_stamps(err)) == 1
    assert err.count("ifs boom") == err.count("gfs boom") == 1
    assert err.count("startup deleted ifs/2026010200 /scratch/a") == 1
    assert err.count("gfs note unique") == 1
    assert err.index("ifs boom") < err.index("gfs boom")
    assert "Traceback" not in err


# --- 2.5：配置、状态守卫、锁竞争 ------------------------------------------------------


def test_run_load_time_config_error_is_stamped_and_exits_two(capsys, tmp_path):
    argv = _ready_run_argv(tmp_path)
    missing = tmp_path / "absent-config.toml"
    argv[argv.index("--config") + 1] = str(missing)
    assert _exit_code(argv, env={}) == 2
    lines = _unstamped(capsys.readouterr().err)
    assert lines[0].startswith("错误：")
    assert str(missing.resolve()) in lines[0]


def test_run_delegation_config_error_note_is_stamped(monkeypatch, capsys, tmp_path):
    def raising(local, config):
        exc = ConfigError("run 装配配置非法")
        exc.add_note("run 装配 note")
        raise exc

    monkeypatch.setattr(cli, "run", raising)
    assert _exit_code(_ready_run_argv(tmp_path), env={}) == 2
    assert _unstamped(capsys.readouterr().err) == [
        "错误：run 装配配置非法",
        "run 装配 note",
    ]


def test_run_states_guard_is_stamped_and_exits_one(capsys, tmp_path):
    argv, local = _argv("run", tmp_path)
    assert _exit_code(argv, env={}) == 1
    lines = _unstamped(capsys.readouterr().err)
    assert len(lines) == 1
    assert lines[0].startswith(
        f"错误：状态目录不存在：{Path(local.yd_root) / 'states'}"
    )


def test_run_lock_contention_stays_silent(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        cli,
        "run_with_lock",
        lambda **kw: RunLockResult(acquired=False, lock_path=Path(kw["lock_path"])),
    )
    assert _exit_code(_ready_run_argv(tmp_path), env={}) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


# --- 2.5a：运行期异常路径 --------------------------------------------------------------


def _run_error():
    exc = RunError("run boom", phase="submit", source="ifs", job_id="11")
    exc.add_note("run note")
    return exc, ["错误：run boom", "source=ifs", "phase=submit", "job=11", "run note"]


def _executor_error():
    exc = ExecutorError("provider timeout", "99")
    exc.source, exc.phase = "gfs", "poll"
    exc.add_note("executor note")
    return exc, [
        "错误：provider timeout",
        "source=gfs",
        "phase=poll",
        "job=99",
        "executor note",
    ]


def _generic_error():
    exc = RuntimeError("driver boom")
    exc.source, exc.phase, exc.job_id = "ifs", "prepare", "7"
    exc.add_note("driver note")
    return exc, [
        "错误：driver boom",
        "source=ifs",
        "phase=prepare",
        "job=7",
        "driver note",
    ]


@pytest.mark.parametrize(
    "build,site",
    [
        (_run_error, "run_sources"),
        (_executor_error, "run_sources"),
        (lambda: (OSError("disk gone"), ["错误：disk gone"]), "run_sources"),
        (_generic_error, "run_sources"),
        (
            lambda: (
                RunLockError("锁 identity 无法确认"),
                ["错误：锁 identity 无法确认"],
            ),
            "lock",
        ),
    ],
    ids=["run-error", "executor-error", "os-error", "generic", "run-lock-error"],
)
def test_runtime_failure_lines_are_all_stamped(
    monkeypatch, capsys, tmp_path, build, site
):
    exc, expected = build()

    def boom(**_):
        raise exc

    if site == "lock":
        monkeypatch.setattr(cli, "run_with_lock", boom)
    else:
        _hold_lock(monkeypatch)
        monkeypatch.setattr(cli, "run_sources", boom)
    assert _exit_code(_ready_run_argv(tmp_path), env={}) == 3
    err = capsys.readouterr().err
    assert _unstamped(err) == expected
    assert len(_stamps(err)) == 1
    assert "Traceback" not in err


# --- 2.6：非 run 输出不带前缀、逐字节不变 ----------------------------------------------


def _assert_unstamped_output(err: str) -> None:
    assert err
    assert not any(STAMPED.match(line) for line in err.splitlines())


def test_prepare_error_note_has_no_stamp(monkeypatch, capsys, tmp_path):
    def raising(**_):
        exc = prepare_module.PrepareError("提交失败：变体 rename 撞上既有条目")
        exc.add_note("回滚/清理未完成：injected rollback residue")
        raise exc

    monkeypatch.setattr(cli, "run_prepare", raising)
    assert _exit_code(_prepare_argv(tmp_path), env={}) == 1
    assert capsys.readouterr().err == (
        "错误：提交失败：变体 rename 撞上既有条目\n"
        "回滚/清理未完成：injected rollback residue\n"
    )


def test_prepare_config_error_has_no_stamp(capsys, tmp_path):
    argv = _prepare_argv(tmp_path)
    argv[argv.index("--config") + 1] = str(tmp_path / "absent-config.toml")
    assert _exit_code(argv, env={}) == 1
    err = capsys.readouterr().err
    _assert_unstamped_output(err)
    assert err.startswith("错误：")


def test_init_refusal_has_no_stamp(capsys, tmp_path):
    assert _exit_code(_argv("init", tmp_path)[0], env={}) == 1
    err = capsys.readouterr().err
    _assert_unstamped_output(err)
    assert err.startswith("错误：init 拒绝执行（variant_missing）")


def test_database_url_guard_on_run_is_byte_identical(capsys, tmp_path):
    argv = _ready_run_argv(tmp_path)
    assert _exit_code(argv, env={"DATABASE_URL": _DB_URL}) == 1
    assert capsys.readouterr().err == (
        "错误：检测到环境变量 DATABASE_URL：yd producer 不连接 NWM PostgreSQL"
        "（agent-ops §2.2）。请清除该变量后重试，不要尝试连通\n"
    )


def test_run_argparse_error_has_no_stamp(capsys, tmp_path):
    argv = _ready_run_argv(tmp_path)[:3]
    assert _exit_code(argv, env={}) == 2
    err = capsys.readouterr().err
    _assert_unstamped_output(err)
    assert err.startswith("usage: yd-producer run")
