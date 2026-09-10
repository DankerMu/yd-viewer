"""Issue #102: bounded canonical JSON/raw ingestion and NumPy grid retention.

yd-owned regressions; not an NWM snapshot. Public seams are load_manifest,
convert_manifest and the reader-returned RawRecord.values. Limits are injected
at the converter consumer constants so oversize cases stay tiny.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Self

import numpy as np
import pytest
from netcdf_fixture import default_gfs_value
from test_canonical_db_free import (
    COMPACT_CYCLE,
    FALLBACK_WARNING,
    GRIB_SHORT_NAMES,
    GRID_CELLS,
    _build_converter,
    _encode_grib2,
    _write_netcdf_raw,
)

from yd_producer.canonical import converter as converter_module
from yd_producer.canonical.converter import CanonicalConversionError
from yd_producer.store.object_store import LocalObjectStore

GFS_GRID_KEY = "canonical/gfs/grid/gfs_0p25/grid.json"
MANIFEST_KEY = f"raw/gfs/{COMPACT_CYCLE}/raw-manifest.json"
_STAGING_PREFIX = "yd-canonical-raw-"
_TINY_JSON_LIMIT = 512


def _inject_limit(monkeypatch: pytest.MonkeyPatch, name: str, value: int) -> None:
    monkeypatch.setattr(converter_module, name, value, raising=False)


def _json_bytes_at_size(payload: dict[str, Any], size: int) -> bytes:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > size:
        raise AssertionError(f"compact JSON is {len(raw)} bytes, over {size}")
    return raw + b" " * (size - len(raw))


def _minimal_manifest_payload() -> dict[str, Any]:
    return {
        "source_id": "gfs",
        "cycle_time": converter_module.parse_cycle_time(COMPACT_CYCLE).isoformat(),
        "entries": [],
    }


def _matching_cell_grid_payload() -> dict[str, Any]:
    return {
        "schema_version": "nhms.grid_definition.v1",
        "grid_id": "gfs_0p25",
        "cells": [{"id": 0, "lon": 0.0, "lat": 0.0}],
    }


def _write_json(
    store: LocalObjectStore, key: str, payload: dict[str, Any], size: int
) -> bytes:
    content = _json_bytes_at_size(payload, size)
    store.write_bytes_atomic(key, content)
    return content


def _assert_input_size_error(
    error: CanonicalConversionError, *, key: str, observed: int, limit: int
) -> None:
    message = str(error)
    assert "input size" in message
    assert key in message
    assert str(observed) in message
    assert str(limit) in message


def test_manifest_json_at_byte_limit_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inject_limit(monkeypatch, "MAX_OBJECT_MANIFEST_BYTES", _TINY_JSON_LIMIT)
    converter = _build_converter(tmp_path)
    _write_json(
        converter.object_store,
        MANIFEST_KEY,
        _minimal_manifest_payload(),
        _TINY_JSON_LIMIT,
    )

    loaded = converter.load_manifest(MANIFEST_KEY)

    assert loaded["source_id"] == "gfs"
    assert loaded["entries"] == []


def test_manifest_json_one_byte_over_limit_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inject_limit(monkeypatch, "MAX_OBJECT_MANIFEST_BYTES", _TINY_JSON_LIMIT)
    converter = _build_converter(tmp_path)
    _write_json(
        converter.object_store,
        MANIFEST_KEY,
        _minimal_manifest_payload(),
        _TINY_JSON_LIMIT + 1,
    )

    with pytest.raises(
        CanonicalConversionError, match="Failed to load manifest"
    ) as info:
        converter.load_manifest(MANIFEST_KEY)

    assert "exceeds read limit" in str(info.value)


def test_existing_grid_json_at_byte_limit_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inject_limit(monkeypatch, "MAX_OBJECT_MANIFEST_BYTES", _TINY_JSON_LIMIT)
    converter = _build_converter(tmp_path)
    manifest = _write_netcdf_raw(converter.object_store)
    _write_json(
        converter.object_store,
        GFS_GRID_KEY,
        _matching_cell_grid_payload(),
        _TINY_JSON_LIMIT,
    )

    result = converter.convert_manifest(manifest)

    assert result.status == "canonical_ready"


def test_existing_grid_json_one_byte_over_limit_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inject_limit(monkeypatch, "MAX_OBJECT_MANIFEST_BYTES", _TINY_JSON_LIMIT)
    converter = _build_converter(tmp_path)
    manifest = _write_netcdf_raw(converter.object_store)
    _write_json(
        converter.object_store,
        GFS_GRID_KEY,
        _matching_cell_grid_payload(),
        _TINY_JSON_LIMIT + 1,
    )

    with pytest.raises(CanonicalConversionError) as info:
        converter.convert_manifest(manifest)

    assert "exceeds read limit" in str(info.value)


def test_raw_input_at_byte_limit_decodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converter = _build_converter(tmp_path)
    manifest = _write_netcdf_raw(converter.object_store)
    sizes = [
        converter.object_store.size(str(entry["local_key"]))
        for entry in manifest["entries"]
    ]
    exact_limit = max(sizes)
    _inject_limit(monkeypatch, "MAX_RAW_INPUT_BYTES", exact_limit)

    result = converter.convert_manifest(manifest)

    assert result.status == "canonical_ready"
    assert exact_limit in sizes


def test_raw_input_over_limit_raises_before_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converter = _build_converter(tmp_path)
    manifest = _write_netcdf_raw(converter.object_store)
    first_key = str(manifest["entries"][0]["local_key"])
    observed = converter.object_store.size(first_key)
    limit = observed - 1
    assert limit >= 0
    _inject_limit(monkeypatch, "MAX_RAW_INPUT_BYTES", limit)
    opened: list[str] = []

    def forbidden_open(filename: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(filename))
        raise AssertionError("decoder must not run for over-limit raw")

    monkeypatch.setattr("xarray.open_dataset", forbidden_open)

    with pytest.raises(CanonicalConversionError) as info:
        converter.convert_manifest(manifest)

    _assert_input_size_error(info.value, key=first_key, observed=observed, limit=limit)
    assert opened == []


def test_raw_growth_after_stat_refuses_overflow_chunk_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converter = _build_converter(tmp_path)
    store = converter.object_store
    manifest = _write_netcdf_raw(store)
    first_key = str(manifest["entries"][0]["local_key"])
    original_size = store.size(first_key)
    _inject_limit(monkeypatch, "MAX_RAW_INPUT_BYTES", original_size)
    real_size = LocalObjectStore.size
    writes: list[int] = []
    staged: list[str] = []
    real_named_temporary_file = tempfile.NamedTemporaryFile
    opened: list[str] = []
    grown_keys: set[str] = set()

    def size_then_grow(self: LocalObjectStore, key_or_uri: str) -> int:
        observed = real_size(self, key_or_uri)
        normalized = self.normalize_key(key_or_uri)
        if normalized == self.normalize_key(first_key) and normalized not in grown_keys:
            grown_keys.add(normalized)
            path = self.resolve_path(key_or_uri)
            with path.open("ab") as handle:
                handle.write(b"X")
        return observed

    class _RecordingStaging:
        def __init__(self, inner: Any) -> None:
            self._inner = inner
            self.name = inner.name
            self._record = _STAGING_PREFIX in Path(inner.name).name

        def write(self, chunk: bytes) -> int:
            if self._record:
                writes.append(len(chunk))
            return self._inner.write(chunk)

        def flush(self) -> None:
            self._inner.flush()

        def __enter__(self) -> Self:
            self._inner.__enter__()
            if self._record:
                staged.append(self.name)
            return self

        def __exit__(self, *args: object) -> object:
            return self._inner.__exit__(*args)

    def recording_named_temporary_file(*args: Any, **kwargs: Any) -> _RecordingStaging:
        return _RecordingStaging(real_named_temporary_file(*args, **kwargs))

    def forbidden_open(filename: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(filename))
        raise AssertionError("decoder must not run after post-stat growth")

    monkeypatch.setattr(LocalObjectStore, "size", size_then_grow)
    monkeypatch.setattr(
        converter_module.tempfile, "NamedTemporaryFile", recording_named_temporary_file
    )
    monkeypatch.setattr("xarray.open_dataset", forbidden_open)

    with pytest.raises(CanonicalConversionError) as info:
        converter.convert_manifest(manifest)

    grown = original_size + 1
    _assert_input_size_error(
        info.value, key=first_key, observed=grown, limit=original_size
    )
    assert writes == []
    assert opened == []
    assert staged
    for path in staged:
        assert _STAGING_PREFIX in Path(path).name
        assert not Path(path).exists()


def test_decoded_raw_values_are_float64_flat_and_survive_dataset_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    converter = _build_converter(tmp_path)
    manifest = _write_netcdf_raw(converter.object_store)
    captured: list[Any] = []
    original_read = converter_module.CanonicalConverter._read_record

    def capturing_read(self: Any, entry: Any) -> Any:
        record = original_read(self, entry)
        captured.append(record)
        return record

    monkeypatch.setattr(
        converter_module.CanonicalConverter, "_read_record", capturing_read
    )

    result = converter.convert_manifest(manifest)

    assert result.status == "canonical_ready"
    assert captured
    tmp2m = next(
        record
        for record in captured
        if record.native_variable == "tmp2m" and record.forecast_hour == 0
    )
    values = tmp2m.values
    expected = default_gfs_value("tmp2m", 0)
    assert isinstance(values, np.ndarray)
    assert values.dtype == np.float64
    assert values.ndim == 1
    assert values.tolist() == [expected]
    assert isinstance(tmp2m.longitudes, tuple)
    assert isinstance(tmp2m.latitudes, tuple)
    assert tmp2m.longitudes == (0.0,)
    assert tmp2m.latitudes == (0.0,)
    np.testing.assert_array_equal(values, np.asarray([expected], dtype=np.float64))


def test_same_key_grib_bundle_entries_return_entry_specific_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    converter = _build_converter(tmp_path)
    cycle_time = converter_module.parse_cycle_time(COMPACT_CYCLE)
    local_key = f"raw/gfs/{COMPACT_CYCLE}/gfs.t00z.pgrb2.0p25.f003.bundle.grib2"
    tmp2m_value = 280.0
    rh2m_value = 50.0
    tmp2m_short_name = GRIB_SHORT_NAMES["tmp2m"]
    rh2m_short_name = GRIB_SHORT_NAMES["rh2m"]
    converter.object_store.write_bytes_atomic(
        local_key,
        _encode_grib2(
            short_name=tmp2m_short_name,
            forecast_hour=3,
            value=tmp2m_value,
            cycle_time=cycle_time,
        )
        + _encode_grib2(
            short_name=rh2m_short_name,
            forecast_hour=3,
            value=rh2m_value,
            cycle_time=cycle_time,
        ),
    )
    bundle = {
        "layout": "per_forecast_hour",
        "variables": ["tmp2m", "rh2m"],
    }
    tmp2m_entry = {
        "local_key": local_key,
        "variable": "tmp2m",
        "forecast_hour": 3,
        "metadata": {
            "bundle": bundle,
            "grib_short_name": tmp2m_short_name,
            "cfgrib_filter_by_keys": {"shortName": tmp2m_short_name},
        },
    }
    rh2m_entry = {
        "local_key": local_key,
        "variable": "rh2m",
        "forecast_hour": 3,
        "metadata": {
            "bundle": bundle,
            "grib_short_name": rh2m_short_name,
            "cfgrib_filter_by_keys": {"shortName": rh2m_short_name},
        },
    }

    with caplog.at_level(logging.WARNING, logger=converter_module.LOGGER.name):
        tmp2m_record = converter._read_record(tmp2m_entry)
        rh2m_record = converter._read_record(rh2m_entry)

    assert FALLBACK_WARNING not in caplog.text
    assert tmp2m_record.native_variable == "tmp2m"
    assert rh2m_record.native_variable == "rh2m"
    np.testing.assert_array_equal(
        tmp2m_record.values,
        np.asarray([tmp2m_value] * GRID_CELLS, dtype=np.float64),
    )
    np.testing.assert_array_equal(
        rh2m_record.values,
        np.asarray([rh2m_value] * GRID_CELLS, dtype=np.float64),
    )
