"""load_settings() reads only the three directory environment variables."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from yd_viewer.settings import Settings, SettingsError, load_settings

INPUT = "YD_VIEWER_INPUT_DIR"
OUTPUT = "YD_VIEWER_OUTPUT_DIR"
STATIC = "YD_VIEWER_STATIC_DIR"
ALL = (INPUT, OUTPUT, STATIC)


def _three_dirs(tmp_path: Path) -> dict[str, Path]:
    dirs = {
        INPUT: tmp_path / "input",
        OUTPUT: tmp_path / "output",
        STATIC: tmp_path / "static",
    }
    for path in dirs.values():
        path.mkdir(parents=True)
    return dirs


def _apply_env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str | None]) -> None:
    for name in ALL:
        value = values.get(name)
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def _valid_env(tmp_path: Path) -> dict[str, str]:
    return {name: str(path) for name, path in _three_dirs(tmp_path).items()}


@contextmanager
def _chmod(path: Path, mode: int):
    original = stat.S_IMODE(path.stat().st_mode)
    path.chmod(mode)
    try:
        yield
    finally:
        path.chmod(original)


def _skip_if_root() -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores directory mode bits")


def test_valid_directories_return_immutable_settings(tmp_path, monkeypatch):
    dirs = _three_dirs(tmp_path)
    _apply_env(monkeypatch, {name: str(path) for name, path in dirs.items()})

    settings = load_settings()

    assert settings == Settings(
        input_dir=dirs[INPUT],
        output_dir=dirs[OUTPUT],
        static_dir=dirs[STATIC],
    )
    with pytest.raises(FrozenInstanceError):
        settings.input_dir = tmp_path / "other"


@pytest.mark.parametrize("missing", ALL)
def test_missing_variable_fails_with_its_name(tmp_path, monkeypatch, missing):
    values = _valid_env(tmp_path)
    del values[missing]
    _apply_env(monkeypatch, values)

    with pytest.raises(SettingsError) as excinfo:
        load_settings()

    assert missing in str(excinfo.value)


@pytest.mark.parametrize("empty", ALL)
def test_empty_variable_fails_with_name_and_empty_path(tmp_path, monkeypatch, empty):
    values = _valid_env(tmp_path)
    values[empty] = ""
    _apply_env(monkeypatch, values)

    with pytest.raises(SettingsError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    assert empty in message
    assert "''" in message


@pytest.mark.parametrize("missing_path", ALL)
def test_nonexistent_path_fails_without_creating_it(
    tmp_path, monkeypatch, missing_path
):
    values = _valid_env(tmp_path)
    absent = tmp_path / "absent" / "dir"
    values[missing_path] = str(absent)
    _apply_env(monkeypatch, values)

    with pytest.raises(SettingsError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    assert missing_path in message
    assert str(absent) in message
    assert not absent.exists()
    assert not absent.parent.exists()


@pytest.mark.parametrize("file_var", ALL)
def test_regular_file_is_rejected(tmp_path, monkeypatch, file_var):
    values = _valid_env(tmp_path)
    target = tmp_path / "not-a-dir"
    target.write_text("file", encoding="utf-8")
    values[file_var] = str(target)
    _apply_env(monkeypatch, values)

    with pytest.raises(SettingsError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    assert file_var in message
    assert str(target) in message
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "file"


@pytest.mark.parametrize("blocked", ALL)
@pytest.mark.parametrize("mode", [0o000, 0o400, 0o100])
def test_unreadable_or_untraversable_directory_is_rejected(
    tmp_path, monkeypatch, blocked, mode
):
    _skip_if_root()
    dirs = _three_dirs(tmp_path)
    _apply_env(monkeypatch, {name: str(path) for name, path in dirs.items()})
    target = dirs[blocked]

    with _chmod(target, mode), pytest.raises(SettingsError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    assert blocked in message
    assert str(target) in message
    assert os.access(target, os.R_OK | os.X_OK)


def test_unsearchable_ancestor_fails_with_variable_and_path(tmp_path, monkeypatch):
    _skip_if_root()
    dirs = _three_dirs(tmp_path)
    ancestor = tmp_path / "blocked"
    nested = ancestor / "input"
    nested.mkdir(parents=True)
    values = {name: str(path) for name, path in dirs.items()}
    values[INPUT] = str(nested)
    _apply_env(monkeypatch, values)

    with _chmod(ancestor, 0o000), pytest.raises(SettingsError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    assert INPUT in message
    assert str(nested) in message
    assert os.access(ancestor, os.R_OK | os.X_OK)


def test_read_and_search_without_write_is_accepted(tmp_path, monkeypatch):
    _skip_if_root()
    dirs = _three_dirs(tmp_path)
    _apply_env(monkeypatch, {name: str(path) for name, path in dirs.items()})

    with (
        _chmod(dirs[INPUT], 0o500),
        _chmod(dirs[OUTPUT], 0o500),
        _chmod(dirs[STATIC], 0o500),
    ):
        settings = load_settings()

    assert settings.input_dir == dirs[INPUT]
    assert settings.output_dir == dirs[OUTPUT]
    assert settings.static_dir == dirs[STATIC]


def test_does_not_fall_back_to_yd_root(tmp_path, monkeypatch):
    yd_root = tmp_path / "yd-root"
    (yd_root / "input").mkdir(parents=True)
    (yd_root / "output").mkdir()
    (yd_root / "static").mkdir()
    values = _valid_env(tmp_path)
    del values[OUTPUT]
    _apply_env(monkeypatch, values)
    monkeypatch.setenv("YD_ROOT", str(yd_root))

    with pytest.raises(SettingsError) as excinfo:
        load_settings()

    assert OUTPUT in str(excinfo.value)


def test_second_load_reads_current_environment(tmp_path, monkeypatch):
    first = _three_dirs(tmp_path / "first")
    second = _three_dirs(tmp_path / "second")
    _apply_env(monkeypatch, {name: str(path) for name, path in first.items()})
    first_settings = load_settings()
    _apply_env(monkeypatch, {name: str(path) for name, path in second.items()})

    second_settings = load_settings()

    assert first_settings.input_dir == first[INPUT]
    assert second_settings == Settings(
        input_dir=second[INPUT],
        output_dir=second[OUTPUT],
        static_dir=second[STATIC],
    )


def test_load_does_not_write_or_create_paths(tmp_path, monkeypatch):
    dirs = _three_dirs(tmp_path)
    _apply_env(monkeypatch, {name: str(path) for name, path in dirs.items()})
    before = {
        path: (stat.S_IMODE(path.stat().st_mode), set(path.iterdir()))
        for path in dirs.values()
    }

    load_settings()

    for path, (mode, entries) in before.items():
        assert stat.S_IMODE(path.stat().st_mode) == mode
        assert set(path.iterdir()) == entries
