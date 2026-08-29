"""`yd-producer init`：只在全新根建立首态（任务 11.1）。

契约来源：`docs/compute-loop-design.md` §6.2、`openspec/changes/m2-producer-core/specs/
init-bootstrap/spec.md` 的三条 Requirement（「只在全新根执行」「扫描窗内确定各源首轮」
「率定末态定位」「首态生成」）。

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
  链起点。本模块因此 MUST 区分 `FileExistsError` 与 `SafeFilesystemError(kind="io")`，
  后者的 `detail` 点名该目标**可能已被部分写入**、重跑前须一并人工确认——它既不在
  `written` 里、也不算「前序已落盘」，照类一的话术清理会漏掉它。
  `safe_fs` 的缺 unlink 与 `controller` 接受截断状态两条缺陷都在本模块的 Must-preserve
  面之外，已另行立案；本模块只负责**把它变成可观测的**。

**收尾话术随 `written` 分支**：`written` 非空才说「根已非全新，重跑 init 前需人工清理
`states/`」；`written` 为空（如阶段 B 首个 `ensure_directory_no_follow` 就抛 `EACCES`）时
根仍是全新根，MUST NOT 宣称需要清理，而是把根因放在首位并报「零写入，根仍是全新根」。

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
from yd_producer.config import Config, ConfigError, LocalConfig
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
    """`init` 的拒绝理由。闭合词表（9 项），逐项可区分，MUST NOT 以异常逃逸。"""

    #: `states/` 树下存在任一普通文件（含不合命名规则的残留）。
    STATES_NOT_EMPTY = "states_not_empty"
    #: `output/` 树下存在任一名为 `DONE` 的普通文件。
    DONE_PRESENT = "done_present"
    #: 变体目录不存在 / 不是目录。
    VARIANT_MISSING = "variant_missing"
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
) -> datetime | None:
    """升序取第一个完整 cycle。

    `rawscan.judge` 抛的 `ConfigError`（配置取值域 / 请求校验 / 模式校验）**原样上抛**，
    MUST NOT 被吞成「不完整」——那会把一个配置错误伪装成「等 raw 补齐」，让运维永远重跑
    init。只有 `complete is False` 走「继续找下一个候选」（cycle 目录整体不存在**不是**
    错误，见 `rawscan.judge` 的 docstring）。
    """
    for cycle in candidates:
        if rawscan.judge(raw_root, source, cycle, config).complete:
            return cycle
    return None


# --- 编排 --------------------------------------------------------------------


def _refuse(refusal: InitRefusal, detail: str) -> InitReport:
    return InitReport(written=(), refusal=refusal, detail=detail)


def _write_failed(
    source: str,
    target: Path,
    written: list[Path],
    error: BaseException,
    *,
    possibly_partial: bool,
) -> InitReport:
    """阶段 B 的失败收尾。话术随 `written` 与失败类别分支（见模块头）。"""
    parts = [f"{source} 的首态写入 {target} 失败：{error}"]
    if possibly_partial:
        parts.append(
            f"{target} 已被排他创建但写入中途失败，可能已被部分写入"
            "（header 合法、body 截断），重跑 init 前须一并人工确认并清理"
        )
    landed = "、".join(str(path) for path in written) or "（无）"
    parts.append(f"已落盘的首态：{landed}（不回滚、不删除）")
    if written or possibly_partial:
        # 半写产物同样让根不再全新，即使它不在 `written` 里。
        parts.append("根已非全新，重跑 init 前需人工清理 `states/`")
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
            variant_dir = yd_root / getattr(config.variants, source)
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
    for source in rawscan.SOURCES:
        cycle = _first_complete_cycle(local.nwm.raw_root, source, candidates, config)
        if cycle is None:
            return _refuse(
                InitRefusal.NO_COMPLETE_RAW_CYCLE,
                f"{source} 在扫描窗 [{window_start.isoformat()}, "
                f"{now_utc.isoformat()}] 内没有完整 cycle；"
                "整体拒绝、不写任何状态，等待 raw 补齐后重跑 init",
            )
        frontier[source] = cycle

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
        # 两次调用**分别** try：`ensure_directory_no_follow` 的 `mkdir` 失败同样被
        # `safe_fs` 包成 `kind="io"`，合在一个 try 里会让「目标可能被部分写入」这句话套到
        # 一个从未被 `os.open` 过的路径上（`chmod 0o500 states/` 就是这种终态）。
        try:
            safe_fs.ensure_directory_no_follow(target_dir)
        except (OSError, safe_fs.SafeFilesystemError) as error:
            return _write_failed(source, target, written, error, possibly_partial=False)
        try:
            safe_fs.write_bytes_no_follow_exclusive(target, payloads[source])
        except FileExistsError as error:
            # 类一：`O_EXCL` 拒绝覆盖，盘上零残留。
            return _write_failed(source, target, written, error, possibly_partial=False)
        except (OSError, safe_fs.SafeFilesystemError) as error:
            # 类二：`O_EXCL` 已建成文件、`os.write` 中途失败（`safe_fs` 不 unlink）。
            possibly_partial = (
                isinstance(error, safe_fs.SafeFilesystemError) and error.kind == "io"
            )
            return _write_failed(
                source, target, written, error, possibly_partial=possibly_partial
            )
        written.append(target)

    return InitReport(
        written=tuple(written),
        refusal=None,
        detail="；".join(
            f"{source} 首轮 T={frontier[source].strftime(rawscan.CYCLE_DIR_FORMAT)}"
            for source in rawscan.SOURCES
        ),
    )
