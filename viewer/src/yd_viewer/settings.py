"""Load viewer directories from the three required environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

INPUT_DIR = "YD_VIEWER_INPUT_DIR"
OUTPUT_DIR = "YD_VIEWER_OUTPUT_DIR"
STATIC_DIR = "YD_VIEWER_STATIC_DIR"
TIANDITU_KEY = "YD_TIANDITU_KEY"
BASEMAP_CACHE_DIR = "YD_BASEMAP_CACHE_DIR"
DEFAULT_BASEMAP_CACHE_DIR = Path("/cache")


class SettingsError(Exception):
    """Environment settings are missing or not usable directories."""


@dataclass(frozen=True, kw_only=True)
class Settings:
    input_dir: Path
    output_dir: Path
    static_dir: Path
    tianditu_key: str | None = None
    basemap_cache_dir: Path = DEFAULT_BASEMAP_CACHE_DIR


def load_settings() -> Settings:
    """Three required directories plus the two optional basemap proxy values.

    The cache directory is not validated here; it is created on first tile
    write, so an unset or absent directory cannot break the other endpoints.
    """
    cache_dir = os.environ.get(BASEMAP_CACHE_DIR)
    return Settings(
        input_dir=_require_readable_directory(INPUT_DIR),
        output_dir=_require_readable_directory(OUTPUT_DIR),
        static_dir=_require_readable_directory(STATIC_DIR),
        tianditu_key=os.environ.get(TIANDITU_KEY) or None,
        basemap_cache_dir=Path(cache_dir) if cache_dir else DEFAULT_BASEMAP_CACHE_DIR,
    )


def _require_readable_directory(name: str) -> Path:
    raw = os.environ.get(name)
    if raw is None:
        raise SettingsError(f"{name} 未设置")
    if raw == "":
        raise SettingsError(f"{name} 为空：{raw!r}")
    path = Path(raw)
    try:
        is_directory = path.is_dir()
        accessible = os.access(path, os.R_OK | os.X_OK, effective_ids=True)
    except OSError as exc:
        raise SettingsError(f"{name} 不可读：{raw}") from exc
    if not is_directory:
        raise SettingsError(f"{name} 不是目录：{raw}")
    if not accessible:
        raise SettingsError(f"{name} 不可读：{raw}")
    return path
