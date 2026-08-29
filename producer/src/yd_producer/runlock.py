"""`run` 入口的非阻塞 `flock` 封装：已有实例持锁时本 tick 跳过不排队（任务 12.3）。

契约来源：`docs/compute-loop-design.md` §10（cron 每小时调用的非阻塞 `flock` 包装，锁
覆盖发现、提交、等待、发布、清理全生命周期），
`openspec/changes/m2-producer-core/specs/run-controller/spec.md` 的「并发与锁」
Requirement 与其三条 Scenario。

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

10. **不接线 `cli.py`**：`cli.py` 的 `run` 仍是 `_unimplemented`，接线归任务 14.1
    （issue #26/#27）。本模块只交付可复用的包装函数。

11. **flock 语义**：
    - `fcntl.flock(fd, LOCK_EX | LOCK_NB)`，**MUST NOT** 用 `fcntl.lockf`。`flock` 的锁
      挂在 **open file description** 上，`lockf`（POSIX record lock）的锁挂在**进程**上：
      后者下同一进程的第二次加锁会直接成功，手工补跑与 cron 在同一进程树里的互斥就此
      失效。（darwin 的 XNU 还把两者并进同一条 lock list，所以「测试自己 flock + 实现
      lockf」这种半边构造无法判别，见 `tests/test_controller_lock.py` 的进程内用例。）
    - `LOCK_NB`：拿不到锁**立即**返回跳过，MUST NOT 排队等待——cron 每小时一 tick，排队
      只会堆出一串迟到的实例。
    - 跳过是**成功**语义：返回 `RunLockResult(acquired=False)`，不是异常、不是非零退出，
      且与「跑过了但返回 None」可区分（判 `acquired`，不判 `value`）。
    - 跳过分支 MUST NOT 调用被包裹的可调用对象，且此分支零文件系统副作用（除锁文件
      本身按 `O_CREAT` 语义可能被创建——那是取锁的必要条件，不是发现动作）。
    - 释放时 MUST NOT `unlink` 锁文件：删掉后另一实例会在**新 inode** 上建锁，两个持有者
      同时成立。锁文件是长期存在的哨兵，不是临时文件。
    - 被包裹对象抛异常时锁仍释放（`finally`），异常原样外传：失败不该把锁泄漏到下一个
      tick。

12. **零新增依赖**：`fcntl`/`os`/`pathlib` 全在 stdlib，MUST NOT 引入 `filelock` 之类
    第三方包。

竞争与真错误严格分流：只有 `BlockingIOError`（`EAGAIN`/`EWOULDBLOCK`）算「别的实例持
锁」；`EACCES`、`ENOSPC`、`EIO` 等一律上抛。把权限失败当成「跳过」会让互斥在一台配错权限
的机器上静默变成「永远跳过」，与本模块存在的理由相反。
"""

from __future__ import annotations

import fcntl
import os
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


class RunLockError(RuntimeError):
    """锁路径形态非法：本次 run 报错退出，不创建锁文件、不执行发现。"""


@dataclass(frozen=True)
class RunLockResult:
    """一次进入封装的结果。`acquired` 是跳过与执行的**唯一**判别字段。

    `acquired=False`（跳过）时 `value` 恒为 `None` 且被包裹对象零调用；
    `acquired=True` 时 `value` 是被包裹对象的返回值（可以本来就是 `None`）。
    """

    acquired: bool
    lock_path: Path
    value: Any = None


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
        RunLockError: `lock_path` 不是绝对路径（含 `~` 前缀形态）。此时不发生任何文件
            系统副作用。
        OSError: 打开锁文件失败（父目录不存在、权限不足等），或 `flock` 遇到竞争以外的
            错误。
        Exception: `action` 自己抛出的任何异常原样外传（锁已释放）。
    """
    target = _require_absolute(lock_path)

    fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, LOCK_FILE_MODE)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # 别的实例持锁：本 tick 跳过，不排队、不执行发现、不删锁文件。
            return RunLockResult(acquired=False, lock_path=target, value=None)
        try:
            value = action()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        # 关闭 fd 即释放 flock；锁文件本身**永远**保留（裁决 11）。
        os.close(fd)
    return RunLockResult(acquired=True, lock_path=target, value=value)


def _require_absolute(lock_path: str | Path) -> Path:
    """绝对路径闸（裁决 8）。MUST 先于任何文件系统副作用被调用。"""
    target = Path(lock_path)
    if not target.is_absolute():
        raise RunLockError(
            f"cron.lock_path 必须是绝对路径，实得 {str(lock_path)!r}："
            "相对路径与 `~` 前缀会随工作目录落到不同的锁文件上，使 run 的互斥静默失效"
        )
    return target
