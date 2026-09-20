"""Application factory, GET /api/health, GET /api/cycles, and GET /api/map/latest."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException

from yd_viewer import catalog
from yd_viewer.dat import DatError, read_dat
from yd_viewer.geometry import load_geometry
from yd_viewer.settings import Settings, load_settings

_DAT_NAME = "yd.rivqdown.dat"
_LOGGER = logging.getLogger(__name__)


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
    def map_latest() -> dict[str, str | tuple[float, ...]]:
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

    return app
