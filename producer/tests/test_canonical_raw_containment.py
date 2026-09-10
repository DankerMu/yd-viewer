"""Issue #103: canonical convert_manifest refuses outside-root raw links.

yd-owned regressions; not an NWM snapshot. Helpers are imported from
test_canonical_db_free so GFS/IFS fixtures stay on one construction path.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from test_canonical_db_free import (
    CATALOG_KEY,
    COMPACT_CYCLE,
    IFS_CATALOG_KEY,
    IFS_COMPACT_CYCLE,
    _build_converter,
    _build_ifs_converter,
    _write_grib2_raw,
    _write_ifs_netcdf_raw,
    _write_netcdf_raw,
)

from yd_producer.canonical.converter import CanonicalConversionError, CanonicalConverter
from yd_producer.store.safe_fs import open_file_no_follow

GFS_GRID_KEY = "canonical/gfs/grid/gfs_0p25/grid.json"
IFS_GRID_KEY = "canonical/ifs/grid/ifs_0p25/grid.json"
_STAGING_PREFIX = "yd-canonical-raw-"


def _canonical_paths(root: Path) -> list[Path]:
    canonical = root / "canonical"
    if not canonical.exists():
        return []
    return [path for path in canonical.rglob("*")]


def _tree_snapshot(root: Path) -> dict[str, tuple[str, int, bytes | str]]:
    snapshot: dict[str, tuple[str, int, bytes | str]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: str(item)):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            snapshot[relative] = ("symlink", info.st_mode, os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            snapshot[relative] = ("directory", info.st_mode, "")
        elif stat.S_ISREG(info.st_mode):
            snapshot[relative] = ("file", info.st_mode, path.read_bytes())
        else:
            snapshot[relative] = ("other", info.st_mode, "")
    return snapshot


def _plant_outside_link(root: Path, relative: str) -> Path:
    target = root / relative
    outside = root.parent / f"{root.name}-outside-{relative.replace('/', '_')}"
    if target.is_dir() and not target.is_symlink():
        target.rename(outside)
        target.symlink_to(outside, target_is_directory=True)
        return outside
    outside.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.rename(outside)
    else:
        outside.write_bytes(b"outside-raw")
    target.symlink_to(outside)
    return outside


def _late_gfs_key() -> str:
    return f"raw/gfs/{COMPACT_CYCLE}/gfs.t00z.pgrb2.0p25.f003.dswrf.grib2"


def _late_ifs_key() -> str:
    return f"raw/IFS/{IFS_COMPACT_CYCLE}/ifs.t06z.0p25.f003.str.grib2"


def _assert_zero_canonical_writes(converter: CanonicalConverter) -> None:
    root = Path(converter.object_store.root)
    assert _canonical_paths(root) == []
    for key in (CATALOG_KEY, IFS_CATALOG_KEY, GFS_GRID_KEY, IFS_GRID_KEY):
        assert not converter.object_store.exists(key)


@pytest.mark.parametrize("engine", ["gfs", "ifs"])
@pytest.mark.parametrize("component", ["leaf", "raw", "source", "cycle", "late"])
def test_preexisting_outside_raw_links_fail_closed_with_zero_canonical_writes(
    tmp_path: Path, engine: str, component: str
) -> None:
    converter = (
        _build_converter(tmp_path)
        if engine == "gfs"
        else _build_ifs_converter(tmp_path)
    )
    store = converter.object_store
    root = Path(store.root)
    manifest = (
        _write_netcdf_raw(store) if engine == "gfs" else _write_ifs_netcdf_raw(store)
    )
    cycle = COMPACT_CYCLE if engine == "gfs" else IFS_COMPACT_CYCLE
    source = "gfs" if engine == "gfs" else "IFS"
    first_key = str(manifest["entries"][0]["local_key"])
    late_key = _late_gfs_key() if engine == "gfs" else _late_ifs_key()
    assert first_key != late_key
    if component == "leaf":
        _plant_outside_link(root, first_key)
    elif component == "raw":
        _plant_outside_link(root, "raw")
    elif component == "source":
        _plant_outside_link(root, f"raw/{source}")
    elif component == "cycle":
        _plant_outside_link(root, f"raw/{source}/{cycle}")
    else:
        _plant_outside_link(root, late_key)
    raw_before = _tree_snapshot(root / "raw") if (root / "raw").exists() else {}

    with pytest.raises(CanonicalConversionError):
        converter.convert_manifest(manifest)

    _assert_zero_canonical_writes(converter)
    if (root / "raw").exists():
        assert _tree_snapshot(root / "raw") == raw_before


def test_original_path_replacement_after_no_follow_open_does_not_redirect_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converter = _build_converter(tmp_path)
    store = converter.object_store
    manifest = _write_grib2_raw(store)
    replaced: list[str] = []
    original_open = open_file_no_follow

    def replacing_open(path: Path, *, containment_root: Path | None = None) -> int:
        file_fd = original_open(path, containment_root=containment_root)
        if "raw/" in path.as_posix() and path.suffix == ".grib2":
            outside = tmp_path / "hostile-replacement"
            outside.write_bytes(b"not a grib replacement")
            path.unlink()
            path.symlink_to(outside)
            replaced.append(path.as_posix())
        return file_fd

    monkeypatch.setattr(
        "yd_producer.store.object_store.open_file_no_follow", replacing_open
    )
    result = converter.convert_manifest(manifest)
    assert replaced
    assert result.status == "canonical_ready"
    assert len(result.products) == 14
    assert converter.object_store.exists(CATALOG_KEY)


def test_netcdf_fallback_uses_the_same_staged_source_after_safe_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converter = _build_converter(tmp_path)
    store = converter.object_store
    manifest = _write_netcdf_raw(store)
    opened_sources: list[tuple[str, str]] = []
    replaced: list[str] = []
    original_open = open_file_no_follow
    import xarray as xr

    original_open_dataset = xr.open_dataset

    def replacing_open(path: Path, *, containment_root: Path | None = None) -> int:
        file_fd = original_open(path, containment_root=containment_root)
        posix = path.as_posix()
        if "raw/" in posix:
            outside = tmp_path / "hostile-netcdf-replacement"
            outside.write_bytes(b"not a netcdf replacement")
            path.unlink()
            path.symlink_to(outside)
            replaced.append(posix)
        return file_fd

    def tracking_open_dataset(filename: Any, *args: Any, **kwargs: Any) -> Any:
        opened_sources.append((str(filename), str(kwargs.get("engine"))))
        return original_open_dataset(filename, *args, **kwargs)

    monkeypatch.setattr(
        "yd_producer.store.object_store.open_file_no_follow", replacing_open
    )
    monkeypatch.setattr("xarray.open_dataset", tracking_open_dataset)

    result = converter.convert_manifest(manifest)
    assert replaced
    assert result.status == "canonical_ready"
    assert converter.object_store.exists(CATALOG_KEY)
    raw_opens = [
        (source, engine)
        for source, engine in opened_sources
        if _STAGING_PREFIX in Path(source).name
    ]
    assert raw_opens
    unique_sources = {source for source, _engine in raw_opens}
    for source in unique_sources:
        engines_for_source = {engine for path, engine in raw_opens if path == source}
        assert "cfgrib" in engines_for_source
        assert "netcdf4" in engines_for_source


def test_backend_failure_closes_owned_resources_without_raw_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converter = _build_converter(tmp_path)
    store = converter.object_store
    root = Path(store.root)
    manifest = _write_netcdf_raw(store)
    raw_before = _tree_snapshot(root / "raw")
    opened: list[int] = []
    staged: list[str] = []
    original_open = open_file_no_follow
    original_close = os.close

    def tracking_open(path: Path, *, containment_root: Path | None = None) -> int:
        file_fd = original_open(path, containment_root=containment_root)
        if "raw/" in path.as_posix():
            opened.append(file_fd)
        return file_fd

    def tracking_close(fd: int) -> None:
        if fd in opened:
            opened.remove(fd)
        original_close(fd)

    def exploding_open(filename: Any, *args: Any, **kwargs: Any) -> Any:
        staged.append(str(filename))
        raise OSError(errno.EIO, "injected decoder failure")

    monkeypatch.setattr(
        "yd_producer.store.object_store.open_file_no_follow", tracking_open
    )
    monkeypatch.setattr(os, "close", tracking_close)
    monkeypatch.setattr("xarray.open_dataset", exploding_open)

    with pytest.raises(CanonicalConversionError, match="injected decoder failure"):
        converter.convert_manifest(manifest)

    assert opened == []
    assert staged
    for path in staged:
        assert not Path(path).exists()
    _assert_zero_canonical_writes(converter)
    assert _tree_snapshot(root / "raw") == raw_before
