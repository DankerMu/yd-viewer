"""Claimed-work staged variant/state capability (#177)."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from yd_producer._work_claim import (
    ClaimLostError,
    WorkClaim,
    lexists_claimed,
    mkdir_relative_to_claim,
    open_claimed_excl,
    validate_claim,
)
from yd_producer.assemble import (
    AssemblyError,
    RunDirectory,
    WorkRegistry,
    _assemble_kernel,
    _AssemblyInputs,
    _error,
)
from yd_producer.controller import STATE_SUFFIX, cycle_id
from yd_producer.forcing import ForcingProductionResult
from yd_producer.forcing.bounded_json import BoundedJSONError, load_bounded_json
from yd_producer.prepare_handoff import (
    PREPARED_VARIANT_BINDING_FILENAME,
    PREPARED_VARIANT_CALIBRATED_STATE_FILENAME,
    PREPARED_VARIANT_HANDOFF_FILENAME,
    PREPARED_VARIANT_PARAMETER_FILENAME,
    PreparedVariantHandoff,
    PreparedVariantHandoffError,
    load_prepared_variant_handoff,
)
from yd_producer.raw.source_identity import normalize_source_id
from yd_producer.state import cfg_ic_header_minute_time, parse
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    directory_identity_no_follow,
    list_directory_no_follow_limited,
    read_bytes_limited_no_follow,
    stat_no_follow,
)

__all__ = [
    "STAGED_INPUTS_MANIFEST_FILENAME",
    "STAGED_INPUTS_SCHEMA",
    "STAGED_INPUT_DIRNAME",
    "STAGED_STATES_DIRNAME",
    "STAGED_VARIANT_DIRNAME",
    "StagedWorkInputs",
    "StagedWorkInputsError",
    "assemble_staged",
    "load_staged_work_inputs",
    "stage_work_inputs",
]

STAGED_INPUTS_SCHEMA = "yd.run.staged-inputs.v1"
STAGED_INPUT_DIRNAME = "input"
STAGED_VARIANT_DIRNAME = "variant"
STAGED_STATES_DIRNAME = "states"
STAGED_INPUTS_MANIFEST_FILENAME = "yd.staged-inputs.json"

_MANIFEST_KEYS = frozenset(
    {"schema_version", "source_id", "cycle_id", "work_dir", "files"}
)
_INPUT_ENTRIES = frozenset(
    {STAGED_VARIANT_DIRNAME, STAGED_STATES_DIRNAME, STAGED_INPUTS_MANIFEST_FILENAME}
)
_FIXED_VARIANT_FILENAMES = frozenset(
    {
        PREPARED_VARIANT_CALIBRATED_STATE_FILENAME,
        PREPARED_VARIANT_PARAMETER_FILENAME,
        PREPARED_VARIANT_BINDING_FILENAME,
        PREPARED_VARIANT_HANDOFF_FILENAME,
    }
)
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CHECKSUM = re.compile(r"^sha256:[0-9a-f]{64}$")
_VARIANT_FILE_KEY = re.compile(
    rf"^{STAGED_INPUT_DIRNAME}/{STAGED_VARIANT_DIRNAME}/"
    r"([A-Za-z0-9][A-Za-z0-9_.-]*)$"
)
_ORDINARY_ERRORS = (
    BoundedJSONError,
    ClaimLostError,
    PreparedVariantHandoffError,
    SafeFilesystemError,
    OSError,
    TypeError,
    ValueError,
    OverflowError,
    RecursionError,
)


class StagedWorkInputsError(ValueError):
    """Staged work inputs are malformed, unsafe, or not current-caller bound."""


@dataclass(frozen=True, kw_only=True)
class StagedWorkInputs:
    source: str
    cycle: datetime
    work_dir: Path
    variant_dir: Path
    state_path: Path
    manifest_path: Path
    work_identity: tuple[int, int]
    manifest_checksum: str
    file_checksums: tuple[tuple[str, str], ...]
    prepared: PreparedVariantHandoff
    project_name: str
    grid_id: str
    max_manifest_bytes: int
    max_asset_bytes: int
    max_state_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not isinstance(self.project_name, str):
            raise TypeError("source and project_name must be strings.")
        if not isinstance(self.grid_id, str) or not isinstance(
            self.manifest_checksum, str
        ):
            raise TypeError("grid_id and manifest_checksum must be strings.")
        if not isinstance(self.cycle, datetime):
            raise TypeError("cycle must be a datetime.")
        for name in ("work_dir", "variant_dir", "state_path", "manifest_path"):
            if not isinstance(getattr(self, name), Path):
                raise TypeError(f"{name} must be a Path.")
        identity = self.work_identity
        if type(identity) is not tuple or len(identity) != 2:
            raise TypeError("work_identity must be a tuple of two ints.")
        if any(type(item) is not int for item in identity):
            raise TypeError("work_identity must be a tuple of two ints.")
        checksums = self.file_checksums
        if type(checksums) is not tuple:
            raise TypeError("file_checksums must be a tuple of 2-str tuples.")
        for item in checksums:
            if (
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) is not str
            ):
                raise TypeError("file_checksums must be a tuple of 2-str tuples.")
        if type(self.prepared) is not PreparedVariantHandoff:
            raise TypeError("prepared must be a PreparedVariantHandoff.")
        for name in ("max_manifest_bytes", "max_asset_bytes", "max_state_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a strict positive integer.")


@dataclass(frozen=True, kw_only=True)
class _Caller:
    source: str
    cycle: datetime
    cycle_name: str
    project_name: str
    grid_id: str
    max_manifest_bytes: int
    max_asset_bytes: int
    max_state_bytes: int


@dataclass(frozen=True, kw_only=True)
class _SourceSnapshot:
    prepared: PreparedVariantHandoff
    variant_identity: tuple[int, int]
    entries: frozenset[str]
    contents: tuple[tuple[str, bytes], ...]
    checksums: tuple[tuple[str, str], ...]


def stage_work_inputs(
    *,
    claim: WorkClaim,
    source_variant_dir: Path | str,
    source_state_path: Path | str,
    source: str,
    cycle: datetime,
    project_name: str,
    grid_id: str,
    max_manifest_bytes: int,
    max_asset_bytes: int,
    max_state_bytes: int,
) -> StagedWorkInputs:
    """Copy admitted NFS variant/state into claimed work and return the loader snapshot."""
    try:
        return _stage(
            claim=claim,
            source_variant_dir=source_variant_dir,
            source_state_path=source_state_path,
            source=source,
            cycle=cycle,
            project_name=project_name,
            grid_id=grid_id,
            max_manifest_bytes=max_manifest_bytes,
            max_asset_bytes=max_asset_bytes,
            max_state_bytes=max_state_bytes,
        )
    except StagedWorkInputsError:
        raise
    except _ORDINARY_ERRORS as error:
        raise StagedWorkInputsError(f"invalid staged work inputs: {error}") from error


def load_staged_work_inputs(
    *,
    work_dir: Path | str,
    source: str,
    cycle: datetime,
    project_name: str,
    grid_id: str,
    max_manifest_bytes: int,
    max_asset_bytes: int,
    max_state_bytes: int,
) -> StagedWorkInputs:
    """Load one canonical v1 staged-input tree without discovering candidate paths."""
    try:
        return _load(
            work_dir=work_dir,
            source=source,
            cycle=cycle,
            project_name=project_name,
            grid_id=grid_id,
            max_manifest_bytes=max_manifest_bytes,
            max_asset_bytes=max_asset_bytes,
            max_state_bytes=max_state_bytes,
        )
    except StagedWorkInputsError:
        raise
    except _ORDINARY_ERRORS as error:
        raise StagedWorkInputsError(f"invalid staged work inputs: {error}") from error


def assemble_staged(
    *,
    registry: WorkRegistry,
    staged_inputs: StagedWorkInputs,
    forcing: ForcingProductionResult,
) -> RunDirectory:
    """Assemble from the checksum-bound exact-six staged capability."""
    try:
        if type(staged_inputs) is not StagedWorkInputs:
            raise TypeError("staged_inputs must be an exact StagedWorkInputs.")
        if not isinstance(registry, WorkRegistry):
            raise TypeError("registry must be a WorkRegistry.")
        identity = registry.identity
        if (
            registry.work_dir != staged_inputs.work_dir
            or identity.source_id != staged_inputs.source
            or identity.cycle_time != staged_inputs.cycle
            or identity.project_name != staged_inputs.project_name
        ):
            raise ValueError("registry identity differs from staged inputs.")
        reloaded = load_staged_work_inputs(
            work_dir=staged_inputs.work_dir,
            source=staged_inputs.source,
            cycle=staged_inputs.cycle,
            project_name=staged_inputs.project_name,
            grid_id=staged_inputs.grid_id,
            max_manifest_bytes=staged_inputs.max_manifest_bytes,
            max_asset_bytes=staged_inputs.max_asset_bytes,
            max_state_bytes=staged_inputs.max_state_bytes,
        )
        if reloaded != staged_inputs:
            raise ValueError("staged inputs differ from their point-of-use reload.")
        checksums = dict(reloaded.file_checksums)
        variant_key = f"{STAGED_INPUT_DIRNAME}/{STAGED_VARIANT_DIRNAME}"
        parameter_name = PREPARED_VARIANT_PARAMETER_FILENAME
        state_key = (
            f"{STAGED_INPUT_DIRNAME}/{STAGED_STATES_DIRNAME}/"
            f"{reloaded.source}/{cycle_id(reloaded.cycle)}{STATE_SUFFIX}"
        )
        passthrough_names = (
            PREPARED_VARIANT_BINDING_FILENAME,
            PREPARED_VARIANT_HANDOFF_FILENAME,
            reloaded.prepared.sp_att_asset_name,
        )
        passthrough = tuple(
            (
                Path(name),
                reloaded.variant_dir / name,
                checksums[f"{variant_key}/{name}"],
            )
            for name in passthrough_names
        )
        parameter_checksum = checksums[f"{variant_key}/{parameter_name}"]
        state_checksum = checksums[state_key]
    except AssemblyError:
        raise
    except (KeyError,) + _ORDINARY_ERRORS as error:
        raise _error(
            "Invalid staged SHUD assembly input", "validate", cause=error
        ) from error
    states = reloaded.work_dir / STAGED_INPUT_DIRNAME / STAGED_STATES_DIRNAME
    return _assemble_kernel(
        registry,
        forcing,
        _AssemblyInputs(
            reloaded.variant_dir,
            states,
            reloaded.state_path,
            (Path("."),),
            passthrough,
            (reloaded.max_asset_bytes, parameter_checksum),
            (reloaded.max_state_bytes, state_checksum),
            reloaded.work_dir,
            reloaded.prepared.contract,
        ),
    )


def _stage(
    *,
    claim: WorkClaim,
    source_variant_dir: Path | str,
    source_state_path: Path | str,
    source: str,
    cycle: datetime,
    project_name: str,
    grid_id: str,
    max_manifest_bytes: int,
    max_asset_bytes: int,
    max_state_bytes: int,
) -> StagedWorkInputs:
    if not isinstance(claim, WorkClaim):
        raise TypeError("claim must be a WorkClaim.")
    validate_claim(claim)
    caller = _preflight(
        source=source,
        cycle=cycle,
        project_name=project_name,
        grid_id=grid_id,
        max_manifest_bytes=max_manifest_bytes,
        max_asset_bytes=max_asset_bytes,
        max_state_bytes=max_state_bytes,
    )
    work = _work_shape(claim.work_dir, caller)
    if work != claim.work_root / caller.source / caller.cycle_name:
        raise ValueError("claimed work is not the exact source/cycle root.")
    variant_root, state_path = _admit_source_roots(
        claim,
        source_variant_dir=source_variant_dir,
        source_state_path=source_state_path,
        caller=caller,
    )
    snapshot = _capture_source(variant_root, state_path, caller)
    manifest_bytes = _manifest_bytes(
        source=caller.source,
        cycle_name=caller.cycle_name,
        work=work,
        checksums=snapshot.checksums,
    )
    if len(manifest_bytes) > caller.max_manifest_bytes:
        raise ValueError(
            "staged-inputs manifest exceeds its "
            f"{caller.max_manifest_bytes} byte limit."
        )
    validate_claim(claim)
    input_dir = work / STAGED_INPUT_DIRNAME
    variant_dir = input_dir / STAGED_VARIANT_DIRNAME
    states_dir = input_dir / STAGED_STATES_DIRNAME
    source_states = states_dir / caller.source
    if lexists_claimed(claim, input_dir):
        raise ValueError(f"target input already exists in any form: {input_dir}")
    _mkdir_new(claim, variant_dir, {input_dir, variant_dir})
    _mkdir_new(claim, source_states, {states_dir, source_states})
    for key, content in snapshot.contents:
        _write_claimed(claim, work.joinpath(*key.split("/")), content)
    _write_claimed(
        claim,
        work / STAGED_INPUT_DIRNAME / STAGED_INPUTS_MANIFEST_FILENAME,
        manifest_bytes,
    )
    loaded = load_staged_work_inputs(
        work_dir=work,
        source=caller.source,
        cycle=caller.cycle,
        project_name=caller.project_name,
        grid_id=caller.grid_id,
        max_manifest_bytes=caller.max_manifest_bytes,
        max_asset_bytes=caller.max_asset_bytes,
        max_state_bytes=caller.max_state_bytes,
    )
    reread = _capture_source(variant_root, state_path, caller)
    if reread.prepared != snapshot.prepared:
        raise ValueError("source prepared variant snapshot changed after staging.")
    if reread.checksums != snapshot.checksums:
        raise ValueError("source file checksums changed after staging.")
    if reread.variant_identity != snapshot.variant_identity:
        raise ValueError("source variant root identity changed after staging.")
    if reread.entries != snapshot.entries:
        raise ValueError("source variant entry set changed after staging.")
    if loaded.file_checksums != snapshot.checksums:
        raise ValueError("staged file checksums do not match the source snapshot.")
    if loaded.prepared != snapshot.prepared:
        raise ValueError("staged prepared variant does not match the source snapshot.")
    return loaded


def _load(
    *,
    work_dir: Path | str,
    source: str,
    cycle: datetime,
    project_name: str,
    grid_id: str,
    max_manifest_bytes: int,
    max_asset_bytes: int,
    max_state_bytes: int,
) -> StagedWorkInputs:
    caller = _preflight(
        source=source,
        cycle=cycle,
        project_name=project_name,
        grid_id=grid_id,
        max_manifest_bytes=max_manifest_bytes,
        max_asset_bytes=max_asset_bytes,
        max_state_bytes=max_state_bytes,
    )
    work = _work_shape(work_dir, caller)
    work_identity = directory_identity_no_follow(work)
    input_dir = work / STAGED_INPUT_DIRNAME
    input_identity = directory_identity_no_follow(input_dir)
    _list_exact(input_dir, _INPUT_ENTRIES, root=work)
    variant_dir = input_dir / STAGED_VARIANT_DIRNAME
    states_dir = input_dir / STAGED_STATES_DIRNAME
    manifest_path = input_dir / STAGED_INPUTS_MANIFEST_FILENAME
    _require_dir(variant_dir, work)
    _require_dir(states_dir, work)
    _require_file(manifest_path, work)
    manifest_bytes = _read_limited(
        manifest_path, caller.max_manifest_bytes, containment_root=work
    )
    manifest = _canonical_object(manifest_bytes, caller.max_manifest_bytes)
    _exact_keys(manifest, _MANIFEST_KEYS, "staged-inputs manifest")
    _manifest_fields(manifest, caller, work)
    files = manifest["files"]
    if not isinstance(files, dict):
        raise TypeError("staged-inputs files must be a JSON object.")
    declared = _declared_files(files, caller)
    source_states, state_path, state_name = _require_staged_layout(
        work, variant_dir, states_dir, declared, caller
    )
    prepared_first = _load_prepared(variant_dir, caller)
    expected_entries = _FIXED_VARIANT_FILENAMES | {prepared_first.sp_att_asset_name}
    if declared.variant_names != expected_entries:
        raise ValueError(
            "staged variant entries do not equal the v1 exact five-entry set."
        )
    contents, file_checksums = _read_declared(work, declared, files, caller)
    prepared = _load_prepared(variant_dir, caller)
    if prepared != prepared_first:
        raise ValueError("prepared variant snapshot changed during loading.")
    _, checksums_again = _read_declared(work, declared, files, caller)
    if checksums_again != file_checksums:
        raise ValueError("staged file checksums changed during loading.")
    _bind_prepared_assets(prepared, dict(contents))
    _validate_state(dict(contents)[_state_file_key(caller)], caller)
    _coherent_tree(
        work=work,
        work_identity=work_identity,
        input_dir=input_dir,
        input_identity=input_identity,
        variant_dir=variant_dir,
        variant_names=expected_entries,
        states_dir=states_dir,
        source_states=source_states,
        state_name=state_name,
        caller=caller,
    )
    return StagedWorkInputs(
        source=caller.source,
        cycle=caller.cycle,
        work_dir=work,
        variant_dir=variant_dir,
        state_path=state_path,
        manifest_path=manifest_path,
        work_identity=work_identity,
        manifest_checksum=_digest(manifest_bytes),
        file_checksums=file_checksums,
        prepared=prepared,
        project_name=caller.project_name,
        grid_id=caller.grid_id,
        max_manifest_bytes=caller.max_manifest_bytes,
        max_asset_bytes=caller.max_asset_bytes,
        max_state_bytes=caller.max_state_bytes,
    )


def _preflight(
    *,
    source: str,
    cycle: datetime,
    project_name: str,
    grid_id: str,
    max_manifest_bytes: int,
    max_asset_bytes: int,
    max_state_bytes: int,
) -> _Caller:
    for name, value in (
        ("max_manifest_bytes", max_manifest_bytes),
        ("max_asset_bytes", max_asset_bytes),
        ("max_state_bytes", max_state_bytes),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a strict positive integer.")
    source_text = _text(source, "source")
    try:
        normalized = normalize_source_id(source_text)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("source must be gfs or ifs.") from error
    if not isinstance(cycle, datetime) or cycle.tzinfo is None:
        raise ValueError("cycle must be timezone-aware UTC.")
    if cycle.utcoffset() != timedelta(0):
        raise ValueError("cycle must be timezone-aware UTC.")
    cycle = cycle.astimezone(UTC)
    if cycle.hour not in {0, 12} or any(
        (cycle.minute, cycle.second, cycle.microsecond)
    ):
        raise ValueError("cycle must be a UTC 00Z or 12Z whole-hour cycle.")
    return _Caller(
        source=normalized,
        cycle=cycle,
        cycle_name=cycle_id(cycle),
        project_name=_identifier(project_name, "project_name"),
        grid_id=_text(grid_id, "grid_id"),
        max_manifest_bytes=max_manifest_bytes,
        max_asset_bytes=max_asset_bytes,
        max_state_bytes=max_state_bytes,
    )


def _work_shape(work_dir: Path | str, caller: _Caller) -> Path:
    work = _absolute(work_dir, "work_dir")
    _reject_dot_parts(work, "work_dir")
    if (work.parent.parent.name, work.parent.name, work.name) != (
        "work",
        caller.source,
        caller.cycle_name,
    ):
        raise ValueError("work_dir must be an absolute .../work/<source>/<cycle> path.")
    return work


def _admit_source_roots(
    claim: WorkClaim,
    *,
    source_variant_dir: Path | str,
    source_state_path: Path | str,
    caller: _Caller,
) -> tuple[Path, Path]:
    variant = _absolute(source_variant_dir, "source_variant_dir")
    state = _absolute(source_state_path, "source_state_path")
    _reject_dot_parts(variant, "source_variant_dir")
    _reject_dot_parts(state, "source_state_path")
    expected_suffix = ("states", caller.source, f"{caller.cycle_name}{STATE_SUFFIX}")
    if state.parts[-3:] != expected_suffix:
        raise ValueError(
            "source_state_path must end with states/<source>/<cycle>.cfg.ic."
        )
    _disjoint(variant, claim.work_dir, "source_variant_dir")
    _disjoint(state, claim.work_dir, "source_state_path")
    return variant, state


def _capture_source(
    variant_root: Path, state_path: Path, caller: _Caller
) -> _SourceSnapshot:
    prepared = load_prepared_variant_handoff(
        variant_root=variant_root,
        source_id=caller.source,
        project_name=caller.project_name,
        grid_id=caller.grid_id,
        max_manifest_bytes=caller.max_manifest_bytes,
        max_asset_bytes=caller.max_asset_bytes,
    )
    identity = directory_identity_no_follow(variant_root)
    expected_entries = _FIXED_VARIANT_FILENAMES | {prepared.sp_att_asset_name}
    names = _list_exact(variant_root, expected_entries, root=variant_root)
    contents: list[tuple[str, bytes]] = []
    for name in sorted(names):
        cap = (
            caller.max_manifest_bytes
            if name == PREPARED_VARIANT_HANDOFF_FILENAME
            else caller.max_asset_bytes
        )
        content = _read_limited(variant_root / name, cap, containment_root=variant_root)
        contents.append(
            (f"{STAGED_INPUT_DIRNAME}/{STAGED_VARIANT_DIRNAME}/{name}", content)
        )
    state_bytes = _read_limited(
        state_path, caller.max_state_bytes, containment_root=None
    )
    _validate_state(state_bytes, caller)
    contents.append((_state_file_key(caller), state_bytes))
    if directory_identity_no_follow(variant_root) != identity:
        raise ValueError("source variant root identity changed during loading.")
    if (
        set(_list_exact(variant_root, expected_entries, root=variant_root))
        != expected_entries
    ):
        raise ValueError("source variant entry set changed during loading.")
    frozen = tuple(sorted(contents))
    checksums = tuple((key, _digest(content)) for key, content in frozen)
    return _SourceSnapshot(
        prepared=prepared,
        variant_identity=identity,
        entries=expected_entries,
        contents=frozen,
        checksums=checksums,
    )


def _declared_files(files: dict[str, Any], caller: _Caller) -> _DeclaredFiles:
    if len(files) != 6:
        raise ValueError("staged-inputs files must contain exactly six keys.")
    state_key = _state_file_key(caller)
    variant_names: set[str] = set()
    keys: list[str] = []
    for key, value in files.items():
        if not isinstance(key, str):
            raise TypeError("staged-inputs file keys must be strings.")
        if not isinstance(value, str) or _CHECKSUM.fullmatch(value) is None:
            raise ValueError(f"{key} checksum must be sha256:<64 lowercase hex>.")
        keys.append(key)
        if key == state_key:
            continue
        matched = _VARIANT_FILE_KEY.fullmatch(key)
        if matched is None:
            raise ValueError(f"unsupported staged file key: {key!r}.")
        variant_names.add(matched.group(1))
    if state_key not in files:
        raise ValueError("staged-inputs files must include the exact cycle state key.")
    if len(variant_names) != 5:
        raise ValueError("staged-inputs files must include exactly five variant keys.")
    missing_fixed = _FIXED_VARIANT_FILENAMES - variant_names
    if missing_fixed:
        raise ValueError(
            f"staged variant keys are missing fixed files: {sorted(missing_fixed)!r}."
        )
    return _DeclaredFiles(
        keys=tuple(sorted(keys)),
        variant_names=frozenset(variant_names),
    )


@dataclass(frozen=True, kw_only=True)
class _DeclaredFiles:
    keys: tuple[str, ...]
    variant_names: frozenset[str]


def _manifest_fields(manifest: dict[str, Any], caller: _Caller, work: Path) -> None:
    schema = manifest["schema_version"]
    if not isinstance(schema, str):
        raise TypeError("staged-inputs schema_version must be a string.")
    if schema != STAGED_INPUTS_SCHEMA:
        raise ValueError("staged-inputs schema_version is unsupported.")
    declared_source = _text(manifest["source_id"], "manifest source_id")
    if declared_source != caller.source:
        raise ValueError("staged-inputs source_id does not match the current source.")
    declared_cycle = _text(manifest["cycle_id"], "manifest cycle_id")
    if declared_cycle != caller.cycle_name:
        raise ValueError("staged-inputs cycle_id does not match the current cycle.")
    declared_work = manifest["work_dir"]
    if not isinstance(declared_work, str):
        raise TypeError("staged-inputs work_dir must be a string.")
    if declared_work != work.as_posix():
        raise ValueError(
            "staged-inputs work_dir does not match the named work directory."
        )


def _canonical_object(content: bytes, max_bytes: int) -> dict[str, Any]:
    value = load_bounded_json(content, max_bytes=max_bytes)
    if not isinstance(value, dict):
        raise TypeError("staged-inputs manifest must be a JSON object.")
    canonical = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if content != canonical:
        raise ValueError("staged-inputs manifest is not canonical JSON bytes.")
    return value


def _manifest_bytes(
    *,
    source: str,
    cycle_name: str,
    work: Path,
    checksums: tuple[tuple[str, str], ...],
) -> bytes:
    payload = {
        "schema_version": STAGED_INPUTS_SCHEMA,
        "source_id": source,
        "cycle_id": cycle_name,
        "work_dir": work.as_posix(),
        "files": {key: value for key, value in checksums},
    }
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_state(content: bytes, caller: _Caller) -> None:
    document = parse(content, max_bytes=caller.max_state_bytes)
    minute = cfg_ic_header_minute_time(document.lines[document.header_index].split())
    if minute is None or not math.isfinite(minute):
        raise ValueError("state header lacks a finite absolute minute token.")
    if round(minute) != round(caller.cycle.timestamp() / 60):
        raise ValueError("state header minute does not match the current cycle.")


def _coherent_tree(
    *,
    work: Path,
    work_identity: tuple[int, int],
    input_dir: Path,
    input_identity: tuple[int, int],
    variant_dir: Path,
    variant_names: frozenset[str],
    states_dir: Path,
    source_states: Path,
    state_name: str,
    caller: _Caller,
) -> None:
    if directory_identity_no_follow(work) != work_identity:
        raise ValueError("work root identity changed during loading.")
    if directory_identity_no_follow(input_dir) != input_identity:
        raise ValueError("input root identity changed during loading.")
    _list_exact(input_dir, _INPUT_ENTRIES, root=work)
    _list_exact(variant_dir, variant_names, root=work)
    _list_exact(states_dir, frozenset({caller.source}), root=work)
    _list_exact(source_states, frozenset({state_name}), root=work)


def _write_claimed(claim: WorkClaim, path: Path, content: bytes) -> None:
    fd = open_claimed_excl(claim, path)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(f"short write to {path}: wrote {written} bytes")
            view = view[written:]
    finally:
        os.close(fd)


def _mkdir_new(claim: WorkClaim, directory: Path, expected: set[Path]) -> None:
    created = mkdir_relative_to_claim(claim, directory)
    if set(created) != expected:
        raise ValueError(
            f"refusing to adopt preexisting or raced directories for {directory}: "
            f"created={sorted(created)!r}, expected={sorted(expected)!r}."
        )


def _require_staged_layout(
    work: Path,
    variant_dir: Path,
    states_dir: Path,
    declared: _DeclaredFiles,
    caller: _Caller,
) -> tuple[Path, Path, str]:
    _list_exact(variant_dir, declared.variant_names, root=work)
    for name in declared.variant_names:
        _require_file(variant_dir / name, work)
    _list_exact(states_dir, frozenset({caller.source}), root=work)
    source_states = states_dir / caller.source
    _require_dir(source_states, work)
    state_name = f"{caller.cycle_name}{STATE_SUFFIX}"
    _list_exact(source_states, frozenset({state_name}), root=work)
    state_path = source_states / state_name
    _require_file(state_path, work)
    return source_states, state_path, state_name


def _load_prepared(variant_dir: Path, caller: _Caller) -> PreparedVariantHandoff:
    return load_prepared_variant_handoff(
        variant_root=variant_dir,
        source_id=caller.source,
        project_name=caller.project_name,
        grid_id=caller.grid_id,
        max_manifest_bytes=caller.max_manifest_bytes,
        max_asset_bytes=caller.max_asset_bytes,
    )


def _read_declared(
    work: Path,
    declared: _DeclaredFiles,
    files: dict[str, Any],
    caller: _Caller,
) -> tuple[tuple[tuple[str, bytes], ...], tuple[tuple[str, str], ...]]:
    contents: list[tuple[str, bytes]] = []
    checksums: list[tuple[str, str]] = []
    for key in declared.keys:
        path = work.joinpath(*key.split("/"))
        content = _read_limited(path, _cap_for(key, caller), containment_root=work)
        digest = _digest(content)
        if files[key] != digest:
            raise ValueError(f"{key} checksum does not match exact bytes.")
        contents.append((key, content))
        checksums.append((key, digest))
    return tuple(contents), tuple(sorted(checksums))


def _bind_prepared_assets(
    prepared: PreparedVariantHandoff, contents: dict[str, bytes]
) -> None:
    binding_key = (
        f"{STAGED_INPUT_DIRNAME}/{STAGED_VARIANT_DIRNAME}/"
        f"{PREPARED_VARIANT_BINDING_FILENAME}"
    )
    asset_key = (
        f"{STAGED_INPUT_DIRNAME}/{STAGED_VARIANT_DIRNAME}/{prepared.sp_att_asset_name}"
    )
    if prepared.binding_content != contents[binding_key]:
        raise ValueError("prepared binding bytes do not match declared staged bytes.")
    if prepared.sp_att_content != contents[asset_key]:
        raise ValueError("prepared .sp.att bytes do not match declared staged bytes.")


def _list_exact(path: Path, expected: frozenset[str], *, root: Path) -> list[str]:
    names = list_directory_no_follow_limited(
        path, max_entries=len(expected), containment_root=root
    )
    actual = set(names)
    if len(names) > len(expected) or actual != expected:
        raise ValueError(
            f"{path} entries must be exact; missing={sorted(expected - actual)!r}, "
            f"unknown={sorted(actual - expected)!r}."
        )
    return names


def _require_dir(path: Path, root: Path) -> None:
    info = stat_no_follow(path, containment_root=root)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"{path} must be a directory.")


def _require_file(path: Path, root: Path) -> None:
    info = stat_no_follow(path, containment_root=root)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{path} must be a regular file.")


def _read_limited(path: Path, maximum: int, *, containment_root: Path | None) -> bytes:
    content = read_bytes_limited_no_follow(
        path, max_bytes=maximum, containment_root=containment_root
    )
    if len(content) > maximum:
        raise ValueError(f"{path} exceeds its {maximum} byte limit.")
    return content


def _cap_for(key: str, caller: _Caller) -> int:
    if key == (
        f"{STAGED_INPUT_DIRNAME}/{STAGED_VARIANT_DIRNAME}/"
        f"{PREPARED_VARIANT_HANDOFF_FILENAME}"
    ):
        return caller.max_manifest_bytes
    if key == _state_file_key(caller):
        return caller.max_state_bytes
    return caller.max_asset_bytes


def _state_file_key(caller: _Caller) -> str:
    return (
        f"{STAGED_INPUT_DIRNAME}/{STAGED_STATES_DIRNAME}/"
        f"{caller.source}/{caller.cycle_name}{STATE_SUFFIX}"
    )


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _exact_keys(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} keys must be exact; missing={sorted(expected - actual)!r}, "
            f"unknown={sorted(actual - expected)!r}."
        )


def _absolute(value: Path | str, label: str) -> Path:
    if not isinstance(value, Path | str):
        raise TypeError(f"{label} must be an absolute path.")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path.")
    return path


def _reject_dot_parts(path: Path, label: str) -> None:
    if any(part in {".", ".."} for part in path.parts):
        raise ValueError(f"{label} must not contain '.' or '..' components.")


def _disjoint(path: Path, work: Path, label: str) -> None:
    if path == work:
        raise ValueError(f"{label} must be outside claimed work.")
    for left, right in ((path, work), (work, path)):
        try:
            left.relative_to(right)
        except ValueError:
            continue
        raise ValueError(f"{label} must not overlap claimed work.")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string.")
    return value


def _identifier(value: Any, label: str) -> str:
    text = _text(value, label)
    if ".." in text or _COMPONENT.fullmatch(text) is None:
        raise ValueError(f"{label} must be a safe ASCII component.")
    return text
