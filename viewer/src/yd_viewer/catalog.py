"""Enumerate legally named regular DONE files under an output root."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import TypedDict

_CYCLE_NAME = re.compile(r"\d{8}(?:00|12)")
_SOURCES = frozenset({"gfs", "ifs"})
_DONE_NAME = "DONE"


class CycleEntry(TypedDict):
    cycle: str
    sources: list[str]


def list_cycles(output_dir: str | Path) -> list[CycleEntry]:
    root = Path(output_dir)
    entries: list[CycleEntry] = []
    for cycle_path in root.iterdir():
        cycle = cycle_path.name
        if _CYCLE_NAME.fullmatch(cycle) is None:
            continue
        if not cycle_path.is_dir():
            continue
        sources = _sources_with_regular_done(cycle_path)
        if sources:
            entries.append({"cycle": cycle, "sources": sources})
    return entries


def _sources_with_regular_done(cycle_path: Path) -> list[str]:
    sources: list[str] = []
    try:
        for source_path in cycle_path.iterdir():
            source = source_path.name
            if source not in _SOURCES:
                continue
            if not source_path.is_dir():
                continue
            if _is_regular_done(source_path / _DONE_NAME):
                sources.append(source)
    except (FileNotFoundError, NotADirectoryError):
        return sources
    return sources


def _is_regular_done(path: Path) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat.S_ISREG(mode)
