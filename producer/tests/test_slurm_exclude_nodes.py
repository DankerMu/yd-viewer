"""#382 可选 `[slurm].exclude_nodes`：装载校验、sbatch argv 与 cli 接线。

oracle 来源：`openspec/changes/slurm-exclude-nodes/tasks.md` §2 与
`docs/compute-loop-design.md` §5。argv 一律按完整 tuple 断言，基准是
`slurm_fixtures.EXPECTED_SBATCH`（改动前的逐字节形态）。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import MappingProxyType

import pytest
from cli_fixtures import write_config, write_local
from slurm_fixtures import (
    EXPECTED_SBATCH,
    REQUIRED_FIVE,
    STEP,
    T0,
    RecordingRunner,
    make_executor,
    make_spec,
)

from yd_producer import cli
from yd_producer.config import (
    ConfigError,
    CronLocal,
    LocalConfig,
    NwmLocal,
    load_config,
    load_local,
)
from yd_producer.controller import RunOutcome
from yd_producer.executor import JobState, StepClock
from yd_producer.runlock import RunLockResult
from yd_producer.slurm import (
    SlurmJobExecutor,
    build_sbatch_command,
    query_failure_exit_code,
)

#: cli_fixtures 的 `[slurm]` 资源键集（来自其 config 模板的 `required_fields`）
_FIXTURE_REQUIRED = {"partition", "account", "cpus", "memory", "walltime"}

_WALLTIME_LINE = 'walltime = "04:00:00"\n'


def _local_with(tmp_path: Path, extra_line: str | None) -> Path:
    """在 cli_fixtures 齐备 `local.toml` 的 `[slurm]` 表里追加一行原文 TOML。"""
    path = write_local(tmp_path)
    if extra_line is not None:
        text = path.read_text(encoding="utf-8")
        assert text.count(_WALLTIME_LINE) == 1
        path.write_text(
            text.replace(_WALLTIME_LINE, _WALLTIME_LINE + extra_line + "\n"),
            encoding="utf-8",
        )
    return path


def _load(tmp_path: Path, extra_line: str | None) -> LocalConfig:
    config = load_config(write_config(tmp_path))
    return load_local(_local_with(tmp_path, extra_line), config)


# --- 2.1 缺省 -----------------------------------------------------------------


def test_absent_and_empty_list_both_load_as_empty_tuple(tmp_path):
    (tmp_path / "absent").mkdir()
    (tmp_path / "empty").mkdir()
    absent = _load(tmp_path / "absent", None)
    empty = _load(tmp_path / "empty", "exclude_nodes = []")

    assert absent.slurm_exclude_nodes == ()
    assert empty.slurm_exclude_nodes == ()
    assert set(absent.slurm) == _FIXTURE_REQUIRED
    assert set(empty.slurm) == _FIXTURE_REQUIRED
    assert isinstance(empty.slurm, MappingProxyType)
    assert dict(absent.slurm) == dict(empty.slurm)


def test_legacy_direct_construction_defaults_to_empty_tuple():
    local = LocalConfig(
        yd_root="/fixture/yd",
        scratch_root="/fixture/scratch",
        shud_binary="/fixture/bin/shud",
        nwm=NwmLocal(
            raw_root="/fixture/nwm/raw",
            canonical_root="/fixture/nwm/canonical",
            checkout_root="/fixture/nwm/checkout",
            python="/fixture/nwm/python",
        ),
        slurm=MappingProxyType({"partition": "cpu"}),
        cron=CronLocal(lock_path="/fixture/run/lock", log_dir="/fixture/log"),
    )
    assert local.slurm_exclude_nodes == ()


# --- 2.2 合法值 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('exclude_nodes = ["cn02"]', ("cn02",)),
        ('exclude_nodes = ["cn02", "cn13"]', ("cn02", "cn13")),
        ('exclude_nodes = ["cn13", "cn02"]', ("cn13", "cn02")),
        ('exclude_nodes = ["cn02", "cn02"]', ("cn02", "cn02")),
    ],
    ids=["single", "pair", "pair-reversed-order-kept", "duplicate-kept"],
)
def test_valid_exclude_nodes_kept_in_order_and_out_of_resources(
    tmp_path, line, expected
):
    local = _load(tmp_path, line)

    assert local.slurm_exclude_nodes == expected
    assert type(local.slurm_exclude_nodes) is tuple
    assert set(local.slurm) == _FIXTURE_REQUIRED
    assert "exclude_nodes" not in local.slurm


# --- 2.3 非法值 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'exclude_nodes = "cn02"',
        "exclude_nodes = 2",
        "exclude_nodes = {a = 1}",
        "exclude_nodes = [2]",
        'exclude_nodes = [""]',
        'exclude_nodes = ["cn02,cn13"]',
        'exclude_nodes = ["cn 02"]',
        'exclude_nodes = ["cn02\\t"]',
        'exclude_nodes = ["cn02\\n"]',
        'exclude_nodes = ["cn02\\u00a0"]',
        'exclude_nodes = ["cn02\\u3000"]',
        'exclude_nodes = ["cn02", true]',
        'exclude_nodes = ["cn02", ["cn13"]]',
    ],
    ids=[
        "string",
        "int",
        "table",
        "int-item",
        "empty-item",
        "comma",
        "space",
        "tab",
        "newline",
        "nbsp",
        "ideographic-space",
        "bool-item",
        "nested-list",
    ],
)
def test_invalid_exclude_nodes_rejected(tmp_path, line):
    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, line)
    assert excinfo.value.path == "slurm.exclude_nodes"
    assert "`slurm.exclude_nodes`" in str(excinfo.value)
    # 改动前的装载器把该键当作多余资源键拒绝，path 恰好也相同；拒绝必须来自取值校验，
    # 而不是资源键集不一致（后者的消息指向 `slurm.required_fields`）
    assert "slurm.required_fields" not in str(excinfo.value)


# --- 2.4 保留名 ---------------------------------------------------------------


def test_required_fields_must_not_declare_exclude_nodes(tmp_path):
    path = write_config(tmp_path)
    text = path.read_text(encoding="utf-8")
    old = 'required_fields = ["partition", "account", "cpus", "memory", "walltime"]'
    assert text.count(old) == 1
    path.write_text(
        text.replace(old, old[:-1] + ', "exclude_nodes"]'), encoding="utf-8"
    )

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert excinfo.value.path == "slurm.required_fields"
    assert "`exclude_nodes`" in str(excinfo.value)


# --- 2.5 argv 完整 tuple ------------------------------------------------------


@pytest.mark.parametrize(
    ("exclude_nodes", "expected"),
    [
        ((), EXPECTED_SBATCH),
        (
            ("cn02",),
            EXPECTED_SBATCH[:-2] + ("--exclude=cn02",) + EXPECTED_SBATCH[-2:],
        ),
        (
            ("cn02", "cn13"),
            EXPECTED_SBATCH[:-2] + ("--exclude=cn02,cn13",) + EXPECTED_SBATCH[-2:],
        ),
    ],
    ids=["empty", "single", "pair"],
)
def test_build_sbatch_command_exclude_argv(exclude_nodes, expected):
    argv = build_sbatch_command(
        make_spec(), required_fields=REQUIRED_FIVE, exclude_nodes=exclude_nodes
    )
    assert argv == expected


def test_build_sbatch_command_without_kwarg_is_unchanged():
    assert build_sbatch_command(make_spec(), required_fields=REQUIRED_FIVE) == (
        EXPECTED_SBATCH
    )


def test_build_sbatch_command_exclude_nodes_is_keyword_only():
    param = inspect.signature(build_sbatch_command).parameters["exclude_nodes"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default == ()
    with pytest.raises(TypeError):
        build_sbatch_command(make_spec(), REQUIRED_FIVE, ("cn02",))  # type: ignore[misc]


# --- 2.6 executor 与 cli 接线 -------------------------------------------------

_EXPECTED_POLL_SACCT = (
    "sacct",
    "-j",
    "12345",
    "-X",
    "--noheader",
    "--parsable2",
    "--format=JobID,State,Start,End",
)
_EXPECTED_EXIT_CODE_SACCT = (
    "sacct",
    "-j",
    "12345",
    "-X",
    "-n",
    "-P",
    "--format=ExitCode",
)


def test_executor_passes_exclude_to_sbatch_only():
    runner = RecordingRunner(
        [
            "12345\n",
            "12345|FAILED|2026-08-28T00:00:10|2026-08-28T00:00:20\n",
            "127:0|\n",
        ]
    )
    executor = SlurmJobExecutor(
        required_fields=REQUIRED_FIVE,
        clock=StepClock(start=T0, step=STEP),
        runner=runner,
        exclude_nodes=("cn02",),
    )
    record = executor.submit(make_spec())
    terminal = executor.poll(record.job_id)
    assert terminal.state is JobState.FAILED
    assert query_failure_exit_code(terminal, runner=runner) == "127:0"

    argvs = [argv for argv, _env in runner.calls]
    assert argvs == [
        EXPECTED_SBATCH[:-2] + ("--exclude=cn02",) + EXPECTED_SBATCH[-2:],
        _EXPECTED_POLL_SACCT,
        _EXPECTED_EXIT_CODE_SACCT,
    ]
    assert "exclude_nodes" not in record.resources
    assert "exclude_nodes" not in terminal.resources


def test_executor_default_keeps_sbatch_argv_unchanged():
    executor, runner = make_executor(["12345\n"])
    executor.submit(make_spec())
    assert runner.calls[0][0] == EXPECTED_SBATCH


def _ready_run(tmp_path: Path, extra_line: str | None) -> list[str]:
    config_path = write_config(tmp_path)
    local_path = _local_with(tmp_path, extra_line)
    local = load_local(local_path, load_config(config_path))
    Path(local.yd_root).mkdir(parents=True, exist_ok=True)
    Path(local.scratch_root).mkdir(parents=True, exist_ok=True)
    Path(local.cron.lock_path).parent.mkdir(parents=True, exist_ok=True)
    states = Path(local.yd_root) / "states" / "gfs"
    states.mkdir(parents=True, exist_ok=True)
    (states / "2026010200.cfg.ic").write_bytes(b"presence")
    return ["run", "--config", str(config_path), "--local", str(local_path)]


class _ExecutorKwargs:
    """`cli.SlurmJobExecutor` 的替身：只记下构造 kwargs。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _AllSucceeded:
    class _Item:
        outcome = RunOutcome.SUCCEEDED
        detail = "ok"

    ifs = (_Item(),)
    gfs = (_Item(),)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('exclude_nodes = ["cn02"]', ("cn02",)),
        (None, ()),
    ],
    ids=["configured", "absent"],
)
def test_cli_run_wires_exclude_nodes_into_both_sources(
    monkeypatch, tmp_path, line, expected
):
    captured: dict[str, object] = {}

    def fake_run_sources(**kwargs):
        captured.update(kwargs)
        return _AllSucceeded()

    monkeypatch.setattr(
        cli,
        "run_with_lock",
        lambda **kw: RunLockResult(
            acquired=True, lock_path=Path(kw["lock_path"]), value=kw["action"]()
        ),
    )
    monkeypatch.setattr(cli, "run_sources", fake_run_sources)
    monkeypatch.setattr(cli, "SlurmJobExecutor", _ExecutorKwargs)

    assert cli.main(_ready_run(tmp_path, line), env={}) == 0

    executors = captured["executors"]
    assert set(executors) == {"ifs", "gfs"}
    for source in ("ifs", "gfs"):
        kwargs = executors[source].kwargs
        assert kwargs["exclude_nodes"] == expected
        assert kwargs["required_fields"] == (
            "partition",
            "account",
            "cpus",
            "memory",
            "walltime",
        )
    assert captured["local"].slurm_exclude_nodes == expected
    assert "exclude_nodes" not in captured["local"].slurm
