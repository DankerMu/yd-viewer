"""load_geometry() reads rivers and boundary GeoJSON once per call."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from yd_viewer.geometry import GeometryError, load_geometry

RIVERS = "rivers.geojson"
BOUNDARY = "boundary.geojson"
IDS_1_TO_5 = [1, 2, 3, 4, 5]


def _river_feature(reach_id: object) -> dict:
    return {
        "type": "Feature",
        "properties": {"reach_id": reach_id},
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
    }


def _rivers(ids: list[object]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [_river_feature(reach_id) for reach_id in ids],
    }


def _polygon() -> dict:
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
        },
    }


def _multipolygon() -> dict:
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]],
        },
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_pair(directory: Path, rivers: object, boundary: object) -> None:
    _write_json(directory / RIVERS, rivers)
    _write_json(directory / BOUNDARY, boundary)


def test_polygon_boundary_loads_reach_ids_1_through_5(tmp_path: Path) -> None:
    _write_pair(tmp_path, _rivers(IDS_1_TO_5), _polygon())

    geometry = load_geometry(tmp_path)

    assert geometry.reach_ids == {1, 2, 3, 4, 5}


def test_multipolygon_boundary_is_accepted(tmp_path: Path) -> None:
    _write_pair(tmp_path, _rivers(IDS_1_TO_5), _multipolygon())

    geometry = load_geometry(tmp_path)

    assert geometry.reach_ids == {1, 2, 3, 4, 5}


def test_rivers_top_level_feature_is_rejected(tmp_path: Path) -> None:
    _write_pair(tmp_path, _river_feature(1), _polygon())

    with pytest.raises(GeometryError) as excinfo:
        load_geometry(tmp_path)

    message = str(excinfo.value)
    assert RIVERS in message
    assert "FeatureCollection" in message


def _missing_reach_id_feature() -> dict:
    feature = _river_feature(1)
    del feature["properties"]["reach_id"]
    return feature


@pytest.mark.parametrize(
    "features",
    [
        [_river_feature(1), _missing_reach_id_feature()],
        [_river_feature(1), _river_feature(1.0)],
        [_river_feature(1), _river_feature("1")],
        [_river_feature(1), _river_feature(True)],
    ],
    ids=["missing", "float", "string", "bool"],
)
def test_reach_id_missing_or_non_integer_is_rejected(
    tmp_path: Path, features: list[dict]
) -> None:
    _write_pair(
        tmp_path,
        {"type": "FeatureCollection", "features": features},
        _polygon(),
    )

    with pytest.raises(GeometryError) as excinfo:
        load_geometry(tmp_path)

    message = str(excinfo.value)
    assert RIVERS in message
    assert "reach_id" in message
    assert "2" in message


def test_boolean_true_is_not_treated_as_reach_id_one(tmp_path: Path) -> None:
    _write_pair(tmp_path, _rivers([1, True]), _polygon())

    with pytest.raises(GeometryError) as excinfo:
        load_geometry(tmp_path)

    message = str(excinfo.value)
    assert RIVERS in message
    assert "reach_id" in message
    assert "2" in message
    assert "true" in message.lower()


def test_duplicate_reach_id_is_rejected(tmp_path: Path) -> None:
    _write_pair(tmp_path, _rivers([1, 17, 17]), _polygon())

    with pytest.raises(GeometryError) as excinfo:
        load_geometry(tmp_path)

    message = str(excinfo.value)
    assert RIVERS in message
    assert "17" in message


@pytest.mark.parametrize("count", [1, 2])
def test_boundary_feature_collection_is_rejected(tmp_path: Path, count: int) -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [_polygon() for _ in range(count)],
    }
    _write_pair(tmp_path, _rivers(IDS_1_TO_5), collection)

    with pytest.raises(GeometryError) as excinfo:
        load_geometry(tmp_path)

    assert BOUNDARY in str(excinfo.value)


def test_boundary_linestring_is_rejected(tmp_path: Path) -> None:
    boundary = {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
    }
    _write_pair(tmp_path, _rivers(IDS_1_TO_5), boundary)

    with pytest.raises(GeometryError) as excinfo:
        load_geometry(tmp_path)

    message = str(excinfo.value)
    assert BOUNDARY in message
    assert "LineString" in message


def test_missing_boundary_is_rejected(tmp_path: Path) -> None:
    _write_json(tmp_path / RIVERS, _rivers(IDS_1_TO_5))

    with pytest.raises(GeometryError) as excinfo:
        load_geometry(tmp_path)

    assert BOUNDARY in str(excinfo.value)
    assert not (tmp_path / BOUNDARY).exists()


def test_missing_rivers_is_rejected(tmp_path: Path) -> None:
    _write_json(tmp_path / BOUNDARY, _polygon())

    with pytest.raises(GeometryError) as excinfo:
        load_geometry(tmp_path)

    assert RIVERS in str(excinfo.value)
    assert not (tmp_path / RIVERS).exists()


def test_rivers_non_json_is_rejected(tmp_path: Path) -> None:
    (tmp_path / RIVERS).write_text("{", encoding="utf-8")
    _write_json(tmp_path / BOUNDARY, _polygon())

    with pytest.raises(GeometryError) as excinfo:
        load_geometry(tmp_path)

    assert RIVERS in str(excinfo.value)


def test_noncontiguous_reach_ids_are_preserved(tmp_path: Path) -> None:
    _write_pair(tmp_path, _rivers([0, -2, 7, 100]), _polygon())

    geometry = load_geometry(tmp_path)

    assert geometry.reach_ids == {0, -2, 7, 100}


def test_empty_rivers_collection_yields_empty_authority(tmp_path: Path) -> None:
    _write_pair(tmp_path, _rivers([]), _polygon())

    geometry = load_geometry(tmp_path)

    assert geometry.reach_ids == set()


@pytest.mark.parametrize(
    "rivers, boundary, filename",
    [
        ([], _polygon(), RIVERS),
        ({"type": "FeatureCollection", "features": {}}, _polygon(), RIVERS),
        ({"type": "FeatureCollection", "features": ["nope"]}, _polygon(), RIVERS),
        (
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": [], "geometry": None},
                ],
            },
            _polygon(),
            RIVERS,
        ),
        (_rivers(IDS_1_TO_5), [], BOUNDARY),
        (
            _rivers(IDS_1_TO_5),
            {"type": "Feature", "properties": {}, "geometry": []},
            BOUNDARY,
        ),
        (
            _rivers(IDS_1_TO_5),
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": ["Polygon"]},
            },
            BOUNDARY,
        ),
    ],
    ids=[
        "rivers_list",
        "features_object",
        "feature_string",
        "properties_list",
        "boundary_list",
        "geometry_list",
        "geometry_type_list",
    ],
)
def test_malformed_containers_fail_with_filename(
    tmp_path: Path, rivers: object, boundary: object, filename: str
) -> None:
    _write_pair(tmp_path, rivers, boundary)

    with pytest.raises(GeometryError) as excinfo:
        load_geometry(tmp_path)

    assert filename in str(excinfo.value)


def test_invalid_utf8_rivers_fails_with_filename(tmp_path: Path) -> None:
    (tmp_path / RIVERS).write_bytes(b"\xff")
    _write_json(tmp_path / BOUNDARY, _polygon())

    with pytest.raises(GeometryError) as excinfo:
        load_geometry(tmp_path)

    assert RIVERS in str(excinfo.value)


def test_load_does_not_write_or_create_paths(tmp_path: Path) -> None:
    _write_pair(tmp_path, _rivers(IDS_1_TO_5), _polygon())
    before = {
        path: (stat.S_IMODE(path.stat().st_mode), path.read_bytes())
        for path in (tmp_path / RIVERS, tmp_path / BOUNDARY)
    }
    entries = set(tmp_path.iterdir())

    load_geometry(tmp_path)

    assert set(tmp_path.iterdir()) == entries
    for path, (mode, payload) in before.items():
        assert stat.S_IMODE(path.stat().st_mode) == mode
        assert path.read_bytes() == payload
