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
`%Y%m%d%H` 解析（`2026023100`、`9999999999` 是 10 位数字却非法），`states/` 侧另需
`.cfg.ic` 后缀。不满足者对前沿**不可见**，既不报错也不停源——发布的临时文件在
`states/<source>/` 目录内 rename，若对不可解析文件名 fail-closed，一次崩溃的发布会把该源
永久砖化。残留的**清理**归 issue #23。

**状态文件可读性跟随 symlink**（与 `state/cfg_ic.py` 的有界读同一理由：macOS `/tmp` 本身
是 symlink，no-follow 会误拒合法测试树）。no-follow 的越界拒绝属删除/发布面，归 #24/#25。

停止一律经返回值里的 `StopReason` 表达，MUST NOT 以异常逃逸：`OSError`、
`UnicodeDecodeError`、`ValueError` 全部被吞成分类结果。

本模块 stdlib-only：零 NWM 运行时 import、零数据库/scheduler 依赖。
"""

from __future__ import annotations

import enum
import math
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


class StopReason(enum.Enum):
    """本次不提交该源的原因。闭合词表，逐项可区分。"""

    #: 该源既无任何 `DONE`，也没有任何合法命名的状态文件（链尚未由 init 建立）。
    NO_INITIAL_STATE = "no_initial_state"
    #: 待跑 T 的状态文件不存在（MUST NOT 回退到更旧状态或互借另一源）。
    STATE_MISSING = "state_missing"
    #: 状态文件存在但不可读：非普通文件、断链 symlink、权限拒绝、非 UTF-8 或超字节上界。
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
    """
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
    """10 位 ASCII 数字**且** `%Y%m%d%H` 可解析时返回 UTC aware 时刻，否则 `None`。

    两道门缺一不可：`str.isdigit()` 会接受 Unicode 数字，而 `2026023100`（2 月 31 日）与
    `9999999999`（99 月）都是 10 位 ASCII 数字却不是合法 cycle。解析失败是「不可见」，
    MUST NOT 让 `ValueError` 逃逸、更不得因此停源。
    """
    if len(name) != 10 or not all(char in "0123456789" for char in name):
        return None
    try:
        return datetime.strptime(name, CYCLE_ID_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _iter_entry_names(directory: Path) -> list[str]:
    """列目录；目录不存在/不可读时视为空，MUST NOT 抛错。"""
    try:
        return [entry.name for entry in directory.iterdir()]
    except OSError:
        return []


def _done_cycles(output_root: Path, source: str) -> set[datetime]:
    """该源已完成的 cycle 集合。

    `DONE` MUST 是**普通文件**（products-contract §4.1）：目录、断链 symlink、FIFO 都不
    算完成。`Path.is_file()` 跟随 symlink 且对不存在/断链返回 `False`，正是这条语义。
    逐源独立：`output/<cycle>/gfs/DONE` 不为 `ifs` 计数。
    """
    cycles: set[datetime] = set()
    for name in _iter_entry_names(output_root):
        cycle = _parse_cycle_id(name)
        if cycle is None:
            continue
        try:
            if (output_root / name / source / "DONE").is_file():
                cycles.add(cycle)
        except OSError:
            continue
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


def _classify_state(
    state_path: Path, target: datetime
) -> tuple[StopReason | None, str]:
    """返回 `(停止原因, 说明)`；`(None, 说明)` 表示该状态可用于起跑。"""
    if not state_path.exists():
        if state_path.is_symlink():
            return (
                StopReason.STATE_UNREADABLE,
                f"状态 {state_path} 是断链 symlink",
            )
        return (StopReason.STATE_MISSING, f"状态 {state_path} 不存在")

    try:
        mode = state_path.stat().st_mode
    except OSError as error:
        return (StopReason.STATE_UNREADABLE, f"状态 {state_path} 无法 stat（{error}）")
    if not stat.S_ISREG(mode):
        return (
            StopReason.STATE_UNREADABLE,
            f"状态 {state_path} 不是普通文件（st_mode={mode:#o}）",
        )

    header_line = _read_header_line(state_path)
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


def _read_header_line(state_path: Path) -> str | StopReason | None:
    """有界读出首个非空行；不可读/超界/非 UTF-8 返回 `StopReason.STATE_UNREADABLE`。

    读取上界复用 `state/cfg_ic.MAX_STATE_IC_BYTES`（读到上界 +1 字节即可判超界而不必
    把超大文件整个读进内存）。这里**只取 header 行**：缺段、行数不符、数值区损坏等结构
    检查是任务 4.2 / issue #9 的面，前沿不做全量解析。
    """
    try:
        with open(state_path, "rb") as handle:
            data = handle.read(MAX_STATE_IC_BYTES + 1)
    except OSError:
        return StopReason.STATE_UNREADABLE
    if len(data) > MAX_STATE_IC_BYTES:
        return StopReason.STATE_UNREADABLE
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return StopReason.STATE_UNREADABLE
    for line in text.splitlines():
        if line.strip():
            return line
    return None
