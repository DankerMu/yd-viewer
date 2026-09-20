"""Independent SHUD v2 DAT / DONE / GeoJSON fixtures for viewer tests.

Layout follows products-contract §5.1, not the production parser: 1024-byte
text prefix, little-endian float64 `st` / `nc` / column IDs, then rows of
`nc + 1` float64s (minute column first). Must not import yd_viewer.dat or
yd_producer.
"""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Sequence
from pathlib import Path

_TEXT_HEADER = 1024


def write_dat(
    path: Path,
    *,
    column_ids: Sequence[int],
    rows: int = 168,
    minutes: Sequence[float] | None = None,
    st: float = 19000101.0,
    nan_cells: Sequence[tuple[int, int]] = (),
) -> Path:
    """Write a v2 DAT. Default minutes are `range(0, rows * 60, 60)`."""

    nc = len(column_ids)
    if nc == 0:
        raise ValueError("column_ids must not be empty")
    if minutes is None:
        minute_values = [float(value) for value in range(0, rows * 60, 60)]
    else:
        minute_values = [float(value) for value in minutes]
    if len(minute_values) != rows:
        raise ValueError(f"minutes length {len(minute_values)} != rows {rows}")

    cells: list[list[float]] = []
    for row_index, minute in enumerate(minute_values):
        row = [float(minute)]
        row.extend(float(row_index + column + 1) for column in range(nc))
        cells.append(row)
    for row_index, column_index in nan_cells:
        cells[row_index][column_index] = math.nan

    payload = bytearray(_TEXT_HEADER)
    payload[:12] = b"synthetic v2"
    payload.extend(struct.pack("<dd", float(st), float(nc)))
    payload.extend(
        struct.pack(f"<{nc}d", *[float(column_id) for column_id in column_ids])
    )
    for row in cells:
        payload.extend(struct.pack(f"<{nc + 1}d", *row))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def write_done(path: Path, *, symlink: bool = False) -> Path:
    """Write an empty DONE file, or a symlink to an empty target."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if symlink:
        target = path.with_name(path.name + ".target")
        target.write_bytes(b"")
        path.symlink_to(target.name)
    else:
        path.write_bytes(b"")
    return path


def write_rivers(path: Path, reach_ids: Sequence[int]) -> Path:
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"reach_id": reach_id},
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            }
            for reach_id in reach_ids
        ],
    }
    return _write_json(path, collection)


def write_boundary(path: Path, *, kind: str = "Polygon") -> Path:
    if kind == "Polygon":
        geometry = {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
        }
    elif kind == "MultiPolygon":
        geometry = {
            "type": "MultiPolygon",
            "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]],
        }
    else:
        raise ValueError(f"unsupported boundary kind: {kind!r}")
    return _write_json(
        path,
        {"type": "Feature", "properties": {}, "geometry": geometry},
    )


def write_geometry(
    directory: Path,
    *,
    reach_ids: Sequence[int],
    boundary: str = "Polygon",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    write_rivers(directory / "rivers.geojson", reach_ids)
    write_boundary(directory / "boundary.geojson", kind=boundary)
    return directory


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
