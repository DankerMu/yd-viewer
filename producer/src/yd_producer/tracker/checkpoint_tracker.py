# NWM@8ae9b8f2 workers/shud_runtime/runtime.py
"""SHUD 运行期 T+12 状态 checkpoint 的**观测与捕获**（任务 9.1）。

抽取自 pin 的 `_StateCheckpointTracker`：`capture_available`(:3717)、`capture_final`(:3737)、
`_capture`(:3887)、`missing_hours`(:3919)，加 `_read_cfg_ic_header_minute`(:3618) 与
`_header_minute_matches_checkpoint`(:3963)。漏采补跑那一半（`install_recovered`、
`record_recovery_outcome`、`write_manifest` 一族）不在本模块内，另行落进同一文件。

SHUD 在运行中**就地反复覆写**同一个 `<project>.cfg.ic.update`：模型时间到 720、1440、…
分钟时各写一次。因此 T+12 状态不能等 7 天跑完再取，只能在运行期观测 header 分钟、命中
即复制。撕裂读是本设计的一等公民——header 已写到 720 不代表 body 已刷完，故每次复制后
**从盘上回读副本**再校验，校验不过就删副本、该小时保持未捕获，等下一次观测重来。

对 pin 的**刻意偏离**（八条，此处即全集）：

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

pin 原语 → 本仓 `safe_fs` 的映射（无对应物的一个都不新造）：

- `_ensure_directory` → `safe_fs.ensure_directory_no_follow`
- `_regular_file_exists` + `_read_staged_bytes` → `safe_fs.read_bytes_limited_no_follow`
  （上限 `state.MAX_STATE_IC_BYTES`；不存在 / 非普通文件 / 符号链接一律判为本次无结果）
- `_copy_staged_file_no_follow` + `_write_staged_bytes` → `safe_fs.atomic_write_bytes_no_follow`
- `unlink_no_follow` → `safe_fs.unlink_no_follow`（`missing_ok=True`）
- `sha256_bytes` → `hashlib.sha256(...).hexdigest()`
- `state_ic_structure_complete` → `yd_producer.state.parse`（见上偏离 5）

`CapturedCheckpoint` 的字段裁剪（去掉 pin 的 `valid_time` / `relative_path` /
`original_shud_filename` / `checkpoint_filename` / `provenance`）是偏离 6「不写 manifest」的
直接后果，不另计一条。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from yd_producer import state
from yd_producer.store import safe_fs

__all__ = ["CapturedCheckpoint", "CheckpointTracker", "TrackerError"]

#: `safe_fs` 的两种失败形态：`SafeFilesystemError` 是 `RuntimeError` 子类而**不是**
#: `OSError`（符号链接 / 非普通文件 / 越出 containment 走这一支），裸 `OSError` 则是
#: 「父目录还不存在」这类真实缺席。两者都要接住，只接一半等于把偏离 8 开个口子。
_FS_FAILURES = (OSError, safe_fs.SafeFilesystemError)

#: 捕获产物的落点，相对 `run_dir`。
CHECKPOINT_DIR_NAME = "state_checkpoints"


class TrackerError(Exception):
    """本模块唯一对外异常类型。"""


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
        # `_FS_FAILURES` 来堵：那会连 `state.parse` 的 `ValueError` 一起吞掉，而
        # `_copy_is_intact` 正靠它做判别。
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
        try:
            safe_fs.ensure_directory_no_follow(
                self.checkpoint_dir, containment_root=self._run_dir
            )
            payload = safe_fs.read_bytes_limited_no_follow(
                self.source_path,
                max_bytes=state.MAX_STATE_IC_BYTES,
                containment_root=self._run_dir,
            )
            safe_fs.atomic_write_bytes_no_follow(
                target, payload, containment_root=self._run_dir
            )
            # **从盘上回读**，而不是复用写之前那份内存副本：后者校验的是「我读到什么」，
            # 不是「盘上是什么」，落盘半途被截断时会一路绿灯放行一个残缺的状态。
            copied = safe_fs.read_bytes_limited_no_follow(
                target,
                max_bytes=state.MAX_STATE_IC_BYTES,
                containment_root=self._run_dir,
            )
        except _FS_FAILURES:
            self._discard(target)
            return
        if not self._copy_is_intact(copied, hour=hour):
            # SHUD 就地覆写 `cfg.ic.update`，header 到了 720 不代表 body 写完。这次撕裂了，
            # 删掉这份私有半成品、该小时保持未捕获，下一次观测再来。
            self._discard(target)
            return
        self._captured[hour] = CapturedCheckpoint(
            lead_hours=hour,
            relative_minute=float(hour * 60),
            path=target,
            source_name=self.source_path.name,
            checksum=hashlib.sha256(copied).hexdigest(),
        )

    def _copy_is_intact(self, copied: bytes, *, hour: int) -> bool:
        """副本的两项校验，都对**回读到的字节**做。"""
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

    def _discard(self, target: Path) -> None:
        try:
            safe_fs.unlink_no_follow(
                target, containment_root=self._run_dir, missing_ok=True
            )
        except _FS_FAILURES:
            # 副本删不掉不改变「该小时未捕获」这一事实，也不该把一次观测炸掉；下一次观测
            # 会原子覆写同一路径重试。
            return

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


def _header_minute_of(data: bytes) -> float | None:
    """由 `.cfg.ic` 字节取 header 分钟：首行的**最后一个数值 token**。

    这条规则在本仓的唯一实现是 `state.header_minute_time`（与 `cfg_ic._header_counts` 的
    `numeric[:-1]` 同源、同一份 pin），此处 MUST NOT 另写一份：两份实现一旦漂移，轮询与
    结构检查会对「哪个 token 是 minute-time」产生分歧。
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.splitlines()
    if not lines:
        return None
    minute = state.header_minute_time(lines[0].split())
    # 非有限判定放在**这里**、也就是「读」这一步的出口：`header_minute_time` 与 pin 一样
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
