"""create_app() validates geometry, serves APIs, and hosts static geometry/SPA."""

from __future__ import annotations

import importlib
import json
import logging
import os
import shutil
import stat
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
LEAD_HOURS = list(range(168))
GFS_SERIES = [float(n) for n in range(1, 169)]
IFS_SERIES = [float(n) for n in range(1001, 1169)]
GFS_COLUMN_RAW = tuple(value * 86400.0 for value in GFS_SERIES)
IFS_COLUMN_RAW = tuple(value * 86400.0 for value in IFS_SERIES)
ABSENT_CYCLE = "2026082600"
OUT_WINDOW_CYCLE = "2026081900"
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


def _curve(cycle: str, reach_id: int | str) -> str:
    return f"/api/cycles/{cycle}/reaches/{reach_id}"


def _set_column_raw(
    path: Path, *, nc: int, column: int, raw_values: tuple[float, ...]
) -> None:
    payload = bytearray(path.read_bytes())
    header = 1024 + 8 * (2 + nc)
    stride = nc + 1
    for row, value in enumerate(raw_values):
        offset = header + (row * stride + column) * 8
        payload[offset : offset + 8] = struct.pack("<d", value)
    path.write_bytes(payload)


def _assert_default_detail(
    response, status: int, tmp_path: Path, output_dir: Path
) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"detail"}
    detail = body["detail"]
    if isinstance(detail, str):
        assert detail
        text = detail
    else:
        assert detail
        text = json.dumps(detail)
    assert str(tmp_path) not in text
    assert str(output_dir) not in text


INDEX_HTML = b"<!doctype html><title>yd</title>"
SHADOW_API_NOPE = b"<p>static-api-nope</p>"
SHADOW_404 = b"<h1>static-404</h1>"


def _hosted(tmp_path: Path) -> tuple[Settings, TestClient]:
    settings = _settings(tmp_path)
    for rel, payload in (
        ("geometry/rivers.geojson", b'{"shadow":"rivers"}'),
        ("geometry/boundary.geojson", b'{"shadow":"boundary"}'),
        ("index.html", INDEX_HTML),
        ("404.html", SHADOW_404),
        ("assets/app.js", b"window.YD=1"),
        ("api/nope", SHADOW_API_NOPE),
        ("api/health", b'{"status":"shadow-health"}'),
        ("api/map/latest", b'{"source":"shadow-map"}'),
        ("api/cycles/index.html", b"<p>shadow-cycles</p>"),
        (f"api/cycles/{CYCLE_00}/reaches/1", b'{"series":{"shadow":true}}'),
    ):
        path = settings.static_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return settings, TestClient(create_app(settings))


def _skip_if_root() -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores directory mode bits")


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


def test_curve_returns_selected_cycle_series_by_reach_not_column_position(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, GEOMETRY_IDS)
    ifs_dat = _write_eligible(
        settings.output_dir,
        CYCLE_00,
        "ifs",
        column_ids=DAT_COLUMN_IDS,
        st=WRONG_ST,
    )
    gfs_dat = _write_eligible(
        settings.output_dir,
        CYCLE_00,
        "gfs",
        column_ids=DAT_COLUMN_IDS,
        st=WRONG_ST,
    )
    _write_eligible(settings.output_dir, CYCLE_12, "gfs", column_ids=DAT_COLUMN_IDS)
    _set_column_raw(gfs_dat, nc=3, column=3, raw_values=GFS_COLUMN_RAW)
    _set_column_raw(ifs_dat, nc=3, column=3, raw_values=IFS_COLUMN_RAW)
    client = TestClient(create_app(settings))

    cycles = client.get(CYCLES)
    response = client.get(_curve(CYCLE_00, 19))

    assert cycles.status_code == 200
    assert cycles.json() == [
        {"cycle": CYCLE_12, "sources": ["gfs"]},
        {"cycle": CYCLE_00, "sources": ["gfs", "ifs"]},
    ]
    assert response.status_code == 200
    assert response.json() == {
        "cycle": CYCLE_00,
        "reach_id": 19,
        "lead_hours": LEAD_HOURS,
        "series": {"gfs": GFS_SERIES, "ifs": IFS_SERIES},
    }


def test_curve_returns_only_ifs_when_selected_cycle_has_no_gfs(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _write_eligible(settings.output_dir, CYCLE_12, "ifs")
    client = TestClient(create_app(settings))

    cycles = client.get(CYCLES)
    response = client.get(_curve(CYCLE_12, 1))

    assert cycles.status_code == 200
    assert cycles.json() == [
        {"cycle": CYCLE_12, "sources": ["ifs"]},
        {"cycle": CYCLE_00, "sources": ["gfs"]},
    ]
    assert response.status_code == 200
    body = response.json()
    assert body["cycle"] == CYCLE_12
    assert body["reach_id"] == 1
    assert body["lead_hours"] == LEAD_HOURS
    assert set(body["series"]) == {"ifs"}
    assert len(body["series"]["ifs"]) == 168


def test_curve_omits_shifted_gfs_and_keeps_same_cycle_ifs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    gfs_dat = _write_eligible(
        settings.output_dir, CYCLE_12, "gfs", minutes=SHIFTED_MINUTES
    )
    ifs_dat = _write_eligible(settings.output_dir, CYCLE_12, "ifs")
    _set_column_raw(ifs_dat, nc=5, column=1, raw_values=IFS_COLUMN_RAW)
    client = TestClient(create_app(settings))

    with caplog.at_level(logging.WARNING, logger=APP_LOGGER):
        cycles = client.get(CYCLES)
        response = client.get(_curve(CYCLE_12, 1))

    assert cycles.status_code == 200
    assert cycles.json() == [
        {"cycle": CYCLE_12, "sources": ["gfs", "ifs"]},
        {"cycle": CYCLE_00, "sources": ["gfs"]},
    ]
    assert response.status_code == 200
    assert response.json() == {
        "cycle": CYCLE_12,
        "reach_id": 1,
        "lead_hours": LEAD_HOURS,
        "series": {"ifs": IFS_SERIES},
    }
    warnings = _warning_messages(caplog)
    assert len(warnings) == 1
    assert str(gfs_dat) in warnings[0]
    assert warnings[0] != str(gfs_dat)


def test_curve_does_not_fall_back_to_older_cycle_when_selected_data_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    older_gfs = _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _set_column_raw(older_gfs, nc=5, column=1, raw_values=GFS_COLUMN_RAW)
    latest_gfs = _write_eligible(
        settings.output_dir, CYCLE_12, "gfs", minutes=SHIFTED_MINUTES
    )
    latest_ifs = _write_eligible(
        settings.output_dir, CYCLE_12, "ifs", minutes=SHIFTED_MINUTES
    )
    client = TestClient(create_app(settings))

    with caplog.at_level(logging.WARNING, logger=APP_LOGGER):
        cycles = client.get(CYCLES)
        response = client.get(_curve(CYCLE_12, 1))

    assert cycles.status_code == 200
    assert cycles.json() == [
        {"cycle": CYCLE_12, "sources": ["gfs", "ifs"]},
        {"cycle": CYCLE_00, "sources": ["gfs"]},
    ]
    _assert_default_detail(response, 404, tmp_path, settings.output_dir)
    warnings = _warning_messages(caplog)
    assert len(warnings) == 2
    text = "\n".join(warnings)
    assert str(latest_gfs) in text
    assert str(latest_ifs) in text
    assert str(older_gfs) not in text


def test_curve_returns_404_for_legal_absent_and_out_of_window_cycles(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    selected = _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _set_column_raw(selected, nc=5, column=1, raw_values=GFS_COLUMN_RAW)
    _write_eligible(settings.output_dir, OUT_WINDOW_CYCLE, "gfs")
    client = TestClient(create_app(settings))

    ok = client.get(_curve(CYCLE_00, 1))
    absent = client.get(_curve(ABSENT_CYCLE, 1))
    out_window = client.get(_curve(OUT_WINDOW_CYCLE, 1))

    assert ok.status_code == 200
    assert ok.json()["cycle"] == CYCLE_00
    _assert_default_detail(absent, 404, tmp_path, settings.output_dir)
    _assert_default_detail(out_window, 404, tmp_path, settings.output_dir)


@pytest.mark.parametrize(
    ("cycle", "reach_id", "status"),
    [
        ("202608270", 1, 400),
        ("2026082701", 1, 400),
        ("2026ab2700", 1, 400),
        (CYCLE_00, 99999, 400),
        (CYCLE_00, "abc", 422),
    ],
)
def test_curve_rejects_malformed_cycle_and_out_of_authority_reach(
    tmp_path: Path, cycle: str, reach_id: int | str, status: int
) -> None:
    settings = _settings(tmp_path)
    dat = _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _set_column_raw(dat, nc=5, column=1, raw_values=GFS_COLUMN_RAW)
    client = TestClient(create_app(settings))

    ok = client.get(_curve(CYCLE_00, 1))
    response = client.get(_curve(cycle, reach_id))

    assert ok.status_code == 200
    _assert_default_detail(response, status, tmp_path, settings.output_dir)


def test_removed_output_returns_curve_503_after_validating_request(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    dat = _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _set_column_raw(dat, nc=5, column=1, raw_values=GFS_COLUMN_RAW)
    client = TestClient(create_app(settings))
    first = client.get(_curve(CYCLE_00, 1))
    shutil.rmtree(settings.output_dir)

    valid = client.get(_curve(CYCLE_00, 1))
    malformed = client.get(_curve("2026082701", 1))
    unknown_reach = client.get(_curve(CYCLE_00, 99999))

    assert first.status_code == 200
    _assert_default_detail(valid, 503, tmp_path, settings.output_dir)
    _assert_default_detail(malformed, 400, tmp_path, settings.output_dir)
    _assert_default_detail(unknown_reach, 400, tmp_path, settings.output_dir)


def test_curve_does_not_reread_geometry_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path, GEOMETRY_IDS)
    gfs_dat = _write_eligible(
        settings.output_dir,
        CYCLE_00,
        "gfs",
        column_ids=DAT_COLUMN_IDS,
        st=WRONG_ST,
    )
    _set_column_raw(gfs_dat, nc=3, column=3, raw_values=GFS_COLUMN_RAW)
    client = TestClient(create_app(settings))
    (settings.input_dir / "rivers.geojson").unlink()
    (settings.input_dir / "boundary.geojson").unlink()

    response = client.get(_curve(CYCLE_00, 19))

    assert response.status_code == 200
    assert response.json() == {
        "cycle": CYCLE_00,
        "reach_id": 19,
        "lead_hours": LEAD_HOURS,
        "series": {"gfs": GFS_SERIES},
    }


@pytest.mark.parametrize(
    "path",
    [HEALTH, CYCLES, MAP_LATEST, _curve(CYCLE_00, 1)],
    ids=["health", "cycles", "map", "curve"],
)
def test_unreadable_output_returns_503_then_recovers_without_restart(
    tmp_path: Path, path: str
) -> None:
    _skip_if_root()
    settings = _settings(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    client = TestClient(create_app(settings))

    first = client.get(path)
    assert first.status_code == 200
    first_body = first.json()

    original_mode = stat.S_IMODE(settings.output_dir.stat().st_mode)
    try:
        settings.output_dir.chmod(0o000)
        response = client.get(path)
        _assert_default_detail(response, 503, tmp_path, settings.output_dir)
        content_type = response.headers.get("content-type", "")
        assert "json" in content_type
        assert "html" not in content_type
    finally:
        settings.output_dir.chmod(original_mode)

    assert stat.S_IMODE(settings.output_dir.stat().st_mode) == original_mode
    restored = client.get(path)
    assert restored.status_code == 200
    assert restored.json() == first_body


def test_geometry_bytes_match_input_not_static_shadow(tmp_path: Path) -> None:
    settings, client = _hosted(tmp_path)
    for name in ("rivers.geojson", "boundary.geojson"):
        expected = (settings.input_dir / name).read_bytes()
        shadow = (settings.static_dir / "geometry" / name).read_bytes()
        response = client.get(f"/geometry/{name}")
        assert response.status_code == 200
        assert response.content == expected != shadow


def test_root_serves_index_and_assets_without_history_fallback(
    tmp_path: Path,
) -> None:
    _, client = _hosted(tmp_path)
    index, asset = client.get("/"), client.get("/assets/app.js")
    missing = client.get("/nowhere")
    assert index.status_code == 200
    assert index.content == INDEX_HTML
    assert asset.status_code == 200
    assert asset.content == b"window.YD=1"
    assert missing.status_code == 404
    assert INDEX_HTML not in missing.content


def test_unknown_api_is_non_html_404_despite_static_files(tmp_path: Path) -> None:
    settings, client = _hosted(tmp_path)
    for path in ("/api/nope", "/api/also-missing"):
        response = client.get(path)
        _assert_default_detail(response, 404, tmp_path, settings.output_dir)
        assert "html" not in response.headers.get("content-type", "")
        assert SHADOW_API_NOPE not in response.content
        assert SHADOW_404 not in response.content
        assert INDEX_HTML not in response.content


def test_existing_api_routes_keep_precedence_over_static_shadows(
    tmp_path: Path,
) -> None:
    settings, client = _hosted(tmp_path)
    _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    health = client.get(HEALTH)
    cycles = client.get(CYCLES)
    latest = client.get(MAP_LATEST)
    curve = client.get(_curve(CYCLE_00, 1))
    assert health.status_code == 200
    assert health.json() == OK_00Z
    assert cycles.status_code == 200
    assert cycles.json() == [{"cycle": CYCLE_00, "sources": ["gfs"]}]
    assert latest.status_code == 200
    assert latest.json() == {
        "cycle": CYCLE_00,
        "source": "gfs",
        "valid_time": VALID_TIME_00Z,
        "values": [float(n) / 86400.0 for n in range(1, 6)],
    }
    assert curve.status_code == 200
    assert curve.json() == {
        "cycle": CYCLE_00,
        "reach_id": 1,
        "lead_hours": LEAD_HOURS,
        "series": {"gfs": [float(n) / 86400.0 for n in range(1, 169)]},
    }
