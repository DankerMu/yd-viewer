"""Issue #14 Phase 1 public forcing contract-boundary discriminators.

The tests use the file-backed public producer seam and mutate only its local
object-store fixtures.  Expected schema and path values are pinned literals,
not values derived from the production implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import types
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from forcing_fixtures import (
    CYCLE_00Z,
    DEFAULT_GRID_SIGNATURE,
    GFS_GRID_ID,
    canonical_products_for_cycle,
    direct_grid_binding,
    make_file_producer,
)

from yd_producer.forcing import (
    DirectGridContractError,
    ForcingProducer,
    ForcingProductionError,
    parse_cycle_time,
    parse_direct_grid_forcing_contract,
)
from yd_producer.forcing.file_store import FileForcingRepository, ForcingStoreError
from yd_producer.forcing.netcdf_open import open_canonical_netcdf
from yd_producer.store.object_store import LocalObjectStore, ObjectStoreError

CATALOG_KEY = "canonical/gfs/2026050700/_catalog/catalog.json"
PACKAGE_KEY = "forcing/gfs/2026050700/basin_v1/demo_model"
SIDECAR_KEY = f"{PACKAGE_KEY}/forcing_version_record.json"
DOMAIN_PACKAGE_KEY = f"{PACKAGE_KEY}/forcing_domain_package.json"
HANDOFF_KEY = "runs/fcst_gfs_2026050700_demo_model/input/forcing_domain_handoff.json"
FORCING_VERSION_ID = "forc_gfs_2026050700_demo_model"

# Literal schema oracle from test_canonical_db_free.py's inherited-writer pin.
CATALOG_ENVELOPE_KEYS = (
    "cycle_time",
    "products",
    "schema_version",
    "source_id",
)
CATALOG_ROW_KEYS = (
    "canonical_product_id",
    "checksum",
    "cycle_time",
    "grid_definition_uri",
    "grid_id",
    "lead_time_hours",
    "lineage_json",
    "native_spatial_resolution",
    "native_time_resolution",
    "object_uri",
    "quality_flag",
    "source_id",
    "source_version",
    "unit",
    "valid_time",
    "variable",
)
ROW_IDENTITY_KEYS = (
    "source_id",
    "cycle_time",
    "grid_id",
    "grid_definition_uri",
    "object_uri",
    "checksum",
)


def _prepared_file_seam(
    tmp_path: Path,
    *,
    store: LocalObjectStore | None = None,
    object_store_prefix: str = "",
) -> tuple[ForcingProducer, FileForcingRepository, LocalObjectStore]:
    object_store = (
        store
        if store is not None
        else LocalObjectStore(tmp_path, object_store_prefix=object_store_prefix)
    )
    seed = direct_grid_binding()
    producer, repository = make_file_producer(
        tmp_path,
        seed=seed,
        store=object_store,
    )
    if object_store_prefix:
        producer = ForcingProducer(
            config=replace(producer.config, object_store_prefix=object_store_prefix),
            repository=repository,
            object_store=object_store,
        )
    canonical_products_for_cycle(
        object_store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )
    return producer, repository, object_store


def _produce(producer: ForcingProducer) -> Any:
    return producer.produce(
        source_id="gfs",
        cycle_time=CYCLE_00Z,
        model_id="demo_model",
    )


def _catalog_payload(store: LocalObjectStore) -> dict[str, Any]:
    return json.loads(store.read_bytes(CATALOG_KEY).decode("utf-8"))


def _write_catalog(store: LocalObjectStore, payload: dict[str, Any]) -> None:
    store.write_bytes_atomic(
        CATALOG_KEY,
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )


def _assert_no_ready(
    repository: FileForcingRepository,
    store: LocalObjectStore,
) -> None:
    record = repository.get_forcing_version(
        source_id="gfs",
        cycle_time=parse_cycle_time(CYCLE_00Z),
        model_id="demo_model",
    )
    assert record is None or not str(record.get("checksum") or "").strip()
    assert not store.exists(SIDECAR_KEY)
    assert not store.exists(DOMAIN_PACKAGE_KEY)
    assert not store.exists(HANDOFF_KEY)
    assert not store.exists(f"{PACKAGE_KEY}/forcing_package.json")


def _assert_final_evidence_exists(store: LocalObjectStore) -> None:
    assert store.exists(SIDECAR_KEY)
    assert store.exists(DOMAIN_PACKAGE_KEY)
    assert store.exists(HANDOFF_KEY)


def _assert_catalog_refused(
    producer: ForcingProducer,
    repository: FileForcingRepository,
    store: LocalObjectStore,
) -> None:
    with pytest.raises(ForcingStoreError, match="Canonical product catalog"):
        repository.list_canonical_products(
            source_id="gfs",
            cycle_time=parse_cycle_time(CYCLE_00Z),
        )
    with pytest.raises(ForcingProductionError, match="Canonical product catalog"):
        _produce(producer)
    _assert_no_ready(repository, store)


# A. Stable public request types ------------------------------------------------


@pytest.mark.parametrize("use_config_source", (False, True), ids=("argument", "config"))
def test_public_produce_rejects_non_string_effective_source_id(
    tmp_path: Path,
    use_config_source: bool,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    if use_config_source:
        producer = ForcingProducer(
            config=replace(producer.config, source_id=object()),
            repository=repository,
            object_store=store,
        )
        source_id: Any = None
    else:
        source_id = object()

    with pytest.raises(
        ForcingProductionError,
        match="Invalid forcing production request",
    ):
        producer.produce(
            source_id=source_id,
            cycle_time=CYCLE_00Z,
            model_id="demo_model",
        )

    _assert_no_ready(repository, store)


@pytest.mark.parametrize(
    "cycle_time",
    (object(), None, 2026050700, date(2026, 5, 7)),
    ids=("object", "none", "integer", "date"),
)
def test_public_produce_rejects_non_string_non_datetime_cycle_time(
    tmp_path: Path,
    cycle_time: Any,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)

    with pytest.raises(
        ForcingProductionError,
        match="Invalid forcing production request",
    ):
        producer.produce(
            source_id="gfs",
            cycle_time=cycle_time,
            model_id="demo_model",
        )

    _assert_no_ready(repository, store)


def test_public_produce_keeps_none_config_source_and_datetime_requests_valid(
    tmp_path: Path,
) -> None:
    producer, _, _ = _prepared_file_seam(tmp_path)

    result = producer.produce(
        source_id=None,
        cycle_time=datetime(2026, 5, 7, 0, tzinfo=UTC),
        model_id="demo_model",
    )

    assert result.status == "forcing_ready"


# B. Canonical catalog authority ------------------------------------------------


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("schema_version", None),
        ("schema_version", "nhms.canonical.product_catalog.v0"),
        ("source_id", None),
        ("source_id", "ifs"),
        ("cycle_time", None),
        ("cycle_time", "2026-05-07T12:00:00Z"),
    ),
    ids=(
        "missing-schema",
        "wrong-schema",
        "missing-source",
        "wrong-source",
        "missing-cycle",
        "wrong-cycle",
    ),
)
def test_catalog_envelope_identity_is_required_and_matches_request(
    tmp_path: Path,
    field: str,
    replacement: str | None,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    payload = _catalog_payload(store)
    if replacement is None:
        del payload[field]
    else:
        payload[field] = replacement
    _write_catalog(store, payload)

    _assert_catalog_refused(producer, repository, store)


@pytest.mark.parametrize("missing_key", CATALOG_ROW_KEYS)
def test_catalog_requires_every_inherited_product_row_key(
    tmp_path: Path,
    missing_key: str,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    payload = _catalog_payload(store)
    del payload["products"][0][missing_key]
    _write_catalog(store, payload)

    _assert_catalog_refused(producer, repository, store)


@pytest.mark.parametrize("identity_key", ROW_IDENTITY_KEYS)
def test_catalog_rejects_empty_product_identity_without_request_fallback(
    tmp_path: Path,
    identity_key: str,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    payload = _catalog_payload(store)
    payload["products"][0][identity_key] = ""
    _write_catalog(store, payload)

    _assert_catalog_refused(producer, repository, store)


def test_catalog_rejects_extra_envelope_key_at_repository_and_public_seams(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    payload = _catalog_payload(store)
    payload["unexpected"] = "not-in-inherited-schema"
    _write_catalog(store, payload)

    _assert_catalog_refused(producer, repository, store)


def test_catalog_rejects_extra_product_row_key_at_repository_and_public_seams(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    payload = _catalog_payload(store)
    payload["products"][0]["unexpected"] = "not-in-inherited-row-schema"
    _write_catalog(store, payload)

    _assert_catalog_refused(producer, repository, store)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (("source_id", "ifs"), ("cycle_time", "2026-05-07T12:00:00Z")),
    ids=("row-source", "row-cycle"),
)
def test_catalog_product_row_identity_matches_requested_envelope_and_public_request(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    payload = _catalog_payload(store)
    payload["products"][0][field] = replacement
    _write_catalog(store, payload)

    _assert_catalog_refused(producer, repository, store)


def test_catalog_rejects_non_object_product_row_at_repository_boundary(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    payload = _catalog_payload(store)
    payload["products"][0] = ["not", "a", "catalog", "object"]
    _write_catalog(store, payload)

    _assert_catalog_refused(producer, repository, store)


def test_catalog_translates_malformed_row_time_to_store_error(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    payload = _catalog_payload(store)
    payload["products"][0]["valid_time"] = {"not": "a time"}
    _write_catalog(store, payload)

    _assert_catalog_refused(producer, repository, store)


def test_catalog_requires_mapping_lineage_without_second_json_parse(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    payload = _catalog_payload(store)
    payload["products"][0]["lineage_json"] = "[" * 200 + "]" * 200
    _write_catalog(store, payload)

    _assert_catalog_refused(producer, repository, store)


def test_catalog_rejects_duplicate_canonical_product_id_before_producer_grouping(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    payload = _catalog_payload(store)
    payload["products"].append(dict(payload["products"][0]))
    _write_catalog(store, payload)

    _assert_catalog_refused(producer, repository, store)


def test_catalog_rejects_duplicate_variable_valid_time_slot_before_grouping(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    payload = _catalog_payload(store)
    duplicate = dict(payload["products"][0])
    duplicate["canonical_product_id"] = (
        "gfs_2026050700_prcp_rate_or_amount_f000_duplicate"
    )
    payload["products"].append(duplicate)
    _write_catalog(store, payload)

    _assert_catalog_refused(producer, repository, store)


# C. Package namespace collisions ------------------------------------------------


def test_direct_grid_parser_rejects_casefolded_station_filename_collision() -> None:
    manifest = direct_grid_binding()
    manifest["station_bindings"][1]["forcing_filename"] = "x1.csv"

    with pytest.raises(
        DirectGridContractError, match="forcing_filename values must be unique"
    ):
        parse_direct_grid_forcing_contract(manifest, source_id="gfs")


def test_direct_grid_parser_keeps_distinct_mixed_case_station_filenames_valid() -> None:
    manifest = direct_grid_binding()
    manifest["station_bindings"][0]["forcing_filename"] = "X1.csv"
    manifest["station_bindings"][1]["forcing_filename"] = "Y2.csv"

    contract = parse_direct_grid_forcing_contract(manifest, source_id="gfs")

    assert [station.forcing_filename for station in contract.stations] == [
        "X1.csv",
        "Y2.csv",
    ]


@pytest.mark.parametrize(
    "reserved_name",
    (
        "forcing_version_record.json",
        "forcing_domain_package.json",
        "shud",
        "payloads",
    ),
)
@pytest.mark.parametrize(
    "field",
    ("forcing_filename", "csv_filename", "package_manifest_filename"),
)
def test_public_produce_rejects_configured_internal_package_name_collisions(
    tmp_path: Path,
    field: str,
    reserved_name: str,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    producer = ForcingProducer(
        config=replace(producer.config, **{field: reserved_name}),
        repository=repository,
        object_store=store,
    )

    with pytest.raises(
        ForcingProductionError,
        match="Configured package filename .*reserved",
    ):
        _produce(producer)

    _assert_no_ready(repository, store)


def test_public_produce_rejects_casefolded_configured_root_name_collision(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    producer = ForcingProducer(
        config=replace(
            producer.config,
            forcing_filename="forcing.tsd.forc",
            csv_filename="FORCING.TSD.FORC",
        ),
        repository=repository,
        object_store=store,
    )

    with pytest.raises(ForcingProductionError, match="Configured package filename"):
        _produce(producer)

    _assert_no_ready(repository, store)


# D. Existing-ready final-evidence proof ----------------------------------------


@pytest.mark.parametrize(
    "missing_marker",
    (SIDECAR_KEY, DOMAIN_PACKAGE_KEY, HANDOFF_KEY),
    ids=("sidecar", "domain-package", "handoff"),
)
def test_missing_final_evidence_regenerates_and_then_proves_already_done(
    tmp_path: Path,
    missing_marker: str,
) -> None:
    producer, _, store = _prepared_file_seam(tmp_path)
    first = _produce(producer)
    assert first.status == "forcing_ready"
    _assert_final_evidence_exists(store)
    store.delete(missing_marker)
    assert not store.exists(missing_marker)

    regenerated = _produce(producer)

    assert regenerated.status == "forcing_ready"
    _assert_final_evidence_exists(store)
    assert _produce(producer).status == "already_done"


@pytest.mark.parametrize(
    ("marker_key", "field", "replacement"),
    (
        (SIDECAR_KEY, "forcing_version_id", "forc_other_2026050700_demo_model"),
        (
            HANDOFF_KEY,
            "forcing_package_manifest_checksum_sha256",
            "not-the-ready-checksum",
        ),
        (
            HANDOFF_KEY,
            "forcing_domain_package_manifest_uri",
            "forcing/gfs/2026050700/basin_v1/demo_model/not-the-domain-package.json",
        ),
        (
            HANDOFF_KEY,
            "forcing_domain_package_manifest_checksum_sha256",
            "not-the-domain-package-checksum",
        ),
        (
            DOMAIN_PACKAGE_KEY,
            "contract_id",
            "nhms.forcing_domain_handoff.package.v0",
        ),
    ),
    ids=(
        "sidecar-version-identity",
        "handoff-package-checksum",
        "handoff-domain-uri",
        "handoff-domain-checksum",
        "domain-contract-id",
    ),
)
def test_incoherent_final_evidence_regenerates_before_already_done(
    tmp_path: Path,
    marker_key: str,
    field: str,
    replacement: str,
) -> None:
    producer, _, store = _prepared_file_seam(tmp_path)
    assert _produce(producer).status == "forcing_ready"
    marker = json.loads(store.read_bytes(marker_key).decode("utf-8"))
    marker[field] = replacement
    store.write_bytes_atomic(
        marker_key, json.dumps(marker, sort_keys=True).encode("utf-8")
    )

    regenerated = _produce(producer)

    assert regenerated.status == "forcing_ready"
    _assert_final_evidence_exists(store)
    assert _produce(producer).status == "already_done"


@pytest.mark.parametrize(
    ("marker_key", "field", "replacement", "relink_domain_checksum"),
    (
        (SIDECAR_KEY, "model_id", "other_model", False),
        (HANDOFF_KEY, "source_id", "ifs", False),
        (HANDOFF_KEY, "cycle_time", "2026-05-07T12:00:00Z", False),
        (DOMAIN_PACKAGE_KEY, "source_id", "ifs", True),
        (DOMAIN_PACKAGE_KEY, "model_id", "other_model", True),
        (SIDECAR_KEY, "source_id", "ifs", False),
        (SIDECAR_KEY, "cycle_time", "2026-05-07T12:00:00Z", False),
        (DOMAIN_PACKAGE_KEY, "cycle_time", "2026-05-07T12:00:00Z", True),
        (HANDOFF_KEY, "model_id", "other_model", False),
    ),
    ids=(
        "sidecar-model",
        "handoff-source",
        "handoff-cycle",
        "domain-package-source-relinked",
        "domain-package-model-relinked",
        "sidecar-source",
        "sidecar-cycle",
        "domain-package-cycle-relinked",
        "handoff-model",
    ),
)
def test_all_final_evidence_tuple_siblings_regenerate_before_already_done(
    tmp_path: Path,
    marker_key: str,
    field: str,
    replacement: str,
    relink_domain_checksum: bool,
) -> None:
    producer, _, store = _prepared_file_seam(tmp_path)
    assert _produce(producer).status == "forcing_ready"
    marker = json.loads(store.read_bytes(marker_key).decode("utf-8"))
    marker[field] = replacement
    store.write_bytes_atomic(
        marker_key, json.dumps(marker, sort_keys=True).encode("utf-8")
    )
    if relink_domain_checksum:
        handoff = json.loads(store.read_bytes(HANDOFF_KEY).decode("utf-8"))
        handoff["forcing_domain_package_manifest_checksum_sha256"] = hashlib.sha256(
            store.read_bytes(DOMAIN_PACKAGE_KEY)
        ).hexdigest()
        store.write_bytes_atomic(
            HANDOFF_KEY, json.dumps(handoff, sort_keys=True).encode("utf-8")
        )

    regenerated = _produce(producer)

    assert regenerated.status == "forcing_ready"
    _assert_final_evidence_exists(store)
    assert _produce(producer).status == "already_done"


def test_malformed_domain_package_evidence_regenerates_without_escape(
    tmp_path: Path,
) -> None:
    producer, _, store = _prepared_file_seam(tmp_path)
    assert _produce(producer).status == "forcing_ready"
    store.write_bytes_atomic(DOMAIN_PACKAGE_KEY, b"{not valid JSON")

    regenerated = _produce(producer)

    assert regenerated.status == "forcing_ready"
    _assert_final_evidence_exists(store)
    assert _produce(producer).status == "already_done"


@pytest.mark.parametrize("drift_kind", ("component", "timeseries"))
def test_same_count_child_identity_drift_regenerates_before_already_done(
    tmp_path: Path,
    drift_kind: str,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    assert _produce(producer).status == "forcing_ready"
    if drift_kind == "component":
        components = list(repository._forcing_components[FORCING_VERSION_ID])
        components[0] = replace(
            components[0],
            canonical_product_id="gfs_2026050700_corrupted_component_f000",
        )
        repository.replace_forcing_components(FORCING_VERSION_ID, components)
    else:
        rows = list(repository._forcing_timeseries_rows[FORCING_VERSION_ID])
        source_row = next(row for row in rows if row.station_id == "forc_001")
        rows[rows.index(source_row)] = replace(source_row, station_id="forc_002")
        repository.replace_forcing_timeseries(FORCING_VERSION_ID, rows)

    regenerated = _produce(producer)

    assert regenerated.status == "forcing_ready"
    _assert_final_evidence_exists(store)
    assert _produce(producer).status == "already_done"


def test_binding_validation_failure_revokes_existing_ready_evidence(
    tmp_path: Path,
) -> None:
    producer, repository, store = _prepared_file_seam(tmp_path)
    first = _produce(producer)
    assert first.status == "forcing_ready"
    seed = direct_grid_binding(grid_signature="not-the-canonical-grid-signature")
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
    repository._registry_cache = None

    with pytest.raises(ForcingProductionError, match="grid_signature mismatch"):
        _produce(producer)

    record = repository.get_forcing_version(
        source_id="gfs",
        cycle_time=parse_cycle_time(CYCLE_00Z),
        model_id="demo_model",
    )
    assert record is not None
    assert record["checksum"] is None
    assert not store.exists(SIDECAR_KEY)
    assert not store.exists(HANDOFF_KEY)


class _ToggleWriteFailureStore(LocalObjectStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        object.__setattr__(self, "fail_regeneration_writes", False)

    def enable_regeneration_write_failure(self) -> None:
        object.__setattr__(self, "fail_regeneration_writes", True)

    def write_bytes_atomic(self, key_or_uri: str, content: bytes) -> str:
        if self.fail_regeneration_writes and str(key_or_uri).endswith(
            "/forcing.tsd.forc"
        ):
            raise OSError("regeneration write injection")
        return super().write_bytes_atomic(key_or_uri, content)


def test_stale_ready_markers_are_cleared_before_regeneration_write_failure(
    tmp_path: Path,
) -> None:
    store = _ToggleWriteFailureStore(tmp_path)
    producer, repository, _ = _prepared_file_seam(tmp_path, store=store)
    first = _produce(producer)
    assert first.status == "forcing_ready"
    store.delete(SIDECAR_KEY)
    store.enable_regeneration_write_failure()

    with pytest.raises(ForcingProductionError, match="regeneration write injection"):
        _produce(producer)

    record = repository.get_forcing_version(
        source_id="gfs",
        cycle_time=parse_cycle_time(CYCLE_00Z),
        model_id="demo_model",
    )
    assert record is not None
    assert record["checksum"] is None
    assert not store.exists(SIDECAR_KEY)
    assert not store.exists(HANDOFF_KEY)


def test_prefixed_store_production_reads_own_package_uri_and_reuses_evidence(
    tmp_path: Path,
) -> None:
    producer, _, store = _prepared_file_seam(
        tmp_path,
        object_store_prefix="s3://nhms/work",
    )

    first = _produce(producer)

    assert first.status == "forcing_ready"
    assert (
        first.forcing_package_uri
        == "s3://nhms/work/forcing/gfs/2026050700/basin_v1/demo_model/"
    )
    _assert_final_evidence_exists(store)
    sidecar = json.loads(store.read_bytes(SIDECAR_KEY).decode("utf-8"))
    handoff = json.loads(store.read_bytes(HANDOFF_KEY).decode("utf-8"))
    domain_bytes = store.read_bytes(DOMAIN_PACKAGE_KEY)
    assert sidecar["checksum"] == first.checksum
    assert handoff["forcing_package_manifest_checksum_sha256"] == first.checksum
    assert handoff["forcing_domain_package_manifest_uri"] == (
        "s3://nhms/work/forcing/gfs/2026050700/basin_v1/demo_model/forcing_domain_package.json"
    )
    assert (
        handoff["forcing_domain_package_manifest_checksum_sha256"]
        == __import__("hashlib").sha256(domain_bytes).hexdigest()
    )
    assert _produce(producer).status == "already_done"


@pytest.mark.parametrize(
    "reference",
    (
        "s3://other-bucket/work/forcing/gfs/2026050700/basin_v1/demo_model/forcing_package.json",
        "s3://nhms/outside/forcing/gfs/2026050700/basin_v1/demo_model/forcing_package.json",
    ),
)
def test_file_repository_rejects_external_or_outside_prefix_json_reference(
    tmp_path: Path,
    reference: str,
) -> None:
    _, repository, _ = _prepared_file_seam(
        tmp_path,
        object_store_prefix="s3://nhms/work",
    )

    with pytest.raises(ForcingStoreError, match="object-store relative key"):
        repository._read_json_reference(reference)


class _FailOneMarkerDeleteStore(LocalObjectStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        object.__setattr__(self, "delete_attempts", [])
        object.__setattr__(self, "failing_key", "")

    def fail_delete_for(self, key: str) -> None:
        object.__setattr__(self, "failing_key", key)

    def delete(self, key_or_uri: str) -> None:
        normalized = self.normalize_key(key_or_uri)
        self.delete_attempts.append(normalized)
        if normalized == self.failing_key:
            raise ObjectStoreError("marker delete injection")
        super().delete(key_or_uri)


def test_final_marker_cleanup_attempts_handoff_after_sidecar_delete_failure(
    tmp_path: Path,
) -> None:
    store = _FailOneMarkerDeleteStore(tmp_path)
    producer, repository, _ = _prepared_file_seam(tmp_path, store=store)
    assert _produce(producer).status == "forcing_ready"
    store.delete_attempts.clear()
    store.fail_delete_for(SIDECAR_KEY)

    with pytest.raises(
        ForcingStoreError,
        match="Failed to remove final forcing readiness markers",
    ):
        repository.clear_forcing_version_checksum(FORCING_VERSION_ID)

    assert store.delete_attempts == [SIDECAR_KEY, HANDOFF_KEY]


# E. Descriptor lifecycle --------------------------------------------------------


def test_open_canonical_netcdf_closes_fd_when_dataset_close_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalObjectStore(tmp_path)
    object_key = "canonical/gfs/2026050700/air_temperature_2m/fake.nc"
    store.write_bytes_atomic(object_key, b"not a NetCDF file")
    captured: dict[str, Path] = {}

    class CloseRaises:
        def close(self) -> None:
            raise RuntimeError("dataset close injection")

    fake_xarray = types.ModuleType("xarray")

    def open_dataset(alias: Path) -> CloseRaises:
        captured["alias"] = alias
        return CloseRaises()

    fake_xarray.open_dataset = open_dataset  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "xarray", fake_xarray)

    with (
        pytest.raises(RuntimeError, match="dataset close injection"),
        open_canonical_netcdf(store, object_key),
    ):
        pass

    alias = captured["alias"]
    assert alias != store.resolve_path(object_key)
    assert str(alias).startswith(("/proc/self/fd/", "/dev/fd/"))
    file_fd = int(alias.name)
    try:
        with pytest.raises(OSError):
            os.fstat(file_fd)
    finally:
        try:
            os.close(file_fd)
        except OSError:
            pass
