"""The tianditu tile proxy caches only 200 images and never leaks the key."""

from __future__ import annotations

import io
import logging
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest
from fastapi.testclient import TestClient
from synthetic import write_geometry

from yd_viewer import basemap
from yd_viewer.app import create_app
from yd_viewer.settings import Settings

KEY = "SECRET-TIANDITU-KEY"
REACH_IDS = (1, 2, 3)
PNG = b"\x89PNG\r\n\x1a\n" + b"png-tile-body"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-tile-body"
HTML = "<html>code 302010 该tk已限流</html>".encode()
VEC_TILE = "/api/basemap/tianditu/vec/1/0/0"
IMG_TILE = "/api/basemap/tianditu/img/2/3/2"
# design §6.1 / viewer-api spec: T=<layer>_w, l=<z>, subdomain t{(x+y)%8}
VEC_UPSTREAM = f"https://t0.tianditu.gov.cn/DataServer?T=vec_w&x=0&y=0&l=1&tk={KEY}"
IMG_UPSTREAM = f"https://t5.tianditu.gov.cn/DataServer?T=img_w&x=3&y=2&l=2&tk={KEY}"
PUBLIC_CACHE = "public, max-age=604800"
NO_STORE = "no-store"


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc_info: object) -> bool:
        return False


class _Upstream:
    """Replay outcomes for successive urlopen calls; the last one repeats."""

    def __init__(self, *outcomes: tuple[int, bytes] | BaseException) -> None:
        self._outcomes = outcomes
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[object] = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        index = min(len(self.requests) - 1, len(self._outcomes) - 1)
        outcome = self._outcomes[index]
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeResponse(*outcome)

    @property
    def urls(self) -> list[str]:
        return [request.full_url for request in self.requests]


@pytest.fixture(autouse=True)
def _reset_cooldown():
    basemap._throttled_until.clear()
    yield
    basemap._throttled_until.clear()


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache"


def _settings(tmp_path: Path, *, key: str | None, cache_dir: Path) -> Settings:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    static_dir = tmp_path / "static"
    write_geometry(input_dir, reach_ids=REACH_IDS)
    output_dir.mkdir()
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    return Settings(
        input_dir=input_dir,
        output_dir=output_dir,
        static_dir=static_dir,
        tianditu_key=key,
        basemap_cache_dir=cache_dir,
    )


def _client(tmp_path: Path, cache_dir: Path, *, key: str | None = KEY) -> TestClient:
    return TestClient(create_app(_settings(tmp_path, key=key, cache_dir=cache_dir)))


def _install(monkeypatch: pytest.MonkeyPatch, upstream: _Upstream) -> _Upstream:
    monkeypatch.setattr(urllib.request, "urlopen", upstream)
    return upstream


def _freeze_clock(monkeypatch: pytest.MonkeyPatch, now: list[float]) -> None:
    monkeypatch.setattr(
        basemap, "time", SimpleNamespace(monotonic=lambda: now[0]), raising=True
    )


def test_miss_fetches_upstream_then_hit_serves_the_cached_tile(
    tmp_path, cache_dir, monkeypatch
):
    upstream = _install(monkeypatch, _Upstream((200, PNG)))
    client = _client(tmp_path, cache_dir)

    miss = client.get(VEC_TILE)
    hit = client.get(VEC_TILE)

    assert upstream.urls == [VEC_UPSTREAM]
    assert upstream.timeouts == [10]
    request = upstream.requests[0]
    assert list(request.headers) == ["User-agent"]
    assert request.headers["User-agent"].startswith("Mozilla/5.0")
    assert not request.has_header("Referer")
    assert not request.has_header("Cookie")
    for response, state in ((miss, "miss"), (hit, "hit")):
        assert response.status_code == 200
        assert response.content == PNG
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-tile-cache"] == state
        assert response.headers["cache-control"] == PUBLIC_CACHE
    assert (cache_dir / "vec" / "1" / "0" / "0").read_bytes() == PNG


def test_jpeg_body_is_served_as_jpeg_from_the_coordinate_subdomain(
    tmp_path, cache_dir, monkeypatch
):
    upstream = _install(monkeypatch, _Upstream((200, JPEG)))
    client = _client(tmp_path, cache_dir)

    response = client.get(IMG_TILE)

    assert upstream.urls == [IMG_UPSTREAM]
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == JPEG
    assert (cache_dir / "img" / "2" / "3" / "2").read_bytes() == JPEG


@pytest.mark.parametrize(
    "outcome",
    [
        (500, b"upstream is broken"),
        (403, b"forbidden"),
        (200, HTML),
        TimeoutError("timed out"),
        urllib.error.URLError("name resolution failed"),
    ],
)
def test_upstream_failures_answer_502_without_caching(
    tmp_path, cache_dir, monkeypatch, outcome
):
    upstream = _install(monkeypatch, _Upstream(outcome))
    client = _client(tmp_path, cache_dir)

    response = client.get(VEC_TILE)

    assert len(upstream.requests) == 1
    assert response.status_code == 502
    assert response.headers["cache-control"] == NO_STORE
    assert response.json() == {"detail": "Bad Gateway"}
    assert not (cache_dir / "vec").exists()


def test_raised_http_error_keeps_its_status(tmp_path, cache_dir, monkeypatch):
    """urlopen raises on non-2xx; a raised 429 must still cool the layer down."""
    error = urllib.error.HTTPError(
        VEC_UPSTREAM, 429, "Too Many Requests", {}, io.BytesIO(b"rate limited")
    )
    upstream = _install(monkeypatch, _Upstream(error, (200, PNG)))
    client = _client(tmp_path, cache_dir)

    throttled = client.get(VEC_TILE)
    cooling = client.get(VEC_TILE)

    assert throttled.status_code == 503
    assert cooling.status_code == 503
    assert cooling.headers["cache-control"] == NO_STORE
    assert len(upstream.requests) == 1
    assert KEY not in throttled.text


def test_429_cools_the_layer_down_for_60s_without_touching_other_layers(
    tmp_path, cache_dir, monkeypatch
):
    now = [1000.0]
    _freeze_clock(monkeypatch, now)
    upstream = _install(monkeypatch, _Upstream((429, b"rate limited"), (200, JPEG)))
    client = _client(tmp_path, cache_dir)

    throttled = client.get(VEC_TILE)
    now[0] += 59.0
    cooling = client.get(VEC_TILE)
    other_layer = client.get(IMG_TILE)

    for response in (throttled, cooling):
        assert response.status_code == 503
        assert response.headers["cache-control"] == NO_STORE
        assert response.json() == {"detail": "Service Unavailable"}
    assert upstream.urls == [VEC_UPSTREAM, IMG_UPSTREAM]
    assert other_layer.status_code == 200
    assert not (cache_dir / "vec").exists()


def test_layer_is_refetched_after_the_cooldown_expires(
    tmp_path, cache_dir, monkeypatch
):
    now = [1000.0]
    _freeze_clock(monkeypatch, now)
    upstream = _install(monkeypatch, _Upstream((429, b"rate limited"), (200, PNG)))
    client = _client(tmp_path, cache_dir)

    assert client.get(VEC_TILE).status_code == 503
    now[0] += 60.0
    recovered = client.get(VEC_TILE)

    assert recovered.status_code == 200
    assert recovered.headers["x-tile-cache"] == "miss"
    assert upstream.urls == [VEC_UPSTREAM, VEC_UPSTREAM]


def test_cached_tile_is_served_while_the_layer_is_cooling_down(
    tmp_path, cache_dir, monkeypatch
):
    now = [1000.0]
    _freeze_clock(monkeypatch, now)
    upstream = _install(monkeypatch, _Upstream((200, PNG), (429, b"rate limited")))
    client = _client(tmp_path, cache_dir)

    assert client.get(VEC_TILE).headers["x-tile-cache"] == "miss"
    assert client.get("/api/basemap/tianditu/vec/1/1/0").status_code == 503

    hit = client.get(VEC_TILE)

    assert hit.status_code == 200
    assert hit.headers["x-tile-cache"] == "hit"
    assert len(upstream.requests) == 2


@pytest.mark.parametrize(
    "path",
    [
        "/api/basemap/tianditu/foo/1/0/0",
        "/api/basemap/tianditu/vec/19/0/0",
        "/api/basemap/tianditu/vec/-1/0/0",
        "/api/basemap/tianditu/vec/1/2/0",
        "/api/basemap/tianditu/vec/1/0/2",
        "/api/basemap/tianditu/vec/1/-1/0",
        "/api/basemap/tianditu/vec/1/0/abc",
    ],
)
def test_invalid_parameters_are_4xx_without_calling_upstream(
    tmp_path, cache_dir, monkeypatch, path
):
    upstream = _install(monkeypatch, _Upstream((200, PNG)))
    client = _client(tmp_path, cache_dir)

    response = client.get(path)

    assert 400 <= response.status_code < 500
    assert upstream.requests == []
    assert "detail" in response.json()
    assert not cache_dir.exists()


def test_route_is_absent_without_a_key(tmp_path, cache_dir, monkeypatch):
    upstream = _install(monkeypatch, _Upstream((200, PNG)))
    client = _client(tmp_path, cache_dir, key=None)

    response = client.get(VEC_TILE)

    assert response.status_code == 404
    assert "text/html" not in response.headers["content-type"]
    assert upstream.requests == []
    assert client.get("/api/health").status_code == 200


def test_key_never_reaches_logs_or_response_bodies(
    tmp_path, cache_dir, monkeypatch, caplog
):
    _install(
        monkeypatch,
        _Upstream(
            (500, b"broken"),
            (429, b"rate limited"),
            TimeoutError("timed out"),
            (200, HTML),
            (200, PNG),
        ),
    )
    client = _client(tmp_path, cache_dir)

    with caplog.at_level(logging.DEBUG):
        responses = [
            client.get("/api/basemap/tianditu/vec/1/0/0"),
            client.get("/api/basemap/tianditu/cva/1/0/0"),
            client.get("/api/basemap/tianditu/ter/1/0/0"),
            client.get("/api/basemap/tianditu/cia/1/0/0"),
            client.get("/api/basemap/tianditu/cta/1/0/0"),
        ]

    assert [response.status_code for response in responses] == [502, 503, 502, 502, 200]
    assert KEY not in caplog.text
    assert "tianditu.gov.cn" not in caplog.text
    for response in responses:
        assert KEY not in response.text
