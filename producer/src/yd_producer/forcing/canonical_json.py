# NWM@8ae9b8f2 packages/common/grid_signature.py
"""Canonical JSON serialization for forcing package manifests.

This module retains only the pin's ``_json_default`` / ``_json_bytes`` helpers.
Grid-signature hashing lives in the yd-authored ``grid_identity`` helper; this
file is not a second signature implementation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            normalized = value.replace(tzinfo=UTC)
        else:
            normalized = value.astimezone(UTC)
        return normalized.isoformat().replace("+00:00", "Z")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable.")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=_json_default
    ).encode("utf-8")
