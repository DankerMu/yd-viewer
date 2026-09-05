"""Phase 2 RE30: `_work_claim` helper-owned descriptors close exactly once."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
from controller_sources_fixtures import CYCLE_T, T_TEXT

from yd_producer import _work_claim as claim_mod
from yd_producer._work_claim import (
    WorkClaim,
    claim_exact_work,
    lexists_claimed,
    mkdir_relative_to_claim,
    open_claimed_excl,
    open_claimed_file,
    open_claimed_root,
    read_claimed_bytes,
    release_empty_claimed_root,
    rmdir_claimed,
    stat_claimed,
    unlink_claimed,
)


class FdLedger:
    """Count only descriptors opened/closed by the helper under test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._open = claim_mod.os.open
        self._close = claim_mod.os.close
        self.balance: dict[int, int] = {}
        monkeypatch.setattr(claim_mod.os, "open", self.open)
        monkeypatch.setattr(claim_mod.os, "close", self.close)

    def open(self, *args, **kwargs) -> int:
        fd = self._open(*args, **kwargs)
        self.balance[fd] = self.balance.get(fd, 0) + 1
        return fd

    def close(self, fd: int) -> None:
        self.balance[fd] = self.balance.get(fd, 0) - 1
        self._close(fd)

    def assert_clean(self) -> None:
        assert all(count >= 0 for count in self.balance.values())
        assert {fd: count for fd, count in self.balance.items() if count} == {}


def _claim(tmp_path: Path) -> tuple[WorkClaim, Path, Path]:
    claim = claim_exact_work(
        work_root=tmp_path / "work",
        source="ifs",
        cycle=CYCLE_T,
        cycle_name=T_TEXT,
    )
    nested = claim.work_dir / "a" / "b"
    nested.mkdir(parents=True)
    leaf = nested / "payload.bin"
    leaf.write_bytes(b"payload")
    return claim, nested, leaf


def _error(kind: Literal["os", "interrupt"]) -> BaseException:
    return OSError("injected fd seam") if kind == "os" else KeyboardInterrupt()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
def test_open_claimed_root_closes_on_fstat_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["os", "interrupt"]
) -> None:
    claim, _nested, _leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)

    def failing_fstat(fd: int):
        raise _error(kind)

    monkeypatch.setattr(claim_mod.os, "fstat", failing_fstat)
    with pytest.raises(type(_error(kind))):
        open_claimed_root(claim)
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
def test_walk_to_parent_closes_open_descendant_on_next_walk_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["os", "interrupt"]
) -> None:
    claim, _nested, leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)
    original = claim_mod._open_dir_child

    def failing_child(parent_fd: int, name: str, *, path: Path) -> int:
        if name == "b":
            raise _error(kind)
        return original(parent_fd, name, path=path)

    monkeypatch.setattr(claim_mod, "_open_dir_child", failing_child)
    root_fd = open_claimed_root(claim)
    try:
        with pytest.raises(type(_error(kind))):
            claim_mod._walk_to_parent(
                root_fd, leaf.relative_to(claim.work_dir).parts, path=leaf
            )
    finally:
        claim_mod.os.close(root_fd)
    ledger.assert_clean()


def test_walk_to_parent_success_closes_intermediate_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim, _nested, leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)
    root_fd = open_claimed_root(claim)
    parent_fd = claim_mod._walk_to_parent(
        root_fd, leaf.relative_to(claim.work_dir).parts, path=leaf
    )
    live = {fd: count for fd, count in ledger.balance.items() if count}
    assert live == {root_fd: 1, parent_fd: 1}
    claim_mod.os.close(parent_fd)
    claim_mod.os.close(root_fd)
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
def test_walk_to_parent_closes_next_fd_on_previous_transfer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["os", "interrupt"]
) -> None:
    claim, _nested, leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)
    original_close = claim_mod._close_walk
    calls = {"count": 0}

    def failing_close(fd: int, root_fd: int) -> None:
        original_close(fd, root_fd)
        calls["count"] += 1
        if calls["count"] == 1:
            raise _error(kind)

    monkeypatch.setattr(claim_mod, "_close_walk", failing_close)
    root_fd = open_claimed_root(claim)
    try:
        with pytest.raises(type(_error(kind))):
            claim_mod._walk_to_parent(
                root_fd, leaf.relative_to(claim.work_dir).parts, path=leaf
            )
    finally:
        claim_mod.os.close(root_fd)
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
@pytest.mark.parametrize("helper", [stat_claimed, lexists_claimed])
def test_stat_and_lexists_close_descendants_on_metadata_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["os", "interrupt"],
    helper,
) -> None:
    claim, _nested, leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)

    def failing_stat(*args, **kwargs):
        raise _error(kind)

    monkeypatch.setattr(claim_mod.os, "stat", failing_stat)
    with pytest.raises(type(_error(kind))):
        helper(claim, leaf)
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
def test_open_claimed_file_closes_owned_fds_on_file_fstat_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["os", "interrupt"]
) -> None:
    claim, _nested, leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)
    original = claim_mod.os.fstat
    calls = {"count": 0}

    def failing_fstat(fd: int):
        calls["count"] += 1
        if calls["count"] == 2:
            raise _error(kind)
        return original(fd)

    monkeypatch.setattr(claim_mod.os, "fstat", failing_fstat)
    with pytest.raises(type(_error(kind))):
        open_claimed_file(claim, leaf)
    ledger.assert_clean()


def test_open_claimed_file_returns_fd_owned_by_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim, _nested, leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)
    fd = open_claimed_file(claim, leaf)
    assert {item: count for item, count in ledger.balance.items() if count} == {fd: 1}
    claim_mod.os.close(fd)
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
def test_open_claimed_file_closes_file_on_parent_transfer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["os", "interrupt"]
) -> None:
    claim, _nested, leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)
    original_close = claim_mod._close_walk
    opened = {"value": False}
    original_open = ledger.open

    def tracking_open(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if path == "payload.bin":
            opened["value"] = True
        return fd

    def failing_close(fd: int, root_fd: int) -> None:
        original_close(fd, root_fd)
        if opened["value"] and fd != root_fd:
            raise _error(kind)

    monkeypatch.setattr(claim_mod.os, "open", tracking_open)
    monkeypatch.setattr(claim_mod, "_close_walk", failing_close)
    with pytest.raises(type(_error(kind))):
        open_claimed_file(claim, leaf)
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
def test_read_claimed_bytes_closes_file_on_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["os", "interrupt"]
) -> None:
    claim, _nested, leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)

    def failing_read(fd: int, size: int) -> bytes:
        raise _error(kind)

    monkeypatch.setattr(claim_mod.os, "read", failing_read)
    expected = claim_mod.ClaimLostError if kind == "os" else KeyboardInterrupt
    with pytest.raises(expected):
        read_claimed_bytes(claim, leaf)
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
def test_open_claimed_excl_closes_parent_and_root_on_create_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["os", "interrupt"]
) -> None:
    claim, _nested, leaf = _claim(tmp_path)
    target = leaf.with_name("created.bin")
    ledger = FdLedger(monkeypatch)
    original = ledger.open

    def failing_open(path, *args, **kwargs):
        if path == "created.bin":
            raise _error(kind)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(claim_mod.os, "open", failing_open)
    with pytest.raises(type(_error(kind))):
        open_claimed_excl(claim, target)
    ledger.assert_clean()


def test_open_claimed_excl_returns_fd_owned_by_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim, _nested, leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)
    fd = open_claimed_excl(claim, leaf.with_name("created.bin"))
    assert {item: count for item, count in ledger.balance.items() if count} == {fd: 1}
    claim_mod.os.fstat(fd)
    claim_mod.os.close(fd)
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
def test_open_claimed_excl_closes_created_fd_on_parent_transfer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["os", "interrupt"]
) -> None:
    claim, _nested, leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)
    original_open = ledger.open
    original_walk_close = claim_mod._close_walk
    created = {"value": False}

    def tracking_open(path, *args, **kwargs):
        fd = original_open(path, *args, **kwargs)
        if path == "created.bin":
            created["value"] = True
        return fd

    def failing_walk_close(fd: int, root_fd: int) -> None:
        original_walk_close(fd, root_fd)
        if created["value"] and fd != root_fd:
            raise _error(kind)

    monkeypatch.setattr(claim_mod.os, "open", tracking_open)
    monkeypatch.setattr(claim_mod, "_close_walk", failing_walk_close)
    with pytest.raises(type(_error(kind))):
        open_claimed_excl(claim, leaf.with_name("created.bin"))
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
@pytest.mark.parametrize("operation", ["unlink", "rmdir"])
def test_unlink_and_rmdir_close_descendants_on_operation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["os", "interrupt"],
    operation: Literal["unlink", "rmdir"],
) -> None:
    claim, nested, leaf = _claim(tmp_path)
    target = leaf if operation == "unlink" else nested / "empty"
    if operation == "rmdir":
        target.mkdir()
    ledger = FdLedger(monkeypatch)

    def failing_operation(*args, **kwargs):
        raise _error(kind)

    monkeypatch.setattr(claim_mod.os, operation, failing_operation)
    helper = unlink_claimed if operation == "unlink" else rmdir_claimed
    with pytest.raises(type(_error(kind))):
        helper(claim, target)
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
def test_mkdir_relative_to_claim_closes_open_descendant_on_next_child_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["os", "interrupt"]
) -> None:
    claim, _nested, _leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)
    original = claim_mod._open_dir_child

    def failing_child(parent_fd: int, name: str, *, path: Path) -> int:
        if name == "new":
            raise _error(kind)
        return original(parent_fd, name, path=path)

    monkeypatch.setattr(claim_mod, "_open_dir_child", failing_child)
    with pytest.raises(type(_error(kind))):
        mkdir_relative_to_claim(claim, claim.work_dir / "a" / "new" / "leaf")
    ledger.assert_clean()


def test_mkdir_relative_to_claim_closes_intermediate_fds_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim, _nested, _leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)
    created = mkdir_relative_to_claim(claim, claim.work_dir / "new" / "branch")
    assert created == [claim.work_dir / "new" / "branch", claim.work_dir / "new"]
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
def test_mkdir_relative_to_claim_closes_next_fd_on_transfer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["os", "interrupt"]
) -> None:
    claim, _nested, _leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)
    original_close = claim_mod._close_walk
    calls = {"count": 0}

    def failing_close(fd: int, root_fd: int) -> None:
        original_close(fd, root_fd)
        calls["count"] += 1
        if calls["count"] == 1:
            raise _error(kind)

    monkeypatch.setattr(claim_mod, "_close_walk", failing_close)
    with pytest.raises(type(_error(kind))):
        mkdir_relative_to_claim(claim, claim.work_dir / "new" / "branch")
    ledger.assert_clean()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
def test_mkdir_relative_to_claim_closes_descendant_on_mkdir_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["os", "interrupt"]
) -> None:
    claim, _nested, _leaf = _claim(tmp_path)
    ledger = FdLedger(monkeypatch)

    def failing_mkdir(*args, **kwargs):
        raise _error(kind)

    monkeypatch.setattr(claim_mod.os, "mkdir", failing_mkdir)
    expected = claim_mod.ClaimLostError if kind == "os" else KeyboardInterrupt
    with pytest.raises(expected):
        mkdir_relative_to_claim(claim, claim.work_dir / "a" / "new" / "leaf")
    ledger.assert_clean()


def _empty_claimed_root(claim: WorkClaim, nested: Path, leaf: Path) -> None:
    leaf.unlink()
    nested.rmdir()
    nested.parent.rmdir()


@pytest.mark.parametrize("kind", ["os", "interrupt"])
@pytest.mark.parametrize("operation", ["listdir", "rmdir"])
def test_release_empty_claimed_root_closes_child_and_parent_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["os", "interrupt"],
    operation: Literal["listdir", "rmdir"],
) -> None:
    claim, nested, leaf = _claim(tmp_path)
    _empty_claimed_root(claim, nested, leaf)
    ledger = FdLedger(monkeypatch)

    def failing_operation(*args, **kwargs):
        raise _error(kind)

    monkeypatch.setattr(claim_mod.os, operation, failing_operation)
    with pytest.raises(type(_error(kind))):
        release_empty_claimed_root(claim)
    ledger.assert_clean()
