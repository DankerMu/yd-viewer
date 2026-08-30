"""`yd-producer init`：只在全新根建立首态（任务 11.1）。

契约来源：`docs/compute-loop-design.md` §6.2 与
`openspec/changes/m2-producer-core/specs/init-bootstrap/spec.md`。**这里只给路径、不复述
条数、也不复述 Requirement 名字清单**：复述会随规范增删而静默过期（实测该清单曾写「三条」
却列了四个名字，两者又都与 spec 的实际条目对不上），规范文件本身是唯一真源。

**中心不变量**：`init` 要么让 `YD_ROOT` 从「全新根」转到「每个 source 恰有一份重戳到其
首轮 T 的首态」，要么**一个字节都不写**；除阶段 B 内的写入失败外没有第三种终态，且任何
情况下 `output/` 与已有 `states/` 的内容都不被修改或删除（本模块零删除、零覆盖）。

判定与落盘严格两阶段，顺序固定、不得交错：

- **阶段 A（零写入）**：`states/`/`output/` 拒绝守卫 → 逐源定位并 `state.parse` 率定末态
  → 逐源在扫描窗内定首轮 T → 逐源 `state.restamp_to_absolute_time` + `state.render`。
  任一步失败即返回拒绝，`states/` 与 `output/` 逐字节不变。
  **阶段化而非按源循环**：两源的每一步都先做完再进下一步，拒绝的优先级因此只由步骤序
  决定（gfs 变体缺失 + ifs 窗内无完整 raw 恒判 `VARIANT_MISSING`）；按源交错的写法会让
  同一个根在两次实现下给出不同的拒绝理由，且「任一源无完整 cycle 即整体拒绝」将退化为
  依赖循环顺序的巧合。
- **阶段 B（唯一写入窗）**：按 `rawscan.SOURCES` 的迭代序（当前 `("ifs", "gfs")`）
  `ensure_directory_no_follow` 建 `states/<source>/`，再
  `write_bytes_no_follow_exclusive` 写 `states/<source>/<T:%Y%m%d%H>.cfg.ic`。
  写入序钉死为该常量的迭代序，MUST NOT 依赖 `dict`/`set` 的偶然序——部分落盘时的收尾
  报告必须在两次执行之间可复现。

**写用 `O_EXCL` 而非 `atomic_write_bytes_no_follow`**：后者按语义覆盖已有文件，与「只在
全新根执行」直接冲突；`O_EXCL` 让守卫与写入之间的 TOCTOU 窗口 fail closed
（`FileExistsError` → 拒绝，不覆盖）。

**阶段 B 的失败构造分两类**（MUST NOT 再写「唯一可达构造」——那个说法已被实测证伪）：

- **类一（`EEXIST`）**：拒绝守卫只认**普通文件**（下条），而 `safe_fs._FILE_FLAGS` 的
  `O_CREAT|O_EXCL` 对**任何**已存在的条目都得 `EEXIST`。于是 `states/<source>/<T>.cfg.ic`
  预置为目录/FIFO 一类非普通文件时，它过得了阶段 A 的守卫却挡得住阶段 B 的写入。这是
  **已知且刻意**的缝隙：不把守卫扩到「任何条目」，否则 `states/` 下一个 `.DS_Store` 目录
  就永久砖化建链，方向与下条「宁可要求人工确认」相反。此类**盘上零残留**。
- **类二（写循环中途的 I/O 失败）**：目标不存在、`O_EXCL` 成功创建之后 `os.write` 中途抛
  `ENOSPC`/`EDQUOT`/`EIO`（NFS 发布根上最现实的失败类）。
  `safe_fs.write_bytes_no_follow_exclusive` 的 `except OSError` 臂只关 fd 后转抛、**不
  unlink**（与同模块 `atomic_write_bytes_no_follow` 的失败路径不对称），故盘上留下一份
  **header 合法、body 截断**的普通文件；下游 `controller` 只读 header 行，会把它当成合法
  链起点。`detail` 因此 MUST 点名该目标**可能已被部分写入**、重跑前须一并人工确认——它
  既不在 `written` 里、也不算「前序已落盘」，照类一的话术清理会漏掉它。
  `safe_fs` 的缺 unlink 与 `controller` 接受截断状态两条缺陷都在本模块的 Must-preserve
  面之外，已另行立案；本模块只负责**把它变成可观测的**。

**判据是盘上探测，MUST NOT 用 `SafeFilesystemError.kind` 当代理**（round 2 cand-R2-01
实测证伪）：`kind == "io"` 与「inode 已被创建」不等价——`write_bytes_no_follow_exclusive`
的 `except OSError` 臂覆盖整个写入体、**含 `os.open(..., O_CREAT|O_EXCL, ...)` 本身**，父
目录分量走查（`_open_parent_dir`，在该 `try` 之外）另从自身站点抛出且 `kind` 可为 `"io"`
或 `"unsafe"`；于是 open 期的 `EACCES`/`EROFS`/`ESTALE`（**盘上零残留**）与真正的写中途
`ENOSPC` 拿到同一个 `kind`。故捕获写入腿的异常后 MUST 用 no-follow 的 `os.lstat(target)`
**直接探测目标**（:func:`_probe_partial_residue`）：

- **探到条目** → 判「可能已被部分写入」，话术点名它**已被排他创建**；
- **`FileNotFoundError`** → 判零残留，MUST NOT 出现任何半写话术（这是**精确**结论而非
  保守近似：目标不存在就是不存在）；
- **`lstat` 自身失败**（如目标父目录无 `x`）→ fail closed 到「可能已被部分写入」，但话术
  MUST **hedge**——点名探测本身失败，MUST NOT 宣称「已被排他创建」。

`FileExistsError`（类一）走**自己的**分支且**先于**该臂捕获，故预置条目永远不会被误认成
本模块自己的半写产物。`ensure_directory_no_follow` 的失败**不做半写探测**：那条腿上**对
target 的** `os.open(..., O_CREAT|O_EXCL, ...)` 从未被调用过（它只开/建目录分量），target
侧零残留是结构性事实。但它 MUST 做**另一种**探测——no-follow `os.lstat(target_dir)`，用来
分流下面收尾话术的第二路与第三路（:func:`_foreign_entry_blocks`）：`states/<source>` 本身
被一个 symlink/FIFO/普通文件占住时该腿同样失败，而那是一个**持久外来条目**，与终名被占
逐字节同构。

**收尾话术是三路**（`docs/compute-loop-design.md` §6.2 逐字；MUST NOT 退化成两路，更 MUST
NOT 退化成单看 `written`）：

1. 「`written` 非空 **或** 探到半写目标」这个**析取**为真 → 「根已非全新，重跑 init 前需
   人工清理 `states/`」。判据不能只看 `written`：首个 source 就写中途失败时 `written` 为
   空、盘上却已有一份截断文件，只看 `written` 会报「根仍是全新根」而下一次 init 必然
   `STATES_NOT_EMPTY`，两条话术直接矛盾。
2. 零落盘、零残留、且**写入路径上不存在持久外来条目**（`states/` 置 `0o500`/`0o600` 使
   父目录 open 拿 `EACCES`，或 `O_EXCL` 的 `os.open` 拿 `EACCES` 而探测干净地得
   `FileNotFoundError`）→ 根仍是全新根，MUST NOT 宣称需要清理，把根因放在首位并报「零
   写入，根仍是全新根」。这句话是一条可执行的运维指令：实测恢复权限后**直接重跑**
   `bootstrap` 即成功。
3. 零落盘、零残留、但**写入路径上被一个持久外来条目挡住** → 点名**被占住的那个路径本身**、
   声明它**不是**本次写入产生（故不必也不该删 `states/` 整树），并要求重跑前先确认并移除
   它。这条腿 MUST NOT 说「根仍是全新根」——实测不移除该条目时 run 2 与 run 1 的 `detail`
   逐字节相同，「直接重跑」的承诺在这里为假；也 MUST NOT 说「可能已被部分写入」——该条目
   不是本模块的产物。

**第二路与第三路的判据是「阻塞物是否为持久外来条目」**，MUST NOT 由「哪条腿抛的异常」或
「条目是否恰好落在终名 `target` 上」决定（round 4 R4-A）。外来条目有两种载体，运维后果
逐字节相同（重跑复现同一失败，必须先移除该条目），故 MUST 走同一路：

- **占住终名 `target`**：`O_CREAT|O_EXCL` 撞已存在条目得 `EEXIST`，话术点名 `target`；
- **占住父目录分量 `states/<source>`**（symlink / FIFO / 普通文件）：
  `ensure_directory_no_follow` 抛 `NotADirectoryError`/`ELOOP`（`store/safe_fs.py`），
  话术点名 `target_dir`。

与之相对，权限类失败（`states/` 的 `0o500`/`0o600`）盘上并没有外来条目，`chmod` 后直接
重跑即可成功，仍走第二路。分流判据同样是**盘上探测**而非 `SafeFilesystemError.kind`：
`safe_fs` 把「父目录分量是 symlink」与「父目录 open 拿 `EACCES`」包成同一个类型，只有
no-follow `os.lstat(target_dir)` 能把它们分开（:func:`_foreign_entry_blocks`）。

**可见性判据取「宽」，与 `controller` 的前沿可见集刻意不同**：`controller.decide_frontier`
（issue #22）对不可解析的条目判「不可见」，为的是不让一次崩溃的发布永久砖化该源；本模块
是**唯一的 bootstrap 闸门**，方向相反——`states/` 树下**任一**普通文件（含不合命名规则的
残留）都算「已有状态」而拒绝，`output/` 树下**任一**名为 `DONE` 的普通文件都算已有产物而
拒绝。init 只在系统历史第一次执行，宁可要求人工确认，也不能在一个有残留的根上重新建链。

**枚举/探测失败 MUST NOT fail-open**（沿用 #22 裁决 9 的同一规则，本模块是写侧）：**只有**
`FileNotFoundError` / `NotADirectoryError` 等价于「空集合」；其余任何 `OSError`
（`EACCES`/`EPERM`/`EIO`/`ESTALE`/`ELOOP`…）一律 `DISCOVERY_UNREADABLE` 并整体拒绝。方向
性理由比 #22 更严：这里判空即**放行写入**，`states/` 因权限不可枚举时若按「空」处理，就会
往一个可能已有状态的根上写首态，直接断链。故本模块 MUST NOT 用裸
`Path.exists()`/`Path.is_file()`——`pathlib` 只吞 `ENOENT/ENOTDIR/EBADF/ELOOP/EINVAL`。
**分层切分**：`DISCOVERY_UNREADABLE` 专指「集合无法枚举 / 条目无法判定」；率定末态**本身**
被定位成功之后的读失败（含 mode 000 的 `EACCES`）由 `state.parse` 收敛为 `ValueError`，
一律归 `CALIBRATION_STATE_UNREADABLE`。

**`variants.<source>` 先过相对性闸门再拼接**（compute-loop §6.2）：取值是绝对路径、或含
`..` 分量时，判 `VARIANT_PATH_INVALID` 整体拒绝、零写入，MUST NOT 拼接后读取——否则整条
状态链的**起点**会取自 `YD_ROOT` 之外（写入面恒为 `yd_root/"states"`，故这是越界**读**）。
判据本身在 `config.variant_relative_violation`，与 `prepare._resolve_variant_relative`
**共用同一份实现**，MUST NOT 复制第二份。

**`NO_COMPLETE_RAW_CYCLE` 区分「缺数据」与「不可读」**：`rawscan.ScanVerdict` 把
`missing_files` 与 `unreadable_files` 分开，本模块 MUST NOT 只取 `.complete`。生产 raw 根
是 NFS 上由另一 uid 写入的树，权限故障同样使 cycle 判不完整；此时提示「等待 raw 补齐后重
跑」是把权限故障伪装成缺数据，运维会对着已在盘上的数据永远重跑（同一伪装已在
`cycle.hours` 路径上被禁）。方向不变——两种情形都整体拒绝、零写入，区别只在给运维的下一
步动作：确为缺文件才提示等 raw 补齐，存在不可读文件时 MUST 点明并要求先修复可读性。

**non-goals**（越界即偏离）：MUST NOT 运行 SHUD（本模块无任何 subprocess 面）、MUST NOT
写任何 `DONE`、MUST NOT 触碰 `output/`；不做状态 QC 与负残差归零（`state_qc` 的两个入口在
init 期**不调用**——率定末态是 prepare 提交的、已被 #20 校验过的基线产物，init 只做「复制
+ 重戳」，compute-loop §6.2 第 4 步逐字；§8 的负残差归零逐字属**运行期**语义）；不提供单
源建链、补链、`--force` 或任何覆盖参数（compute-loop §6.2 逐字）；不提供回滚（删除面归
#23/#25，init 无权删它没确认过的东西）。

本模块 stdlib-only：零 NWM 运行时 import、零数据库/scheduler 依赖。
"""

from __future__ import annotations

import enum
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from yd_producer import rawscan
from yd_producer.config import (
    Config,
    ConfigError,
    LocalConfig,
    variant_relative_violation,
)
from yd_producer.controller import STATE_SUFFIX
from yd_producer.state import parse, render, restamp_to_absolute_time
from yd_producer.store import safe_fs

__all__ = [
    "SCAN_WINDOW",
    "InitRefusal",
    "InitReport",
    "bootstrap",
]

#: 扫描窗长度（compute-loop §6.2）：`[now - 7 天, now]`，**双端闭**。
SCAN_WINDOW = timedelta(days=7)

#: `DONE` 标记文件名（products-contract §4.1）。本模块只**读**它，从不写。
DONE_NAME = "DONE"


class InitRefusal(enum.StrEnum):
    """`init` 的拒绝理由。闭合词表（10 项），逐项可区分，MUST NOT 以异常逃逸。"""

    #: `states/` 树下存在任一普通文件（含不合命名规则的残留）。
    STATES_NOT_EMPTY = "states_not_empty"
    #: `output/` 树下存在任一名为 `DONE` 的普通文件。
    DONE_PRESENT = "done_present"
    #: 变体目录不存在 / 不是目录。
    VARIANT_MISSING = "variant_missing"
    #: `config.variants.<source>` 是绝对路径或含 `..` 分量（`detail` 带 source 与原始
    #: 取值）。相对性闸门跑在拼接之前，MUST NOT 拼接后再读。
    VARIANT_PATH_INVALID = "variant_path_invalid"
    #: 变体**顶层**的 `*.cfg.ic` 普通文件命中数 ≠ 1（`detail` 带命中数与路径）。
    CALIBRATION_STATE_AMBIGUOUS = "calibration_state_ambiguous"
    #: 率定末态定位成功但 `state.parse` 抛 `ValueError`（超界、非 UTF-8、结构不可用、
    #: 以及被定位条目自身的读失败）。
    CALIBRATION_STATE_UNREADABLE = "calibration_state_unreadable"
    #: `restamp_to_absolute_time` 的 shape 门拒绝（header 数值 token 数不为 3/4）。
    HEADER_SHAPE_INVALID = "header_shape_invalid"
    #: 某源扫描窗内无完整 cycle（`detail` 带 source 与窗口端点）。
    NO_COMPLETE_RAW_CYCLE = "no_complete_raw_cycle"
    #: 任一文件系统探测**无法确定**（`ENOENT`/`ENOTDIR` 之外的 `OSError`）。
    DISCOVERY_UNREADABLE = "discovery_unreadable"
    #: 阶段 B 写入失败（`detail` 列出**全部**已落盘 source 的路径）。
    WRITE_FAILED = "write_failed"


@dataclass(frozen=True)
class InitReport:
    """一次 `bootstrap` 的结论。

    `refusal is None` ⟺ 完全成功，此时 `written` 是每源恰一条的落盘路径。

    **与 `controller.FrontierDecision` 的「恰有一个非 None」不同**：本类型的 `written`
    非空与 `refusal` 非 `None` **可以同时成立**——阶段 B 中途失败时前序 source 的首态已
    落盘且 MUST NOT 被回滚删除，`written` 如实列出它们，`refusal` 为
    :data:`InitRefusal.WRITE_FAILED`。调用方 MUST 以 `refusal is not None` 判失败，
    MUST NOT 以 `written` 是否为空判成败。
    """

    written: tuple[Path, ...]
    refusal: InitRefusal | None
    detail: str


class _DiscoveryUnreadable(Exception):
    """探测信号：某处文件系统探测无法确定。由 `bootstrap` 收敛成拒绝，不外泄。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


# --- 阶段 A：文件系统探测原语（全部 fail closed）-----------------------------


def _entry_names(directory: Path) -> list[str]:
    """列目录。目录**不存在**（或路径中途非目录）才视为空集合；列不出来一律不可确定。"""
    try:
        return sorted(os.listdir(directory))
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as error:
        raise _DiscoveryUnreadable(f"目录 {directory} 无法枚举（{error}）") from error


def _entry_kind(path: Path) -> tuple[bool, bool]:
    """返回 `(是普通文件, 是真实目录)`。

    「普通文件」跟随 symlink（指向普通文件的 symlink 同样算「已有状态」——守卫取宽）；
    「真实目录」用 `lstat` 判，故遍历**不进入** symlink 指向的目录，符号链接环因此不可能
    让遍历发散。断链 symlink 的 `stat` 抛 `ENOENT`，两项皆为假。
    """
    try:
        link_mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return (False, False)
    except OSError as error:
        raise _DiscoveryUnreadable(f"条目 {path} 无法判定（{error}）") from error
    if stat.S_ISDIR(link_mode):
        return (False, True)
    try:
        mode = os.stat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return (False, False)
    except OSError as error:
        raise _DiscoveryUnreadable(f"条目 {path} 无法判定（{error}）") from error
    return (stat.S_ISREG(mode), False)


def _first_regular_file(root: Path, *, name: str | None = None) -> Path | None:
    """树遍历：返回首个普通文件（`name` 非空时只认该文件名），没有则 `None`。

    遍历序由 `_entry_names` 的排序保证可复现；探测失败一律上抛 `_DiscoveryUnreadable`。
    """
    pending = [root]
    while pending:
        directory = pending.pop(0)
        for entry_name in _entry_names(directory):
            path = directory / entry_name
            is_file, is_dir = _entry_kind(path)
            if is_dir:
                pending.append(path)
            elif is_file and (name is None or entry_name == name):
                return path
    return None


def _locate_calibration_state(variant_dir: Path) -> Path | list[Path]:
    """定位变体**顶层**（非递归）唯一的 `.cfg.ic` 普通文件。

    命中恰好一个时返回该路径；否则返回命中列表（可能为空）供调用方判
    `CALIBRATION_STATE_AMBIGUOUS`。目录不存在 / 不是目录由调用方先行判掉。
    """
    hits = [
        variant_dir / entry_name
        for entry_name in _entry_names(variant_dir)
        if entry_name.endswith(STATE_SUFFIX)
        and _entry_kind(variant_dir / entry_name)[0]
    ]
    return hits[0] if len(hits) == 1 else hits


def _is_directory(path: Path) -> bool:
    """目录判定，fail closed。不存在 / 不是目录返回 `False`，无法判定即上抛。"""
    try:
        mode = os.stat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as error:
        raise _DiscoveryUnreadable(f"路径 {path} 无法判定（{error}）") from error
    return stat.S_ISDIR(mode)


# --- 阶段 A：扫描窗 ----------------------------------------------------------


def _normalize_now(now: datetime) -> datetime:
    """把 `now` 归一为 UTC aware。naive 一律拒绝，MUST NOT 按宿主时区静默重释。"""
    if not isinstance(now, datetime):
        raise ConfigError(f"now 必须是 datetime，实际 {type(now).__name__}")
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ConfigError(
            f"now 必须是 tz-aware 时刻，实际 {now!r}"
            "（naive 值按宿主时区重释会把 7 天扫描窗整体挪走）"
        )
    return now.astimezone(UTC)


def _require_constructible_hours(hours: tuple[int, ...]) -> None:
    """候选网格可构造性自查：`hours` 非空、且每个值都在 `0..23` 内。

    只管「网格能不能建」，不管业务取值域（`{0, 12}` 由 `rawscan.judge` 施加，见
    :func:`_candidate_cycles` 的 docstring）。
    """
    if not hours:
        raise ConfigError(
            "配置项 `cycle.hours` 不得为空列表：候选 cycle 网格会退化成空集，"
            "扫描窗内一次 raw 判定都不会发生"
        )
    invalid = [
        hour for hour in hours if not isinstance(hour, int) or not 0 <= hour < 24
    ]
    if invalid:
        raise ConfigError(
            f"cycle.hours 含非法小时 {invalid}：每个值必须是 0..23 内的整数"
        )


def _candidate_cycles(
    window_start: datetime, now: datetime, hours: tuple[int, ...]
) -> tuple[datetime, ...]:
    """窗内候选 cycle，**升序**。

    小时集合取自 `config.cycle.hours`（MUST NOT 硬编码 `[0, 12]`）；窗**双端闭**；严格
    `cycle <= now`，未来 cycle 不进候选集。`hours` 在 `config.toml` 内的书写顺序不作数，
    故最后统一排序——按日期网格 × 声明序枚举出的序列未必升序。

    **取值域自查跑在枚举之前**：全仓唯一的域校验 `rawscan._validate_config_domain` 在
    `judge` **体内**，而本函数是这条路径上 `config.cycle.hours` 的第一个消费者、跑在任何
    `judge` 调用之前，于是有两个输入结构性地到不了那道校验——`hours = ()` 让候选集为空、
    `judge` 一次都不调，退化成「窗内无完整 cycle、等 raw 补齐」这个**伪装**；`hours` 含
    `0..23` 之外的值让 `datetime(...)` 抛**裸 `ValueError`**，`cli.main` 的
    `except ConfigError` 接不住，traceback 逃逸出 CLI。故此处只补这两个洞：非空 + 每个值
    是合法小时，不满足即 `ConfigError` 点名 `cycle.hours`。
    **MUST NOT 在此重新声明 `{0, 12}` 这个域**，也 MUST NOT 导入私有的
    `rawscan._validate_config_domain`（`rawscan` 属 Must-preserve 面）：候选网格一旦非空
    且可构造，第一次 `judge` 调用就会施加 `{0, 12}` 并原样上抛 `ConfigError`——`rawscan`
    仍是取值域的唯一权威。
    """
    _require_constructible_hours(hours)
    candidates: list[datetime] = []
    start_date = window_start.date()
    span = (now.date() - start_date).days
    for offset in range(span + 1):
        day = start_date + timedelta(days=offset)
        for hour in sorted(set(hours)):
            cycle = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
            if window_start <= cycle <= now:
                candidates.append(cycle)
    return tuple(sorted(candidates))


def _first_complete_cycle(
    raw_root: str,
    source: str,
    candidates: tuple[datetime, ...],
    config: Config,
) -> tuple[datetime | None, tuple[Path, ...], tuple[Path, ...]]:
    """升序取第一个完整 cycle，并带回被跳过候选上的**不可读**与**缺失** raw 文件。

    `rawscan.judge` 抛的 `ConfigError`（配置取值域 / 请求校验 / 模式校验）**原样上抛**，
    MUST NOT 被吞成「不完整」——那会把一个配置错误伪装成「等 raw 补齐」，让运维永远重跑
    init。只有 `complete is False` 走「继续找下一个候选」（cycle 目录整体不存在**不是**
    错误，见 `rawscan.judge` 的 docstring）。

    **返回值 MUST NOT 塌缩成 `complete` 一个 bool**：`rawscan.ScanVerdict` 把
    `missing_files` 与 `unreadable_files` 分得很清楚，而生产 raw 根是 NFS 上由 NWM 以另一
    uid 写入的目录树——权限故障同样让 cycle 判不完整。丢掉这条信息，拒绝理由就只剩「等
    raw 补齐」这一句，把一次权限故障伪装成缺数据，运维会对着已在盘上的数据永远重跑
    （本模块已在 `cycle.hours` 路径上禁止了同一伪装）。方向不变——两种情形都 fail closed
    地整体拒绝、零写入，区别只在**给运维的下一步动作**。

    **命中完整 cycle 时同样 MUST 带回累积的 `unreadable`**（round 4 R4-C）：先前这里
    `return cycle, ()` 把此前候选上的不可读文件整个丢掉，于是一次 raw 权限故障会把链起点
    **静默**推后一个 cycle 步长**并落盘**；落盘后根已非全新，重跑必被 `STATES_NOT_EMPTY`
    拒绝——静默偏移没有自愈路径。方向仍不变（照样建链，MUST NOT 改成拒绝），只是把它变成
    成功理由里可观测的一句话。

    **`missing` 同样 MUST 带回**（round 4 R4-B）：只带回 `unreadable` 时，调用方的
    「不是缺数据」这个**全称否定**在混合态（同一候选既缺文件又有不可读文件）上为假，而
    混合态在生产 NFS 上是主导形态。**整目录缺席的候选不计入 `missing`**
    （`verdict.missing_files == verdict.expected_files`）：`rawscan.judge` 自陈 cycle 目录
    整体不存在时返回「全部缺失」，而 7 天扫描窗的绝大多数候选正落在这里，把它们计进来会
    让每一次纯不可读的拒绝都退化成混合态、并在 `detail` 里刷出上百条路径。
    """
    unreadable: list[Path] = []
    missing: list[Path] = []
    for cycle in candidates:
        verdict = rawscan.judge(raw_root, source, cycle, config)
        if verdict.complete:
            return cycle, tuple(unreadable), tuple(missing)
        unreadable.extend(verdict.unreadable_files)
        if verdict.missing_files != verdict.expected_files:
            missing.extend(verdict.missing_files)
    return None, tuple(unreadable), tuple(missing)


# --- 编排 --------------------------------------------------------------------


def _refuse(refusal: InitRefusal, detail: str) -> InitReport:
    return InitReport(written=(), refusal=refusal, detail=detail)


def _probe_partial_residue(target: Path) -> str | None:
    """写入腿失败后**直接探测**目标，返回半写话术；确定零残留时返回 `None`。

    只用 no-follow 的 `os.lstat`：判据必须是「盘上是否真的留下了条目」，而不是
    `SafeFilesystemError.kind`（它对 open 期失败与写中途失败给出同一个值，见模块头）。
    探测本身失败时 fail closed 到「可能半写」，但话术点名的是**探测失败**，不是一个未被
    观测到的排他创建。
    """
    try:
        os.lstat(target)
    except FileNotFoundError:
        return None
    except OSError as error:
        return (
            f"{target} 的落盘残留无法探测（{error}）：保守起见按可能已被部分写入处理，"
            "重跑 init 前须一并人工确认并清理"
        )
    return (
        f"{target} 已被排他创建但写入中途失败，可能已被部分写入"
        "（header 合法、body 截断），重跑 init 前须一并人工确认并清理"
    )


def _foreign_entry_blocks(path: Path) -> bool:
    """no-follow **盘上探测**：`path` 上是否坐着一个持久的、非目录的外来条目。

    专供 `ensure_directory_no_follow` 腿的收尾分流（round 4 R4-A）。判据 MUST 是盘上有
    什么，而不是 `SafeFilesystemError.kind`（同模块头：`safe_fs` 把「分量是 symlink」与
    「父目录 open 拿 `EACCES`」包成同一个类型）：

    - **探到非目录条目**（symlink——含悬垂、FIFO、普通文件）→ `True`，走第三路。这类条目
      不是本次写入产生，不 `chmod` 也不删除它就重跑，必然以同样理由再次失败。
    - **`FileNotFoundError`** → `False`：分量根本不存在，是权限或 I/O 故障，走第二路。
    - **探到真实目录** → `False`：目录不是阻塞物（`ensure_directory_no_follow` 对既存目录
      是空操作成功），失败来自更上层的权限，走第二路。
    - **`lstat` 自身失败**（如 `states/` 置 `0o600`，探针穿不过去）→ `False`：这里
      fail-open 到第二路是**刻意**的，且与「探测失败 fail closed」的半写判据不冲突——两者
      的保守方向相反。半写探测的风险是漏报盘上残留（下一次 init 会撞
      `STATES_NOT_EMPTY`），故宁可多报；本探测的风险是错误地要求运维去移除一个并不存在的
      条目，而 `states/` 探不动本身恰恰就是权限故障的证据，第二路的「排掉根因后直接重跑」
      正是对的指令。
    """
    try:
        entry = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return not stat.S_ISDIR(entry.st_mode)


def _write_failed(
    source: str,
    target: Path,
    written: list[Path],
    error: BaseException,
    *,
    partial_note: str | None,
    blocked_by_foreign_entry: bool = False,
    blocking_path: Path | None = None,
) -> InitReport:
    """阶段 B 的失败收尾，结尾话术**三路**（compute-loop §6.2）。

    `partial_note` 是 :func:`_probe_partial_residue` 的探测结论：非 `None` 即「盘上可能
    留下了半写目标」，其文本已按探测结果（探到 / 探测失败）分好话术。
    `blocked_by_foreign_entry` 为真表示**写入路径上**坐着一个不是本次写入产生的持久外来
    条目。它有两种载体：`FileExistsError` 腿（占住终名 `target`）与 ensure 腿
    （占住父目录分量 `states/<source>`，由 :func:`_foreign_entry_blocks` 盘上探到）。
    `blocking_path` 是**被占住的那个路径本身**，第三路话术 MUST 插值它而不是 `target`
    ——ensure 腿上 `target` 只是一个从未被创建的终名，点名它会让「点名该条目路径」的承诺
    在下一层再次为假（round 4 R4-A）。缺省回落到 `target`（终名腿）。

    三路互斥且顺序固定：

    (a) `written` 非空 **或** `partial_note is not None` → 「需人工清理 `states/`」。判据
        MUST 是这个析取而非单看 `written`：首个 source 就写中途失败时 `written` 为空、盘
        上却已有一份截断文件，只看 `written` 会报「根仍是全新根」而下一次 init 必然
        `STATES_NOT_EMPTY`。
    (b) 零落盘、零残留、且目标路径上**无任何条目**（open 期失败）→ 「零写入，根仍是全新
        根」，根因放在首位。这句话是一条运维指令（直接重跑即可），实测成立。
    (c) 零落盘、零残留、但**写入路径上被一个持久外来条目挡住**（终名被占 → `O_EXCL` 撞
        `EEXIST`；父目录分量被占 → ensure 腿抛 `NotADirectoryError`/`ELOOP`）→ 点名
        `blocking_path` 并要求重跑前先确认并移除它。这里 MUST NOT 说「根仍是全新根」
        （不移除该条目，重跑必然以同样理由再次失败——实测 run 1 与 run 2 的 detail 逐字节
        相同），也 MUST NOT 说「可能已被部分写入」（该条目不是本次写入产生，把 `states/`
        整树删掉是过度动作）。
    """
    parts = [f"{source} 的首态写入 {target} 失败：{error}"]
    if partial_note is not None:
        parts.append(partial_note)
    landed = "、".join(str(path) for path in written) or "（无）"
    parts.append(f"已落盘的首态：{landed}（不回滚、不删除）")
    if written or partial_note is not None:
        # 半写产物同样让根不再全新，即使它不在 `written` 里。
        parts.append("根已非全新，重跑 init 前需人工清理 `states/`")
    elif blocked_by_foreign_entry:
        blocker = target if blocking_path is None else blocking_path
        parts.append(
            f"本次没有任何首态落盘，但写入路径 {blocker} 上已有一个**非本次写入产生**的"
            "条目：它既不是半写产物、也不该连同 `states/` 整树一起删除；"
            "重跑 init 之前须人工确认该条目的来源并移除它，否则重跑必然以同样理由再次失败"
        )
    else:
        parts.append("零写入，根仍是全新根")
    return InitReport(
        written=tuple(written),
        refusal=InitRefusal.WRITE_FAILED,
        detail="；".join(parts),
    )


def bootstrap(*, local: LocalConfig, config: Config, now: datetime) -> InitReport:
    """在全新根上为每个 source 建立首态；任一判定不过即整体拒绝且零写入。

    `now` **必须可注入**：7 天扫描窗对「执行时刻」有语义依赖，不可注入即测试只能自证。
    naive `now` 抛 `ConfigError`；`rawscan.judge` 的 `ConfigError` 同样原样上抛（两者都由
    `cli.main` 转成退出码 `1`）。其余一切失败都以 :class:`InitReport` 的 `refusal` 表达。
    """
    now_utc = _normalize_now(now)
    window_start = now_utc - SCAN_WINDOW
    yd_root = Path(local.yd_root)
    states_root = yd_root / "states"
    output_root = yd_root / "output"

    try:
        # 1. 拒绝守卫：`states/` 树下任一普通文件、`output/` 树下任一 `DONE`。
        residual_state = _first_regular_file(states_root)
        if residual_state is not None:
            return _refuse(
                InitRefusal.STATES_NOT_EMPTY,
                f"{states_root} 下已有状态文件 {residual_state}；"
                "init 只在全新根执行，请人工确认后清理",
            )
        existing_done = _first_regular_file(output_root, name=DONE_NAME)
        if existing_done is not None:
            return _refuse(
                InitRefusal.DONE_PRESENT,
                f"{output_root} 下已有产物标记 {existing_done}；init 只在全新根执行",
            )

        # 2. 逐源定位率定末态（变体顶层恰一个 `.cfg.ic` 普通文件）。
        calibration: dict[str, Path] = {}
        for source in rawscan.SOURCES:
            # 相对性闸门跑在 join **之前**：绝对路径或含 `..` 的取值一旦被拼接后读取，
            # 链起点就取自 `YD_ROOT` 之外。判据与 `prepare` 共用同一份实现。
            variant_value = getattr(config.variants, source)
            violation = variant_relative_violation(f"variants.{source}", variant_value)
            if violation is not None:
                return _refuse(
                    InitRefusal.VARIANT_PATH_INVALID,
                    f"{source} 的变体路径越出 `yd_root`：{violation}"
                    "（链起点必须取自 `YD_ROOT` 之内，故拒绝拼接后读取）",
                )
            variant_dir = yd_root / variant_value
            if not _is_directory(variant_dir):
                return _refuse(
                    InitRefusal.VARIANT_MISSING,
                    f"{source} 的变体目录不存在或不是目录：{variant_dir}"
                    "（`variants." + source + "` 相对 yd_root）",
                )
            located = _locate_calibration_state(variant_dir)
            if isinstance(located, list):
                listed = "、".join(str(path) for path in located) or "（无）"
                return _refuse(
                    InitRefusal.CALIBRATION_STATE_AMBIGUOUS,
                    f"{source} 的变体目录 {variant_dir} 顶层的 `{STATE_SUFFIX}` 普通文件"
                    f"命中 {len(located)} 个，必须恰好 1 个：{listed}",
                )
            calibration[source] = located
    except _DiscoveryUnreadable as error:
        return _refuse(InitRefusal.DISCOVERY_UNREADABLE, error.detail)

    # 3. 逐源解析率定末态。定位成功之后的读失败由 `state.parse` 收敛为 `ValueError`。
    documents = {}
    for source in rawscan.SOURCES:
        try:
            documents[source] = parse(calibration[source])
        except ValueError as error:
            return _refuse(
                InitRefusal.CALIBRATION_STATE_UNREADABLE,
                f"{source} 的率定末态 {calibration[source]} 不可用：{error}",
            )

    # 4. 逐源在扫描窗内定首轮 T；任一源无完整 cycle 即整体拒绝（fail closed）。
    candidates = _candidate_cycles(window_start, now_utc, config.cycle.hours)
    frontier: dict[str, datetime] = {}
    skipped_unreadable: dict[str, tuple[Path, ...]] = {}
    for source in rawscan.SOURCES:
        cycle, unreadable, missing = _first_complete_cycle(
            local.nwm.raw_root, source, candidates, config
        )
        if cycle is None:
            reason = (
                f"{source} 在扫描窗 [{window_start.isoformat()}, "
                f"{now_utc.isoformat()}] 内没有完整 cycle；整体拒绝、不写任何状态"
            )
            # 分支优先级固定：不可读优先于纯缺文件。「等待 raw 补齐后重跑 init」是**只**
            # 对纯缺文件成立的补救指令（compute-loop §6.2 逐字），MUST NOT 出现在下面
            # 任何一条含不可读文件的腿上——包括混合态。
            if unreadable:
                listed = "、".join(str(path) for path in unreadable[:3])
                head = (
                    f"{reason}；窗内有 {len(unreadable)} 个 raw 文件**存在但不可读**"
                    f"（如 {listed}）"
                )
                if missing:
                    # 混合态：MUST 并列点名两者。全称否定「不是缺数据」在这里为假，
                    # 而给缺失侧补一句「等 raw 补齐」又会把权限故障的补救指令稀释掉，
                    # 故两侧都只陈述事实（round 4 R4-B）。
                    listed_missing = "、".join(str(path) for path in missing[:3])
                    return _refuse(
                        InitRefusal.NO_COMPLETE_RAW_CYCLE,
                        f"{head}；同时另有 {len(missing)} 个预期 raw 文件**缺失**"
                        f"（如 {listed_missing}）：这是权限/IO 故障与数据缺口**并存**，"
                        "重跑 init 之前两者都须逐一确认",
                    )
                return _refuse(
                    InitRefusal.NO_COMPLETE_RAW_CYCLE,
                    f"{head}：这是权限或 I/O 故障，不是缺数据，"
                    "重跑 init 之前须先修复这些文件的可读性",
                )
            return _refuse(
                InitRefusal.NO_COMPLETE_RAW_CYCLE,
                f"{reason}，等待 raw 补齐后重跑 init",
            )
        frontier[source] = cycle
        if unreadable:
            skipped_unreadable[source] = unreadable

    # 5. 逐源重戳并渲染字节。到这里为止仍然零写入。
    payloads: dict[str, bytes] = {}
    for source in rawscan.SOURCES:
        try:
            restamped = restamp_to_absolute_time(documents[source], frontier[source])
        except ValueError as error:
            return _refuse(
                InitRefusal.HEADER_SHAPE_INVALID,
                f"{source} 的率定末态 {calibration[source]} 无法重戳：{error}",
            )
        payloads[source] = render(restamped)

    # 6. 阶段 B：唯一的写入窗，顺序钉死为 `rawscan.SOURCES` 的迭代序。
    written: list[Path] = []
    for source in rawscan.SOURCES:
        target_dir = states_root / source
        target = target_dir / (
            frontier[source].strftime(rawscan.CYCLE_DIR_FORMAT) + STATE_SUFFIX
        )
        # 两次调用**分别** try：目录腿失败时 `os.open(target...)` 结构性地**从未被调用
        # 过**，零残留是事实而非推断，故不必也不该去探测 **target**（`chmod 0o500 states/`
        # 就是这种终态）。合在一个 try 里会把写入腿的探测语义套到一条与目标无关的失败上。
        # 但这条腿仍必须探测 **target_dir**：`states/<source>` 被 symlink/FIFO/普通文件
        # 占住时它同样在这里失败，而那是一个持久外来条目，收尾 MUST 走第三路并点名
        # `target_dir` 本身（round 4 R4-A；判据是盘上探测，不是异常类型/`kind`）。
        try:
            safe_fs.ensure_directory_no_follow(target_dir)
        except (OSError, safe_fs.SafeFilesystemError) as error:
            return _write_failed(
                source,
                target,
                written,
                error,
                partial_note=None,
                blocked_by_foreign_entry=_foreign_entry_blocks(target_dir),
                blocking_path=target_dir,
            )
        try:
            safe_fs.write_bytes_no_follow_exclusive(target, payloads[source])
        except FileExistsError as error:
            # 类一：`O_EXCL` 拒绝覆盖。盘上的条目是**别人**预置的，不是本模块的半写产物，
            # 故本臂 MUST 先于下面的探测臂捕获，且不走探测；但它同样让「直接重跑」这条
            # 运维承诺为假，故走收尾话术的第三路。
            return _write_failed(
                source,
                target,
                written,
                error,
                partial_note=None,
                blocked_by_foreign_entry=True,
            )
        except (OSError, safe_fs.SafeFilesystemError) as error:
            # 写入腿的其余失败：open 期（零残留）与写中途（半写）在异常类型与 `kind` 上
            # 不可区分，只有盘上探测能分开，见 `_probe_partial_residue`。
            return _write_failed(
                source,
                target,
                written,
                error,
                partial_note=_probe_partial_residue(target),
            )
        written.append(target)

    # 成功理由：两源的首轮 T，外加**被跳过候选上的不可读 raw**（round 4 R4-C）。后者不改
    # 方向（照样建链），但链起点因此比 raw 实际到达情况更晚，而 init 一生只跑一次——落盘
    # 之后重跑必被 `STATES_NOT_EMPTY` 拒绝，静默偏移没有自愈路径，故 MUST 在这里点名。
    detail_parts = [
        f"{source} 首轮 T={frontier[source].strftime(rawscan.CYCLE_DIR_FORMAT)}"
        for source in rawscan.SOURCES
    ]
    for source in rawscan.SOURCES:
        skipped = skipped_unreadable.get(source, ())
        if skipped:
            listed = "、".join(str(path) for path in skipped[:3])
            detail_parts.append(
                f"{source} 的链起点跳过了更早的候选，那些候选上有 {len(skipped)} 个 raw "
                f"文件**存在但不可读**（如 {listed}）：链起点已因此后移，"
                "init 不会重跑，须人工确认这些文件的可读性"
            )
    return InitReport(
        written=tuple(written),
        refusal=None,
        detail="；".join(detail_parts),
    )
