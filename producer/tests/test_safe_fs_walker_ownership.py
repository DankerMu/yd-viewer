"""Directory-walker successor ownership regressions (#183).

Each fault is armed only after the real ``_open_child_dir`` returns the final
child beneath ``root/lane-183/child-183``.  That distinguishes the named
walker's non-root handoff from its nested absolute-root opener.  The tests use
captured descriptors rather than process-wide fd counts: a failed previous
close may or may not consume its descriptor, so only successful cleanup is
checked with ``fstat(...)=EBADF``.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

from yd_producer.store import safe_fs
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    list_directory_no_follow,
    list_directory_no_follow_limited,
    open_directory_no_follow,
)

_WALKERS = ("parent", "private", "public", "list")
_LANE = "lane-183"
_CHILD = "child-183"
_REPEAT = 3


def _tree(root: Path) -> Path:
    target = root / _LANE / _CHILD
    target.mkdir(parents=True)
    return target


def _invoke(walker: str, target: Path, root: Path):
    if walker == "parent":
        return safe_fs._open_parent_dir(
            target / "payload.bin", containment_root=root, create=False
        )
    if walker == "private":
        return safe_fs._open_directory_no_follow(target)
    if walker == "public":
        return open_directory_no_follow(target, containment_root=root)
    assert walker == "list"
    return safe_fs._list_directory_no_follow(
        target, containment_root=root, max_entries=None
    )


def _primary_from(walker: str, error: BaseException) -> BaseException:
    if walker != "list":
        return error
    assert isinstance(error, SafeFilesystemError)
    assert error.kind == "io"
    assert error.__cause__ is not None
    return error.__cause__


def _assert_closed(captured: dict[str, object], key: str) -> None:
    fd = captured[key]
    assert isinstance(fd, int)
    real_fstat = captured["real_fstat"]
    assert callable(real_fstat)
    with pytest.raises(OSError) as info:
        real_fstat(fd)
    assert info.value.errno == errno.EBADF


def _assert_note(primary: BaseException, secondary: OSError) -> None:
    notes = "\n".join(getattr(primary, "__notes__", []))
    assert type(secondary).__name__ in notes
    assert secondary.strerror in notes


def _cleanup_leftovers(captured: dict[str, object]) -> None:
    captured["armed"] = False
    real_close = captured["real_close"]
    assert callable(real_close)
    for role in ("previous", "successor", "root"):
        fd = captured[role]
        if fd is None or captured[f"{role}_really_closed"]:
            continue
        try:
            real_close(fd)
        except OSError:
            pass


def _install_handoff_fault(
    monkeypatch: pytest.MonkeyPatch,
    *,
    walker: str,
    root: Path,
    target: Path,
    consume_previous: bool,
    successor_error: OSError | None = None,
    root_error: OSError | None = None,
) -> dict[str, object]:
    """Inject only after this walker has opened its real second child."""

    real_child = safe_fs._open_child_dir
    real_close = os.close
    real_fstat = os.fstat
    primary = OSError(errno.EIO, f"{walker} previous close")
    captured: dict[str, object] = {
        "armed": True,
        "primary": primary,
        "previous": None,
        "successor": None,
        "root": None,
        "previous_close_count": 0,
        "successor_close_count": 0,
        "root_close_count": 0,
        "previous_really_closed": False,
        "successor_really_closed": False,
        "root_really_closed": False,
        "real_close": real_close,
        "real_fstat": real_fstat,
    }

    def opening_child(parent_fd: int, name: str, path_label: Path) -> int:
        successor = real_child(parent_fd, name, path_label)
        if (
            captured["previous"] is None
            and Path(path_label) == target
            and name == _CHILD
        ):
            captured["previous"] = parent_fd
            captured["successor"] = successor
            assert stat.S_ISDIR(real_fstat(successor).st_mode)
        return successor

    def opening_root(path: Path) -> int:
        if walker == "private":
            raise AssertionError("private walker uses _open_verified_dir")
        fd = real_private(path)
        if Path(path) == root:
            assert captured["root"] is None
            captured["root"] = fd
        return fd

    def opening_verified_root(path: Path) -> int:
        fd = real_verified(path)
        if Path(path) == Path(target.anchor):
            assert captured["root"] is None
            captured["root"] = fd
        return fd

    def closing(fd: int) -> None:
        if not captured["armed"]:
            real_close(fd)
            return
        if captured["previous"] == fd:
            captured["previous_close_count"] = int(captured["previous_close_count"]) + 1
            if int(captured["previous_close_count"]) == 1:
                if consume_previous:
                    real_close(fd)
                    captured["previous_really_closed"] = True
                raise primary
            real_close(fd)
            captured["previous_really_closed"] = True
            return
        if captured["successor"] == fd:
            captured["successor_close_count"] = (
                int(captured["successor_close_count"]) + 1
            )
            real_close(fd)
            captured["successor_really_closed"] = True
            if successor_error is not None:
                raise successor_error
            return
        if captured["root"] == fd:
            captured["root_close_count"] = int(captured["root_close_count"]) + 1
            real_close(fd)
            captured["root_really_closed"] = True
            if root_error is not None:
                raise root_error
            return
        real_close(fd)

    monkeypatch.setattr(safe_fs, "_open_child_dir", opening_child)
    if walker == "private":
        real_verified = safe_fs._open_verified_dir
        monkeypatch.setattr(safe_fs, "_open_verified_dir", opening_verified_root)
    else:
        real_private = safe_fs._open_directory_no_follow
        monkeypatch.setattr(safe_fs, "_open_directory_no_follow", opening_root)
    monkeypatch.setattr(os, "close", closing)
    return captured


@pytest.mark.parametrize("walker", _WALKERS)
@pytest.mark.parametrize(
    "consume_previous", [False, True], ids=["unconsumed", "consumed"]
)
def test_walker_handoff_closes_successor_once_without_retrying_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    walker: str,
    consume_previous: bool,
) -> None:
    root = tmp_path.resolve()
    target = _tree(root)
    captured = _install_handoff_fault(
        monkeypatch,
        walker=walker,
        root=root,
        target=target,
        consume_previous=consume_previous,
    )
    try:
        with pytest.raises(BaseException) as info:
            _invoke(walker, target, root)
        primary = _primary_from(walker, info.value)
        assert primary is captured["primary"]
        assert captured["previous"] is not None
        assert captured["successor"] is not None
        assert captured["root"] is not None
        assert captured["previous"] != captured["root"]
        assert captured["previous_close_count"] == 1
        assert captured["successor_close_count"] == 1
        assert captured["root_close_count"] == 1
        _assert_closed(captured, "successor")
        _assert_closed(captured, "root")
    finally:
        _cleanup_leftovers(captured)


@pytest.mark.parametrize("walker", _WALKERS)
@pytest.mark.parametrize("secondary", ["successor", "root", "both"])
def test_walker_handoff_keeps_previous_primary_when_cleanup_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    walker: str,
    secondary: str,
) -> None:
    root = tmp_path.resolve()
    target = _tree(root)
    successor_error = (
        OSError(errno.ESTALE, f"{walker} successor close")
        if secondary in {"successor", "both"}
        else None
    )
    root_error = (
        OSError(errno.EBADF, f"{walker} root close")
        if secondary in {"root", "both"}
        else None
    )
    captured = _install_handoff_fault(
        monkeypatch,
        walker=walker,
        root=root,
        target=target,
        consume_previous=True,
        successor_error=successor_error,
        root_error=root_error,
    )
    try:
        with pytest.raises(BaseException) as info:
            _invoke(walker, target, root)
        primary = _primary_from(walker, info.value)
        assert primary is captured["primary"]
        assert captured["previous_close_count"] == 1
        assert captured["successor_close_count"] == 1
        assert captured["root_close_count"] == 1
        if successor_error is not None:
            _assert_note(primary, successor_error)
        else:
            _assert_closed(captured, "successor")
        if root_error is not None:
            _assert_note(primary, root_error)
        else:
            _assert_closed(captured, "root")
    finally:
        _cleanup_leftovers(captured)


@pytest.mark.parametrize("walker", _WALKERS)
def test_walker_repeated_handoff_failures_do_not_accumulate_successors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, walker: str
) -> None:
    root = tmp_path.resolve()
    target = _tree(root)
    completed = 0
    for _ in range(_REPEAT):
        with monkeypatch.context() as fault_patch:
            captured = _install_handoff_fault(
                fault_patch,
                walker=walker,
                root=root,
                target=target,
                consume_previous=False,
            )
            try:
                with pytest.raises(BaseException) as info:
                    _invoke(walker, target, root)
                assert _primary_from(walker, info.value) is captured["primary"]
                assert captured["previous_close_count"] == 1
                assert captured["successor_close_count"] == 1
                assert captured["root_close_count"] == 1
                _assert_closed(captured, "successor")
                _assert_closed(captured, "root")
                completed += 1
            finally:
                _cleanup_leftovers(captured)
    assert completed == _REPEAT


def _assert_directory_fd(fd: int, path: Path) -> None:
    opened = os.fstat(fd)
    expected = path.stat()
    assert stat.S_ISDIR(opened.st_mode)
    assert (opened.st_dev, opened.st_ino) == (expected.st_dev, expected.st_ino)


def test_directory_walkers_preserve_return_and_listing_contracts(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    target = _tree(root)
    names = ["first", "second", "third"]
    for name in names:
        (target / name).write_text(name, encoding="utf-8")

    root_fd = open_directory_no_follow(root, containment_root=root)
    deep_fd = open_directory_no_follow(target, containment_root=root)
    private_fd = safe_fs._open_directory_no_follow(target)
    parent_fd, parent = safe_fs._open_parent_dir(
        target / "payload.bin", containment_root=root, create=False
    )
    try:
        _assert_directory_fd(root_fd, root)
        _assert_directory_fd(deep_fd, target)
        _assert_directory_fd(private_fd, target)
        assert parent == target
        _assert_directory_fd(parent_fd, target)
    finally:
        os.close(parent_fd)
        os.close(private_fd)
        os.close(deep_fd)
        os.close(root_fd)

    assert sorted(list_directory_no_follow(target, containment_root=root)) == names
    limited = list_directory_no_follow_limited(
        target, max_entries=2, containment_root=root
    )
    assert len(limited) == 3
    assert set(limited) <= set(names)
