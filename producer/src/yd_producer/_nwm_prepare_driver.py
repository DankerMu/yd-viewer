"""Prepare-only NWM library driver. Runs under the pinned NWM interpreter."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pathlib
import shutil
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from packages.common.grid_signature import canonical_json_bytes
from packages.common.source_identity import normalize_source_id
from workers.grid_registry.input_record import GridSnapshotInputError, read_input_record
from workers.grid_registry.registry import RegistrationError, prepare_snapshot
from workers.mapping_builder.algorithm import (
    MappingAlgorithmError,
    algorithm_id,
    assign_shud_forcing_index,
    derive_used_cell_subset,
    nearest_cell_barycenter_geodesic_v1,
    verify_small_basin_gate,
)
from workers.mapping_builder.binding import (
    BindingArtifactError,
    emit_direct_grid_manifest_and_binding,
)
from workers.mapping_builder.cli import _parse_mesh_nodes
from workers.mapping_builder.integrity import BaselineIntegrityError, verify_package_crs
from workers.mapping_builder.rewrite import (
    SpAttRewriteError,
    copy_and_rewrite_sp_att_forc,
)
from workers.mapping_builder.z_policy_verdict import (
    SAMPLER_RULE_ID,
    PackageProjection,
    UsedCell,
    VerdictResolutionError,
    build_z_policy,
    resolve_verdict,
    sample_per_cell_z,
)

_SOURCES = frozenset({"gfs", "ifs"})
_SOURCE_GRID_DIR = {"gfs": "gfs", "ifs": "IFS"}
# Same twelve native names as yd_producer._native_input.NATIVE_MODEL_FILENAMES.
# This script runs under the NWM interpreter and must not import daily yd modules.
_NATIVE_FILES = (
    "yd.cfg.ic",
    "yd.cfg.para",
    "yd.cfg.calib",
    "yd.sp.mesh",
    "yd.sp.att",
    "yd.sp.riv",
    "yd.sp.rivseg",
    "yd.para.lc",
    "yd.para.soil",
    "yd.para.geol",
    "yd.tsd.lai",
    "yd.tsd.mf",
)
_COPIED_NATIVE_FILES = tuple(name for name in _NATIVE_FILES if name != "yd.sp.att")
_RIVER_IDENTITY_FILES = ("yd.sp.mesh", "yd.sp.riv", "yd.sp.rivseg")
_CONTRACT_KEYS = (
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
)
_NWM_PIN = "8ae9b8f29c8b72c574e8cbd95f2994160bd42832"
_HANDOFF_SCHEMA = "yd.prepare.direct-grid-handoff.v2"
_PROJECT_NAME = "yd"
_BASIN_ID = "yd"
_SP_ATT_ASSET = "yd.sp.att"
_BINDING_NAME = "yd.binding"
_HANDOFF_NAME = "yd.direct-grid-handoff.json"
_D11_SP_ATT_PATH = "input/yd.sp.att"
_CHECKSUM_PREFIX = "sha256:"
_OUTPUT_COUNT = 14


class PrepareDriverError(Exception):
    """Driver failure reported on stderr without a traceback."""


class _InMemorySnapshotLoader:
    def __init__(self, source: str, grid_id: str, snapshot, cells) -> None:
        self._source = normalize_source_id(source)
        self._grid_id = grid_id
        self._snapshot = snapshot
        self._cells = cells

    def find_snapshot_by_identity(self, source_id: str, grid_id: str):
        if normalize_source_id(source_id) == self._source and grid_id == self._grid_id:
            return self._snapshot, self._cells
        return None


def _fail(message: str) -> None:
    raise PrepareDriverError(message)


def _require_directory(path: pathlib.Path, label: str) -> pathlib.Path:
    if not path.is_absolute():
        _fail(f"{label} must be an absolute directory: {path}")
    if not path.exists():
        _fail(f"{label} does not exist: {path}")
    if not path.is_dir():
        _fail(f"{label} is not a directory: {path}")
    return path


def _require_file(path: pathlib.Path, label: str) -> pathlib.Path:
    if not path.is_file():
        _fail(f"{label} is missing or not a regular file: {path}")
    return path


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: pathlib.Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _digest(payload: Mapping[str, Any]) -> str:
    return _sha256_bytes(canonical_json_bytes(payload))


def _prefixed(hex_digest: str) -> str:
    if hex_digest.startswith(_CHECKSUM_PREFIX):
        return hex_digest
    return f"{_CHECKSUM_PREFIX}{hex_digest}"


def _canonical_handoff_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _original_native_checksums(baseline: pathlib.Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for name in _NATIVE_FILES:
        checksums[name] = _sha256_file(
            _require_file(baseline / name, f"baseline {name}")
        )
    return checksums


def _copy_native_files(baseline: pathlib.Path, output: pathlib.Path) -> None:
    for name in _COPIED_NATIVE_FILES:
        shutil.copyfile(baseline / name, output / name)


def _grid_paths(
    checkout: pathlib.Path, source: str, grid_id: str
) -> tuple[pathlib.Path, pathlib.Path, str]:
    physical = _SOURCE_GRID_DIR[source]
    uri = f"canonical/{physical}/grid/{grid_id}/grid.json"
    directory = checkout / "canonical" / physical / "grid" / grid_id
    return directory / "grid.json", directory / "grid_snapshot_metadata.json", uri


def _used_cells_for_sampler(used_cells) -> tuple[UsedCell, ...]:
    return tuple(
        UsedCell(
            cell_id=cell.grid_cell_id,
            wgs84_lon=float(cell.longitude),
            wgs84_lat=float(cell.latitude),
        )
        for cell in used_cells
    )


def _project_contract(section: Mapping[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in _CONTRACT_KEYS:
        if key not in section:
            _fail(f"NWM contract section is missing {key}")
        projected[key] = section[key]
    for key in ("binding_checksum", "sp_att_checksum"):
        value = projected[key]
        if not isinstance(value, str) or not value:
            _fail(f"{key} must be a non-empty hex digest")
        projected[key] = _prefixed(value)
    return projected


def _write_handoff(
    output: pathlib.Path,
    *,
    source: str,
    grid_id: str,
    identities: Mapping[str, str],
    contract: Mapping[str, Any],
    file_checksums: Mapping[str, str],
) -> None:
    payload = {
        "basin_id": identities["basin_id"],
        "basin_version_id": identities["basin_version_id"],
        "direct_grid_forcing_contract": contract,
        "file_checksums": dict(file_checksums),
        "model_id": identities["model_id"],
        "project_name": _PROJECT_NAME,
        "river_network_version_id": identities["river_network_version_id"],
        "schema_version": _HANDOFF_SCHEMA,
        "source_id": source,
        "sp_att_asset_name": _SP_ATT_ASSET,
    }
    (output / _HANDOFF_NAME).write_bytes(_canonical_handoff_bytes(payload))


def build_variant(
    *, source: str, grid_id: str, baseline: pathlib.Path, output: pathlib.Path
) -> None:
    if source not in _SOURCES:
        _fail(f"source must be gfs or ifs, got {source!r}")
    baseline = _require_directory(baseline, "baseline")
    output = _require_directory(output, "output")
    leftover = [name for name in os.listdir(output) if name not in {".", ".."}]
    if leftover:
        _fail(f"output directory is not empty: {output} ({sorted(leftover)!r})")

    original_checksums = _original_native_checksums(baseline)
    basin_version_id = "yd-" + _digest(
        {name: original_checksums[name] for name in _NATIVE_FILES}
    )
    river_network_version_id = "yd-river-" + _digest(
        {name: original_checksums[name] for name in _RIVER_IDENTITY_FILES}
    )

    checkout = pathlib.Path.cwd()
    grid_json, metadata, uri = _grid_paths(checkout, source, grid_id)
    _require_file(grid_json, "canonical grid.json")
    _require_file(metadata, "canonical grid_snapshot_metadata.json")
    model_crs_wkt = verify_package_crs(baseline).wkt

    record = read_input_record(
        source,
        grid_json,
        metadata,
        grid_definition_uri=uri,
    )
    snapshot, cells = prepare_snapshot(record, source_id=source)
    loader = _InMemorySnapshotLoader(source, record.grid_id, snapshot, cells)
    ownerships = nearest_cell_barycenter_geodesic_v1(baseline, source, grid_id, loader)
    used_cells = derive_used_cell_subset(ownerships, cells)
    shud_forcing_index = assign_shud_forcing_index(used_cells)
    verify_small_basin_gate(used_cells, approval=None)

    _copy_native_files(baseline, output)
    copy_and_rewrite_sp_att_forc(
        baseline_att_path=baseline / _SP_ATT_ASSET,
        variant_att_path=output / _SP_ATT_ASSET,
        ownership=ownerships,
        shud_forcing_index=shud_forcing_index,
        used_cell_count=len(used_cells),
    )

    output_checksums = dict(original_checksums)
    output_checksums[_SP_ATT_ASSET] = _sha256_file(output / _SP_ATT_ASSET)

    verdict = resolve_verdict()
    z_policy = dataclasses.replace(
        build_z_policy(verdict),
        per_cell_z=sample_per_cell_z(
            _used_cells_for_sampler(used_cells),
            _parse_mesh_nodes(baseline),
            PackageProjection.from_prj_wkt(model_crs_wkt),
        ),
    )

    model_id = (
        "yd-"
        + source
        + "-"
        + _digest(
            {
                "algorithm_id": algorithm_id,
                "basin_version_id": basin_version_id,
                "grid_signature": snapshot.grid_signature,
                "nwm_pin": _NWM_PIN,
                "river_network_version_id": river_network_version_id,
                "sampler_rule_id": SAMPLER_RULE_ID,
                "source": source,
            }
        )
    )
    identities = {
        "basin_id": _BASIN_ID,
        "basin_version_id": basin_version_id,
        "model_id": model_id,
        "river_network_version_id": river_network_version_id,
    }

    manifest, binding = emit_direct_grid_manifest_and_binding(
        used_cells=used_cells,
        snapshot_cells=cells,
        shud_forcing_index=shud_forcing_index,
        mapping_asset_identity=model_id,
        model_input_package_id=model_id,
        sp_att_path=_D11_SP_ATT_PATH,
        sp_att_bytes=(output / _SP_ATT_ASSET).read_bytes(),
        applicable_source_ids=(source,),
        grid_id=grid_id,
        grid_signature=snapshot.grid_signature,
        z_policy=z_policy,
        binding_uri=f"models/{model_id}/direct-grid/binding.json",
        model_crs_wkt=model_crs_wkt,
    )
    (output / _BINDING_NAME).write_bytes(binding.bytes)
    output_checksums[_BINDING_NAME] = _sha256_bytes(binding.bytes)
    contract = _project_contract(manifest.to_contract_section_dict())
    _write_handoff(
        output,
        source=source,
        grid_id=grid_id,
        identities=identities,
        contract=contract,
        file_checksums={
            name: _prefixed(digest) for name, digest in output_checksums.items()
        },
    )
    written = sorted(name for name in os.listdir(output) if (output / name).is_file())
    expected = sorted((*_NATIVE_FILES, _BINDING_NAME, _HANDOFF_NAME))
    if written != expected or len(written) != _OUTPUT_COUNT:
        _fail(f"driver did not write the fixed fourteen files: {written!r}")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="yd-nwm-prepare-driver")
    parser.add_argument("--source", required=True, choices=sorted(_SOURCES))
    parser.add_argument("--grid-id", required=True)
    parser.add_argument("--baseline", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        build_variant(
            source=args.source,
            grid_id=args.grid_id,
            baseline=args.baseline,
            output=args.output,
        )
    except (
        PrepareDriverError,
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        KeyError,
        GridSnapshotInputError,
        RegistrationError,
        MappingAlgorithmError,
        BindingArtifactError,
        BaselineIntegrityError,
        SpAttRewriteError,
        VerdictResolutionError,
    ) as cop:
        print(str(cop), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
