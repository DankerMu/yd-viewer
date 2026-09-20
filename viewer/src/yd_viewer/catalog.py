"""Enumerate structurally valid regular DONE candidates under an output root."""

from __future__ import annotations

import logging
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

from yd_viewer.dat import DatError, read_header

_CYCLE_NAME = re.compile(r"\d{8}(?:00|12)")
_SOURCES = frozenset({"gfs", "ifs"})
_WINDOW = timedelta(days=7)
_DONE_NAME = "DONE"
_DAT_NAME = "yd.rivqdown.dat"
_LOGGER = logging.getLogger(__name__)


class CycleEntry(TypedDict):
    cycle: str
    sources: list[str]


def list_cycles(output_dir: str | Path, reach_ids: set[int]) -> list[CycleEntry]:
    root = Path(output_dir)
    dated: list[tuple[datetime, CycleEntry]] = []
    for cycle_path in root.iterdir():
        cycle = cycle_path.name
        start = _utc_cycle_start(cycle)
        if start is None:
            continue
        if not cycle_path.is_dir():
            continue
        sources = _structurally_valid_sources(cycle_path, reach_ids)
        if sources:
            dated.append((start, {"cycle": cycle, "sources": sources}))
    if not dated:
        return []
    dated.sort(key=lambda item: item[0], reverse=True)
    anchor = dated[0][0]
    return [entry for start, entry in dated if anchor - start <= _WINDOW]


def latest(output_dir: str | Path, reach_ids: set[int]) -> str | None:
    entries = list_cycles(output_dir, reach_ids)
    if not entries:
        return None
    return entries[0]["cycle"]


def _utc_cycle_start(cycle: str) -> datetime | None:
    if _CYCLE_NAME.fullmatch(cycle) is None:
        return None
    try:
        return datetime.strptime(cycle, "%Y%m%d%H").replace(tzinfo=UTC)
    except ValueError:
        return None


def _structurally_valid_sources(cycle_path: Path, reach_ids: set[int]) -> list[str]:
    sources: list[str] = []
    try:
        for source_path in cycle_path.iterdir():
            source = source_path.name
            if source not in _SOURCES:
                continue
            if not source_path.is_dir():
                continue
            if not _is_regular_done(source_path / _DONE_NAME):
                continue
            if _header_is_valid(source_path / _DAT_NAME, reach_ids):
                sources.append(source)
    except (FileNotFoundError, NotADirectoryError):
        return sources
    sources.sort()
    return sources


def _header_is_valid(path: Path, reach_ids: set[int]) -> bool:
    try:
        read_header(path, reach_ids)
    except DatError as exc:
        _LOGGER.warning("%s", exc)
        return False
    return True


def _is_regular_done(path: Path) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat.S_ISREG(mode)
