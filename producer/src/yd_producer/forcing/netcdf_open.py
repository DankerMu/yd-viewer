"""Descriptor-bound NetCDF open for canonical products.

Opens the object-store leaf with ``open_file_no_follow`` and hands xarray a
kernel descriptor alias (Linux ``/proc/self/fd/<fd>`` or Darwin ``/dev/fd/<fd>``).
Neither alias available is a hard failure; the helper never falls back to a
bare ``Path``.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from yd_producer.store.object_store import LocalObjectStore
from yd_producer.store.safe_fs import open_file_no_follow

_LINUX_FD_ROOT = Path("/proc/self/fd")
_DARWIN_FD_ROOT = Path("/dev/fd")
MAX_CANONICAL_NETCDF_BYTES = 536_870_912


def descriptor_alias_path(fd: int) -> Path:
    """Return the first usable descriptor alias for ``fd``.

    Existence is checked with ``os.lstat`` so a dangling alias is not treated as
    available. The helper never returns the original filesystem path.
    """

    candidates = (_LINUX_FD_ROOT / str(fd), _DARWIN_FD_ROOT / str(fd))
    for candidate in candidates:
        try:
            os.lstat(candidate)
        except OSError:
            continue
        return candidate
    raise OSError(
        "Canonical NetCDF descriptor alias is unavailable "
        f"(tried {candidates[0]} and {candidates[1]})."
    )


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


@contextmanager
def open_canonical_netcdf(
    object_store: LocalObjectStore,
    key_or_uri: str,
    *,
    expected_checksum: str | None = None,
    max_bytes: int = MAX_CANONICAL_NETCDF_BYTES,
) -> Iterator[Any]:
    """Open a canonical NetCDF product through a bounded no-follow descriptor alias."""

    _validate_max_bytes(max_bytes)
    path = object_store.resolve_path(key_or_uri)
    file_fd = open_file_no_follow(path, containment_root=object_store.root)
    dataset = None
    try:
        size_bytes = os.fstat(file_fd).st_size
        if size_bytes > max_bytes:
            raise ValueError(
                "Canonical NetCDF exceeds byte limit: "
                f"size {size_bytes} exceeds {max_bytes} bytes."
            )
        if expected_checksum is not None:
            actual_checksum = _checksum_descriptor(file_fd, max_bytes=max_bytes)
            if actual_checksum != _normalized_sha256(expected_checksum):
                raise ValueError(
                    "Canonical checksum mismatch: "
                    f"expected {_normalized_sha256(expected_checksum)}, got {actual_checksum}."
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
            os.close(file_fd)
