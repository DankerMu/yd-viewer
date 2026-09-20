"""Application factory, GET /api/health, and GET /api/cycles."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from yd_viewer import catalog
from yd_viewer.geometry import load_geometry
from yd_viewer.settings import Settings, load_settings


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

    return app
