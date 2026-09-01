"""Independent literals and directory builders for Issue #15 assembly tests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from yd_producer.assemble import (
    WorkIdentity,
    WorkRegistry,
    assemble,
    stage_work_registry,
)
from yd_producer.forcing import (
    DirectGridForcingContract,
    DirectGridStationBinding,
    ForcingProductionResult,
)
from yd_producer.forcing.grid_identity import grid_identity_hash
from yd_producer.forcing.producer import GridPoint
from yd_producer.store.object_store import LocalObjectStore

CYCLE_00 = datetime(2026, 5, 7, 0, tzinfo=UTC)
CYCLE_12 = datetime(2026, 5, 7, 12, tzinfo=UTC)
BINDING = b'{"binding":"literal"}\n'
SP_ATT = b"2 1\nTRI\tA\tB\tC\tFORC\n1\t0\t0\t0\t1\n2\t0\t0\t0\t2\n"
BINDING_SHA = "54667e8692f419b59ccbc48b7767ff6f9008b5932b50c7ccd8827f5cd130d420"
SP_ATT_SHA = "378d3048687c872f30d3a5a29399ed25ac5ccd060c389704fb173d5010a53d3a"
INDEX_LITERAL = (
    b"ID\tLon\tLat\tX\tY\tZ\tFilename\n"
    b"1\t1\t2\t3\t4\t5\tX1.csv\n2\t6\t7\t8\t9\t10\tX2.csv\n"
)
INDEX = (
    b"2 20260507\nshud\nID\tLon\tLat\tX\tY\tZ\tFilename\n"
    b"1\t1\t2\t3\t4\t5\tX1.csv\n2\t6\t7\t8\t9\t10\tX2.csv\n"
)
CSV_ONE = b"1\t6\t20260507\t20260507\nTime_Day\tPrecip\tTemp\tRH\tWind\tRN\n0\t1\t2\t3\t4\t5\n"
CSV_TWO = b"1\t6\t20260507\t20260507\nTime_Day\tPrecip\tTemp\tRH\tWind\tRN\n0\t6\t7\t8\t9\t10\n"
PARAMETER_TEMPLATE = (
    b"KEEP = unchanged\n"
    b"START = old\n"
    b"END = {{END}}\n"
    b"DT_QR_DOWN = ${DT_QR_DOWN}\n"
    b"Update_IC_STEP = old\n"
    b"# BINARY_OUTPUT = comment only\n"
)
PARAMETER_EXPECTED = (
    b"KEEP = unchanged\n"
    b"START = 0\n"
    b"END = 7\n"
    b"DT_QR_DOWN = 60\n"
    b"Update_IC_STEP = 720\n"
    b"# BINARY_OUTPUT = comment only\n"
    b"BINARY_OUTPUT = 1\n"
    b"ASCII_OUTPUT = 0\n"
)
PARAMETER_SAME_LINE = b"keep {{START}} ${END} {{DT_QR_DOWN}} trailing\n"
PARAMETER_SAME_LINE_EXPECTED = (
    b"keep 0 7 60 trailing\nUpdate_IC_STEP = 720\nBINARY_OUTPUT = 1\nASCII_OUTPUT = 0\n"
)
MANIFEST_BYTES = b'{"basin_slug":"demo"}'
REGISTRY_JSON = {
    "gfs": (
        b'{"models":[{"basin_id":"basin_a","basin_version_id":"basin_v1",'
        b'"manifest_uri":"models/demo_model/manifest.json","model_id":"demo_model",'
        b'"model_package_uri":"models/demo_model/package","resource_profile":{'
        b'"direct_grid_forcing":{"applicable_source_ids":["gfs"],'
        b'"binding_checksum":"sha256:54667e8692f419b59ccbc48b7767ff6f9008b5932b50c7ccd8827f5cd130d420",'
        b'"binding_uri":"models/demo_model/direct-grid/binding.json",'
        b'"forcing_mapping_mode":"direct_grid","grid_id":"gfs_grid",'
        b'"grid_signature":"grid-signature","model_input_package_id":"model-input-v1",'
        b'"sp_att_checksum":"378d3048687c872f30d3a5a29399ed25ac5ccd060c389704fb173d5010a53d3a",'
        b'"sp_att_path":"input/demo.sp.att","station_bindings":['
        b'{"forcing_filename":"X1.csv","grid_cell_id":"cell-one","grid_id":"gfs_grid",'
        b'"latitude":2.0,"longitude":1.0,"shud_forcing_index":1,"station_id":"station-one",'
        b'"x":3.0,"y":4.0,"z":5.0},'
        b'{"forcing_filename":"X2.csv","grid_cell_id":"cell-two","grid_id":"gfs_grid",'
        b'"latitude":7.0,"longitude":6.0,"shud_forcing_index":2,"station_id":"station-two",'
        b'"x":8.0,"y":9.0,"z":10.0}]},"shud_input_name":"demo"},'
        b'"river_network_version_id":"rivnet_v1"}]}'
    ),
    "ifs": (
        b'{"models":[{"basin_id":"basin_a","basin_version_id":"basin_v1",'
        b'"manifest_uri":"models/demo_model/manifest.json","model_id":"demo_model",'
        b'"model_package_uri":"models/demo_model/package","resource_profile":{'
        b'"direct_grid_forcing":{"applicable_source_ids":["ifs"],'
        b'"binding_checksum":"sha256:54667e8692f419b59ccbc48b7767ff6f9008b5932b50c7ccd8827f5cd130d420",'
        b'"binding_uri":"models/demo_model/direct-grid/binding.json",'
        b'"forcing_mapping_mode":"direct_grid","grid_id":"ifs_grid",'
        b'"grid_signature":"grid-signature","model_input_package_id":"model-input-v1",'
        b'"sp_att_checksum":"378d3048687c872f30d3a5a29399ed25ac5ccd060c389704fb173d5010a53d3a",'
        b'"sp_att_path":"input/demo.sp.att","station_bindings":['
        b'{"forcing_filename":"X1.csv","grid_cell_id":"cell-one","grid_id":"ifs_grid",'
        b'"latitude":2.0,"longitude":1.0,"shud_forcing_index":1,"station_id":"station-one",'
        b'"x":3.0,"y":4.0,"z":5.0},'
        b'{"forcing_filename":"X2.csv","grid_cell_id":"cell-two","grid_id":"ifs_grid",'
        b'"latitude":7.0,"longitude":6.0,"shud_forcing_index":2,"station_id":"station-two",'
        b'"x":8.0,"y":9.0,"z":10.0}]},"shud_input_name":"demo"},'
        b'"river_network_version_id":"rivnet_v1"}]}'
    ),
}
IDENTITY_FIELDS = {
    "source_id": "gfs",
    "cycle_time": CYCLE_00,
    "model_id": "demo_model",
    "basin_id": "basin_a",
    "basin_version_id": "basin_v1",
    "river_network_version_id": "rivnet_v1",
    "project_name": "demo",
}


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def identity(cycle: datetime = CYCLE_00, *, source: str = "gfs") -> WorkIdentity:
    return WorkIdentity(
        source_id=source,
        cycle_time=cycle,
        model_id="demo_model",
        basin_id="basin_a",
        basin_version_id="basin_v1",
        river_network_version_id="rivnet_v1",
        project_name="demo",
    )


def contract(
    value: WorkIdentity, *, binding: bytes = BINDING, sp_att: bytes = SP_ATT
) -> DirectGridForcingContract:
    return DirectGridForcingContract(
        forcing_mapping_mode="direct_grid",
        binding_uri=f"models/{value.model_id}/direct-grid/binding.json",
        binding_checksum=f"sha256:{digest(binding)}",
        model_input_package_id="model-input-v1",
        sp_att_path=f"input/{value.project_name}.sp.att",
        sp_att_checksum=digest(sp_att),
        applicable_source_ids=(value.source_id,),
        grid_id=f"{value.source_id}_grid",
        grid_signature="grid-signature",
        stations=(
            DirectGridStationBinding(
                station_id="station-one",
                shud_forcing_index=1,
                forcing_filename="X1.csv",
                longitude=1.0,
                latitude=2.0,
                x=3.0,
                y=4.0,
                z=5.0,
                grid_id=f"{value.source_id}_grid",
                grid_cell_id="cell-one",
            ),
            DirectGridStationBinding(
                station_id="station-two",
                shud_forcing_index=2,
                forcing_filename="X2.csv",
                longitude=6.0,
                latitude=7.0,
                x=8.0,
                y=9.0,
                z=10.0,
                grid_id=f"{value.source_id}_grid",
                grid_cell_id="cell-two",
            ),
        ),
    )


def work_dir(root: Path, value: WorkIdentity) -> Path:
    path = root / value.source_id / value.cycle_time.strftime("%Y%m%d%H")
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_bytes(value: WorkIdentity, *, minute: int | None = None) -> bytes:
    actual_minute = (
        minute if minute is not None else round(value.cycle_time.timestamp() / 60)
    )
    return (
        f"1 6 {actual_minute}\nIndex Canopy Snow Surface Unsat GW\n1 0 0 0 0 0\n"
    ).encode()


def write_state(
    root: Path, value: WorkIdentity, *, content: bytes | None = None
) -> Path:
    path = root / value.source_id / f"{value.cycle_time:%Y%m%d%H}.cfg.ic"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if content is not None else state_bytes(value))
    return path


def write_variant(
    root: Path, value: WorkIdentity, *, parameter: bytes = PARAMETER_TEMPLATE
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{value.project_name}.cfg.ic").write_bytes(b"CALIBRATED-STATE\n")
    (root / f"{value.project_name}.para").write_bytes(parameter)
    (root / "nested").mkdir()
    (root / "nested" / "ordinary.dat").write_bytes(b"nested bytes\n")
    return root


def file_repository_contract(value: WorkIdentity) -> DirectGridForcingContract:
    return replace(
        contract(value),
        grid_signature=grid_identity_hash(
            (GridPoint("cell-one", 1.0, 2.0), GridPoint("cell-two", 6.0, 7.0))
        ),
    )


def write_file_repository_canonical_catalog(
    store: LocalObjectStore, value: WorkIdentity
) -> None:
    variables = {
        "prcp_rate_or_amount": ("mm/day", 1.0),
        "air_temperature_2m": ("degC", 10.0),
        "relative_humidity_2m": ("0-1", 0.5),
        "wind_u_10m": ("m/s", 3.0),
        "wind_v_10m": ("m/s", 4.0),
        "pressure_surface": ("Pa", 101000.0),
        "shortwave_down": ("W/m2", 250.0),
    }
    grid_key = f"canonical/{value.source_id}/grid/{value.source_id}_grid/grid.json"
    grid = {
        "cells": [
            {"id": "cell-one", "lon": 1.0, "lat": 2.0},
            {"id": "cell-two", "lon": 6.0, "lat": 7.0},
        ]
    }
    store.write_bytes_atomic(grid_key, json.dumps(grid, separators=(",", ":")).encode())
    products: list[dict[str, Any]] = []
    for variable, (unit, number) in variables.items():
        lead = 3 if variable in {"prcp_rate_or_amount", "shortwave_down"} else 0
        identifier = (
            f"{value.source_id}_{value.cycle_time:%Y%m%d%H}_{variable}_f{lead:03d}"
        )
        key = f"canonical/{value.source_id}/{value.cycle_time:%Y%m%d%H}/{variable}/{identifier}.nc"
        content = canonical_netcdf(variable, value, unit, number, lead)
        store.write_bytes_atomic(key, content)
        products.append(
            {
                "canonical_product_id": identifier,
                "source_id": value.source_id,
                "source_version": f"{value.cycle_time:%Y%m%d%H}",
                "cycle_time": value.cycle_time.isoformat().replace("+00:00", "Z"),
                "valid_time": (value.cycle_time + timedelta(hours=lead))
                .isoformat()
                .replace("+00:00", "Z"),
                "lead_time_hours": lead,
                "variable": variable,
                "unit": unit,
                "grid_id": f"{value.source_id}_grid",
                "grid_definition_uri": grid_key,
                "native_time_resolution": "3h",
                "native_spatial_resolution": "1deg",
                "object_uri": key,
                "checksum": digest(content),
                "quality_flag": "ok",
                "lineage_json": {},
            }
        )
    catalog = {
        "schema_version": "nhms.canonical.product_catalog.v1",
        "source_id": value.source_id,
        "cycle_time": value.cycle_time.isoformat().replace("+00:00", "Z"),
        "products": products,
    }
    store.write_bytes_atomic(
        f"canonical/{value.source_id}/{value.cycle_time:%Y%m%d%H}/_catalog/catalog.json",
        json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode(),
    )


def canonical_netcdf(
    variable: str, value: WorkIdentity, unit: str, number: float, lead: int
) -> bytes:
    import tempfile

    import xarray as xr

    dataset = xr.Dataset(
        data_vars={variable: ("point", [number, number + 1])},
        coords={
            "point": ["cell-one", "cell-two"],
            "longitude": ("point", [1.0, 6.0]),
            "latitude": ("point", [2.0, 7.0]),
        },
        attrs={
            "cycle_time": value.cycle_time.isoformat(),
            "valid_time": (value.cycle_time + timedelta(hours=lead)).isoformat(),
            "lead_time_hours": lead,
            "unit": unit,
            "grid_id": f"{value.source_id}_grid",
        },
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".nc") as temporary:
            dataset.to_netcdf(temporary.name, engine="netcdf4", format="NETCDF4")
            temporary.seek(0)
            return temporary.read()
    finally:
        dataset.close()


def forcing_package_key(value: WorkIdentity) -> str:
    return (
        f"forcing/{value.source_id}/{value.cycle_time:%Y%m%d%H}/"
        f"{value.basin_version_id}/{value.model_id}"
    )


def write_forcing_package(
    object_root: Path,
    value: WorkIdentity,
    *,
    index: bytes = INDEX,
    csv_one: bytes = CSV_ONE,
    csv_two: bytes = CSV_TWO,
    index_checksum: str | None = None,
    csv_one_checksum: str | None = None,
    status: str = "forcing_ready",
    index_role: str = "shud_forcing",
    index_relative: str = "shud/stations.tsd.forc",
    include_legacy: bool = False,
    include_debug_index: bool = False,
    extra_files: list[dict[str, Any]] | None = None,
    undeclared: dict[str, bytes] | None = None,
    raw_manifest: bytes | None = None,
    mutate_manifest: Any | None = None,
    mutate_result: Any | None = None,
) -> ForcingProductionResult:
    package = object_root / forcing_package_key(value)
    shud = package / "shud"
    shud.mkdir(parents=True, exist_ok=True)
    (package / index_relative).write_bytes(index)
    (shud / "X1.csv").write_bytes(csv_one)
    (shud / "X2.csv").write_bytes(csv_two)
    prefix = forcing_package_key(value) + "/"
    entries = [
        {
            "role": index_role,
            "relative_path": index_relative,
            "uri": f"{prefix}{index_relative}",
            "checksum": index_checksum or digest(index),
        },
        {
            "role": "shud_forcing_csv",
            "relative_path": "shud/X1.csv",
            "uri": f"{prefix}shud/X1.csv",
            "checksum": csv_one_checksum or digest(csv_one),
        },
        {
            "role": "shud_forcing_csv",
            "relative_path": "shud/X2.csv",
            "uri": f"{prefix}shud/X2.csv",
            "checksum": digest(csv_two),
        },
    ]
    if include_legacy:
        (shud / "qhh.tsd.forc").write_bytes(index)
        entries.append(
            {
                "role": "shud_forcing",
                "relative_path": "shud/qhh.tsd.forc",
                "uri": f"{prefix}shud/qhh.tsd.forc",
                "checksum": digest(index),
            }
        )
    if include_debug_index:
        entries.append(
            {
                "role": "debug",
                "relative_path": "shud/stations.tsd.forc",
                "uri": f"{prefix}shud/stations.tsd.forc",
                "checksum": digest(index),
            }
        )
    entries.extend(extra_files or [])
    manifest = {
        "forcing_version_id": "forc_gfs_2026050700_demo_model",
        "source_id": value.source_id,
        "cycle_time": value.cycle_time.isoformat().replace("+00:00", "Z"),
        "model_id": value.model_id,
        "files": entries,
    }
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    content = (
        raw_manifest
        if raw_manifest is not None
        else json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    )
    (package / "forcing_package.json").write_bytes(content)
    for relative, payload in (undeclared or {}).items():
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    result = ForcingProductionResult(
        status=status,
        forcing_version_id=manifest["forcing_version_id"]
        if raw_manifest is None
        else "forc_gfs_2026050700_demo_model",
        forcing_package_uri=prefix,
        checksum=digest(content),
        station_count=2,
        timestep_count=1,
        file_uris={"package_manifest": f"{prefix}forcing_package.json"},
    )
    if mutate_result is not None:
        result = mutate_result(result)
    return result


def handoff_paths(store: LocalObjectStore, value: WorkIdentity) -> tuple[Path, Path]:
    package = forcing_package_key(value)
    run = f"fcst_{value.source_id}_{value.cycle_time:%Y%m%d%H}_{value.model_id}"
    return (
        store.resolve_path(f"runs/{run}/input/forcing_domain_handoff.json"),
        store.resolve_path(f"{package}/forcing_domain_package.json"),
    )


def tree_snapshot(root: Path) -> tuple[tuple[str, str, bytes | str], ...]:
    values: list[tuple[str, str, bytes | str]] = []
    if not root.exists():
        return ()
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            values.append((relative, "symlink", os.readlink(path)))
        elif stat.S_ISDIR(info.st_mode):
            values.append((relative, "directory", ""))
        elif stat.S_ISREG(info.st_mode):
            values.append((relative, "file", path.read_bytes()))
        else:
            values.append((relative, f"mode:{info.st_mode}", ""))
    return tuple(values)


def stage(
    root: Path, value: WorkIdentity | None = None, **kwargs: Any
) -> tuple[WorkIdentity, Path, WorkRegistry]:
    current = value or identity()
    work = work_dir(root, current)
    registry = stage_work_registry(
        work_root=root,
        identity=current,
        contract=kwargs.get("contract") or contract(current),
        binding_content=kwargs.get("binding_content", BINDING),
        sp_att_content=kwargs.get("sp_att_content", SP_ATT),
        max_asset_bytes=kwargs.get("max_asset_bytes", 4096),
    )
    return current, work, registry


def prepared(root: Path, value: WorkIdentity | None = None):
    current, work, registry = stage(root, value)
    variant = write_variant(root / "variant", current)
    states = root / "states"
    state = write_state(states, current)
    forcing = write_forcing_package(registry.object_store_root, current)
    return current, work, registry, variant, states, state, forcing


def sources(variant: Path, states: Path, store: Path):
    return tree_snapshot(variant), tree_snapshot(states), tree_snapshot(store)


def run_assemble(prepared_inputs):
    _value, _work, registry, variant, states, state, forcing = prepared_inputs
    return assemble(
        registry=registry,
        variant_dir=variant,
        forcing=forcing,
        states_root=states,
        state_path=state,
    )
