"""Issue #95: `run` admits a states tree only when a source has a state file."""

from pathlib import Path

import pytest
from cli_fixtures import write_config, write_local

from yd_producer import cli
from yd_producer.config import load_config, load_local
from yd_producer.controller import RunOutcome
from yd_producer.runlock import RunLockResult

_GUIDANCE = "请先经授权执行 `yd-producer init`"
_STATE_NAME = "not-a-cycle.cfg.ic"
_STATE_BYTES = b"presence-not-validity"


class Recorder:
    def __init__(self, result=None):
        self.calls: list[tuple[tuple, dict]] = []
        self._result = result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._result

    @property
    def count(self) -> int:
        return len(self.calls)


def _ensure_local_dirs(local):
    Path(local.yd_root).mkdir(parents=True, exist_ok=True)
    Path(local.scratch_root).mkdir(parents=True, exist_ok=True)
    Path(local.cron.lock_path).parent.mkdir(parents=True, exist_ok=True)
    return local


def _run_argv(tmp_path, yd_root):
    config_path = write_config(tmp_path)
    local_path = write_local(tmp_path, yd_root=yd_root)
    _ensure_local_dirs(load_local(local_path, load_config(config_path)))
    return ["run", "--config", str(config_path), "--local", str(local_path)]


def _exit_code(argv, env):
    try:
        return cli.main(argv, env=env)
    except SystemExit as exc:
        return exc.code


def _snapshot(root: Path):
    entries = []
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            entries.append((relative, "file", path.read_bytes(), path.stat().st_mode))
        else:
            entries.append((relative, "dir", None, path.stat().st_mode))
    return (root.exists(), root.is_dir(), root.stat().st_mode, tuple(entries))


def _classified_success():
    class Item:
        outcome = RunOutcome.SUCCEEDED
        detail = "ok"

    class Pair:
        ifs = (Item(),)
        gfs = (Item(),)

    return Pair()


def _plant_false_positive(states: Path, kind: str) -> None:
    if kind == "empty-ifs":
        (states / "ifs").mkdir()
        return
    if kind == "top-level-state":
        (states / _STATE_NAME).write_bytes(_STATE_BYTES)
        return
    if kind == "junk":
        (states / "notes.txt").write_bytes(b"not a state")
        (states / "ifs").mkdir()
        (states / "ifs" / "readme").write_bytes(b"still empty of states")
        return
    if kind == "suffix-directory":
        (states / "ifs").mkdir()
        (states / "ifs" / _STATE_NAME).mkdir()
        return
    if kind == "nested-state":
        nested = states / "ifs" / "nested"
        nested.mkdir(parents=True)
        (nested / _STATE_NAME).write_bytes(_STATE_BYTES)
        return
    raise AssertionError(kind)


@pytest.mark.parametrize(
    "kind",
    ["empty-ifs", "top-level-state", "junk", "suffix-directory", "nested-state"],
    ids=["empty-ifs", "top-level-state", "junk", "suffix-directory", "nested-state"],
)
def test_run_refuses_false_positive_state_presence(monkeypatch, capsys, tmp_path, kind):
    init_fake = Recorder(result=0)
    run_sources = Recorder(result=_classified_success())
    monkeypatch.setattr(cli, "init", init_fake)
    monkeypatch.setattr(cli, "run_sources", run_sources)
    yd_root = tmp_path / "custom-yd-root"
    states = yd_root / "states"
    states.mkdir(parents=True)
    _plant_false_positive(states, kind)
    before = _snapshot(states)
    argv = _run_argv(tmp_path, yd_root)
    assert _exit_code(argv, env={}) == 1
    err = capsys.readouterr().err
    assert str(states.resolve()) in err
    assert "为空" in err
    assert _GUIDANCE in err
    assert "Traceback" not in err
    assert init_fake.count == 0
    assert run_sources.count == 0
    assert _snapshot(states) == before


@pytest.mark.parametrize(
    "sources",
    [("ifs",), ("gfs",), ("ifs", "gfs")],
    ids=["ifs-only", "gfs-only", "both-sources"],
)
def test_run_admits_any_source_state_file_into_locked_production(
    monkeypatch, capsys, tmp_path, sources
):
    captured: dict[str, object] = {}

    def fake_lock(*, lock_path, action):
        captured["report"] = action()
        return RunLockResult(
            acquired=True, lock_path=Path(lock_path), value=captured["report"]
        )

    init_fake = Recorder(result=0)
    run_sources = Recorder(result=_classified_success())
    monkeypatch.setattr(cli, "run_with_lock", fake_lock)
    monkeypatch.setattr(cli, "init", init_fake)
    monkeypatch.setattr(cli, "run_sources", run_sources)
    yd_root = tmp_path / "custom-yd-root"
    states = yd_root / "states"
    states.mkdir(parents=True)
    for source in sources:
        directory = states / source
        directory.mkdir()
        (directory / _STATE_NAME).write_bytes(_STATE_BYTES)
    before = _snapshot(states)
    argv = _run_argv(tmp_path, yd_root)
    assert _exit_code(argv, env={}) == 0
    assert run_sources.count == 1
    assert init_fake.count == 0
    assert "Traceback" not in capsys.readouterr().err
    assert _snapshot(states) == before
