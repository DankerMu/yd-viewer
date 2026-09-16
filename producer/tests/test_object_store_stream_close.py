"""Real-fd stream close-causality scenes for LocalObjectStore.iter_bytes (#185).

Injection arms only after the real open_file_no_follow returns so directory
and opener fds stay untouched. Failed-close leftover cleanup uses the saved
real close and is separate from the one-attempt ownership assertion.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from yd_producer.store import object_store as object_store_module
from yd_producer.store.object_store import LocalObjectStore, ObjectStoreError

_KEY = "raw/gfs/2026050700/payload.bin"
_PAYLOAD = b"0123456789"
_FIRST_CHUNK = b"0123"
_EXPECTED_CHUNKS = [b"0123", b"4567", b"89"]
_CHUNK_SIZE = 4
_EXPECTED_SIZE = 10
_EXPECTED_DIGEST = "84d89877f0d4041efb6bf91a16f0248f2fd573e6af05c19f96bedb9f882f7882"


def _plant_store(tmp_path: Path) -> LocalObjectStore:
    store = LocalObjectStore(root=tmp_path)
    store.write_bytes_atomic(_KEY, _PAYLOAD)
    return store


def _install_stream_faults(
    monkeypatch: pytest.MonkeyPatch,
    *,
    read_error: BaseException | None = None,
    close_error: OSError | None = None,
) -> dict[str, int | None]:
    real_open_file = object_store_module.open_file_no_follow
    real_read = os.read
    real_close = os.close
    captured: dict[str, int | None] = {
        "fd": None,
        "close_count": 0,
        "read_count": 0,
    }

    def opening(path, *, containment_root=None):
        fd = real_open_file(path, containment_root=containment_root)
        captured["fd"] = fd
        return fd

    def reading(fd: int, n: int) -> bytes:
        if captured["fd"] is not None and fd == captured["fd"]:
            captured["read_count"] += 1
            if read_error is not None and captured["read_count"] > 1:
                raise read_error
        return real_read(fd, n)

    def closing(fd: int) -> None:
        if captured["fd"] is not None and fd == captured["fd"]:
            captured["close_count"] += 1
            real_close(fd)
            if close_error is not None:
                raise close_error
            return
        real_close(fd)

    monkeypatch.setattr(object_store_module, "open_file_no_follow", opening)
    monkeypatch.setattr(os, "read", reading)
    monkeypatch.setattr(os, "close", closing)
    captured["real_close"] = real_close  # type: ignore[assignment]
    return captured


def _cleanup_injected_close(
    captured: dict[str, int | None], close_error: OSError | None
) -> None:
    fd = captured["fd"]
    if fd is None or close_error is None:
        return
    real_close = captured["real_close"]
    try:
        real_close(fd)
    except OSError:
        return


def _assert_close_once(captured: dict[str, int | None]) -> None:
    assert captured["fd"] is not None
    assert captured["close_count"] == 1


def _assert_close_note(error: BaseException, close_error: OSError) -> None:
    notes = getattr(error, "__notes__", ())
    joined = "".join(notes)
    assert str(close_error) in joined
    assert type(close_error).__name__ in joined
    assert "file descriptor close also failed" in joined


def _consume_after_first_chunk(stream: Iterator[bytes]) -> list[bytes]:
    first = next(stream)
    assert first == _FIRST_CHUNK
    return [first, *list(stream)]


@pytest.mark.parametrize(
    "via_checksum",
    [False, True],
    ids=["iter_bytes", "size_and_checksum"],
)
def test_successful_stream_and_checksum_close_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, via_checksum: bool
) -> None:
    store = _plant_store(tmp_path)
    captured = _install_stream_faults(monkeypatch)

    if via_checksum:
        assert store.size_and_checksum(_KEY, chunk_size=_CHUNK_SIZE) == (
            _EXPECTED_SIZE,
            _EXPECTED_DIGEST,
        )
    else:
        stream = store.iter_bytes(_KEY, chunk_size=_CHUNK_SIZE)
        assert _consume_after_first_chunk(stream) == _EXPECTED_CHUNKS

    _assert_close_once(captured)
    assert (tmp_path / _KEY).read_bytes() == _PAYLOAD


def test_read_oserror_keeps_read_cause_and_closes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _plant_store(tmp_path)
    read_error = OSError(errno.EIO, "injected read EIO")
    captured = _install_stream_faults(monkeypatch, read_error=read_error)
    stream = store.iter_bytes(_KEY, chunk_size=_CHUNK_SIZE)

    with pytest.raises(ObjectStoreError) as info:
        _consume_after_first_chunk(stream)

    assert info.value.__cause__ is read_error
    assert not getattr(info.value, "__notes__", ())
    _assert_close_once(captured)


def test_read_and_close_oserror_keeps_read_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _plant_store(tmp_path)
    read_error = OSError(errno.EIO, "injected read EIO")
    close_error = OSError(errno.ESTALE, "stale close")
    captured = _install_stream_faults(
        monkeypatch, read_error=read_error, close_error=close_error
    )
    stream = store.iter_bytes(_KEY, chunk_size=_CHUNK_SIZE)

    with pytest.raises(ObjectStoreError) as info:
        _consume_after_first_chunk(stream)

    assert info.value.__cause__ is read_error
    assert info.value.__cause__ is not close_error
    _assert_close_note(info.value, close_error)
    _assert_close_once(captured)
    _cleanup_injected_close(captured, close_error)


def test_close_only_oserror_becomes_object_store_error_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _plant_store(tmp_path)
    close_error = OSError(errno.ESTALE, "stale close")
    captured = _install_stream_faults(monkeypatch, close_error=close_error)
    stream = store.iter_bytes(_KEY, chunk_size=_CHUNK_SIZE)

    with pytest.raises(ObjectStoreError) as info:
        _consume_after_first_chunk(stream)

    assert info.value.__cause__ is close_error
    assert not getattr(info.value, "__notes__", ())
    _assert_close_once(captured)
    _cleanup_injected_close(captured, close_error)


@pytest.mark.parametrize(
    ("exc_type", "message"),
    [
        (KeyboardInterrupt, "primary interrupt"),
        (SystemExit, "primary exit"),
    ],
    ids=["keyboardinterrupt", "systemexit"],
)
@pytest.mark.parametrize(
    "with_close_error",
    [False, True],
    ids=["read_only", "read_and_close"],
)
def test_cancellation_keeps_identity_and_closes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc_type: type[BaseException],
    message: str,
    with_close_error: bool,
) -> None:
    store = _plant_store(tmp_path)
    injected = exc_type(message)
    close_error = OSError(errno.ESTALE, "stale close") if with_close_error else None
    captured = _install_stream_faults(
        monkeypatch, read_error=injected, close_error=close_error
    )
    stream = store.iter_bytes(_KEY, chunk_size=_CHUNK_SIZE)

    with pytest.raises(exc_type) as info:
        _consume_after_first_chunk(stream)

    assert info.value is injected
    assert not isinstance(info.value, ObjectStoreError)
    if close_error is None:
        assert not getattr(info.value, "__notes__", ())
    else:
        assert info.value is not close_error
        _assert_close_note(info.value, close_error)
    _assert_close_once(captured)
    _cleanup_injected_close(captured, close_error)


@pytest.mark.parametrize(
    "fault",
    ["read", "read_and_close", "close_only"],
    ids=["read", "read_and_close", "close_only"],
)
def test_checksum_consumer_observes_stream_failure_without_partial_tuple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    store = _plant_store(tmp_path)
    read_error = (
        OSError(errno.EIO, "injected read EIO") if fault != "close_only" else None
    )
    close_error = OSError(errno.ESTALE, "stale close") if fault != "read" else None
    captured = _install_stream_faults(
        monkeypatch, read_error=read_error, close_error=close_error
    )

    with pytest.raises(ObjectStoreError) as info:
        store.size_and_checksum(_KEY, chunk_size=_CHUNK_SIZE)

    if fault == "close_only":
        assert info.value.__cause__ is close_error
        assert not getattr(info.value, "__notes__", ())
    else:
        assert info.value.__cause__ is read_error
        if close_error is None:
            assert not getattr(info.value, "__notes__", ())
        else:
            _assert_close_note(info.value, close_error)
    _assert_close_once(captured)
    _cleanup_injected_close(captured, close_error)


def test_iter_bytes_is_lazy_until_first_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _plant_store(tmp_path)
    captured = _install_stream_faults(monkeypatch)

    stream = store.iter_bytes(_KEY, chunk_size=_CHUNK_SIZE)

    assert captured["fd"] is None
    assert captured["close_count"] == 0
    assert next(stream) == _FIRST_CHUNK
    assert captured["fd"] is not None
    stream.close()
    _assert_close_once(captured)
