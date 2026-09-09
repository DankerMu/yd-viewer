"""#109 DAT 第 0 列相对分钟：公开 check/publish seam 的值级、域闸与 same-fd 有界读。

负例从合法 bytes 出发按独立固定偏移做 `struct.pack` 手术，不调用/复制
`build_dat_bytes` 的分钟生成表达式。37 分钟小 DAT 用字面 `0, 37, 74`。
"""

from __future__ import annotations

import errno
import math
import os
import struct
from pathlib import Path

import pytest
from cfg_ic_fixtures import build_cfg_ic
from dat_fixtures import (
    DEFAULT_HEADER_TEXT,
    FIXED_HEADER_BYTES,
    FLOAT64_BYTES,
    build_dat_bytes,
    build_text_header,
)
from frontier_fixtures import snapshot_tree
from test_publish import (
    EXPECTED_ROWS,
    REACH_COUNT,
    RELATIVE_MINUTE,
    _assert_unchanged,
    build_scene,
)

from yd_producer import publish
from yd_producer._work_claim import (
    ClaimLostError,
    WorkClaim,
    current_identity,
)
from yd_producer.store.safe_fs import SafeFilesystemError

#: 独立登记：v2 数据区起点 = 1024 文本头 + st + nc + nc 个列编号。
_TABLE_END = FIXED_HEADER_BYTES + FLOAT64_BYTES * REACH_COUNT
_ROW_STRIDE = (REACH_COUNT + 1) * FLOAT64_BYTES
#: 37 分钟小 DAT 的手写行数/列数（不从生产 168 行反推）。
_SMALL_NC = 2
_SMALL_ROWS = 3
_SMALL_TABLE_END = FIXED_HEADER_BYTES + FLOAT64_BYTES * _SMALL_NC
_SMALL_STRIDE = (_SMALL_NC + 1) * FLOAT64_BYTES
#: 显式非 60 间隔与字面分钟列（0, 37, 74）。
_INTERVAL_37 = 37
_LITERAL_MINUTES_37 = (0.0, 37.0, 74.0)
#: 首/中/末行：168 行轴上的 0-based 下标。
_FIRST_ROW = 0
_MID_ROW = 84
_LAST_ROW = 167
#: 错值相对合法分钟的独立偏移（不复用 writer 的 `row * step` 表达式）。
_WRONG_DELTA = 60.0


def _minute_offset(
    row: int, *, table_end: int = _TABLE_END, stride: int = _ROW_STRIDE
) -> int:
    return table_end + row * stride


def _pack_le(value: float) -> bytes:
    return struct.pack("<d", value)


def _mutate_minute(payload: bytes, row: int, packed: bytes) -> bytes:
    """独立固定偏移手术：只改第 `row` 行第 0 列 8 字节。"""
    start = _minute_offset(row)
    mutated = bytearray(payload)
    mutated[start : start + FLOAT64_BYTES] = packed
    return bytes(mutated)


def _legal_production_bytes() -> bytes:
    return build_dat_bytes(nc=REACH_COUNT, rows=EXPECTED_ROWS)


def _small_37_bytes() -> bytes:
    """独立字面分钟列：header/列编号来自 helper，分钟值手写 0/37/74，不走 writer 分钟算式。"""
    head = (
        build_text_header(DEFAULT_HEADER_TEXT)
        + struct.pack("<d", 20260826.0)
        + struct.pack("<d", float(_SMALL_NC))
        + struct.pack("<dd", 1.0, 2.0)
    )
    assert len(head) == _SMALL_TABLE_END
    rows = bytearray()
    for minute, flow in zip(_LITERAL_MINUTES_37, (1.0, 2.0, 3.0), strict=True):
        rows.extend(_pack_le(minute))
        rows.extend(struct.pack("<dd", flow, flow))
    return head + bytes(rows)


def _claim_for_scene(scene) -> publish.PublishInputs:
    """把 DAT / checkpoint / 合并日志都放进 claimed exact work。"""
    claimed_dat = scene.work_dir / "yd.rivqdown.dat"
    claimed_checkpoint = scene.work_dir / "checkpoint.cfg.ic"
    claimed_log = scene.work_dir / "merged.log"
    claimed_dat.write_bytes(scene.dat.read_bytes())
    claimed_checkpoint.write_bytes(scene.checkpoint.read_bytes())
    claimed_log.write_bytes(scene.log.read_bytes())
    claim = WorkClaim(
        work_root=scene.work_root,
        work_dir=scene.work_dir,
        identity=current_identity(scene.work_dir),
    )
    return scene.make_inputs(
        scratch_dat=claimed_dat,
        scratch_checkpoint=claimed_checkpoint,
        merged_log=claimed_log,
        claim=claim,
    )


def test_legal_168_row_minutes_pass_check_and_publish_with_zero_pre_done_write(
    tmp_path: Path,
) -> None:
    scene = build_scene(tmp_path)
    before = snapshot_tree(scene.root)
    publish.check_publish_contract(scene.make_inputs())
    assert snapshot_tree(scene.root) == before
    result = publish.publish(scene.make_inputs())
    assert result.done_path.is_file()
    assert not scene.work_dir.exists()


def test_explicit_37_minute_small_dat_passes_and_default_60_rejects_same_bytes(
    tmp_path: Path,
) -> None:
    payload = _small_37_bytes()
    first, mid, last = (
        struct.unpack("<d", payload[off : off + FLOAT64_BYTES])[0]
        for off in (
            _minute_offset(0, table_end=_SMALL_TABLE_END, stride=_SMALL_STRIDE),
            _minute_offset(1, table_end=_SMALL_TABLE_END, stride=_SMALL_STRIDE),
            _minute_offset(2, table_end=_SMALL_TABLE_END, stride=_SMALL_STRIDE),
        )
    )
    assert (first, mid, last) == _LITERAL_MINUTES_37

    scene = build_scene(
        tmp_path,
        dat_payload=payload,
        checkpoint_payload=build_cfg_ic(
            mesh_count=3, river_count=_SMALL_NC, minute=RELATIVE_MINUTE
        ).payload,
    )
    before = snapshot_tree(scene.root)
    publish.check_publish_contract(
        scene.make_inputs(
            expected_rows=_SMALL_ROWS,
            reach_count=_SMALL_NC,
            variant_reach_count=_SMALL_NC,
            output_interval_minutes=_INTERVAL_37,
        )
    )
    assert snapshot_tree(scene.root) == before

    with pytest.raises(publish.PublishError, match=r"第 1 行相对分钟不符") as info:
        publish.check_publish_contract(
            scene.make_inputs(
                expected_rows=_SMALL_ROWS,
                reach_count=_SMALL_NC,
                variant_reach_count=_SMALL_NC,
            )
        )
    message = str(info.value)
    assert "期望 60.0" in message
    assert "实得 37.0" in message
    _assert_unchanged(before, scene)


@pytest.mark.parametrize(
    ("row", "packed", "expected", "actual_token"),
    [
        (
            _FIRST_ROW,
            _pack_le(60.0),
            0.0,
            "60.0",
        ),
        (
            _MID_ROW,
            _pack_le(5100.0),
            5040.0,
            "5100.0",
        ),
        (
            _LAST_ROW,
            _pack_le(10080.0),
            10020.0,
            "10080.0",
        ),
        (_MID_ROW, _pack_le(math.nan), 5040.0, "nan"),
        (_MID_ROW, _pack_le(math.inf), 5040.0, "inf"),
        (_MID_ROW, _pack_le(-math.inf), 5040.0, "-inf"),
    ],
    ids=["first+60", "mid+60", "last+60", "nan", "+inf", "-inf"],
)
def test_single_row_minute_mismatch_rejects_before_nfs_write(
    tmp_path: Path,
    row: int,
    packed: bytes,
    expected: float,
    actual_token: str,
) -> None:
    payload = _mutate_minute(_legal_production_bytes(), row, packed)
    scene = build_scene(tmp_path, dat_payload=payload)
    before = snapshot_tree(scene.root)
    with pytest.raises(publish.PublishError) as info:
        publish.publish(scene.make_inputs())
    message = str(info.value)
    assert f"第 {row} 行相对分钟不符" in message
    assert f"期望 {expected}" in message
    assert f"实得 {actual_token}" in message.lower()
    _assert_unchanged(before, scene)


def test_whole_column_plus_sixty_rejects_at_first_row(tmp_path: Path) -> None:
    payload = bytearray(_legal_production_bytes())
    for row in range(EXPECTED_ROWS):
        start = _minute_offset(row)
        (legal,) = struct.unpack("<d", payload[start : start + FLOAT64_BYTES])
        payload[start : start + FLOAT64_BYTES] = _pack_le(legal + _WRONG_DELTA)
    scene = build_scene(tmp_path, dat_payload=bytes(payload))
    before = snapshot_tree(scene.root)
    with pytest.raises(publish.PublishError) as info:
        publish.check_publish_contract(scene.make_inputs())
    message = str(info.value)
    assert "第 0 行相对分钟不符" in message
    assert "期望 0.0" in message
    assert "实得 60.0" in message
    _assert_unchanged(before, scene)


@pytest.mark.parametrize(
    "interval",
    [True, 60.0, "60", 0, -1],
    ids=["bool", "float", "str", "zero", "negative"],
)
def test_invalid_interval_is_rejected_before_dat_or_claim_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interval: object
) -> None:
    scene = build_scene(tmp_path)
    before = snapshot_tree(scene.root)
    standalone = scene.make_inputs(output_interval_minutes=interval)
    claimed_valid = _claim_for_scene(scene)
    claimed = scene.make_inputs(
        scratch_dat=claimed_valid.scratch_dat,
        scratch_checkpoint=claimed_valid.scratch_checkpoint,
        merged_log=claimed_valid.merged_log,
        claim=claimed_valid.claim,
        output_interval_minutes=interval,
    )
    opened: list[str] = []

    def boom_claimed(*_args, **_kwargs):
        opened.append("claimed")
        raise AssertionError("claim open must not run for illegal interval")

    def boom_nofollow(*_args, **_kwargs):
        opened.append("nofollow")
        raise AssertionError("DAT open must not run for illegal interval")

    monkeypatch.setattr(publish, "open_claimed_file", boom_claimed)
    monkeypatch.setattr(publish, "open_file_no_follow", boom_nofollow)
    monkeypatch.setattr(
        publish,
        "read_bytes_limited_no_follow",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("header read must not run for illegal interval")
        ),
    )
    monkeypatch.setattr(
        publish,
        "read_claimed_bytes",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("claimed header read must not run for illegal interval")
        ),
    )
    monkeypatch.setattr(
        publish,
        "validate_claim",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("claim validate must not run for illegal interval")
        ),
    )

    for inputs in (standalone, claimed):
        with pytest.raises(
            publish.PublishError, match="output_interval_minutes 必须为正整数"
        ):
            publish.check_publish_contract(inputs)
        with pytest.raises(
            publish.PublishError, match="output_interval_minutes 必须为正整数"
        ):
            publish.publish(inputs)
    assert opened == []
    _assert_unchanged(before, scene)


def test_short_minute_read_rejects_and_closes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = build_scene(tmp_path)
    before = snapshot_tree(scene.root)
    real_pread = os.pread
    real_close = os.close
    fds: list[int] = []
    closed: list[int] = []

    def recording_pread(fd: int, n: int, offset: int) -> bytes:
        if n == FLOAT64_BYTES and offset >= _TABLE_END:
            fds.append(fd)
            if offset == _minute_offset(_LAST_ROW):
                return b"\x00\x00\x00"
        return real_pread(fd, n, offset)

    def recording_close(fd: int) -> None:
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(os, "pread", recording_pread)
    monkeypatch.setattr(os, "close", recording_close)
    with pytest.raises(publish.PublishError, match=r"第 167 行相对分钟短读") as info:
        publish.check_publish_contract(scene.make_inputs())
    message = str(info.value)
    assert "期望 10020.0" in message
    assert "实得 3 字节" in message
    assert fds, "minute pread never ran"
    minute_fd = fds[0]
    assert all(fd == minute_fd for fd in fds)
    assert minute_fd in closed
    _assert_unchanged(before, scene)


def test_minute_read_oserror_converges_and_closes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = build_scene(tmp_path)
    before = snapshot_tree(scene.root)
    real_pread = os.pread
    real_close = os.close
    fds: list[int] = []
    closed: list[int] = []

    def exploding_pread(fd: int, n: int, offset: int) -> bytes:
        if n == FLOAT64_BYTES and offset >= _TABLE_END:
            fds.append(fd)
            if offset == _minute_offset(_MID_ROW):
                raise OSError(errno.EIO, "injected minute EIO")
        return real_pread(fd, n, offset)

    def recording_close(fd: int) -> None:
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(os, "pread", exploding_pread)
    monkeypatch.setattr(os, "close", recording_close)
    with pytest.raises(publish.PublishError, match="读取失败"):
        publish.check_publish_contract(scene.make_inputs())
    assert fds[0] in closed
    _assert_unchanged(before, scene)


def test_minute_read_baseexception_still_closes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = build_scene(tmp_path)
    real_pread = os.pread
    real_close = os.close
    fds: list[int] = []
    closed: list[int] = []

    def exploding_pread(fd: int, n: int, offset: int) -> bytes:
        if n == FLOAT64_BYTES and offset >= _TABLE_END:
            fds.append(fd)
            if offset == _minute_offset(_FIRST_ROW):
                raise KeyboardInterrupt()
        return real_pread(fd, n, offset)

    def recording_close(fd: int) -> None:
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(os, "pread", exploding_pread)
    monkeypatch.setattr(os, "close", recording_close)
    with pytest.raises(KeyboardInterrupt):
        publish.check_publish_contract(scene.make_inputs())
    assert fds[0] in closed


@pytest.mark.parametrize("leg", ["standalone", "claim"], ids=["standalone", "claim"])
def test_same_fd_reads_exactly_eight_bytes_per_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leg: str
) -> None:
    scene = build_scene(tmp_path)
    inputs = scene.make_inputs() if leg == "standalone" else _claim_for_scene(scene)
    real_open_nofollow = publish.open_file_no_follow
    real_open_claimed = publish.open_claimed_file
    real_pread = os.pread
    real_close = os.close
    opened: list[tuple[str, int]] = []
    preads: list[tuple[int, int, int]] = []
    closed: list[int] = []

    def wrapping_nofollow(path, *args, **kwargs):
        fd = real_open_nofollow(path, *args, **kwargs)
        if Path(path) == inputs.scratch_dat:
            opened.append(("nofollow", fd))
        return fd

    def wrapping_claimed(claim, path):
        fd = real_open_claimed(claim, path)
        if Path(path) == inputs.scratch_dat:
            opened.append(("claimed", fd))
        return fd

    def wrapping_pread(fd: int, n: int, offset: int) -> bytes:
        if n == FLOAT64_BYTES and offset >= _TABLE_END:
            preads.append((fd, n, offset))
        return real_pread(fd, n, offset)

    def wrapping_close(fd: int) -> None:
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(publish, "open_file_no_follow", wrapping_nofollow)
    monkeypatch.setattr(publish, "open_claimed_file", wrapping_claimed)
    monkeypatch.setattr(os, "pread", wrapping_pread)
    monkeypatch.setattr(os, "close", wrapping_close)

    publish.check_publish_contract(inputs)

    expected_opener = "nofollow" if leg == "standalone" else "claimed"
    minute_opens = [item for item in opened if item[0] == expected_opener]
    assert minute_opens, opened
    minute_fd = minute_opens[-1][1]
    assert all(item[0] == minute_fd for item in preads)
    assert [item[1] for item in preads] == [FLOAT64_BYTES] * EXPECTED_ROWS
    assert sum(item[1] for item in preads) == EXPECTED_ROWS * FLOAT64_BYTES
    assert [item[2] for item in preads] == [
        _minute_offset(row) for row in range(EXPECTED_ROWS)
    ]
    assert minute_fd in closed
    other = "claimed" if expected_opener == "nofollow" else "nofollow"
    assert other not in {name for name, _fd in opened}


@pytest.mark.parametrize(
    ("leg", "factory"),
    [
        (
            "standalone",
            lambda path: PermissionError(errno.EACCES, "minute open denied"),
        ),
        (
            "standalone",
            lambda path: SafeFilesystemError(
                f"Target file must not be a symlink: {path}"
            ),
        ),
        (
            "claim",
            lambda path: ClaimLostError("claimed minute open denied", path=path),
        ),
    ],
    ids=["standalone-eacces", "standalone-safe-fs", "claim-lost"],
)
def test_minute_open_refusal_converges_to_publish_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    leg: str,
    factory,
) -> None:
    scene = build_scene(tmp_path)
    before = snapshot_tree(scene.root)
    inputs = scene.make_inputs() if leg == "standalone" else _claim_for_scene(scene)
    orig = factory(inputs.scratch_dat)
    opener = "open_file_no_follow" if leg == "standalone" else "open_claimed_file"
    monkeypatch.setattr(publish, opener, lambda *_a, **_k: (_ for _ in ()).throw(orig))
    with pytest.raises(publish.PublishError, match="读取失败") as info:
        publish.check_publish_contract(inputs)
    assert info.value.__cause__ is orig
    _assert_unchanged(before, scene)


def test_minute_close_error_converges_and_preserves_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = build_scene(tmp_path)
    before = snapshot_tree(scene.root)
    real_pread = os.pread
    real_close = os.close
    fds: list[int] = []
    close_error = OSError(errno.EIO, "injected minute close EIO")

    def recording_pread(fd: int, n: int, offset: int) -> bytes:
        if n == FLOAT64_BYTES and offset >= _TABLE_END:
            fds.append(fd)
        return real_pread(fd, n, offset)

    def exploding_close(fd: int) -> None:
        if fds and fd == fds[0]:
            real_close(fd)
            raise close_error
        return real_close(fd)

    monkeypatch.setattr(os, "pread", recording_pread)
    monkeypatch.setattr(os, "close", exploding_close)
    with pytest.raises(publish.PublishError, match="读取失败") as info:
        publish.check_publish_contract(scene.make_inputs())
    assert info.value.__cause__ is close_error
    _assert_unchanged(before, scene)

    short = build_scene(tmp_path / "short")
    before_short = snapshot_tree(short.root)
    short_fds: list[int] = []
    short_close = OSError(errno.EBADF, "injected short-read close")

    def short_pread(fd: int, n: int, offset: int) -> bytes:
        if n == FLOAT64_BYTES and offset >= _TABLE_END:
            short_fds.append(fd)
            if offset == _minute_offset(_LAST_ROW):
                return b"\x00\x00\x00"
        return real_pread(fd, n, offset)

    def short_close_fn(fd: int) -> None:
        if short_fds and fd == short_fds[0]:
            real_close(fd)
            raise short_close
        return real_close(fd)

    monkeypatch.setattr(os, "pread", short_pread)
    monkeypatch.setattr(os, "close", short_close_fn)
    with pytest.raises(
        publish.PublishError, match=r"第 167 行相对分钟短读"
    ) as short_info:
        publish.check_publish_contract(short.make_inputs())
    assert short_info.value.__cause__ is None
    assert any("close also failed" in note for note in short_info.value.__notes__)
    _assert_unchanged(before_short, short)
