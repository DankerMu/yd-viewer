"""Issue #95 any-source admission and issue #44 probe-error classification."""

import errno
import os
import stat
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
_PROBE_SITES = (
    "root-metadata",
    "root-open",
    "root-iteration",
    "source-type",
    "source-open",
    "source-iteration",
    "state-type",
    "root-cleanup",
    "source-cleanup",
)


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


def _make_probe_error(kind: str, path: Path, *, with_filename: bool) -> OSError:
    code = errno.EACCES if kind == "eacces" else errno.EIO
    cls = PermissionError if kind == "eacces" else OSError
    if with_filename:
        return cls(code, os.strerror(code), str(path))
    return cls(code, os.strerror(code))


def _same_path(left, right: Path) -> bool:
    return os.path.normpath(os.fspath(left)) == os.path.normpath(os.fspath(right))


def _plant_probe_tree(states: Path, site: str) -> Path:
    states.mkdir(parents=True)
    source = states / "ifs"
    if site in {"root-metadata", "root-open", "root-cleanup"}:
        return states
    if site == "root-iteration":
        (states / "notes.txt").write_bytes(b"skip-me")
        return states
    if site == "source-type":
        (states / "notes.txt").write_bytes(b"skip-me")
        source.mkdir()
        return source
    if site in {"source-open", "source-cleanup"}:
        source.mkdir()
        return source
    if site == "source-iteration":
        source.mkdir()
        (source / "readme").write_bytes(b"not a state")
        return source
    if site == "state-type":
        source.mkdir()
        (source / "readme").write_bytes(b"not a state")
        target = source / _STATE_NAME
        target.write_bytes(_STATE_BYTES)
        return target
    raise AssertionError(site)


def _install_states_probe(
    monkeypatch, *, states: Path, site: str, kind: str, with_filename: bool
) -> None:
    original_stat = cli.os.stat
    original_scandir = cli.os.scandir
    source = states / "ifs"

    def error_for(path: Path) -> OSError:
        return _make_probe_error(kind, path, with_filename=with_filename)

    def wrapped_stat(path, *args, **kwargs):
        if site == "root-metadata" and _same_path(path, states):
            raise error_for(states)
        return original_stat(path, *args, **kwargs)

    class FaultyEntry:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def is_dir(self, *args, **kwargs):
            if site == "source-type" and self.name == "ifs":
                raise error_for(Path(self.path))
            return self._wrapped.is_dir(*args, **kwargs)

        def is_file(self, *args, **kwargs):
            if site == "state-type" and self.name.endswith(".cfg.ic"):
                raise error_for(Path(self.path))
            return self._wrapped.is_file(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    class FaultyScan:
        def __init__(self, wrapped, *, role: str):
            self._wrapped = wrapped
            self._role = role
            self._yielded = 0

        def __iter__(self):
            return self

        def __next__(self):
            if self._role == "root" and site == "root-iteration" and self._yielded >= 1:
                raise error_for(states)
            if (
                self._role == "source"
                and site == "source-iteration"
                and self._yielded >= 1
            ):
                raise error_for(source)
            entry = next(self._wrapped)
            self._yielded += 1
            if self._role == "root" and site == "source-type":
                return FaultyEntry(entry)
            if self._role == "source" and site == "state-type":
                return FaultyEntry(entry)
            return entry

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def __exit__(self, *exc):
            suppress = self._wrapped.__exit__(*exc)
            if exc[0] is None and self._role == "root" and site == "root-cleanup":
                raise error_for(states)
            if exc[0] is None and self._role == "source" and site == "source-cleanup":
                raise error_for(source)
            return suppress

        def close(self):
            return self._wrapped.close()

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    def wrapped_scandir(path, *args, **kwargs):
        if _same_path(path, states):
            if site == "root-open":
                raise error_for(states)
            iterator = original_scandir(path, *args, **kwargs)
            if site in {"root-iteration", "root-cleanup", "source-type"}:
                return FaultyScan(iterator, role="root")
            return iterator
        if _same_path(path, source):
            if site == "source-open":
                raise error_for(source)
            iterator = original_scandir(path, *args, **kwargs)
            if site in {"source-iteration", "source-cleanup", "state-type"}:
                return FaultyScan(iterator, role="source")
            return iterator
        return original_scandir(path, *args, **kwargs)

    monkeypatch.setattr(cli.os, "stat", wrapped_stat)
    monkeypatch.setattr(cli.os, "scandir", wrapped_scandir)


@pytest.mark.parametrize("with_filename", [True, False], ids=["named", "nameless"])
@pytest.mark.parametrize("kind", ["eacces", "eio"], ids=["eacces", "eio"])
@pytest.mark.parametrize("site", _PROBE_SITES)
def test_run_classifies_states_probe_errors(
    monkeypatch, capsys, tmp_path, site, kind, with_filename
):
    init_fake = Recorder(result=0)
    run_sources = Recorder(result=_classified_success())
    monkeypatch.setattr(cli, "init", init_fake)
    monkeypatch.setattr(cli, "run_sources", run_sources)
    yd_root = (tmp_path / "custom-yd-root").resolve()
    states = yd_root / "states"
    failing = _plant_probe_tree(states, site)
    before = _snapshot(states)
    argv = _run_argv(tmp_path, yd_root)
    with monkeypatch.context() as patcher:
        _install_states_probe(
            patcher,
            states=states,
            site=site,
            kind=kind,
            with_filename=with_filename,
        )
        assert _exit_code(argv, env={}) == 1
        err = capsys.readouterr().err
    errno_text = os.strerror(errno.EACCES if kind == "eacces" else errno.EIO)
    assert str(states) in err
    assert str(failing) in err
    assert errno_text in err
    if kind == "eacces":
        assert "权限不足" in err
        assert "读取失败" not in err
    else:
        assert "读取失败" in err
        assert "权限不足" not in err
    assert "为空" not in err
    assert "不存在" not in err
    assert _GUIDANCE not in err
    assert "Traceback" not in err
    assert init_fake.count == 0
    assert run_sources.count == 0
    assert _snapshot(states) == before


def test_run_refuses_mode_000_states_root(monkeypatch, capsys, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root 无视 mode 位，本用例无判别力")
    init_fake = Recorder(result=0)
    run_sources = Recorder(result=_classified_success())
    monkeypatch.setattr(cli, "init", init_fake)
    monkeypatch.setattr(cli, "run_sources", run_sources)
    yd_root = (tmp_path / "custom-yd-root").resolve()
    states = yd_root / "states"
    states.mkdir(parents=True)
    argv = _run_argv(tmp_path, yd_root)
    before = _snapshot(states)
    original_mode = stat.S_IMODE(states.stat().st_mode)
    states.chmod(0o000)
    try:
        assert _exit_code(argv, env={}) == 1
        err = capsys.readouterr().err
        assert str(states) in err
        assert "权限不足" in err
        assert os.strerror(errno.EACCES) in err
        assert "读取失败" not in err
        assert "为空" not in err
        assert "不存在" not in err
        assert _GUIDANCE not in err
        assert "Traceback" not in err
        assert stat.S_IMODE(states.stat().st_mode) == 0
        assert init_fake.count == 0
        assert run_sources.count == 0
    finally:
        states.chmod(original_mode)
    assert _snapshot(states) == before
