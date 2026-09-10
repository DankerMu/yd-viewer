"""`yd_producer.cli` entry tests. All cases call `cli.main(argv, env=...)`."""

import argparse
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cli_fixtures import write_config, write_fake_interpreter, write_local

from yd_producer import cli, nwm
from yd_producer import prepare as prepare_module
from yd_producer.config import load_config, load_local
from yd_producer.controller import (
    JobRunReport,
    RunError,
    RunOutcome,
    RunReport,
    RunSourcesError,
    RunSourcesReport,
    StopReason,
)
from yd_producer.executor import JobState
from yd_producer.init import InitReport
from yd_producer.nwm import ProductionAttemptDriver
from yd_producer.runlock import RunLockResult
from yd_producer.slurm import (
    SlurmJobExecutor,
    query_failure_exit_code,
    subprocess_runner,
)


class Recorder:
    def __init__(self, result=None, delegate=None):
        self.calls: list[tuple[tuple, dict]] = []
        self._result = result
        self._delegate = delegate

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._delegate is not None:
            return self._delegate(*args, **kwargs)
        return self._result

    @property
    def count(self) -> int:
        return len(self.calls)


def _fake_everything(monkeypatch) -> dict[str, Recorder]:
    fakes = {
        "prepare": Recorder(result=0),
        "init": Recorder(result=0),
        "run": Recorder(result=0),
        "load_config": Recorder(result=object()),
        "load_local": Recorder(result=object()),
        "run_prepare": Recorder(result=None),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(cli, name, fake)
    fakes["invoke_mapping_builder"] = Recorder(result=None)
    monkeypatch.setattr(nwm, "invoke_mapping_builder", fakes["invoke_mapping_builder"])
    return fakes


def _exit_code(argv, env):
    try:
        return cli.main(argv, env=env)
    except SystemExit as exc:
        return exc.code


def _ensure_local_dirs(local):
    Path(local.yd_root).mkdir(parents=True, exist_ok=True)
    Path(local.scratch_root).mkdir(parents=True, exist_ok=True)
    Path(local.cron.lock_path).parent.mkdir(parents=True, exist_ok=True)
    return local


def _argv(command, tmp_path, *, baseline=None, **local_kwargs):
    config_path = write_config(tmp_path)
    local_path = write_local(tmp_path, **local_kwargs)
    _ensure_local_dirs(load_local(local_path, load_config(config_path)))
    argv = [command, "--config", str(config_path), "--local", str(local_path)]
    if command == "prepare":
        argv += [
            "--baseline",
            str(tmp_path / "baseline" if baseline is None else baseline),
        ]
    return argv


def _subparsers():
    return next(
        action
        for action in cli.build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )


def test_parser_registers_exactly_three_subcommands():
    assert set(_subparsers().choices) == {"prepare", "init", "run"}


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("prepare", {"--config", "--local", "--baseline"}),
        ("init", {"--config", "--local"}),
        ("run", {"--config", "--local"}),
    ],
)
def test_required_option_sets_per_subcommand(command, expected):
    parser = _subparsers().choices[command]
    required = {
        option
        for action in parser._actions
        if action.required
        for option in action.option_strings
    }
    assert required == expected


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"], env={})
    assert excinfo.value.code == 0
    assert capsys.readouterr().out != ""


def test_unknown_subcommand_exits_two_without_delegation(monkeypatch, capsys):
    fakes = _fake_everything(monkeypatch)
    assert _exit_code(["bootstrap"], env={}) == 2
    capsys.readouterr()
    assert all(fake.count == 0 for fake in fakes.values())


def test_missing_subcommand_exits_two_without_delegation(monkeypatch, capsys):
    fakes = _fake_everything(monkeypatch)
    assert _exit_code([], env={}) == 2
    capsys.readouterr()
    assert all(fake.count == 0 for fake in fakes.values())


@pytest.mark.parametrize("missing", ["--config", "--local"])
def test_missing_required_option_exits_two(monkeypatch, capsys, tmp_path, missing):
    fakes = _fake_everything(monkeypatch)
    argv = _argv("run", tmp_path)
    index = argv.index(missing)
    del argv[index : index + 2]
    assert _exit_code(argv, env={}) == 2
    err = capsys.readouterr().err
    assert missing in err
    assert fakes["load_config"].count == 0
    assert fakes["run"].count == 0


def test_prepare_without_baseline_exits_two(monkeypatch, capsys, tmp_path):
    fakes = _fake_everything(monkeypatch)
    argv = _argv("prepare", tmp_path)
    index = argv.index("--baseline")
    del argv[index : index + 2]
    assert _exit_code(argv, env={}) == 2
    assert "--baseline" in capsys.readouterr().err
    assert fakes["load_config"].count == 0
    assert fakes["prepare"].count == 0
    assert fakes["run_prepare"].count == 0


@pytest.mark.parametrize("command", ["init", "run"])
def test_baseline_is_rejected_on_other_subcommands(
    monkeypatch, capsys, tmp_path, command
):
    fakes = _fake_everything(monkeypatch)
    argv = _argv(command, tmp_path) + ["--baseline", str(tmp_path / "baseline")]
    assert _exit_code(argv, env={}) == 2
    capsys.readouterr()
    assert all(fake.count == 0 for fake in fakes.values())


_DB_URL = "postgresql://x"


@pytest.mark.parametrize(
    "argv_kind",
    ["run", "prepare", "init", "unknown-subcommand", "no-subcommand", "help"],
)
def test_database_url_guard_wins_before_parsing(
    monkeypatch, capsys, tmp_path, argv_kind
):
    fakes = _fake_everything(monkeypatch)
    special = {
        "unknown-subcommand": ["bootstrap"],
        "no-subcommand": [],
        "help": ["--help"],
    }
    argv = special[argv_kind] if argv_kind in special else _argv(argv_kind, tmp_path)
    assert _exit_code(argv, env={"DATABASE_URL": _DB_URL}) == 1
    err = capsys.readouterr().err
    assert "DATABASE_URL" in err
    assert _DB_URL not in err
    assert all(fake.count == 0 for fake in fakes.values())


@pytest.mark.parametrize("command,code", [("prepare", 1), ("init", 1), ("run", 2)])
def test_config_error_exit_codes_without_traceback(capsys, tmp_path, command, code):
    argv = _argv(command, tmp_path)
    argv[argv.index("--config") + 1] = str(tmp_path / "absent-config.toml")
    assert _exit_code(argv, env={}) == code
    err = capsys.readouterr().err
    assert "absent-config.toml" in err
    assert "Traceback" not in err


def test_relative_paths_are_resolved_before_reaching_loaders(monkeypatch, tmp_path):
    from yd_producer import config as config_module

    write_config(tmp_path, "c1.toml")
    write_local(tmp_path, name="l1.toml")
    load_config = Recorder(delegate=config_module.load_config)
    load_local = Recorder(delegate=config_module.load_local)
    monkeypatch.setattr(cli, "load_config", load_config)
    monkeypatch.setattr(cli, "load_local", load_local)
    monkeypatch.chdir(tmp_path)
    assert (
        _exit_code(["init", "--config", "c1.toml", "--local", "l1.toml"], {})
        == cli.EXIT_GUARD
    )
    assert load_config.calls[0][0][0] == (tmp_path / "c1.toml").resolve()
    assert load_local.calls[0][0][0] == (tmp_path / "l1.toml").resolve()


def test_error_message_carries_resolved_absolute_path(monkeypatch, capsys, tmp_path):
    write_local(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert (
        _exit_code(["run", "--config", "missing.toml", "--local", "local.toml"], env={})
        == 2
    )
    assert str((tmp_path / "missing.toml").resolve()) in capsys.readouterr().err


def test_run_rejects_missing_states_dir_and_creates_nothing(
    monkeypatch, capsys, tmp_path
):
    init_fake = Recorder(result=0)
    monkeypatch.setattr(cli, "init", init_fake)
    yd_root = tmp_path / "custom-yd-root"
    yd_root.mkdir()
    argv = _argv("run", tmp_path, yd_root=yd_root)
    assert _exit_code(argv, env={}) == 1
    err = capsys.readouterr().err
    assert str(yd_root / "states") in err
    assert "不存在" in err
    assert not (yd_root / "states").exists()
    assert init_fake.count == 0


def test_run_rejects_empty_states_dir(monkeypatch, capsys, tmp_path):
    init_fake = Recorder(result=0)
    monkeypatch.setattr(cli, "init", init_fake)
    yd_root = tmp_path / "custom-yd-root"
    states = yd_root / "states"
    states.mkdir(parents=True)
    argv = _argv("run", tmp_path, yd_root=yd_root)
    assert _exit_code(argv, env={}) == 1
    err = capsys.readouterr().err
    assert str(states) in err
    assert "为空" in err
    assert list(states.iterdir()) == []
    assert init_fake.count == 0


def test_run_rejects_states_path_that_is_a_regular_file(monkeypatch, capsys, tmp_path):
    init_fake = Recorder(result=0)
    monkeypatch.setattr(cli, "init", init_fake)
    yd_root = tmp_path / "custom-yd-root"
    yd_root.mkdir()
    states = yd_root / "states"
    states.write_text("stale placeholder\n", encoding="utf-8")
    argv = _argv("run", tmp_path, yd_root=yd_root)
    assert _exit_code(argv, env={}) == 1
    err = capsys.readouterr().err
    assert str(states) in err
    assert "不是目录" in err
    assert "Traceback" not in err
    assert states.read_text(encoding="utf-8") == "stale placeholder\n"
    assert init_fake.count == 0


def test_run_with_non_empty_states_enters_locked_production_assembly(
    monkeypatch, capsys, tmp_path
):
    captured: dict[str, object] = {}

    def fake_lock(*, lock_path, action):
        captured["report"] = action()
        return RunLockResult(
            acquired=True, lock_path=Path(lock_path), value=captured["report"]
        )

    def fake_run_sources(**kwargs):
        captured["kwargs"] = kwargs
        return _classified_success()

    monkeypatch.setattr(cli, "run_with_lock", fake_lock)
    monkeypatch.setattr(cli, "run_sources", fake_run_sources)
    init_fake = Recorder(result=0)
    monkeypatch.setattr(cli, "init", init_fake)
    yd_root = tmp_path / "custom-yd-root"
    states = yd_root / "states"
    states.mkdir(parents=True)
    (states / "gfs").mkdir()
    argv = _argv("run", tmp_path, yd_root=yd_root)
    assert _exit_code(argv, env={}) == 0
    kwargs = captured["kwargs"]
    assert set(kwargs["executors"]) == set(kwargs["drivers"]) == {"ifs", "gfs"}
    assert (
        set(kwargs["poll_waits"]) == set(kwargs["failure_exit_codes"]) == {"ifs", "gfs"}
    )
    assert kwargs["executors"]["ifs"] is not kwargs["executors"]["gfs"]
    assert kwargs["drivers"]["ifs"] is not kwargs["drivers"]["gfs"]
    assert isinstance(kwargs["executors"]["ifs"], SlurmJobExecutor)
    assert isinstance(kwargs["drivers"]["ifs"], ProductionAttemptDriver)
    assert kwargs["poll_waits"]["ifs"] is cli._production_poll_wait
    assert "command_timeout_seconds" not in dict(kwargs["local"].slurm)
    assert init_fake.count == 0
    assert "14.1" not in capsys.readouterr().err


def test_prepare_stops_when_interpreter_missing(monkeypatch, capsys, tmp_path):
    runner = Recorder(result=None)
    monkeypatch.setattr(nwm, "invoke_mapping_builder", runner)
    absent = tmp_path.resolve() / "nwm" / ".venv" / "bin" / "python"
    argv = _argv("prepare", tmp_path, python=absent)
    assert _exit_code(argv, env={}) == 1
    err = capsys.readouterr().err
    assert str(absent) in err
    assert "不存在" in err
    assert runner.count == 0


def test_prepare_stops_when_interpreter_not_executable(monkeypatch, capsys, tmp_path):
    runner = Recorder(result=None)
    monkeypatch.setattr(nwm, "invoke_mapping_builder", runner)
    script = tmp_path.resolve() / "python-no-x"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o644)
    argv = _argv("prepare", tmp_path, python=script)
    assert _exit_code(argv, env={}) == 1
    err = capsys.readouterr().err
    assert str(script) in err
    assert "不可执行" in err
    assert runner.count == 0


def _prepare_argv(tmp_path, **kwargs):
    script = write_fake_interpreter(
        tmp_path.resolve() / "fake-python", tmp_path.resolve() / "record.json"
    )
    return _argv("prepare", tmp_path, python=script, **kwargs)


def test_prepare_with_executable_interpreter_reaches_production_builder_binding(
    monkeypatch, capsys, tmp_path
):
    runner = Recorder(result=None)
    monkeypatch.setattr(nwm, "invoke_mapping_builder", runner)
    assert _exit_code(_prepare_argv(tmp_path), env={}) == 3
    err = capsys.readouterr().err
    assert prepare_module.BUILDER_OWNER in err
    assert "归属 M4" in err
    assert "Traceback" not in err
    assert runner.count == 0


def _refuse_cleanup(*args, **kwargs):
    raise prepare_module.safe_fs.SafeFilesystemError(
        "injected cleanup failure", kind="io"
    )


def _patch_cleanup_refuse(monkeypatch):
    monkeypatch.setattr(nwm, "invoke_mapping_builder", Recorder(result=None))
    monkeypatch.setattr(
        prepare_module.safe_fs, "remove_tree_allow_symlinks", _refuse_cleanup
    )
    monkeypatch.setattr(prepare_module.safe_fs, "rmtree_no_follow", _refuse_cleanup)


def test_cleanup_failure_does_not_downgrade_the_unimplemented_exit_code(
    monkeypatch, capsys, tmp_path
):
    _patch_cleanup_refuse(monkeypatch)
    assert _exit_code(_prepare_argv(tmp_path), env={}) == 3
    err = capsys.readouterr().err
    assert "归属 M4" in err
    assert "Traceback" not in err


def test_cleanup_failure_text_reaches_stderr_on_the_failure_path(
    monkeypatch, capsys, tmp_path
):
    _patch_cleanup_refuse(monkeypatch)
    assert _exit_code(_prepare_argv(tmp_path), env={}) == 3
    err = capsys.readouterr().err
    assert "归属 M4" in err
    assert "injected cleanup failure" in err
    assert "Traceback" not in err


def test_success_path_cleanup_warnings_reach_stderr_without_changing_the_exit_code(
    monkeypatch, capsys, tmp_path
):
    warnings = ("残留 staging：/x/.prepare-staging-1", "残留 scratch：/y/prepare-1")
    fake = Recorder(
        result=prepare_module.PrepareReport(
            variants={},
            rivers_geojson=tmp_path / "rivers.geojson",
            boundary_geojson=tmp_path / "boundary.geojson",
            cleanup_warnings=warnings,
        )
    )
    monkeypatch.setattr(cli, "run_prepare", fake)
    assert _exit_code(_prepare_argv(tmp_path), env={}) == 0
    err = capsys.readouterr().err
    for warning in warnings:
        assert warning in err


def test_prepare_rejection_and_unimplemented_binding_use_different_exit_codes(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(nwm, "invoke_mapping_builder", Recorder(result=None))
    argv = _prepare_argv(tmp_path)
    yd_root = tmp_path.resolve() / "yd"
    assert _exit_code(argv, env={}) == 3
    capsys.readouterr()
    (yd_root / "input" / "models" / "yd_gfs").mkdir(parents=True)
    assert _exit_code(argv, env={}) == 1
    err = capsys.readouterr().err
    assert str(yd_root / "input" / "models" / "yd_gfs") in err
    assert "Traceback" not in err


def test_prepare_delegates_resolved_baseline_path(monkeypatch, tmp_path):
    fake = Recorder(
        result=prepare_module.PrepareReport(
            variants={},
            rivers_geojson=tmp_path / "rivers.geojson",
            boundary_geojson=tmp_path / "boundary.geojson",
        )
    )
    monkeypatch.setattr(cli, "run_prepare", fake)
    monkeypatch.chdir(tmp_path)
    argv = _prepare_argv(tmp_path, baseline="baseline")
    assert _exit_code(argv, env={}) == 0
    assert fake.count == 1
    assert fake.calls[0][1]["baseline_root"] == (tmp_path / "baseline").resolve()


def test_prepare_error_becomes_exit_one(monkeypatch, capsys, tmp_path):
    def raising(**kwargs):
        raise prepare_module.PrepareError("变体 reach 数与 reach_count 不符")

    monkeypatch.setattr(cli, "run_prepare", raising)
    assert _exit_code(_prepare_argv(tmp_path), env={}) == 1
    err = capsys.readouterr().err
    assert "变体 reach 数与 reach_count 不符" in err
    assert "Traceback" not in err


def test_cleanup_note_reaches_stderr_on_the_exit_one_path(
    monkeypatch, capsys, tmp_path
):
    def raising(**kwargs):
        exc = prepare_module.PrepareError("提交失败：变体 rename 撞上既有条目")
        exc.add_note("回滚/清理未完成：injected rollback residue")
        raise exc

    monkeypatch.setattr(cli, "run_prepare", raising)
    assert _exit_code(_prepare_argv(tmp_path), env={}) == 1
    err = capsys.readouterr().err
    assert "提交失败：变体 rename 撞上既有条目" in err
    assert "injected rollback residue" in err
    assert "Traceback" not in err


def test_a_none_report_is_never_reported_as_success(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "run_prepare", Recorder(result=None))
    try:
        rc = cli.main(_prepare_argv(tmp_path), env={})
    except SystemExit as exc:
        rc = exc.code
    except BaseException:  # noqa: BLE001 - test asserts any escaped error rather than pin exception type
        rc = "escaped"
    assert rc != 0 and rc is not None


@pytest.mark.parametrize("command", ["prepare", "init", "run"])
def test_dispatch_resolves_delegates_at_call_time(monkeypatch, tmp_path, command):
    fakes = {name: Recorder(result=0) for name in ("prepare", "init", "run")}
    for name, fake in fakes.items():
        monkeypatch.setattr(cli, name, fake)
    assert _exit_code(_argv(command, tmp_path), env={}) == 0
    assert fakes[command].count == 1
    assert [name for name, fake in fakes.items() if fake.count] == [command]


def test_init_reaches_the_real_business_body(capsys, tmp_path):
    assert _exit_code(_argv("init", tmp_path), env={}) == cli.EXIT_GUARD
    err = capsys.readouterr().err
    assert "variant_missing" in err
    assert "11.1" not in err
    assert "尚未落地" not in err


def test_init_success_detail_reaches_the_operator_on_stderr(capsys, monkeypatch):
    detail = "ifs 首轮 T=2026082512；ifs 的链起点跳过了更早的候选，那些候选上有 1 个预期 raw 文件**无法访问**"
    written = (Path("/yd/states/ifs/2026082512.cfg.ic"),)
    monkeypatch.setattr(
        cli,
        "bootstrap",
        lambda **kwargs: InitReport(written=written, refusal=None, detail=detail),
    )
    assert cli.init(local=None, config=None) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [str(written[0])]
    assert detail in captured.err


CYCLE = datetime(2026, 1, 2, 0, tzinfo=UTC)


def _write_timeout_local(tmp_path, *, timeout: int | None = 37, **kwargs) -> Path:
    path = write_local(tmp_path, **kwargs)
    if timeout is None:
        return path
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'walltime = "04:00:00"\n',
            f'walltime = "04:00:00"\ncommand_timeout_seconds = {timeout}\n',
        ),
        encoding="utf-8",
    )
    return path


def _ready_run_argv(tmp_path, *, timeout: int | None = 37):
    config_path = write_config(tmp_path)
    local_path = _write_timeout_local(tmp_path, timeout=timeout)
    local = _ensure_local_dirs(load_local(local_path, load_config(config_path)))
    states = Path(local.yd_root) / "states"
    states.mkdir(parents=True, exist_ok=True)
    (states / "gfs").mkdir(exist_ok=True)
    return ["run", "--config", str(config_path), "--local", str(local_path)], local


def _job(state=JobState.SUCCEEDED, job_id="1") -> JobRunReport:
    return JobRunReport(
        job_id=job_id,
        partition="cpu",
        state=state,
        submitted_at=CYCLE,
        started_at=CYCLE,
        ended_at=CYCLE,
    )


def _stopped(source: str) -> RunReport:
    return RunReport(
        source=source,
        cycle=CYCLE,
        outcome=RunOutcome.STOPPED,
        stop_reason=StopReason.RAW_INCOMPLETE,
        detail=f"{source} stopped",
        job=None,
        published=None,
        done_path=None,
    )


def _classified_success():
    class Item:
        outcome = RunOutcome.SUCCEEDED
        detail = "ok"

    class Pair:
        ifs = (Item(),)
        gfs = (Item(),)

    return Pair()


def _hold_lock(action, lock_path):
    return RunLockResult(acquired=True, lock_path=Path(lock_path), value=action())


def _stub_acquired_lock(monkeypatch):
    monkeypatch.setattr(
        cli, "run_with_lock", lambda **kw: _hold_lock(kw["action"], kw["lock_path"])
    )


def test_run_lock_skip_returns_zero_with_zero_factories(monkeypatch, tmp_path):
    calls = {"run_sources": 0, "sleep": 0, "executor": 0, "driver": 0, "provider": 0}

    def skip(*, lock_path, action):
        return RunLockResult(acquired=False, lock_path=Path(lock_path), value=None)

    monkeypatch.setattr(cli, "run_with_lock", skip)
    monkeypatch.setattr(
        cli, "run_sources", lambda **_: calls.__setitem__("run_sources", 1)
    )
    monkeypatch.setattr(cli.time, "sleep", lambda *_: calls.__setitem__("sleep", 1))
    monkeypatch.setattr(
        cli, "SlurmJobExecutor", lambda **_: calls.__setitem__("executor", 1)
    )
    monkeypatch.setattr(
        cli, "ProductionAttemptDriver", lambda **_: calls.__setitem__("driver", 1)
    )
    monkeypatch.setattr(
        cli, "query_failure_exit_code", lambda *a, **k: calls.__setitem__("provider", 1)
    )
    argv, _local = _ready_run_argv(tmp_path)
    assert _exit_code(argv, env={}) == 0
    assert calls == {
        "run_sources": 0,
        "sleep": 0,
        "executor": 0,
        "driver": 0,
        "provider": 0,
    }


def test_run_same_bounded_runner_timeout_37_is_shared(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_run_sources(**kwargs):
        captured["kwargs"] = kwargs
        return _classified_success()

    _stub_acquired_lock(monkeypatch)
    monkeypatch.setattr(cli, "run_sources", fake_run_sources)
    argv, local = _ready_run_argv(tmp_path, timeout=37)
    assert local.slurm_command_timeout_seconds == 37
    assert _exit_code(argv, env={}) == 0
    kwargs = captured["kwargs"]
    runner = kwargs["executors"]["ifs"]._runner
    assert runner is kwargs["executors"]["gfs"]._runner
    assert runner.func is subprocess_runner
    assert runner.keywords["command_timeout_seconds"] == 37
    assert kwargs["failure_exit_codes"]["ifs"].func is query_failure_exit_code
    assert kwargs["failure_exit_codes"]["ifs"].keywords["runner"] is runner
    assert kwargs["failure_exit_codes"]["gfs"].keywords["runner"] is runner
    assert "command_timeout_seconds" not in dict(kwargs["local"].slurm)


def test_production_poll_wait_sleeps_exactly_ten_seconds(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", slept.append)
    cli._production_poll_wait()
    assert slept == [10]
    assert cli.POLL_INTERVAL_SECONDS == 10


@pytest.mark.parametrize(
    "outcome,code",
    [
        (RunOutcome.STOPPED, 3),
        (RunOutcome.JOB_FAILED, 3),
        (RunOutcome.SUCCEEDED_CLEANUP_PENDING, 3),
    ],
)
def test_run_non_success_outcomes_return_three(
    monkeypatch, capsys, tmp_path, outcome, code
):
    def fake_run_sources(**kwargs):
        if outcome is RunOutcome.STOPPED:
            ifs = RunReport(
                source="ifs",
                cycle=CYCLE,
                outcome=outcome,
                stop_reason=StopReason.RAW_INCOMPLETE,
                detail="ifs stopped",
                job=None,
                published=None,
                done_path=None,
            )
        else:
            ifs = RunReport(
                source="ifs",
                cycle=CYCLE,
                outcome=outcome,
                stop_reason=None,
                detail="ifs failed",
                job=_job(JobState.FAILED, "77"),
                published=None,
                done_path=None
                if outcome is RunOutcome.JOB_FAILED
                else Path("/tmp/done"),
            )
        return RunSourcesReport(ifs=(ifs,), gfs=(_stopped("gfs"),))

    _stub_acquired_lock(monkeypatch)
    monkeypatch.setattr(cli, "run_sources", fake_run_sources)
    argv, _local = _ready_run_argv(tmp_path)
    assert _exit_code(argv, env={}) == code
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "ifs" in err


def test_run_sources_error_prints_notes_once_without_traceback(
    monkeypatch, capsys, tmp_path
):
    ifs_error = RunError("ifs boom", phase="cleanup", source="ifs", job_id="11")
    ifs_error.add_note("startup deleted ifs/2026010200 /scratch/a")
    gfs_error = RunError("gfs boom", phase="submit", source="gfs", job_id="22")
    gfs_error.add_note("gfs note unique")

    def fake_lock(*, lock_path, action):
        raise RunSourcesError(
            {"ifs": (), "gfs": ()}, {"ifs": ifs_error, "gfs": gfs_error}
        )

    monkeypatch.setattr(cli, "run_with_lock", fake_lock)
    argv, _local = _ready_run_argv(tmp_path)
    assert _exit_code(argv, env={}) == 3
    err = capsys.readouterr().err
    assert err.count("ifs boom") == err.count("gfs boom") == 1
    assert err.count("startup deleted ifs/2026010200 /scratch/a") == 1
    assert err.count("gfs note unique") == 1
    assert err.index("ifs") < err.index("gfs")
    assert "Traceback" not in err


def test_run_config_error_returns_two_before_lock(monkeypatch, capsys, tmp_path):
    argv, _local = _ready_run_argv(tmp_path)
    argv[argv.index("--config") + 1] = str(tmp_path / "absent-config.toml")
    lock = Recorder(
        result=RunLockResult(acquired=False, lock_path=tmp_path, value=None)
    )
    monkeypatch.setattr(cli, "run_with_lock", lock)
    assert _exit_code(argv, env={}) == 2
    assert lock.count == 0
    assert "Traceback" not in capsys.readouterr().err


def test_run_omitted_timeout_binds_sixty(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_run_sources(**kwargs):
        captured["timeout"] = kwargs["executors"]["ifs"]._runner.keywords[
            "command_timeout_seconds"
        ]
        return _classified_success()

    _stub_acquired_lock(monkeypatch)
    monkeypatch.setattr(cli, "run_sources", fake_run_sources)
    argv, local = _ready_run_argv(tmp_path, timeout=None)
    assert local.slurm_command_timeout_seconds == 60
    assert _exit_code(argv, env={}) == 0
    assert captured["timeout"] == 60


def test_run_all_success_classification_returns_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli,
        "run_with_lock",
        lambda **kw: _hold_lock(lambda: _classified_success(), kw["lock_path"]),
    )
    argv, _local = _ready_run_argv(tmp_path)
    assert _exit_code(argv, env={}) == 0


def test_run_driver_exception_returns_three_without_traceback(
    monkeypatch, capsys, tmp_path
):
    def boom(**kwargs):
        raise RuntimeError("driver boom")

    _stub_acquired_lock(monkeypatch)
    monkeypatch.setattr(cli, "run_sources", boom)
    argv, _local = _ready_run_argv(tmp_path)
    assert _exit_code(argv, env={}) == 3
    err = capsys.readouterr().err
    assert "driver boom" in err
    assert "Traceback" not in err


def test_run_executor_error_prints_source_phase_job(monkeypatch, capsys, tmp_path):
    from yd_producer.executor import ExecutorError

    def boom(**kwargs):
        raise ExecutorError("provider timeout", "99")

    _stub_acquired_lock(monkeypatch)
    monkeypatch.setattr(cli, "run_sources", boom)
    argv, _local = _ready_run_argv(tmp_path)
    assert _exit_code(argv, env={}) == 3
    err = capsys.readouterr().err
    assert "provider timeout" in err
    assert "job=99" in err
    assert "Traceback" not in err


def _dual_source_run(tmp_path, ifs_runner, gfs_runner):
    from functools import partial

    import controller_sources_fixtures as fx

    from yd_producer.controller import run_sources

    config, local = fx.write_dual_tree(tmp_path)
    barrier = fx.DualBarrier()
    gfs_driver, gfs_state, gfs_slot = fx.success_driver()
    ifs_driver, _, _ = fx.success_driver()
    ifs_work, gfs_work = fx.work_dir(local, "ifs"), fx.work_dir(local, "gfs")
    kwargs = {
        "config": config,
        "local": local,
        "executors": {
            "ifs": fx.BarrierExecutor(
                fx.fake_for("ifs", state=JobState.FAILED, polls=1),
                source="ifs",
                barrier=barrier,
                hook=fx.FailureLogHook(local, "ifs", fx.IFS_RAW_LOG),
            ),
            "gfs": fx.BarrierExecutor(
                fx.fake_for("gfs", polls=1),
                source="gfs",
                barrier=barrier,
                hook=fx.success_hook(gfs_slot, gfs_state),
            ),
        },
        "drivers": {"ifs": ifs_driver, "gfs": gfs_driver},
        "poll_waits": {"ifs": lambda: None, "gfs": lambda: None},
        "failure_exit_codes": {
            "ifs": partial(query_failure_exit_code, runner=ifs_runner),
            "gfs": partial(query_failure_exit_code, runner=gfs_runner),
        },
    }
    try:
        return run_sources(**kwargs), local, ifs_work, gfs_work, fx
    except RunSourcesError as error:
        error.ifs_work, error.gfs_work, error.local, error.fx = (
            ifs_work,
            gfs_work,
            local,
            fx,
        )
        raise


class _ProviderRunner:
    def __init__(self, value=None, *, timeout=False):
        self.value, self.timeout, self.calls = value, timeout, 0

    def __call__(self, argv, *, env):
        self.calls += 1
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, 37)
        assert argv[:3] == ("sacct", "-j", "fake-1")
        return self.value


def test_real_provider_timeout_keeps_exact_work(tmp_path):
    runner = _ProviderRunner(timeout=True)
    with pytest.raises(RunSourcesError) as captured:
        _dual_source_run(tmp_path, runner, _ProviderRunner("0:0"))
    err = captured.value
    assert "ifs" in str(err) and "99" not in str(err)
    assert runner.calls == 1
    assert err.ifs_work.exists()
    gfs_done = Path(err.local.yd_root) / "output"
    sibling_done = list(gfs_done.glob("**/gfs/**/DONE")) + list(
        gfs_done.glob("**/DONE")
    )
    assert gfs_done.exists()
    assert sibling_done or err.gfs_work.exists()


def test_real_provider_commits_failure_log_before_deleting_exact_work(tmp_path):
    runner = _ProviderRunner("42:7")
    report, local, ifs_work, gfs_work, fx = _dual_source_run(
        tmp_path, runner, _ProviderRunner("0:0")
    )
    log = fx.failure_log_path(local, "ifs")
    assert report.ifs[-1].outcome is RunOutcome.JOB_FAILED
    assert report.gfs[-1].outcome is RunOutcome.STOPPED
    assert report.gfs[-1].done_path is None
    succeeded = [item for item in report.gfs if item.outcome is RunOutcome.SUCCEEDED]
    assert [item.cycle for item in succeeded] == [fx.CYCLE_T]
    prior = succeeded[0]
    assert prior.published is not None
    assert prior.done_path == fx.done_path(local, "gfs", fx.T_TEXT)
    assert prior.done_path.is_file()
    assert prior.job is not None and prior.job.state is JobState.SUCCEEDED
    assert all(
        item.job is None or item.job.state is JobState.SUCCEEDED for item in report.gfs
    )
    assert runner.calls == 1 and log.is_file()
    assert b'"exit_code":"42:7"' in log.read_bytes()
    assert not ifs_work.exists()
    assert not gfs_work.exists()
    assert not fx.work_dir(local, "gfs", fx.T_PLUS_12_TEXT).exists()
