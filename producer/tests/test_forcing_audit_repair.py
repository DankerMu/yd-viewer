"""Focused issue #14 Phase 1 audit discriminators at forcing public seams."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from forcing_fixtures import (
    CYCLE_00Z,
    DEFAULT_GRID_SIGNATURE,
    GFS_GRID_ID,
    canonical_products_for_cycle,
    direct_grid_binding,
)

from yd_producer.forcing import (
    CanonicalProduct,
    ForcingProducer,
    ForcingProducerConfig,
    ForcingProductionError,
    parse_cycle_time,
)
from yd_producer.forcing.file_store import FileForcingRepository, ForcingStoreError
from yd_producer.forcing.producer import REQUIRED_CANONICAL_VARIABLES
from yd_producer.store.object_store import LocalObjectStore


class _FailFirstSidecarRepository(FileForcingRepository):
    def __init__(
        self, *, object_store: LocalObjectStore, registry_manifest: str
    ) -> None:
        super().__init__(object_store=object_store, registry_manifest=registry_manifest)
        self.fail_sidecar = True

    def _write_forcing_version_sidecar(self, record: Any) -> None:
        if self.fail_sidecar:
            self.fail_sidecar = False
            super()._write_forcing_version_sidecar(record)
            raise RuntimeError("sidecar injection")
        super()._write_forcing_version_sidecar(record)


class _FailFirstHandoffRepository(FileForcingRepository):
    def __init__(
        self, *, object_store: LocalObjectStore, registry_manifest: str
    ) -> None:
        super().__init__(object_store=object_store, registry_manifest=registry_manifest)
        self.fail_handoff = True

    def _write_forcing_domain_handoff(self, record: Any) -> None:
        if self.fail_handoff:
            self.fail_handoff = False
            raise RuntimeError("handoff injection")
        super()._write_forcing_domain_handoff(record)


class _FailFirstReadyCycleRepository(FileForcingRepository):
    def __init__(
        self, *, object_store: LocalObjectStore, registry_manifest: str
    ) -> None:
        super().__init__(object_store=object_store, registry_manifest=registry_manifest)
        self.fail_ready_cycle = True
        self._cycle_updates: list[dict[str, Any]] = []

    def update_forecast_cycle(self, **kwargs: Any) -> dict[str, Any] | None:
        if kwargs.get("status") == "forcing_ready" and self.fail_ready_cycle:
            self.fail_ready_cycle = False
            raise RuntimeError("ready cycle injection")
        self._cycle_updates.append(dict(kwargs))
        return super().update_forecast_cycle(**kwargs)


def _make_file_producer(
    tmp_path: Path,
    *,
    seed: dict[str, Any],
    store: LocalObjectStore,
    max_manifest_bytes: int = 33_554_432,
    binding_content: bytes = b'{"schema_version":"nhms.direct_grid.binding.v1"}',
    repository_type: type[FileForcingRepository] = FileForcingRepository,
) -> tuple[ForcingProducer, FileForcingRepository]:
    sp_att_content = b"2 1\nTRI\tA\tB\tC\tFORC\n1\t0\t0\t0\t1\n2\t0\t0\t0\t2\n"
    seed["binding_checksum"] = __import__(
        "yd_producer.store.object_store", fromlist=["sha256_bytes"]
    ).sha256_bytes(binding_content)
    seed["sp_att_checksum"] = __import__(
        "yd_producer.store.object_store", fromlist=["sha256_bytes"]
    ).sha256_bytes(sp_att_content)
    store.write_bytes_atomic(seed["binding_uri"], binding_content)
    store.write_bytes_atomic(
        f"models/demo/package/{seed['sp_att_path']}", sp_att_content
    )
    store.write_bytes_atomic(
        "models/demo/registry.json",
        json.dumps(
            {
                "models": [
                    {
                        "model_id": "demo_model",
                        "basin_id": "basin_a",
                        "basin_version_id": "basin_v1",
                        "river_network_version_id": "rivnet_v1",
                        "model_package_uri": "models/demo/package",
                        "resource_profile": {
                            "direct_grid_forcing": seed,
                            "shud_input_name": "demo",
                        },
                    }
                ]
            }
        ).encode("utf-8"),
    )
    repository = repository_type(
        object_store=store, registry_manifest="models/demo/registry.json"
    )
    producer = ForcingProducer(
        config=ForcingProducerConfig(
            workspace_root=tmp_path,
            object_store_root=tmp_path,
            object_store_prefix="",
            max_manifest_bytes=max_manifest_bytes,
        ),
        repository=repository,
        object_store=store,
    )
    return producer, repository


def _prepared_file_seam(
    tmp_path: Path,
    *,
    max_manifest_bytes: int = 33_554_432,
) -> tuple[ForcingProducer, FileForcingRepository, LocalObjectStore]:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding()
    producer, repository = _make_file_producer(
        tmp_path,
        seed=seed,
        store=store,
        max_manifest_bytes=max_manifest_bytes,
    )
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )
    return producer, repository, store


def _assert_no_ready(
    repository: FileForcingRepository, store: LocalObjectStore
) -> None:
    assert not any(
        record.get("checksum") for record in repository._forcing_versions.values()
    )
    assert not any(
        update.get("status") == "forcing_ready"
        for update in getattr(repository, "_cycle_updates", ())[:-1]
    )
    assert not any(
        path.name == "forcing_version_record.json"
        for path in store.root.rglob("forcing_version_record.json")
    )
    assert not any(
        path.name == "forcing_domain_handoff.json"
        for path in store.root.rglob("forcing_domain_handoff.json")
    )


def _assert_final_evidence_exists(result: Any, store: LocalObjectStore) -> None:
    package_root = store.root / result.forcing_package_uri.strip("/")
    assert (package_root / "forcing_version_record.json").is_file()
    assert any(
        path.name == "forcing_domain_handoff.json"
        for path in store.root.rglob("forcing_domain_handoff.json")
    )


@pytest.mark.parametrize("bad_source", ("unknown", "", "era5"))
def test_public_produce_normalizes_invalid_source_to_stable_error(
    tmp_path: Path, bad_source: str
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)

    with pytest.raises(
        ForcingProductionError, match="Invalid forcing production request"
    ):
        producer.produce(
            source_id=bad_source, cycle_time=CYCLE_00Z, model_id="demo_model"
        )

    _assert_no_ready(repository, store)


@pytest.mark.parametrize("cycle", ("not-a-cycle", "202605071"))
def test_public_produce_normalizes_malformed_cycle_to_stable_error(
    tmp_path: Path, cycle: str
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)

    with pytest.raises(
        ForcingProductionError, match="Invalid forcing production request"
    ):
        producer.produce(source_id="gfs", cycle_time=cycle, model_id="demo_model")

    _assert_no_ready(repository, store)


def test_public_produce_normalizes_unsafe_model_id_to_stable_error(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)

    with pytest.raises(
        ForcingProductionError, match="Invalid forcing production request"
    ):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="../escape")

    _assert_no_ready(repository, store)


@pytest.mark.parametrize("field", ("source_id", "cycle_time"))
def test_public_produce_rejects_self_consistent_wrong_product_identity(
    tmp_path: Path, field: str
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    original = repository.list_canonical_products

    def wrong_products(
        *, source_id: str, cycle_time: Any
    ) -> tuple[CanonicalProduct, ...]:
        products = original(source_id=source_id, cycle_time=cycle_time)
        if field == "source_id":
            return tuple(replace(product, source_id="ifs") for product in products)
        wrong_cycle = parse_cycle_time("2026050712")
        return tuple(replace(product, cycle_time=wrong_cycle) for product in products)

    repository.list_canonical_products = wrong_products  # type: ignore[method-assign]

    with pytest.raises(
        ForcingProductionError, match="do not match the requested source/cycle"
    ):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")

    _assert_no_ready(repository, store)


def test_public_produce_rejects_duplicate_canonical_grid_cell_id_before_ready(
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path)
    duplicate_grid_signature = (
        "410e449f17e81f665d28ec0efd6ceaa2f50fb457bd560e71d90cea6bca2e966d"
    )
    seed = direct_grid_binding(grid_signature=duplicate_grid_signature)
    producer, repository = _make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=duplicate_grid_signature,
        cell_ids=("0", "0", "1"),
    )

    with pytest.raises(
        ForcingProductionError,
        match="duplicate grid_cell_id '0'",
    ):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")

    _assert_no_ready(repository, store)


def test_public_produce_rejects_low_grid_cell_limit_before_ready(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    producer = ForcingProducer(
        config=replace(producer.config, max_grid_cell_count=2),
        repository=repository,
        object_store=store,
    )

    with pytest.raises(
        ForcingProductionError, match="grid_cell_count 3 exceeds configured limit 2"
    ):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")

    _assert_no_ready(repository, store)


def test_read_canonical_field_selects_bound_indexes_before_values_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer, _, _ = _prepared_file_seam(tmp_path)
    product = next(
        product
        for product in producer.repository.list_canonical_products(  # type: ignore[union-attr]
            source_id="gfs", cycle_time=parse_cycle_time(CYCLE_00Z)
        )
        if product.variable == "air_temperature_2m"
    )
    import xarray as xr

    original_values = xr.DataArray.values

    def guarded_values(data_array: Any) -> Any:
        if data_array.sizes.get("point") == 3:
            raise AssertionError("full canonical data array values were materialized")
        return original_values.fget(data_array)

    monkeypatch.setattr(xr.DataArray, "values", property(guarded_values))
    field = producer._read_canonical_field(  # public produce error-path support seam
        product,
        required_grid_cell_ids=frozenset({"0", "1"}),
        retain_grid_points=False,
        validate_all_values=False,
    )

    assert field.values_by_grid_cell_id == {"0": 10.0, "1": 20.0}


def test_file_repository_does_not_discover_products_when_catalog_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalObjectStore(tmp_path)
    store.write_bytes_atomic("models/demo/registry.json", b'{"models":[]}')
    store.write_bytes_atomic(
        "canonical/gfs/2026050700/air_temperature_2m/host-only.nc", b"not a catalog"
    )
    repository = FileForcingRepository(
        object_store=store, registry_manifest="models/demo/registry.json"
    )

    def directory_inspection(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("catalog miss inspected host directories")

    monkeypatch.setattr(Path, "glob", directory_inspection)
    monkeypatch.setattr(Path, "iterdir", directory_inspection)

    assert (
        repository.list_canonical_products(
            source_id="gfs", cycle_time=parse_cycle_time(CYCLE_00Z)
        )
        == ()
    )


@pytest.mark.parametrize(
    "reference",
    (
        "",
        "/models/demo/registry.json",
        "s3://bucket/models/demo/registry.json",
        "models/../demo/registry.json",
        "canonical/gfs/catalog.json",
        "forcing/gfs/x.json",
        "runs/demo/input/manifest.json",
    ),
)
def test_registry_manifest_rejects_non_model_object_keys(
    tmp_path: Path, reference: str
) -> None:
    repository = FileForcingRepository(
        object_store=LocalObjectStore(tmp_path), registry_manifest=reference
    )

    with pytest.raises(ForcingStoreError, match="registry_manifest"):
        repository.resolve_model_identity(model_id="demo_model")


def test_file_repository_uses_producer_asset_byte_limit(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding()
    binding_content = b"x" * 65_537
    producer, repository = _make_file_producer(
        tmp_path,
        seed=seed,
        store=store,
        max_manifest_bytes=65_536,
        binding_content=binding_content,
    )
    producer = ForcingProducer(
        config=replace(producer.config, max_manifest_bytes=65_536),
        repository=repository,
        object_store=store,
    )
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )

    with pytest.raises(ForcingProductionError, match=r"exceeds read limit.*65536"):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")

    _assert_no_ready(repository, store)


def test_declared_grid_definition_is_bounded_and_fails_closed(tmp_path: Path) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    producer = ForcingProducer(
        config=replace(producer.config, max_manifest_bytes=256),
        repository=repository,
        object_store=store,
    )
    grid_uri = "canonical/gfs/grid/gfs_0p25/grid.json"
    store.write_bytes_atomic(grid_uri, b"x" * 257)

    with pytest.raises(ForcingProductionError, match="grid definition"):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")

    _assert_no_ready(repository, store)


@pytest.mark.parametrize("leaf_kind", ("symlink", "fifo", "directory"))
def test_produce_rejects_no_follow_canonical_leaf_kinds(
    tmp_path: Path, leaf_kind: str
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    product = repository.list_canonical_products(
        source_id="gfs", cycle_time=parse_cycle_time(CYCLE_00Z)
    )[0]
    leaf = store.resolve_path(product.object_uri)
    outside = tmp_path.parent / "outside.nc"
    outside.write_bytes(leaf.read_bytes())
    leaf.unlink()
    if leaf_kind == "symlink":
        os.symlink(outside, leaf)
    elif leaf_kind == "fifo":
        os.mkfifo(leaf)
    else:
        leaf.mkdir()

    with pytest.raises(ForcingProductionError):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")
    _assert_no_ready(repository, store)


def test_produce_rejects_descriptor_alias_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    monkeypatch.setattr(
        "yd_producer.forcing.netcdf_open.descriptor_alias_path",
        lambda fd: (_ for _ in ()).throw(OSError("no alias")),
    )

    with pytest.raises(ForcingProductionError):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")
    _assert_no_ready(repository, store)


def test_produce_rejects_symlinked_canonical_ancestor(tmp_path: Path) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    product = repository.list_canonical_products(
        source_id="gfs", cycle_time=parse_cycle_time(CYCLE_00Z)
    )[0]
    product_path = store.resolve_path(product.object_uri)
    source_root = product_path.parents[2]
    outside = tmp_path.parent / "canonical-outside"
    source_root.rename(outside)
    os.symlink(outside, source_root)

    with pytest.raises(ForcingProductionError):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")
    _assert_no_ready(repository, store)


def _replace_canonical_leaf_with_changed_values(
    repository: FileForcingRepository,
    store: LocalObjectStore,
) -> None:
    from test_forcing_producer import _netcdf_bytes

    product = next(
        product
        for product in repository.list_canonical_products(
            source_id="gfs", cycle_time=parse_cycle_time(CYCLE_00Z)
        )
        if product.variable == "air_temperature_2m"
    )
    replacement = _netcdf_bytes(
        "air_temperature_2m",
        values=(101.0, 202.0, 999.0),
        cell_ids=("0", "1", "2"),
    )
    store.write_bytes_atomic(product.object_uri, replacement)


def test_public_produce_rejects_replaced_canonical_leaf_checksum_before_ready(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    _replace_canonical_leaf_with_changed_values(repository, store)

    with pytest.raises(ForcingProductionError, match="Canonical checksum mismatch"):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")

    _assert_no_ready(repository, store)


def test_public_produce_does_not_reuse_ready_after_canonical_leaf_replacement(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    first = producer.produce(
        source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model"
    )
    assert first.status == "forcing_ready"
    _assert_final_evidence_exists(first, store)
    _replace_canonical_leaf_with_changed_values(repository, store)

    with pytest.raises(ForcingProductionError, match="Canonical checksum mismatch"):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")

    assert repository._forcing_versions[first.forcing_version_id]["checksum"] is None
    assert not store.exists(
        f"{first.forcing_package_uri.strip('/')}/forcing_version_record.json"
    )
    assert not any(
        path.name == "forcing_domain_handoff.json" for path in store.root.rglob("*")
    )


def _prepared_failing_finalization_seam(
    tmp_path: Path,
    repository_type: type[FileForcingRepository],
) -> tuple[ForcingProducer, FileForcingRepository, LocalObjectStore]:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding()
    producer, repository = _make_file_producer(
        tmp_path,
        seed=seed,
        store=store,
        repository_type=repository_type,
    )
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )
    return producer, repository, store


def test_finalize_sidecar_failure_is_not_ready_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_failing_finalization_seam(
        tmp_path, _FailFirstSidecarRepository
    )

    with pytest.raises(ForcingProductionError, match="sidecar injection"):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")
    _assert_no_ready(repository, store)

    result = producer.produce(
        source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model"
    )
    assert result.status == "forcing_ready"
    assert (
        repository._forcing_versions[result.forcing_version_id]["checksum"]
        == result.checksum
    )
    _assert_final_evidence_exists(result, store)


def test_finalize_handoff_failure_removes_earlier_sidecar_and_retries(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_failing_finalization_seam(
        tmp_path, _FailFirstHandoffRepository
    )

    with pytest.raises(ForcingProductionError, match="handoff injection"):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")
    _assert_no_ready(repository, store)

    result = producer.produce(
        source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model"
    )
    assert result.status == "forcing_ready"
    _assert_final_evidence_exists(result, store)


def test_ready_cycle_failure_removes_final_markers_and_retries(tmp_path: Path) -> None:
    producer, repository, store = _prepared_failing_finalization_seam(
        tmp_path, _FailFirstReadyCycleRepository
    )

    with pytest.raises(ForcingProductionError, match="ready cycle injection"):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")
    _assert_no_ready(repository, store)

    result = producer.produce(
        source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model"
    )
    assert result.status == "forcing_ready"
    _assert_final_evidence_exists(result, store)


def test_ifs_branch_uses_ifs_specific_pressure_variable(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding(
        grid_id="ifs_0p25",
        grid_signature=DEFAULT_GRID_SIGNATURE,
        applicable_source_ids=("IFS",),
    )
    producer, _repository = _make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="ifs",
        cycle_text="2026050712",
        grid_id="ifs_0p25",
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )

    assert producer._required_canonical_variables("ifs") != REQUIRED_CANONICAL_VARIABLES
    result = producer.produce(
        source_id="ifs", cycle_time="2026050712", model_id="demo_model"
    )
    assert result.status == "forcing_ready"
