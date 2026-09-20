"""create_app() validates geometry at factory time and serves GET /api/health and GET /api/cycles."""

from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from synthetic import write_dat, write_done, write_geometry

from yd_viewer.app import create_app
from yd_viewer.geometry import GeometryError
from yd_viewer.settings import Settings, SettingsError

CYCLE_00 = "2026082700"
CYCLE_2612 = "2026082612"
CYCLE_12 = "2026082712"
CYCLE_NEWER = "2026082800"
IDS_1_TO_5 = (1, 2, 3, 4, 5)
SHIFTED_MINUTES = tuple(range(60, 10081, 60))
INPUT = "YD_VIEWER_INPUT_DIR"
OUTPUT = "YD_VIEWER_OUTPUT_DIR"
STATIC = "YD_VIEWER_STATIC_DIR"
HEALTH = "/api/health"
CYCLES = "/api/cycles"
MIXED_CYCLES = [
    {"cycle": "2026082700", "sources": ["gfs", "ifs"]},
    {"cycle": "2026082612", "sources": ["gfs"]},
]
OK_EMPTY = {"status": "ok", "latest_cycle": None}
OK_12Z = {"status": "ok", "latest_cycle": CYCLE_12}
OK_00Z = {"status": "ok", "latest_cycle": CYCLE_00}


def _dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    static_dir = tmp_path / "static"
    write_geometry(input_dir, reach_ids=IDS_1_TO_5)
    output_dir.mkdir()
    static_dir.mkdir()
    return input_dir, output_dir, static_dir


def _settings(tmp_path: Path) -> Settings:
    input_dir, output_dir, static_dir = _dirs(tmp_path)
    return Settings(
        input_dir=input_dir,
        output_dir=output_dir,
        static_dir=static_dir,
    )


def _write_eligible(
    output_dir: Path,
    cycle: str,
    source: str,
    **dat_kwargs,
) -> Path:
    source_dir = output_dir / cycle / source
    write_done(source_dir / "DONE")
    kwargs = {"column_ids": IDS_1_TO_5, **dat_kwargs}
    return write_dat(source_dir / "yd.rivqdown.dat", **kwargs)


def _health(settings: Settings):
    return TestClient(create_app(settings)).get(HEALTH)


def _cycles(settings: Settings):
    return TestClient(create_app(settings)).get(CYCLES)


def test_empty_output_returns_ok_with_null_latest_cycle(tmp_path: Path) -> None:
    response = _health(_settings(tmp_path))

    assert response.status_code == 200
    assert response.json() == OK_EMPTY


def test_health_reports_latest_usable_12z_cycle(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _write_eligible(settings.output_dir, CYCLE_12, "ifs")

    response = _health(settings)

    assert response.status_code == 200
    assert response.json() == OK_12Z


def test_newer_structurally_invalid_cycle_is_ignored(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _write_eligible(settings.output_dir, CYCLE_NEWER, "gfs", rows=167)

    response = _health(settings)

    assert response.status_code == 200
    assert response.json() == OK_00Z


def test_shifted_minutes_cycle_remains_health_anchor(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _write_eligible(settings.output_dir, CYCLE_12, "gfs", minutes=SHIFTED_MINUTES)

    response = _health(settings)

    assert response.status_code == 200
    assert response.json() == OK_12Z


def test_next_health_reflects_newly_published_cycle(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    client = TestClient(create_app(settings))

    first = client.get(HEALTH)
    _write_eligible(settings.output_dir, CYCLE_12, "ifs")
    second = client.get(HEALTH)

    assert first.status_code == 200
    assert first.json() == OK_00Z
    assert second.status_code == 200
    assert second.json() == OK_12Z


def test_removed_output_returns_503_with_generic_detail(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    first = client.get(HEALTH)
    shutil.rmtree(settings.output_dir)
    response = client.get(HEALTH)

    assert first.status_code == 200
    assert first.json() == OK_EMPTY
    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"detail"}
    detail = body["detail"]
    assert isinstance(detail, str)
    assert detail
    assert str(tmp_path) not in detail
    assert str(settings.output_dir) not in detail


def test_missing_boundary_raises_at_factory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    (settings.input_dir / "boundary.geojson").unlink()

    with pytest.raises(GeometryError) as excinfo:
        create_app(settings)

    assert "boundary.geojson" in str(excinfo.value)


def test_boundary_feature_collection_raises_at_factory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    (settings.input_dir / "boundary.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": []}),
        encoding="utf-8",
    )

    with pytest.raises(GeometryError) as excinfo:
        create_app(settings)

    assert "boundary.geojson" in str(excinfo.value)


def test_missing_required_env_fails_noarg_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_dir, _output_dir, static_dir = _dirs(tmp_path)
    monkeypatch.setenv(INPUT, str(input_dir))
    monkeypatch.delenv(OUTPUT, raising=False)
    monkeypatch.setenv(STATIC, str(static_dir))

    with pytest.raises(SettingsError) as excinfo:
        create_app()

    assert OUTPUT in str(excinfo.value)


def test_noarg_factory_uses_environment_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_dir, output_dir, static_dir = _dirs(tmp_path)
    _write_eligible(output_dir, CYCLE_12, "gfs")
    monkeypatch.setenv(INPUT, str(input_dir))
    monkeypatch.setenv(OUTPUT, str(output_dir))
    monkeypatch.setenv(STATIC, str(static_dir))

    response = TestClient(create_app()).get(HEALTH)

    assert response.status_code == 200
    assert response.json() == OK_12Z


def test_supplied_settings_ignore_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_12, "ifs")
    for name in (INPUT, OUTPUT, STATIC):
        monkeypatch.delenv(name, raising=False)

    response = _health(settings)

    assert response.status_code == 200
    assert response.json() == OK_12Z


def test_health_does_not_reread_geometry_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    (settings.input_dir / "rivers.geojson").unlink()
    (settings.input_dir / "boundary.geojson").unlink()

    response = client.get(HEALTH)

    assert response.status_code == 200
    assert response.json() == OK_EMPTY


def test_importing_app_does_not_require_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (INPUT, OUTPUT, STATIC):
        monkeypatch.delenv(name, raising=False)

    import yd_viewer.app as app_module

    reloaded = importlib.reload(app_module)

    assert callable(reloaded.create_app)


def test_cycles_returns_mixed_dual_and_single_source_array(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_2612, "gfs")
    _write_eligible(settings.output_dir, CYCLE_00, "ifs")
    _write_eligible(settings.output_dir, CYCLE_00, "gfs")

    response = _cycles(settings)

    assert response.status_code == 200
    assert response.json() == MIXED_CYCLES


def test_cycles_empty_output_returns_empty_array(tmp_path: Path) -> None:
    response = _cycles(_settings(tmp_path))

    assert response.status_code == 200
    assert response.json() == []


def test_cycles_does_not_reread_geometry_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_2612, "gfs")
    _write_eligible(settings.output_dir, CYCLE_00, "ifs")
    _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    client = TestClient(create_app(settings))
    (settings.input_dir / "rivers.geojson").unlink()

    response = client.get(CYCLES)

    assert response.status_code == 200
    assert response.json() == MIXED_CYCLES


def test_next_cycles_reflects_newly_published_cycle(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_2612, "gfs")
    client = TestClient(create_app(settings))

    first = client.get(CYCLES)
    _write_eligible(settings.output_dir, CYCLE_00, "ifs")
    _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    second = client.get(CYCLES)

    assert first.status_code == 200
    assert first.json() == [{"cycle": "2026082612", "sources": ["gfs"]}]
    assert second.status_code == 200
    assert second.json() == MIXED_CYCLES


def test_removed_output_returns_cycles_503_with_generic_detail(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    first = client.get(CYCLES)
    shutil.rmtree(settings.output_dir)
    response = client.get(CYCLES)

    assert first.status_code == 200
    assert first.json() == []
    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"detail"}
    detail = body["detail"]
    assert isinstance(detail, str)
    assert detail
    assert str(tmp_path) not in detail
    assert str(settings.output_dir) not in detail
