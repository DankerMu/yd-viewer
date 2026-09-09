"""`run` 入口的非阻塞 `flock` 封装：已有实例持锁时本 tick 跳过不排队（任务 12.3 / #85）。

契约来源：`docs/compute-loop-design.md` §10（cron 每小时调用的非阻塞 `flock` 包装，锁
覆盖发现、提交、等待、发布、清理全生命周期），
`openspec/changes/m2-producer-core/specs/run-controller/spec.md` 的「并发与锁」
Requirement。

本模块实现 issue #23 fixture 的下列裁决：

8. **`cron.lock_path` 非绝对路径 fail closed，闸门在封装的最前**（`_require_absolute`
   是 `run_with_lock` 的第一条语句，先于 `os.open`、先于任何目录创建、先于任何
   `expanduser`）。危害不是「路径不存在」而是路径被正常创建、正常打开却落在**错的
   地方**：cron 以 cwd=`$HOME` 调 `run`、人工补跑在 checkout 目录走同一入口，相对路径
   会让两边 `flock` 拿到两个不同的锁文件，互斥静默失效，两个控制器同时进入发布段。
   报错（`RunLockError`）MUST 指名 `cron.lock_path`。
   判据是 `Path(text).is_absolute()` **这一条**，刻意不先 `expanduser()`：`Path` 不展开
   `~`（`Path("~/x") / "y"` 得到 `'~/x/y'`），因此 `"yd.lock"` 与 `"~/yd.lock"` 两种形态
   都落在同一条判据的拒绝侧；先展开再判会把 `~/yd.lock` 判成绝对路径而放行。
   本封装拿到锁路径后**逐字**使用它：不 `expanduser`、不 `resolve`、不建父目录——父目录
   缺失是现场配置错误，按 `FileNotFoundError` 上抛，MUST NOT 由本封装替运维造目录。
   **刻意不选**在 `config.py` 装载期强制：`local.toml` 其余现场路径字段当前都不做绝对性
   校验，只为本字段开特例会让 `cli-config` spec 的 MUST 范围与实现不一致；闸放在唯一的
   消费点更窄且可测。

10. **不接线 `cli.py`**：本模块只交付可复用的包装函数，不改 CLI 接线。

11. **flock 语义**（部署前提：`cron.lock_path` 在 **node-22 本地文件系统** 的专属 `run/`
    目录；Linux NFS 把 `flock` 仿真为整文件 byte-range lock，本项目依赖的
    **per-open-file-description** 判别前提在那里不成立。业务代码不按路径前缀、hostname
    或平台猜文件系统；现场挂载验收归 M4 receipt，本地测试的 tmp 目录不是那份 receipt）：
    - `fcntl.flock(fd, LOCK_EX | LOCK_NB)`，**MUST NOT** 用 `fcntl.lockf`。在本地盘上
      `flock` 的锁挂在 **open file description** 上，`lockf`（POSIX record lock）的锁挂在
      **进程**上：后者下同一进程的第二次加锁会直接成功，手工补跑与 cron 在同一进程树里
      的互斥就此失效。（darwin 的 XNU 还把两者并进同一条 lock list，所以「测试自己
      flock + 实现 lockf」这种半边构造无法判别，见 `tests/test_controller_lock.py`。）
    - `LOCK_NB`：拿不到锁**立即**返回跳过，MUST NOT 排队等待——cron 每小时一 tick，排队
      只会堆出一串迟到的实例。
    - 跳过是**成功**语义：返回 `RunLockResult(acquired=False)`，不是异常、不是非零退出，
      且与「跑过了但返回 None」可区分（判 `acquired`，不判 `value`）。
    - 跳过分支 MUST NOT 调用被包裹的可调用对象。只有真实 `BlockingIOError` 竞争算跳过。
    - 锁文件及其专属目录是**长期哨兵**：释放时 MUST NOT `unlink` / `rename` / `replace`；
      删掉后另一实例会在**新 inode** 上建锁，两个持有者同时成立。当前 pathname 已是
      replacement 时同样原样保留。
    - 被包裹对象抛异常时锁仍释放（`finally`），异常原样外传：失败不该把锁泄漏到下一个
      tick。

12. **零新增依赖**：`fcntl`/`os`/`pathlib`/`stat` 全在 stdlib，MUST NOT 引入 `filelock`
    之类第三方包。

13. **持锁 fd 与命名路径 identity（#85）**：每次成功 `flock` 后以 `fstat(fd)` 冻结普通
    文件 `(st_dev, st_ino)`，再用 no-follow path stat 确认 `cron.lock_path` 是同一普通
    文件后才调用 action。首次核对不稳定（fd/path IO、非普通、缺失、identity 不等）时
    完整 unlock/close 旧 fd，从 open/flock 重取至多一次；重取不得 `O_CREAT`、不得跟随
    dangling symlink 造 target。重取遇真实竞争仍跳过；第二次仍不稳定则 `RunLockError`
    指名 `cron.lock_path`，action 零调用。初次 open/flock 的非竞争 `OSError` 保持原样；
    重取的非竞争 open/flock 失败收敛为带原 cause 的 `RunLockError`。action 返回或抛
    `BaseException` 后、unlock 前再核对命名路径：正常返回转 `RunLockError`；已有异常则
    保持同一对象/cause/旧 notes，只追加一条 expected/actual（或 unavailable/type/error）
    note。有限边界检查不能阻止两次核对之间的不合作 unlink；外部永不替换哨兵仍是防止
    双持有者的必要部署不变量。

竞争与真错误严格分流：只有 `BlockingIOError`（`EAGAIN`/`EWOULDBLOCK`）算「别的实例持
锁」；`EACCES`、`EOPNOTSUPP`、`ENOSPC`、`EIO` 等一律上抛（初次原样，重取包进
`RunLockError`）。把权限失败当成「跳过」会让互斥在一台配错权限的机器上静默变成
「永远跳过」，与本模块存在的理由相反。
"""

from __future__ import annotations

import fcntl
import os
import stat as stat_module
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "RunLockError",
    "RunLockResult",
    "run_with_lock",
]

#: 锁文件的创建模式（`O_CREAT` 时生效；已存在的锁文件不被 chmod）。
LOCK_FILE_MODE = 0o644

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_OPEN_BASE = os.O_RDWR | _NOFOLLOW | _NONBLOCK | _CLOEXEC
_FIRST_OPEN_FLAGS = _OPEN_BASE | os.O_CREAT
_RETRY_OPEN_FLAGS = _OPEN_BASE
_FLOCK_EX_NB = fcntl.LOCK_EX | fcntl.LOCK_NB


class RunLockError(RuntimeError):
    """锁路径形态非法，或持锁 identity 无法确认：本次 run 报错退出。"""


@dataclass(frozen=True)
class RunLockResult:
    """一次进入封装的结果。`acquired` 是跳过与执行的**唯一**判别字段。

    `acquired=False`（跳过）时 `value` 恒为 `None` 且被包裹对象零调用；
    `acquired=True` 时 `value` 是被包裹对象的返回值（可以本来就是 `None`）。
    """

    acquired: bool
    lock_path: Path
    value: Any = None


class _IdentityFailed(Exception):
    """入口 identity 核对不稳定；不是公开异常。"""


def run_with_lock(
    *,
    lock_path: str | Path,
    action: Callable[[], Any],
) -> RunLockResult:
    """在 `cron.lock_path` 的非阻塞独占 `flock` 下执行 `action`。

    Args:
        lock_path: `local.toml` 的 `cron.lock_path`，MUST 是绝对路径。
        action: 被锁覆盖的完整生命周期（发现、提交、等待、发布、清理）。

    Returns:
        `RunLockResult`：拿到锁则 `acquired=True` 且 `value` 是 `action()` 的返回值；
        锁被别的持有者占用则 `acquired=False`、`value is None`、`action` 零调用。

    Raises:
        RunLockError: `lock_path` 不是绝对路径（含 `~` 前缀形态）；或重取/identity
            核对失败。绝对路径闸不发生任何文件系统副作用。
        OSError: 初次打开锁文件失败，或初次 `flock` 遇到竞争以外的错误。
        BaseException: `action` 自己抛出的异常原样外传（锁已释放；若退出 identity
            同时漂移则追加一条 note）。
    """
    target = _require_absolute(lock_path)
    fd: int | None = None
    held = False
    retried = False
    try:
        while True:
            flags = _RETRY_OPEN_FLAGS if retried else _FIRST_OPEN_FLAGS
            try:
                fd = os.open(os.fspath(target), flags, LOCK_FILE_MODE)
            except OSError as orig:
                if retried:
                    raise RunLockError(
                        f"cron.lock_path 重取失败，实得 {str(target)!r}：{orig}"
                    ) from orig
                raise
            try:
                fcntl.flock(fd, _FLOCK_EX_NB)
            except BlockingIOError:
                return RunLockResult(acquired=False, lock_path=target, value=None)
            except OSError as orig:
                if retried:
                    raise RunLockError(
                        f"cron.lock_path 重取失败，实得 {str(target)!r}：{orig}"
                    ) from orig
                raise
            held = True
            try:
                frozen = _probe_held_identity(fd, target)
            except _IdentityFailed as probe:
                try:
                    _unlock_close(fd)
                finally:
                    fd = None
                    held = False
                if retried:
                    raise RunLockError(
                        f"cron.lock_path 持锁 identity 不稳定，实得 {str(target)!r}："
                        f"{probe}"
                    ) from probe.__cause__
                retried = True
                continue
            break
        action_error: BaseException | None = None
        value = None
        try:
            value = action()
        except BaseException as orig:  # noqa: BLE001 - retain primary for exit identity note
            action_error = orig
        note = _exit_drift_note(target, frozen)
        if note is not None:
            if action_error is None:
                raise RunLockError(note)
            action_error.add_note(note)
            raise action_error
        if action_error is not None:
            raise action_error
        return RunLockResult(acquired=True, lock_path=target, value=value)
    finally:
        if fd is not None:
            if held:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
            else:
                os.close(fd)


def _require_absolute(lock_path: str | Path) -> Path:
    """绝对路径闸（裁决 8）。MUST 先于任何文件系统副作用被调用。"""
    target = Path(lock_path)
    if not target.is_absolute():
        raise RunLockError(
            f"cron.lock_path 必须是绝对路径，实得 {str(lock_path)!r}："
            "相对路径与 `~` 前缀会随工作目录落到不同的锁文件上，使 run 的互斥静默失效"
        )
    return target


def _unlock_close(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _kind(info: os.stat_result) -> str:
    mode = info.st_mode
    if stat_module.S_ISLNK(mode):
        return "symlink"
    if stat_module.S_ISDIR(mode):
        return "directory"
    if stat_module.S_ISFIFO(mode):
        return "fifo"
    return "non-regular"


def _named_identity(path: Path) -> tuple[tuple[int, int] | None, str]:
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None, "unavailable"
    if not stat_module.S_ISREG(info.st_mode):
        return None, f"type={_kind(info)}"
    pair = (info.st_dev, info.st_ino)
    return pair, f"(st_dev, st_ino)={pair}"


def _probe_held_identity(fd: int, path: Path) -> tuple[int, int]:
    try:
        held = os.fstat(fd)
    except OSError as orig:
        raise _IdentityFailed(f"fstat 失败：{orig}") from orig
    if not stat_module.S_ISREG(held.st_mode):
        kind = _kind(held)
        raise _IdentityFailed(f"fd 类型为 {kind}")
    frozen = (held.st_dev, held.st_ino)
    try:
        named, actual = _named_identity(path)
    except OSError as orig:
        raise _IdentityFailed(f"path stat 失败：{orig}") from orig
    if named != frozen:
        raise _IdentityFailed(f"fd (st_dev, st_ino)={frozen} 与路径 {actual} 不一致")
    return frozen


def _exit_drift_note(path: Path, expected: tuple[int, int]) -> str | None:
    try:
        named, actual = _named_identity(path)
    except OSError as orig:
        named, actual = None, f"error={orig}"
    if named == expected:
        return None
    return (
        f"cron.lock_path {path} identity drifted: "
        f"expected (st_dev, st_ino)={expected}; actual {actual}"
    )
