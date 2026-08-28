"""`yd_producer.store.safe_fs` 的新写覆盖：拒绝分型两条腿 + 限量读的字节上限。

本文件**不是**快照：pin `8ae9b8f2` 的 `tests/test_safe_fs.py` 14 个用例里无一触及
`S_ISREG`，也无一在写入面（`atomic_write_bytes_no_follow` / `rename_entry_no_follow`）
上测符号链接叶与祖先；快照文件是逐字节等价物，不得改动，故新覆盖落在这里。

期望值一律取自 pin 的 `packages/common/safe_fs.py` 源码，不从运行结果回读：

- 非常规文件：`open_file_no_follow` 先 `os.stat(..., follow_symlinks=False)` 判
  `S_ISREG`（前置），`os.open` 之后再 `os.fstat` 判一次 `S_ISREG`（后置，堵 TOCTOU
  窗口）；两处都抛 `SafeFilesystemError("Target file must be a regular file: ...")`。
  两腿的消息**逐字节相同**（是两个各自独立的字面量），所以断言消息只能证明「拒了」，
  不能区分是哪条腿拒的；前置腿由 `os.open` recorder 用例单独钉死（拒绝发生在打开之前），
  后置腿由前置 stat 谎报用例钉死。
- 写入面符号链接的**叶**：`atomic_write_bytes_no_follow` 走 `_reject_existing_symlink`，
  这条腿是单点的——把它的 `S_ISLNK` 判断摘掉，`test_atomic_write_refuses_a_symlink_leaf`
  独家变红。
- 写入面符号链接的**祖先**、以及 `rename_entry_no_follow` 两侧父目录：pin 在这里是
  **纵深防御**，同一次拒绝有多条独立的腿（`_DIR_FLAGS` 的 `O_NOFOLLOW` 组件行走、
  `_verify_fd_matches_path` 的前后两次调用、`open_directory_no_follow` 里 `_lstat_dir`
  的 `S_ISLNK`/`S_ISDIR` 与目录身份复核）。实测口径：单摘任何一条腿，本文件这两条用例
  都**不**变红（例如摘掉 `O_NOFOLLOW` 只有快照用例
  `test_directory_identity_refuses_symlink_components` 变红）。所以它们声明并强制的是
  **端到端结果**——「符号链接祖先/父目录上的写与改名一律被拒，且真实目录不被触碰」，
  不是任何一条具体的腿；非空洞性由「三条腿同时摘掉」（写入面）与「父目录先 `resolve()`
  再打开」（改名面，两个参数化各有自己的判别器）证明。具体某条腿的归属由快照用例
  `test_safe_fs.py` 承担，不由本文件声称。
- `rename_entry_no_follow` 的叶语义**不是**拒绝：其 docstring 明写符号链接
  「is MOVED as a link and never followed or inspected」，本文件按 pin 语义钉死这一点；
  它的拒绝面在两侧父目录（`open_directory_no_follow` 的组件行走）。

平台口径：符号链接组件的拒绝在 macOS 上以 `ENOTDIR`、在 Linux 上以 `ELOOP` 到达
（`_open_child_dir` 两个分支都归一成 `SafeFilesystemError`），故一律只断异常**类型**，
不断消息，与快照用例 `test_directory_identity_refuses_symlink_components` 同口径。

本文件还承担一条**非拒绝**性质：`read_bytes_limited_no_follow` 的字节上限（见文件末节）。
它不是拒绝分型，但落点相同——`test_safe_fs.py` 是逐字节等价的快照、不得改动，而这条
性质在别处无人钉死。
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    atomic_write_bytes_no_follow,
    open_file_no_follow,
    read_bytes_limited_no_follow,
    read_bytes_no_follow,
    rename_entry_no_follow,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """已解析的临时根。

    safe_fs 的组件行走拒绝符号链接祖先，而 macOS 的 `/var` → `/private/var` 本身就是
    符号链接，未解析的 pytest tmp 根会被无条件拒绝，掩盖用例真正要测的那一条。
    """

    return tmp_path.resolve()


# --- 非常规文件（S_ISREG 前后置校验） ----------------------------------------


def test_open_file_no_follow_refuses_a_fifo_target(root: Path) -> None:
    fifo = root / "payload.fifo"
    os.mkfifo(fifo)

    # FIFO 的读端以 O_NONBLOCK 先开、再挂一个写端：前置校验被摘掉时，
    # 实现里的阻塞式 O_RDONLY 会立刻返回而不是把用例挂死。
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    writer = os.open(fifo, os.O_WRONLY)
    try:
        with pytest.raises(SafeFilesystemError, match="must be a regular file"):
            open_file_no_follow(fifo, containment_root=root)
        with pytest.raises(SafeFilesystemError, match="must be a regular file"):
            read_bytes_no_follow(fifo, containment_root=root)
    finally:
        os.close(writer)
        os.close(reader)


def test_open_file_no_follow_refuses_a_fifo_before_calling_os_open(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """前置 `S_ISREG` 腿：拒绝必须发生在 `os.open` **之前**，设备节点一次都不许被打开。

    只断异常类型/消息无法钉死这条腿：`_READ_FLAGS` 带 `O_NONBLOCK`，删掉前置校验后
    控制流会落到 `os.open` 成功、再由后置 `fstat` 腿抛出**逐字节相同**的消息——拒绝的
    「值」不变，变的是「拒绝发生在打开之前」这条可观测性质。快照文件不可改，故唯一
    可构造的判别器是把 `os.open` 包成 recorder，断言目标名从未出现在调用记录里。
    """

    fifo = root / "payload.fifo"
    os.mkfifo(fifo)

    opened: list[str] = []
    real_open = os.open

    def recording_open(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append(os.fsdecode(os.fspath(path)))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)

    with pytest.raises(SafeFilesystemError, match="must be a regular file"):
        open_file_no_follow(fifo, containment_root=root)

    # recorder 确实接上了：父目录组件行走必然至少开过一次目录 fd。
    assert opened, "recorder 未被调用，判别器失准"
    assert fifo.name not in opened, f"FIFO 在拒绝前被 os.open 打开了：{opened}"


def test_open_file_no_follow_refuses_a_non_regular_fd_after_the_open(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """后置 `fstat` 校验：前置 stat 说是普通文件、fd 却不是，仍须拒绝。

    这是 TOCTOU 窗口，只能在 OS 边界上模拟：把前置 `os.stat`（唯一带 `dir_fd` 且
    `follow_symlinks=False` 的那一次调用）替换成「这是普通文件」的谎报，其余调用原样
    透传。目标是一个目录——它能被 `O_RDONLY|O_NOFOLLOW` 打开，于是执行流必然抵达
    `os.fstat` 后置分支。
    """

    target = root / "payload"
    target.mkdir()
    real_stat = os.stat
    lie = os.stat_result((stat.S_IFREG | 0o644, 0, 0, 1, 0, 0, 0, 0, 0, 0))

    def fake_stat(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if (
            kwargs.get("dir_fd") is not None
            and path == target.name
            and kwargs.get("follow_symlinks") is not False
        ):
            raise AssertionError("前置校验的调用口径变了，谎报窗口失准")
        if kwargs.get("dir_fd") is not None and path == target.name:
            return lie
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fake_stat)

    with pytest.raises(SafeFilesystemError, match="must be a regular file"):
        open_file_no_follow(target, containment_root=root)


# --- 写入面：符号链接叶与符号链接祖先 -----------------------------------------


def test_atomic_write_refuses_a_symlink_leaf(root: Path) -> None:
    outside = root / "outside.txt"
    outside.write_bytes(b"untouched")
    target = root / "target.txt"
    target.symlink_to(outside)

    with pytest.raises(SafeFilesystemError):
        atomic_write_bytes_no_follow(target, b"payload", containment_root=root)

    assert outside.read_bytes() == b"untouched"


def test_atomic_write_refuses_a_symlinked_ancestor(root: Path) -> None:
    real = root / "real"
    real.mkdir()
    link = root / "link"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(SafeFilesystemError):
        atomic_write_bytes_no_follow(
            link / "target.txt", b"payload", containment_root=root
        )

    assert list(real.iterdir()) == []


@pytest.mark.parametrize("symlinked_side", ["source", "destination"])
def test_rename_entry_refuses_a_symlinked_parent(
    root: Path, symlinked_side: str
) -> None:
    source_parent = root / "src"
    source_parent.mkdir()
    (source_parent / "entry.txt").write_bytes(b"payload")
    dest_parent = root / "dst"
    dest_parent.mkdir()
    link = root / "link"

    if symlinked_side == "source":
        link.symlink_to(source_parent, target_is_directory=True)
        parent, destination = link, dest_parent
    else:
        link.symlink_to(dest_parent, target_is_directory=True)
        parent, destination = source_parent, link

    with pytest.raises(SafeFilesystemError):
        rename_entry_no_follow(
            parent, "entry.txt", destination, "moved.txt", containment_root=root
        )

    assert (source_parent / "entry.txt").read_bytes() == b"payload"
    assert not (dest_parent / "moved.txt").exists()


def test_rename_entry_moves_a_symlink_leaf_as_a_link(root: Path) -> None:
    # pin 语义（`rename_entry_no_follow` docstring）：叶上的符号链接被整体搬走，
    # 既不跟随也不检查。这里钉死这条**非拒绝**语义，防后续组读成写入面的兜底闸门。
    source_parent = root / "src"
    source_parent.mkdir()
    dest_parent = root / "dst"
    dest_parent.mkdir()
    payload = root / "payload.txt"
    payload.write_bytes(b"untouched")
    (source_parent / "entry").symlink_to(payload)

    rename_entry_no_follow(
        source_parent, "entry", dest_parent, "moved", containment_root=root
    )

    assert not (source_parent / "entry").is_symlink()
    assert (dest_parent / "moved").is_symlink()
    assert os.readlink(dest_parent / "moved") == str(payload)
    assert payload.read_bytes() == b"untouched"


# --- 限量读的字节上限（非拒绝性质：有界读） ------------------------------------


def test_read_bytes_limited_reads_at_most_one_sentinel_byte_past_the_ceiling(
    root: Path,
) -> None:
    """`read_bytes_limited_no_follow` 的**上限**方向：至多读回 `max_bytes + 1` 字节。

    期望值取自 pin 的实现契约（docstring「Read at most max_bytes plus one sentinel
    byte」＋ `limit = max_bytes + 1` 的有界循环），不从运行结果回读。

    声明集 / 执行集：这条断言同时钉死**两个方向**，因为它断的是**相等**而不是不等式。

    - 上限方向（内存有界）：循环换成一次性无界 `os.read` / `limit` 放大成
      `max_bytes * 1000 + 1` 时返回 100 字节，本条红。此前全仓无人钉死这一维——
      `test_object_store.py` 的「超限读」用例由 `object_store.py` 事后的
      `len(content) > max_bytes` 独立检查满足，把整个上限删掉全套仍全绿。
    - 下限方向（哨兵字节存在，超限可判定）：`limit` 改成 `max_bytes` 时返回 4 字节，
      本条同样红（快照用例 `test_read_bytes_limited_refuses_beyond_the_byte_ceiling`
      原本只钉住这一维）。

    内容用互不相同的字节而非等长填充：一并钉死「读回的是文件**前缀**」，而不只是长度。
    """

    payload = bytes(range(100))
    target = root / "payload.bin"
    target.write_bytes(payload)

    content = read_bytes_limited_no_follow(target, max_bytes=4, containment_root=root)

    assert content == payload[:5]


def test_read_bytes_limited_returns_the_whole_file_when_it_fits(root: Path) -> None:
    # 上限那一维的对照：文件短于上限时按 EOF 收尾、返回全文，不会为凑满 `limit`
    # 而阻塞或补齐。少了这条，「至多」可能被实现成「恰好」。
    payload = bytes(range(3))
    target = root / "short.bin"
    target.write_bytes(payload)

    assert (
        read_bytes_limited_no_follow(target, max_bytes=10, containment_root=root)
        == payload
    )
