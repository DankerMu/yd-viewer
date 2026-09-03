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
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from yd_producer.executor import JobRecord, JobSpec, JobState
from yd_producer.state.cfg_ic import MAX_STATE_IC_BYTES
from yd_producer.state.header_time import (
    cfg_ic_header_minute_time,
    cfg_ic_header_shape,
)

if TYPE_CHECKING:
    from yd_producer.assemble import RunDirectory, WorkIdentity
    from yd_producer.config import Config, LocalConfig
    from yd_producer.executor import JobExecutor, JobRecord
    from yd_producer.publish import PublishResult
    from yd_producer.tracker import CheckpointTracker

__all__ = [
    "CYCLE_ID_FORMAT",
    "CYCLE_STRIDE",
    "MAX_HEADER_LINE_BYTES",
    "STATE_SUFFIX",
    "AttemptDriver",
    "AttemptProducts",
    "AttemptRequest",
    "DiscoveryUnreadableError",
    "FrontierDecision",
    "JobRunReport",
    "PreparedAttempt",
    "RunError",
    "RunOutcome",
    "RunReport",
    "StopReason",
    "cycle_id",
    "decide_frontier",
    "done_cycles",
    "parse_cycle_id",
    "run_once",
    "visible_state_cycles",
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
    except DiscoveryUnreadableError as error:
        return FrontierDecision(
            source=source,
            cycle=None,
            stop_reason=StopReason.DISCOVERY_UNREADABLE,
            detail=f"{source}: {error.detail}",
        )


class DiscoveryUnreadableError(Exception):
    """探测信号：某处文件系统探测**无法确定**（`ENOENT`/`ENOTDIR` 之外的 `OSError`）。

    原名 `_DiscoveryUnreadable`，随 `done_cycles` / `visible_state_cycles` /
    `parse_cycle_id` / `cycle_id` 一并**提升为公开符号**（issue #23 裁决 5：残留清理
    MUST 复用本模块的 cycle 可见集与 `DONE` 判据而不是重写一份三道门，那两份判据一旦
    分叉，「更晚」的定义就会与前沿的定义不一致）。捕获点因此从「只允许在
    `decide_frontier` 内」扩为「`decide_frontier` 与 `residue.plan_residue`」——两处都
    把它收敛成本源的停止语义，MUST NOT 被吞成「空集合」。行为逐字未变。
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _target_and_state(
    yd_root: Path, source: str
) -> tuple[datetime, str, Path] | FrontierDecision:
    """`decide_frontier` 的前半段：DONE/状态集合得到 T 并三判状态，**不含 raw 判定**。

    返回 `(target, origin, state_path)` 表示该源可跑（状态三判全过）；返回
    `FrontierDecision`（`cycle=None`）表示停止。`decide_frontier` 以本函数 + 注入的
    `raw_complete` 组合（对外行为逐字保持）；`run_once` 复用前半段，以便在接入
    `rawscan.judge` 之前独立处理任意可解析小时（越域小时不触发 residue/raw/work）。
    """
    yd_root = Path(yd_root)
    states_dir = yd_root / "states" / source
    completed = done_cycles(yd_root / "output", source)

    if completed:
        latest_done = max(completed)
        target = latest_done + CYCLE_STRIDE
        origin = f"最新 DONE cycle {cycle_id(latest_done)} + 12h"
    else:
        state_cycles = visible_state_cycles(states_dir)
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
        origin = f"全新链最早状态文件名 {cycle_id(target)}"

    state_path = states_dir / f"{cycle_id(target)}{STATE_SUFFIX}"
    stop_reason, note = _classify_state(state_path, target)
    if stop_reason is not None:
        return FrontierDecision(
            source=source,
            cycle=None,
            stop_reason=stop_reason,
            detail=f"{source}: 待跑 T={cycle_id(target)}（{origin}）；{note}",
        )
    return (target, origin, state_path)


def _decide(
    *,
    yd_root: Path,
    source: str,
    raw_complete: Callable[[datetime], bool],
) -> FrontierDecision:
    """`decide_frontier` 的判定体；探测层的「无法确定」以 `DiscoveryUnreadableError` 上抛。"""
    gathered = _target_and_state(Path(yd_root), source)
    if isinstance(gathered, FrontierDecision):
        return gathered
    target, origin, state_path = gathered

    if not raw_complete(target):
        return FrontierDecision(
            source=source,
            cycle=None,
            stop_reason=StopReason.RAW_INCOMPLETE,
            detail=(
                f"{source}: 待跑 T={cycle_id(target)}（{origin}）；"
                "该 cycle 的 raw 未齐，停在缺口等待，不跳轮"
            ),
        )

    return FrontierDecision(
        source=source,
        cycle=target,
        stop_reason=None,
        detail=(
            f"{source}: 待跑 T={cycle_id(target)}（{origin}）；"
            f"状态 {state_path} 时间头对应绝对 T，raw 完整"
        ),
    )


# --- cycle 可见集 ---


def cycle_id(cycle: datetime) -> str:
    return cycle.strftime(CYCLE_ID_FORMAT)


def parse_cycle_id(name: str) -> datetime | None:
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
    """列目录。目录**不存在**才视为空集合；列不出来一律 `DiscoveryUnreadableError`。"""
    try:
        return [entry.name for entry in directory.iterdir()]
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as error:
        raise DiscoveryUnreadableError(
            f"目录 {directory} 无法枚举（{error}）"
        ) from error


def done_cycles(output_root: Path, source: str) -> set[datetime]:
    """该源已完成的 cycle 集合。

    `DONE` MUST 是**普通文件**（products-contract §4.1）：目录、断链 symlink、FIFO 都不
    算完成。判定用 `os.stat`（跟随 symlink）而非 `Path.is_file()`：后者把 `EACCES`/`EIO`
    直接向外抛，而在旧实现里被 `except OSError: continue` 吞掉时会让该 cycle **静默掉出**
    DONE 集合，前沿倒退回更旧的已发布 cycle。不存在/断链（`ENOENT`）才是「不算完成」。
    逐源独立：`output/<cycle>/gfs/DONE` 不为 `ifs` 计数。
    """
    cycles: set[datetime] = set()
    for name in _iter_entry_names(output_root):
        cycle = parse_cycle_id(name)
        if cycle is None:
            continue
        done_path = output_root / name / source / "DONE"
        try:
            mode = os.stat(done_path).st_mode
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as error:
            raise DiscoveryUnreadableError(
                f"DONE 判定失败：{done_path} 无法确定（{error}）"
            ) from error
        if stat.S_ISREG(mode):
            cycles.add(cycle)
    return cycles


def visible_state_cycles(states_dir: Path) -> set[datetime]:
    """`states/<source>/` 下**文件名可见**的 cycle 集合（形态门，不判内容）。"""
    cycles: set[datetime] = set()
    for name in _iter_entry_names(states_dir):
        if not name.endswith(STATE_SUFFIX):
            continue
        cycle = parse_cycle_id(name[: -len(STATE_SUFFIX)])
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
        raise DiscoveryUnreadableError(
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
    「无法确定」以 `DiscoveryUnreadableError` 上抛（裁决 9）；文件自身读不出来才是
    `STATE_UNREADABLE`。
    """
    try:
        info = os.stat(state_path)
    except (FileNotFoundError, NotADirectoryError):
        return _classify_absent_state(state_path)
    except OSError as error:
        raise DiscoveryUnreadableError(
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
                f"{cycle_id(target)}（期望 {expected_minute} 分钟，"
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


# --- 单源单轮 run_once（issue #26 / 任务 14.1）------------------------------------
#
# 公共类型与入口只从本模块导出（tasks.md「测试布局」：私有支撑在
# `yd_producer._controller_run`，无 `__all__` 公共面）。实现与逐条 ownership 的
# 对应注释都在该私有模块，类型定义留在本公开 seam（测试从
# `yd_producer.controller` 导入）。

RunPhase = Literal[
    "preflight",
    "frontier",
    "residue",
    "raw",
    "prepare",
    "submit",
    "poll",
    "collect",
    "publish",
]

_RUN_PHASES: tuple[str, ...] = (
    "preflight",
    "frontier",
    "residue",
    "raw",
    "prepare",
    "submit",
    "poll",
    "collect",
    "publish",
)


class RunError(RuntimeError):
    """run_once 的阶段类型化失败（tasks.md「公开面」逐字冻结）。

    `phase` 取自闭合词表；`source`/`cycle`/`job_id` 是涉事的身份（尚未确定时为
    `None`）；预期的底层异常以 `__cause__` 保留，普通异常不裸逃，`BaseException`
    不包。
    """

    def __init__(
        self,
        message: str,
        *,
        phase: RunPhase,
        source: str,
        cycle: datetime | None = None,
        job_id: str | None = None,
    ) -> None:
        if phase not in _RUN_PHASES:
            raise ValueError(f"RunError.phase 取值非法：{phase!r}")
        super().__init__(message)
        self.phase = phase
        self.source = source
        self.cycle = cycle
        self.job_id = job_id


class RunOutcome(StrEnum):
    """一次 run_once 的结局。恰四项（tasks.md「公开面」逐字）。"""

    STOPPED = "STOPPED"
    SUCCEEDED = "SUCCEEDED"
    SUCCEEDED_CLEANUP_PENDING = "SUCCEEDED_CLEANUP_PENDING"
    JOB_FAILED = "JOB_FAILED"


@dataclass(frozen=True, kw_only=True)
class AttemptRequest:
    """driver.prepare 接收的一次 attempt 的全部显式输入（逐字冻结）。

    全部字段无默认值；路径与身份由 controller 决定，driver 无权改派。
    """

    source: str
    cycle: datetime
    work_root: Path
    work_dir: Path
    object_store_root: Path
    raw_manifest_path: Path
    variant_dir: Path
    state_path: Path
    shud_binary: str
    checkpoint_hours: tuple[int, ...]
    forecast_days: int
    output_interval_minutes: int
    reach_count: int


@dataclass(frozen=True, kw_only=True)
class PreparedAttempt:
    """driver.prepare 的产物：只含显式 identity、worker 命令与 scratch DAT 终名。

    JobSpec 的 name/work/log/resources 由 controller 独占构造，driver 无权选择。
    """

    identity: WorkIdentity
    command: tuple[str, ...]
    scratch_dat: Path


@dataclass(frozen=True, kw_only=True)
class AttemptProducts:
    """driver.collect 在 SUCCEEDED 后交出的产物。

    只返回**已经存在**的对象/路径，不创建文件（创建归 fake 的 terminal hook / M4
    worker）。
    """

    job_id: str
    run_directory: RunDirectory
    tracker: CheckpointTracker
    scratch_dat: Path
    merged_log: Path


@runtime_checkable
class AttemptDriver(Protocol):
    """注入式计算节点边界（tasks.md「公开面」逐字）。

    `prepare` 只交身份/命令/DAT 打戳；`collect` 只交同一 job 已存在的产物。
    """

    def prepare(self, *, request: AttemptRequest) -> PreparedAttempt: ...

    def collect(
        self, *, attempt: PreparedAttempt, terminal_record: JobRecord
    ) -> AttemptProducts: ...


@dataclass(frozen=True, kw_only=True)
class JobRunReport:
    """一次提交的 job 身份四元组 + 起止时间（逐字来自同一 submit/terminal record）。

    `partition` 必须是 nonblank `str`（值取自 submit record 的资源映射，MUST NOT 从
    新常量/driver 取）。
    """

    job_id: str
    partition: str
    state: JobState
    submitted_at: datetime
    started_at: datetime | None
    ended_at: datetime | None

    def __post_init__(self) -> None:
        # nonblank 判据只做 `strip()` 探针：空白串（含纯空白）拒绝，但值**原样保留**，
        # 绝不把环绕空格剥离/归一（现场 partition 值是 whitespace-sensitive）。
        if not isinstance(self.partition, str) or not self.partition.strip():
            raise ValueError(
                f"JobRunReport.partition 必须是 nonblank str，实得 {self.partition!r}"
            )
        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise ValueError(
                f"JobRunReport.job_id 必须是 nonblank str，实得 {self.job_id!r}"
            )
        if not isinstance(self.state, JobState):
            # #26 报告族的约定：结构性/语义性拒绝一律 ValueError（JobRunReport 的字段
            # 逐字冻结由 dataclass 参数把关，这里只做构造点校验）。
            raise ValueError(  # noqa: TRY004
                f"JobRunReport.state 必须是 JobState，实得 {type(self.state).__name__}"
            )


@dataclass(frozen=True, kw_only=True)
class RunReport:
    """一次 run_once 的结论（tasks.md「公开面」逐字）。

    STOPPED 恰有 stop_reason 且无 job；提交后的结果恰有 job；SUCCEEDED 恰有
    published 且 `done_path == published.done_path`；cleanup-pending 的 published 为
    None 但 done_path 逐字取异常的已落盘路径；其它 outcome 的 done_path 为 None。
    """

    source: str
    cycle: datetime | None
    outcome: RunOutcome
    stop_reason: StopReason | None
    detail: str
    job: JobRunReport | None
    published: PublishResult | None
    done_path: Path | None

    def __post_init__(self) -> None:
        # outcome 不是 `RunOutcome` 的构造点拒绝：若漏掉这条，一个外来字符串（如
        # `"JOB_FAILED"` 字面值）会一路落到下面的 JOB_FAILED 分支而静默通过。
        # （#26 报告族约定：结构性/语义性拒绝一律 ValueError，与 JobRunReport 同。）
        if not isinstance(self.outcome, RunOutcome):
            raise ValueError(  # noqa: TRY004
                f"RunReport.outcome 必须是 RunOutcome，实得 {self.outcome!r}"
            )
        if self.outcome is RunOutcome.STOPPED:
            if self.stop_reason is None or self.job is not None:
                raise ValueError("STOPPED 恰有 stop_reason 且无 job")
            if self.published is not None or self.done_path is not None:
                raise ValueError("STOPPED 不得携带 published / done_path")
            return
        if self.stop_reason is not None or self.job is None:
            raise ValueError("提交后的结果恰有 job 且无 stop_reason")
        if self.outcome is RunOutcome.SUCCEEDED:
            if self.published is None:
                raise ValueError("SUCCEEDED 必须携带 published")
            if self.done_path != self.published.done_path:
                raise ValueError("SUCCEEDED 的 done_path 必须等于 published.done_path")
            return
        if self.outcome is RunOutcome.SUCCEEDED_CLEANUP_PENDING:
            if self.published is not None or self.done_path is None:
                raise ValueError(
                    "SUCCEEDED_CLEANUP_PENDING 的 published 为 None 且 done_path 取已落盘路径"
                )
            return
        # JOB_FAILED
        if self.published is not None or self.done_path is not None:
            raise ValueError("JOB_FAILED 不得携带 published / done_path")


# --- run_once 私有校验面（issue #26 support 折叠；不导出，仅供 _controller_run）-----


def _preflight(*, config: Config, local: LocalConfig, source: str) -> None:
    """run_once 的 preflight（ownership 1）：零发现/写/删/driver/executor。"""
    if source not in ("ifs", "gfs"):
        raise RunError(
            f"source 取值非法：{source!r}，只接受 ifs、gfs",
            phase="preflight",
            source=source,
        )
    for label, value in (
        ("yd_root", local.yd_root),
        ("scratch_root", local.scratch_root),
        ("nwm.raw_root", local.nwm.raw_root),
        ("shud_binary", local.shud_binary),
    ):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise RunError(
                f"配置项 `{label}` 必须是绝对路径文本，实得 {value!r}",
                phase="preflight",
                source=source,
            )
    if config.forecast_days != 7:
        raise RunError(
            f"forecast_days 必须为 7，实得 {config.forecast_days!r}",
            phase="preflight",
            source=source,
        )
    if config.output_interval_minutes != 60:
        raise RunError(
            f"output_interval_minutes 必须为 60，实得 {config.output_interval_minutes!r}",
            phase="preflight",
            source=source,
        )
    if config.checkpoint_hours != (12,):
        raise RunError(
            f"checkpoint_hours 必须恰为 (12,)，实得 {config.checkpoint_hours!r}",
            phase="preflight",
            source=source,
        )
    if (
        not isinstance(config.reach_count, int)
        or isinstance(config.reach_count, bool)
        or config.reach_count <= 0
    ):
        raise RunError(
            f"reach_count 必须为正整数，实得 {config.reach_count!r}",
            phase="preflight",
            source=source,
        )
    required = set(config.slurm.required_fields)
    present = set(local.slurm)
    # required/local 两侧 partition 闸都先于键集相等复查：放在键集检查之后，任何到达者
    # 都已键集相等，缺 partition 必被另一侧兜住，OWNER 闸成为不可达死代码（变异测试实测）。
    if "partition" not in required:
        raise RunError(
            "slurm.required_fields 必须声明 partition（缺第 0 项）",
            phase="preflight",
            source=source,
        )
    if "partition" not in local.slurm:
        raise RunError(
            "local.toml 的 [slurm] 缺少 partition：在发现、残留清理、work 创建和提交"
            "之前报错退出，零作业提交、零文件系统变更",
            phase="preflight",
            source=source,
        )
    if required != present:
        missing = sorted(required - present)
        extra = sorted(present - required)
        raise RunError(
            "`[slurm]` 的键集必须与 config.toml 的 `slurm.required_fields` 完全一致"
            f"（缺 {'、'.join(missing) if missing else '无'}，"
            f"多 {'、'.join(extra) if extra else '无'}）",
            phase="preflight",
            source=source,
        )
    partition = local.slurm["partition"]
    # nonblank 判据只看 strip() 探针；值原样保留（现场 partition 值 whitespace-sensitive）。
    if not isinstance(partition, str) or not partition.strip():
        raise RunError(
            f"slurm.partition 必须是 nonblank string，实得 {partition!r}",
            phase="preflight",
            source=source,
        )


def _require_spec_record(
    record: JobRecord, spec: JobSpec, phase: str, *, source: str, cycle: datetime
) -> None:
    """submit 返回值必须匹配 spec name/resources，且不得已是终态（ownership 6/7）。"""
    # nonblank 判据只做 `strip()` 探针：空白/纯空白 job_id 拒绝，但值原样保留（不归一）。
    if (
        record.job_id is None
        or not isinstance(record.job_id, str)
        or not record.job_id.strip()
    ):
        raise RunError(
            f"submit 返回空/纯空白 job_id：{record.job_id!r}",
            phase=phase,
            source=source,
            cycle=cycle,
        )
    if record.name != spec.name:
        raise RunError(
            f"submit 返回的 name {record.name!r} 不等于 JobSpec.name {spec.name!r}",
            phase=phase,
            source=source,
            cycle=cycle,
            job_id=record.job_id,
        )
    if dict(record.resources) != dict(spec.resources):
        raise RunError(
            f"submit 返回的 resources 不等于 JobSpec.resources："
            f"{dict(record.resources)!r} != {dict(spec.resources)!r}",
            phase=phase,
            source=source,
            cycle=cycle,
            job_id=record.job_id,
        )
    if record.state.is_terminal:
        raise RunError(
            f"submit 不得返回终态（初态只允许 PENDING/RUNNING），实得 {record.state.value}",
            phase=phase,
            source=source,
            cycle=cycle,
            job_id=record.job_id,
        )
    if record.state not in (JobState.PENDING, JobState.RUNNING):
        raise RunError(
            f"submit 返回未知初态 {record.state.value}",
            phase=phase,
            source=source,
            cycle=cycle,
            job_id=record.job_id,
        )


def _require_poll_record(
    record: JobRecord,
    submission: JobRecord,
    spec: JobSpec,
    previous: JobRecord,
    *,
    source: str,
    cycle: datetime,
) -> None:
    """每条 poll record 的身份/时间戳/状态单调性相对提交记录重验（ownership 7）。"""
    if record.job_id != submission.job_id:
        raise RunError(
            f"poll 返回的 job_id {record.job_id!r} 不等于提交返回的 {submission.job_id!r}",
            phase="poll",
            source=source,
            cycle=cycle,
            job_id=submission.job_id,
        )
    if record.name != spec.name:
        raise RunError(
            f"poll 返回的 name {record.name!r} 不等于 JobSpec.name {spec.name!r}",
            phase="poll",
            source=source,
            cycle=cycle,
            job_id=submission.job_id,
        )
    if dict(record.resources) != dict(spec.resources):
        raise RunError(
            "poll 返回的 resources 不等于提交时的 JobSpec.resources",
            phase="poll",
            source=source,
            cycle=cycle,
            job_id=submission.job_id,
        )
    if record.submitted_at != submission.submitted_at:
        raise RunError(
            f"poll 返回的 submitted_at {record.submitted_at!r} 改变/消失："
            f"提交时是 {submission.submitted_at!r}",
            phase="poll",
            source=source,
            cycle=cycle,
            job_id=submission.job_id,
        )
    if previous.started_at is not None and record.started_at != previous.started_at:
        raise RunError(
            f"started_at 一旦出现不得改变/消失：{previous.started_at!r} -> "
            f"{record.started_at!r}",
            phase="poll",
            source=source,
            cycle=cycle,
            job_id=submission.job_id,
        )
    allowed = {
        JobState.PENDING: (JobState.PENDING, JobState.RUNNING),
        JobState.RUNNING: (JobState.RUNNING,),
    }[previous.state]
    if record.state not in allowed and not record.state.is_terminal:
        raise RunError(
            f"非法状态跃迁：{previous.state.value} -> {record.state.value}（"
            "只允许 PENDING->PENDING/RUNNING/终态、RUNNING->RUNNING/终态）",
            phase="poll",
            source=source,
            cycle=cycle,
            job_id=submission.job_id,
        )


def _require_no_recovery(run_directory, output_dir) -> int:  # pragma: no cover
    """controller 绝不在登录侧补跑：recovery runner 被调用即整轮失败。"""
    raise AssertionError("controller 的 checkpoint 重验 runner 必须零调用")


def _job_report(submission: JobRecord, terminal: JobRecord) -> JobRunReport:
    """job 报告逐字来自同一次 submit/terminal record；partition 原值出自提交记录。"""
    return JobRunReport(
        job_id=terminal.job_id,
        partition=submission.resources["partition"],
        state=terminal.state,
        submitted_at=submission.submitted_at,
        started_at=terminal.started_at,
        ended_at=terminal.ended_at,
    )


def run_once(
    *,
    config: Config,
    local: LocalConfig,
    source: str,
    executor: JobExecutor,
    driver: AttemptDriver,
    poll_wait: Callable[[], None],
) -> RunReport:
    """单源单轮骨架：发现 -> 残留 -> raw -> 组装 -> 提交 fake -> 发布 -> work 清理。

    签名逐字冻结（tasks.md「公开面」）：全部 keyword-only、无默认值。实现与逐条
    ownership 的对应注释在私有支撑模块 `yd_producer._controller_run`（本模块同文件
    上方的 `_preflight`/`_require_spec_record`/`_require_poll_record`/_job_report 等
    私有校验面由该模块消费）；本入口只做惰性转发——把 assemble/forcing/tracker 的
    导入链从本模块冷面挪开（本模块同时是 `residue`/`publish` 的依赖）。
    """
    from yd_producer._controller_run import run_once as _impl

    return _impl(
        config=config,
        local=local,
        source=source,
        executor=executor,
        driver=driver,
        poll_wait=poll_wait,
    )
