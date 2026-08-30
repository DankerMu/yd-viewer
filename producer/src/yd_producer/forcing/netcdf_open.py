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


def _checksum_descriptor(file_fd: int) -> str:
    digest = hashlib.sha256()
    try:
        while chunk := os.read(file_fd, 1024 * 1024):
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
) -> Iterator[Any]:
    """Open a canonical NetCDF product through a no-follow descriptor alias."""

    path = object_store.resolve_path(key_or_uri)
    file_fd = open_file_no_follow(path, containment_root=object_store.root)
    dataset = None
    try:
        if expected_checksum is not None:
            actual_checksum = _checksum_descriptor(file_fd)
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
