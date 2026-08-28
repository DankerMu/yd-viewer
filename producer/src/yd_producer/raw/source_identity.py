# NWM@8ae9b8f2 packages/common/source_identity.py
"""Shared source-id normalization for storage boundaries."""

from __future__ import annotations

_STORAGE_SOURCE_IDS = {
    "GFS": "gfs",
    "IFS": "ifs",
}


def normalize_source_id(source_id: str | None) -> str:
    if source_id is None:
        raise ValueError("source_id must not be None")
    normalized = _STORAGE_SOURCE_IDS.get(source_id.upper())
    if normalized is None:
        raise ValueError(f"Unknown source_id: {source_id!r}")
    return normalized
