"""list_cycles() enumerates regular DONE files by cycle and source."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path

import pytest
from synthetic import write_dat, write_done

from yd_viewer.catalog import list_cycles

CYCLE_00 = "2026082700"
CYCLE_12 = "2026082712"
CYCLE_06 = "2026082706"
IDS_1_TO_5 = (1, 2, 3, 4, 5)


def _groups(entries) -> list[tuple[str, list[str]]]:
    return sorted((entry["cycle"], sorted(entry["sources"])) for entry in entries)


def _source_dir(output_dir: Path, cycle: str, source: str) -> Path:
    return output_dir / cycle / source


def _write_regular_done(output_dir: Path, cycle: str, source: str) -> Path:
    return write_done(_source_dir(output_dir, cycle, source) / "DONE")


def _write_dat(output_dir: Path, cycle: str, source: str) -> Path:
    return write_dat(
        _source_dir(output_dir, cycle, source) / "yd.rivqdown.dat",
        column_ids=IDS_1_TO_5,
    )


@contextmanager
def _chmod(path: Path, mode: int):
    original = stat.S_IMODE(path.stat().st_mode)
    path.chmod(mode)
    try:
        yield
    finally:
        path.chmod(original)


def _skip_if_root() -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores directory mode bits")


def test_regular_done_for_both_sources_groups_under_each_cycle(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    _write_regular_done(output_dir, CYCLE_00, "gfs")
    _write_regular_done(output_dir, CYCLE_00, "ifs")
    _write_regular_done(output_dir, CYCLE_12, "gfs")

    assert _groups(list_cycles(output_dir)) == [
        (CYCLE_00, ["gfs", "ifs"]),
        (CYCLE_12, ["gfs"]),
    ]


def test_valid_done_is_listed_without_dat(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    _write_regular_done(output_dir, CYCLE_00, "ifs")
    dat = _source_dir(output_dir, CYCLE_00, "ifs") / "yd.rivqdown.dat"

    assert not dat.exists()
    assert _groups(list_cycles(output_dir)) == [(CYCLE_00, ["ifs"])]
    assert not dat.exists()


def test_dat_without_done_is_not_listed(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    _write_dat(output_dir, CYCLE_00, "gfs")
    _write_regular_done(output_dir, CYCLE_00, "ifs")

    assert not (_source_dir(output_dir, CYCLE_00, "gfs") / "DONE").exists()
    assert _groups(list_cycles(output_dir)) == [(CYCLE_00, ["ifs"])]


def test_symlink_done_is_excluded_even_with_valid_regular_target(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    target = write_done(tmp_path / "completed" / "DONE")
    linked = _source_dir(output_dir, CYCLE_00, "gfs") / "DONE"
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(target)
    _write_regular_done(output_dir, CYCLE_00, "ifs")

    assert stat.S_ISLNK(linked.lstat().st_mode)
    assert stat.S_ISREG(target.lstat().st_mode)
    assert stat.S_ISREG(linked.stat().st_mode)
    assert _groups(list_cycles(output_dir)) == [(CYCLE_00, ["ifs"])]


def test_directory_done_is_excluded(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    directory_done = _source_dir(output_dir, CYCLE_00, "gfs") / "DONE"
    directory_done.mkdir(parents=True)
    _write_regular_done(output_dir, CYCLE_00, "ifs")

    assert directory_done.is_dir()
    assert not stat.S_ISREG(directory_done.lstat().st_mode)
    assert _groups(list_cycles(output_dir)) == [(CYCLE_00, ["ifs"])]


def test_invalid_cycle_and_source_names_are_ignored(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    write_done(output_dir / "tmp" / "gfs" / "DONE")
    write_done(output_dir / CYCLE_06 / "gfs" / "DONE")
    write_done(output_dir / CYCLE_00 / "GFS" / "DONE")
    write_done(output_dir / f"{CYCLE_00}\n" / "gfs" / "DONE")
    write_done(output_dir / f"{CYCLE_00}x" / "gfs" / "DONE")
    write_done(output_dir / CYCLE_12 / "gfs" / "DONE")

    assert _groups(list_cycles(output_dir)) == [(CYCLE_12, ["gfs"])]


def test_neighbor_plain_files_do_not_break_traversal(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    (output_dir).mkdir()
    (output_dir / "README").write_text("ignore", encoding="utf-8")
    cycle_dir = output_dir / CYCLE_00
    cycle_dir.mkdir()
    (cycle_dir / "notes.txt").write_text("ignore", encoding="utf-8")
    _write_regular_done(output_dir, CYCLE_00, "gfs")

    assert _groups(list_cycles(output_dir)) == [(CYCLE_00, ["gfs"])]


def test_empty_root_returns_empty_list(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    assert list_cycles(output_dir) == []


def test_missing_root_raises_oserror(tmp_path: Path) -> None:
    output_dir = tmp_path / "missing"

    with pytest.raises(OSError):
        list_cycles(output_dir)
    assert not output_dir.exists()


def test_nondirectory_root_raises_oserror(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.write_bytes(b"not-a-directory")

    with pytest.raises(OSError):
        list_cycles(output_dir)


def test_unreadable_root_raises_oserror(tmp_path: Path) -> None:
    _skip_if_root()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_regular_done(output_dir, CYCLE_00, "gfs")

    with _chmod(output_dir, 0o000), pytest.raises(OSError):
        list_cycles(output_dir)
