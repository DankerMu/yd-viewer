# NWM@8ae9b8f2 tests/test_forcing_producer.py
"""yd structural glue: imports.

Moved snapshot bodies retain existing registered adaptations.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

from yd_producer.forcing import (
    CanonicalProduct,
    parse_cycle_time,
    parse_direct_grid_forcing_contract,
)
from yd_producer.forcing.file_store import FileForcingRepository
from yd_producer.store.object_store import (
    LocalObjectStore,
    sha256_bytes,
)


def _file_direct_grid_repository(
    tmp_path: Path,
    *,
    snapshot_overrides: Mapping[str, Any] | None = None,
    duplicate_snapshot_overrides: Mapping[str, Any] | None = None,
) -> tuple[FileForcingRepository, Any, bytes, bytes]:
    store = LocalObjectStore(tmp_path, object_store_prefix="s3://nhms")
    binding_content = b'{"schema_version":"nhms.direct_grid.binding.v1"}'
    sp_att_content = _sp_att_content().encode("utf-8")
    binding_uri = "models/demo/direct-grid/binding.json"
    package_uri = "models/demo/direct-grid/package"
    store.write_bytes_atomic(binding_uri, binding_content)
    store.write_bytes_atomic(f"{package_uri}/input/demo.sp.att", sp_att_content)
    direct_grid = _direct_grid_manifest_for_default_grid()
    direct_grid.update(
        {
            "binding_uri": binding_uri,
            "binding_checksum": sha256_bytes(binding_content),
            "sp_att_path": "input/demo.sp.att",
            "sp_att_checksum": sha256_bytes(sp_att_content),
        }
    )
    snapshot = {
        "source_id": "GFS",
        "grid_id": direct_grid["grid_id"],
        "grid_signature": direct_grid["grid_signature"],
        "grid_snapshot_id": "9dcbb4cf-cdaf-4255-8500-364f75cf2e00",
        "bbox_south": 8.0,
        "bbox_north": 64.0,
        "bbox_west": 63.0,
        "bbox_east": 145.0,
        "superseded_at": None,
    }
    snapshot.update(snapshot_overrides or {})

    def model_entry(model_id: str, projection: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "model_id": model_id,
            "basin_id": "basin_a",
            "basin_version_id": "basin_v1",
            "river_network_version_id": "rivnet_v1",
            "model_package_uri": package_uri,
            "resource_profile": {
                "direct_grid_forcing": direct_grid,
                "canonical_grid_snapshot": dict(projection),
            },
        }

    models = [model_entry("demo_model", snapshot)]
    if duplicate_snapshot_overrides is not None:
        duplicate_snapshot = dict(snapshot)
        duplicate_snapshot.update(duplicate_snapshot_overrides)
        models.append(model_entry("demo_model_duplicate", duplicate_snapshot))
    store.write_bytes_atomic(
        "models/demo/registry.json", json.dumps({"models": models}).encode("utf-8")
    )
    registry_manifest = "models/demo/registry.json"
    repository = FileForcingRepository(
        object_store=store, registry_manifest=registry_manifest
    )
    contract = parse_direct_grid_forcing_contract(direct_grid, source_id="GFS")
    return repository, contract, binding_content, sp_att_content


def _direct_grid_manifest() -> dict[str, Any]:
    return {
        "forcing_mapping_mode": "direct_grid",
        "binding_uri": "models/demo/direct-grid/binding.json",
        "binding_checksum": "sha256:binding",
        "model_input_package_id": "model-input-demo-v1",
        "sp_att_path": "input/qhh.sp.att",
        "sp_att_checksum": "sha256:sp-att",
        "applicable_source_ids": ["GFS"],
        "grid_id": "ifs_gfs_025deg",
        "grid_signature": "sha256:grid-signature",
        "station_bindings": [
            {
                "station_id": "qhh_forc_001",
                "shud_forcing_index": 1,
                "forcing_filename": "X100.95Y36.25.csv",
                "longitude": 100.95,
                "latitude": 36.25,
                "x": 1,
                "y": 2,
                "z": 3657,
                "grid_id": "ifs_gfs_025deg",
                "grid_cell_id": "cell-001",
            },
            {
                "station_id": "qhh_forc_002",
                "shud_forcing_index": 2,
                "forcing_filename": "X101.05Y36.25.csv",
                "longitude": 101.05,
                "latitude": 36.25,
                "x": 2,
                "y": 3,
                "z": -9999,
                "grid_id": "ifs_gfs_025deg",
                "grid_cell_id": "cell-002",
            },
        ],
    }


def _direct_grid_manifest_for_default_grid() -> dict[str, Any]:
    manifest = _direct_grid_manifest()
    manifest.update(
        {
            "binding_checksum": "sha256:binding-actual",
            "sp_att_checksum": "sha256:sp-att-actual",
            "grid_id": "grid_a",
            "grid_signature": "b56a451cd543e6d23dfce1d486fe5fccdb6e14385b283eceec01d3af30870d4c",
        }
    )
    manifest["station_bindings"][0].update(
        {"grid_id": "grid_a", "grid_cell_id": "0", "longitude": -75.0, "latitude": 40.0}
    )
    manifest["station_bindings"][1].update(
        {"grid_id": "grid_a", "grid_cell_id": "1", "longitude": -74.5, "latitude": 40.2}
    )
    return manifest


def _sp_att_content(forc_values: tuple[str | int, ...] = (1, 2)) -> str:
    rows = "\n".join(
        f"{index}\t0\t0\t0\t{value}" for index, value in enumerate(forc_values, start=1)
    )
    return f"2 1\nTRI\tA\tB\tC\tFORC\n{rows}\n"


def _direct_grid_validation_assets(
    *,
    binding_checksum: str = "binding-actual",
    model_input_package_id: str = "model-input-demo-v1",
    sp_att_checksum: str = "sp-att-actual",
    sp_att_content: str | None = None,
) -> dict[str, Any]:
    return {
        "binding_checksum": binding_checksum,
        "model_input_package_id": model_input_package_id,
        "sp_att_checksum": sp_att_checksum,
        "sp_att_content": _sp_att_content()
        if sp_att_content is None
        else sp_att_content,
    }


def _write_canonical_products(
    store: LocalObjectStore,
    *,
    source_id: str = "gfs",
    cycle_time_text: str = "2026050700",
    product_id_prefix: str | None = None,
    forecast_hours: tuple[int, ...] = (0, 3),
    lead_time_by_hour: Mapping[int, int] | None = None,
    omitted_variables: set[str] | None = None,
    omitted_by_time: set[tuple[str, int]] | None = None,
    include_geographic_coords: bool = True,
    values_by_variable: Mapping[str, tuple[float, float, float]] | None = None,
    radiation_variable: str = "shortwave_down",
    longitudes: tuple[float, float, float] = (-75.0, -74.5, -74.0),
    latitudes: tuple[float, float, float] = (40.0, 40.2, 40.4),
) -> tuple[CanonicalProduct, ...]:
    cycle_time = parse_cycle_time(cycle_time_text)
    product_id_prefix = product_id_prefix or source_id.lower()
    lead_time_by_hour = lead_time_by_hour or {}
    omitted_variables = omitted_variables or set()
    omitted_by_time = omitted_by_time or set()
    values_by_variable = values_by_variable or {}
    products: list[CanonicalProduct] = []
    variables = {
        "prcp_rate_or_amount": ("mm/day", 1.0),
        "air_temperature_2m": ("degC", 10.0),
        "relative_humidity_2m": ("0-1", 0.5),
        "wind_u_10m": ("m/s", 3.0),
        "wind_v_10m": ("m/s", 4.0),
        "pressure_surface": ("Pa", 101000.0),
        radiation_variable: ("W/m2", 250.0),
    }
    if omitted_variables:
        variables = {
            variable: details
            for variable, details in variables.items()
            if variable not in omitted_variables
        }
    compact_cycle = cycle_time.strftime("%Y%m%d%H")
    for forecast_hour in forecast_hours:
        valid_time = cycle_time + timedelta(hours=forecast_hour)
        for variable, (unit, base_value) in variables.items():
            if (
                variable in omitted_variables
                or (variable, forecast_hour) in omitted_by_time
            ):
                continue
            if (
                source_id == "gfs"
                and forecast_hour == 0
                and variable in {"prcp_rate_or_amount", radiation_variable}
            ):
                continue
            product_id = (
                f"{product_id_prefix}_{compact_cycle}_{variable}_f{forecast_hour:03d}"
            )
            key = f"canonical/{source_id}/{compact_cycle}/{variable}/{product_id}.nc"
            values = values_by_variable.get(
                variable,
                (
                    base_value + forecast_hour,
                    base_value + forecast_hour + 1.0,
                    base_value + forecast_hour + 2.0,
                ),
            )
            content = _netcdf_bytes(
                variable,
                values=values,
                include_geographic_coords=include_geographic_coords,
                longitudes=longitudes,
                latitudes=latitudes,
                attrs={
                    "cycle_time": cycle_time.isoformat(),
                    "valid_time": valid_time.isoformat(),
                    "lead_time_hours": lead_time_by_hour.get(
                        forecast_hour, forecast_hour
                    ),
                    "unit": unit,
                    "grid_id": "grid_a",
                },
            )
            object_uri = store.write_bytes_atomic(key, content)
            products.append(
                CanonicalProduct(
                    canonical_product_id=product_id,
                    source_id=source_id,
                    cycle_time=cycle_time,
                    valid_time=valid_time,
                    variable=variable,
                    unit=unit,
                    grid_id="grid_a",
                    object_uri=object_uri,
                    checksum=sha256_bytes(content),
                    native_time_resolution="3h",
                    native_spatial_resolution="1deg",
                    lead_time_hours=lead_time_by_hour.get(forecast_hour, forecast_hour),
                )
            )
    return tuple(products)


def _netcdf_bytes(
    variable: str,
    *,
    values: tuple[float, float, float],
    include_geographic_coords: bool = True,
    longitudes: tuple[float, float, float] = (-75.0, -74.5, -74.0),
    latitudes: tuple[float, float, float] = (40.0, 40.2, 40.4),
    cell_ids: tuple[str, ...] = ("0", "1", "2"),
    attrs: Mapping[str, Any] | None = None,
) -> bytes:
    import xarray as xr

    coords: dict[str, Any] = (
        {"point": list(cell_ids)} if len(cell_ids) == 3 else {"point": list(cell_ids)}
    )
    if include_geographic_coords:
        coords.update(
            {
                "longitude": ("point", list(longitudes)),
                "latitude": ("point", list(latitudes)),
            }
        )
    dataset = xr.Dataset(
        data_vars={variable: ("point", list(values))},
        coords=coords,
        attrs=dict(attrs or {}),
    )
    try:
        with tempfile.NamedTemporaryFile(suffix=".nc") as temp_file:
            dataset.to_netcdf(temp_file.name, engine="netcdf4", format="NETCDF4")
            temp_file.seek(0)
            return temp_file.read()
    finally:
        dataset.close()
