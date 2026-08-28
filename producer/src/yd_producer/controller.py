"""严格前沿：由 `DONE` 与状态文件集合判定每源的待跑 cycle 或停止原因（任务 12.1）。

契约来源：`docs/compute-loop-design.md` §8/§10、`docs/products-contract.md` §2–§4、
`openspec/changes/m2-producer-core/specs/run-controller/spec.md` 的「严格前沿确定待跑
cycle」与「raw 缺口阻塞不跳轮」两条 Requirement。

**本模块零写入**：只 `stat` / 列目录 / 有界读状态文件首行，MUST NOT 创建、修改或删除
任何路径。残留清理归任务 12.2（issue #23），发布与 `DONE` 写入归任务 13.1（issue #24）。

判定顺序固定（compute-loop §10 逐条），一步不得提前：

1. 该源的 `DONE` 集合（`output/<cycle_id>/<source>/DONE`，MUST 是普通文件）定 D；
   无 `DONE` 时待跑 T 取 `states/<source>/` 里**最早**的合法状态文件名（全新链）；
   两者皆空即 `NO_INITIAL_STATE`。
2. 有 `DONE` 时 T 固定为 `max(D) + 12h`。前沿**只由 `DONE` 推进**：状态目录里存在比 T
   更晚的文件（上次发布中断的残留）MUST NOT 让 T 前进。
3. `states/<source>/<T>.cfg.ic` 必须存在、可读、且 header 的分钟时标对应**绝对** T。
4. 最后才问 raw 是否齐（注入的 `raw_complete`）。T 的 raw 未齐即停在 T，
   MUST NOT 跳到下一个 raw 齐的 cycle。

**绝对时间判据**（不是相对分钟）：header shape 有效 → 取最后一个数值 token →
先过 `math.isfinite` 闸（`float("nan")`/`inf` 会被 `_as_float` 接受，随后 `round()` 抛
`ValueError`/`OverflowError`）→ `round(minute) == round(T.timestamp() / 60)` 才算对应 T。
四舍五入到整分钟是因为 minute token 是浮点文本（写入侧为 `valid_time.timestamp()/60`，
形如 `27000000.000000`）；cycle 间距 12h，±30s 容差不产生歧义。
**刻意不采用** NWM pin 的 `_valid_time_from_header_minute`（`packages/common/state_cli.py:359`）
那种「`0 <= m <= horizon` 时按相对分钟解释」的宽容读法：一份未重戳的残留 header
（`720.000000`）会在 T=cycle+12h 上被判为「对应 T」而放行，正是断链的入口。

**cycle 可见集**（`states/` 与 `output/` 对称）：条目名为 10 位 ASCII 数字**且**可被
`%Y%m%d%H` 解析（`2026023100`、`9999999999` 是 10 位数字却非法）**且**解析出的 cycle 满足
`cycle + 12h` 仍在 `datetime` 值域内（`9999123123` 三门过前两门、第三门溢出），`states/`
侧另需 `.cfg.ic` 后缀。不满足者对前沿**不可见**，既不报错也不停源——发布的临时文件在
`states/<source>/` 目录内 rename，若对不可解析文件名 fail-closed，一次崩溃的发布会把该源
永久砖化。残留的**清理**归 issue #23。

**「不存在」与「不可确定」严格分流**（裁决 9）：文件系统探测里**只有**
`FileNotFoundError` / `NotADirectoryError` 等价于「空集合 / 该条目不算数」；其余任何
`OSError`（`EACCES`/`EPERM`/`EIO`/`ESTALE`/`ELOOP`…）都是「无法确定」，一律停该源并返回
`DISCOVERY_UNREADABLE`。因此本模块 MUST NOT 使用裸 `Path.exists()` / `Path.is_file()` /
`Path.is_symlink()` 去判定需要被分类的路径：`pathlib` 只吞 `ENOENT/ENOTDIR/EBADF/ELOOP/
EINVAL`，`EACCES`/`EIO` 会穿透。方向性理由：`states/` 侧判空是 fail-closed
（`NO_INITIAL_STATE`），`output/` 侧判空却会让链看起来是**全新链**，把前沿**倒退**到已发布
的 cycle——发布侧的「见 `DONE` 不覆盖」守卫归 #24 尚未落地，本函数当前是唯一闸门。
集合无法枚举 / 条目无法判定归 `DISCOVERY_UNREADABLE`；状态文件**自身**读不出来仍归
`STATE_UNREADABLE`。

**状态文件可读性跟随 symlink**（与 `state/cfg_ic.py` 的有界读同一理由：macOS `/tmp` 本身
是 symlink，no-follow 会误拒合法测试树）。no-follow 的越界拒绝属删除/发布面，归 #24/#25。

停止一律经返回值里的 `StopReason` 表达，MUST NOT 以异常逃逸：`OSError`、
`UnicodeDecodeError`、`ValueError` 与文件名派生值引发的 `OverflowError` 全部被吞成分类
结果。唯一的例外是注入的 `raw_complete` 自己抛出的异常（见 `decide_frontier` 的
docstring：那是调用方的输入域责任，归 #26）。

本模块 stdlib-only：零 NWM 运行时 import、零数据库/scheduler 依赖。
"""

from __future__ import annotations

import enum
import math
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from yd_producer.state.cfg_ic import MAX_STATE_IC_BYTES
from yd_producer.state.header_time import (
    cfg_ic_header_minute_time,
    cfg_ic_header_shape,
)

__all__ = [
    "CYCLE_ID_FORMAT",
    "CYCLE_STRIDE",
    "MAX_HEADER_LINE_BYTES",
    "STATE_SUFFIX",
    "FrontierDecision",
    "StopReason",
    "decide_frontier",
]

#: `cycle_id` 的唯一形态（products-contract §3.1）：10 位 UTC `YYYYMMDDHH`。
CYCLE_ID_FORMAT = "%Y%m%d%H"
#: 状态文件的固定后缀（compute-loop §8）。
STATE_SUFFIX = ".cfg.ic"
#: 前沿步长：最新 `DONE` 的 cycle D 之后固定跑 D+12h（compute-loop §10.2）。
CYCLE_STRIDE = timedelta(hours=12)
#: 首行有界读的分块大小：只影响峰值内存，不影响判定。
_READ_CHUNK_BYTES = 64 * 1024
#: 候选 header 行的字节上界（裁决 4 二次增补）：原生 header 只有 3–4 个 token，
#: 64 KiB 已留出两个数量级余量。累计到这个长度仍未遇到 `\n` 的行一律 `STATE_UNREADABLE`。
MAX_HEADER_LINE_BYTES = 64 * 1024


class StopReason(enum.Enum):
    """本次不提交该源的原因。闭合词表（6 项），逐项可区分。"""

    #: 该源既无任何 `DONE`，也没有任何合法命名的状态文件（链尚未由 init 建立）。
    NO_INITIAL_STATE = "no_initial_state"
    #: 集合**无法枚举**或条目**无法判定**：目录列不出、`DONE` 或状态路径的元数据探测
    #: 遇到 `ENOENT`/`ENOTDIR` 之外的 `OSError`。与「路径不存在」严格分流：不存在是空
    #: 集合，不可确定一律停源，MUST NOT fail-open 成「全新链」让前沿倒退。
    DISCOVERY_UNREADABLE = "discovery_unreadable"
    #: 待跑 T 的状态文件不存在（MUST NOT 回退到更旧状态或互借另一源）。
    STATE_MISSING = "state_missing"
    #: 状态文件**自身**存在但读不出来：非普通文件、断链 symlink、`open`/`read` 被权限
    #: 拒绝、非 UTF-8、超字节上界，或候选 header 行超 `MAX_HEADER_LINE_BYTES`。
    #: 元数据探测层面的不可确定归 `DISCOVERY_UNREADABLE`。
    STATE_UNREADABLE = "state_unreadable"
    #: header 形状非法、分钟时标非有限值，或其绝对时间不对应 T。
    HEADER_TIME_MISMATCH = "header_time_mismatch"
    #: T 的 raw 未齐；停在 T 等待补齐，MUST NOT 跳轮。
    RAW_INCOMPLETE = "raw_incomplete"


@dataclass(frozen=True)
class FrontierDecision:
    """单源的前沿结论。`cycle` 与 `stop_reason` **恰有一个**非 `None`。"""

    source: str
    cycle: datetime | None
    stop_reason: StopReason | None
    detail: str

    def __post_init__(self) -> None:
        if (self.cycle is None) == (self.stop_reason is None):
            raise ValueError(
                "FrontierDecision 必须恰有一个非 None 的 cycle / stop_reason，"
                f"实得 cycle={self.cycle!r} stop_reason={self.stop_reason!r}"
            )

    @property
    def runnable(self) -> bool:
        return self.cycle is not None


def decide_frontier(
    *,
    yd_root: Path,
    source: str,
    raw_complete: Callable[[datetime], bool],
) -> FrontierDecision:
    """判定 `source` 本次的待跑 cycle，或给出停止原因。

    `raw_complete` 是「给定 cycle 的 raw 是否完整」的注入判定（生产接线到
    `rawscan.judge`，归 issue #26）。它**只在状态三判全部通过后**被调用一次：状态判据
    先于 raw（compute-loop §10 的顺序），状态已经不可用时不该去扫 raw。

    注入契约（前置条件，裁决 10）：本函数返回的 T、以及传给 `raw_complete` 的 cycle，
    可能带**任意可解析的 cycle 小时**（不限 00/12——裁决 5 刻意如此：对不可解析的文件名
    fail-closed 会让一次崩溃的发布把该源永久砖化）。因此 `raw_complete` MUST 对该输入域
    **全域**有定义；否则调用方 MUST 自己把 `ConfigError` 收敛为该源的停止原因。本函数
    不守卫这一条：`raw_complete` 抛出的异常会原样穿透（接线与守卫归 issue #26 组 14）。
    """
    try:
        return _decide(yd_root=yd_root, source=source, raw_complete=raw_complete)
    except _DiscoveryUnreadable as error:
        return FrontierDecision(
            source=source,
            cycle=None,
            stop_reason=StopReason.DISCOVERY_UNREADABLE,
            detail=f"{source}: {error.detail}",
        )


class _DiscoveryUnreadable(Exception):
    """内部信号：某处文件系统探测**无法确定**。只允许在 `decide_frontier` 内被捕获。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _decide(
    *,
    yd_root: Path,
    source: str,
    raw_complete: Callable[[datetime], bool],
) -> FrontierDecision:
    """`decide_frontier` 的判定体；探测层的「无法确定」以 `_DiscoveryUnreadable` 上抛。"""
    yd_root = Path(yd_root)
    states_dir = yd_root / "states" / source
    done_cycles = _done_cycles(yd_root / "output", source)

    if done_cycles:
        latest_done = max(done_cycles)
        target = latest_done + CYCLE_STRIDE
        origin = f"最新 DONE cycle {_cycle_id(latest_done)} + 12h"
    else:
        state_cycles = _visible_state_cycles(states_dir)
        if not state_cycles:
            return FrontierDecision(
                source=source,
                cycle=None,
                stop_reason=StopReason.NO_INITIAL_STATE,
                detail=(
                    f"{source}: 无任何 DONE，且 {states_dir} 下没有合法命名的状态文件"
                ),
            )
        target = min(state_cycles)
        origin = f"全新链最早状态文件名 {_cycle_id(target)}"

    state_path = states_dir / f"{_cycle_id(target)}{STATE_SUFFIX}"
    stop_reason, note = _classify_state(state_path, target)
    if stop_reason is not None:
        return FrontierDecision(
            source=source,
            cycle=None,
            stop_reason=stop_reason,
            detail=f"{source}: 待跑 T={_cycle_id(target)}（{origin}）；{note}",
        )

    if not raw_complete(target):
        return FrontierDecision(
            source=source,
            cycle=None,
            stop_reason=StopReason.RAW_INCOMPLETE,
            detail=(
                f"{source}: 待跑 T={_cycle_id(target)}（{origin}）；"
                "该 cycle 的 raw 未齐，停在缺口等待，不跳轮"
            ),
        )

    return FrontierDecision(
        source=source,
        cycle=target,
        stop_reason=None,
        detail=(
            f"{source}: 待跑 T={_cycle_id(target)}（{origin}）；"
            f"状态 {state_path} 时间头对应绝对 T，raw 完整"
        ),
    )


# --- cycle 可见集 ---


def _cycle_id(cycle: datetime) -> str:
    return cycle.strftime(CYCLE_ID_FORMAT)


def _parse_cycle_id(name: str) -> datetime | None:
    """10 位 ASCII 数字、`%Y%m%d%H` 可解析、且 `+12h` 可表示时返回 UTC aware 时刻。

    三道门缺一不可：`str.isdigit()` 会接受 Unicode 数字；`2026023100`（2 月 31 日）与
    `9999999999`（99 月）都是 10 位 ASCII 数字却不是合法 cycle；`9999123123` 连
    `%Y%m%d%H` 都能过，但 `datetime(9999,12,31,23) + 12h` 抛 `OverflowError`——前沿的
    每一次推进都要做这个加法，所以「不可表示」在**可见性**这一层就判掉（裁决 5 增补），
    而不是新增第七个停止原因：这类条目在语义上本来就不是合法 cycle。
    解析/溢出失败一律是「不可见」，MUST NOT 让 `ValueError`/`OverflowError` 逃逸。
    """
    if len(name) != 10 or not all(char in "0123456789" for char in name):
        return None
    try:
        cycle = datetime.strptime(name, CYCLE_ID_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
    try:
        cycle + CYCLE_STRIDE
    except OverflowError:
        return None
    return cycle


def _iter_entry_names(directory: Path) -> list[str]:
    """列目录。目录**不存在**才视为空集合；列不出来一律 `_DiscoveryUnreadable`。"""
    try:
        return [entry.name for entry in directory.iterdir()]
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as error:
        raise _DiscoveryUnreadable(f"目录 {directory} 无法枚举（{error}）") from error


def _done_cycles(output_root: Path, source: str) -> set[datetime]:
    """该源已完成的 cycle 集合。

    `DONE` MUST 是**普通文件**（products-contract §4.1）：目录、断链 symlink、FIFO 都不
    算完成。判定用 `os.stat`（跟随 symlink）而非 `Path.is_file()`：后者把 `EACCES`/`EIO`
    直接向外抛，而在旧实现里被 `except OSError: continue` 吞掉时会让该 cycle **静默掉出**
    DONE 集合，前沿倒退回更旧的已发布 cycle。不存在/断链（`ENOENT`）才是「不算完成」。
    逐源独立：`output/<cycle>/gfs/DONE` 不为 `ifs` 计数。
    """
    cycles: set[datetime] = set()
    for name in _iter_entry_names(output_root):
        cycle = _parse_cycle_id(name)
        if cycle is None:
            continue
        done_path = output_root / name / source / "DONE"
        try:
            mode = os.stat(done_path).st_mode
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as error:
            raise _DiscoveryUnreadable(
                f"DONE 判定失败：{done_path} 无法确定（{error}）"
            ) from error
        if stat.S_ISREG(mode):
            cycles.add(cycle)
    return cycles


def _visible_state_cycles(states_dir: Path) -> set[datetime]:
    """`states/<source>/` 下**文件名可见**的 cycle 集合（形态门，不判内容）。"""
    cycles: set[datetime] = set()
    for name in _iter_entry_names(states_dir):
        if not name.endswith(STATE_SUFFIX):
            continue
        cycle = _parse_cycle_id(name[: -len(STATE_SUFFIX)])
        if cycle is None:
            continue
        cycles.add(cycle)
    return cycles


# --- 状态文件三判：存在 / 可读 / 时间头对应绝对 T ---


def _classify_absent_state(state_path: Path) -> tuple[StopReason, str]:
    """`os.stat` 报「不存在」后再分：断链 symlink 是不可读，真缺失是 `STATE_MISSING`。"""
    try:
        link_mode = os.lstat(state_path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return (StopReason.STATE_MISSING, f"状态 {state_path} 不存在")
    except OSError as error:
        raise _DiscoveryUnreadable(
            f"状态 {state_path} 的存在性无法判定（{error}）"
        ) from error
    if stat.S_ISLNK(link_mode):
        return (StopReason.STATE_UNREADABLE, f"状态 {state_path} 是断链 symlink")
    return (StopReason.STATE_MISSING, f"状态 {state_path} 不存在")


def _classify_state(
    state_path: Path, target: datetime
) -> tuple[StopReason | None, str]:
    """返回 `(停止原因, 说明)`；`(None, 说明)` 表示该状态可用于起跑。

    存在性探测用 `os.stat`（跟随 symlink，裁决 4）与 `os.lstat`，**不用** 裸
    `Path.exists()`/`Path.is_symlink()`：后者只吞 `ENOENT/ENOTDIR/EBADF/ELOOP/EINVAL`，
    父目录 `chmod 0o000` 时 `PermissionError` 会直接逃出 `decide_frontier`。元数据探测的
    「无法确定」以 `_DiscoveryUnreadable` 上抛（裁决 9）；文件自身读不出来才是
    `STATE_UNREADABLE`。
    """
    try:
        info = os.stat(state_path)
    except (FileNotFoundError, NotADirectoryError):
        return _classify_absent_state(state_path)
    except OSError as error:
        raise _DiscoveryUnreadable(
            f"状态 {state_path} 的存在性无法判定（{error}）"
        ) from error
    if not stat.S_ISREG(info.st_mode):
        return (
            StopReason.STATE_UNREADABLE,
            f"状态 {state_path} 不是普通文件（st_mode={info.st_mode:#o}）",
        )

    header_line = _read_header_line(state_path, size=info.st_size)
    if isinstance(header_line, StopReason):
        return (header_line, f"状态 {state_path} 不可读或超界")
    if header_line is None:
        return (
            StopReason.HEADER_TIME_MISMATCH,
            f"状态 {state_path} 没有可用的 header 行（文件为空或只有空白行）",
        )

    tokens = header_line.split()
    shape = cfg_ic_header_shape(tokens)
    if not shape.valid:
        return (
            StopReason.HEADER_TIME_MISMATCH,
            f"状态 {state_path} 的 header 形状非法：{shape.reason}",
        )

    observed = cfg_ic_header_minute_time(tokens)
    if observed is None or not math.isfinite(observed):
        return (
            StopReason.HEADER_TIME_MISMATCH,
            f"状态 {state_path} 的 header 分钟时标非有限值：{observed!r}",
        )

    expected_minute = round(target.timestamp() / 60)
    if round(observed) != expected_minute:
        return (
            StopReason.HEADER_TIME_MISMATCH,
            (
                f"状态 {state_path} 的 header 分钟时标 {observed!r} 不对应绝对 T="
                f"{_cycle_id(target)}（期望 {expected_minute} 分钟，"
                f"实得 {round(observed)}）；相对分钟一律不接受"
            ),
        )
    return (None, f"状态 {state_path} 的 header 分钟时标对应绝对 T")


def _read_header_line(state_path: Path, *, size: int) -> str | StopReason | None:
    """有界读出首个非空行；不可读/超界/非 UTF-8 返回 `StopReason.STATE_UNREADABLE`。

    超界由**调用方已经拿到的** `st_size` 判定（`> MAX_STATE_IC_BYTES` 即超界），随后按
    `_READ_CHUNK_BYTES` 分块读到首个非空行为止，累计读入 MUST NOT 超过
    `MAX_STATE_IC_BYTES + 1` 字节，且**只保留当前候选行**——上界是 64 MiB，若像旧实现那样
    `read(MAX+1)` 再 `decode()` 再 `splitlines()`，一份 16 MiB 的合法状态文件峰值会放大
    到十倍量级（round 1 验证闸门 batch resource-limits cand-04 实测），正好架空
    `MAX_STATE_IC_BYTES` 自述的 OOM 保护意图。

    **候选行本身也有界**（裁决 4 二次增补，round 2 batch resource-and-coverage-2
    cand-12）：字节预算只约束「读了多少」，不约束「首行有多长」。首个 `MAX+1` 字节里没有
    `\n` 时（64 MiB 无换行文本，或截断/预分配出来的**全 NUL** 文件——NUL 是合法 UTF-8 且
    不是 `str.strip()` 的空白，整个文件成为一个巨大的 header 行），`pending`、
    `bytes(pending)`、`decode()` 与调用方的 `.split()` 各自实体化一份文件大小的对象，实测
    端到端 traced peak 576 MiB / `ru_maxrss` 681 MiB。故候选行累计超过
    `MAX_HEADER_LINE_BYTES` 仍未遇到 `\n` 时**立即**判 `STATE_UNREADABLE` 并停止读取。
    判定在**两处**：行尾在后续 chunk 里才出现的超长行同样被拒（否则 (cap, cap+chunk] 这段
    长度会从「读满才判」的那道闸里漏过去）。跳过前导空行时已丢弃的空白**不计入**候选行
    长度——`pending` 里只留当前这一行。副作用是单独一条**超过上界的空白行**（如 1 MB 空格）
    也按超界拒绝而非跳过：fail-closed 方向一致，且真实写入侧不产出这种行。
    这条**改变了可观测行为**：全 NUL 的 64 MiB 状态由 `HEADER_TIME_MISMATCH` 变为
    `STATE_UNREADABLE`——两者都停源，方向不变。

    行切分只认 `\\n`（不是 `str.splitlines()` 的全套行分隔符）：只读首行的语义下，本仓
    写入侧只产出 `\\n`，而 `splitlines()` 的 `\\r`/`\\x0b`/`U+2028` 需要先解码整个缓冲区
    才能定位，与有界读互斥。

    这里**只取 header 行**：缺段、行数不符、数值区损坏等结构检查是任务 4.2 / issue #9
    的面，前沿不做全量解析。
    """
    if size > MAX_STATE_IC_BYTES:
        return StopReason.STATE_UNREADABLE
    budget = MAX_STATE_IC_BYTES + 1
    pending = bytearray()
    try:
        with open(state_path, "rb") as handle:
            while budget > 0:
                chunk = handle.read(min(_READ_CHUNK_BYTES, budget))
                if not chunk:
                    break
                budget -= len(chunk)
                pending += chunk
                while True:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        break
                    if newline > MAX_HEADER_LINE_BYTES:
                        return StopReason.STATE_UNREADABLE
                    line = _decode_line(bytes(pending[:newline]))
                    del pending[: newline + 1]
                    if line is None:
                        return StopReason.STATE_UNREADABLE
                    if line.strip():
                        return line
                if len(pending) > MAX_HEADER_LINE_BYTES:
                    return StopReason.STATE_UNREADABLE
    except OSError:
        return StopReason.STATE_UNREADABLE
    if budget <= 0 and pending:
        # 读满上界仍未见换行：首行本身超界，与整文件超界同类。
        return StopReason.STATE_UNREADABLE
    last = _decode_line(bytes(pending))
    if last is None:
        return StopReason.STATE_UNREADABLE
    return last if last.strip() else None


def _decode_line(raw: bytes) -> str | None:
    """UTF-8 解码一行；非 UTF-8 返回 `None`（调用方判 `STATE_UNREADABLE`）。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
