"""create_app() validates geometry at factory time and serves health, cycles, and map/latest."""

from __future__ import annotations

import importlib
import json
import logging
import shutil
import struct
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
MAP_LATEST = "/api/map/latest"
APP_LOGGER = "yd_viewer.app"
GEOMETRY_IDS = (19, 42, 7)
DAT_COLUMN_IDS = (42, 7, 19)
LEAD0_RAW = (172800.0, 259200.0, 86400.0)
SORTED_LEAD0_VALUES = [3.0, 1.0, 2.0]
WRONG_ST = 19990101.0
VALID_TIME_12Z = "2026-08-27T12:00:00Z"
VALID_TIME_00Z = "2026-08-27T00:00:00Z"
MIXED_CYCLES = [
    {"cycle": "2026082700", "sources": ["gfs", "ifs"]},
    {"cycle": "2026082612", "sources": ["gfs"]},
]
OK_EMPTY = {"status": "ok", "latest_cycle": None}
OK_12Z = {"status": "ok", "latest_cycle": CYCLE_12}
OK_00Z = {"status": "ok", "latest_cycle": CYCLE_00}


def _dirs(
    tmp_path: Path, reach_ids: tuple[int, ...] = IDS_1_TO_5
) -> tuple[Path, Path, Path]:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    static_dir = tmp_path / "static"
    write_geometry(input_dir, reach_ids=reach_ids)
    output_dir.mkdir()
    static_dir.mkdir()
    return input_dir, output_dir, static_dir


def _settings(tmp_path: Path, reach_ids: tuple[int, ...] = IDS_1_TO_5) -> Settings:
    input_dir, output_dir, static_dir = _dirs(tmp_path, reach_ids)
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


def _set_lead0_raw(path: Path, raw_values: tuple[float, ...]) -> None:
    nc = len(raw_values)
    payload = bytearray(path.read_bytes())
    for column, value in enumerate(raw_values, start=1):
        offset = 1024 + 8 * (2 + nc) + column * 8
        payload[offset : offset + 8] = struct.pack("<d", value)
    path.write_bytes(payload)


def _warning_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]


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


def test_map_latest_prefers_gfs_and_returns_sorted_lead0_values(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, GEOMETRY_IDS)
    ifs_dat = _write_eligible(
        settings.output_dir,
        CYCLE_12,
        "ifs",
        column_ids=DAT_COLUMN_IDS,
        st=WRONG_ST,
    )
    gfs_dat = _write_eligible(
        settings.output_dir,
        CYCLE_12,
        "gfs",
        column_ids=DAT_COLUMN_IDS,
        st=WRONG_ST,
    )
    _set_lead0_raw(ifs_dat, (86400.0, 86400.0, 86400.0))
    _set_lead0_raw(gfs_dat, LEAD0_RAW)
    client = TestClient(create_app(settings))

    cycles = client.get(CYCLES)
    response = client.get(MAP_LATEST)

    assert cycles.status_code == 200
    assert cycles.json() == [{"cycle": CYCLE_12, "sources": ["gfs", "ifs"]}]
    assert response.status_code == 200
    assert response.json() == {
        "cycle": CYCLE_12,
        "source": "gfs",
        "valid_time": VALID_TIME_12Z,
        "values": SORTED_LEAD0_VALUES,
    }


def test_map_latest_uses_ifs_when_latest_cycle_has_only_ifs(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_12, "ifs")
    client = TestClient(create_app(settings))

    cycles = client.get(CYCLES)
    response = client.get(MAP_LATEST)

    assert cycles.status_code == 200
    assert cycles.json() == [{"cycle": CYCLE_12, "sources": ["ifs"]}]
    assert response.status_code == 200
    body = response.json()
    assert body["cycle"] == CYCLE_12
    assert body["source"] == "ifs"
    assert body["valid_time"] == VALID_TIME_12Z


def test_map_latest_skips_shifted_gfs_for_same_cycle_ifs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    older_gfs = _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _set_lead0_raw(older_gfs, (172800.0,) * 5)
    gfs_dat = _write_eligible(
        settings.output_dir, CYCLE_12, "gfs", minutes=SHIFTED_MINUTES
    )
    ifs_dat = _write_eligible(settings.output_dir, CYCLE_12, "ifs")
    _set_lead0_raw(ifs_dat, (86400.0,) * 5)
    client = TestClient(create_app(settings))

    with caplog.at_level(logging.WARNING, logger=APP_LOGGER):
        cycles = client.get(CYCLES)
        response = client.get(MAP_LATEST)

    assert cycles.status_code == 200
    assert cycles.json() == [
        {"cycle": CYCLE_12, "sources": ["gfs", "ifs"]},
        {"cycle": CYCLE_00, "sources": ["gfs"]},
    ]
    assert response.status_code == 200
    assert response.json() == {
        "cycle": CYCLE_12,
        "source": "ifs",
        "valid_time": VALID_TIME_12Z,
        "values": [1.0, 1.0, 1.0, 1.0, 1.0],
    }
    warnings = _warning_messages(caplog)
    assert len(warnings) == 1
    assert str(gfs_dat) in warnings[0]
    assert warnings[0] != str(gfs_dat)


def test_map_latest_falls_back_to_older_cycle_gfs_when_latest_data_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    older_ifs = _write_eligible(settings.output_dir, CYCLE_00, "ifs")
    _set_lead0_raw(older_ifs, (86400.0,) * 5)
    older_gfs = _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _set_lead0_raw(older_gfs, (172800.0,) * 5)
    latest_gfs = _write_eligible(
        settings.output_dir, CYCLE_12, "gfs", minutes=SHIFTED_MINUTES
    )
    latest_ifs = _write_eligible(
        settings.output_dir, CYCLE_12, "ifs", minutes=SHIFTED_MINUTES
    )
    client = TestClient(create_app(settings))

    with caplog.at_level(logging.WARNING, logger=APP_LOGGER):
        cycles = client.get(CYCLES)
        response = client.get(MAP_LATEST)

    assert cycles.status_code == 200
    assert cycles.json() == [
        {"cycle": CYCLE_12, "sources": ["gfs", "ifs"]},
        {"cycle": CYCLE_00, "sources": ["gfs", "ifs"]},
    ]
    assert response.status_code == 200
    assert response.json() == {
        "cycle": CYCLE_00,
        "source": "gfs",
        "valid_time": VALID_TIME_00Z,
        "values": [2.0, 2.0, 2.0, 2.0, 2.0],
    }
    warnings = _warning_messages(caplog)
    assert len(warnings) == 2
    text = "\n".join(warnings)
    assert str(latest_gfs) in text
    assert str(latest_ifs) in text


def test_map_latest_returns_404_when_all_catalog_candidates_fail_data_layer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    latest_gfs = _write_eligible(
        settings.output_dir, CYCLE_12, "gfs", minutes=SHIFTED_MINUTES
    )
    latest_ifs = _write_eligible(
        settings.output_dir, CYCLE_12, "ifs", minutes=SHIFTED_MINUTES
    )
    older_gfs = _write_eligible(
        settings.output_dir, CYCLE_00, "gfs", minutes=SHIFTED_MINUTES
    )
    older_ifs = _write_eligible(
        settings.output_dir, CYCLE_00, "ifs", minutes=SHIFTED_MINUTES
    )
    client = TestClient(create_app(settings))

    with caplog.at_level(logging.WARNING, logger=APP_LOGGER):
        cycles = client.get(CYCLES)
        response = client.get(MAP_LATEST)

    assert cycles.status_code == 200
    assert cycles.json() == [
        {"cycle": CYCLE_12, "sources": ["gfs", "ifs"]},
        {"cycle": CYCLE_00, "sources": ["gfs", "ifs"]},
    ]
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"detail"}
    detail = body["detail"]
    assert isinstance(detail, str)
    assert detail
    assert str(tmp_path) not in detail
    assert str(settings.output_dir) not in detail
    warnings = _warning_messages(caplog)
    assert len(warnings) == 4
    text = "\n".join(warnings)
    for path in (latest_gfs, latest_ifs, older_gfs, older_ifs):
        assert str(path) in text
        assert sum(str(path) in message for message in warnings) == 1


def test_map_latest_returns_404_until_done_is_published(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source_dir = settings.output_dir / CYCLE_12 / "gfs"
    dat = write_dat(source_dir / "yd.rivqdown.dat", column_ids=IDS_1_TO_5)
    _set_lead0_raw(dat, (86400.0,) * 5)
    client = TestClient(create_app(settings))

    unpublished = client.get(MAP_LATEST)
    cycles = client.get(CYCLES)
    write_done(source_dir / "DONE")
    published = client.get(MAP_LATEST)

    assert unpublished.status_code == 404
    unpublished_body = unpublished.json()
    assert set(unpublished_body) == {"detail"}
    unpublished_detail = unpublished_body["detail"]
    assert isinstance(unpublished_detail, str)
    assert unpublished_detail
    assert cycles.status_code == 200
    assert cycles.json() == []
    assert published.status_code == 200
    assert published.json() == {
        "cycle": CYCLE_12,
        "source": "gfs",
        "valid_time": VALID_TIME_12Z,
        "values": [1.0, 1.0, 1.0, 1.0, 1.0],
    }


def test_removed_output_returns_map_latest_503_with_generic_detail(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    gfs_dat = _write_eligible(
        settings.output_dir,
        CYCLE_12,
        "gfs",
        column_ids=IDS_1_TO_5,
    )
    _set_lead0_raw(gfs_dat, (86400.0,) * 5)
    client = TestClient(create_app(settings))
    first = client.get(MAP_LATEST)
    shutil.rmtree(settings.output_dir)
    response = client.get(MAP_LATEST)

    assert first.status_code == 200
    assert first.json()["source"] == "gfs"
    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"detail"}
    detail = body["detail"]
    assert isinstance(detail, str)
    assert detail
    assert str(tmp_path) not in detail
    assert str(settings.output_dir) not in detail


def test_map_latest_does_not_reread_geometry_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path, GEOMETRY_IDS)
    gfs_dat = _write_eligible(
        settings.output_dir,
        CYCLE_12,
        "gfs",
        column_ids=DAT_COLUMN_IDS,
        st=WRONG_ST,
    )
    _set_lead0_raw(gfs_dat, LEAD0_RAW)
    client = TestClient(create_app(settings))
    (settings.input_dir / "rivers.geojson").unlink()
    (settings.input_dir / "boundary.geojson").unlink()

    response = client.get(MAP_LATEST)

    assert response.status_code == 200
    assert response.json() == {
        "cycle": CYCLE_12,
        "source": "gfs",
        "valid_time": VALID_TIME_12Z,
        "values": SORTED_LEAD0_VALUES,
    }
