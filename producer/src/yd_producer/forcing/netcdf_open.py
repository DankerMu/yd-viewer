"""Descriptor-bound NetCDF open for canonical products.

Opens the object-store leaf with ``open_file_no_follow``. Linux hands xarray
the kernel descriptor alias ``/proc/self/fd/<fd>``. Darwin reads a bounded
immutable memory payload from the same admitted FD and opens
``netCDF4.Dataset(memory=payload)`` through ``NetCDF4DataStore``. Neither
platform falls back to a bare ``Path``.
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from yd_producer.store.object_store import LocalObjectStore
from yd_producer.store.safe_fs import open_file_no_follow

_LINUX_FD_ROOT = Path("/proc/self/fd")
_STREAM_CHUNK_BYTES = 1024 * 1024
MAX_CANONICAL_NETCDF_BYTES = 536_870_912


def descriptor_alias_path(fd: int) -> Path:
    """Return the Linux ``/proc/self/fd/<fd>`` alias when it is usable.

    Existence is checked with ``os.lstat`` so a dangling alias is not treated as
    available. The helper never returns the original filesystem path and does
    not fall back to Darwin ``/dev/fd``.
    """

    candidate = _LINUX_FD_ROOT / str(fd)
    try:
        os.lstat(candidate)
    except OSError as error:
        raise OSError(
            f"Canonical NetCDF descriptor alias is unavailable (tried {candidate})."
        ) from error
    return candidate


def _normalized_sha256(expected_checksum: str) -> str:
    return expected_checksum.strip().removeprefix("sha256:").lower()


def _validate_max_bytes(max_bytes: int) -> None:
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer.")


def _checksum_descriptor(file_fd: int, *, max_bytes: int) -> str:
    """Hash one descriptor without reading more than ``max_bytes`` and rewind it."""

    _validate_max_bytes(max_bytes)
    digest = hashlib.sha256()
    observed_bytes = 0
    try:
        while chunk := os.read(file_fd, 1024 * 1024):
            observed_bytes += len(chunk)
            if observed_bytes > max_bytes:
                raise ValueError(
                    "Canonical NetCDF exceeds byte limit: "
                    f"observed more than {max_bytes} bytes."
                )
            digest.update(chunk)
    finally:
        os.lseek(file_fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def _read_immutable_descriptor_bytes(file_fd: int, *, max_bytes: int) -> bytes:
    """Read one admitted FD into immutable bytes without exceeding ``max_bytes``."""

    buffer = io.BytesIO()
    observed_bytes = 0
    remaining = max_bytes
    while True:
        chunk = os.read(file_fd, min(_STREAM_CHUNK_BYTES, remaining + 1))
        if not chunk:
            break
        observed_bytes += len(chunk)
        if observed_bytes > max_bytes:
            raise ValueError(
                "Canonical NetCDF exceeds byte limit: "
                f"observed more than {max_bytes} bytes."
            )
        buffer.write(chunk)
        remaining = max_bytes - observed_bytes
    return buffer.getvalue()


@contextmanager
def open_canonical_netcdf(
    object_store: LocalObjectStore,
    key_or_uri: str,
    *,
    expected_checksum: str | None = None,
    max_bytes: int = MAX_CANONICAL_NETCDF_BYTES,
) -> Iterator[Any]:
    """Open a canonical NetCDF product through a bounded no-follow descriptor."""

    _validate_max_bytes(max_bytes)
    path = object_store.resolve_path(key_or_uri)
    file_fd = open_file_no_follow(path, containment_root=object_store.root)
    dataset = None
    native = None
    payload = b""
    try:
        size_bytes = os.fstat(file_fd).st_size
        if size_bytes > max_bytes:
            raise ValueError(
                "Canonical NetCDF exceeds byte limit: "
                f"size {size_bytes} exceeds {max_bytes} bytes."
            )
        if sys.platform == "darwin":
            payload = _read_immutable_descriptor_bytes(file_fd, max_bytes=max_bytes)
            if expected_checksum is not None:
                actual_checksum = hashlib.sha256(payload).hexdigest()
                if actual_checksum != _normalized_sha256(expected_checksum):
                    raise ValueError(
                        "Canonical checksum mismatch: "
                        f"expected {_normalized_sha256(expected_checksum)}, "
                        f"got {actual_checksum}."
                    )
            import netCDF4
            import xarray as xr

            native = netCDF4.Dataset("canonical", mode="r", memory=payload)
            store = xr.backends.NetCDF4DataStore(native)
            dataset = xr.open_dataset(store, engine="store")
        else:
            if expected_checksum is not None:
                actual_checksum = _checksum_descriptor(file_fd, max_bytes=max_bytes)
                if actual_checksum != _normalized_sha256(expected_checksum):
                    raise ValueError(
                        "Canonical checksum mismatch: "
                        f"expected {_normalized_sha256(expected_checksum)}, "
                        f"got {actual_checksum}."
                    )
            alias = descriptor_alias_path(file_fd)
            import xarray as xr

            dataset = xr.open_dataset(alias)
        yield dataset
    finally:
        try:
            if dataset is not None:
                dataset.close()
        finally:
            try:
                if native is not None and native.isopen():
                    native.close()
            finally:
                os.close(file_fd)
