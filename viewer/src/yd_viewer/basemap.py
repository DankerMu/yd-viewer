"""Same-origin tianditu tile reverse proxy with an on-disk cache.

The key lives only in the upstream URL: it never reaches a log record or a
response body. Only 200 responses whose body starts with a PNG or JPEG
signature are cached; every failure is answered with ``no-store`` and leaves
the cache tree untouched.
"""

from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from yd_viewer.settings import Settings

_LOGGER = logging.getLogger(__name__)

Layer = Literal["vec", "cva", "img", "cia", "ter", "cta"]

_MAX_ZOOM = 18
_SUBDOMAINS = 8
_TIMEOUT_SECONDS = 10
_COOLDOWN_SECONDS = 60.0
_MAX_AGE_SECONDS = 604800
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_NO_STORE = {"Cache-Control": "no-store"}

# Per-layer cooldown deadlines (monotonic seconds) after an upstream 429.
_throttled_until: dict[str, float] = {}


def register(app: FastAPI, settings: Settings) -> None:
    """Add the tile route when a key is configured, otherwise add nothing."""
    key = settings.tianditu_key
    if not key:
        return
    cache_dir = settings.basemap_cache_dir

    @app.get("/api/basemap/tianditu/{layer}/{z}/{x}/{y}")
    async def tianditu_tile(layer: Layer, z: int, x: int, y: int) -> Response:
        if not 0 <= z <= _MAX_ZOOM:
            raise HTTPException(status_code=404)
        limit = 2**z
        if not 0 <= x < limit or not 0 <= y < limit:
            raise HTTPException(status_code=404)

        path = cache_dir / layer / str(z) / str(x) / str(y)
        cached = _read_cached(path)
        if cached is not None:
            body, media_type = cached
            return _tile_response(body, media_type, "hit")

        if _cooling_down(layer):
            raise HTTPException(status_code=503, headers=_NO_STORE)

        url = (
            f"https://t{(x + y) % _SUBDOMAINS}.tianditu.gov.cn/DataServer"
            f"?T={layer}_w&x={x}&y={y}&l={z}&tk={key}"
        )
        try:
            status, body = await run_in_threadpool(_fetch, url)
        except Exception:  # noqa: BLE001 - 任何上游异常都必须变成 502，且不带 URL
            _LOGGER.warning("天地图上游请求失败：%s/%s/%s/%s", layer, z, x, y)
            raise HTTPException(status_code=502, headers=_NO_STORE) from None

        if status == 429:
            _throttled_until[layer] = time.monotonic() + _COOLDOWN_SECONDS
            _LOGGER.warning("天地图上游限流，冷却 %ss：%s", _COOLDOWN_SECONDS, layer)
            raise HTTPException(status_code=503, headers=_NO_STORE)
        media_type = _media_type(body) if status == 200 else None
        if media_type is None:
            _LOGGER.warning(
                "天地图上游响应无效（%s）：%s/%s/%s/%s", status, layer, z, x, y
            )
            raise HTTPException(status_code=502, headers=_NO_STORE)

        _write_cached(path, body)
        return _tile_response(body, media_type, "miss")


def _fetch(url: str) -> tuple[int, bytes]:
    """Return the upstream status and body; HTTP errors are statuses, not raises."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.status, error.read()


def _media_type(body: bytes) -> str | None:
    if body.startswith(_PNG_SIGNATURE):
        return "image/png"
    if body.startswith(_JPEG_SIGNATURE):
        return "image/jpeg"
    return None


def _cooling_down(layer: str) -> bool:
    deadline = _throttled_until.get(layer)
    if deadline is None:
        return False
    if time.monotonic() >= deadline:
        del _throttled_until[layer]
        return False
    return True


def _read_cached(path: Path) -> tuple[bytes, str] | None:
    try:
        body = path.read_bytes()
    except OSError:
        return None
    media_type = _media_type(body)
    return None if media_type is None else (body, media_type)


def _write_cached(path: Path, body: bytes) -> None:
    temporary = path.parent / f"tmp-{uuid.uuid4().hex}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(body)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        _LOGGER.warning("瓦片缓存写入失败：%s", path)


def _tile_response(body: bytes, media_type: str, cache_state: str) -> Response:
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Cache-Control": f"public, max-age={_MAX_AGE_SECONDS}",
            "X-Tile-Cache": cache_state,
        },
    )
