"""HTTP null-discharge contracts for map/curve; helpers live in test_api."""

from __future__ import annotations

import logging
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_api import (
    APP_LOGGER,
    CYCLE_00,
    CYCLE_12,
    DAT_COLUMN_IDS,
    GEOMETRY_IDS,
    GFS_COLUMN_RAW,
    GFS_SERIES,
    IFS_COLUMN_RAW,
    IFS_SERIES,
    LEAD_HOURS,
    MAP_LATEST,
    VALID_TIME_00Z,
    VALID_TIME_12Z,
    _curve,
    _set_column_raw,
    _set_lead0_raw,
    _settings,
    _warning_messages,
    _write_eligible,
)

from yd_viewer.app import create_app


def _nullable_schema(schema: dict) -> dict:
    if "$ref" in schema:
        return schema
    if schema.get("type") == "null":
        return schema
    any_of = schema.get("anyOf") or schema.get("oneOf")
    if any_of:
        return next(item for item in any_of if item.get("type") != "null")
    return schema


def _schema_allows_null(schema: dict) -> bool:
    if schema.get("type") == "null":
        return True
    if schema.get("nullable") is True:
        return True
    any_of = schema.get("anyOf") or schema.get("oneOf")
    if any_of:
        return any(item.get("type") == "null" for item in any_of)
    return False


def test_map_keeps_gfs_with_null_discharge_instead_of_falling_back_to_ifs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path, GEOMETRY_IDS)
    ifs_dat = _write_eligible(
        settings.output_dir,
        CYCLE_12,
        "ifs",
        column_ids=DAT_COLUMN_IDS,
    )
    gfs_dat = _write_eligible(
        settings.output_dir,
        CYCLE_12,
        "gfs",
        column_ids=DAT_COLUMN_IDS,
    )
    _set_lead0_raw(ifs_dat, (86400.0, 86400.0, 86400.0))
    _set_lead0_raw(gfs_dat, (math.nan, math.inf, -math.inf))
    client = TestClient(create_app(settings))
    caplog.set_level(logging.WARNING, logger=APP_LOGGER)

    response = client.get(MAP_LATEST)

    assert response.status_code == 200
    assert response.json() == {
        "cycle": CYCLE_12,
        "source": "gfs",
        "valid_time": VALID_TIME_12Z,
        "values": [None, None, None],
    }
    assert b"NaN" not in response.content
    assert b"Infinity" not in response.content
    assert _warning_messages(caplog) == []


def test_map_keeps_all_null_sole_source_without_404(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    gfs_dat = _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _set_lead0_raw(gfs_dat, (math.nan, math.nan, math.nan, math.nan, math.nan))
    client = TestClient(create_app(settings))

    response = client.get(MAP_LATEST)

    assert response.status_code == 200
    assert response.json() == {
        "cycle": CYCLE_00,
        "source": "gfs",
        "valid_time": VALID_TIME_00Z,
        "values": [None, None, None, None, None],
    }


def test_curve_keeps_both_sources_with_null_gaps(tmp_path: Path) -> None:
    settings = _settings(tmp_path, GEOMETRY_IDS)
    ifs_dat = _write_eligible(
        settings.output_dir,
        CYCLE_00,
        "ifs",
        column_ids=DAT_COLUMN_IDS,
    )
    gfs_dat = _write_eligible(
        settings.output_dir,
        CYCLE_00,
        "gfs",
        column_ids=DAT_COLUMN_IDS,
    )
    gfs_raw = list(GFS_COLUMN_RAW)
    gfs_raw[5] = math.nan
    gfs_raw[6] = math.inf
    gfs_raw[7] = -math.inf
    _set_column_raw(gfs_dat, nc=3, column=3, raw_values=tuple(gfs_raw))
    _set_column_raw(ifs_dat, nc=3, column=3, raw_values=IFS_COLUMN_RAW)
    client = TestClient(create_app(settings))

    response = client.get(_curve(CYCLE_00, 19))

    expected_gfs = list(GFS_SERIES)
    expected_gfs[5] = None
    expected_gfs[6] = None
    expected_gfs[7] = None
    assert response.status_code == 200
    body = response.json()
    assert body["cycle"] == CYCLE_00
    assert body["reach_id"] == 19
    assert body["lead_hours"] == LEAD_HOURS
    assert list(body["series"]) == ["gfs", "ifs"]
    assert body["series"]["gfs"] == expected_gfs
    assert body["series"]["ifs"] == IFS_SERIES
    assert len(body["series"]["gfs"]) == 168
    assert b"NaN" not in response.content
    assert b"Infinity" not in response.content


def test_curve_keeps_all_null_source_without_404(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    gfs_dat = _write_eligible(settings.output_dir, CYCLE_00, "gfs")
    _set_column_raw(
        gfs_dat,
        nc=5,
        column=1,
        raw_values=tuple(math.nan for _ in range(168)),
    )
    client = TestClient(create_app(settings))

    response = client.get(_curve(CYCLE_00, 1))

    assert response.status_code == 200
    assert response.json()["series"] == {"gfs": [None] * 168}


def test_map_and_curve_openapi_declare_nullable_discharge(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(_settings(tmp_path)))
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]

    map_schema = spec["paths"]["/api/map/latest"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    map_props = schemas[map_schema["$ref"].rsplit("/", 1)[-1]]["properties"]
    values = _nullable_schema(map_props["values"])
    value_items = _nullable_schema(values["items"])
    assert values["type"] == "array"
    assert value_items["type"] == "number"
    assert _schema_allows_null(values["items"])
    assert map_props["cycle"]["type"] == "string"
    assert map_props["source"]["type"] == "string"
    assert map_props["valid_time"]["type"] == "string"

    curve_schema = spec["paths"]["/api/cycles/{cycle}/reaches/{reach_id}"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    curve_props = schemas[curve_schema["$ref"].rsplit("/", 1)[-1]]["properties"]
    series = _nullable_schema(curve_props["series"])
    series_additional = series["additionalProperties"]
    series_array = _nullable_schema(series_additional)
    series_items = _nullable_schema(series_array["items"])
    assert series["type"] == "object"
    assert series_array["type"] == "array"
    assert series_items["type"] == "number"
    assert _schema_allows_null(series_array["items"])
    assert curve_props["cycle"]["type"] == "string"
    assert curve_props["reach_id"]["type"] == "integer"
    assert _nullable_schema(curve_props["lead_hours"])["type"] == "array"
