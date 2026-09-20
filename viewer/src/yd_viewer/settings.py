"""Load viewer directories from the three required environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

INPUT_DIR = "YD_VIEWER_INPUT_DIR"
OUTPUT_DIR = "YD_VIEWER_OUTPUT_DIR"
STATIC_DIR = "YD_VIEWER_STATIC_DIR"


class SettingsError(Exception):
    """Environment settings are missing or not usable directories."""


@dataclass(frozen=True, kw_only=True)
class Settings:
    input_dir: Path
    output_dir: Path
    static_dir: Path


def load_settings() -> Settings:
    return Settings(
        input_dir=_require_readable_directory(INPUT_DIR),
        output_dir=_require_readable_directory(OUTPUT_DIR),
        static_dir=_require_readable_directory(STATIC_DIR),
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
