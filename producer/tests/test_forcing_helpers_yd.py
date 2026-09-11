"""Issue #14 yd-authored helper tests: canonical JSON, grid identity,
descriptor-bound NetCDF, bounded JSON, and direct-grid contract parser."""

from __future__ import annotations

import errno
import inspect
import json
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from yd_producer.forcing.bounded_json import (
    MAX_JSON_BYTES,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    BoundedJSONError,
    load_bounded_json,
)
from yd_producer.forcing.canonical_json import _json_bytes, _json_default
from yd_producer.forcing.direct_grid_contract import (
    MAX_DIRECT_GRID_STATION_BINDINGS,
    REQUIRED_MANIFEST_FIELDS,
    REQUIRED_STATION_FIELDS,
    DirectGridContractError,
    parse_direct_grid_forcing_contract,
)
from yd_producer.forcing.file_store import FileForcingRepository, ForcingStoreError
from yd_producer.forcing.grid_identity import grid_identity_hash, grid_identity_tuples
from yd_producer.forcing.netcdf_open import open_canonical_netcdf
from yd_producer.forcing.producer import (
    ForcingTimeseriesRow,
    _met_stations_from_direct_grid_contract,
    format_shud_forcing_package,
)
from yd_producer.forcing.shud_forcing_contract import (
    CANONICAL_SHUD_FORCING_INDEX_MEMBER,
)
from yd_producer.store.object_store import LocalObjectStore, sha256_bytes

# --- canonical_json ---------------------------------------------------------


def test_json_default_rejects_unsupported_types_and_normalizes_datetime() -> None:
    with pytest.raises(TypeError, match="not JSON serializable"):
        _json_default(object())
    naive = datetime(2026, 5, 7, 0, 0)  # noqa: DTZ001 naive 是被测输入
    aware = datetime(2026, 5, 7, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    assert _json_default(naive) == "2026-05-07T00:00:00Z"
    assert _json_default(aware) == "2026-05-07T00:00:00Z"


def test_json_bytes_matches_exact_json_dumps_literal() -> None:
    payload = {
        "grid_id": "青海",
        "cycle": datetime(2026, 5, 7, 0, 0),  # noqa: DTZ001 naive 是被测输入
        "int": 3,
    }
    expected = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    assert _json_bytes(payload) == expected


def test_canonical_and_file_store_json_bytes_differ_for_unicode() -> None:
    from yd_producer.forcing.file_store import _json_bytes as file_store_json_bytes

    payload = {"grid_id": "青海"}
    canonical = _json_bytes(payload)
    local = file_store_json_bytes(payload)
    assert canonical != local
    assert canonical == b'{"grid_id":"\\u9752\\u6d77"}'
    assert local == '{"grid_id":"青海"}'.encode()
    assert (
        sha256_bytes(canonical)
        == "30c14bf38267349c91d01ee4f171746b3689efc5348f4057f8db78c85d3f4ae5"
    )
    assert (
        sha256_bytes(local)
        == "fa53e5f3a2985c24c40d801c1c7679324914050d4f091fc24d82813cb498cc66"
    )


# --- grid_identity ----------------------------------------------------------


class _GridPoint:
    def __init__(self, cell_id: str, lon: float, lat: float) -> None:
        self.grid_cell_id = cell_id
        self.longitude = lon
        self.latitude = lat


def test_grid_identity_hash_matches_independent_literal() -> None:
    points = (
        _GridPoint("0", -75.0, 40.0),
        _GridPoint("1", -74.5, 40.2),
        _GridPoint("2", -74.0, 40.4),
    )
    assert (
        grid_identity_hash(points)
        == "b56a451cd543e6d23dfce1d486fe5fccdb6e14385b283eceec01d3af30870d4c"
    )


def test_grid_identity_tuples_round_coordinates_to_12_decimals() -> None:
    points = (_GridPoint("0", -75.12345678912345, 40.0),)
    assert grid_identity_tuples(points) == (("0", -75.123456789123, 40.0),)


def test_grid_identity_is_cell_order_sensitive() -> None:
    a = (_GridPoint("0", -75.0, 40.0), _GridPoint("1", -74.5, 40.2))
    b = (_GridPoint("1", -74.5, 40.2), _GridPoint("0", -75.0, 40.0))
    assert grid_identity_hash(a) != grid_identity_hash(b)


# --- bounded_json -----------------------------------------------------------


def test_bounded_json_defaults_are_pinned_literals() -> None:
    assert MAX_JSON_BYTES == 16 * 1024 * 1024
    assert MAX_JSON_DEPTH == 64
    assert MAX_JSON_NODES == 250_000


def test_load_bounded_json_rejects_oversize_bytes() -> None:
    with pytest.raises(BoundedJSONError, match="byte read limit"):
        load_bounded_json(b"x" * 100, max_bytes=10)


def test_load_bounded_json_rejects_deep_nesting() -> None:
    payload = b"[" * 70 + b"1" + b"]" * 70
    with pytest.raises(BoundedJSONError, match="nesting depth"):
        load_bounded_json(payload, max_depth=64)


def test_load_bounded_json_rejects_wide_documents() -> None:
    payload = json.dumps(["x"] * 1000).encode("utf-8")
    with pytest.raises(BoundedJSONError, match="node limit"):
        load_bounded_json(payload, max_nodes=100)


def test_load_bounded_json_rejects_invalid_utf8_and_malformed() -> None:
    with pytest.raises(BoundedJSONError, match="not valid UTF-8"):
        load_bounded_json(b"\xff\xfe")
    with pytest.raises(BoundedJSONError, match="malformed"):
        load_bounded_json(b"{not json")


def test_load_bounded_json_accepts_wellformed() -> None:
    assert load_bounded_json(b'{"a": 1}') == {"a": 1}


# --- Darwin descriptor-memory handoff ---------------------------------------


def _canonical_netcdf_bytes(
    values: tuple[float, float, float] = (1.25, 2.5, 3.75),
    source: str = "gfs",
) -> bytes:
    import tempfile

    import netCDF4

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp).resolve() / "canonical.nc"
        with netCDF4.Dataset(path, "w", format="NETCDF4") as native:
            native.createDimension("cell", 3)
            variable = native.createVariable("value", "f8", ("cell",))
            variable[:] = list(values)
            native.source = source
        return path.read_bytes()


def _force_platform(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    from yd_producer.forcing import netcdf_open

    monkeypatch.setattr(netcdf_open, "sys", SimpleNamespace(platform=platform))


def _capture_admitted_fd(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> None:
    from yd_producer.forcing import netcdf_open

    original_open = netcdf_open.open_file_no_follow

    def capturing_open(path: Path, *, containment_root: Path | None = None) -> int:
        file_fd = original_open(path, containment_root=containment_root)
        captured["fd"] = file_fd
        captured["path"] = path
        return file_fd

    monkeypatch.setattr(netcdf_open, "open_file_no_follow", capturing_open)


def _capture_native(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    import xarray as xr

    real_store = xr.backends.NetCDF4DataStore

    def capturing_store(native: Any, *args: Any, **kwargs: Any) -> Any:
        captured["native"] = native
        return real_store(native, *args, **kwargs)

    monkeypatch.setattr(xr.backends, "NetCDF4DataStore", capturing_store)


def _assert_native_and_fd_closed(captured: dict[str, Any]) -> None:
    assert not captured["native"].isopen()
    with pytest.raises(OSError) as closed:
        os.fstat(captured["fd"])
    assert closed.value.errno == errno.EBADF


def _write_canonical(tmp_path: Path, payload: bytes | None = None) -> tuple[Any, str]:
    store = LocalObjectStore(tmp_path)
    key = "canonical/gfs/2026050700/air_temperature_2m/value.nc"
    content = payload if payload is not None else _canonical_netcdf_bytes()
    store.write_bytes_atomic(key, content)
    return store, key


def test_darwin_alias_ebadf_reads_admitted_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    _force_platform(monkeypatch, "darwin")
    _capture_admitted_fd(monkeypatch, captured)
    _capture_native(monkeypatch, captured)
    real_lstat = os.lstat

    def fault_alias_lstat(
        path: str | os.PathLike[str], *args: Any, **kwargs: Any
    ) -> os.stat_result:
        text = os.fspath(path)
        if text.startswith(("/dev/fd/", "/proc/self/fd/")):
            captured["alias_lstat_called"] = True
            os.fstat(captured["fd"])
            raise OSError(errno.EBADF, "injected alias EBADF")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr("yd_producer.forcing.netcdf_open.os.lstat", fault_alias_lstat)
    store, key = _write_canonical(tmp_path)
    with open_canonical_netcdf(store, key) as dataset:
        assert dataset.value.values.tolist() == [1.25, 2.5, 3.75]
        assert dataset.attrs["source"] == "gfs"
    assert "alias_lstat_called" not in captured
    _assert_native_and_fd_closed(captured)


def test_darwin_post_admission_replacement_keeps_original_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    original = _canonical_netcdf_bytes()
    replacement = _canonical_netcdf_bytes(values=(9.0, 8.0, 7.0), source="ifs")
    _force_platform(monkeypatch, "darwin")
    _capture_native(monkeypatch, captured)
    from yd_producer.forcing import netcdf_open

    original_open = netcdf_open.open_file_no_follow

    def replacing_open(path: Path, *, containment_root: Path | None = None) -> int:
        file_fd = original_open(path, containment_root=containment_root)
        captured["fd"] = file_fd
        path.unlink()
        path.write_bytes(replacement)
        return file_fd

    monkeypatch.setattr(netcdf_open, "open_file_no_follow", replacing_open)
    store, key = _write_canonical(tmp_path, original)
    with open_canonical_netcdf(
        store, key, expected_checksum=sha256_bytes(original)
    ) as dataset:
        assert dataset.value.values.tolist() == [1.25, 2.5, 3.75]
        assert dataset.attrs["source"] == "gfs"
    _assert_native_and_fd_closed(captured)


def test_darwin_observed_growth_without_checksum_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    payload = _canonical_netcdf_bytes()
    _force_platform(monkeypatch, "darwin")
    _capture_admitted_fd(monkeypatch, captured)
    from yd_producer.forcing import netcdf_open

    real_fstat = os.fstat

    def growing_fstat(fd: int) -> os.stat_result:
        result = real_fstat(fd)
        if captured.get("fd") == fd and not captured.get("grown"):
            captured["initial_size"] = result.st_size
            captured["grown"] = True
            with captured["path"].open("ab") as handle:
                handle.write(b"x")
        return result

    monkeypatch.setattr(netcdf_open.os, "fstat", growing_fstat)
    store, key = _write_canonical(tmp_path, payload)
    with (
        pytest.raises(ValueError, match=f"observed more than {len(payload)}") as caught,
        open_canonical_netcdf(store, key, max_bytes=len(payload)),
    ):
        pass
    assert f"size {len(payload)} exceeds" not in str(caught.value)
    assert captured["initial_size"] == len(payload)
    with pytest.raises(OSError) as closed:
        os.fstat(captured["fd"])
    assert closed.value.errno == errno.EBADF


def test_darwin_native_created_setup_failure_closes_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    _force_platform(monkeypatch, "darwin")
    _capture_admitted_fd(monkeypatch, captured)
    import xarray as xr

    def exploding_store(native: Any, *args: Any, **kwargs: Any) -> Any:
        captured["native"] = native
        raise RuntimeError("setup injection")

    monkeypatch.setattr(xr.backends, "NetCDF4DataStore", exploding_store)
    store, key = _write_canonical(tmp_path)
    with (
        pytest.raises(RuntimeError, match="setup injection"),
        open_canonical_netcdf(store, key),
    ):
        pass
    _assert_native_and_fd_closed(captured)


def test_darwin_context_body_failure_closes_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    _force_platform(monkeypatch, "darwin")
    _capture_admitted_fd(monkeypatch, captured)
    _capture_native(monkeypatch, captured)
    store, key = _write_canonical(tmp_path)
    with (
        pytest.raises(RuntimeError, match="body injection"),
        open_canonical_netcdf(store, key) as dataset,
    ):
        assert dataset.value.values.tolist() == [1.25, 2.5, 3.75]
        assert dataset.attrs["source"] == "gfs"
        raise RuntimeError("body injection")
    _assert_native_and_fd_closed(captured)


# --- contract parser --------------------------------------------------------


def _base_manifest() -> dict[str, Any]:
    return {
        "forcing_mapping_mode": "direct_grid",
        "binding_uri": "models/demo/binding.json",
        "binding_checksum": "sha256:abc",
        "model_input_package_id": "model-input-v1",
        "sp_att_path": "input/demo.sp.att",
        "sp_att_checksum": "sha256:def",
        "applicable_source_ids": ["GFS"],
        "grid_id": "gfs_0p25",
        "grid_signature": "sha256:grid-sig",
        "station_bindings": [
            {
                "station_id": "forc_001",
                "shud_forcing_index": 1,
                "forcing_filename": "X1.csv",
                "longitude": 100.0,
                "latitude": 30.0,
                "x": 1,
                "y": 2,
                "z": 3.0,
                "grid_id": "gfs_0p25",
                "grid_cell_id": "0",
            }
        ],
    }


def test_contract_valid_parse_preserves_fields() -> None:
    contract = parse_direct_grid_forcing_contract(_base_manifest(), source_id="GFS")
    assert contract.forcing_mapping_mode == "direct_grid"
    assert contract.grid_id == "gfs_0p25"
    assert contract.applicable_source_ids == ("gfs",)
    assert contract.stations[0].grid_cell_id == "0"
    assert contract.stations[0].shud_forcing_index == 1


@pytest.mark.parametrize("missing_field", REQUIRED_MANIFEST_FIELDS)
def test_contract_missing_manifest_field_raises(missing_field: str) -> None:
    manifest = _base_manifest()
    del manifest[missing_field]
    with pytest.raises(DirectGridContractError):
        parse_direct_grid_forcing_contract(manifest, source_id="GFS")


@pytest.mark.parametrize("missing_field", REQUIRED_STATION_FIELDS)
def test_contract_missing_station_field_raises(missing_field: str) -> None:
    manifest = _base_manifest()
    del manifest["station_bindings"][0][missing_field]
    with pytest.raises(DirectGridContractError):
        parse_direct_grid_forcing_contract(manifest, source_id="GFS")


def test_contract_non_finite_coordinate_rejected() -> None:
    manifest = _base_manifest()
    manifest["station_bindings"][0]["longitude"] = "nan"
    with pytest.raises(DirectGridContractError, match="finite"):
        parse_direct_grid_forcing_contract(manifest, source_id="GFS")


def test_contract_unsafe_filename_rejected() -> None:
    manifest = _base_manifest()
    manifest["station_bindings"][0]["forcing_filename"] = "../evil.csv"
    with pytest.raises(DirectGridContractError, match="unsafe"):
        parse_direct_grid_forcing_contract(manifest, source_id="GFS")


def test_contract_oversized_bindings_rejected() -> None:
    manifest = _base_manifest()
    stations = []
    for index in range(1, MAX_DIRECT_GRID_STATION_BINDINGS + 2):
        stations.append(
            {
                "station_id": f"s{index}",
                "shud_forcing_index": index,
                "forcing_filename": f"s{index}.csv",
                "longitude": 100.0,
                "latitude": 30.0,
                "x": 1,
                "y": 2,
                "z": 3.0,
                "grid_id": "gfs_0p25",
                "grid_cell_id": str(index),
            }
        )
    manifest["station_bindings"] = stations
    with pytest.raises(DirectGridContractError, match="station binding count limit"):
        parse_direct_grid_forcing_contract(manifest, source_id="GFS")


@pytest.mark.parametrize(
    "longitude",
    [
        # Full-precision canonical values must round-trip float-identically:
        # unconditional modulo would drift 112.12345678901234 by 1 ULP.
        112.12345678901234,
        -179.99999999999997,
        -112.12345678901234,
        0.0,
        179.99999999999997,
    ],
)
def test_contract_parser_preserves_canonical_longitude_float_identity(
    longitude: float,
) -> None:
    manifest = _base_manifest()
    manifest["station_bindings"][0]["longitude"] = longitude
    contract = parse_direct_grid_forcing_contract(manifest, source_id="GFS")
    assert contract.stations[0].longitude == longitude
    assert repr(contract.stations[0].longitude) == repr(longitude)


def test_contract_parser_normalizes_negative_zero_longitude_to_positive() -> None:
    manifest = _base_manifest()
    manifest["station_bindings"][0]["longitude"] = -0.0
    contract = parse_direct_grid_forcing_contract(manifest, source_id="GFS")
    assert contract.stations[0].longitude == 0.0
    assert repr(contract.stations[0].longitude) == "0.0"


@pytest.mark.parametrize(
    "longitude, expected",
    [
        # Legacy [180, 360] input keeps subtract-360 normalization.
        (180.0, -180.0),
        (200.5, -159.5),
        (292.12345678901234, -67.87654321098768),
        (360.0, 0.0),
    ],
)
def test_contract_parser_normalizes_legacy_longitude_by_subtracting_360(
    longitude: float, expected: float
) -> None:
    manifest = _base_manifest()
    manifest["station_bindings"][0]["longitude"] = longitude
    contract = parse_direct_grid_forcing_contract(manifest, source_id="GFS")
    assert contract.stations[0].longitude == expected
    assert repr(contract.stations[0].longitude) == repr(expected)


def test_contract_rejects_duplicate_grid_cell_id_with_stable_discriminator() -> None:
    manifest = _base_manifest()
    duplicate = dict(manifest["station_bindings"][0])
    duplicate.update(
        {
            "station_id": "forc_002",
            "shud_forcing_index": 2,
            "forcing_filename": "X2.csv",
        }
    )
    manifest["station_bindings"].append(duplicate)

    with pytest.raises(DirectGridContractError) as exc_info:
        parse_direct_grid_forcing_contract(manifest, source_id="GFS")

    error = exc_info.value
    assert error.field == "grid_cell_id"
    assert error.station_id == "forc_002"
    assert error.to_dict()["error_code"] == "DIRECT_GRID_CONTRACT_INVALID"
    assert error.to_dict()["duplicate_grid_cell_id"] == "0"


# --- store error surface ----------------------------------------------------


def test_forcing_store_error_is_stable_public_type() -> None:
    error = ForcingStoreError("boom")
    assert isinstance(error, RuntimeError)
    assert not isinstance(error, ConnectionError)


def test_file_repository_has_no_independent_asset_byte_limit() -> None:
    signature = inspect.signature(FileForcingRepository)
    assert "max_asset_bytes" not in signature.parameters
    assert (
        "max_bytes"
        in inspect.signature(
            FileForcingRepository.load_direct_grid_validation_assets
        ).parameters
    )


# --- station-index geometry precision ---------------------------------------


def test_format_shud_forcing_package_preserves_full_precision_station_geometry() -> (
    None
):
    manifest = _base_manifest()
    first = dict(manifest["station_bindings"][0])
    first.update(
        longitude=116.1234567890123,
        latitude=39.87654321098765,
        x=1540123.4567890123,
        y=4123456.789012345,
        z=3375.123456789012,
    )
    second = {
        "station_id": "forc_002",
        "shud_forcing_index": 2,
        "forcing_filename": "X2.csv",
        "longitude": 100.0,
        "latitude": 30.0,
        "x": 1.0,
        "y": 2.0,
        "z": -12.345678901234,
        "grid_id": first["grid_id"],
        "grid_cell_id": "1",
    }
    manifest["station_bindings"] = [first, second]
    contract = parse_direct_grid_forcing_contract(manifest, source_id="GFS")
    stations = _met_stations_from_direct_grid_contract(
        contract, basin_version_id="basin_v1"
    )
    cycle_time = datetime(2026, 5, 7, tzinfo=UTC)
    rows = tuple(
        ForcingTimeseriesRow(
            forcing_version_id="forcing_v1",
            basin_version_id="basin_v1",
            station_id=station.station_id,
            valid_time=cycle_time,
            source_id="gfs",
            variable="PRCP",
            value=116.1234567890123,
            unit="mm/day",
            native_resolution=None,
        )
        for station in stations
    )
    files = format_shud_forcing_package(rows, stations=stations, cycle_time=cycle_time)
    expected_index = (
        b"2 20260507\n"
        b"shud\n"
        b"ID\tLon\tLat\tX\tY\tZ\tFilename\n"
        b"1\t116.1234567890123\t39.87654321098765\t1540123.4567890123"
        b"\t4123456.789012345\t3375.123456789012\tX1.csv\n"
        b"2\t100.0\t30.0\t1.0\t2.0\t-12.345678901234\tX2.csv\n"
    )
    expected_csv = (
        b"1\t6\t20260507\t20260507\n"
        b"Time_Day\tPrecip\tTemp\tRH\tWind\tRN\n"
        b"0\t116.1234568\t0\t0\t0\t0\n"
    )
    index_text = files[CANONICAL_SHUD_FORCING_INDEX_MEMBER]
    assert index_text.encode("utf-8") == expected_index
    for line, binding in zip(
        index_text.splitlines()[3:], contract.stations, strict=True
    ):
        _index, lon, lat, x, y, z, _filename = line.split("\t")
        assert float(lon) == binding.longitude
        assert float(lat) == binding.latitude
        assert float(x) == binding.x
        assert float(y) == binding.y
        assert float(z) == binding.z
    assert files["shud/X1.csv"].encode("utf-8") == expected_csv
    assert files["shud/X2.csv"].encode("utf-8") == expected_csv
