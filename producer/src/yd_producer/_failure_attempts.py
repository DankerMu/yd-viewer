"""失败日志尝试计数（issue #370；compute-loop §11.3）。

`logs/<source>/<T>.log` 是同一 cycle 各次失败尝试的追加记录。本模块只读：
no-follow、有界地读出整份日志（`cleanup` 追加写复用同一 reader），并按字节切行
统计 header 行——单独一行、`json.loads` 得到 dict 且 `schema == FAILURE_LOG_SCHEMA`。
无法解码或解析的行不是 header，不令文件不可判定。文件缺席计 0；symlink、非普通
文件、超界或读失败一律抛 `FailureLogUndeterminable`，调用方 MUST NOT fail-open。
"""

from __future__ import annotations

import json
import stat
from datetime import datetime
from pathlib import Path

from yd_producer.controller import cycle_id
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    read_bytes_limited_no_follow,
    stat_no_follow,
)

#: 同一 `<source>/<T>` 累计失败达到此数即停源待人工（版本化常量，不入 config）。
FAILURE_ATTEMPT_LIMIT = 3
#: 失败日志读取的字节上界；超出即不可判定（计数侧停源，追加侧提交失败保留 work）。
FAILURE_LOG_MAX_BYTES = 64 * 1024 * 1024
#: 每条尝试记录 header 行的 schema 标识；`cleanup` 写 header 时引用同一常量。
FAILURE_LOG_SCHEMA = "yd-failure-log-v1"


class FailureLogUndeterminable(SafeFilesystemError):
    """失败日志存在但无法安全读出；`reason` 是写进停源 detail 的原因文本。

    继承 `SafeFilesystemError`：`cleanup` 既有的日志提交错误映射原样覆盖它。
    """

    def __init__(self, reason: str, *, path: Path) -> None:
        super().__init__(reason)
        self.reason = reason
        self.path = path


def failure_log_path(yd_root: Path, source: str, cycle: datetime) -> Path:
    return Path(yd_root) / "logs" / source / f"{cycle_id(cycle)}.log"


def read_failure_log(yd_root: Path, log_path: Path) -> bytes | None:
    """no-follow、有界读出整份失败日志；缺席返回 `None`。"""

    try:
        info = stat_no_follow(log_path, containment_root=yd_root)
    except FileNotFoundError:
        return None
    except (SafeFilesystemError, OSError) as error:
        raise _unreadable(log_path, error) from error
    if not stat.S_ISREG(info.st_mode):
        raise FailureLogUndeterminable(
            f"{log_path} 不是普通文件（st_mode={info.st_mode:#o}）", path=log_path
        )
    if info.st_size > FAILURE_LOG_MAX_BYTES:
        raise _over_bound(log_path)
    try:
        content = read_bytes_limited_no_follow(
            log_path, max_bytes=FAILURE_LOG_MAX_BYTES, containment_root=yd_root
        )
    except FileNotFoundError:
        return None
    except (SafeFilesystemError, OSError) as error:
        raise _unreadable(log_path, error) from error
    if len(content) > FAILURE_LOG_MAX_BYTES:
        raise _over_bound(log_path)
    return content


def count_headers(content: bytes) -> int:
    """按字节 `b"\\n"` 切行，只对可能是 JSON 对象的候选行做解码与解析。"""

    count = 0
    for line in content.split(b"\n"):
        if not line.lstrip(b" \t\r").startswith(b"{"):
            continue
        try:
            payload = json.loads(line.decode("utf-8"))
        except (ValueError, RecursionError):
            continue
        if isinstance(payload, dict) and payload.get("schema") == FAILURE_LOG_SCHEMA:
            count += 1
    return count


def count_failure_attempts(yd_root: Path, source: str, cycle: datetime) -> int:
    """`logs/<source>/<T>.log` 内的尝试记录数；不可判定时抛 `FailureLogUndeterminable`。"""

    content = read_failure_log(yd_root, failure_log_path(yd_root, source, cycle))
    return 0 if content is None else count_headers(content)


def _unreadable(log_path: Path, error: BaseException) -> FailureLogUndeterminable:
    return FailureLogUndeterminable(
        f"{log_path} 无法安全读取（{error}）", path=log_path
    )


def _over_bound(log_path: Path) -> FailureLogUndeterminable:
    return FailureLogUndeterminable(
        f"{log_path} 超出字节上界 {FAILURE_LOG_MAX_BYTES}", path=log_path
    )
