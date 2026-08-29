r"""NFS 发布器：一轮成功计算的正式提交（任务 13.1，issue #24）。

治理不变量：`output/<T>/<source>/DONE` 一旦存在，`yd.rivqdown.dat` 与
`states/<source>/<T+12>.cfg.ic` 就已是完整、合约达标、node-27 可读的正式产物，且
`states/<source>/` 下 T 与 T+12 两份俱在。`DONE` 之前无正式承诺，`DONE` 之后不删除本轮
所需状态。

**零发现、零推导**：本模块不读 `config.toml`、不扫 `states/` 猜 T、不从 DAT 的列编号表
反推「变体 reach 数」（那张表正是被校验的对象，只能做内部一致性校验）。全部输入由调用方
经 :class:`PublishInputs` 交来；接线归任务 14.1。

执行序（`specs/run-controller/spec.md`「NFS 提交顺序与 DONE 语义」/
`docs/compute-loop-design.md` §11.2 逐字）：

1. 读 scratch checkpoint -> `state.parse` -> 重戳到绝对 T+12 -> **内存中**的字节
   （不回写 scratch 原文件：原文件是失败路径要回收的证据）；
2. 契约检查（scratch DAT、上一步的重戳字节、合并日志，外加 NFS 侧 `DONE(T)` 不存在）；
3. DAT 写入 `output/<T>/<source>/` 并原子 rename 为 `yd.rivqdown.dat`；
4. 重戳字节原子 rename 为 `states/<source>/<T+12>.cfg.ic`；
5. `output/<T>/<source>/DONE` 以 `O_EXCL` 原子创建（最后写）；
6. 删除 `states/<source>/` 下 cycle **严格早于** T 的合法状态文件；
7. 删除 scratch `work/<source>/<T>`。

步骤 2 失败 -> `PublishError` 且 NFS 侧零字节变更。步骤 3–5 中途失败 -> 不回滚、留无
`DONE` 的半成品（§11.2 的恢复协议：下次由任务 12.2 判定清理后整轮重跑），抛
`PublishError`。步骤 6/7 失败 -> 本轮**已完成**，抛 :class:`PublishCleanupError`（刻意
**不是** `PublishError` 的子类：14.1 的 `except PublishError` 不得把已完成轮吞成失败）。

`containment_root` 前置条件（承 issue #23 裁决 6）：`safe_fs` 会把容纳根**自身**的每个
分量重新过一遍 `O_NOFOLLOW`（`store/safe_fs.py:824-843`），而
`safe_fs._relative_parts_under_root`（`:944-960`）是纯词法 `relative_to`。故 NFS 侧的根在
:class:`PublishInputs` 入口 **一次性** `Path(yd_root).resolve()`，`output/` 与 `states/`
两棵子树的全部路径都由该已解析值派生；scratch work 侧的容纳根由调用方显式交来
（`work_root`），MUST NOT 由 `work_dir` 的父链反推。

正式文件按发布权限创建（`docs/agent-ops.md` §10：不把计算节点的 uid/gid/模式带进 NFS，
由控制器按发布权限创建）：落地方式一律是「读字节 -> 新建文件写入」，文件位
:data:`PUBLISH_FILE_MODE`，`output` 子树上本次自建的每一级目录随后显式放宽到
:data:`PUBLISH_DIR_MODE`（`ensure_directory_no_follow` 逐字拒绝放宽，见
`store/safe_fs.py:107-131`，故 umask 0o077 的现场它落地即 0o700，node-27 连穿越都做不到）。
放宽逐级、非递归，且只作用于 `output/`、`output/<T>/`、`output/<T>/<source>/` 三级——
不触碰 `states/`、`logs/`、`YD_ROOT` 自身或历史 cycle 目录。

零新增依赖：`struct`/`os`/`pathlib`/`datetime` 全在 stdlib。契约检查阶段对 DAT 只做**有界
读**（先读定长头部拿 `nc`，再读列编号表），行数由 `st_size` 算术得出，MUST NOT 把数据区
读进内存——`expected_rows` 是配置驱动的，检查阶段的无界读会把一处配置错误放大成 OOM。
步骤 3 的整读是允许且必需的（`safe_fs` 无流式写原语），其上界已由前置检查钉死的
`st_size` 等式约束：先证明大小合法，再整读，顺序不得颠倒。
"""

from __future__ import annotations

import math
import os
import stat
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from yd_producer.controller import (
    CYCLE_STRIDE,
    STATE_SUFFIX,
    DiscoveryUnreadableError,
    cycle_id,
    parse_cycle_id,
    visible_state_cycles,
)
from yd_producer.state import (
    cfg_ic_header_minute_time,
    parse,
    render,
    restamp_to_absolute_time,
    state_ic_structure_complete,
)
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    atomic_write_bytes_no_follow,
    ensure_directory_no_follow,
    open_directory_no_follow,
    read_bytes_limited_no_follow,
    read_bytes_no_follow,
    remove_tree_allow_symlinks,
    stat_no_follow,
    unlink_no_follow,
    write_bytes_no_follow_exclusive,
)

__all__ = [
    "DAT_FIXED_HEADER_BYTES",
    "PUBLISH_DIR_MODE",
    "PUBLISH_FILE_MODE",
    "PublishCleanupError",
    "PublishError",
    "PublishInputs",
    "PublishResult",
    "check_publish_contract",
    "publish",
]

#: 正式产物的文件位（`docs/agent-ops.md` §10）：node-27 以 `nwm` 身份只需读。
#: 共享组与 setgid 是现场目录策略，不由本模块设置 gid。
PUBLISH_FILE_MODE = 0o644
#: 发布目录位：node-27 必须能**穿越** `output/<T>/<source>/`。
PUBLISH_DIR_MODE = 0o755
#: v2 DAT 的定长前缀：1024 字节文本头 + `st`(float64) + `nc`(float64)。
DAT_FIXED_HEADER_BYTES = 1024 + 8 + 8
#: 文本头本身的字节数（v2 判据作用的窗口）。
_TEXT_HEADER_BYTES = 1024
#: 一个 float64 的字节数。
_FLOAT64_BYTES = 8
#: DAT 的终名（`docs/products-contract.md` §3）。
DAT_FINAL_NAME = "yd.rivqdown.dat"
#: 完成判据的终名（§4：唯一完成判据，空普通文件）。
DONE_NAME = "DONE"


class PublishError(RuntimeError):
    """`DONE` 之前的发布失败：**本轮未完成**。

    覆盖契约检查拒绝（NFS 侧零字节变更）与步骤 3–5 中途失败（留无 `DONE` 半成品，交
    任务 12.2 判定清理后整轮重跑）。调用方按「本轮失败」处理。
    """


class PublishCleanupError(RuntimeError):
    """`DONE` **之后**的清理失败：本轮**已完成**，只是旧状态/work 没删干净。

    刻意**不是** :class:`PublishError` 的子类——子类关系会让 14.1 的
    `except PublishError` 把一个已被 `DONE` 承诺的成功轮吞成失败，进而触发失败侧回收，
    删掉已完成轮的 work 证据。`done_path` 指向已写成的 `DONE`。
    """

    def __init__(self, message: str, *, done_path: Path) -> None:
        super().__init__(message)
        self.done_path = done_path


def _require_path_component(value: str, *, label: str) -> None:
    """路径分量的输入域闸（承 issue #23 裁决 2 的实测教训）。

    空串会让 `output/<T>/<source>/` 塌回 `output/<T>/`（连另一源的产物一起进入删除/覆盖
    面）；`"."` 同样塌回；`".."` 把写入与删除面抬到另一个源；含 `/` 的形态直接改写层级。
    四类一律 `ValueError`，且在任何文件系统动作之前。
    """
    if not value or value in {".", ".."} or "/" in value or "\x00" in value:
        raise ValueError(
            f"{label} 必须是单个非空路径分量（不得为 ''、'.'、'..' 或含 '/'），实得 {value!r}"
        )


def _normalize_cycle(cycle: datetime) -> datetime:
    """naive 视为 UTC、aware 转 UTC（与 `state.restamp._ensure_utc` 同向）。

    随后做 `cycle_id` -> `parse_cycle_id` 的往返自检：本模块把 T 拼进 `output/<T>/` 与
    `<T+12>.cfg.ic` 两处路径，一个不能被 `controller.parse_cycle_id` 认回来的 cycle 会写出
    一份下一轮前沿**看不见**的状态，等于当场断链。
    """
    normalized = (
        cycle.replace(tzinfo=UTC) if cycle.tzinfo is None else cycle.astimezone(UTC)
    )
    if parse_cycle_id(cycle_id(normalized)) != normalized:
        raise ValueError(
            f"cycle 必须是可由 controller.parse_cycle_id 认回的整点 cycle，实得 {cycle!r}"
        )
    return normalized


@dataclass(frozen=True)
class PublishInputs:
    """一轮发布的**全部**输入。发布器零发现：这里没有的东西，发布器不去找。

    `yd_root` 在入口一次性 `resolve()`，结果落在 :attr:`root`；`output/` 与 `states/` 的
    全部路径都由 :attr:`root` 派生（见模块头的 `containment_root` 前置条件）。
    """

    yd_root: Path
    source: str
    #: 待跑 T。
    cycle: datetime
    #: 作业产出的 DAT（scratch 侧）。
    scratch_dat: Path
    #: tracker 捕获的 T+12 checkpoint（scratch 侧，**未重戳**）。
    scratch_checkpoint: Path
    #: 本轮合并 stdout/stderr。
    merged_log: Path
    #: 本轮 scratch `work/<source>/<T>`。
    work_dir: Path
    #: work 删除的容纳根（scratch work 根）。MUST 由调用方显式交来，不由 `work_dir` 反推。
    work_root: Path
    #: 期望行数 = `config.forecast_days * 24`。
    expected_rows: int
    #: 期望列数 = `config.reach_count`。
    reach_count: int
    #: 模型变体的 reach 数（真实来源归 #20 / 14.1 接线）。
    variant_reach_count: int

    #: `Path(yd_root).resolve()`：NFS 侧全部路径与 `containment_root` 的唯一来源。
    root: Path = field(init=False)

    def __post_init__(self) -> None:
        _require_path_component(self.source, label="source")
        object.__setattr__(self, "cycle", _normalize_cycle(self.cycle))
        object.__setattr__(self, "root", Path(self.yd_root).resolve())

    @property
    def next_cycle(self) -> datetime:
        """T+12：本轮发布的状态所属 cycle（`controller.CYCLE_STRIDE`）。"""
        return self.cycle + CYCLE_STRIDE

    @property
    def output_root(self) -> Path:
        return self.root / "output"

    @property
    def cycle_output_dir(self) -> Path:
        return self.output_root / cycle_id(self.cycle)

    @property
    def source_output_dir(self) -> Path:
        return self.cycle_output_dir / self.source

    @property
    def dat_path(self) -> Path:
        return self.source_output_dir / DAT_FINAL_NAME

    @property
    def done_path(self) -> Path:
        return self.source_output_dir / DONE_NAME

    @property
    def states_dir(self) -> Path:
        return self.root / "states" / self.source

    @property
    def state_path(self) -> Path:
        return self.states_dir / f"{cycle_id(self.next_cycle)}{STATE_SUFFIX}"


@dataclass(frozen=True)
class PublishResult:
    """一轮成功发布交回的终名路径集合（供 14.1 写运行报告）。"""

    source: str
    cycle: datetime
    next_cycle: datetime
    dat_path: Path
    state_path: Path
    done_path: Path
    #: 步骤 6 实际删除的旧状态文件，按 cycle 升序。
    removed_state_files: tuple[Path, ...]
    #: 步骤 7 删除的本轮 work。
    removed_work_dir: Path


# --- 契约检查 ---


def _stat_or_none(path: Path, *, containment_root: Path) -> os.stat_result | None:
    """存在性探测。不存在返回 `None`；symlink 形态的 `SafeFilesystemError` 由调用方收敛。"""
    try:
        return stat_no_follow(path, containment_root=containment_root)
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        return None


def _check_done_absent(inputs: PublishInputs) -> None:
    """`DONE` 双闸之一：前置不存在。

    `docs/products-contract.md` §4.4 逐字「重复运行看到 `DONE` 时视为已完成，不覆盖正式
    产物」。任何类型的条目（普通文件、目录、symlink）都算「存在」——symlink 形态下
    `stat_no_follow` 抛的是 `SafeFilesystemError`，此处必须收敛为 `PublishError`，不得穿透。
    """
    done = inputs.done_path
    try:
        info = _stat_or_none(done, containment_root=inputs.root)
    except SafeFilesystemError as error:
        raise PublishError(
            f"{done} 已存在且形态不合法（{error}）；本轮不覆盖"
        ) from error
    if info is not None:
        raise PublishError(
            f"{done} 已存在（st_mode={info.st_mode:#o}）：该 source/cycle 视为已完成，不覆盖正式产物"
        )


def _check_merged_log(inputs: PublishInputs) -> None:
    """合并日志 MUST 存在、是普通文件、非空——失败时要回收的就是它。"""
    log = inputs.merged_log
    try:
        info = stat_no_follow(log)
    except FileNotFoundError as error:
        raise PublishError(f"合并日志 {log} 不存在") from error
    except SafeFilesystemError as error:
        raise PublishError(f"合并日志 {log} 不可用（{error}）") from error
    if not stat.S_ISREG(info.st_mode):
        raise PublishError(f"合并日志 {log} 不是普通文件（st_mode={info.st_mode:#o}）")
    if info.st_size <= 0:
        raise PublishError(f"合并日志 {log} 是 0 字节，等于没有日志")


def _read_dat_head(inputs: PublishInputs) -> tuple[int, int]:
    """有界读 DAT 头部，返回 `(nc, st_size)`。数据区**不**进内存。"""
    dat = inputs.scratch_dat
    try:
        info = stat_no_follow(dat)
    except FileNotFoundError as error:
        raise PublishError(f"DAT {dat} 不存在") from error
    except SafeFilesystemError as error:
        raise PublishError(f"DAT {dat} 不可用（{error}）") from error
    if not stat.S_ISREG(info.st_mode):
        raise PublishError(f"DAT {dat} 不是普通文件（st_mode={info.st_mode:#o}）")

    try:
        head = read_bytes_limited_no_follow(dat, max_bytes=DAT_FIXED_HEADER_BYTES)
    except SafeFilesystemError as error:
        raise PublishError(f"DAT {dat} 读取失败（{error}）") from error
    if len(head) < DAT_FIXED_HEADER_BYTES:
        raise PublishError(
            f"DAT {dat} 非 v2：定长头部不足 {DAT_FIXED_HEADER_BYTES} 字节（实得 {len(head)}）"
        )

    _check_v2_text_header(head[:_TEXT_HEADER_BYTES], dat=dat)

    (column_count,) = struct.unpack(
        "<d", head[_TEXT_HEADER_BYTES + _FLOAT64_BYTES : DAT_FIXED_HEADER_BYTES]
    )
    if not math.isfinite(column_count) or column_count != int(column_count):
        raise PublishError(f"DAT {dat} 的列数 {column_count!r} 不是有限整数值")
    nc = int(column_count)
    if nc <= 0:
        raise PublishError(f"DAT {dat} 的列数 {nc} 非正")
    return nc, info.st_size


def _check_v2_text_header(text_header: bytes, *, dat: Path) -> None:
    """v2 判据 = **文本头形状**：可打印 ASCII 前缀 + 其后全 NUL。

    这是 SHUD 侧 `char header[1024] = {}` + `strcpy` 的必然形态。MUST NOT 退化成「文件够
    大」或「`nc` 恰好等于 `reach_count`」：v1 布局把 `nc` 放在 offset 0，`nc == 3988` 时
    前 8 字节正是 `3988.0` 的 little-endian 表示，两种退化判据都会放行它。
    """
    first_nul = text_header.find(b"\x00")
    prefix = text_header if first_nul < 0 else text_header[:first_nul]
    tail = b"" if first_nul < 0 else text_header[first_nul:]
    if tail.strip(b"\x00"):
        raise PublishError(
            f"DAT {dat} 非 v2：1024 字节文本头在 NUL 之后又出现非 NUL 字节"
        )
    if any(byte < 0x20 or byte > 0x7E for byte in prefix):
        raise PublishError(f"DAT {dat} 非 v2：1024 字节文本头含非可打印 ASCII 字节")


def _check_dat(inputs: PublishInputs) -> None:
    """v2 布局 + 列数 + 行数。行数判据是**恰好相等**：残行一律拒绝。

    `docs/products-contract.md` §5.1 逐字「不规定残行修复」；`rSHUD/R/readout.R:41` 对残行
    只 `message` 不报错，那份宽容不得进入 producer 的写 `DONE` 闸。
    """
    dat = inputs.scratch_dat
    nc, size = _read_dat_head(inputs)
    if nc != inputs.reach_count:
        raise PublishError(
            f"DAT {dat} 的数据列数 {nc} 不等于 reach_count {inputs.reach_count}"
        )

    # 第二趟有界读：只到列编号表末尾为止，数据区 MUST NOT 进内存。
    table_end = DAT_FIXED_HEADER_BYTES + _FLOAT64_BYTES * nc
    try:
        table = read_bytes_limited_no_follow(dat, max_bytes=table_end)
    except SafeFilesystemError as error:
        raise PublishError(f"DAT {dat} 读取失败（{error}）") from error
    if len(table) < table_end:
        raise PublishError(
            f"DAT {dat} 的列编号表不完整：需要 {table_end} 字节，实得 {len(table)} 字节"
        )

    expected_size = table_end + inputs.expected_rows * (nc + 1) * _FLOAT64_BYTES
    if size != expected_size:
        raise PublishError(
            f"DAT {dat} 的数据区字节数不符：期望 {inputs.expected_rows} 行"
            f"（共 {expected_size} 字节），实得 {size} 字节；残行一律拒绝"
        )


def _restamped_bytes(inputs: PublishInputs) -> bytes:
    """步骤 1：读 checkpoint、重戳到绝对 T+12，返回**内存中**的字节。

    MUST NOT 回写 scratch 原文件：那是失败路径要回收的证据。
    """
    checkpoint = inputs.scratch_checkpoint
    try:
        doc = parse(checkpoint)
    except (ValueError, OSError) as error:
        raise PublishError(f"checkpoint {checkpoint} 无法解析（{error}）") from error
    try:
        restamped = restamp_to_absolute_time(doc, inputs.next_cycle)
    except ValueError as error:
        raise PublishError(
            f"checkpoint {checkpoint} 无法重戳到 T+12（{error}）"
        ) from error
    return render(restamped)


def _check_restamped_state(payload: bytes, inputs: PublishInputs) -> None:
    """对**重戳后**的字节做「T+12 状态可按分段格式读取」检查。

    检查对象是重戳后的文档而不是 scratch 原文件——否则一份重戳后才损坏的状态会被放行。
    时间判据与 `controller._classify_state` 逐字同构：header 的分钟时标 `round()` 后必须
    等于 `round((T+12).timestamp()/60)`，**相对分钟一律不接受**。写出去的那份状态，正是
    下一轮前沿闸门要读的那份。
    """
    if not state_ic_structure_complete(payload):
        raise PublishError("重戳后的 T+12 状态结构不完整，无法按分段格式读取")
    doc = parse(payload)
    tokens = doc.lines[doc.header_index].split()
    observed = cfg_ic_header_minute_time(tokens)
    expected_minute = round(inputs.next_cycle.timestamp() / 60)
    if observed is None or not math.isfinite(observed):
        raise PublishError(f"重戳后的 T+12 状态 header 分钟时标非有限值：{observed!r}")
    if round(observed) != expected_minute:
        raise PublishError(
            f"重戳后的 T+12 状态 header 分钟时标 {observed!r} 不对应绝对 T+12="
            f"{cycle_id(inputs.next_cycle)}（期望 {expected_minute} 分钟，"
            f"实得 {round(observed)}）；相对分钟一律不接受"
        )


def _check_positive_expectations(inputs: PublishInputs) -> None:
    """期望值正数闸。**先于**读文件：`expected_rows == 0` 会让「行数相等」在空数据区上恒真。"""
    if inputs.expected_rows <= 0:
        raise PublishError(f"expected_rows 必须为正，实得 {inputs.expected_rows}")
    if inputs.reach_count <= 0:
        raise PublishError(f"reach_count 必须为正，实得 {inputs.reach_count}")
    if inputs.variant_reach_count <= 0:
        raise PublishError(
            f"variant_reach_count 必须为正，实得 {inputs.variant_reach_count}"
        )
    if inputs.reach_count != inputs.variant_reach_count:
        raise PublishError(
            f"reach_count {inputs.reach_count} 与变体 reach 数 "
            f"{inputs.variant_reach_count} 不相等"
        )


def _check_and_restamp(inputs: PublishInputs) -> bytes:
    """DONE 前的全部契约检查，返回重戳后的状态字节。**零写入**。

    次序固定：期望值正数闸（不读文件）-> 重戳（步骤 1）-> DAT/状态/日志/`DONE` 前置。
    """
    _check_positive_expectations(inputs)
    payload = _restamped_bytes(inputs)
    _check_dat(inputs)
    _check_restamped_state(payload, inputs)
    _check_merged_log(inputs)
    _check_done_absent(inputs)
    return payload


def check_publish_contract(inputs: PublishInputs) -> None:
    """DONE 前的自身契约检查（`docs/compute-loop-design.md` §11.1）。**零写入**。

    通过时返回 `None`；任一判据不满足抛 :class:`PublishError`。只读 scratch 侧的 DAT /
    checkpoint / 合并日志，外加 NFS 侧 `DONE(T)` 的不存在前置。
    """
    _check_and_restamp(inputs)


# --- 提交 ---


def _widen_publish_dir(directory: Path, *, root: Path) -> None:
    """把本次自建的发布目录放宽到 :data:`PUBLISH_DIR_MODE`。

    `ensure_directory_no_follow` 逐字拒绝放宽（`store/safe_fs.py:107-131`：「the umask may
    further restrict a safe_fs directory, it may never loosen it」），故 umask 0o077 的现场
    它落地即 0o700。放宽方式是 fd 绑定的（`open_directory_no_follow` + `os.fchmod`），不用
    跟随 symlink 的路径式写法；原语交回的是裸 fd，必须在 `finally` 里关掉。
    对已合规的目录重复放宽是幂等的。
    """
    fd = open_directory_no_follow(directory, containment_root=root)
    try:
        os.fchmod(fd, PUBLISH_DIR_MODE)
    finally:
        os.close(fd)


def _prepare_output_dir(inputs: PublishInputs) -> None:
    """创建并放宽 `output/`、`output/<T>/`、`output/<T>/<source>/` 三级。

    `output/` 这一级在全新根上同样由本发布器补建（任务 11.1 的 init 只写 `states/`）。
    umask 0o077 下漏掉它，node-27 连 `output/` 都穿不进去，下面两级的 0o755 全部白设。
    逐级、非递归：不 walk 历史 cycle 目录，不触碰 `states/`、`logs/` 或 `YD_ROOT` 自身。
    """
    root = inputs.root
    ensure_directory_no_follow(inputs.source_output_dir, containment_root=root)
    for directory in (
        inputs.output_root,
        inputs.cycle_output_dir,
        inputs.source_output_dir,
    ):
        _widen_publish_dir(directory, root=root)


def _publish_dat(inputs: PublishInputs) -> None:
    """步骤 3：整读 scratch DAT，按发布权限新建写入并在同目录内原子 rename 为终名。

    整读的上界已由 `_check_dat` 钉死的 `st_size` 等式约束（先证明大小合法，再整读）。
    落地方式是「读字节 -> 新建文件写入」，故 scratch 侧的 uid/gid/mode 一概不随产物过来。
    """
    payload = read_bytes_no_follow(inputs.scratch_dat)
    atomic_write_bytes_no_follow(
        inputs.dat_path,
        payload,
        containment_root=inputs.root,
        mode=PUBLISH_FILE_MODE,
    )


def _publish_state(payload: bytes, inputs: PublishInputs) -> None:
    """步骤 4：重戳后的字节按发布权限新建写入并原子 rename 为 `<T+12>.cfg.ic`。"""
    atomic_write_bytes_no_follow(
        inputs.state_path,
        payload,
        containment_root=inputs.root,
        mode=PUBLISH_FILE_MODE,
    )


def _create_done(inputs: PublishInputs) -> None:
    """步骤 5：`O_EXCL` 原子创建空 `DONE`（最后写）。

    这是 `DONE` 双闸的第二道：契约检查阶段的前置探测与此处创建之间仍有窗口，`O_EXCL` 是
    该竞态的兜底。`FileExistsError` 收敛为 :class:`PublishError`，不穿透。
    """
    write_bytes_no_follow_exclusive(
        inputs.done_path,
        b"",
        containment_root=inputs.root,
        mode=PUBLISH_FILE_MODE,
    )


def _stale_state_files(inputs: PublishInputs) -> tuple[Path, ...]:
    """cycle **严格早于** T 的合法状态文件，按 cycle 升序。

    可见性判据（10 位数字、`%Y%m%d%H` 可解析、`+12h` 不溢出、`.cfg.ic` 后缀）从
    `controller.visible_state_cycles` / `parse_cycle_id` 复用，不重写一份——文件名字符串
    比较会在 `2026-08-25.cfg.ic` 这类形态上误判为「更旧」而删掉。
    边界方向：`== T` 与 `== T+12` 永不删（spec 步骤 5 的「最终保留 T 与 T+12 两份」正是这条
    的直接后果）；`> T+12` 的更晚状态也不删——那是任务 12.2 的残留集合，越界删它等于把两处
    删除面耦合起来。
    """
    cycles = sorted(
        cycle
        for cycle in visible_state_cycles(inputs.states_dir)
        if cycle < inputs.cycle
    )
    return tuple(
        inputs.states_dir / f"{cycle_id(cycle)}{STATE_SUFFIX}" for cycle in cycles
    )


def _remove_stale_states(inputs: PublishInputs) -> tuple[Path, ...]:
    """步骤 6：删除旧状态。

    删除原语是 `unlink_no_follow`：状态文件只由本发布器以「普通文件原子 rename」写入，
    遇 symlink 即 `SafeFilesystemError`（与 issue #23 裁决 6 的状态侧策略一致）。
    `containment_root` 是入口一次性解析出的 `root`。
    """
    removed = _stale_state_files(inputs)
    for path in removed:
        unlink_no_follow(path, containment_root=inputs.root, missing_ok=True)
    return removed


def _remove_work(inputs: PublishInputs) -> None:
    """步骤 7：删除本轮 scratch `work/<source>/<T>`。

    用 `remove_tree_allow_symlinks` 而不是 `rmtree_no_follow`：该树的内容按构造不可信
    （作业自己写的 raw 副本 / canonical / forcing / registry，可能含 symlink），拒 symlink
    会 permanently lock 住每一轮成功发布。容纳根是调用方交来的 `work_root`（scratch 侧），
    不是 `YD_ROOT`——`work` 不在 `YD_ROOT` 内，传 `YD_ROOT` 会被直接拒。
    """
    work_dir = inputs.work_dir
    remove_tree_allow_symlinks(
        work_dir.parent,
        work_dir.name,
        containment_root=inputs.work_root,
        missing_ok=True,
    )


def publish(inputs: PublishInputs) -> PublishResult:
    """按模块头的七步序提交一轮成功计算。

    Raises:
        PublishError: 契约检查拒绝（NFS 侧零字节变更），或步骤 3–5 中途失败（不回滚，留
            无 `DONE` 的半成品）。两种形态都表示**本轮未完成**。
        PublishCleanupError: `DONE` 已写成之后的旧状态 / work 清理失败。本轮**已完成**，
            调用方 MUST NOT 触发失败侧回收。
    """
    payload = _check_and_restamp(inputs)

    try:
        _prepare_output_dir(inputs)
        _publish_dat(inputs)
        _publish_state(payload, inputs)
    except SafeFilesystemError as error:
        raise PublishError(
            f"发布 {inputs.source}/{cycle_id(inputs.cycle)} 失败：{error}"
        ) from error
    except OSError as error:
        raise PublishError(
            f"发布 {inputs.source}/{cycle_id(inputs.cycle)} 失败：{error}"
        ) from error

    try:
        _create_done(inputs)
    except FileExistsError as error:
        raise PublishError(
            f"{inputs.done_path} 在契约检查之后被并发创建：本轮不覆盖正式产物"
        ) from error
    except SafeFilesystemError as error:
        raise PublishError(f"{inputs.done_path} 创建失败：{error}") from error
    except OSError as error:
        raise PublishError(f"{inputs.done_path} 创建失败：{error}") from error

    # --- 以下是 `DONE` 之后：本轮已完成，失败一律 `PublishCleanupError` ---
    try:
        removed = _remove_stale_states(inputs)
    except (SafeFilesystemError, OSError, DiscoveryUnreadableError) as error:
        raise PublishCleanupError(
            f"{inputs.done_path} 已写成（本轮完成），但旧状态清理失败：{error}",
            done_path=inputs.done_path,
        ) from error
    try:
        _remove_work(inputs)
    except (SafeFilesystemError, OSError) as error:
        raise PublishCleanupError(
            f"{inputs.done_path} 已写成（本轮完成），但 work 清理失败：{error}",
            done_path=inputs.done_path,
        ) from error

    return PublishResult(
        source=inputs.source,
        cycle=inputs.cycle,
        next_cycle=inputs.next_cycle,
        dat_path=inputs.dat_path,
        state_path=inputs.state_path,
        done_path=inputs.done_path,
        removed_state_files=removed,
        removed_work_dir=inputs.work_dir,
    )
