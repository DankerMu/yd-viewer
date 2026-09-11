"""Shared legacy/native SHUD run-directory stage writer."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from yd_producer import _assemble_fs
from yd_producer._assemble_io import AssemblyInputs, AssemblyIO
from yd_producer._native_input import (
    NATIVE_INPUT_DIR,
    NATIVE_INPUT_PARENT,
    PREPARED_VARIANT_BINDING_FILENAME,
    PREPARED_VARIANT_CALIBRATED_STATE_FILENAME,
    PREPARED_VARIANT_HANDOFF_FILENAME,
    PREPARED_VARIANT_PARAMETER_FILENAME,
    native_run_paths,
    rewrite_forcing_index_path,
)
from yd_producer.store.object_store import (
    MAX_OBJECT_MANIFEST_BYTES,
    LocalObjectStore,
)

_CSV = re.compile(r"^[A-Za-z0-9_.-]+\.csv$")
_fs = _assemble_fs


def run_paths(root: Path, project: str, native: bool) -> dict[str, Path]:
    if native:
        return native_run_paths(root)
    return {
        "state_path": root / f"{project}.cfg.ic",
        "parameter_path": root / f"{project}.para",
        "forcing_index_path": root / f"{project}.tsd.forc",
    }


def csv_path(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("shud/"):
        raise ValueError("SHUD CSV path must be shud/<basename>.csv.")
    name = value.removeprefix("shud/")
    if "/" in name or not _CSV.fullmatch(name):
        raise ValueError("SHUD CSV basename is unsafe.")
    return name


def write_stage(
    fs: AssemblyIO,
    *,
    stage: Path,
    work: Path,
    root: Path,
    inputs: AssemblyInputs,
    project: str,
    parameter: bytes,
    warm_state: bytes,
    index: dict[str, Any],
    csvs: tuple[dict[str, Any], ...],
    object_store_root: Path,
    native: bool,
) -> None:
    paths = run_paths(stage, project, native)
    variant_root = stage / NATIVE_INPUT_DIR if native else stage
    if native:
        fs.directory(stage / NATIVE_INPUT_PARENT, work, create=True)
        fs.directory(variant_root, work, create=True)
        skipped = {
            Path(PREPARED_VARIANT_CALIBRATED_STATE_FILENAME),
            Path(PREPARED_VARIANT_PARAMETER_FILENAME),
            Path(PREPARED_VARIANT_BINDING_FILENAME),
            Path(PREPARED_VARIANT_HANDOFF_FILENAME),
        }
    else:
        skipped = {Path(f"{project}.{suffix}") for suffix in ("cfg.ic", "para")}
        for relative in inputs.variant_dirs:
            if relative != Path("."):
                fs.directory(stage / relative, work, create=True)
    for relative, source, checksum in inputs.variant_files:
        if relative not in skipped:
            fs.copy_regular(
                source,
                variant_root / relative,
                root,
                work,
                expected_checksum=checksum,
            )
    fs.write_new(paths["parameter_path"], parameter, work)
    fs.write_new(paths["state_path"], warm_state, work)
    store = LocalObjectStore(object_store_root)
    index_source = store.resolve_path(str(index["uri"]))
    if native:
        index_bytes = fs.read_limited(
            index_source,
            MAX_OBJECT_MANIFEST_BYTES,
            store.root,
        )
        _fs.checksum(str(index["checksum"]), index_bytes, "SHUD index")
        fs.write_new(
            paths["forcing_index_path"],
            rewrite_forcing_index_path(index_bytes),
            work,
        )
    else:
        fs.copy_regular(
            index_source,
            paths["forcing_index_path"],
            store.root,
            work,
            expected_checksum=str(index["checksum"]),
        )
    for entry in csvs:
        fs.copy_regular(
            store.resolve_path(str(entry["uri"])),
            stage / csv_path(entry["relative_path"]),
            store.root,
            work,
            expected_checksum=str(entry["checksum"]),
        )
