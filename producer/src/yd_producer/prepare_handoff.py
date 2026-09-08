"""Validated immutable prepared-variant direct-grid handoff (#171)."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from yd_producer.forcing.bounded_json import BoundedJSONError, load_bounded_json
from yd_producer.forcing.direct_grid_contract import (
    REQUIRED_STATION_FIELDS,
    DirectGridContractError,
    DirectGridForcingContract,
    DirectGridStationBinding,
    parse_direct_grid_forcing_contract,
    validate_direct_grid_forcing_contract,
)
from yd_producer.raw.source_identity import normalize_source_id
from yd_producer.store.object_store import MAX_OBJECT_MANIFEST_BYTES
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    directory_identity_no_follow,
    list_directory_no_follow_limited,
    read_bytes_limited_no_follow,
)

__all__ = [
    "MAX_PREPARED_VARIANT_ASSET_BYTES",
    "MAX_PREPARED_VARIANT_MANIFEST_BYTES",
    "PREPARED_VARIANT_BINDING_FILENAME",
    "PREPARED_VARIANT_CALIBRATED_STATE_FILENAME",
    "PREPARED_VARIANT_HANDOFF_FILENAME",
    "PREPARED_VARIANT_HANDOFF_SCHEMA",
    "PREPARED_VARIANT_PARAMETER_FILENAME",
    "PreparedVariantHandoff",
    "PreparedVariantHandoffError",
    "load_prepared_variant_handoff",
]

PREPARED_VARIANT_CALIBRATED_STATE_FILENAME = "yd.cfg.ic"
PREPARED_VARIANT_PARAMETER_FILENAME = "yd.para"
PREPARED_VARIANT_BINDING_FILENAME = "yd.binding"
PREPARED_VARIANT_HANDOFF_FILENAME = "yd.direct-grid-handoff.json"
PREPARED_VARIANT_HANDOFF_SCHEMA = "yd.prepare.direct-grid-handoff.v1"
MAX_PREPARED_VARIANT_MANIFEST_BYTES = MAX_OBJECT_MANIFEST_BYTES
MAX_PREPARED_VARIANT_ASSET_BYTES = MAX_OBJECT_MANIFEST_BYTES

_ENVELOPE_KEYS = frozenset(
    {
        "schema_version",
        "source_id",
        "project_name",
        "model_id",
        "basin_id",
        "basin_version_id",
        "river_network_version_id",
        "direct_grid_forcing_contract",
        "sp_att_asset_name",
    }
)
_CONTRACT_KEYS = frozenset(
    {
        "forcing_mapping_mode",
        "binding_uri",
        "binding_checksum",
        "model_input_package_id",
        "sp_att_path",
        "sp_att_checksum",
        "applicable_source_ids",
        "grid_id",
        "grid_signature",
        "station_bindings",
    }
)
_FIXED_FILENAMES = frozenset(
    {
        PREPARED_VARIANT_CALIBRATED_STATE_FILENAME,
        PREPARED_VARIANT_PARAMETER_FILENAME,
        PREPARED_VARIANT_BINDING_FILENAME,
        PREPARED_VARIANT_HANDOFF_FILENAME,
    }
)
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CHECKSUM = re.compile(r"^sha256:[0-9a-f]{64}$")


class PreparedVariantHandoffError(ValueError):
    """A prepared variant is malformed, unsafe, or not current-caller bound."""


@dataclass(frozen=True, kw_only=True)
class PreparedVariantHandoff:
    source_id: str
    project_name: str
    model_id: str
    basin_id: str
    basin_version_id: str
    river_network_version_id: str
    contract: DirectGridForcingContract
    sp_att_asset_name: str
    binding_content: bytes
    sp_att_content: bytes


def load_prepared_variant_handoff(
    *,
    variant_root: Path | str,
    source_id: str,
    project_name: str,
    grid_id: str,
    max_manifest_bytes: int,
    max_asset_bytes: int,
) -> PreparedVariantHandoff:
    """Load exactly one canonical v1 carrier without discovering any candidate paths."""
    try:
        root, source, project, grid = _preflight(
            variant_root,
            source_id,
            project_name,
            grid_id,
            max_manifest_bytes,
            max_asset_bytes,
        )
        expected_identity = directory_identity_no_follow(root)
        initial_names = _entries(root)
        if len(initial_names) != 5:
            raise ValueError(
                "prepared variant must contain exactly five entries; "
                f"missing fixed files={sorted(_FIXED_FILENAMES - set(initial_names))!r}."
            )

        manifest_content = _read(
            root, PREPARED_VARIANT_HANDOFF_FILENAME, max_manifest_bytes
        )
        manifest = _canonical_object(manifest_content, max_manifest_bytes)
        _exact_keys(manifest, _ENVELOPE_KEYS, "handoff manifest")
        _manifest_fields(manifest, source, project)
        asset_name = _asset_name(manifest["sp_att_asset_name"])
        expected_entries = _FIXED_FILENAMES | {asset_name}
        if set(initial_names) != expected_entries:
            raise ValueError(
                "prepared variant entries do not equal the v1 exact five-entry set."
            )

        contract_payload = _contract_shape(manifest["direct_grid_forcing_contract"])
        identifiers = {
            field: _identifier(manifest[field], field)
            for field in (
                "model_id",
                "basin_id",
                "basin_version_id",
                "river_network_version_id",
            )
        }
        _contract_binding(
            contract_payload, source, project, grid, identifiers["model_id"]
        )
        for name in (
            PREPARED_VARIANT_CALIBRATED_STATE_FILENAME,
            PREPARED_VARIANT_PARAMETER_FILENAME,
        ):
            _read(root, name, max_asset_bytes)
        binding = _read(root, PREPARED_VARIANT_BINDING_FILENAME, max_asset_bytes)
        sp_att = _read(root, asset_name, max_asset_bytes)
        try:
            sp_att.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("prepared .sp.att asset must be UTF-8.") from error
        _checksum(contract_payload["binding_checksum"], binding, "binding")
        _checksum(contract_payload["sp_att_checksum"], sp_att, ".sp.att")

        contract = parse_direct_grid_forcing_contract(
            contract_payload, source_id=source
        )
        frozen_contract = _freeze_contract(contract)
        validate_direct_grid_forcing_contract(frozen_contract, source_id=source)
        _coherent_root(root, expected_identity, expected_entries)
        return PreparedVariantHandoff(
            source_id=source,
            project_name=project,
            model_id=identifiers["model_id"],
            basin_id=identifiers["basin_id"],
            basin_version_id=identifiers["basin_version_id"],
            river_network_version_id=identifiers["river_network_version_id"],
            contract=frozen_contract,
            sp_att_asset_name=asset_name,
            binding_content=bytes(binding),
            sp_att_content=bytes(sp_att),
        )
    except PreparedVariantHandoffError:
        raise
    except (
        BoundedJSONError,
        DirectGridContractError,
        SafeFilesystemError,
        OSError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise PreparedVariantHandoffError(
            f"invalid prepared variant handoff: {error}"
        ) from error


def _preflight(
    variant_root: Path | str,
    source_id: str,
    project_name: str,
    grid_id: str,
    max_manifest_bytes: int,
    max_asset_bytes: int,
) -> tuple[Path, str, str, str]:
    if not isinstance(variant_root, Path | str):
        raise TypeError("variant_root must be an absolute path.")
    root = Path(variant_root)
    if not root.is_absolute():
        raise ValueError("variant_root must be an absolute path.")
    for name, value in (
        ("max_manifest_bytes", max_manifest_bytes),
        ("max_asset_bytes", max_asset_bytes),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a strict positive integer.")
    source_text = _text(source_id, "source_id")
    try:
        source = normalize_source_id(source_text)
    except (AttributeError, ValueError) as error:
        raise ValueError("source_id must be gfs or ifs.") from error
    project = _identifier(project_name, "project_name")
    grid = _text(grid_id, "grid_id")
    return root, source, project, grid


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string.")
    return value


def _identifier(value: Any, label: str) -> str:
    text = _text(value, label)
    if ".." in text or _COMPONENT.fullmatch(text) is None:
        raise ValueError(f"{label} must be a safe ASCII component.")
    return text


def _entries(root: Path) -> list[str]:
    names = list_directory_no_follow_limited(root, max_entries=5, containment_root=root)
    if len(names) > 5:
        raise ValueError(
            f"prepared variant 未预期条目 / exceeds five-entry limit: {sorted(names)!r}."
        )
    return names


def _read(root: Path, name: str, max_bytes: int) -> bytes:
    content = read_bytes_limited_no_follow(
        root / name, max_bytes=max_bytes, containment_root=root
    )
    if len(content) > max_bytes:
        raise ValueError(f"{name} exceeds its {max_bytes} byte limit.")
    return content


def _canonical_object(content: bytes, max_bytes: int) -> dict[str, Any]:
    # Canonical byte equality also rejects duplicate keys and non-standard constants.
    value = load_bounded_json(content, max_bytes=max_bytes)
    if not isinstance(value, dict):
        raise TypeError("handoff manifest must be a JSON object.")
    canonical = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if content != canonical:
        raise ValueError("handoff manifest is not canonical JSON bytes.")
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} keys must be exact; missing={sorted(expected - actual)!r}, "
            f"unknown={sorted(actual - expected)!r}."
        )


def _manifest_fields(manifest: Mapping[str, Any], source: str, project: str) -> None:
    if manifest["schema_version"] != PREPARED_VARIANT_HANDOFF_SCHEMA:
        raise ValueError("handoff manifest schema_version is unsupported.")
    if not isinstance(manifest["schema_version"], str):
        raise TypeError("handoff manifest schema_version must be a string.")
    declared_source = _text(manifest["source_id"], "manifest source_id")
    if declared_source != source:
        raise ValueError(
            "handoff manifest source_id does not match the current source."
        )
    declared_project = _identifier(manifest["project_name"], "manifest project_name")
    if declared_project != project:
        raise ValueError(
            "handoff manifest project_name does not match the current project."
        )


def _asset_name(value: Any) -> str:
    name = _identifier(value, "sp_att_asset_name")
    if not name.endswith(".sp.att") or name in _FIXED_FILENAMES:
        raise ValueError("sp_att_asset_name must name a non-fixed .sp.att leaf.")
    return name


def _contract_shape(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("direct_grid_forcing_contract must be a JSON object.")
    _exact_keys(value, _CONTRACT_KEYS, "direct_grid_forcing_contract")
    stations = value["station_bindings"]
    if not isinstance(stations, list):
        raise TypeError("station_bindings must be a JSON list.")
    for index, station in enumerate(stations):
        if not isinstance(station, dict):
            raise TypeError(f"station_bindings[{index}] must be a JSON object.")
        _exact_keys(
            station, frozenset(REQUIRED_STATION_FIELDS), f"station_bindings[{index}]"
        )
    return value


def _contract_binding(
    contract: Mapping[str, Any], source: str, project: str, grid: str, model_id: str
) -> None:
    if contract["applicable_source_ids"] != [source]:
        raise ValueError("direct-grid contract must have the current-source singleton.")
    if contract["grid_id"] != grid:
        raise ValueError(
            "direct-grid contract grid_id does not match the current grid."
        )
    if contract["binding_uri"] != f"models/{model_id}/direct-grid/binding.json":
        raise ValueError("direct-grid binding_uri is not the D11 work-local key.")
    if contract["sp_att_path"] != f"input/{project}.sp.att":
        raise ValueError("direct-grid sp_att_path is not the D11 work-local key.")
    for field in ("binding_checksum", "sp_att_checksum"):
        if (
            not isinstance(contract[field], str)
            or _CHECKSUM.fullmatch(contract[field]) is None
        ):
            raise ValueError(f"{field} must be sha256:<64 lowercase hex>.")


def _checksum(expected: str, content: bytes, label: str) -> None:
    actual = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if expected != actual:
        raise ValueError(f"{label} checksum does not match exact bytes.")


def _freeze_contract(contract: DirectGridForcingContract) -> DirectGridForcingContract:
    stations = tuple(
        DirectGridStationBinding(
            station_id=str(station.station_id),
            shud_forcing_index=int(station.shud_forcing_index),
            forcing_filename=str(station.forcing_filename),
            longitude=float(station.longitude),
            latitude=float(station.latitude),
            x=float(station.x),
            y=float(station.y),
            z=float(station.z),
            grid_id=str(station.grid_id),
            grid_cell_id=str(station.grid_cell_id),
            properties=MappingProxyType({}),
        )
        for station in contract.stations
    )
    return DirectGridForcingContract(
        forcing_mapping_mode=str(contract.forcing_mapping_mode),
        binding_uri=str(contract.binding_uri),
        binding_checksum=str(contract.binding_checksum),
        model_input_package_id=str(contract.model_input_package_id),
        sp_att_path=str(contract.sp_att_path),
        sp_att_checksum=str(contract.sp_att_checksum),
        applicable_source_ids=tuple(
            str(item) for item in contract.applicable_source_ids
        ),
        grid_id=str(contract.grid_id),
        grid_signature=str(contract.grid_signature),
        stations=stations,
    )


def _coherent_root(
    root: Path, identity: tuple[int, int], expected_entries: frozenset[str]
) -> None:
    if directory_identity_no_follow(root) != identity:
        raise ValueError("prepared variant root identity changed during loading.")
    if set(_entries(root)) != expected_entries:
        raise ValueError("prepared variant entry set changed during loading.")
