"""Synthetic direct-grid forcing fixtures for the yd acceptance suite.

No provenance header: this module is yd-authored.  It holds the builders and
the independent literals (station CSVs, grid signatures, checksums) that the
acceptance tests assert against.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from yd_producer.store.object_store import LocalObjectStore, sha256_bytes

#: Independent literal oracle: SHA-256 over the canonical JSON envelope
#: {"grid_points": [["0",-75.0,40.0],["1",-74.5,40.2],["2",-74.0,40.4]]}
#: where each tuple is (grid_cell_id, round(lon,12), round(lat,12)).
DEFAULT_GRID_SIGNATURE = (
    "b56a451cd543e6d23dfce1d486fe5fccdb6e14385b283eceec01d3af30870d4c"
)

GFS_GRID_ID = "gfs_0p25"
IFS_GRID_ID = "ifs_0p25"

CYCLE_00Z = "2026050700"
CYCLE_12Z = "2026050712"


def direct_grid_binding(
    *,
    grid_id: str = GFS_GRID_ID,
    grid_signature: str = DEFAULT_GRID_SIGNATURE,
    applicable_source_ids: tuple[str, ...] = ("GFS",),
    cell_ids: tuple[str, ...] = ("0", "1"),
    wind_uv: tuple[tuple[float, float], ...] = ((3.0, 4.0), (6.0, 8.0)),
) -> dict:
    stations = []
    paired_wind = wind_uv[: len(cell_ids)]
    for index, (cell_id, (u, v)) in enumerate(
        zip(cell_ids, paired_wind, strict=True), start=1
    ):
        stations.append(
            {
                "station_id": f"forc_{index:03d}",
                "shud_forcing_index": index,
                "forcing_filename": f"X{index}.csv",
                "longitude": -75.0 + (index - 1) * 0.5,
                "latitude": 40.0 + (index - 1) * 0.2,
                "x": index,
                "y": index,
                "z": 100.0 + index,
                "grid_id": grid_id,
                "grid_cell_id": cell_id,
            }
        )
    return {
        "forcing_mapping_mode": "direct_grid",
        "binding_uri": "models/demo/binding.json",
        "binding_checksum": sha256_bytes(
            json.dumps({"schema_version": "nhms.direct_grid.binding.v1"}).encode(
                "utf-8"
            )
        ),
        "model_input_package_id": "model-input-v1",
        "sp_att_path": "input/demo.sp.att",
        "sp_att_checksum": sha256_bytes(sp_att_content().encode("utf-8")),
        "applicable_source_ids": list(applicable_source_ids),
        "grid_id": grid_id,
        "grid_signature": grid_signature,
        "station_bindings": stations,
    }


def canonical_products_for_cycle(
    store: LocalObjectStore,
    *,
    source_id: str,
    cycle_text: str,
    grid_id: str,
    grid_signature: str,
    cell_count: int = 3,
    cell_ids: tuple[str, ...] = ("0", "1", "2"),
    values_by_variable: dict[str, tuple[float, ...]] | None = None,
    grid_definition_uri: str | None = None,
    forecast_hours: tuple[int, ...] = (0,),
) -> list:
    """Write synthetic canonical NetCDF products and their explicit catalog."""

    from test_forcing_producer import _netcdf_bytes

    cycle_time = {
        CYCLE_00Z: datetime(2026, 5, 7, 0, tzinfo=UTC),
        CYCLE_12Z: datetime(2026, 5, 7, 12, tzinfo=UTC),
    }[cycle_text]
    if grid_definition_uri is None:
        grid_definition_uri = (
            "canonical/IFS/grid/ifs_0p25/grid.json"
            if source_id.lower() == "ifs"
            else f"canonical/{source_id}/grid/{grid_id}/grid.json"
        )
    compact_cycle = cycle_time.strftime("%Y%m%d%H")
    longitudes = tuple(-75.0 + i * 0.5 for i in range(cell_count))
    latitudes = tuple(40.0 + i * 0.2 for i in range(cell_count))
    store.write_bytes_atomic(
        grid_definition_uri,
        json.dumps(
            {
                "cells": [
                    {
                        "grid_cell_id": cell_id,
                        "longitude": longitude,
                        "latitude": latitude,
                    }
                    for cell_id, longitude, latitude in zip(
                        cell_ids, longitudes, latitudes, strict=True
                    )
                ]
            }
        ).encode("utf-8"),
    )
    default_values = {
        "prcp_rate_or_amount": (1.0, 2.0, 999.0),
        "air_temperature_2m": (10.0, 20.0, 999.0),
        "relative_humidity_2m": (0.50, 0.75, 999.0),
        "wind_u_10m": (3.0, 6.0, 999.0),
        "wind_v_10m": (4.0, 8.0, 999.0),
        "pressure_surface": (101000.0, 102000.0, 999.0),
        "surface_pressure": (101000.0, 102000.0, 999.0),
        "shortwave_down": (100.0, 200.0, 999.0),
    }
    variables = (
        "prcp_rate_or_amount",
        "air_temperature_2m",
        "relative_humidity_2m",
        "wind_u_10m",
        "wind_v_10m",
        "surface_pressure" if source_id.lower() == "ifs" else "pressure_surface",
        "shortwave_down",
    )
    products = []
    for forecast_hour in forecast_hours:
        valid_time = cycle_time + timedelta(hours=forecast_hour)
        for variable in variables:
            values = (values_by_variable or {}).get(variable) or default_values[
                variable
            ]
            product_id = f"{source_id}_{compact_cycle}_{variable}_f{forecast_hour:03d}"
            key = f"canonical/{source_id}/{compact_cycle}/{variable}/{product_id}.nc"
            content = _netcdf_bytes(
                variable,
                values=values,
                longitudes=longitudes,
                latitudes=latitudes,
                cell_ids=cell_ids,
                attrs={
                    "cycle_time": cycle_time.isoformat(),
                    "valid_time": valid_time.isoformat(),
                    "lead_time_hours": forecast_hour,
                    "unit": _unit_for(variable),
                    "grid_id": grid_id,
                },
            )
            uri = store.write_bytes_atomic(key, content)
            product = {
                "canonical_product_id": product_id,
                "source_id": source_id,
                "source_version": compact_cycle,
                "cycle_time": cycle_time,
                "valid_time": valid_time,
                "lead_time_hours": forecast_hour,
                "variable": variable,
                "unit": _unit_for(variable),
                "grid_id": grid_id,
                "grid_definition_uri": grid_definition_uri,
                "native_time_resolution": "3h",
                "native_spatial_resolution": "0.25deg",
                "object_uri": uri,
                "checksum": sha256_bytes(content),
                "quality_flag": "ok",
                "lineage_json": {},
            }
            products.append(product)
    write_canonical_catalog(
        store,
        source_id=source_id,
        cycle_time=cycle_time,
        products=products,
    )
    return products


def write_canonical_catalog(
    store: LocalObjectStore,
    *,
    source_id: str,
    cycle_time: datetime,
    products: list[dict],
) -> str:
    """Write the bounded file-backend catalog used by forcing production."""

    serialized_rows = []
    for product in products:
        row = dict(product)
        row["cycle_time"] = time_iso(product["cycle_time"])
        row["valid_time"] = time_iso(product["valid_time"])
        serialized_rows.append(row)
    payload = {
        "schema_version": "nhms.canonical.product_catalog.v1",
        "source_id": source_id,
        "cycle_time": time_iso(cycle_time),
        "products": serialized_rows,
    }
    return store.write_bytes_atomic(
        f"canonical/{source_id}/{cycle_time.strftime('%Y%m%d%H')}/_catalog/catalog.json",
        json.dumps(payload).encode("utf-8"),
    )


def make_file_producer(
    tmp_path,
    *,
    seed: dict,
    store: LocalObjectStore,
    max_manifest_bytes: int = 33_554_432,
):
    """Build the public forcing seam with an explicit file repository."""
    from yd_producer.forcing import ForcingProducer, ForcingProducerConfig
    from yd_producer.forcing.file_store import FileForcingRepository

    binding_content = json.dumps(
        {"schema_version": "nhms.direct_grid.binding.v1"}
    ).encode("utf-8")
    stations = seed["station_bindings"]
    sp_att = sp_att_content(tuple(range(1, len(stations) + 1))).encode("utf-8")
    seed["binding_checksum"] = sha256_bytes(binding_content)
    seed["sp_att_checksum"] = sha256_bytes(sp_att)
    store.write_bytes_atomic(seed["binding_uri"], binding_content)
    store.write_bytes_atomic(f"models/demo/package/{seed['sp_att_path']}", sp_att)
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
    repository = FileForcingRepository(
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


def _unit_for(variable: str) -> str:
    return {
        "prcp_rate_or_amount": "mm/day",
        "air_temperature_2m": "degC",
        "relative_humidity_2m": "0-1",
        "wind_u_10m": "m/s",
        "wind_v_10m": "m/s",
        "pressure_surface": "Pa",
        "surface_pressure": "Pa",
        "shortwave_down": "W/m2",
    }[variable]


def sp_att_content(forc_values: tuple[int, ...] = (1, 2)) -> str:
    rows = "\n".join(
        f"{index}\t0\t0\t0\t{value}" for index, value in enumerate(forc_values, start=1)
    )
    return f"2 1\nTRI\tA\tB\tC\tFORC\n{rows}\n"


def time_iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
