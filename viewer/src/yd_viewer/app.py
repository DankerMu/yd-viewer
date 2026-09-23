"""Application factory: APIs, static geometry, and SPA hosting."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TypedDict

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from yd_viewer import basemap, catalog
from yd_viewer.dat import DatError, read_dat
from yd_viewer.geometry import load_geometry
from yd_viewer.settings import Settings, load_settings

_DAT_NAME = "yd.rivqdown.dat"
_LOGGER = logging.getLogger(__name__)
_CYCLE_NAME = re.compile(r"^\d{8}(?:00|12)$")
_LEAD_HOURS = list(range(168))
_Discharge = float | None


class MapLatestResponse(TypedDict):
    cycle: str
    source: str
    valid_time: str
    values: tuple[_Discharge, ...]


class CurveResponse(TypedDict):
    cycle: str
    reach_id: int
    lead_hours: list[int]
    series: dict[str, tuple[_Discharge, ...]]


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = load_settings()
    geometry = load_geometry(settings.input_dir)
    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict[str, str | None]:
        try:
            latest_cycle = catalog.latest(settings.output_dir, geometry.reach_ids)
        except OSError as exc:
            raise HTTPException(status_code=503) from exc
        return {"status": "ok", "latest_cycle": latest_cycle}

    @app.get("/api/cycles")
    def cycles() -> list[dict[str, str | list[str]]]:
        try:
            return catalog.list_cycles(settings.output_dir, geometry.reach_ids)
        except OSError as exc:
            raise HTTPException(status_code=503) from exc

    @app.get("/api/map/latest")
    def map_latest() -> MapLatestResponse:
        try:
            entries = catalog.list_cycles(settings.output_dir, geometry.reach_ids)
        except OSError as exc:
            raise HTTPException(status_code=503) from exc
        for entry in entries:
            cycle = entry["cycle"]
            for source in entry["sources"]:
                path = settings.output_dir / cycle / source / _DAT_NAME
                try:
                    values = read_dat(path, geometry.reach_ids).row(0)
                except DatError as exc:
                    _LOGGER.warning("%s", exc)
                    continue
                return {
                    "cycle": cycle,
                    "source": source,
                    "valid_time": datetime.strptime(cycle, "%Y%m%d%H")
                    .replace(tzinfo=UTC)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "values": values,
                }
        raise HTTPException(status_code=404)

    @app.get("/api/cycles/{cycle}/reaches/{reach_id}")
    def reach_curve(cycle: str, reach_id: int) -> CurveResponse:
        if _CYCLE_NAME.fullmatch(cycle) is None or reach_id not in geometry.reach_ids:
            raise HTTPException(status_code=400)
        try:
            entries = catalog.list_cycles(settings.output_dir, geometry.reach_ids)
        except OSError as exc:
            raise HTTPException(status_code=503) from exc
        selected = next((entry for entry in entries if entry["cycle"] == cycle), None)
        if selected is None:
            raise HTTPException(status_code=404)
        series: dict[str, tuple[_Discharge, ...]] = {}
        for source in selected["sources"]:
            path = settings.output_dir / selected["cycle"] / source / _DAT_NAME
            try:
                series[source] = read_dat(path, geometry.reach_ids).column(reach_id)
            except DatError as exc:
                _LOGGER.warning("%s", exc)
        if not series:
            raise HTTPException(status_code=404)
        return {
            "cycle": selected["cycle"],
            "reach_id": reach_id,
            "lead_hours": _LEAD_HOURS,
            "series": series,
        }

    basemap.register(app, settings)
    app.mount("/api", app.router.not_found)
    app.mount("/geometry", StaticFiles(directory=settings.input_dir))
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True))
    return app
