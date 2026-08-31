"""Issue #14 yd-authored helper tests: canonical JSON, grid identity,
descriptor alias, bounded JSON, and direct-grid contract parser."""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
from yd_producer.forcing.netcdf_open import descriptor_alias_path
from yd_producer.store.object_store import sha256_bytes

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


# --- descriptor alias -------------------------------------------------------


def test_descriptor_alias_path_prefers_linux_then_darwin(tmp_path: Path) -> None:
    import os

    fd = os.open(tmp_path / "x", os.O_CREAT | os.O_RDWR)
    try:
        alias = descriptor_alias_path(fd)
        assert str(alias).startswith("/proc/self/fd/") or str(alias).startswith(
            "/dev/fd/"
        )
    finally:
        os.close(fd)


def test_descriptor_alias_path_raises_when_no_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    monkeypatch.setattr(
        "yd_producer.forcing.netcdf_open.os.lstat",
        lambda p: (_ for _ in ()).throw(OSError()),
    )
    fd = os.open(tmp_path / "x", os.O_CREAT | os.O_RDWR)
    try:
        with pytest.raises(OSError, match="descriptor alias is unavailable"):
            descriptor_alias_path(fd)
    finally:
        os.close(fd)


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
