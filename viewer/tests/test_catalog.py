"""list_cycles() enumerates structurally valid DONE candidates."""

from __future__ import annotations

import logging
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
AUTHORITY_1_TO_5 = {1, 2, 3, 4, 5}
SHIFTED_MINUTES = tuple(range(60, 10081, 60))
NC_3988 = 3988
N_BUDGET_CANDIDATES = 30
HEADER_BUDGET_3988 = 1024 + 8 * (2 + NC_3988)
FILE_SIZE_3988 = HEADER_BUDGET_3988 + 168 * 8 * (NC_3988 + 1)
CATALOG_LOGGER = "yd_viewer.catalog"


def _groups(entries) -> list[tuple[str, list[str]]]:
    return sorted((entry["cycle"], sorted(entry["sources"])) for entry in entries)


def _source_dir(output_dir: Path, cycle: str, source: str) -> Path:
    return output_dir / cycle / source


def _write_regular_done(output_dir: Path, cycle: str, source: str) -> Path:
    return write_done(_source_dir(output_dir, cycle, source) / "DONE")


def _write_dat(
    output_dir: Path,
    cycle: str,
    source: str,
    **dat_kwargs,
) -> Path:
    kwargs = {"column_ids": IDS_1_TO_5, **dat_kwargs}
    return write_dat(
        _source_dir(output_dir, cycle, source) / "yd.rivqdown.dat",
        **kwargs,
    )


def _write_eligible(
    output_dir: Path,
    cycle: str,
    source: str,
    **dat_kwargs,
) -> Path:
    _write_regular_done(output_dir, cycle, source)
    return _write_dat(output_dir, cycle, source, **dat_kwargs)


def _list(output_dir: Path):
    return list_cycles(output_dir, AUTHORITY_1_TO_5)


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


def _track_os_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[tuple[int, int]]]:
    """Map realpath -> (offset, returned byte count) for real os.read calls."""

    real_open = os.open
    real_read = os.read
    real_close = os.close
    fd_to_path: dict[int, str] = {}
    calls: dict[str, list[tuple[int, int]]] = {}

    def open_path(name, flags, *args, **kwargs):
        fd = real_open(name, flags, *args, **kwargs)
        fd_to_path[fd] = os.path.realpath(name)
        return fd

    def read_fd(fd, n):
        path = fd_to_path.get(fd)
        if path is None:
            return real_read(fd, n)
        position = os.lseek(fd, 0, os.SEEK_CUR)
        data = real_read(fd, n)
        calls.setdefault(path, []).append((position, len(data)))
        return data

    def close_fd(fd):
        fd_to_path.pop(fd, None)
        return real_close(fd)

    monkeypatch.setattr(os, "open", open_path)
    monkeypatch.setattr(os, "read", read_fd)
    monkeypatch.setattr(os, "close", close_fd)
    return calls


def _warning_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]


def test_regular_done_for_both_sources_groups_under_each_cycle(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    _write_eligible(output_dir, CYCLE_00, "gfs")
    _write_eligible(output_dir, CYCLE_00, "ifs")
    _write_eligible(output_dir, CYCLE_12, "gfs")

    assert _groups(_list(output_dir)) == [
        (CYCLE_00, ["gfs", "ifs"]),
        (CYCLE_12, ["gfs"]),
    ]


def test_done_without_dat_is_excluded_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output_dir = tmp_path / "output"
    _write_regular_done(output_dir, CYCLE_00, "ifs")
    dat = _source_dir(output_dir, CYCLE_00, "ifs") / "yd.rivqdown.dat"

    assert not dat.exists()
    with caplog.at_level(logging.WARNING, logger=CATALOG_LOGGER):
        assert _list(output_dir) == []
    warnings = _warning_messages(caplog)
    assert len(warnings) == 1
    assert str(dat) in warnings[0]
    assert warnings[0] != str(dat)


def test_dat_without_done_is_not_listed_or_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "output"
    gfs_dat = _write_dat(output_dir, CYCLE_00, "gfs")
    _write_eligible(output_dir, CYCLE_00, "ifs")
    calls = _track_os_reads(monkeypatch)

    assert not (_source_dir(output_dir, CYCLE_00, "gfs") / "DONE").exists()
    assert _groups(_list(output_dir)) == [(CYCLE_00, ["ifs"])]
    assert os.path.realpath(gfs_dat) not in calls


def test_symlink_done_is_excluded_even_with_valid_regular_target(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    target = write_done(tmp_path / "completed" / "DONE")
    linked = _source_dir(output_dir, CYCLE_00, "gfs") / "DONE"
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(target)
    _write_dat(output_dir, CYCLE_00, "gfs")
    _write_eligible(output_dir, CYCLE_00, "ifs")

    assert stat.S_ISLNK(linked.lstat().st_mode)
    assert stat.S_ISREG(target.lstat().st_mode)
    assert stat.S_ISREG(linked.stat().st_mode)
    assert _groups(_list(output_dir)) == [(CYCLE_00, ["ifs"])]


def test_directory_done_is_excluded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output_dir = tmp_path / "output"
    directory_done = _source_dir(output_dir, CYCLE_00, "gfs") / "DONE"
    directory_done.mkdir(parents=True)
    _write_dat(output_dir, CYCLE_00, "gfs")
    _write_eligible(output_dir, CYCLE_00, "ifs")

    assert directory_done.is_dir()
    assert not stat.S_ISREG(directory_done.lstat().st_mode)
    with caplog.at_level(logging.WARNING, logger=CATALOG_LOGGER):
        assert _groups(_list(output_dir)) == [(CYCLE_00, ["ifs"])]
    assert _warning_messages(caplog) == []


def test_invalid_cycle_and_source_names_are_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output_dir = tmp_path / "output"
    write_done(output_dir / "tmp" / "gfs" / "DONE")
    _write_dat(output_dir, "tmp", "gfs")
    write_done(output_dir / CYCLE_06 / "gfs" / "DONE")
    _write_dat(output_dir, CYCLE_06, "gfs")
    write_done(output_dir / CYCLE_00 / "GFS" / "DONE")
    _write_dat(output_dir, CYCLE_00, "GFS")
    write_done(output_dir / f"{CYCLE_00}\n" / "gfs" / "DONE")
    _write_dat(output_dir, f"{CYCLE_00}\n", "gfs")
    write_done(output_dir / f"{CYCLE_00}x" / "gfs" / "DONE")
    _write_dat(output_dir, f"{CYCLE_00}x", "gfs")
    _write_eligible(output_dir, CYCLE_12, "gfs")

    with caplog.at_level(logging.WARNING, logger=CATALOG_LOGGER):
        assert _groups(_list(output_dir)) == [(CYCLE_12, ["gfs"])]
    assert _warning_messages(caplog) == []


def test_neighbor_plain_files_do_not_break_traversal(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    (output_dir).mkdir()
    (output_dir / "README").write_text("ignore", encoding="utf-8")
    cycle_dir = output_dir / CYCLE_00
    cycle_dir.mkdir()
    (cycle_dir / "notes.txt").write_text("ignore", encoding="utf-8")
    _write_eligible(output_dir, CYCLE_00, "gfs")

    assert _groups(_list(output_dir)) == [(CYCLE_00, ["gfs"])]


def test_empty_root_returns_empty_list(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    assert _list(output_dir) == []


def test_missing_root_raises_oserror(tmp_path: Path) -> None:
    output_dir = tmp_path / "missing"

    with pytest.raises(OSError):
        _list(output_dir)
    assert not output_dir.exists()


def test_nondirectory_root_raises_oserror(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.write_bytes(b"not-a-directory")

    with pytest.raises(OSError):
        _list(output_dir)


def test_unreadable_root_raises_oserror(tmp_path: Path) -> None:
    _skip_if_root()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_regular_done(output_dir, CYCLE_00, "gfs")

    with _chmod(output_dir, 0o000), pytest.raises(OSError):
        _list(output_dir)


def test_short_gfs_is_excluded_while_valid_ifs_remains(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output_dir = tmp_path / "output"
    gfs_dat = _write_eligible(output_dir, CYCLE_00, "gfs", rows=167)
    _write_eligible(output_dir, CYCLE_00, "ifs")

    with caplog.at_level(logging.WARNING, logger=CATALOG_LOGGER):
        entries = _list(output_dir)

    assert _groups(entries) == [(CYCLE_00, ["ifs"])]
    warnings = _warning_messages(caplog)
    assert len(warnings) == 1
    assert str(gfs_dat) in warnings[0]
    assert "167" in warnings[0]
    assert "168" in warnings[0]


def test_structurally_invalid_sources_omit_the_cycle(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output_dir = tmp_path / "output"
    gfs_dat = _write_eligible(output_dir, CYCLE_00, "gfs", rows=167)
    ifs_dat = _write_eligible(
        output_dir, CYCLE_00, "ifs", column_ids=(1, 2, 3, 4, 5, 1)
    )

    with caplog.at_level(logging.WARNING, logger=CATALOG_LOGGER):
        assert _list(output_dir) == []

    warnings = _warning_messages(caplog)
    assert len(warnings) == 2
    text = "\n".join(warnings)
    assert str(gfs_dat) in text
    assert str(ifs_dat) in text


def test_duplicate_column_ids_are_excluded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output_dir = tmp_path / "output"
    gfs_dat = _write_eligible(
        output_dir, CYCLE_00, "gfs", column_ids=(1, 2, 3, 4, 5, 1)
    )
    _write_eligible(output_dir, CYCLE_00, "ifs")

    with caplog.at_level(logging.WARNING, logger=CATALOG_LOGGER):
        entries = _list(output_dir)

    assert _groups(entries) == [(CYCLE_00, ["ifs"])]
    warnings = _warning_messages(caplog)
    assert len(warnings) == 1
    assert str(gfs_dat) in warnings[0]
    assert "1" in warnings[0]


def test_column_ids_that_do_not_match_authority_are_excluded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    output_dir = tmp_path / "output"
    gfs_dat = _write_eligible(output_dir, CYCLE_00, "gfs", column_ids=(1, 2, 3, 4, 6))
    _write_eligible(output_dir, CYCLE_00, "ifs")

    with caplog.at_level(logging.WARNING, logger=CATALOG_LOGGER):
        entries = _list(output_dir)

    assert _groups(entries) == [(CYCLE_00, ["ifs"])]
    warnings = _warning_messages(caplog)
    assert len(warnings) == 1
    assert str(gfs_dat) in warnings[0]
    assert "5" in warnings[0]
    assert "6" in warnings[0]


def test_shifted_minutes_remain_listed(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    _write_eligible(output_dir, CYCLE_00, "gfs", minutes=SHIFTED_MINUTES)

    assert _groups(_list(output_dir)) == [(CYCLE_00, ["gfs"])]


def test_nan_minutes_remain_listed(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    _write_eligible(output_dir, CYCLE_00, "gfs", nan_cells=((5, 0),))

    assert _groups(_list(output_dir)) == [(CYCLE_00, ["gfs"])]


def test_enumeration_reads_only_header_bytes_per_large_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "output"
    template = write_dat(
        tmp_path / "template.dat",
        column_ids=range(1, NC_3988 + 1),
    )
    assert template.stat().st_size == FILE_SIZE_3988
    authority = set(range(1, NC_3988 + 1))
    pairs = [
        (f"202608{day:02d}00", source)
        for day in range(1, 16)
        for source in ("gfs", "ifs")
    ]
    assert len(pairs) == N_BUDGET_CANDIDATES
    dat_paths = []
    for cycle, source in pairs:
        _write_regular_done(output_dir, cycle, source)
        dest = _source_dir(output_dir, cycle, source) / "yd.rivqdown.dat"
        os.link(template, dest)
        dat_paths.append(dest)

    expected = {os.path.realpath(path) for path in dat_paths}
    assert len(expected) == N_BUDGET_CANDIDATES
    calls = _track_os_reads(monkeypatch)

    entries = list_cycles(output_dir, authority)

    listed = {
        (entry["cycle"], source) for entry in entries for source in entry["sources"]
    }
    assert listed == set(pairs)
    for path in expected:
        spans = calls.get(path, [])
        assert spans, f"no os.read for {path}"
        returned = 0
        for position, nbytes in spans:
            assert nbytes > 0
            assert position + nbytes <= HEADER_BUDGET_3988
            returned += nbytes
        assert 0 < returned <= HEADER_BUDGET_3988
