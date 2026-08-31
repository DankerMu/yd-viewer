"""Round-1 public regression seams for direct-grid forcing invariants.

Each expected value is a literal fixture value or a hand-worked product shape;
the tests do not derive expectations through the production parser or path
builder.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from forcing_fixtures import (
    CYCLE_00Z,
    CYCLE_12Z,
    DEFAULT_GRID_SIGNATURE,
    GFS_GRID_ID,
    IFS_GRID_ID,
    canonical_products_for_cycle,
    direct_grid_binding,
    make_file_producer,
)

from yd_producer.forcing import (
    DirectGridContractError,
    ForcingProducer,
    ForcingProductionError,
    parse_direct_grid_forcing_contract,
)
from yd_producer.forcing.file_store import FileForcingRepository, ForcingStoreError
from yd_producer.forcing.netcdf_open import _checksum_descriptor, open_canonical_netcdf
from yd_producer.store.object_store import LocalObjectStore, sha256_bytes

_CATALOG_KEY = "canonical/gfs/2026050700/_catalog/catalog.json"
_GFS_PACKAGE_KEY = "forcing/gfs/2026050700/basin_v1/demo_model"
_IFS_PACKAGE_KEY = "forcing/ifs/2026050712/basin_v1/demo_model"


def _prepared_gfs(
    tmp_path: Path,
) -> tuple[ForcingProducer, FileForcingRepository, LocalObjectStore]:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding()
    producer, repository = make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )
    return producer, repository, store


def _produce_gfs(producer: ForcingProducer) -> Any:
    return producer.produce(
        source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model"
    )


def _catalog(store: LocalObjectStore) -> dict[str, Any]:
    return json.loads(store.read_bytes(_CATALOG_KEY).decode("utf-8"))


def _write_catalog(store: LocalObjectStore, payload: dict[str, Any]) -> None:
    store.write_bytes_atomic(
        _CATALOG_KEY,
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )


def _assert_no_gfs_ready(
    repository: FileForcingRepository, store: LocalObjectStore
) -> None:
    record = repository.get_forcing_version(
        source_id="gfs",
        cycle_time=datetime(2026, 5, 7, 0, tzinfo=UTC),
        model_id="demo_model",
    )
    assert record is None or not str(record.get("checksum") or "").strip()
    assert not store.exists(f"{_GFS_PACKAGE_KEY}/forcing_version_record.json")
    assert not store.exists(f"{_GFS_PACKAGE_KEY}/forcing_package.json")
    assert not store.exists(
        "runs/fcst_gfs_2026050700_demo_model/input/forcing_domain_handoff.json"
    )


@pytest.mark.parametrize(
    "invalid_cycle",
    (
        "2026050706",
        "2026-05-07T12:30:00Z",
        "2026-05-07T12:00:01Z",
        "2026-05-07T12:00:00.000001Z",
    ),
    ids=("06z", "minute", "second", "microsecond"),
)
def test_invalid_cycle_cannot_change_existing_12z_ready(
    tmp_path: Path, invalid_cycle: str
) -> None:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding()
    producer, repository = make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_12Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )
    ready = producer.produce(
        source_id="gfs", cycle_time=CYCLE_12Z, model_id="demo_model"
    )
    assert ready.status == "forcing_ready"

    package_key = _IFS_PACKAGE_KEY.replace("ifs", "gfs")
    handoff_key = (
        "runs/fcst_gfs_2026050712_demo_model/input/forcing_domain_handoff.json"
    )
    evidence = {
        key: store.read_bytes(key)
        for key in (
            f"{package_key}/forcing_version_record.json",
            f"{package_key}/forcing_domain_package.json",
            f"{package_key}/forcing_package.json",
            handoff_key,
        )
    }

    with pytest.raises(
        ForcingProductionError, match="Invalid forcing production request"
    ):
        producer.produce(
            source_id="gfs", cycle_time=invalid_cycle, model_id="demo_model"
        )

    assert {key: store.read_bytes(key) for key in evidence} == evidence
    assert (
        repository.get_forcing_version(
            source_id="gfs",
            cycle_time=datetime(2026, 5, 7, 12, tzinfo=UTC),
            model_id="demo_model",
        )["checksum"]
        == ready.checksum
    )


def test_parser_requires_current_source_singleton_but_keeps_pin_compatibility() -> None:
    manifest = direct_grid_binding(applicable_source_ids=("GFS", "IFS"))

    pin_compatible = parse_direct_grid_forcing_contract(manifest)
    assert pin_compatible.applicable_source_ids == ("gfs", "ifs")
    for source_id in ("gfs", "ifs"):
        with pytest.raises(DirectGridContractError, match="exclusively"):
            parse_direct_grid_forcing_contract(manifest, source_id=source_id)
    with pytest.raises(DirectGridContractError, match="exclusively"):
        parse_direct_grid_forcing_contract(
            direct_grid_binding(applicable_source_ids=("IFS",)), source_id="gfs"
        )


def test_supported_but_nonapplicable_binding_has_no_ready_output(
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding(applicable_source_ids=("IFS",))
    producer, repository = make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )

    with pytest.raises(
        ForcingProductionError, match="Invalid forcing mapping contract"
    ):
        _produce_gfs(producer)

    _assert_no_gfs_ready(repository, store)


@pytest.mark.parametrize(
    "foreign_key",
    (
        "canonical/ifs/2026050700/air_temperature_2m/foreign.nc",
        "canonical/gfs/2026050712/air_temperature_2m/foreign.nc",
        "canonical/gfs/2026050700/wind_u_10m/foreign.nc",
    ),
    ids=("source", "cycle", "variable"),
)
def test_checksum_consistent_foreign_object_key_is_rejected(
    tmp_path: Path, foreign_key: str
) -> None:
    producer, repository, store = _prepared_gfs(tmp_path)
    payload = _catalog(store)
    row = payload["products"][0]
    content = store.read_bytes(row["object_uri"])
    store.write_bytes_atomic(foreign_key, content)
    row["object_uri"] = foreign_key
    row["checksum"] = hashlib.sha256(content).hexdigest()
    _write_catalog(store, payload)

    with pytest.raises(ForcingStoreError, match="canonical object identity"):
        repository.list_canonical_products(
            source_id="gfs",
            cycle_time=datetime(2026, 5, 7, 0, tzinfo=UTC),
        )
    with pytest.raises(ForcingProductionError, match="canonical object identity"):
        _produce_gfs(producer)
    _assert_no_gfs_ready(repository, store)


def test_catalog_accepts_same_store_s3_uri(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path, object_store_prefix="s3://nhms/work")
    seed = direct_grid_binding()
    producer, _ = make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )

    result = _produce_gfs(producer)
    assert result.status == "forcing_ready"


def test_exact_catalog_variable_must_exist_in_singleton_dataset(tmp_path: Path) -> None:
    from test_forcing_producer import _netcdf_bytes

    producer, repository, store = _prepared_gfs(tmp_path)
    payload = _catalog(store)
    row_index = next(
        index
        for index, row in enumerate(payload["products"])
        if row["variable"] == "air_temperature_2m"
    )
    row = payload["products"][row_index]
    content = _netcdf_bytes(
        "wrong_singleton",
        values=(10.0, 20.0, 999.0),
        attrs={
            "cycle_time": "2026-05-07T00:00:00+00:00",
            "valid_time": "2026-05-07T00:00:00+00:00",
            "lead_time_hours": 0,
            "unit": row["unit"],
            "grid_id": row["grid_id"],
        },
    )
    store.write_bytes_atomic(row["object_uri"], content)
    row["checksum"] = sha256_bytes(content)
    _write_catalog(store, payload)

    with pytest.raises(ForcingProductionError, match="no matching variable"):
        _produce_gfs(producer)
    _assert_no_gfs_ready(repository, store)


@pytest.mark.parametrize(
    ("attribute", "replacement"),
    (
        ("cycle_time", "2026-05-07T12:00:00+00:00"),
        ("valid_time", "2026-05-07T03:00:00+00:00"),
        ("lead_time_hours", 3),
        ("unit", "K"),
        ("grid_id", "other_grid"),
    ),
    ids=("cycle", "valid", "lead", "unit", "grid"),
)
def test_canonical_writer_attribute_mismatch_has_no_ready_output(
    tmp_path: Path, attribute: str, replacement: Any
) -> None:
    from test_forcing_producer import _netcdf_bytes

    producer, repository, store = _prepared_gfs(tmp_path)
    payload = _catalog(store)
    row = payload["products"][0]
    attrs = {
        "cycle_time": "2026-05-07T00:00:00+00:00",
        "valid_time": "2026-05-07T00:00:00+00:00",
        "lead_time_hours": 0,
        "unit": row["unit"],
        "grid_id": row["grid_id"],
    }
    attrs[attribute] = replacement
    content = _netcdf_bytes(row["variable"], values=(1.0, 2.0, 999.0), attrs=attrs)
    store.write_bytes_atomic(row["object_uri"], content)
    row["checksum"] = sha256_bytes(content)
    _write_catalog(store, payload)

    with pytest.raises(ForcingProductionError, match="NetCDF identity mismatch"):
        _produce_gfs(producer)
    _assert_no_gfs_ready(repository, store)


@pytest.mark.parametrize(
    ("attribute", "replacement", "error_match"),
    (
        ("cycle_time", "not-a-cycle", "malformed NetCDF time attributes"),
        (
            "lead_time_hours",
            "not-an-integral-hour",
            "non-integral NetCDF lead_time_hours",
        ),
    ),
    ids=("time", "lead"),
)
def test_malformed_canonical_writer_attribute_has_no_ready_output(
    tmp_path: Path, attribute: str, replacement: Any, error_match: str
) -> None:
    from test_forcing_producer import _netcdf_bytes

    producer, repository, store = _prepared_gfs(tmp_path)
    payload = _catalog(store)
    row = payload["products"][0]
    attrs = {
        "cycle_time": "2026-05-07T00:00:00+00:00",
        "valid_time": "2026-05-07T00:00:00+00:00",
        "lead_time_hours": 0,
        "unit": row["unit"],
        "grid_id": row["grid_id"],
    }
    attrs[attribute] = replacement
    content = _netcdf_bytes(row["variable"], values=(1.0, 2.0, 999.0), attrs=attrs)
    store.write_bytes_atomic(row["object_uri"], content)
    row["checksum"] = sha256_bytes(content)
    _write_catalog(store, payload)

    with pytest.raises(ForcingProductionError, match=error_match):
        _produce_gfs(producer)
    _assert_no_gfs_ready(repository, store)


def test_missing_canonical_writer_attribute_has_no_ready_output(tmp_path: Path) -> None:
    import xarray as xr

    producer, repository, store = _prepared_gfs(tmp_path)
    payload = _catalog(store)
    row = payload["products"][0]
    source_path = store.resolve_path(row["object_uri"])
    dataset = xr.open_dataset(source_path).load()
    try:
        del dataset.attrs["grid_id"]
        replacement_path = tmp_path / "missing-grid-id.nc"
        dataset.to_netcdf(replacement_path, engine="netcdf4", format="NETCDF4")
        content = replacement_path.read_bytes()
    finally:
        dataset.close()
    store.write_bytes_atomic(row["object_uri"], content)
    row["checksum"] = sha256_bytes(content)
    _write_catalog(store, payload)

    with pytest.raises(ForcingProductionError, match="missing NetCDF attributes"):
        _produce_gfs(producer)
    _assert_no_gfs_ready(repository, store)


def test_ifs_fixture_uses_literal_12z_and_exact_uppercase_grid_uri(
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding(
        grid_id=IFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
        applicable_source_ids=("IFS",),
    )
    producer, _ = make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="ifs",
        cycle_text=CYCLE_12Z,
        grid_id=IFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )

    result = producer.produce(
        source_id="ifs", cycle_time=CYCLE_12Z, model_id="demo_model"
    )
    manifest = json.loads(
        store.read_bytes(f"{_IFS_PACKAGE_KEY}/forcing_package.json").decode("utf-8")
    )
    catalog = json.loads(
        store.read_bytes("canonical/ifs/2026050712/_catalog/catalog.json").decode(
            "utf-8"
        )
    )
    assert result.forcing_package_uri == f"{_IFS_PACKAGE_KEY}/"
    assert manifest["cycle_time"] == "2026-05-07T12:00:00Z"
    assert {row["grid_definition_uri"] for row in catalog["products"]} == {
        "canonical/IFS/grid/ifs_0p25/grid.json"
    }


def test_sparse_canonical_object_over_512_mib_fails_before_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer, repository, store = _prepared_gfs(tmp_path)
    product = next(
        item
        for item in repository.list_canonical_products(
            source_id="gfs", cycle_time=datetime(2026, 5, 7, 0, tzinfo=UTC)
        )
        if item.variable == "prcp_rate_or_amount"
    )
    with store.resolve_path(product.object_uri).open("r+b") as handle:
        handle.truncate(536_870_913)

    from yd_producer.forcing import netcdf_open

    def checksum_must_not_run(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("oversize canonical object reached checksum")

    monkeypatch.setattr(netcdf_open, "_checksum_descriptor", checksum_must_not_run)
    with pytest.raises(ForcingProductionError, match="size .* exceeds 536870912"):
        _produce_gfs(producer)
    _assert_no_gfs_ready(repository, store)


def test_low_limit_checksum_guard_rewinds_descriptor(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    key = "canonical/gfs/2026050700/air_temperature_2m/limit.nc"
    store.write_bytes_atomic(key, b"abcdef")
    file_fd = os.open(store.resolve_path(key), os.O_RDONLY)
    try:
        with pytest.raises(ValueError, match="observed more than 5"):
            _checksum_descriptor(file_fd, max_bytes=5)
        assert os.read(file_fd, 1) == b"a"
    finally:
        os.close(file_fd)


def test_low_limit_fstat_guard_rejects_before_xarray(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    key = "canonical/gfs/2026050700/air_temperature_2m/limit.nc"
    store.write_bytes_atomic(key, b"abcdef")

    with (
        pytest.raises(ValueError, match="size 6 exceeds 5"),
        open_canonical_netcdf(store, key, max_bytes=5),
    ):
        pass


@pytest.mark.parametrize("invalid_limit", (-1, True, 1.5, "5"))
def test_netcdf_limit_requires_nonnegative_integer(
    tmp_path: Path, invalid_limit: Any
) -> None:
    store = LocalObjectStore(tmp_path)
    key = "canonical/gfs/2026050700/air_temperature_2m/limit.nc"
    store.write_bytes_atomic(key, b"x")

    with (
        pytest.raises(ValueError, match="non-negative integer"),
        open_canonical_netcdf(store, key, max_bytes=invalid_limit),
    ):
        pass


def test_config_drift_rebuilds_ifs_output_and_persists_identical_lineage(
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding(
        grid_id=IFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
        applicable_source_ids=("IFS",),
    )
    first, repository = make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="ifs",
        cycle_text=CYCLE_12Z,
        grid_id=IFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )
    first_result = first.produce(
        source_id="ifs", cycle_time=CYCLE_12Z, model_id="demo_model"
    )
    assert first_result.status == "forcing_ready"

    changed = ForcingProducer(
        config=replace(first.config, rn_shortwave_factor=0.5),
        repository=repository,
        object_store=store,
    )
    rebuilt = changed.produce(
        source_id="ifs", cycle_time=CYCLE_12Z, model_id="demo_model"
    )
    assert rebuilt.status == "forcing_ready"
    assert (
        changed.produce(
            source_id="ifs", cycle_time=CYCLE_12Z, model_id="demo_model"
        ).status
        == "already_done"
    )

    package_key = f"{_IFS_PACKAGE_KEY}/forcing_package.json"
    record_key = f"{_IFS_PACKAGE_KEY}/forcing_version_record.json"
    package = json.loads(store.read_bytes(package_key).decode("utf-8"))
    record = json.loads(store.read_bytes(record_key).decode("utf-8"))
    record_identity = record["lineage_json"]["output_config_identity"]
    manifest_identity = package["lineage"]["output_config_identity"]
    assert record_identity == manifest_identity
    assert record_identity["schema_version"] == "nhms.forcing_output_config_identity.v1"
    assert record_identity["payload"] == {
        "rn_shortwave_factor": 0.5,
        "forcing_filename": "forcing.tsd.forc",
        "csv_filename": "forcing_debug.csv",
        "package_manifest_filename": "forcing_package.json",
        "output_variables": ["PRCP", "TEMP", "RH", "wind", "Rn", "Press"],
        "required_canonical_variables": [
            "prcp_rate_or_amount",
            "air_temperature_2m",
            "relative_humidity_2m",
            "wind_u_10m",
            "wind_v_10m",
            "pressure_surface",
            "shortwave_down",
        ],
        "era5_latency_fallback_hours": 23,
        "min_lead_hours": None,
    }
    assert "\t50" in store.read_bytes(f"{_IFS_PACKAGE_KEY}/shud/X1.csv").decode("utf-8")


@pytest.mark.parametrize(
    ("field", "changed_value"),
    (
        ("forcing_filename", "renamed.tsd.forc"),
        (
            "output_variables",
            ("Precip", "Temp", "RH", "Wind", "Press"),
        ),
    ),
    ids=("filename", "variable-policy"),
)
def test_output_config_identity_changes_for_output_semantic_siblings(
    tmp_path: Path, field: str, changed_value: Any
) -> None:
    from yd_producer.forcing.producer import _output_config_identity

    producer, _, _ = _prepared_gfs(tmp_path)
    baseline = _output_config_identity(producer.config)
    changed = _output_config_identity(
        replace(producer.config, **{field: changed_value})
    )

    assert baseline["checksum"] != changed["checksum"]
    assert baseline["payload"][field] != changed["payload"][field]
    assert _output_config_identity(producer.config) == baseline


def test_checksum_consistent_non_utf8_sp_att_has_no_ready_output(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_gfs(tmp_path)
    bad_content = b"2 1\nTRI\tA\xff\tB\tC\tFORC\n1\t0\t0\t0\t1\n2\t0\t0\t0\t2\n"
    key = "models/demo/package/input/demo.sp.att"
    store.write_bytes_atomic(key, bad_content)
    registry = json.loads(store.read_bytes("models/demo/registry.json").decode("utf-8"))
    binding = registry["models"][0]["resource_profile"]["direct_grid_forcing"]
    binding["sp_att_checksum"] = sha256_bytes(bad_content)
    store.write_bytes_atomic(
        "models/demo/registry.json", json.dumps(registry).encode("utf-8")
    )

    with pytest.raises(ForcingProductionError, match="not UTF-8 text"):
        _produce_gfs(producer)
    _assert_no_gfs_ready(repository, store)


def test_public_station_cap_rejects_before_mapping_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer, repository, _ = _prepared_gfs(tmp_path)
    producer = ForcingProducer(
        config=replace(producer.config, max_station_count=1),
        repository=repository,
        object_store=producer.object_store,
    )

    def mapping_write_must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("station cap reached mapping write")

    monkeypatch.setattr(
        producer, "_ensure_direct_grid_met_stations", mapping_write_must_not_run
    )
    with pytest.raises(
        ForcingProductionError,
        match="Forcing station_count 2 exceeds configured limit 1",
    ):
        _produce_gfs(producer)
    _assert_no_gfs_ready(repository, producer.object_store)


@pytest.mark.parametrize(
    ("config_field", "forecast_hours", "expected_message"),
    (
        (
            "max_timestep_count",
            (0, 3),
            "Forcing timestep_count 2 exceeds configured limit 1",
        ),
        (
            "max_timeseries_row_count",
            (0,),
            "Forcing timeseries_row_count 12 exceeds configured limit 1",
        ),
    ),
    ids=("timestep", "row"),
)
def test_public_timeseries_caps_reject_before_mapping_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_field: str,
    forecast_hours: tuple[int, ...],
    expected_message: str,
) -> None:
    producer, repository, store = _prepared_gfs(tmp_path)
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
        forecast_hours=forecast_hours,
    )
    producer = ForcingProducer(
        config=replace(producer.config, **{config_field: 1}),
        repository=repository,
        object_store=store,
    )

    def mapping_write_must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("timeseries cap reached mapping write")

    monkeypatch.setattr(
        producer, "_ensure_direct_grid_met_stations", mapping_write_must_not_run
    )
    with pytest.raises(ForcingProductionError, match=expected_message):
        _produce_gfs(producer)
    _assert_no_gfs_ready(repository, store)


def test_ifs_uses_literal_catalog_grid_uri_without_lowercase_fallback(
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding(
        grid_id=IFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
        applicable_source_ids=("IFS",),
    )
    producer, _ = make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="ifs",
        cycle_text=CYCLE_12Z,
        grid_id=IFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )

    catalog = json.loads(
        store.read_bytes("canonical/ifs/2026050712/_catalog/catalog.json").decode(
            "utf-8"
        )
    )
    assert {row["grid_definition_uri"] for row in catalog["products"]} == {
        "canonical/IFS/grid/ifs_0p25/grid.json"
    }
    assert (
        producer.produce(
            source_id="ifs", cycle_time=CYCLE_12Z, model_id="demo_model"
        ).status
        == "forcing_ready"
    )


def test_ifs_grid_read_receives_exact_uppercase_catalog_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yd_producer.store import object_store as object_store_module

    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding(
        grid_id=IFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
        applicable_source_ids=("IFS",),
    )
    producer, _ = make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="ifs",
        cycle_text=CYCLE_12Z,
        grid_id=IFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )
    observed_uris: list[str] = []
    original_read = object_store_module.LocalObjectStore.read_bytes_limited

    def record_grid_uri(
        object_store: LocalObjectStore, key_or_uri: str, *, max_bytes: int
    ) -> bytes:
        if key_or_uri.casefold().endswith("/ifs_0p25/grid.json"):
            observed_uris.append(key_or_uri)
        return original_read(object_store, key_or_uri, max_bytes=max_bytes)

    monkeypatch.setattr(
        object_store_module.LocalObjectStore, "read_bytes_limited", record_grid_uri
    )
    assert (
        producer.produce(
            source_id="ifs", cycle_time=CYCLE_12Z, model_id="demo_model"
        ).status
        == "forcing_ready"
    )
    assert observed_uris
    assert set(observed_uris) == {"canonical/IFS/grid/ifs_0p25/grid.json"}


# Phase 6.2 depth retry ---------------------------------------------------------


_F003_IDENTITIES: dict[str, tuple[str, str]] = {
    "prcp_rate_or_amount": (
        "gfs_2026050700_prcp_rate_or_amount_f003",
        (
            "canonical/gfs/2026050700/prcp_rate_or_amount/"
            "gfs_2026050700_prcp_rate_or_amount_f003.nc"
        ),
    ),
    "air_temperature_2m": (
        "gfs_2026050700_air_temperature_2m_f003",
        (
            "canonical/gfs/2026050700/air_temperature_2m/"
            "gfs_2026050700_air_temperature_2m_f003.nc"
        ),
    ),
    "relative_humidity_2m": (
        "gfs_2026050700_relative_humidity_2m_f003",
        (
            "canonical/gfs/2026050700/relative_humidity_2m/"
            "gfs_2026050700_relative_humidity_2m_f003.nc"
        ),
    ),
    "wind_u_10m": (
        "gfs_2026050700_wind_u_10m_f003",
        "canonical/gfs/2026050700/wind_u_10m/gfs_2026050700_wind_u_10m_f003.nc",
    ),
    "wind_v_10m": (
        "gfs_2026050700_wind_v_10m_f003",
        "canonical/gfs/2026050700/wind_v_10m/gfs_2026050700_wind_v_10m_f003.nc",
    ),
    "pressure_surface": (
        "gfs_2026050700_pressure_surface_f003",
        (
            "canonical/gfs/2026050700/pressure_surface/"
            "gfs_2026050700_pressure_surface_f003.nc"
        ),
    ),
    "shortwave_down": (
        "gfs_2026050700_shortwave_down_f003",
        (
            "canonical/gfs/2026050700/shortwave_down/"
            "gfs_2026050700_shortwave_down_f003.nc"
        ),
    ),
}


class _ProtocolSourceLessMultiSourceRepository:
    """Repository boundary fake that returns a source-less parsed pin contract."""

    def __init__(self, backing: FileForcingRepository, contract: Any) -> None:
        self._backing = backing
        self._contract = contract
        self.mapping_write_attempted = False

    def load_forcing_mapping_contract(
        self,
        *,
        model_id: str,
        basin_version_id: str,
        source_id: str | None = None,
    ) -> Any:
        return self._contract

    def upsert_interp_weights(self, weights: Any) -> None:
        self.mapping_write_attempted = True
        raise AssertionError("source-less multi-source contract reached mapping write")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backing, name)


def _rewrite_netcdf_identity_attrs(
    store: LocalObjectStore,
    *,
    source_uri: str,
    destination_uri: str,
    temporary_path: Path,
    cycle_time: str,
    valid_time: str,
    lead_time_hours: int,
) -> str:
    import xarray as xr

    dataset = xr.open_dataset(store.resolve_path(source_uri)).load()
    try:
        dataset.attrs["cycle_time"] = cycle_time
        dataset.attrs["valid_time"] = valid_time
        dataset.attrs["lead_time_hours"] = lead_time_hours
        dataset.to_netcdf(temporary_path, engine="netcdf4", format="NETCDF4")
        content = temporary_path.read_bytes()
    finally:
        dataset.close()
        temporary_path.unlink(missing_ok=True)
    store.write_bytes_atomic(destination_uri, content)
    return sha256_bytes(content)


def _prepared_gfs_f003_catalog(
    tmp_path: Path,
) -> tuple[ForcingProducer, FileForcingRepository, LocalObjectStore]:
    store = LocalObjectStore(tmp_path)
    producer, repository = make_file_producer(
        tmp_path,
        seed=direct_grid_binding(),
        store=store,
    )
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
        forecast_hours=(3,),
    )
    return (
        ForcingProducer(
            config=replace(producer.config, min_lead_hours=3),
            repository=repository,
            object_store=store,
        ),
        repository,
        store,
    )


def _forge_catalog_time_lead_incoherence(
    tmp_path: Path, store: LocalObjectStore
) -> None:
    payload = _catalog(store)
    for row in payload["products"]:
        expected_product_id, expected_object_uri = _F003_IDENTITIES[row["variable"]]
        assert row["canonical_product_id"] == expected_product_id
        assert row["object_uri"] == expected_object_uri
        row["valid_time"] = "2026-05-07T00:00:00Z"
        assert row["lead_time_hours"] == 3
        row["checksum"] = _rewrite_netcdf_identity_attrs(
            store,
            source_uri=expected_object_uri,
            destination_uri=expected_object_uri,
            temporary_path=tmp_path / f"forged-time-lead-{row['variable']}.nc",
            cycle_time="2026-05-07T00:00:00+00:00",
            valid_time="2026-05-07T00:00:00+00:00",
            lead_time_hours=3,
        )
    _write_catalog(store, payload)


def _forge_catalog_product_id_incoherence(
    tmp_path: Path, store: LocalObjectStore
) -> None:
    payload = _catalog(store)
    for row in payload["products"]:
        source_uri = row["object_uri"]
        expected_product_id, expected_object_uri = _F003_IDENTITIES[row["variable"]]
        assert row["canonical_product_id"].endswith("_f000")
        assert row["lead_time_hours"] == 0
        assert row["valid_time"] == "2026-05-07T00:00:00Z"
        row["canonical_product_id"] = expected_product_id
        row["object_uri"] = expected_object_uri
        row["checksum"] = _rewrite_netcdf_identity_attrs(
            store,
            source_uri=source_uri,
            destination_uri=expected_object_uri,
            temporary_path=tmp_path / f"forged-product-id-{row['variable']}.nc",
            cycle_time="2026-05-07T00:00:00+00:00",
            valid_time="2026-05-07T00:00:00+00:00",
            lead_time_hours=0,
        )
        assert row["canonical_product_id"] == expected_product_id
        assert row["object_uri"] == expected_object_uri
    _write_catalog(store, payload)


def test_public_produce_rejects_source_less_multisource_contract_before_mapping_write(
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding(applicable_source_ids=("GFS", "IFS"))
    base_producer, backing = make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )
    contract = parse_direct_grid_forcing_contract(seed)
    repository = _ProtocolSourceLessMultiSourceRepository(backing, contract)
    producer = ForcingProducer(
        config=base_producer.config,
        repository=repository,
        object_store=store,
    )

    with pytest.raises(
        ForcingProductionError,
        match="Invalid forcing mapping contract: Direct-grid contract must apply exclusively",
    ):
        _produce_gfs(producer)

    assert repository.mapping_write_attempted is False
    _assert_no_gfs_ready(backing, store)


def test_catalog_rejects_dual_forged_time_lead_before_netcdf_read_or_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer, repository, store = _prepared_gfs_f003_catalog(tmp_path)
    _forge_catalog_time_lead_incoherence(tmp_path, store)

    def netcdf_read_must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("time/lead-incoherent catalog row reached NetCDF read")

    monkeypatch.setattr(
        "yd_producer.forcing.netcdf_open.open_canonical_netcdf",
        netcdf_read_must_not_run,
    )
    with pytest.raises(
        ForcingStoreError,
        match="incoherent valid_time/cycle_time/lead_time_hours",
    ):
        repository.list_canonical_products(
            source_id="gfs",
            cycle_time=datetime(2026, 5, 7, 0, tzinfo=UTC),
        )
    with pytest.raises(
        ForcingProductionError,
        match="incoherent valid_time/cycle_time/lead_time_hours",
    ):
        _produce_gfs(producer)

    _assert_no_gfs_ready(repository, store)


def test_catalog_rejects_dual_forged_product_id_before_netcdf_read_or_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer, repository, store = _prepared_gfs(tmp_path)
    _forge_catalog_product_id_incoherence(tmp_path, store)

    def netcdf_read_must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("product-id-incoherent catalog row reached NetCDF read")

    monkeypatch.setattr(
        "yd_producer.forcing.netcdf_open.open_canonical_netcdf",
        netcdf_read_must_not_run,
    )
    with pytest.raises(
        ForcingStoreError,
        match="canonical_product_id does not match canonical product identity",
    ):
        repository.list_canonical_products(
            source_id="gfs",
            cycle_time=datetime(2026, 5, 7, 0, tzinfo=UTC),
        )
    with pytest.raises(
        ForcingProductionError,
        match="canonical_product_id does not match canonical product identity",
    ):
        _produce_gfs(producer)

    _assert_no_gfs_ready(repository, store)
