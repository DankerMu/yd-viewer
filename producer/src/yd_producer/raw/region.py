# NWM@8ae9b8f2 workers/data_adapters/region.py
"""Shared geographic bounding-box config for download spatial clipping.

A single source of truth for the rectangle used to clip GFS (server-side NOMADS
subregion) and IFS (local cdo) downloads. The bbox is folded into download
product identity so a region change does not collide with previously cached
cycles.

Longitude convention: -180..180 (leftlon may be negative). The bbox has no
built-in default: it is supplied explicitly by `config.toml` and a missing
field fails closed. Keep the supplied values in the -180..180 style so NOMADS
(GFS server-side subregion) and cdo (sellonlatbox for IFS) clip the same area;
mixing in 0..360-style longitudes can silently produce mismatched regions
across the two backends.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GeoBBox:
    """Geographic bounding box.

    Longitudes use the -180..180 convention (west/east may be negative).
    Validation tolerates [-180, 360] for robustness, but -180..180 is the
    recommended/canonical form to keep GFS and IFS clipping consistent.
    """

    south: float
    north: float
    west: float
    east: float

    def __post_init__(self) -> None:
        if not -90.0 <= self.south <= 90.0 or not -90.0 <= self.north <= 90.0:
            raise ValueError(
                f"Latitude out of range [-90, 90]: south={self.south}, north={self.north}"
            )
        if not -180.0 <= self.west <= 360.0 or not -180.0 <= self.east <= 360.0:
            raise ValueError(
                f"Longitude out of range [-180, 360]: west={self.west}, east={self.east}"
            )
        if self.south >= self.north:
            raise ValueError(f"south ({self.south}) must be < north ({self.north})")
        if self.west >= self.east:
            raise ValueError(f"west ({self.west}) must be < east ({self.east})")

    def as_dict(self) -> dict[str, float]:
        return {
            "south": self.south,
            "north": self.north,
            "west": self.west,
            "east": self.east,
        }

    def identity(self) -> str:
        """Stable identity string for folding into source identity."""
        return f"bbox:s{self.south:g}:n{self.north:g}:w{self.west:g}:e{self.east:g}"
