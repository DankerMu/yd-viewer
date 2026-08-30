"""Ordered grid-identity helper for direct-grid production checks.

Computes SHA-256 over the canonical JSON envelope of ordered
``(grid_cell_id, round(lon, 12), round(lat, 12))`` tuples. This is a yd-authored
pure helper: it does not import NWM or a grid-registry package.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from yd_producer.forcing.canonical_json import _json_bytes
from yd_producer.store.object_store import sha256_bytes

COORDINATE_ROUNDING_DECIMALS = 12


class GridIdentityPoint(Protocol):
    grid_cell_id: str
    longitude: float
    latitude: float


def grid_identity_tuples(
    grid_points: Sequence[GridIdentityPoint],
) -> tuple[tuple[str, float, float], ...]:
    return tuple(
        (
            point.grid_cell_id,
            round(float(point.longitude), COORDINATE_ROUNDING_DECIMALS),
            round(float(point.latitude), COORDINATE_ROUNDING_DECIMALS),
        )
        for point in grid_points
    )


def grid_identity_hash(grid_points: Sequence[GridIdentityPoint]) -> str:
    return sha256_bytes(_json_bytes({"grid_points": grid_identity_tuples(grid_points)}))
