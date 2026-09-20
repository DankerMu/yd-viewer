"""Load rivers and boundary GeoJSON and return the reach_id authority."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

RIVERS = "rivers.geojson"
BOUNDARY = "boundary.geojson"
FEATURE_COLLECTION = "FeatureCollection"
FEATURE = "Feature"
POLYGON_TYPES = ("Polygon", "MultiPolygon")


class GeometryError(Exception):
    """Geometry files are missing or do not match the viewer contract."""


@dataclass(frozen=True, kw_only=True)
class Geometry:
    reach_ids: set[int]


def load_geometry(input_dir: str | Path) -> Geometry:
    directory = Path(input_dir)
    rivers = _read_json(directory / RIVERS)
    boundary = _read_json(directory / BOUNDARY)
    reach_ids = _rivers_reach_ids(rivers, directory / RIVERS)
    _validate_boundary(boundary, directory / BOUNDARY)
    return Geometry(reach_ids=reach_ids)


def _read_json(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GeometryError(f"无法读取 {path.name}：{path}") from exc
    except UnicodeDecodeError as exc:
        raise GeometryError(f"{path.name} 不是 UTF-8：{path}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeometryError(f"{path.name} 不是合法 JSON：{path}") from exc


def _rivers_reach_ids(payload: object, path: Path) -> set[int]:
    if not isinstance(payload, dict) or payload.get("type") != FEATURE_COLLECTION:
        raise GeometryError(f"{path.name} 顶层必须是 FeatureCollection：{path}")
    features = payload.get("features")
    if not isinstance(features, list):
        raise GeometryError(f"{path.name} 的 features 必须是数组：{path}")
    reach_ids: set[int] = set()
    seen: dict[int, int] = {}
    for position, feature in enumerate(features, start=1):
        reach_id = _feature_reach_id(feature, path, position)
        if reach_id in seen:
            raise GeometryError(
                f"{path.name} 的 reach_id {reach_id} 重复"
                f"（第 {seen[reach_id]} 个与第 {position} 个 Feature）：{path}"
            )
        seen[reach_id] = position
        reach_ids.add(reach_id)
    return reach_ids


def _feature_reach_id(feature: object, path: Path, position: int) -> int:
    if not isinstance(feature, dict) or feature.get("type") != FEATURE:
        raise GeometryError(f"{path.name} 第 {position} 个要素必须是 Feature：{path}")
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        raise GeometryError(
            f"{path.name} 第 {position} 个 Feature 的 properties 必须是对象：{path}"
        )
    if "reach_id" not in properties:
        raise GeometryError(
            f"{path.name} 第 {position} 个 Feature 缺少 reach_id：{path}"
        )
    value = properties["reach_id"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise GeometryError(
            f"{path.name} 第 {position} 个 Feature 的 reach_id 不是整数"
            f"（{value!r}）：{path}"
        )
    return value


def _validate_boundary(payload: object, path: Path) -> None:
    if not isinstance(payload, dict) or payload.get("type") != FEATURE:
        raise GeometryError(f"{path.name} 顶层必须是单个 Feature：{path}")
    geometry = payload.get("geometry")
    if not isinstance(geometry, dict):
        raise GeometryError(f"{path.name} 的 geometry 必须是对象：{path}")
    geom_type = geometry.get("type")
    if not isinstance(geom_type, str) or geom_type not in POLYGON_TYPES:
        raise GeometryError(
            f"{path.name} 的 geometry.type 必须是 Polygon 或 MultiPolygon"
            f"（得到 {geom_type!r}）：{path}"
        )
