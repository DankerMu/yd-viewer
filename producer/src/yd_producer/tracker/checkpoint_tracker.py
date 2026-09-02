# NWM@8ae9b8f2 workers/shud_runtime/runtime.py
"""SHUD 运行期 T+12 状态 checkpoint 的**观测、捕获与漏采补跑**（任务 9.1 / 9.2）。

抽取自 pin 的 `_StateCheckpointTracker`：`capture_available`(:3717)、`capture_final`(:3737)、
`_capture`(:3887)、`missing_hours`(:3919)，加 `_read_cfg_ic_header_minute`(:3618) 与
`_header_minute_matches_checkpoint`(:3963)。补跑半（pin 的 `_recover_missing_state_checkpoints`
(:784-937) 与 `install_recovered` 的共同 header/body/checksum gate）按 D12 改写为独立函数
`ensure_twelve_hour_checkpoint` + 同步注入的 `RecoveryRunner`；`record_recovery_outcome`、
`recovery_outcome_summary`、`write_manifest`、`_manifest_provenance`、`_final_ic_entry` 一族
与 outer timeout / 多目标状态机按 D12 明确不移植。

SHUD 在运行中**就地反复覆写**同一个 `<project>.cfg.ic.update`：模型时间到 720、1440、…
分钟时各写一次。因此 T+12 状态不能等 7 天跑完再取，只能在运行期观测 header 分钟、命中
即复制。撕裂读是本设计的一等公民——header 已写到 720 不代表 body 已刷完，故每次复制后
**从盘上回读副本**再校验，并把盘上字节与本调用写出去的字节逐字比对；校验不过则该小时保持
未捕获。校验失败的副本**不按路径名删除**（见 R3）：留下的未验证 residue 会挡住后续观测，
整棵 work 由 #26 回收。

对 pin 的**刻意偏离**（八条，此处即**捕获半**全集；补跑半的偏离见文末 R 清单，独立编号）：

1. 目标小时来自 `Config.checkpoint_hours` 显式入参，不解析 manifest；pin 的
   `_state_checkpoint_hours`(:3923-3941) 三路 fail-open 过滤（不可解析 `continue`、≤0 与
   超预报时长静默丢、重复静默去重）改为构造期 fail closed（各抛 `TrackerError`）。副作用：
   不再有预报时长过滤，超出预报时长的小时不会被静默丢弃，而是成为**永久漏采**并原样进入
   补跑判定——配置错误应当可见。
2. `project_name` / `run_dir` 为显式入参，不走 pin `_project_name`(:4114) 的四路 fallback
   加下标兜底：观测源文件名猜错就是永远读不到 header。
3. 无轮询循环、无 `sleep` 等待、无轮询间隔配置（连带消掉 pin
   `_state_checkpoint_poll_seconds`(:3952) 的 `0.01` 秒内置默认）。「反复观测」由调用方重复
   调用 `capture_available()` 驱动，本模块只做**单次观测**。
4. 只接受相对分钟 header，不接受 pin `_header_minute_matches_checkpoint`(:3963) 的第二支
   epoch 形式。捕获产物保持相对时间头，绝对定戳属发布路径（compute-loop §9.2）；接受 epoch
   形式需要 `start_time` 与绝对时间换算，属重戳与发布的面。效果是 fail closed：epoch 形式的
   header 一律判未命中，如实进入漏采路径。
5. 副本结构校验用本仓 `state.parse`，不引入 pin 的 `state_ic_structure_complete` 与
   `expected_river_count`（前者属任务 4.2，后者的来源 work manifest 尚未落地），故本模块
   不比对 river 行数一类预期值。
6. 不写 `state_checkpoints.json`、不记补跑结局、不做末态认领（均属补跑与发布路径）；连带
   本模块**零环境变量读取**、不接任何数据库。
7. IO 原语全部复用 `yd_producer.store.safe_fs`，不移植 pin 的 staged-IO 族。
8. 异常类型收敛为单一 `TrackerError`，MUST NOT 外泄 `OSError` / `SafeFilesystemError` /
   `ValueError`（构造期参数校验除外）。

捕获侧的 pin 原语 → 本仓 `safe_fs` 的映射（无对应物的一个都不新造）：

- `_ensure_directory` → `safe_fs.ensure_directory_no_follow`
- `_regular_file_exists` + `_read_staged_bytes` → `safe_fs.read_bytes_limited_no_follow`
  （上限 `state.MAX_STATE_IC_BYTES`；不存在 / 非普通文件 / 符号链接一律判为本次无结果）
- `_copy_staged_file_no_follow` → `safe_fs.write_bytes_no_follow_exclusive`（#17 起：捕获与
  补跑安装都走 O_EXCL no-clobber，见 R3；参数替换/恢复仍用 `atomic_write_bytes_no_follow`）
- pin 的 `unlink_no_follow` → **本模块不再使用**（R3：canonical 一律不按路径名删除）
- `sha256_bytes` → `hashlib.sha256(...).hexdigest()`
- `state_ic_structure_complete` → `yd_producer.state.parse`（见上偏离 5）
- `cfg_ic_header_minute_time`(pin `packages/common/state_qc.py:629`) →
  `yd_producer.state.cfg_ic_header_minute_time`（`state/header_time.py`，任务 12.1 已落）。
  本模块**消费** master 的这一份，不在本仓再移植第二份「最后一个数值 token 即 minute-time」
  的实现；`state/**` 相对 master 零改动。非有限（`nan` / `inf`）判定不属 header 语义、属轮询
  语义，故留在本模块 `_header_minute_of` 的单一出口，不上移。

`CapturedCheckpoint` 的字段裁剪（去掉 pin 的 `valid_time` / `relative_path` /
`original_shud_filename` / `checkpoint_filename` / `provenance`）是偏离 6「不写 manifest」的
直接后果，不另计一条。

**补跑半的刻意偏离**（独立编号 R1-R8，不与捕获半的八条混计；覆盖 `RecoveryRunner` /
`ensure_twelve_hour_checkpoint` 的适配）：

R1. pin 的 `_recover_missing_state_checkpoints` 是 `SHUDRuntime` 方法、按 manifest 目标集
    循环；#17 改为模块级独立函数 + 单目标 `(12,)` 严格判等，不循环、不轮询、不提交第二个
    Slurm 作业（D12/design D12）。
R2. pin 经 `_runtime_command` 自行构造命令并 subprocess 发起；#17 改为调用方注入的同步
    `RecoveryRunner`，本模块无命令构造、无子进程、无 timeout/kill 状态机（D12）。
R3. 捕获与补跑安装都改用 `write_bytes_no_follow_exclusive`（等价 O_EXCL no-follow）取代 pin
    的覆盖式 staging copy；既有普通文件 / 目录 / 符号链接一律按未验证残留拒绝，绝不覆盖或
    删除。**#17 phase 2 追加**：创建**之后**的回读/校验失败同样不按路径名删除——O_EXCL 成功
    返回只证明「那一刻本调用创建了该 entry」，不证明「路径名现在指向的仍是它」，竞争者可以在
    此窗口 unlink/替换（pin 的 `_discard` 按名删除正是此隐患）。`safe_fs` 无 compare-and-unlink
    原语且不在本 issue 变更面内，身份不可证明 ⇒ 唯一诚实处置是保留 residue + 如实失败；
    整棵 work 的回收归 #26（A.4）。为缩小该窗口，捕获在落盘**之前**先校验读到的源字节。
R4. pin 用 `_replace_or_append` 改写补跑参数（含 `END_TIME`/`OUTPUT_DIR` 与 cfg-style 面）；
    #17 只经 `yd_producer.assemble.render_shud_parameters(content, end="0.5")` 这一个 writer
    改写 END，其余五项固定表不动（Must-preserve 2）。
R5. pin 的补跑先清整 `f012` scratch root（`_clear_recovery_scratch_root`）；#17 要求
    recovery root **调用前完全不存在**，任何形态都保留并拒绝（R3 同族，防半清）。
R6. pin 记录 per-hour outcome 字符串并写 manifest；#17 零 outcome/manifest（偏离 6 延伸），
    失败只抛带 cause 的 `TrackerError`，证据留在 recovery tree 由 whole-work owner 回收。
R7. pin 的 `install_recovered` 接受相对/epoch 两种 header 形态；#17 沿用偏离 4：candidate
    只认相对 720（`_header_minute_matches_checkpoint(..., 720.0)`）。
R8. pin 的补跑依赖 manifest（`segment_count` / 强制文件清单）；#17 以显式 `RunDirectory`
    的 exact 静态路径为输入，并在 runner 前后对初态 / forcing 做 descriptor-bound 对账。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from yd_producer import state
from yd_producer.assemble import (
    AssemblyError,
    RunDirectory,
    WorkIdentity,
    render_shud_parameters,
)
from yd_producer.forcing.direct_grid_contract import (
    MAX_DIRECT_GRID_STATION_BINDINGS,
)
from yd_producer.state import cfg_ic_header_minute_time
from yd_producer.store import safe_fs
from yd_producer.store.object_store import MAX_OBJECT_MANIFEST_BYTES

__all__ = [
    "CapturedCheckpoint",
    "CheckpointTracker",
    "RecoveryRunner",
    "TrackerError",
    "ensure_twelve_hour_checkpoint",
]

#: `safe_fs` 的两种失败形态：`SafeFilesystemError` 是 `RuntimeError` 子类而**不是**
#: `OSError`（符号链接 / 非普通文件 / 越出 containment 走这一支），裸 `OSError` 则是
#: 「父目录还不存在」这类真实缺席。两者都要接住，只接一半等于把偏离 8 开个口子。
_FS_FAILURES = (OSError, safe_fs.SafeFilesystemError)

#: 捕获产物的落点，相对 `run_dir`。
CHECKPOINT_DIR_NAME = "state_checkpoints"

#: `assemble()` 写死的 run 目录名；work 根即其父目录（A.2）。
RUN_DIRECTORY_NAME = "model"

#: 漏采补跑的专用输出根，相对 **work 根**（`RunDirectory.path.parent`）。
RECOVERY_ROOT_NAME = "state_checkpoint_recovery"

#: 补跑输出目录名（12 小时 = f012）。
RECOVERY_DIR_NAME = "f012"

#: 补跑目标小时：产品权威值。MUST NOT 在别处重新推断（D4：现场值不得在代码中猜测，
#: 此处是产品固定目标而非现场值，且 consumer 边界 fail closed）。
RECOVERY_TARGET_HOUR = 12

#: 初态 / 参数 / 候选文件的有界读上限：三者各自已有权威常量。forcing **不设**业务上限
#: ——index/CSV 只要求 descriptor-bound、no-follow 的流式摘要读到 EOF（C.1）。
_STATE_MAX_BYTES = state.MAX_STATE_IC_BYTES
_PARAMETER_MAX_BYTES = MAX_OBJECT_MANIFEST_BYTES

#: forcing CSV 的 ASCII basename 文法，与 `assemble._CSV`（写入侧唯一的 CSV 名字权威）同形；
#: 只判字符集与 `.csv` 后缀，**不发明长度 cap**——长度上限属文件系统（`NAME_MAX`），不是业务。
_CSV_BASENAME = re.compile(r"^[A-Za-z0-9_.-]+\.csv$")


class TrackerError(Exception):
    """本模块唯一对外异常类型。"""


@runtime_checkable
class RecoveryRunner(Protocol):
    """漏采补跑的同步注入 seam（逐字冻结，R1/R2）。

    调用方负责一次真实/假 SHUD 调用；本模块不构造命令、不起子进程、不提交/轮询
    Slurm 作业（job-local 归属、提交计数断言归 #26）。返回值 MUST 是 strict ``int``，
    ``0`` 是唯一成功值。
    """

    def __call__(self, *, run_directory: RunDirectory, output_dir: Path) -> int: ...


@dataclass(frozen=True, kw_only=True)
class CapturedCheckpoint:
    """一次成功捕获的记录。

    `relative_minute` 是**目标值** `float(hour * 60)`，不是观测到的 header 值（pin 的
    `targets[h]["relative_minute"]` 同此）：header 写成 `719.6` 时命中的仍是 T+12，记录
    的分钟数是 `720.0`，否则下游会把一次舍入噪声当成状态的真实时刻。
    """

    lead_hours: int
    relative_minute: float
    path: Path
    source_name: str
    checksum: str


class CheckpointTracker:
    """观测 `<project>.cfg.ic.update`，命中目标小时即捕获一份独立副本。

    三个构造参数**均无默认值**：目标小时的唯一权威是 `Config.checkpoint_hours`
    （`config.toml`），本模块 MUST NOT 写死 `12` / `720`，也不从 manifest 或预报时长反推
    （design.md D4：现场值不得在代码中猜测）。构造**不触碰文件系统**——SHUD 尚未启动、
    `run_dir` 尚不存在时构造出来的 tracker 同样是安全的。

    **调用方前置条件（本模块无法自检）**：`run_dir` MUST 是规范路径，其**任一祖先分量都不得
    是符号链接**。`safe_fs` 的每个原语都以「目录 + 不跟随符号链接」的方式逐段打开路径，且它
    在锚定 containment root 时会把 `run_dir` 自己也从根重新走一遍，所以
    `containment_root=run_dir` **并不豁免 `run_dir` 的祖先**；`/scratch → /mnt/...` 这类 HPC
    常规布局会让每一次观测都抛 `SafeFilesystemError`，被观测步骤归进「本次观测无结果」，
    于是整整一轮零捕获、`observed_header_minutes` 保持为空——与「SHUD 从没启动」逐字节相同。
    本模块**不得**自行 `resolve()`：构造期 resolve 就碰了文件系统（违上一条），惰性 resolve
    等于把符号链接根接受下来，正好废掉 `safe_fs` 要守的东西。故这是调用方契约，由作业脚本
    接线侧保证。
    """

    def __init__(
        self,
        *,
        run_dir: Path,
        project_name: str,
        checkpoint_hours: Sequence[int],
    ) -> None:
        hours = tuple(checkpoint_hours)
        # 构造期 fail closed（偏离 1）：pin 对这三类输入静默过滤，一个配置笔误就退化成
        # 「跑完没有 checkpoint 也不报错」。
        if not hours:
            raise TrackerError("checkpoint_hours 为空：没有任何目标小时可捕获")
        for hour in hours:
            if hour <= 0:
                raise TrackerError(
                    f"checkpoint_hours 含非正小时 {hour!r}：目标小时必须为正"
                )
        if len(set(hours)) != len(hours):
            raise TrackerError(f"checkpoint_hours 含重复项：{hours!r}")
        if not project_name:
            raise TrackerError("project_name 为空：观测源文件名无从构造")
        if "/" in project_name or "\\" in project_name:
            raise TrackerError(
                f"project_name 含路径分隔符：{project_name!r}；它只是文件名的前缀"
            )
        if project_name in {".", ".."}:
            raise TrackerError(f"project_name 是路径遍历分量：{project_name!r}")
        # NUL 的两条是**纯字符串检查**（不碰文件系统，本节上一条不破）：`os.stat` / `os.open`
        # 对路径里的 NUL 抛的是 `ValueError` 而不是 `OSError`，`safe_fs` 也不转译它，于是它
        # 绕过 `_FS_FAILURES` 从 `capture_available()` 直接外泄（违偏离 8）。而它**可从配置
        # 到达**——TOML 的基本字符串接受 `\u0000`。MUST NOT 靠把 `ValueError` 并进
        # `_FS_FAILURES` 来堵：契约要的是**构造期**拒绝，而那样堵会把一个可从配置到达的
        # NUL 变成观测期的静默「无结果」——每一次观测都无结果，整轮零捕获，与「SHUD 从没
        # 启动」逐字节相同。（旧注释在此写的是「会吞掉 `state.parse` 的 `ValueError`」，
        # 那条理由不成立：`_copy_is_intact` 在 `_capture` 的 `try` 之外，且它自带局部
        # `except ValueError`。）
        if "\0" in project_name:
            raise TrackerError(f"project_name 含 NUL 字节：{project_name!r}")
        if "\0" in str(run_dir):
            raise TrackerError(f"run_dir 含 NUL 字节：{str(run_dir)!r}")

        self._run_dir = Path(run_dir)
        self._project_name = project_name
        #: 目标集升序存一次，`missing_hours()` 的升序与入参书写序无关即由此而来。
        self._targets = tuple(sorted(hours))
        self._captured: dict[int, CapturedCheckpoint] = {}
        #: 观测到的每一个**与上一次不同**的 header 分钟。漏采时它是唯一的现场证据，
        #: 故只去重相邻、不做全局去重：`360 → 720 → 360` 的回退轨迹必须留痕。
        self._observed_header_minutes: list[float] = []

    # --- 只读面 ---

    @property
    def run_dir(self) -> Path:
        """调用方给出的 run 目录（构造入参原样返回，不做 resolve）。"""
        return self._run_dir

    @property
    def project_name(self) -> str:
        return self._project_name

    @property
    def targets(self) -> tuple[int, ...]:
        """目标小时集（升序）。补跑侧唯一接受 `(12,)`，与构造入参的书写序无关。"""
        return self._targets

    @property
    def source_path(self) -> Path:
        """被观测的文件；文件名由调用方显式给出，不递归搜索、不按 manifest 猜。"""
        return self._run_dir / f"{self._project_name}.cfg.ic.update"

    @property
    def checkpoint_dir(self) -> Path:
        return self._run_dir / CHECKPOINT_DIR_NAME

    @property
    def captured(self) -> Mapping[int, CapturedCheckpoint]:
        """已捕获记录的**只读视图**：调用方改不动内部表。"""
        return MappingProxyType(self._captured)

    @property
    def observed_header_minutes(self) -> tuple[float, ...]:
        return tuple(self._observed_header_minutes)

    def missing_hours(self) -> tuple[int, ...]:
        """升序的「目标集减已捕获集」——「漏采如实报告」的判据即此。"""
        return tuple(hour for hour in self._targets if hour not in self._captured)

    # --- 观测 ---

    def capture_available(self) -> None:
        """做**一次**观测：读 header 分钟，命中则捕获。不抛错、不 sleep。

        SHUD 未启动、正在覆写、文件不是普通文件——全部落到「本次观测无结果」这一支，
        既不抛错也不记录观测值（偏离 3、8）。
        """
        header_minute = self._read_header_minute(self.source_path)
        if header_minute is None:
            return
        if (
            not self._observed_header_minutes
            or self._observed_header_minutes[-1] != header_minute
        ):
            self._observed_header_minutes.append(header_minute)
        for hour in self._targets:
            if hour in self._captured:
                # 捕获是**一次性**的：晚到的同值 header MUST NOT 覆盖已捕获副本，否则一次
                # 撕裂的重写会把好副本删掉，而 `missing_hours()` 仍报空——静默数据丢失。
                continue
            if not _header_minute_matches_checkpoint(
                header_minute, relative_minute=float(hour * 60)
            ):
                continue
            self._capture(hour)

    def capture_final(self) -> None:
        """末次观测：与常规观测同判据（pin 同名语义），故直接委派。"""
        self.capture_available()

    # --- 捕获 ---

    def _capture(self, hour: int) -> None:
        target = self.checkpoint_dir / f"{self._project_name}.f{hour:03d}.cfg.ic.update"
        #: 清理权（B.3）：**本模块在本小时之后不再按路径名删除任何 canonical**。O_EXCL 成功
        #: 返回只说明「那一刻由本调用创建了该 entry」，不说明「路径名现在指向的那条 entry
        #: 仍是本调用创建的」——竞争者可以在返回之后、回读之前 unlink/替换它（`init` 侧
        #: compute-loop §7 的 `written` 记账踩的就是同一坑）。`safe_fs` 没有 compare-and-unlink
        #: 原语，也不允许为本 issue 扩面，因此身份无法证明；能证明的只有「不删」。失败时留下
        #: 未验证 residue 并如实报 missing，整棵 work 的回收归 #26（A.4）。
        try:
            safe_fs.ensure_directory_no_follow(
                self.checkpoint_dir, containment_root=self._run_dir
            )
            payload = safe_fs.read_bytes_limited_no_follow(
                self.source_path,
                max_bytes=state.MAX_STATE_IC_BYTES,
                containment_root=self._run_dir,
            )
            # 落盘**之前**先按同一判据校验读到的字节：撕裂的源根本不该在 canonical 名上留下
            # 任何痕迹（residue 会一直挡到本 attempt 结束）。这不替代下面的回读校验。
            if not self._copy_is_intact(payload, hour=hour):
                return
            try:
                safe_fs.write_bytes_no_follow_exclusive(
                    target, payload, containment_root=self._run_dir
                )
            except FileExistsError:
                # no-clobber（B.3）：规范文件名已由**别人**预置——它是未验证残留，MUST NOT
                # 覆盖、删除或采纳。该小时如实保持 missing；绝不走进任何清理分支。
                return
            # **从盘上回读**，而不是复用写之前那份内存副本：后者校验的是「我读到什么」，
            # 不是「盘上是什么」，落盘半途被截断时会一路绿灯放行一个残缺的状态。
            copied = safe_fs.read_bytes_limited_no_follow(
                target,
                max_bytes=state.MAX_STATE_IC_BYTES,
                containment_root=self._run_dir,
            )
        except _FS_FAILURES:
            return
        # 盘上字节 MUST 逐字等于本调用写出去的那份。这条比对同时就是回读校验：`payload` 已在
        # 落盘前过同一道 `_copy_is_intact`，逐字相等即蕴含结构合法，再判一遍是死 guard。
        # 换成另一份「同样合法」的状态也是外来 bytes —— MUST NOT 采纳、MUST NOT 删除。
        if copied != payload:
            return
        self._captured[hour] = CapturedCheckpoint(
            lead_hours=hour,
            relative_minute=float(hour * 60),
            path=target,
            source_name=self.source_path.name,
            checksum=hashlib.sha256(copied).hexdigest(),
        )

    def _copy_is_intact(self, copied: bytes, *, hour: int) -> bool:
        """落盘前对**读到的源字节**做两项校验：header 命中目标分钟 + 可按原生分段格式解析。"""
        header_minute = _header_minute_of(copied)
        if header_minute is None or not _header_minute_matches_checkpoint(
            header_minute, relative_minute=float(hour * 60)
        ):
            return False
        try:
            state.parse(copied)
        except ValueError:
            # 「可按原生分段格式读取」——截断 body / 缺分段列头 / 超限都在这里被拦下。
            return False
        return True

    def _read_header_minute(self, path: Path) -> float | None:
        """读 `path` 的 header 分钟；任何读不到的形态一律返回 `None`。

        对应 pin 的 `_read_cfg_ic_header_minute`(:3618)：有界读（上限
        `state.MAX_STATE_IC_BYTES`，不用无界读），header 取**首行**。
        """
        try:
            data = safe_fs.read_bytes_limited_no_follow(
                path,
                max_bytes=state.MAX_STATE_IC_BYTES,
                containment_root=self._run_dir,
            )
        except _FS_FAILURES:
            return None
        return _header_minute_of(data)


# --- 漏采补跑（任务 9.2 / design D12）----------------------------------------


def ensure_twelve_hour_checkpoint(
    *,
    tracker: CheckpointTracker,
    run_directory: RunDirectory,
    runner: RecoveryRunner,
) -> CapturedCheckpoint:
    """漏采补跑：用同一初态/forcing 确定性重跑 12 小时并采纳末态为 T+12 checkpoint。

    逐字冻结的公开 seam（design D12）。全程不提交/轮询第二个 Slurm 作业、不起子进程、
    不写 manifest / `DONE` / 正式状态（偏离 6、R6）；recovery tree 留在本 work 由
    whole-work owner 回收。成功且只有成功时返回 `CapturedCheckpoint` 并写入
    ``tracker._captured[12]``；任何失败形态（preflight、runner、candidate、输入漂移、
    restore、install）都抛 `TrackerError`，不产生 authority。
    """
    if not isinstance(tracker, CheckpointTracker):
        raise TrackerError("tracker must be a CheckpointTracker.")
    if not isinstance(run_directory, RunDirectory):
        raise TrackerError("run_directory must be a RunDirectory.")
    if not callable(runner):
        raise TrackerError("runner must be a callable RecoveryRunner.")
    if tracker.targets != (RECOVERY_TARGET_HOUR,):
        raise TrackerError(
            f"checkpoint targets must be exactly (12,), got {tracker.targets!r}"
        )
    work_root = _validate_run_directory(tracker, run_directory)

    canonical = tracker.checkpoint_dir / (
        f"{tracker.project_name}.f{RECOVERY_TARGET_HOUR:03d}.cfg.ic.update"
    )
    record = tracker.captured.get(RECOVERY_TARGET_HOUR)
    if record is not None:
        # authority（B.1）：实例记录存在时 point-of-use 复核五字段 + 盘上回读。
        _verify_captured_point_of_use(tracker, record, canonical)
        return record

    # 真正缺失（B.2）：canonical 与 recovery root 都必须完全不存在，否则是未验证残留。
    _require_absent(canonical, tracker.run_dir, "canonical checkpoint")
    recovery_root = work_root / RECOVERY_ROOT_NAME
    _require_absent(recovery_root, work_root, "recovery root")

    output_dir = recovery_root / RECOVERY_DIR_NAME
    try:
        safe_fs.ensure_directory_no_follow(output_dir, containment_root=work_root)
    except _FS_FAILURES as error:
        raise TrackerError(
            f"failed to create recovery output dir {output_dir}"
        ) from error
    before = _directory_identity(output_dir, "recovery output dir")

    # 静态输入快照（C.1）：初态可解析、全部字节有界；forcing index/CSV 流式摘要。
    snapshot = _snapshot_inputs(run_directory, work_root)

    # 临时参数（C.2）：唯一 writer、只改 END。写失败是整轮失败，runner 零调用。
    try:
        recovery_parameter = render_shud_parameters(snapshot.parameter, end="0.5")
        safe_fs.atomic_write_bytes_no_follow(
            run_directory.parameter_path,
            recovery_parameter,
            containment_root=run_directory.path,
        )
    except AssemblyError as error:
        raise TrackerError("recovery parameter rendering failed") from error
    except _FS_FAILURES as error:
        raise TrackerError("failed to write recovery parameter") from error

    primary: TrackerError | None = None
    restore_error: BaseException | None = None
    try:
        rc = runner(run_directory=run_directory, output_dir=output_dir)
        if type(rc) is not int:
            raise TrackerError(
                f"runner must return strict int, got {type(rc).__name__}"
            )
        if rc != 0:
            # 即使 output 已有 gate-valid candidate 也不得采纳（D.2）。
            raise TrackerError(f"recovery runner exited nonzero ({rc})")
        _verify_directory_identity(before, output_dir, "recovery output dir")
        payload = _read_candidate(output_dir, work_root, tracker.project_name)
    except TrackerError as error:
        primary = error
    except Exception as error:  # noqa: BLE001 — runner 抛任意普通异常都收敛（D.2）
        wrapped = TrackerError(f"recovery runner failed: {error}")
        wrapped.__cause__ = error
        primary = wrapped
    finally:
        # C.3：无论正常、非零或抛错，finally 都以原 bytes 恢复参数。
        try:
            safe_fs.atomic_write_bytes_no_follow(
                run_directory.parameter_path,
                snapshot.parameter,
                containment_root=run_directory.path,
            )
        except _FS_FAILURES as error:
            restore_error = error

    if restore_error is not None:
        if primary is not None:
            # D.2 双失败：外层 cause 链保留 primary（其自身 `__cause__` 是 runner 异常），
            # restore 异常则以确定性 note 记账——两支失败都必须可检视，丢掉任何一支都是
            # 「把一次整轮失败降级成半条线索」。note 只含类型与消息，不外泄非 TrackerError。
            combined = TrackerError("recovery failed and parameter restore also failed")
            combined.add_note(
                "parameter restore failed: "
                f"{type(restore_error).__name__}: {restore_error}"
            )
            raise combined from primary
        raise TrackerError("recovery parameter restore failed") from restore_error
    if primary is not None:
        raise primary

    # C.3：restore 后逐字读回原参数，并重新流式核对初态与 forcing。
    restored = _read_parameter(run_directory)
    if restored != snapshot.parameter:
        raise TrackerError(
            "recovery parameter restore did not reproduce original bytes"
        )
    _verify_inputs_unchanged(run_directory, work_root, snapshot)

    # D.4：O_EXCL 安装 + 从 canonical 有界回读**二次一致**（逐字等于已验证的 payload）。
    # candidate 未经验证不落盘；盘上换进来「另一份合法的状态」或「结构坏掉的状态」都是外来
    # bytes，由同一条逐字比对拦住——`payload` 已在 `_read_candidate` 跑过 header/body/尺寸
    # 全套判据，此处再判一遍属死 guard（#17 phase 2 审计第 2 条：删冗余判据，不留等价变异体）。
    # 三条失败 lane **一律不按路径名删除**：O_EXCL 成功返回之后竞争者可以把 canonical
    # unlink/换成别的 entry，此时按名删除就是删外来 bytes；`safe_fs` 无 compare-and-unlink
    # 且不许为本 issue 扩面，身份不可证明 ⇒ 唯一诚实处置是保留 residue + 整轮失败（B.4、#26）。
    _install_exclusive(canonical, tracker.run_dir, payload)
    try:
        copied = safe_fs.read_bytes_limited_no_follow(
            canonical,
            max_bytes=_STATE_MAX_BYTES,
            containment_root=tracker.run_dir,
        )
    except _FS_FAILURES as error:
        raise TrackerError("recovered checkpoint readback failed") from error
    if copied != payload:
        raise TrackerError("recovered checkpoint readback differs from installed bytes")

    recovered = CapturedCheckpoint(
        lead_hours=RECOVERY_TARGET_HOUR,
        relative_minute=720.0,
        path=canonical,
        source_name=f"{tracker.project_name}.cfg.ic.update",
        checksum=hashlib.sha256(copied).hexdigest(),
    )
    #: attempt-local authority 的唯一写入口：本函数是补跑半的唯一生产者。
    tracker._captured[RECOVERY_TARGET_HOUR] = recovered
    return recovered


def _install_exclusive(canonical: Path, root: Path, payload: bytes) -> None:
    """O_EXCL 安装 canonical（B.4）；本函数与调用方**都不按路径名删除** canonical。

    三条 lane 一律保留盘上现状：`FileExistsError` = 竞争者在 commit 窗口先建，那条 entry 不归
    本调用；其它 `_FS_FAILURES` = 符号链接 / 目录 / 父目录身份变化，同样不归本调用；写中途失败
    留下的是半成品 residue，而 `safe_fs` 没有 compare-and-unlink 可用来证明「路径名现在指向的
    仍是我建的那条」（#17 R3），故也不删——一律 `TrackerError` 且不记 `_captured`，residue 由
    #26 的 whole-work owner 回收。「路径现在存在」从来不是所有权的证据。
    """
    try:
        safe_fs.write_bytes_no_follow_exclusive(
            canonical, payload, containment_root=root
        )
    except FileExistsError as error:
        # 外来 entry 在 commit 窗口抢先出现：不覆盖、不删除（B.4）。
        raise TrackerError("canonical checkpoint appeared during recovery") from error
    except _FS_FAILURES as error:
        raise TrackerError("failed to install recovered checkpoint") from error


def _validate_run_directory(
    tracker: CheckpointTracker, run_directory: RunDirectory
) -> Path:
    """A.2：验证 `RunDirectory` 的精确路径/名字/静态文件形态，返回 work 根。

    MUST 在任何 runner 调用与任何写操作前失败；任何外指/相对/重复/非普通文件形态都
    拒绝，不做半程删除。
    """
    if not isinstance(run_directory.identity, WorkIdentity):
        raise TrackerError("run_directory.identity must be a WorkIdentity.")
    if run_directory.project_name != tracker.project_name:
        raise TrackerError(
            "run_directory.project_name differs from tracker.project_name"
        )
    if run_directory.identity.project_name != tracker.project_name:
        raise TrackerError("run_directory.identity.project_name differs from tracker")
    path = run_directory.path
    if not isinstance(path, Path) or not path.is_absolute():
        raise TrackerError("run_directory.path must be an absolute path")
    # `assemble()` 的 run 目录名是它自己写死的字面量（`registry.work_dir / "model"`），
    # work 根即其父目录。只验「绝对 + 等于 tracker.run_dir」时，伪造另一个绝对目录并把
    # tracker 与全部字段同步改过去就能过关，而 recovery root 会被安到那棵树的父目录上。
    if path.name != RUN_DIRECTORY_NAME:
        raise TrackerError(
            f"run_directory.path must be the exact `<work>/{RUN_DIRECTORY_NAME}` directory"
        )
    if path != tracker.run_dir:
        raise TrackerError("run_directory.path must exactly equal tracker.run_dir")
    try:
        fd = safe_fs.open_directory_no_follow(path)
        os.close(fd)
    except _FS_FAILURES as error:
        raise TrackerError(
            "run_directory.path is not a pre-existing no-follow directory"
        ) from error
    work_root = path.parent
    project = tracker.project_name
    expected = {
        "state_path": path / f"{project}.cfg.ic",
        "parameter_path": path / f"{project}.para",
        "forcing_index_path": path / f"{project}.tsd.forc",
    }
    for label, value in expected.items():
        actual = getattr(run_directory, label)
        if actual != value:
            raise TrackerError(f"{label} must be the exact top-level path {value}")
        _require_regular(actual, path, label)
    csvs = run_directory.forcing_csv_paths
    if not isinstance(csvs, tuple) or not csvs:
        raise TrackerError("forcing_csv_paths must be a non-empty tuple")
    if len(csvs) > MAX_DIRECT_GRID_STATION_BINDINGS:
        raise TrackerError(
            f"forcing_csv_paths exceeds {MAX_DIRECT_GRID_STATION_BINDINGS} entries"
        )
    names: list[str] = []
    for index, csv in enumerate(csvs):
        if not isinstance(csv, Path) or csv.parent != path:
            raise TrackerError(
                f"forcing_csv_paths[{index}] must be a top-level run-dir path"
            )
        name = csv.name
        if _CSV_BASENAME.fullmatch(name) is None:
            raise TrackerError(
                f"forcing_csv_paths[{index}] basename is unsafe: {name!r}"
            )
        names.append(name.casefold())
        _require_regular(csv, path, f"forcing_csv_paths[{index}]")
    if len(set(names)) != len(names):
        raise TrackerError("forcing CSV basenames must be casefold-unique")
    return work_root


def _require_regular(path: Path, root: Path, label: str) -> None:
    try:
        fd = safe_fs.open_file_no_follow(path, containment_root=root)
        os.close(fd)
    except _FS_FAILURES as error:
        raise TrackerError(
            f"{label} must be a regular no-follow file: {path}"
        ) from error


def _require_absent(path: Path, root: Path, label: str) -> None:
    """`path` 必须完全不存在；普通文件/目录/symlink/不可判定形态都是残留，保留并拒绝。"""
    try:
        safe_fs.stat_no_follow(path, containment_root=root)
    except FileNotFoundError:
        return
    except _FS_FAILURES as error:
        raise TrackerError(
            f"{label} must be absent (unverifiable shape): {path}"
        ) from error
    raise TrackerError(f"{label} must be absent, found pre-existing residue: {path}")


def _directory_identity(path: Path, label: str) -> tuple[int, int]:
    try:
        return safe_fs.directory_identity_no_follow(path)
    except _FS_FAILURES as error:
        raise TrackerError(f"{label} identity unreadable: {path}") from error


def _verify_directory_identity(
    expected: tuple[int, int], path: Path, label: str
) -> None:
    current = _directory_identity(path, label)
    if current != expected:
        raise TrackerError(f"{label} identity changed during recovery: {path}")


def _verify_captured_point_of_use(
    tracker: CheckpointTracker, record: CapturedCheckpoint, canonical: Path
) -> None:
    """B.1：既有记录的四字段复核对 + canonical 有界回读的三项判据（fixture B.1 逐条点名）。

    `checksum` 不单独判类型/空值：缺失或非串的 checksum 不可能等于回读摘要，比对支必然拦住，
    在此重复一遍只会产出一条无独立可观测面的死 guard。
    """
    if record.lead_hours != RECOVERY_TARGET_HOUR:
        raise TrackerError("captured record lead_hours must be 12")
    if record.relative_minute != 720.0:
        raise TrackerError("captured record relative_minute must be 720.0")
    if record.path != canonical:
        raise TrackerError("captured record path differs from canonical target")
    if record.source_name != tracker.source_path.name:
        raise TrackerError("captured record source_name differs from the observed file")
    try:
        data = safe_fs.read_bytes_limited_no_follow(
            canonical, max_bytes=_STATE_MAX_BYTES, containment_root=tracker.run_dir
        )
    except _FS_FAILURES as error:
        raise TrackerError(
            f"captured checkpoint no longer readable: {canonical}"
        ) from error
    # 不设独立尺寸 lane：超限回读会被下面的 `_candidate_gate`（`state.parse` 自带同一
    # `MAX_STATE_IC_BYTES` 权威上限）拒掉。
    if hashlib.sha256(data).hexdigest() != record.checksum:
        raise TrackerError("captured checkpoint checksum drifted")
    if not _candidate_gate(data, minute=_header_minute_of(data)):
        raise TrackerError("captured checkpoint failed header/body recheck")


def _candidate_gate(data: bytes, *, minute: float | None) -> bool:
    """candidate/canonical 的共同 gate：header 相对 720 + `state.parse` 成功。"""
    if minute is None or not _header_minute_matches_checkpoint(
        minute, relative_minute=720.0
    ):
        return False
    try:
        state.parse(data)
    except ValueError:
        return False
    return True


def _read_candidate(output_dir: Path, work_root: Path, project: str) -> bytes:
    """D.3：output dir 顶层精确 `<project>.cfg.ic.update` 的有界单次读 + 校验。"""
    candidate = output_dir / f"{project}.cfg.ic.update"
    try:
        data = safe_fs.read_bytes_limited_no_follow(
            candidate, max_bytes=_STATE_MAX_BYTES, containment_root=work_root
        )
    except FileNotFoundError as error:
        raise TrackerError("recovery produced no candidate state file") from error
    except _FS_FAILURES as error:
        raise TrackerError(
            "recovery candidate is not a readable regular file"
        ) from error
    # 尺寸上限不在这里重复判定：有界读到 `max_bytes + 1` 已封住内存峰值，超限由紧随其后的
    # header/`state.parse`（同一 `MAX_STATE_IC_BYTES` 权威）拒绝。
    minute = _header_minute_of(data)
    if minute is None:
        raise TrackerError("recovery candidate header is unreadable")
    if not _header_minute_matches_checkpoint(minute, relative_minute=720.0):
        raise TrackerError(f"recovery candidate header minute {minute} != 720")
    try:
        state.parse(data)
    except ValueError as error:
        raise TrackerError(
            "recovery candidate body is not a parseable native cfg.ic"
        ) from error
    return data


@dataclass(frozen=True)
class _InputSnapshot:
    parameter: bytes
    state_digest: str
    index_digest: str
    csv_digests: tuple[tuple[str, str], ...]


def _snapshot_inputs(run_directory: RunDirectory, work_root: Path) -> _InputSnapshot:
    parameter = _read_parameter(run_directory)
    try:
        state_payload = safe_fs.read_bytes_limited_no_follow(
            run_directory.state_path,
            max_bytes=_STATE_MAX_BYTES,
            containment_root=run_directory.path,
        )
    except _FS_FAILURES as error:
        raise TrackerError("initial state is not a readable regular file") from error
    if len(state_payload) > _STATE_MAX_BYTES:
        raise TrackerError("initial state exceeds the state size cap")
    try:
        state.parse(state_payload)
    except ValueError as error:
        raise TrackerError("initial state is not a parseable native cfg.ic") from error
    state_digest = hashlib.sha256(state_payload).hexdigest()
    index_digest = _stream_digest(
        run_directory.forcing_index_path, run_directory.path, "forcing index"
    )
    csv_digests = tuple(
        (
            csv.name,
            _stream_digest(csv, run_directory.path, f"forcing CSV {csv.name}"),
        )
        for csv in run_directory.forcing_csv_paths
    )
    return _InputSnapshot(
        parameter=parameter,
        state_digest=state_digest,
        index_digest=index_digest,
        csv_digests=csv_digests,
    )


def _read_parameter(run_directory: RunDirectory) -> bytes:
    try:
        data = safe_fs.read_bytes_limited_no_follow(
            run_directory.parameter_path,
            max_bytes=_PARAMETER_MAX_BYTES,
            containment_root=run_directory.path,
        )
    except _FS_FAILURES as error:
        raise TrackerError("parameter file is not a readable regular file") from error
    if len(data) > _PARAMETER_MAX_BYTES:
        raise TrackerError("parameter file exceeds the manifest size cap")
    return data


def _close_note(label: str, error: OSError) -> str:
    """次级 close 证据的**唯一**文本权威（只含类型与消息，与 D.2 restore 记账同形）。"""
    return f"{label} descriptor close also failed: {type(error).__name__}: {error}"


def _close_stream_fd(fd: int | None) -> OSError | None:
    """关掉摘要 fd（可能为 `None`），**返回**失败而不是抛出；不重试。

    返回错误对象是这条 lane 的全部要点，两个方向各踩过一次（PR #121 Round 1）：
    `finally` 里**抛出** close 失败会替换正在传播的 primary（一线证据被换掉、还外泄裸
    `OSError`）；把 close 完全**移出** `finally` 则会让 `KeyboardInterrupt` / `SystemExit`
    穿过读循环时泄漏 fd。放在 `finally` 内、只回传错误，两条同时不犯。fd 状态在 close 失败
    后不可判定，MUST NOT 重试（第二次可能关掉复用同一号码的外来 fd）。
    """
    if fd is None:
        return None
    try:
        os.close(fd)
    except OSError as error:  # 只接 `OSError`：`close` 的失败形态仅此一类
        return error
    return None


def _stream_failure(label: str, primary: Exception, secondary: OSError | None) -> TrackerError:  # fmt: skip
    """forcing 摘要的 fd 失败 -> 唯一对外类型（偏离 8）；次级证据只记账，不替换 primary。

    次级失败不许升级成 `__cause__`：外部可见的证据链 MUST 只有一条 primary。
    """
    error = TrackerError(f"{label} failed to stream: {primary}")
    if secondary is None:
        return error
    error.add_note(_close_note(label, secondary))
    return error


def _stream_digest(path: Path, root: Path, label: str) -> str:
    """descriptor-bound、no-follow 的流式 SHA-256，读到 EOF，任何 exit lane 都恰好关一次 fd。

    forcing **没有**业务体积上限（C.1「不得无界读取」指的是不得把整文件读进内存，而不是
    给合法输入编一个 cap）：摘要按 1 MiB 窗口滚动，峰值内存与文件大小无关。

    三条 exit lane 的 close 处置（PR #121 Round 1 两轮复审）：正常返回与 `except` 接住的
    open/read 失败走 `finally` 关 fd，close 的失败以 `close_error` 回传、在 `finally` 之后
    收敛成 `TrackerError`（双失败时 primary 仍是 `__cause__`、close 只以 note 记账，close-only
    时 close 错误就是 primary）；而 `KeyboardInterrupt` / `SystemExit` 这类**不被接住**的取消
    信号同样经过 `finally`，故 fd 不会泄漏，且 `sys.exception()`（3.12+，`finally` 内可见
    当前正在传播的异常）让 close 的失败以 note 挂在**原异常对象**上——原对象逐字继续传播，
    MUST NOT 被换成 `TrackerError`（取消不是领域失败），也 MUST NOT 被 close 的 `OSError` 顶掉。
    """
    fd: int | None = None
    stream_error: Exception | None = None
    try:
        fd = safe_fs.open_file_no_follow(path, containment_root=root)
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
    except _FS_FAILURES as error:
        stream_error = error
    finally:
        close_error = _close_stream_fd(fd)
        # 正常支与已被 `except` 接住的支在这里都是 `None`，只有取消支非 `None`。
        if close_error is not None and (active := sys.exception()) is not None:
            active.add_note(_close_note(label, close_error))
    if stream_error is not None:
        raise _stream_failure(label, stream_error, close_error) from stream_error
    if close_error is not None:
        raise _stream_failure(label, close_error, None) from close_error
    return digest.hexdigest()


def _verify_inputs_unchanged(
    run_directory: RunDirectory, work_root: Path, snapshot: _InputSnapshot
) -> None:
    current = _snapshot_inputs(run_directory, work_root)
    if current.state_digest != snapshot.state_digest:
        raise TrackerError("initial state changed during recovery")
    if current.index_digest != snapshot.index_digest:
        raise TrackerError("forcing index changed during recovery")
    if (
        tuple((name, digest) for name, digest in current.csv_digests)
        != snapshot.csv_digests
    ):
        raise TrackerError("forcing CSV changed during recovery")


def _header_minute_of(data: bytes) -> float | None:
    """由 `.cfg.ic` 字节取 header 分钟：首行的**最后一个数值 token**。

    这条规则在本仓的唯一实现是 `state.cfg_ic_header_minute_time`（`state/header_time.py`，
    与 `cfg_ic._header_counts` 的 `numeric[:-1]` 同源、同一份 pin），本模块**消费**它，
    此处 MUST NOT 另写一份：两份实现一旦漂移，轮询与结构检查会对「哪个 token 是
    minute-time」产生分歧。
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.splitlines()
    if not lines:
        return None
    minute = cfg_ic_header_minute_time(lines[0].split())
    # 非有限判定放在**这里**、也就是「读」这一步的出口：`cfg_ic_header_minute_time` 与 pin 一样
    # 只做裸 `float()`，`nan` / `inf` / `-inf` 都解析成功。放行下去，`round(nan)` 抛
    # `ValueError`、`round(inf)` 抛 `OverflowError`，两者都会穿透 `capture_available` 外泄
    # （违偏离 8）；记进观测轨迹同样有害——`nan != nan` 让相邻去重永不生效，轨迹被无限
    # 追加。守在出口的好处是「一个非有限的分钟就不是一个可读的分钟」这句话对**全部**调用点
    # 成立：观测与副本回读校验共用本函数，不必各自再判一次。pin 的
    # `_format_header_minute`(:3634) 把非有限检查放在第一步，正是同一条理由。撕裂的
    # `cfg.ic.update` 首行出现 `nan` / `inf` 是真实可达的：SHUD 就地覆写时数值区可能半写。
    if minute is None or not math.isfinite(minute):
        return None
    return minute


def _header_minute_matches_checkpoint(
    header_minute: float, *, relative_minute: float
) -> bool:
    """header 分钟是否命中目标（四舍五入后的**精确相等**）。

    对应 pin 的 `_header_minute_matches_checkpoint`(:3963)，只保留相对分钟这一支（epoch
    那一支是偏离 4）。判据 MUST NOT 放宽成 `<=` / `>=` / 区间 / 容差：那等于「以更晚时刻
    的版本冒充 T+12」，`m=1440` 对 `h=12` 必须判未命中。`round()` 也 MUST 留着：SHUD 写出
    的 header 不保证是整数，`719.6` 命中的仍是 T+12。
    """
    return round(header_minute) == round(relative_minute)
