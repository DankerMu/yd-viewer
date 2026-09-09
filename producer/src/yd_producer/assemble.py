"""Work-local direct-grid registry staging and SHUD input assembly."""

from __future__ import annotations

import math
import re
import stat
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, NamedTuple, NoReturn

from yd_producer import _assemble_fs
from yd_producer.forcing import DirectGridForcingContract, ForcingProductionResult
from yd_producer.forcing.bounded_json import BoundedJSONError, load_bounded_json
from yd_producer.forcing.direct_grid_contract import (
    MAX_DIRECT_GRID_STATION_BINDINGS,
    DirectGridContractError,
    validate_direct_grid_forcing_contract,
)
from yd_producer.forcing.file_store import FileForcingRepository, ForcingStoreError
from yd_producer.forcing.shud_forcing_contract import (
    SHUD_FORCING_INDEX_MEMBERS,
    SHUD_FORCING_ROLE,
)
from yd_producer.raw.source_identity import normalize_source_id
from yd_producer.state import MAX_STATE_IC_BYTES, cfg_ic_header_minute_time, parse
from yd_producer.store.object_store import (
    MAX_OBJECT_MANIFEST_BYTES,
    LocalObjectStore,
    ObjectStoreError,
)
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    list_directory_no_follow,
    stat_no_follow,
)

_fs = _assemble_fs
AssemblyPhase = Literal[
    "validate",
    "registry-stage",
    "registry-commit",
    "assemble-stage",
    "assemble-commit",
    "cleanup",
]
_FIELDS = "model_id basin_id basin_version_id river_network_version_id project_name"
_IDENTITY_KEYS = "model_id basin_id basin_version_id river_network_version_id"
_CSV = re.compile(r"^[A-Za-z0-9_.-]+\.csv$")
_ASSIGNMENT = re.compile(
    r"^(?P<prefix>[ \t]*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<equals>[ \t]*=[ \t]*)(?P<value>[^\r\n]*)(?P<ending>\r?\n)?$"
)
_PARAMETERS = {
    "START": "0",
    "END": "7",
    "DT_QR_DOWN": "60",
    "Update_IC_STEP": "720",
    "BINARY_OUTPUT": "1",
    "ASCII_OUTPUT": "0",
}


class AssemblyError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        phase: AssemblyPhase,
        path: Path | None = None,
        cleanup_warnings: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.path = path
        self.cleanup_warnings = tuple(cleanup_warnings)


@dataclass(frozen=True, kw_only=True)
class WorkIdentity:
    source_id: str
    cycle_time: datetime
    model_id: str
    basin_id: str
    basin_version_id: str
    river_network_version_id: str
    project_name: str

    def __post_init__(self) -> None:
        try:
            _identity(self)
        except (TypeError, ValueError) as error:
            raise _error("Invalid WorkIdentity", "validate", cause=error) from error


@dataclass(frozen=True, kw_only=True)
class WorkRegistry:
    identity: WorkIdentity
    work_dir: Path
    object_store_root: Path
    registry_manifest: str
    model_package_uri: str
    model_manifest_uri: str
    cleanup_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            _registry_layout(self)
        except AssemblyError:
            raise
        except (OSError, SafeFilesystemError, TypeError, ValueError) as error:
            raise _error("Invalid WorkRegistry", "validate", cause=error) from error


@dataclass(frozen=True, kw_only=True)
class RunDirectory:
    identity: WorkIdentity
    path: Path
    project_name: str
    state_path: Path
    parameter_path: Path
    forcing_index_path: Path
    forcing_csv_paths: tuple[Path, ...]
    cleanup_warnings: tuple[str, ...] = ()


class _AssemblyInputs(NamedTuple):
    variant_root: Path
    states_root: Path
    state_path: Path
    variant_dirs: Sequence[Path]
    variant_files: Sequence[tuple[Path, Path, str | None]]
    parameter_check: tuple[int, str | None]
    state_check: tuple[int, str | None]
    containment_root: Path | None
    expected_contract: DirectGridForcingContract | None


def rename_entry_no_follow(*args: Any, **kwargs: Any) -> None:
    _fs.rename_entry_no_follow(*args, **kwargs)


def stage_work_registry(
    *,
    work_root: Path | str,
    identity: WorkIdentity,
    contract: DirectGridForcingContract,
    binding_content: bytes | bytearray | memoryview,
    sp_att_content: bytes | bytearray | memoryview,
    max_asset_bytes: int,
) -> WorkRegistry:
    try:
        work = _work(work_root, identity)
        if type(max_asset_bytes) is not int or max_asset_bytes <= 0:
            raise ValueError("max_asset_bytes must be a strict positive integer.")
        if not isinstance(
            binding_content, bytes | bytearray | memoryview
        ) or not isinstance(sp_att_content, bytes | bytearray | memoryview):
            raise TypeError("binding_content and sp_att_content must be bytes-like.")
        binding, sp_att = bytes(binding_content), bytes(sp_att_content)
        if len(binding) > max_asset_bytes or len(sp_att) > max_asset_bytes:
            raise ValueError("direct-grid asset exceeds max_asset_bytes.")
        sp_att.decode("utf-8")
        validate_direct_grid_forcing_contract(contract, source_id=identity.source_id)
        _contract_paths(identity, contract)
        _fs.checksum(contract.binding_checksum, binding, "binding")
        _fs.checksum(contract.sp_att_checksum, sp_att, "sp_att")
        _fs.json_bytes(_contract_payload(contract))
        object_root = work / "object-store"
        parent = object_root / "models"
        try:
            _fs.directory(parent, work, create=False)
            _fs.absent(parent, identity.model_id, work)
        except FileNotFoundError:
            pass
    except (
        OSError,
        SafeFilesystemError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        DirectGridContractError,
        ForcingStoreError,
        ObjectStoreError,
    ) as error:
        raise _error("Invalid work registry input", "validate", cause=error) from error
    outer = work / f".{identity.model_id}.registry-stage-{uuid.uuid4().hex}"
    staged_store = outer / "object-store"
    staged_model = staged_store / "models" / identity.model_id
    try:
        _fs.absent(work, outer.name, work)
    except (OSError, SafeFilesystemError, ValueError) as error:
        raise _error(
            "Failed to stage work registry", "registry-stage", path=outer, cause=error
        ) from error
    try:
        _fs.directory(staged_model, work, create=True)
        _write_registry(staged_model, identity, contract, binding, sp_att, work)
        _repository(staged_store, identity, contract, binding, sp_att)
    except Exception as error:  # noqa: BLE001
        _abort(
            "Failed to stage work registry", "registry-stage", outer, work, outer, error
        )
    final = parent / identity.model_id
    try:
        _fs.directory(parent, work, create=True)
        _commit_probe(parent, identity.model_id, work, "registry final")
        _fs.rename(
            staged_model.parent,
            identity.model_id,
            parent,
            identity.model_id,
            work,
            operation=rename_entry_no_follow,
        )
    except Exception as error:  # noqa: BLE001
        _abort(
            "Failed to commit work registry",
            "registry-commit",
            final,
            work,
            outer,
            error,
        )
    return WorkRegistry(
        identity=identity,
        work_dir=work,
        object_store_root=object_root,
        registry_manifest=f"models/{identity.model_id}/registry.json",
        model_package_uri=f"models/{identity.model_id}/package",
        model_manifest_uri=f"models/{identity.model_id}/manifest.json",
        cleanup_warnings=_fs.clean(outer, work),
    )


def assemble(
    *,
    registry: WorkRegistry,
    variant_dir: Path | str,
    forcing: ForcingProductionResult,
    states_root: Path | str,
    state_path: Path | str,
) -> RunDirectory:
    try:
        _registry_layout(registry)
        identity = registry.identity
        variant = _fs.absolute(variant_dir, "variant_dir")
        states = _fs.absolute(states_root, "states_root")
        state = _fs.absolute(state_path, "state_path")
        work = registry.work_dir
        if variant.is_relative_to(work):
            raise ValueError("variant_dir must be outside this work tree.")
        if states.is_relative_to(work):
            raise ValueError("states_root must be outside this work tree.")
        dirs, discovered = _tree(variant, identity.project_name)
    except AssemblyError:
        raise
    except (OSError, SafeFilesystemError, TypeError, ValueError) as error:
        raise _error("Invalid SHUD assembly input", "validate", cause=error) from error
    return _assemble_kernel(
        registry,
        forcing,
        _AssemblyInputs(
            variant,
            states,
            state,
            dirs,
            tuple((relative, source, None) for relative, source in discovered),
            (MAX_OBJECT_MANIFEST_BYTES, None),
            (MAX_STATE_IC_BYTES, None),
            None,
            None,
        ),
    )


def _assemble_kernel(
    registry: WorkRegistry,
    forcing: ForcingProductionResult,
    inputs: _AssemblyInputs,
) -> RunDirectory:
    try:
        contract = _registry(registry)
        if (
            inputs.expected_contract is not None
            and contract != inputs.expected_contract
        ):
            raise ValueError("registry contract differs from prepared variant handoff.")
        identity, work = registry.identity, registry.work_dir
        project = identity.project_name
        root = inputs.containment_root or inputs.variant_root
        parameter = _fs.read_limited(
            inputs.variant_root / f"{project}.para",
            inputs.parameter_check[0],
            root,
        )
        if checksum := inputs.parameter_check[1]:
            _fs.checksum(checksum, parameter, "parameter")
        parameter = render_shud_parameters(parameter)
        warm_state = _state(
            inputs.state_path,
            inputs.states_root,
            identity,
            inputs.state_check,
            inputs.containment_root or inputs.states_root,
        )
        index, csvs = _forcing(registry, forcing)
        outputs = {_csv_path(entry["relative_path"]) for entry in csvs}
        outputs.add(f"{project}.tsd.forc")
        roots = {path.parts[0] for path, *_ in inputs.variant_files}
        if collision := sorted(roots & outputs):
            raise ValueError(
                f"variant/output filename collision: {', '.join(collision)}"
            )
        _fs.absent(work, "model", work)
    except AssemblyError:
        raise
    except (
        OSError,
        SafeFilesystemError,
        TypeError,
        ValueError,
        DirectGridContractError,
        ForcingStoreError,
        ObjectStoreError,
        BoundedJSONError,
    ) as error:
        raise _error("Invalid SHUD assembly input", "validate", cause=error) from error
    final = work / "model"
    stage = work / f".model.assemble-stage-{uuid.uuid4().hex}"
    try:
        _fs.absent(work, stage.name, work)
    except (OSError, SafeFilesystemError, ValueError) as error:
        raise _error(
            "Failed to stage SHUD run directory",
            "assemble-stage",
            path=stage,
            cause=error,
        ) from error
    try:
        _fs.directory(stage, work, create=True)
        skipped = {Path(f"{project}.{suffix}") for suffix in ("cfg.ic", "para")}
        for relative in inputs.variant_dirs:
            if relative != Path("."):
                _fs.directory(stage / relative, work, create=True)
        for relative, source, checksum in inputs.variant_files:
            if relative not in skipped:
                _fs.copy_regular(
                    source,
                    stage / relative,
                    root,
                    work,
                    expected_checksum=checksum,
                )
        for suffix, content in (("para", parameter), ("cfg.ic", warm_state)):
            _fs.write_new(stage / f"{project}.{suffix}", content, work)
        store = LocalObjectStore(registry.object_store_root)
        members = [(index, f"{project}.tsd.forc")]
        members += [(entry, _csv_path(entry["relative_path"])) for entry in csvs]
        for entry, name in members:
            _fs.copy_regular(
                store.resolve_path(str(entry["uri"])),
                stage / name,
                store.root,
                work,
                expected_checksum=str(entry["checksum"]),
            )
    except Exception as error:  # noqa: BLE001
        _abort(
            "Failed to stage SHUD run directory",
            "assemble-stage",
            stage,
            work,
            stage,
            error,
        )
    try:
        _commit_probe(work, "model", work, "run final")
        _fs.rename(
            work,
            stage.name,
            work,
            "model",
            work,
            operation=rename_entry_no_follow,
        )
    except Exception as error:  # noqa: BLE001
        _abort(
            "Failed to commit SHUD run directory",
            "assemble-commit",
            final,
            work,
            stage,
            error,
        )
    return RunDirectory(
        identity=identity,
        path=final,
        project_name=project,
        state_path=final / f"{project}.cfg.ic",
        parameter_path=final / f"{project}.para",
        forcing_index_path=final / f"{project}.tsd.forc",
        forcing_csv_paths=tuple(
            final / _csv_path(entry["relative_path"]) for entry in csvs
        ),
        cleanup_warnings=_fs.clean(stage, work),
    )


def render_shud_parameters(content: bytes, *, end: Literal["7", "0.5"] = "7") -> bytes:
    # 类型判据 MUST 先于成员判据：`not in {…}` 对 unhashable 实参（`[]`/`{}`/`["7"]`）抛裸 `TypeError`，把契约错误漏成第三种异常种类。
    if not isinstance(end, str) or end not in {"7", "0.5"}:
        raise AssemblyError("SHUD END must be '7' or '0.5'.", phase="validate")
    if not isinstance(content, bytes):
        raise AssemblyError("Parameter content must be bytes.", phase="validate")
    if len(content) > MAX_OBJECT_MANIFEST_BYTES:
        raise AssemblyError(
            f"Parameter content exceeds {MAX_OBJECT_MANIFEST_BYTES} bytes.",
            phase="validate",
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AssemblyError(
            "Parameter content must be strict UTF-8.", phase="validate"
        ) from error
    lines = text.splitlines(keepends=True)
    result = list(lines)
    ending = next(
        (
            "\r\n" if line.endswith("\r\n") else "\n"
            for line in lines
            if line.endswith(("\n", "\r"))
        ),
        "\n",
    )
    for key, value in (_PARAMETERS | {"END": end}).items():
        matches: list[tuple[int, str, re.Match[str]]] = []
        token = re.compile(rf"(?<![A-Za-z0-9_])\{{\{{{key}\}}\}}(?![A-Za-z0-9_])")
        shell = re.compile(rf"(?<![A-Za-z0-9_])\$\{{{key}\}}(?![A-Za-z0-9_])")
        for number, line in enumerate(result):
            visible = line.split("#", 1)[0]
            if not visible.strip():
                continue
            assignment = _ASSIGNMENT.fullmatch(line)
            if assignment is not None and assignment.group("key") == key:
                matches.append((number, "assignment", assignment))
                continue
            matches.extend((number, "token", item) for item in token.finditer(visible))
            matches.extend((number, "shell", item) for item in shell.finditer(visible))
        if len(matches) > 1:
            raise AssemblyError(
                f"SHUD parameter {key!r} has multiple authoritative occurrences.",
                phase="validate",
            )
        if not matches:
            if result and not result[-1].endswith(("\n", "\r")):
                result[-1] += ending
            result.append(f"{key} = {value}{ending}")
            continue
        number, kind, match = matches[0]
        line = result[number]
        if kind == "assignment":
            old = match.group("value")
            comment = ""
            if "#" in old:
                before, after = old.split("#", 1)
                comment = before[len(before.rstrip(" \t")) :] + "#" + after
            result[number] = (
                f"{match.group('prefix')}{key}{match.group('equals')}{value}{comment}{match.group('ending') or ''}"
            )
        else:
            result[number] = line[: match.start()] + value + line[match.end() :]
    return "".join(result).encode("utf-8")


def _identity(identity: WorkIdentity) -> None:
    if not isinstance(identity, WorkIdentity):
        raise TypeError("identity must be a WorkIdentity.")
    try:
        source = normalize_source_id(identity.source_id)
    except AttributeError as error:
        raise TypeError("source_id must be a string.") from error
    cycle = identity.cycle_time
    if (
        not isinstance(cycle, datetime)
        or cycle.tzinfo is None
        or cycle.utcoffset() != timedelta(0)
    ):
        raise ValueError("cycle_time must be timezone-aware UTC.")
    cycle = cycle.astimezone(UTC)
    off_cycle = any((cycle.minute, cycle.second, cycle.microsecond))
    if cycle.hour not in {0, 12} or off_cycle:
        raise ValueError("cycle_time must be a UTC 00Z or 12Z whole-hour cycle.")
    fields = (getattr(identity, name) for name in _FIELDS.split())
    if any(not isinstance(value, str) or not value.strip() for value in fields):
        raise ValueError("WorkIdentity text fields must be non-empty strings.")
    for name in "model_id", "basin_version_id", "project_name":
        _fs.component(getattr(identity, name), name)
    object.__setattr__(identity, "source_id", source)
    object.__setattr__(identity, "cycle_time", cycle)


def _work(root: Path | str, identity: WorkIdentity) -> Path:
    _identity(identity)
    root_path = _fs.absolute(root, "work_root")
    _fs.directory(root_path, None, create=False)
    work = root_path / identity.source_id / identity.cycle_time.strftime("%Y%m%d%H")
    if work == root_path:
        raise ValueError("exact source/cycle work directory must not equal work_root.")
    try:
        _fs.directory(work, root_path, create=False)
    except FileNotFoundError as error:
        raise ValueError("exact source/cycle work directory does not exist.") from error
    return work


def _registry_layout(registry: WorkRegistry) -> None:
    if not isinstance(registry, WorkRegistry):
        raise TypeError("registry must be a WorkRegistry.")
    identity = registry.identity
    _identity(identity)
    work = _fs.absolute(registry.work_dir, "registry.work_dir")
    if work == work.parent.parent:
        raise ValueError("registry.work_dir must not equal work_root.")
    if (work.parent.name, work.name) != (
        identity.source_id,
        identity.cycle_time.strftime("%Y%m%d%H"),
    ):
        raise ValueError("registry.work_dir lacks the exact source/cycle layout.")
    root = work.parent.parent
    _fs.directory(root, None, create=False)
    _fs.directory(work, root, create=False)
    object_root = _fs.absolute(registry.object_store_root, "registry.object_store_root")
    if object_root != work / "object-store":
        raise ValueError("registry.object_store_root must equal work_dir/object-store.")
    _fs.directory(object_root, work, create=False)
    prefix = f"models/{identity.model_id}/"
    expected = prefix + "registry.json", prefix + "package", prefix + "manifest.json"
    if (
        registry.registry_manifest,
        registry.model_package_uri,
        registry.model_manifest_uri,
    ) != expected:
        raise ValueError("registry keys do not have the exact model layout.")


def _registry(registry: WorkRegistry) -> DirectGridForcingContract:
    _registry_layout(registry)
    identity = registry.identity
    work, root = registry.work_dir, registry.object_store_root
    _fs.directory(root, work, create=False)
    model_root = root / "models" / identity.model_id
    _fs.directory(model_root, work, create=False)
    for relative in (
        "registry.json",
        "manifest.json",
        "direct-grid/binding.json",
        f"package/input/{identity.project_name}.sp.att",
        f"package/{identity.project_name}.tsd.forc",
    ):
        _fs.regular(model_root / relative, work)
    store = LocalObjectStore(root)
    registry_bytes = store.read_bytes_limited(
        registry.registry_manifest, max_bytes=MAX_OBJECT_MANIFEST_BYTES
    )
    payload = _fs.json_object(registry_bytes, "registry")
    models = payload.get("models")
    if set(payload) != {"models"} or not isinstance(models, list) or len(models) != 1:
        raise ValueError("registry must contain exactly one models object.")
    row = models[0]
    fields = set(_IDENTITY_KEYS.split())
    fields.update(("model_package_uri", "manifest_uri", "resource_profile"))
    if not isinstance(row, Mapping) or set(row) != fields:
        raise ValueError("registry model schema is invalid.")
    names = _IDENTITY_KEYS.split()
    actual = tuple(row.get(name) for name in names)
    if actual != tuple(getattr(identity, name) for name in names):
        raise ValueError("registry model identity differs from WorkIdentity.")
    paths = row.get("model_package_uri"), row.get("manifest_uri")
    if paths != (registry.model_package_uri, registry.model_manifest_uri):
        raise ValueError("registry model paths differ from WorkRegistry.")
    profile = row.get("resource_profile")
    valid_profile = isinstance(profile, Mapping) and set(profile) == {
        "direct_grid_forcing",
        "shud_input_name",
    }
    if not valid_profile or profile.get("shud_input_name") != identity.project_name:
        raise ValueError("registry resource profile is invalid.")
    model_bytes = store.read_bytes_limited(
        registry.model_manifest_uri, max_bytes=MAX_OBJECT_MANIFEST_BYTES
    )
    model_manifest = _fs.json_object(model_bytes, "model manifest")
    if model_manifest != {"basin_slug": identity.project_name}:
        raise ValueError("model manifest differs from WorkIdentity project.")
    contract = _repository(root, identity)
    _contract_paths(identity, contract)
    return contract


def _repository(
    root: Path,
    identity: WorkIdentity,
    expected: DirectGridForcingContract | None = None,
    binding: bytes | None = None,
    sp_att: bytes | None = None,
) -> DirectGridForcingContract:
    store = LocalObjectStore(root)
    key = f"models/{identity.model_id}/registry.json"
    repository = FileForcingRepository(store, key)
    resolved = repository.resolve_model_identity(model_id=identity.model_id)
    wanted_identity = {
        "basin_id": identity.basin_id,
        "basin_version_id": identity.basin_version_id,
        "river_network_version_id": identity.river_network_version_id,
    }
    if resolved != wanted_identity:
        raise ValueError("repository identity differs from WorkIdentity.")
    contract = repository.load_forcing_mapping_contract(
        model_id=identity.model_id,
        basin_version_id=identity.basin_version_id,
        source_id=identity.source_id,
    )
    if contract is None:
        raise ValueError("repository does not expose a direct-grid contract.")
    if expected is not None:
        asset_limit = max(len(binding or b""), len(sp_att or b""))
        assets = repository.load_direct_grid_validation_assets(
            model_id=identity.model_id,
            basin_version_id=identity.basin_version_id,
            contract=contract,
            max_bytes=asset_limit,
        )
        binding_expected = _fs.normalize_checksum(contract.binding_checksum)
        sp_att_expected = _fs.normalize_checksum(contract.sp_att_checksum)
        if (
            binding_expected != str(assets["binding_checksum"]).strip().lower()
            or sp_att_expected != str(assets["sp_att_checksum"]).strip().lower()
        ):
            raise ValueError("repository validation assets differ from its contract.")
    wanted = sorted(contract.stations, key=lambda item: item.shud_forcing_index)
    stations = repository.load_met_stations(basin_version_id=identity.basin_version_id)
    if len(stations) != len(wanted) or any(
        not isinstance(actual.properties_json, Mapping)
        or actual.properties_json.get("shud_forcing_index") != item.shud_forcing_index
        or actual.properties_json.get("forcing_filename") != item.forcing_filename
        or (actual.longitude, actual.latitude, actual.elevation_m)
        != (item.longitude, item.latitude, max(float(item.z), 0.0))
        for actual, item in zip(stations, wanted, strict=True)
    ):
        raise ValueError("repository station-index read-back differs from contract.")
    if expected is not None:
        if _contract_payload(contract) != _contract_payload(expected):
            raise ValueError(
                "repository contract read-back differs from supplied contract."
            )
        assert binding is not None and sp_att is not None
        for expected_sha, content, label in (
            (assets["binding_checksum"], binding, "repository binding"),
            (assets["sp_att_checksum"], sp_att, "repository sp_att"),
        ):
            _fs.checksum(str(expected_sha), content, label)
    else:
        for key, expected_checksum, label in (
            (contract.binding_uri, contract.binding_checksum, "binding"),
            (
                f"models/{identity.model_id}/package/{contract.sp_att_path}",
                contract.sp_att_checksum,
                "sp_att",
            ),
        ):
            _fs.stream_checksum(store.iter_bytes(key), expected_checksum, label)
    return contract


def _contract_paths(
    identity: WorkIdentity, contract: DirectGridForcingContract
) -> None:
    if contract.binding_uri != f"models/{identity.model_id}/direct-grid/binding.json":
        raise ValueError("contract.binding_uri does not match the final binding key.")
    if contract.sp_att_path != f"input/{identity.project_name}.sp.att":
        raise ValueError("contract.sp_att_path does not match the package path.")


def _write_registry(
    root: Path,
    identity: WorkIdentity,
    contract: DirectGridForcingContract,
    binding: bytes,
    sp_att: bytes,
    work: Path,
) -> None:
    package = root / "package"
    for directory in (root / "direct-grid", package / "input"):
        _fs.directory(directory, work, create=True)
    _fs.write_new(root / "direct-grid" / "binding.json", binding, work)
    _fs.write_new(package / contract.sp_att_path, sp_att, work)
    _fs.write_new(
        package / f"{identity.project_name}.tsd.forc", _station_index(contract), work
    )
    row = {
        "model_id": identity.model_id,
        "basin_id": identity.basin_id,
        "basin_version_id": identity.basin_version_id,
        "river_network_version_id": identity.river_network_version_id,
        "model_package_uri": f"models/{identity.model_id}/package",
        "manifest_uri": f"models/{identity.model_id}/manifest.json",
        "resource_profile": {
            "direct_grid_forcing": _contract_payload(contract),
            "shud_input_name": identity.project_name,
        },
    }
    _fs.write_new(root / "registry.json", _fs.json_bytes({"models": [row]}), work)
    _fs.write_new(
        root / "manifest.json",
        _fs.json_bytes({"basin_slug": identity.project_name}),
        work,
    )


def _contract_payload(contract: DirectGridForcingContract) -> dict[str, Any]:
    stations = []
    fields = "station_id shud_forcing_index forcing_filename longitude latitude x y z grid_id grid_cell_id"
    for station in sorted(contract.stations, key=lambda item: item.shud_forcing_index):
        row = {name: getattr(station, name) for name in fields.split()}
        if station.properties:
            row["properties"] = dict(station.properties)
        stations.append(row)
    return {
        "applicable_source_ids": list(contract.applicable_source_ids),
        "binding_checksum": contract.binding_checksum,
        "binding_uri": contract.binding_uri,
        "forcing_mapping_mode": contract.forcing_mapping_mode,
        "grid_id": contract.grid_id,
        "grid_signature": contract.grid_signature,
        "model_input_package_id": contract.model_input_package_id,
        "sp_att_checksum": contract.sp_att_checksum,
        "sp_att_path": contract.sp_att_path,
        "station_bindings": stations,
    }


def _station_index(contract: DirectGridForcingContract) -> bytes:
    rows = ["ID\tLon\tLat\tX\tY\tZ\tFilename\n"]
    for station in sorted(contract.stations, key=lambda item: item.shud_forcing_index):
        values = (
            *((station.longitude, station.latitude, station.x, station.y)),
            max(float(station.z), 0.0),
        )
        geometry = "\t".join(repr(float(value)) for value in values)
        rows.append(
            f"{station.shud_forcing_index}\t{geometry}\t{station.forcing_filename}\n"
        )
    return "".join(rows).encode("utf-8")


def _tree(root: Path, project: str) -> tuple[list[Path], list[tuple[Path, Path]]]:
    _fs.directory(root, None, create=False)
    for path in (root / f"{project}.cfg.ic", root / f"{project}.para"):
        _fs.regular(path, None)
    dirs: list[Path] = [Path(".")]
    files: list[tuple[Path, Path]] = []
    pending = [(Path("."), root)]
    while pending:
        relative, directory = pending.pop()
        for name in sorted(
            list_directory_no_follow(directory, containment_root=None), reverse=True
        ):
            _fs.component(name, "variant entry")
            path = directory / name
            child = Path(name) if relative == Path(".") else relative / name
            mode = stat_no_follow(path, containment_root=None).st_mode
            if stat.S_ISDIR(mode):
                dirs.append(child)
                pending.append((child, path))
            elif stat.S_ISREG(mode):
                files.append((child, path))
            else:
                raise ValueError(f"variant contains a non-regular entry: {path}")
    return sorted(dirs), sorted(files)


def _state(
    path: Path,
    states: Path,
    identity: WorkIdentity,
    check: tuple[int, str | None],
    root: Path,
) -> bytes:
    _fs.directory(states, root, create=False)
    expected = states / identity.source_id / f"{identity.cycle_time:%Y%m%d%H}.cfg.ic"
    if path != expected:
        raise ValueError(
            "state_path must exactly equal states_root/source/cycle.cfg.ic."
        )
    _fs.regular(path, root)
    content = _fs.read_limited(path, check[0], root)
    if check[1] is not None:
        _fs.checksum(check[1], content, "state")
    document = parse(content, max_bytes=check[0])
    minute = cfg_ic_header_minute_time(document.lines[document.header_index].split())
    if minute is None or not math.isfinite(minute):
        raise ValueError("state header lacks a finite absolute minute token.")
    if round(minute) != round(identity.cycle_time.timestamp() / 60):
        raise ValueError("state header minute does not match WorkIdentity cycle.")
    return content


def _forcing(
    registry: WorkRegistry, result: ForcingProductionResult
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    if not isinstance(result, ForcingProductionResult):
        raise TypeError("forcing must be a ForcingProductionResult.")
    if result.status not in {"forcing_ready", "already_done"}:
        raise ValueError("forcing status must be forcing_ready or already_done.")
    if not isinstance(result.checksum, str) or not result.checksum:
        raise ValueError("forcing result must carry a package checksum.")
    if not isinstance(result.file_uris, Mapping):
        raise TypeError("forcing file_uris must be a mapping.")
    manifest_uri = result.file_uris.get("package_manifest")
    if not isinstance(manifest_uri, str) or not manifest_uri:
        raise ValueError("forcing result lacks file_uris['package_manifest'].")
    identity, store = registry.identity, LocalObjectStore(registry.object_store_root)
    package = _store_key(
        store, result.forcing_package_uri, "forcing_package_uri"
    ).rstrip("/")
    expected = f"forcing/{identity.source_id}/{identity.cycle_time:%Y%m%d%H}/{identity.basin_version_id}/{identity.model_id}"
    if package != expected:
        raise ValueError("forcing_package_uri differs from WorkIdentity.")
    manifest_key = _store_key(store, manifest_uri, "package_manifest")
    if not manifest_key.startswith(f"{package}/"):
        raise ValueError("package manifest must be within forcing_package_uri.")
    remainder = manifest_key[len(package) + 1 :]
    if not remainder:
        raise ValueError("package manifest must be within forcing_package_uri.")
    for part in remainder.split("/"):
        try:
            _fs.component(part, "package manifest path")
        except ValueError:
            raise ValueError(
                "package manifest must be within forcing_package_uri."
            ) from None
    content = store.read_bytes_limited(
        manifest_key, max_bytes=MAX_OBJECT_MANIFEST_BYTES
    )
    _fs.checksum(result.checksum, content, "forcing package manifest")
    manifest = load_bounded_json(content, max_bytes=MAX_OBJECT_MANIFEST_BYTES)
    if not isinstance(manifest, Mapping):
        raise TypeError("forcing package manifest must be a JSON object.")
    if (
        normalize_source_id(str(manifest.get("source_id") or "")),
        _manifest_time(manifest.get("cycle_time")),
    ) != (identity.source_id, identity.cycle_time):
        raise ValueError("forcing manifest source/cycle differs from WorkIdentity.")
    if (
        manifest.get("model_id") != identity.model_id
        or manifest.get("forcing_version_id") != result.forcing_version_id
    ):
        raise ValueError("forcing manifest model/version differs from result.")
    files = manifest.get("files")
    if not isinstance(files, Sequence) or isinstance(files, str | bytes | bytearray):
        raise TypeError("forcing manifest files must be an array.")
    indexes: list[dict[str, Any]] = []
    csvs: list[dict[str, Any]] = []
    for raw in files:
        if not isinstance(raw, Mapping):
            raise TypeError("forcing manifest file entries must be objects.")
        entry = dict(raw)
        relative, role = entry.get("relative_path"), entry.get("role")
        if relative in SHUD_FORCING_INDEX_MEMBERS and role != SHUD_FORCING_ROLE:
            raise ValueError("SHUD index path has an incorrect manifest role.")
        if (
            isinstance(relative, str)
            and relative.startswith("shud/")
            and relative.endswith(".csv")
            and role != "shud_forcing_csv"
        ):
            raise ValueError("SHUD CSV path has an incorrect manifest role.")
        if role == SHUD_FORCING_ROLE:
            if relative not in SHUD_FORCING_INDEX_MEMBERS:
                raise ValueError(
                    "SHUD forcing role must name a canonical or legacy index."
                )
            indexes.append(entry)
        elif role == "shud_forcing_csv":
            _csv_path(relative)
            csvs.append(entry)
    if len(indexes) != 1 or not csvs:
        raise ValueError(
            "forcing package must declare exactly one index and SHUD CSVs."
        )
    if len(csvs) > MAX_DIRECT_GRID_STATION_BINDINGS:
        raise ValueError(
            f"forcing package declares more than {MAX_DIRECT_GRID_STATION_BINDINGS} SHUD CSVs."
        )
    names = [_csv_path(entry["relative_path"]) for entry in csvs]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("SHUD CSV basenames must be casefold-unique.")
    for entry in (*indexes, *csvs):
        relative, uri, checksum = (
            entry.get("relative_path"),
            entry.get("uri"),
            entry.get("checksum"),
        )
        if not all(
            isinstance(value, str) and value for value in (relative, uri, checksum)
        ):
            raise TypeError(
                "SHUD members require relative_path, uri, and checksum strings."
            )
        if _store_key(store, uri, "SHUD member URI") != f"{package}/{relative}":
            raise ValueError(
                "SHUD member URI differs from package URI plus relative path."
            )
    if Counter(
        _fs.parse_shud_index_stream(
            store.iter_bytes(str(indexes[0]["uri"])),
            str(indexes[0]["checksum"]),
            names,
        )
    ) != Counter(names):
        raise ValueError("SHUD index filenames differ from declared CSV filenames.")
    for entry in csvs:
        _fs.stream_checksum(
            store.iter_bytes(str(entry["uri"])), str(entry["checksum"]), "SHUD CSV"
        )
    return indexes[0], tuple(csvs)


def _csv_path(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("shud/"):
        raise ValueError("SHUD CSV path must be shud/<basename>.csv.")
    name = value.removeprefix("shud/")
    if "/" in name or not _CSV.fullmatch(name):
        raise ValueError("SHUD CSV basename is unsafe.")
    return name


def _commit_probe(parent: Path, name: str, root: Path, label: str) -> None:
    _fs.absent(parent, name, root)


def _store_key(store: LocalObjectStore, value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("s3://"):
        raise ValueError(f"{label} must be a local work object-store key.")
    if Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError(f"{label} must not escape the work object store.")
    return store.normalize_key(value)


def _manifest_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("forcing manifest cycle_time must be a string.")
    parsed = datetime.fromisoformat(
        value[:-1] + "+00:00" if value.endswith("Z") else value
    )
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("forcing manifest cycle_time must be UTC-aware.")
    return parsed.astimezone(UTC)


def _abort(
    message: str,
    phase: AssemblyPhase,
    path: Path | None,
    work: Path,
    stage: Path,
    error: Exception,
) -> NoReturn:
    warnings = _fs.clean(stage, work)
    if isinstance(error, AssemblyError):
        error.cleanup_warnings = error.cleanup_warnings + warnings
        for warning in warnings:
            error.add_note(warning)
        raise error
    raise _error(
        message, phase, path=path, cause=error, cleanup_warnings=warnings
    ) from error


def _error(
    message: str,
    phase: AssemblyPhase,
    *,
    path: Path | None = None,
    cause: BaseException | None = None,
    cleanup_warnings: Sequence[str] = (),
) -> AssemblyError:
    text = message if cause is None else f"{message}: {cause}"
    error = AssemblyError(
        text, phase=phase, path=path, cleanup_warnings=cleanup_warnings
    )
    for warning in cleanup_warnings:
        error.add_note(warning)
    return error
