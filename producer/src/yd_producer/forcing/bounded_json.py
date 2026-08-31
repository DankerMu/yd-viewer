"""Bounded JSON loader for work-local forcing manifests and catalogs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 250_000


class BoundedJSONError(ValueError):
    """Raised when JSON bytes cannot be decoded within the configured bounds."""


def load_bounded_json(
    content: bytes,
    *,
    max_bytes: int = MAX_JSON_BYTES,
    max_depth: int = MAX_JSON_DEPTH,
    max_nodes: int = MAX_JSON_NODES,
) -> Any:
    """Decode UTF-8 JSON with byte, depth, and node caps.

    ``RecursionError`` from the decoder is mapped to :class:`BoundedJSONError`
    so callers never leak the raw recursion failure.
    """

    if len(content) > max_bytes:
        raise BoundedJSONError(f"JSON payload exceeds the {max_bytes} byte read limit.")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BoundedJSONError("JSON payload is not valid UTF-8.") from error
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise BoundedJSONError(f"JSON payload is malformed: {error}") from error
    except RecursionError as error:
        raise BoundedJSONError(
            "JSON payload exceeds decoder recursion limits."
        ) from error
    _enforce_json_bounds(payload, max_depth=max_depth, max_nodes=max_nodes)
    return payload


def _enforce_json_bounds(payload: Any, *, max_depth: int, max_nodes: int) -> None:
    remaining = max_nodes

    def walk(value: Any, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise BoundedJSONError(f"JSON payload exceeds the {max_nodes} node limit.")
        if depth > max_depth:
            raise BoundedJSONError(
                f"JSON payload exceeds the maximum nesting depth of {max_depth}."
            )
        if isinstance(value, Mapping):
            for nested in value.values():
                walk(nested, depth + 1)
        elif isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for nested in value:
                walk(nested, depth + 1)

    walk(payload, 1)
