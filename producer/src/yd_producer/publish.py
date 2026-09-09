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

**scratch 侧的 symlink 策略只有一条（裁决 14）：入口解析祖先、保留叶子**。
:class:`PublishInputs` 的五个 scratch 字段（`scratch_dat`、`scratch_checkpoint`、
`merged_log`、`work_dir`、`work_root`）在 `__post_init__` 里各自做一次
`path.parent.resolve() / path.name`（`work_root` 是容纳根，整条 `resolve()`）。两条理由
各钉一半：

* **祖先必须解析**——`containment_root=None` 时 `safe_fs._anchor_for` 从 `/` 起把每一个
  祖先分量过 `O_NOFOLLOW`，故 `/scratch -> /mnt/scratch` 这类现场布局会让每一轮在
  `DONE` 之前就失败；而只有 work 一条腿走 symlink 时更糟：产物全部落地、步骤 7 抛
  :class:`PublishCleanupError`，每个成功轮都留一个无人回收的孤儿 work。
* **叶子必须保留**——整条 `resolve()` 会把一个指向 scratch 树外的 `scratch_checkpoint`
  symlink 悄悄解析成它的目标，那份外来文件就会被重戳成正式的 `<T+12>.cfg.ic`。叶子
  留在原样，`safe_fs` 的 `O_NOFOLLOW` 才能当场拒掉它。

`work_root` 与 `work_dir` MUST **一起**解析：`_relative_parts_under_root` 是纯词法
`relative_to`，只解析其中一个会让 containment 判定当场断裂，制造出一个每轮必现的新
:class:`PublishCleanupError`。

错误域不变量：`check_publish_contract` 与 `publish` 的公共边界上，逃出的异常 MUST 恰好是
:class:`PublishError`（本轮未完成）或 :class:`PublishCleanupError`（本轮已完成），
「已完成」的判据是 **`DONE` 在盘上存在**而不是「`_create_done` 返回了」。故本模块 MUST NOT
假定 `safe_fs` 把一切失败都包成 `SafeFilesystemError`：`open_file_no_follow` 对非 `ELOOP`
的 `OSError` 与 `FileNotFoundError` 是**裸抛**（`store/safe_fs.py:340-341,349-355`），
`stat_no_follow` 对 symlink 抛 `SafeFilesystemError`（它是 `RuntimeError` 而**不是**
`OSError`），`controller.DiscoveryUnreadableError` 两者都不是。每一处收敛点因此都同时列
`SafeFilesystemError` 与 `OSError`（需要专门消息的 `FileNotFoundError` 排在前面）。

正式文件按发布权限创建（`docs/agent-ops.md` §10：不把计算节点的 uid/gid/模式带进 NFS，
由控制器按发布权限创建）：落地方式一律是「读字节 -> 新建文件写入」，文件位
:data:`PUBLISH_FILE_MODE`，`output` 子树上本次自建的每一级目录随后显式放宽到
:data:`PUBLISH_DIR_MODE`（`ensure_directory_no_follow` 逐字拒绝放宽，见
`store/safe_fs.py:107-131`，故 umask 0o077 的现场它落地即 0o700，node-27 连穿越都做不到）。
放宽逐级、非递归，且只作用于 `output/`、`output/<T>/`、`output/<T>/<source>/` 三级——
不触碰 `states/`、`logs/`、`YD_ROOT` 自身或历史 cycle 目录。**放宽是「尝试」，node-27 可遍历且可读才是
要立的性质**：三级就位后逐级复 stat，并要求组或其他之中至少有一类**同时**具备 `r` 与 `x`
（判据由 `docs/products-contract.md` §8「目录遍历与读取权限」推导，见
`_is_readable_and_traversable`；它的正确性由一张**穷举真值表**守住，不由任何一组样本
mode 守住），任一级不满足即在第一处 NFS 写入之前抛 `PublishError`
（`_require_traversable`）——「本次调用之前不存在」不是可持久化的
属性，`mkdir` 与放宽之间一次 EIO/重启就会把该层级以 `0o700` 永久闩死，而其后每一轮都把它
看作「已存在」而不动。

零新增依赖：`struct`/`os`/`pathlib`/`datetime` 全在 stdlib。契约检查阶段对 DAT 只做**有界
读**（先读定长头部拿 `nc`，再读列编号表），行数由 `st_size` 算术得出；大小等式成立后以
同一 descriptor 按偏移读每行第 0 列恰 8 字节，MUST NOT 把流量列或整块数据区读进内存。
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

from yd_producer._work_claim import (
    ClaimLostError,
    WorkClaim,
    open_claimed_file,
    read_claimed_bytes,
    stat_claimed,
    validate_claim,
)
from yd_producer.controller import (
    CYCLE_STRIDE,
    STATE_SUFFIX,
    DiscoveryUnreadableError,
    cycle_id,
    parse_cycle_id,
    visible_state_cycles,
)
from yd_producer.state import (
    MAX_STATE_IC_BYTES,
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
    open_file_no_follow,
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
#: 发布目录位：node-27 必须能**穿越并读取** `output/<T>/<source>/`。
PUBLISH_DIR_MODE = 0o755
#: 组的「遍历 + 读取」位对（`0o050`）。
_GROUP_READ_TRAVERSE = stat.S_IRGRP | stat.S_IXGRP
#: 其他的「遍历 + 读取」位对（`0o005`）。
_OTHER_READ_TRAVERSE = stat.S_IROTH | stat.S_IXOTH
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


def _resolved_ancestors(path: Path) -> Path:
    """解析祖先、保留叶子（模块头的 scratch 侧 symlink 策略）。

    MUST NOT 退化成整条 `Path.resolve()`：那会把一个指向 scratch 树外的 symlink 叶子解析
    成它的目标，`safe_fs` 的 `O_NOFOLLOW` 就再也看不见那一节 symlink。
    """
    target = Path(path)
    return target.parent.resolve() / target.name


@dataclass(frozen=True)
class PublishInputs:
    """一轮发布的**全部**输入。发布器零发现：这里没有的东西，发布器不去找。

    `yd_root` 在入口一次性 `resolve()`，结果落在 :attr:`root`；`output/` 与 `states/` 的
    全部路径都由 :attr:`root` 派生（见模块头的 `containment_root` 前置条件）。

    scratch 侧的五个字段在入口按「解析祖先、保留叶子」就地归一（模块头的 symlink 策略），
    故调用方**不必**先自行 `resolve()`，但也不能指望本类保留原始字面路径。
    """

    yd_root: Path
    source: str
    #: 待跑 T。
    cycle: datetime
    #: 作业产出的 DAT（scratch 侧）。入口解析祖先、保留叶子：叶子是 symlink 时被 `safe_fs`
    #: 当场拒掉。
    scratch_dat: Path
    #: tracker 捕获的 T+12 checkpoint（scratch 侧，**未重戳**）。同样解析祖先、保留叶子——
    #: 它是唯一会变成正式 NFS 产物的 scratch 输入，叶子被解析就等于放行树外文件。
    scratch_checkpoint: Path
    #: 本轮合并 stdout/stderr。入口解析祖先、保留叶子。
    merged_log: Path
    #: 本轮 scratch `work/<source>/<T>`。入口解析祖先、保留叶子，且 MUST 与 `work_root`
    #: **一起**解析（词法 containment 判定）。
    work_dir: Path
    #: work 删除的容纳根（scratch work 根）。MUST 由调用方显式交来，不由 `work_dir` 反推；
    #: 作为容纳根整条 `resolve()`。
    work_root: Path
    #: 期望行数 = `config.forecast_days * 24`。
    expected_rows: int
    #: 期望列数 = `config.reach_count`。
    reach_count: int
    #: 模型变体的 reach 数（真实来源归 #20 / 14.1 接线）。
    variant_reach_count: int
    #: Optional frozen exact-work claim. Controller paths pass a non-None token.
    claim: WorkClaim | None = None
    #: DAT 第 0 列相对分钟步长。additive 默认 60 只保旧位置兼容；controller MUST 显式传入。
    output_interval_minutes: int = 60

    #: `Path(yd_root).resolve()`：NFS 侧全部路径与 `containment_root` 的唯一来源。
    root: Path = field(init=False)

    def __post_init__(self) -> None:
        _require_path_component(self.source, label="source")
        object.__setattr__(self, "cycle", _normalize_cycle(self.cycle))
        object.__setattr__(self, "root", Path(self.yd_root).resolve())
        for name in ("scratch_dat", "scratch_checkpoint", "merged_log", "work_dir"):
            object.__setattr__(self, name, _resolved_ancestors(getattr(self, name)))
        object.__setattr__(self, "work_root", Path(self.work_root).resolve())
        # #94 exact-work 危险删除闸：`work_dir` 必须逐字等于
        # `work_root / source / cycle_id(cycle)`（两边都已解析）。少/多一层或兄弟
        # source/cycle 构成不同的删除面，在任何 IO 之前拒绝——守卫在危险边界
        # `PublishInputs.__post_init__`，未来调用方不能绕过。
        expected_work = self.work_root / self.source / cycle_id(self.cycle)
        if self.work_dir != expected_work:
            raise ValueError(
                f"work_dir {self.work_dir} 必须逐字等于 "
                f"work_root/source/cycle_id(cycle) = {expected_work}；"
                "禁止把删除面移到别的 source/cycle 层级（#94 危险删除闸）"
            )
        if self.claim is not None:
            try:
                validate_claim(self.claim, path=self.work_dir)
            except ClaimLostError as orig:
                raise PublishError(f"发布前精确 work identity 已漂移：{orig}") from orig

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


def _scratch_stat(inputs: PublishInputs, path: Path) -> os.stat_result:
    return stat_claimed(inputs.claim, path) if inputs.claim else stat_no_follow(path)


def _scratch_read_limited(
    inputs: PublishInputs, path: Path, *, max_bytes: int
) -> bytes:
    if inputs.claim is not None:
        return read_claimed_bytes(inputs.claim, path, max_bytes=max_bytes)
    return read_bytes_limited_no_follow(path, max_bytes=max_bytes)


def _scratch_read(inputs: PublishInputs, path: Path) -> bytes:
    return (
        read_claimed_bytes(inputs.claim, path)
        if inputs.claim
        else read_bytes_no_follow(path)
    )


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

    探测本身失败（EACCES/EIO 这类裸 `OSError`）同样收敛为 `PublishError` 并 fail closed：
    此刻 `DONE` 尚未写，「未完成」是唯一正确的对外语义。
    """
    done = inputs.done_path
    try:
        info = _stat_or_none(done, containment_root=inputs.root)
    except (SafeFilesystemError, OSError) as error:
        raise PublishError(
            f"{done} 已存在或无法确认不存在（{error}）；本轮不覆盖正式产物"
        ) from error
    if info is not None:
        raise PublishError(
            f"{done} 已存在（st_mode={info.st_mode:#o}）：该 source/cycle 视为已完成，不覆盖正式产物"
        )


def _check_merged_log(inputs: PublishInputs) -> None:
    """合并日志 MUST 存在、是普通文件、非空——失败时要回收的就是它。"""
    log = inputs.merged_log
    try:
        info = _scratch_stat(inputs, log)
    except FileNotFoundError as error:
        raise PublishError(f"合并日志 {log} 不存在") from error
    except (SafeFilesystemError, OSError, ClaimLostError) as error:
        raise PublishError(f"合并日志 {log} 不可用（{error}）") from error
    if not stat.S_ISREG(info.st_mode):
        raise PublishError(f"合并日志 {log} 不是普通文件（st_mode={info.st_mode:#o}）")
    if info.st_size <= 0:
        raise PublishError(f"合并日志 {log} 是 0 字节，等于没有日志")


def _read_dat_head(inputs: PublishInputs) -> tuple[int, int]:
    """有界读 DAT 头部，返回 `(nc, st_size)`。数据区**不**进内存。"""
    dat = inputs.scratch_dat
    try:
        info = _scratch_stat(inputs, dat)
    except FileNotFoundError as error:
        raise PublishError(f"DAT {dat} 不存在") from error
    except (SafeFilesystemError, OSError, ClaimLostError) as error:
        raise PublishError(f"DAT {dat} 不可用（{error}）") from error
    if not stat.S_ISREG(info.st_mode):
        raise PublishError(f"DAT {dat} 不是普通文件（st_mode={info.st_mode:#o}）")

    try:
        head = _scratch_read_limited(inputs, dat, max_bytes=DAT_FIXED_HEADER_BYTES)
    except (SafeFilesystemError, OSError, ClaimLostError) as error:
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


def _check_dat(inputs: PublishInputs) -> int:
    """v2 布局 + 列数 + 行数 + 第 0 列相对分钟，交回**校验通过的字节数**。

    行数判据是**恰好相等**：残行一律拒绝。交回 `expected_size` 给步骤 3 整读复核
    （裁决 12）；分钟列在同一 descriptor 上按偏移各读 8 字节，不改变该语义。
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
        table = _scratch_read_limited(inputs, dat, max_bytes=table_end)
    except (SafeFilesystemError, OSError, ClaimLostError) as error:
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
    _check_dat_minute_column(inputs, nc=nc, table_end=table_end)
    return expected_size


def _open_scratch_dat(inputs: PublishInputs) -> int:
    """Claim 走 `open_claimed_file`，standalone 走 `open_file_no_follow`。"""
    if inputs.claim is not None:
        return open_claimed_file(inputs.claim, inputs.scratch_dat)
    return open_file_no_follow(inputs.scratch_dat)


def _check_dat_minute_column(inputs: PublishInputs, *, nc: int, table_end: int) -> None:
    """同一 descriptor 上按 `table_end + i*(nc+1)*8` 读每行第 0 列一个 LE float64。"""
    dat = inputs.scratch_dat
    stride = (nc + 1) * _FLOAT64_BYTES
    interval = inputs.output_interval_minutes
    fd = None
    primary: BaseException | None = None
    try:
        try:
            fd = _open_scratch_dat(inputs)
            for row_index in range(inputs.expected_rows):
                packed = os.pread(fd, _FLOAT64_BYTES, table_end + row_index * stride)
                expected = float(row_index * interval)
                if len(packed) < _FLOAT64_BYTES:
                    raise PublishError(
                        f"DAT {dat} 第 {row_index} 行相对分钟短读："
                        f"期望 {expected}，需要 {_FLOAT64_BYTES} 字节，实得 {len(packed)} 字节"
                    )
                (actual,) = struct.unpack("<d", packed)
                if not math.isfinite(actual) or actual != expected:
                    raise PublishError(
                        f"DAT {dat} 第 {row_index} 行相对分钟不符："
                        f"期望 {expected}，实得 {actual}"
                    )
        except (SafeFilesystemError, OSError, ClaimLostError) as error:
            raise PublishError(f"DAT {dat} 读取失败（{error}）") from error
    except BaseException as error:
        primary = error
        raise
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError as close_error:
                if primary is None:
                    raise PublishError(
                        f"DAT {dat} 读取失败（{close_error}）"
                    ) from close_error
                primary.add_note(
                    f"DAT descriptor close also failed: {type(close_error).__name__}: {close_error}"
                )


def _restamped_bytes(inputs: PublishInputs) -> bytes:
    """步骤 1：读 checkpoint、重戳到绝对 T+12，返回**内存中**的字节。

    MUST NOT 回写 scratch 原文件：那是失败路径要回收的证据。

    读法是 **no-follow 有界读**后再解析 `bytes`（裁决 14(a)）：`state.parse(Path)` 走的是
    `state/cfg_ic.py:504-513` 刻意保留的裸 `open()`，它**跟随** symlink，于是一份指向
    scratch 树外的 checkpoint symlink 会被重戳成正式的 `<T+12>.cfg.ic`——而同样构造在
    `scratch_dat` 上是被拒的。`parse` 的 `bytes` 分支保留 `MAX_STATE_IC_BYTES` 尺寸闸
    （`read_bytes_limited_no_follow` 多读一个哨兵字节，超界因此可判）。
    """
    checkpoint = inputs.scratch_checkpoint
    try:
        raw = _scratch_read_limited(inputs, checkpoint, max_bytes=MAX_STATE_IC_BYTES)
    except (SafeFilesystemError, OSError, ClaimLostError) as error:
        raise PublishError(f"checkpoint {checkpoint} 读取失败（{error}）") from error
    try:
        doc = parse(raw)
    except (ValueError, OSError) as error:
        raise PublishError(f"checkpoint {checkpoint} 无法解析（{error}）") from error
    try:
        restamped = restamp_to_absolute_time(doc, inputs.next_cycle)
    except (ValueError, OverflowError, OSError) as error:
        raise PublishError(
            f"checkpoint {checkpoint} 无法重戳到 T+12（{error}）"
        ) from error
    return render(restamped)


def _check_restamped_state(payload: bytes, inputs: PublishInputs) -> None:
    """对**重戳后**的字节做「T+12 状态可按分段格式读取」检查。

    时间判据与 `controller._classify_state` 同构：header 分钟 `round()` 后必须等于
    `round((T+12).timestamp()/60)`。结构闸 MUST 传权威 `reach_count`。
    """
    if not state_ic_structure_complete(
        payload, expected_river_count=inputs.reach_count
    ):
        raise PublishError(
            f"重戳后的 T+12 状态结构不完整（river 段期望 {inputs.reach_count} 行），"
            "无法按分段格式读取"
        )
    try:
        doc = parse(payload)
    except ValueError as error:  # pragma: no cover - 结构闸已先行拒绝
        raise PublishError(f"重戳后的 T+12 状态无法解析（{error}）") from error
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
    interval = inputs.output_interval_minutes
    if type(interval) is not int or interval <= 0:
        raise PublishError(f"output_interval_minutes 必须为正整数，实得 {interval!r}")
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


def _check_and_restamp(inputs: PublishInputs) -> tuple[bytes, int]:
    """DONE 前的全部契约检查，返回（重戳后的状态字节，DAT 的合法字节数）。**零写入**。

    次序固定：期望值正数闸（不读文件）-> 重戳（步骤 1）-> DAT/状态/日志/`DONE` 前置。
    """
    _check_positive_expectations(inputs)
    if inputs.claim is not None:
        try:
            validate_claim(inputs.claim, path=inputs.work_dir)
        except ClaimLostError as orig:
            raise PublishError(f"发布读取前精确 work identity 已漂移：{orig}") from orig
    payload = _restamped_bytes(inputs)
    dat_size = _check_dat(inputs)
    _check_restamped_state(payload, inputs)
    _check_merged_log(inputs)
    _check_done_absent(inputs)
    return payload, dat_size


def check_publish_contract(inputs: PublishInputs) -> None:
    """DONE 前的自身契约检查（`docs/compute-loop-design.md` §11.1）。**零写入**。

    通过时返回 `None`；任一判据不满足抛 :class:`PublishError`。只读 scratch 侧的 DAT /
    checkpoint / 合并日志，外加 NFS 侧 `DONE(T)` 的不存在前置。
    """
    _check_and_restamp(inputs)  # 交回值只有 `publish` 用得上（裁决 12 的长度复核）


# --- 提交 ---


def _widen_publish_dir(directory: Path, *, root: Path) -> None:
    """把**本次自建**的发布目录放宽到 :data:`PUBLISH_DIR_MODE`。

    `ensure_directory_no_follow` 逐字拒绝放宽（`store/safe_fs.py:107-131`：「the umask may
    further restrict a safe_fs directory, it may never loosen it」），故 umask 0o077 的现场
    它落地即 0o700。放宽方式是 fd 绑定的（`open_directory_no_follow` + `os.fchmod`），不用
    跟随 symlink 的路径式写法；原语交回的是裸 fd，必须在 `finally` 里关掉。

    高位（`S_ISGID`/sticky）MUST 保留：`chmod` 写的是整个 mode 字，直接写 `0o755` 会清掉
    setgid，其后在该目录下新建的每一个条目都不再继承共享 gid，运维手动 `chmod g+s` 的补救
    每轮都被抹掉（`docs/agent-ops.md` §10 把共享组 + setgid 列为**首选**做法）。自建目录同样
    可能从父目录继承到 setgid，故这条对「只放宽自建层级」的调用点也是必需的。
    """
    fd = open_directory_no_follow(directory, containment_root=root)
    try:
        high_bits = stat.S_IMODE(os.fstat(fd).st_mode) & ~0o777
        os.fchmod(fd, high_bits | PUBLISH_DIR_MODE)
    finally:
        os.close(fd)


def _is_readable_and_traversable(mode: int) -> bool:
    """发布目录对 node-27 是否**既可遍历又可读**。判据由消费者契约推导，不是掩码字面值。

    `products-contract.md` §8 与 `agent-ops.md` §10 并列要求目录遍历与读取，故 group
    或其他必须有一类同时具备 `r` 与 `x`。owner 位不计入（发布进程自己永远进得去）。
    验收是穷举真值表（`test_traversability_predicate_matches_an_independent_oracle`）。
    """
    return (mode & _GROUP_READ_TRAVERSE) == _GROUP_READ_TRAVERSE or (
        mode & _OTHER_READ_TRAVERSE
    ) == _OTHER_READ_TRAVERSE


def _require_traversable(directory: Path, *, root: Path) -> None:
    """发布目录的**可遍历且可读断言**（裁决 8 的 fail-closed 半边）。

    判据交给 :func:`_is_readable_and_traversable`。读法是 fd 绑定的
    （`open_directory_no_follow` + `os.fstat`），与 :func:`_widen_publish_dir` 同一高度。
    """
    fd = open_directory_no_follow(directory, containment_root=root)
    try:
        mode = stat.S_IMODE(os.fstat(fd).st_mode)
    finally:
        os.close(fd)
    if not _is_readable_and_traversable(mode):
        raise PublishError(
            f"发布目录 {directory} 不可遍历或不可读（mode={mode:#o}）：组与其他都没有一类"
            "同时具备 `r` 与 `x`，node-27 将读不到本轮产物（products-contract.md §8："
            "「目录遍历与读取权限」）；本轮不发布（已存在的层级由现场按 "
            "docs/agent-ops.md §10 放宽，本模块不改写它）"
        )


def _prepare_output_dir(inputs: PublishInputs) -> None:
    """创建并放宽 `output/`、`output/<T>/`、`output/<T>/<source>/` 三级。

    `output/` 这一级在全新根上同样由本发布器补建（任务 11.1 的 init 只写 `states/`）。
    umask 0o077 下漏掉它，node-27 连 `output/` 都穿不进去，下面两级的 0o755 全部白设。
    逐级、非递归：不 walk 历史 cycle 目录，不触碰 `states/`、`logs/` 或 `YD_ROOT` 自身。

    **先 stat 再决定**（裁决 8）：放宽面严格等于「本次调用之前**不存在**的层级」。已存在的
    层级一律不动——现场按 `docs/agent-ops.md` §10 把 `output/` 设成 `2750`（共享组 +
    setgid）时，无条件 `fchmod(0o755)` 既把一棵刻意收紧的树开成 world `r-x`，又清掉
    setgid，两者都不是「放宽」。

    **且放宽只是「尝试」，可穿越才是要立的性质**：故三级全部就位后逐级复 stat，任一级不可
    穿越即抛 :class:`PublishError`（:func:`_require_traversable`）。该断言分**两趟**跑：

    * **建目录之前**先查已存在的层级——裁决 8 要求这条在「第一处 NFS 写入之前」抛，而
      Required evidence 的「自建层级不可穿越即拒」一行还要求 `YD_ROOT` 递归快照**逐项不
      变**；只在末尾查的话，一个 `0o700` 的 `output/` 会先让下面两级被 `mkdir` 出来，快照
      当场就变了。
    * **放宽之后**再查全部三级——这一趟才是裁决 8 点名的后置断言，它盖住「本次自建但
      `fchmod` 没跑成/没跑到」这条真实失败面（首轮 EIO -> 次轮把 `0o700` 当已存在而放行）。

    两趟 MUST 共用同一个判据函数（:func:`_is_readable_and_traversable`），避免两处实现漂移。
    但两趟的判别力**不对称**，这一点按变异体 (ap) 的实测结论如实写在这里：删掉前置那趟会被
    「预置 `0o700` 的 `output/`」用例当场杀死（它要求 `YD_ROOT` 递归快照逐项不变，而只留后置
    的实现会先把下面两级 `mkdir` 出来）；而**只删后置那趟按设计存活**——后置唯一独占的场景是
    「`fchmod` 返回成功却不生效」，全项目文档无此机制，最接近的真实类比（父目录 default POSIX
    ACL clamp）已由 Known limits 路由到 M4 现场验证。故后置那趟是 belt-and-braces，MUST NOT
    为它编一段「`os.fchmod` 静默空转」的 mock 编排来凑判别器。
    """
    root = inputs.root
    levels = (
        inputs.output_root,
        inputs.cycle_output_dir,
        inputs.source_output_dir,
    )
    self_created = [
        directory for directory in levels if not _entry_exists(directory, root=root)
    ]
    for directory in levels:
        if directory not in self_created:
            _require_traversable(directory, root=root)
    ensure_directory_no_follow(inputs.source_output_dir, containment_root=root)
    for directory in self_created:
        _widen_publish_dir(directory, root=root)
    for directory in levels:
        _require_traversable(directory, root=root)


def _entry_exists(path: Path, *, root: Path) -> bool:
    """本次发布**之前**该路径上是否已有条目。

    symlink 形态（`stat_no_follow` 抛 `SafeFilesystemError`）算「已存在」：既然不是本次
    自建的，就不该被本模块改 mode——后续的 `ensure_directory_no_follow` 会按它自己的策略
    拒掉它。探测失败（裸 `OSError`）同样算「已存在」，方向上偏保守（不动别人的 mode）。
    """
    try:
        return _stat_or_none(path, containment_root=root) is not None
    except (SafeFilesystemError, OSError):
        return True


def _publish_dat(inputs: PublishInputs, expected_size: int) -> None:
    """步骤 3：整读 scratch DAT，按发布权限新建写入并在同目录内原子 rename 为终名。

    整读的上界已由 `_check_dat` 钉死的 `st_size` 等式约束（先证明大小合法，再整读）。
    落地方式是「读字节 -> 新建文件写入」，故 scratch 侧的 uid/gid/mode 一概不随产物过来。

    整读之后**复核长度**（裁决 12）：校验读的是发布前那一刻的 `st_size`，整读是另一次独立
    open，两者之间 scratch 上若有滞留/重投的作业写入，落地的就是一份带半行尾巴、从未被校验
    过的 DAT，而 `DONE` 会把它封成正式产物。零额外 IO。用 `PublishError` 而不是 `assert`：
    `assert` 在 `-O` 下整条消失。
    """
    payload = _scratch_read(inputs, inputs.scratch_dat)
    if len(payload) != expected_size:
        raise PublishError(
            f"DAT {inputs.scratch_dat} 在契约检查之后被改动：整读得 {len(payload)} 字节，"
            f"校验时是 {expected_size} 字节；不发布未被校验的字节"
        )
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


def _done_is_on_disk(inputs: PublishInputs) -> bool:
    """`DONE` 是否**已在盘上**——这是「本轮已完成」的判据，不是「`_create_done` 返回了」。

    复探原语 MUST 是裸 `os.lstat`：成功即 `True`，任何 `OSError` 即 `False`。MUST NOT
    走 `stat_no_follow`：它把 EACCES/EIO/ESTALE 包成 `SafeFilesystemError`，会把「测不
    出来」翻译成「本轮已完成」。任何条目（含 symlink、目录）都算「在盘」。
    """
    try:
        os.lstat(inputs.done_path)
    except OSError:
        return False
    return True


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
    if inputs.claim is None:
        remove_tree_allow_symlinks(
            work_dir.parent,
            work_dir.name,
            containment_root=inputs.work_root,
            missing_ok=True,
        )
        return
    validate_claim(inputs.claim, path=work_dir)
    remove_tree_allow_symlinks(
        work_dir.parent,
        work_dir.name,
        containment_root=inputs.work_root,
        missing_ok=False,
        expected_root_identity=inputs.claim.identity,
    )


def publish(inputs: PublishInputs) -> PublishResult:
    """按模块头的七步序提交一轮成功计算。

    公共边界上逃出的异常恰好是下面两个之一（模块头的错误域不变量），判据是 **`DONE` 在盘
    上存在**而不是「哪一步抛的错」。

    Raises:
        PublishError: 契约检查拒绝（NFS 侧零字节变更），或步骤 3–5 中途失败且 `DONE` 不在
            盘（不回滚，留无 `DONE` 的半成品）。两种形态都表示**本轮未完成**。
        PublishCleanupError: `DONE` 已在盘之后的任何失败——含步骤 5 自身在 `O_EXCL` 创建
            成功、其后 `fchmod`/`fsync` 失败的那道窄缝，以及步骤 6/7 的旧状态 / work 清理
            失败。本轮**已完成**，调用方 MUST NOT 触发失败侧回收。
    """
    payload, dat_size = _check_and_restamp(inputs)

    try:
        _prepare_output_dir(inputs)
        _publish_dat(inputs, dat_size)
        _publish_state(payload, inputs)
    except (SafeFilesystemError, OSError) as error:
        raise PublishError(
            f"发布 {inputs.source}/{cycle_id(inputs.cycle)} 失败：{error}"
        ) from error

    try:
        _create_done(inputs)
    except FileExistsError as error:
        # 这条臂意味着**另一个写者**建了它（`O_EXCL` 的语义），不是本轮完成：不覆盖，
        # 按未完成收敛。
        raise PublishError(
            f"{inputs.done_path} 在契约检查之后被并发创建：本轮不覆盖正式产物"
        ) from error
    except (SafeFilesystemError, OSError) as error:
        if _done_is_on_disk(inputs):
            raise PublishCleanupError(
                f"{inputs.done_path} 已在盘（本轮完成），但创建过程未正常收尾：{error}",
                done_path=inputs.done_path,
            ) from error
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
    except (ClaimLostError, SafeFilesystemError, OSError) as error:
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
