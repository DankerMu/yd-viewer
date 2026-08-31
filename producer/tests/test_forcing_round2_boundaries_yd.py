"""Round-2 public boundary regressions for direct-grid forcing."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
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
    DirectGridForcingContract,
    DirectGridStationBinding,
    ForcingProducer,
    ForcingProductionError,
    parse_direct_grid_forcing_contract,
)
from yd_producer.forcing.direct_grid_contract import (
    validate_direct_grid_forcing_contract,
)
from yd_producer.forcing.file_store import FileForcingRepository
from yd_producer.store.object_store import LocalObjectStore

_PACKAGE_KEY = "forcing/gfs/2026050700/basin_v1/demo_model"
_RECORD_KEY = f"{_PACKAGE_KEY}/forcing_version_record.json"
_PACKAGE_MANIFEST_KEY = f"{_PACKAGE_KEY}/forcing_package.json"
_DOMAIN_PACKAGE_KEY = f"{_PACKAGE_KEY}/forcing_domain_package.json"
_HANDOFF_KEY = "runs/fcst_gfs_2026050700_demo_model/input/forcing_domain_handoff.json"
_CYCLE_EVIDENCE_KEY = (
    "runs/fcst_gfs_2026050700_demo_model/input/cycle_ready_evidence.json"
)


class _NoRepositoryAccess:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        self.calls.append(name)
        raise AssertionError(f"invalid public request called repository method {name}")


class _PreflightSentinelRepository:
    def __init__(
        self,
        *,
        contract: Any,
        model_identity: Mapping[str, Any] | None = None,
    ) -> None:
        self.contract = contract
        self.model_identity = dict(
            model_identity
            or {
                "basin_id": "basin_a",
                "basin_version_id": "basin_v1",
                "river_network_version_id": "rivnet_v1",
            }
        )
        self.calls: list[str] = []

    def resolve_model_identity(self, *, model_id: str) -> Mapping[str, Any]:
        self.calls.append("resolve_model_identity")
        return dict(self.model_identity)

    def load_forcing_mapping_contract(
        self,
        *,
        model_id: str,
        basin_version_id: str,
        source_id: str | None = None,
    ) -> Any:
        self.calls.append("load_forcing_mapping_contract")
        return self.contract

    def get_forcing_version(self, **_: Any) -> None:
        self.calls.append("get_forcing_version")
        raise AssertionError("invalid preflight reached existing forcing lookup")

    def update_forecast_cycle(self, **_: Any) -> None:
        self.calls.append("update_forecast_cycle")
        raise AssertionError("invalid preflight attempted failure-status write")

    def __getattr__(self, name: str) -> Any:
        self.calls.append(name)
        raise AssertionError(f"invalid preflight called repository method {name}")


class _FileBackedProtocolRepository:
    def __init__(
        self,
        *,
        backing: FileForcingRepository,
        contract: DirectGridForcingContract,
        store: LocalObjectStore,
        model_identity: Mapping[str, Any] | None = None,
        cycle_evidence_key: str = _CYCLE_EVIDENCE_KEY,
    ) -> None:
        self.backing = backing
        self.contract = contract
        self.store = store
        self.model_identity = (
            dict(model_identity) if model_identity is not None else None
        )
        self.cycle_evidence_key = cycle_evidence_key
        self.calls: list[str] = []

    def resolve_model_identity(self, *, model_id: str) -> Mapping[str, Any]:
        self.calls.append("resolve_model_identity")
        if self.model_identity is not None:
            return dict(self.model_identity)
        return self.backing.resolve_model_identity(model_id=model_id)

    def load_forcing_mapping_contract(
        self,
        *,
        model_id: str,
        basin_version_id: str,
        source_id: str | None = None,
    ) -> DirectGridForcingContract:
        self.calls.append("load_forcing_mapping_contract")
        return self.contract

    def get_forcing_version(self, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append("get_forcing_version")
        return self.backing.get_forcing_version(**kwargs)

    def update_forecast_cycle(self, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append("update_forecast_cycle")
        payload = {
            "cycle_time": str(kwargs["cycle_time"]),
            "source_id": kwargs["source_id"],
            "status": kwargs.get("status"),
            "error_code": kwargs.get("error_code"),
        }
        self.store.write_bytes_atomic(
            self.cycle_evidence_key,
            json.dumps(payload, sort_keys=True).encode("utf-8"),
        )
        return self.backing.update_forecast_cycle(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.backing, name)


def _station(
    *,
    index: Any = 1,
    station_id: Any = "forc_001",
    filename: Any = "X1.csv",
    grid_id: Any = GFS_GRID_ID,
    grid_cell_id: Any = "0",
    longitude: Any = -75.0,
    latitude: Any = 40.0,
    x: Any = 1.0,
    y: Any = 1.0,
    z: Any = 101.0,
    properties: Any = None,
) -> DirectGridStationBinding:
    return DirectGridStationBinding(
        station_id=station_id,
        shud_forcing_index=index,
        forcing_filename=filename,
        longitude=longitude,
        latitude=latitude,
        x=x,
        y=y,
        z=z,
        grid_id=grid_id,
        grid_cell_id=grid_cell_id,
        properties={} if properties is None else properties,
    )


def _contract(
    *,
    forcing_mapping_mode: Any = "direct_grid",
    binding_uri: Any = "models/demo/binding.json",
    binding_checksum: Any = "binding-checksum",
    model_input_package_id: Any = "model-input-v1",
    sp_att_path: Any = "input/demo.sp.att",
    sp_att_checksum: Any = "sp-att-checksum",
    applicable_source_ids: Any = ("gfs",),
    grid_id: Any = GFS_GRID_ID,
    grid_signature: Any = DEFAULT_GRID_SIGNATURE,
    stations: Any = None,
) -> DirectGridForcingContract:
    return DirectGridForcingContract(
        forcing_mapping_mode=forcing_mapping_mode,
        binding_uri=binding_uri,
        binding_checksum=binding_checksum,
        model_input_package_id=model_input_package_id,
        sp_att_path=sp_att_path,
        sp_att_checksum=sp_att_checksum,
        applicable_source_ids=applicable_source_ids,
        grid_id=grid_id,
        grid_signature=grid_signature,
        stations=(_station(),) if stations is None else stations,
    )


def _contract_from_file_seed(seed: Mapping[str, Any]) -> DirectGridForcingContract:
    stations = tuple(
        _station(
            index=row["shud_forcing_index"],
            station_id=row["station_id"],
            filename=row["forcing_filename"],
            grid_id=row["grid_id"],
            grid_cell_id=row["grid_cell_id"],
            longitude=row["longitude"],
            latitude=row["latitude"],
            x=row["x"],
            y=row["y"],
            z=row["z"],
        )
        for row in seed["station_bindings"]
    )
    return _contract(
        binding_uri=seed["binding_uri"],
        binding_checksum=seed["binding_checksum"],
        model_input_package_id=seed["model_input_package_id"],
        sp_att_path=seed["sp_att_path"],
        sp_att_checksum=seed["sp_att_checksum"],
        applicable_source_ids=("gfs",),
        grid_id=seed["grid_id"],
        grid_signature=seed["grid_signature"],
        stations=stations,
    )


def _producer_for_preflight(
    tmp_path: Path,
    *,
    repository: Any,
) -> ForcingProducer:
    store = LocalObjectStore(tmp_path)
    return ForcingProducer(
        config=make_file_producer(
            tmp_path,
            seed=direct_grid_binding(),
            store=store,
        )[0].config,
        repository=repository,
        object_store=store,
    )


def _prepared_direct_file_producer(
    tmp_path: Path,
    *,
    forecast_hours: tuple[int, ...] = (0,),
) -> tuple[
    ForcingProducer,
    _FileBackedProtocolRepository,
    FileForcingRepository,
    LocalObjectStore,
]:
    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding()
    base_producer, backing = make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
        forecast_hours=forecast_hours,
    )
    repository = _FileBackedProtocolRepository(
        backing=backing,
        contract=_contract_from_file_seed(seed),
        store=store,
    )
    return (
        ForcingProducer(
            config=base_producer.config,
            repository=repository,
            object_store=store,
        ),
        repository,
        backing,
        store,
    )


def _all_file_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _ready_evidence_snapshot(
    tmp_path: Path,
) -> tuple[ForcingProducer, FileForcingRepository, LocalObjectStore, dict[str, bytes]]:
    producer, _, backing, store = _prepared_direct_file_producer(tmp_path)
    assert (
        producer.produce(
            source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model"
        ).status
        == "forcing_ready"
    )
    snapshot = _all_file_bytes(tmp_path)
    for key in (
        _RECORD_KEY,
        _PACKAGE_MANIFEST_KEY,
        _DOMAIN_PACKAGE_KEY,
        _HANDOFF_KEY,
        _CYCLE_EVIDENCE_KEY,
    ):
        assert key in snapshot
    return producer, backing, store, snapshot


def _assert_contract_preflight_rejected(
    tmp_path: Path, contract: DirectGridForcingContract
) -> None:
    repository = _PreflightSentinelRepository(contract=contract)
    producer = _producer_for_preflight(tmp_path, repository=repository)

    with pytest.raises(
        ForcingProductionError, match="Invalid forcing mapping contract"
    ):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")

    assert repository.calls == [
        "resolve_model_identity",
        "load_forcing_mapping_contract",
    ]


# Request boundary -------------------------------------------------------------


@pytest.mark.parametrize(
    ("model_id", "basin_version_id"),
    ((".", None), ("safe_model", ".")),
    ids=("model-dot", "public-basin-dot"),
)
def test_public_dot_path_identity_has_zero_repository_access(
    tmp_path: Path,
    model_id: str,
    basin_version_id: str | None,
) -> None:
    repository = _NoRepositoryAccess()
    producer = _producer_for_preflight(tmp_path, repository=repository)

    with pytest.raises(
        ForcingProductionError, match="Invalid forcing production request"
    ):
        producer.produce(
            source_id="gfs",
            cycle_time=CYCLE_00Z,
            model_id=model_id,
            basin_version_id=basin_version_id,
        )

    assert repository.calls == []


@pytest.mark.parametrize("basin_version_id", (".", 17), ids=("dot", "non-string"))
def test_repository_invalid_basin_stops_before_existing_lookup_and_preserves_ready_sibling(
    tmp_path: Path,
    basin_version_id: Any,
) -> None:
    ready_producer, _, store, before = _ready_evidence_snapshot(tmp_path)
    repository = _PreflightSentinelRepository(
        contract=_contract(),
        model_identity={
            "basin_id": "basin_a",
            "basin_version_id": basin_version_id,
            "river_network_version_id": "rivnet_v1",
        },
    )
    producer = ForcingProducer(
        config=ready_producer.config,
        repository=repository,
        object_store=store,
    )

    with pytest.raises(ForcingProductionError, match="invalid basin_version_id"):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")

    assert repository.calls == ["resolve_model_identity"]
    assert _all_file_bytes(tmp_path) == before


def test_public_dot_identities_preserve_former_collision_sibling_bytes(
    tmp_path: Path,
) -> None:
    """The former A/B path collision has one physical package directory."""

    store = LocalObjectStore(tmp_path)
    seed = direct_grid_binding()
    base_producer, backing = make_file_producer(tmp_path, seed=seed, store=store)
    canonical_products_for_cycle(
        store,
        source_id="gfs",
        cycle_text=CYCLE_00Z,
        grid_id=GFS_GRID_ID,
        grid_signature=DEFAULT_GRID_SIGNATURE,
    )
    registry = json.loads(store.read_bytes("models/demo/registry.json"))
    common_profile = registry["models"][0]["resource_profile"]
    registry["models"] = [
        {
            "model_id": ".",
            "basin_id": "basin_a",
            "basin_version_id": "basin_v1",
            "river_network_version_id": "rivnet_v1",
            "model_package_uri": "models/demo/package",
            "resource_profile": common_profile,
        },
        {
            "model_id": "basin_v1",
            "basin_id": "basin_b",
            "basin_version_id": ".",
            "river_network_version_id": "rivnet_v1",
            "model_package_uri": "models/demo/package",
            "resource_profile": common_profile,
        },
    ]
    store.write_bytes_atomic("models/demo/registry.json", json.dumps(registry).encode())
    backing._registry_cache = None

    # These literal A/B prefixes formerly collapsed to the same filesystem path.
    a_prefix = "forcing/gfs/2026050700/basin_v1/."
    b_prefix = "forcing/gfs/2026050700/./basin_v1"
    assert (tmp_path / a_prefix).resolve() == (tmp_path / b_prefix).resolve()
    for name, content in {
        "forcing_version_record.json": b"b-record",
        "forcing_package.json": b"b-package",
        "forcing_domain_package.json": b"b-domain",
    }.items():
        store.write_bytes_atomic(f"{b_prefix}/{name}", content)
    handoff_key = "runs/fcst_gfs_2026050700_basin_v1/input/forcing_domain_handoff.json"
    cycle_evidence_key = (
        "runs/fcst_gfs_2026050700_basin_v1/input/cycle_ready_evidence.json"
    )
    store.write_bytes_atomic(handoff_key, b"b-handoff")
    sibling_record = {
        "forcing_version_id": "forc_gfs_2026050700_basin_v1",
        "model_id": "basin_v1",
        "source_id": "gfs",
        "cycle_time": datetime(2026, 5, 7, tzinfo=UTC),
        "forcing_package_uri": f"{b_prefix}/",
        "checksum": "b-ready",
    }
    backing.upsert_forcing_version(sibling_record)
    repository = _FileBackedProtocolRepository(
        backing=backing,
        contract=_contract_from_file_seed(seed),
        store=store,
        cycle_evidence_key=cycle_evidence_key,
    )
    repository.update_forecast_cycle(
        source_id="gfs",
        cycle_time=datetime(2026, 5, 7, tzinfo=UTC),
        status="forcing_ready",
    )
    producer = ForcingProducer(
        config=base_producer.config,
        repository=repository,
        object_store=store,
    )
    before = _all_file_bytes(tmp_path)
    repository.calls.clear()

    with pytest.raises(
        ForcingProductionError, match="Invalid forcing production request"
    ):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id=".")
    with pytest.raises(
        ForcingProductionError, match="Invalid forcing production request"
    ):
        producer.produce(
            source_id="gfs",
            cycle_time=CYCLE_00Z,
            model_id="safe_model",
            basin_version_id=".",
        )
    with pytest.raises(ForcingProductionError, match="invalid basin_version_id"):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="basin_v1")

    assert repository.calls == ["resolve_model_identity"]
    assert _all_file_bytes(tmp_path) == before
    assert (
        backing.get_forcing_version(
            source_id="gfs",
            cycle_time=datetime(2026, 5, 7, tzinfo=UTC),
            model_id="basin_v1",
        )
        == sibling_record
    )


@pytest.mark.parametrize("invalid_value", ("3", True, 3.0, -1))
def test_invalid_max_lead_has_zero_repository_access_and_preserves_sibling_bytes(
    tmp_path: Path, invalid_value: Any
) -> None:
    ready_producer, _, store, before = _ready_evidence_snapshot(tmp_path)
    no_access = _NoRepositoryAccess()
    producer = ForcingProducer(
        config=ready_producer.config,
        repository=no_access,
        object_store=store,
    )

    with pytest.raises(
        ForcingProductionError, match="Invalid forcing production request"
    ):
        producer.produce(
            source_id="gfs",
            cycle_time=CYCLE_00Z,
            model_id="other_model",
            max_lead_hours=invalid_value,
        )

    assert no_access.calls == []
    assert _all_file_bytes(tmp_path) == before


@pytest.mark.parametrize(
    ("max_lead_hours", "expected_available_max", "includes_f003"),
    ((0, 0, False), (3, 3, True), (999999, 3, True)),
)
def test_valid_max_lead_keeps_available_lead_window_without_upper_cap(
    tmp_path: Path,
    max_lead_hours: int,
    expected_available_max: int,
    includes_f003: bool,
) -> None:
    producer, _, _, store = _prepared_direct_file_producer(
        tmp_path, forecast_hours=(0, 3)
    )

    result = producer.produce(
        source_id="gfs",
        cycle_time=CYCLE_00Z,
        model_id="demo_model",
        max_lead_hours=max_lead_hours,
    )

    assert result.status == "forcing_ready"
    manifest = json.loads(store.read_bytes(_PACKAGE_MANIFEST_KEY))
    assert manifest["lineage"]["min_lead_hours"] == 0
    assert manifest["lineage"]["max_lead_hours"] == expected_available_max
    canonical_product_ids = manifest["lineage"]["canonical_product_ids"]
    assert (
        "gfs_2026050700_prcp_rate_or_amount_f003" in canonical_product_ids
    ) is includes_f003


# Repository-returned contract boundary ---------------------------------------


@pytest.mark.parametrize(
    "contract",
    (
        _contract(forcing_mapping_mode="idw"),
        _contract(forcing_mapping_mode="nearest_grid"),
        _contract(applicable_source_ids=()),
        _contract(applicable_source_ids=("ifs",)),
        _contract(applicable_source_ids=("gfs", "ifs")),
        _contract(applicable_source_ids=("unknown",)),
    ),
    ids=(
        "idw",
        "unknown-mode",
        "empty-source",
        "foreign-source",
        "multi-source",
        "unknown-source",
    ),
)
def test_direct_constructor_mode_and_source_fail_before_existing_lookup(
    tmp_path: Path, contract: DirectGridForcingContract
) -> None:
    _assert_contract_preflight_rejected(tmp_path, contract)


def test_direct_constructor_uppercase_current_source_singleton_is_accepted() -> None:
    validate_direct_grid_forcing_contract(
        _contract(applicable_source_ids=("GFS",)),
        source_id="gfs",
    )


@pytest.mark.parametrize(
    "contract",
    tuple(
        _contract(**{field: value})
        for field in (
            "binding_uri",
            "binding_checksum",
            "model_input_package_id",
            "sp_att_path",
            "sp_att_checksum",
            "grid_id",
            "grid_signature",
        )
        for value in ("", 7)
    ),
    ids=tuple(
        f"{field}-{'blank' if value == '' else 'non-string'}"
        for field in (
            "binding_uri",
            "binding_checksum",
            "model_input_package_id",
            "sp_att_path",
            "sp_att_checksum",
            "grid_id",
            "grid_signature",
        )
        for value in ("", 7)
    ),
)
def test_direct_constructor_rejects_blank_or_non_string_top_identities(
    tmp_path: Path, contract: DirectGridForcingContract
) -> None:
    _assert_contract_preflight_rejected(tmp_path, contract)


def test_direct_constructor_rejects_empty_oversize_or_untyped_station_sequences(
    tmp_path: Path,
) -> None:
    _assert_contract_preflight_rejected(tmp_path, _contract(stations=()))
    _assert_contract_preflight_rejected(tmp_path, _contract(stations=({"station": 1},)))
    too_many = tuple(
        _station(
            index=index,
            station_id=f"forc_{index:05d}",
            filename=f"X{index}.csv",
            grid_cell_id=str(index),
        )
        for index in range(1, 10_002)
    )
    _assert_contract_preflight_rejected(tmp_path, _contract(stations=too_many))


@pytest.mark.parametrize(
    "contract",
    tuple(
        _contract(stations=(_station(**{field: value}),))
        for field in ("station_id", "grid_id", "grid_cell_id")
        for value in ("", 9)
    ),
    ids=tuple(
        f"{field}-{'blank' if value == '' else 'non-string'}"
        for field in ("station_id", "grid_id", "grid_cell_id")
        for value in ("", 9)
    ),
)
def test_direct_constructor_rejects_blank_or_non_string_station_identities(
    tmp_path: Path, contract: DirectGridForcingContract
) -> None:
    _assert_contract_preflight_rejected(tmp_path, contract)


@pytest.mark.parametrize(
    "contract",
    (
        _contract(stations=(_station(index=True),)),
        _contract(stations=(_station(index=0),)),
        _contract(stations=(_station(index=-1),)),
        _contract(
            stations=(
                _station(index=1),
                _station(
                    index=1, station_id="forc_002", filename="X2.csv", grid_cell_id="1"
                ),
            )
        ),
        _contract(
            stations=(
                _station(index=1),
                _station(
                    index=3, station_id="forc_002", filename="X2.csv", grid_cell_id="1"
                ),
            )
        ),
    ),
    ids=("bool", "zero", "negative", "duplicate", "gapped"),
)
def test_direct_constructor_rejects_non_contiguous_strict_forcing_indexes(
    tmp_path: Path, contract: DirectGridForcingContract
) -> None:
    _assert_contract_preflight_rejected(tmp_path, contract)


@pytest.mark.parametrize(
    "contract",
    (
        _contract(stations=(_station(filename="../escape.csv"),)),
        _contract(stations=(_station(filename="not-a-csv.txt"),)),
        _contract(
            stations=(
                _station(filename="X1.csv"),
                _station(
                    index=2, station_id="forc_002", filename="x1.csv", grid_cell_id="1"
                ),
            )
        ),
    ),
    ids=("traversal", "wrong-suffix", "casefold-collision"),
)
def test_direct_constructor_rejects_unsafe_or_casefold_colliding_filenames(
    tmp_path: Path, contract: DirectGridForcingContract
) -> None:
    _assert_contract_preflight_rejected(tmp_path, contract)


@pytest.mark.parametrize(
    "contract",
    (
        _contract(stations=(_station(grid_id="foreign_grid"),)),
        _contract(
            stations=(
                _station(),
                _station(index=2, filename="X2.csv", grid_cell_id="1"),
            )
        ),
        _contract(
            stations=(
                _station(),
                _station(index=2, station_id="forc_002", filename="X2.csv"),
            )
        ),
    ),
    ids=("station-grid-mismatch", "duplicate-station-id", "duplicate-grid-cell"),
)
def test_direct_constructor_rejects_station_grid_and_one_to_one_aliases(
    tmp_path: Path, contract: DirectGridForcingContract
) -> None:
    _assert_contract_preflight_rejected(tmp_path, contract)


@pytest.mark.parametrize(
    "contract",
    (
        _contract(stations=(_station(longitude=math.nan),)),
        _contract(stations=(_station(longitude=math.inf),)),
        _contract(stations=(_station(longitude=True),)),
        _contract(stations=(_station(longitude=180.0),)),
        _contract(stations=(_station(latitude=91.0),)),
        _contract(stations=(_station(latitude=True),)),
        _contract(stations=(_station(x=math.nan),)),
        _contract(stations=(_station(x=True),)),
        _contract(stations=(_station(y=math.inf),)),
        _contract(stations=(_station(y=True),)),
        _contract(stations=(_station(z=math.nan),)),
        _contract(stations=(_station(z=True),)),
    ),
    ids=(
        "longitude-nan",
        "longitude-inf",
        "longitude-bool",
        "longitude-noncanonical",
        "latitude-outside",
        "latitude-bool",
        "x-nan",
        "x-bool",
        "y-inf",
        "y-bool",
        "z-nan",
        "z-bool",
    ),
)
def test_direct_constructor_rejects_noncanonical_or_nonfinite_geometry(
    tmp_path: Path, contract: DirectGridForcingContract
) -> None:
    _assert_contract_preflight_rejected(tmp_path, contract)


def test_direct_constructor_rejects_non_mapping_properties_before_existing_lookup(
    tmp_path: Path,
) -> None:
    _assert_contract_preflight_rejected(
        tmp_path,
        _contract(stations=(_station(properties=[("not", "a mapping")]),)),
    )


def test_valid_direct_constructor_reaches_file_backed_successful_production(
    tmp_path: Path,
) -> None:
    producer, repository, backing, store = _prepared_direct_file_producer(tmp_path)

    result = producer.produce(
        source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model"
    )

    assert result.status == "forcing_ready"
    assert result.station_count == 2
    assert repository.calls[:3] == [
        "resolve_model_identity",
        "load_forcing_mapping_contract",
        "get_forcing_version",
    ]
    assert (
        backing.get_forcing_version(
            source_id="gfs",
            cycle_time=datetime(2026, 5, 7, tzinfo=UTC),
            model_id="demo_model",
        )["checksum"]
        == result.checksum
    )
    assert store.read_bytes(_CYCLE_EVIDENCE_KEY)


def test_parser_keeps_source_less_multi_source_shape_but_shared_validator_rejects_semantic_aliases() -> (
    None
):
    manifest = direct_grid_binding(applicable_source_ids=("GFS", "IFS"))
    parsed = parse_direct_grid_forcing_contract(manifest)
    assert parsed.applicable_source_ids == ("gfs", "ifs")

    duplicate = dict(manifest["station_bindings"][1])
    duplicate["grid_cell_id"] = "0"
    manifest["station_bindings"][1] = duplicate
    with pytest.raises(
        DirectGridContractError, match="grid_cell_id values must be unique"
    ):
        parse_direct_grid_forcing_contract(manifest)


def test_producer_rejects_directly_constructed_multi_source_contract(
    tmp_path: Path,
) -> None:
    _assert_contract_preflight_rejected(
        tmp_path,
        _contract(applicable_source_ids=("gfs", "ifs")),
    )


# Authoritative drift remains after existing lookup ---------------------------


@pytest.mark.parametrize("drift_kind", ("binding", "grid"))
def test_authoritative_drift_after_ready_revokes_stale_file_evidence(
    tmp_path: Path, drift_kind: str
) -> None:
    producer, repository, backing, store = _prepared_direct_file_producer(tmp_path)
    ready = producer.produce(
        source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model"
    )
    assert ready.status == "forcing_ready"
    assert store.exists(_RECORD_KEY)
    assert store.exists(_HANDOFF_KEY)
    repository.calls.clear()

    if drift_kind == "binding":
        store.write_bytes_atomic("models/demo/binding.json", b"authoritative drift")
        expected_error = "binding_checksum mismatch"
    else:
        grid_key = "canonical/gfs/grid/gfs_0p25/grid.json"
        grid = json.loads(store.read_bytes(grid_key))
        grid["cells"][0]["longitude"] = -74.75
        store.write_bytes_atomic(grid_key, json.dumps(grid).encode("utf-8"))
        expected_error = "grid_signature mismatch"

    with pytest.raises(ForcingProductionError, match=expected_error):
        producer.produce(source_id="gfs", cycle_time=CYCLE_00Z, model_id="demo_model")

    assert repository.calls[:3] == [
        "resolve_model_identity",
        "load_forcing_mapping_contract",
        "get_forcing_version",
    ]
    record = backing.get_forcing_version(
        source_id="gfs",
        cycle_time=datetime(2026, 5, 7, tzinfo=UTC),
        model_id="demo_model",
    )
    assert record is not None
    assert record["checksum"] is None
    assert not store.exists(_RECORD_KEY)
    assert not store.exists(_HANDOFF_KEY)
    assert (
        json.loads(store.read_bytes(_CYCLE_EVIDENCE_KEY))["status"] == "failed_forcing"
    )
