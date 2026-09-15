"""yd structural glue: imports.

Moved bodies retain existing comments and structure.
"""

import os
import stat as stat_module
from datetime import UTC, datetime, timedelta
from pathlib import Path

from yd_producer._rawcopy_common import RawStagingError
from yd_producer.config import Config, ConfigError, RawSourceConfig
from yd_producer.rawscan import (
    CYCLE_DIR_FORMAT,
    SOURCE_DIR_NAMES,
    ScanVerdict,
    render_bundle_filename,
)

# --- 0. 形参守卫（`ConfigError` 面，不属九项 kind）---------------------------


def _absolute(value: str | os.PathLike[str], label: str) -> Path:
    """把入参路径提升为绝对路径，**与 `judge` 同法**。

    `judge` 接受相对 `raw_root` 并以 `Path.cwd()` 提升（`rawscan.py` 的同名分支），
    故 `verdict.expected_files` 恒为绝对路径。本模块重新构造源路径时 MUST 走同一次
    提升，否则一个**合法**的相对 `raw_root` 调用会被 containment 检查误拒（该误拒
    正是 fixture 里「合法的相对 `raw_root` 调用 -> 正常产出」那行 Regression row 的
    判别对象）。"要求 `raw_root` 绝对、相对即拒绝"不是等价方案：那会让该行不可满足。
    """
    try:
        path = Path(os.fspath(value))
    except TypeError as exc:
        raise ConfigError(
            f"{label} 必须是 str 或 os.PathLike，实际 {type(value).__name__}"
        ) from exc
    if path.is_absolute():
        return path
    try:
        return Path.cwd() / path
    except OSError as exc:
        raise ConfigError(
            f"无法把相对 {label} {os.fspath(value)!r} 提升为绝对路径："
            f"当前工作目录不可用（{exc}）"
        ) from exc


def _normalized(path: Path, label: str) -> Path:
    """containment 判定专用的物理归一：折叠 symlink 与 `..`。

    **只用于该判定**，MUST NOT 用它替换下游路径：`verdict.expected_files` 由 `judge`
    以 `Path.cwd()` 提升而**不**归一，把归一结果拿去重构源路径会让一个合法的相对
    `raw_root` 调用在 `_reconstruct_sources` 上被误判 `verdict-mismatch`。

    `Path.resolve()` 而不是 `os.path.abspath`：后者是纯词法折叠 `..`，跨 symlink 时
    会折出一条不同的物理路径（`tasks.md` 已按此禁用）。
    """
    try:
        return path.resolve()
    except (OSError, ValueError) as exc:
        # NUL 字节路径在这里抛裸 `ValueError`（`lstat: embedded null character`）。
        # 归 `ConfigError`：它是「调用写错了」，且与 `_absolute` 的形参守卫同面。
        raise ConfigError(f"无法规范化 {label} {path}：{exc}") from exc


def _is_same_dir(left: Path, right: Path) -> bool:
    try:
        return os.path.samestat(os.stat(left), os.stat(right))
    except (OSError, ValueError):
        # 不存在/不可 stat 的段不可能与另一侧是同一个 inode。
        return False


def _contains_by_identity(outer: Path, inner: Path) -> bool:
    """`inner` 自身或其任一**已存在**的祖先段与 `outer` 是同一个 inode。

    纯路径比较不够：`resolve()` 折叠 symlink 与 `..`，但 CPython 的 posix 实现
    **保留调用方给的非链组件大小写**，于是在大小写不敏感的卷（darwin 默认、部分
    NFS 导出）上 `<b>/NWM-RAW/work` 与 `<b>/nwm-raw` 归一后仍是两条不相交的字符串，
    而它们物理上是同一棵树。inode 身份是唯一对**任何**别名机制（大小写折叠、硬链接
    目录）都成立的判据——但作用域到**同一挂载实例**为止：`os.path.samestat` 比的是
    `(st_dev, st_ino)`，而 `st_dev` 标识超级块，同一棵树被挂成两个超级块时两侧
    `st_dev` 不等、本判据不成立。跨超级块的互含形态不在本函数作用域内（生产拓扑
    上 `raw_root` 是只读 NFS、`work_dir` 在 scratch，分属两棵树，见 `agent-ops`
    §4.2/§4.3），MUST NOT 据此在本模块新增挂载实例探测。

    走查放在**调用方一侧**（work/raw 两个根互查），MUST NOT 改用 `_reject_symlinks`
    式的目标侧逐段检查：那是 issue #71 的工具，且按设计跳过根本身（生产上 NFS 挂载
    点整体可能就是 symlink），正好漏掉这里要抓的那一段。
    """
    return any(_is_same_dir(candidate, outer) for candidate in (inner, *inner.parents))


def _validate_params(source: str, cycle: datetime, config: Config) -> RawSourceConfig:
    """形参守卫：只挡住会让下游原语抛裸异常的入参形态。

    取值域校验（`cycle.hours`、空列表等）归 `rawscan`，本模块不重复。这里挡的是
    `SOURCE_DIR_NAMES[source]` 的 `KeyError`、`getattr(config.raw, source)` 的
    `AttributeError`、以及 naive/非整点 `cycle` 会被静默写进 manifest `cycle_time`
    的形态——三者都是「调用写错了」，故归 `ConfigError` 而不占九项 kind 的名额。
    """
    if source not in SOURCE_DIR_NAMES:
        raise ConfigError(
            f"source 取值非法：{source!r}，只接受 "
            + "、".join(repr(name) for name in sorted(SOURCE_DIR_NAMES))
        )
    if not isinstance(cycle, datetime):
        raise ConfigError(f"cycle 必须是 datetime，实际 {type(cycle).__name__}")
    if cycle.utcoffset() != timedelta(0):
        raise ConfigError(
            f"cycle 必须是 tz-aware 的 UTC 时刻，实际 {cycle!r}"
            "（naive 或非 UTC 会让目录戳与 manifest 的 cycle_time 指向另一个 cycle）"
        )
    if (cycle.minute, cycle.second, cycle.microsecond) != (0, 0, 0):
        raise ConfigError(f"cycle 必须是整点，实际 {cycle!r}（分/秒/微秒必须均为 0）")
    source_config: RawSourceConfig = getattr(config.raw, source)
    if not source_config.lead_hours:
        # 空 lead 全集会让「复制集恰好 expected_files」与四键的 min/max 同时失去定义
        # （后者会漏一个裸 IndexError）。取值域校验归 `rawscan`，此处只挡住裸异常。
        path = f"raw.{source}.lead_hours"
        raise ConfigError(f"配置项 `{path}` 不得为空列表", path)
    return source_config


# --- 1. 源路径重构与 containment --------------------------------------------


def _reconstruct_sources(
    *,
    raw_root: Path,
    source: str,
    cycle: datetime,
    source_config: RawSourceConfig,
    verdict: ScanVerdict,
) -> tuple[tuple[int, Path], ...]:
    """由**形参**重新构造 (lead, 源 bundle 路径)，并与 `verdict.expected_files` 比对。

    MUST NOT 直接信任 `expected_files` 里的路径：形参与 verdict 由不同调用点提供，
    不一致意味着调用序错误。渲染面复用 `rawscan.render_bundle_filename`，MUST NOT
    在本模块重抄模式校验/渲染规则——本检查以「两处相等」为判据，自抄一份等于让检查
    比对自己、判别力归零；目录段同理复用 `rawscan.SOURCE_DIR_NAMES`。
    """
    cycle_root = (
        raw_root
        / SOURCE_DIR_NAMES[source]
        / cycle.astimezone(UTC).strftime(CYCLE_DIR_FORMAT)
    )
    pattern = source_config.bundles[0]
    config_path = f"raw.{source}.bundles"
    leads = tuple(sorted(source_config.lead_hours))
    rebuilt = tuple(
        (
            lead,
            cycle_root
            / render_bundle_filename(
                pattern, cycle_hour=cycle.hour, lead=lead, config_path=config_path
            ),
        )
        for lead in leads
    )
    if tuple(path for _, path in rebuilt) != tuple(verdict.expected_files):
        raise RawStagingError(
            "由形参重新构造的源文件清单与 verdict.expected_files 不一致"
            f"（重构 {len(rebuilt)} 项、verdict {len(verdict.expected_files)} 项）；"
            "raw_root/source/cycle/config 必须与产生该 verdict 的调用逐字相同",
            "verdict-mismatch",
        )
    # lead 轴取**集合相等**而不是「重构的每个 lead 都在 verdict 里」：后者只判一个方向，
    # 一个多出 lead 的 verdict 会照样通过，而 manifest 的 forecast_hours 由 `rebuilt`
    # 推导，于是产出的小时表比 verdict 声明的少——tasks.md:677「相等（不是包含）」与
    # :708 的三键相等同时被证伪。spec `raw-scan` :58 的 MUST 无「verdict 来自 judge」
    # 的前提，故 judge 恒不产生多余键这一事实不能用来免除本闸门。
    rebuilt_leads = {lead for lead, _ in rebuilt}
    declared_leads = set(verdict.expected_variables)
    if declared_leads != rebuilt_leads:
        raise RawStagingError(
            "verdict.expected_variables 的 lead 集合与由形参重构的 lead 集合不等"
            f"（verdict 多出 {sorted(declared_leads - rebuilt_leads)}、"
            f"缺 {sorted(rebuilt_leads - declared_leads)}）；verdict 与形参配置不同源",
            "verdict-mismatch",
        )
    for lead in rebuilt_leads:
        # 集合相等只判键，不判值。值面必须自己判形态：`None` 会在 `_build_entries`
        # 里漏一个裸 `TypeError`（不可迭代），而一个 `str` 更糟——它可迭代，会被逐
        # 字符当成变量名扇出，静默产出一份变量名全错的 manifest。
        variables = verdict.expected_variables[lead]
        if not isinstance(variables, tuple | list):
            raise RawStagingError(
                f"verdict.expected_variables 的 lead {lead} 的变量集不是 tuple/list，"
                f"实际 {type(variables).__name__}；verdict 与形参配置不同源",
                "verdict-mismatch",
            )
    return rebuilt


def _reject_symlinks(raw_root: Path, source_path: Path) -> None:
    """源路径自身或其在 `raw_root` 之下的任一祖先段是 symlink 即拒绝，不跟随。

    这里刻意**比任务 3.1 更严**：`rawscan._check` 走 `is_file()` 语义、跟随 symlink，
    故 `judge` 可能对一个 symlinked bundle 返回 `complete=True`；而本模块钉死的源不
    可变取证是 `os.lstat`（看链本身、不看目标），两者叠加会留下一个洞——链的元组不变
    而目标被换掉，取证照样通过。收口方式是**拒绝**而不是改用 `os.stat`：stat 版本要
    再补目标的 containment 检查与第二个 TOCTOU 窗口，复杂度换不来收益（NWM 经 object
    store 的 `write_bytes_atomic` 落盘，raw 树内出现 symlink 属异常形态）。该不对称是
    有意的：3.1 判「NWM 说它在」，3.2 判「yd 愿意复制它并为其身份背书」。

    `raw_root` 自身不查：它是调用方给的根（生产上 NFS 挂载点、测试里 `/tmp` 一带都
    可能整体是 symlink），查它会把合法调用一并拒掉。
    """
    try:
        segments = source_path.relative_to(raw_root).parts
    except ValueError as exc:  # 重构路径恒在 raw_root 之下，此支属防御性
        raise RawStagingError(
            f"源路径 {source_path} 不在 raw_root {raw_root} 之下", "verdict-mismatch"
        ) from exc
    current = raw_root
    for segment in segments:
        current = current / segment
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            # 不存在的段不是 symlink；缺件的归属（judge 之后被删）由复制期的 lstat
            # 以 `copy-failed` 报出，本函数不越权改写它的 kind。
            return
        except OSError as exc:
            raise RawStagingError(
                f"无法检查源路径段 {current} 的链接形态：{exc}", "copy-failed"
            ) from exc
        if stat_module.S_ISLNK(mode):
            raise RawStagingError(
                f"源路径段 {current} 是 symlink；raw 树内的 symlink 一律拒绝复制"
                "（不跟随、不为其身份背书）",
                "source-symlink",
            )


def _reject_target_symlinks(work_dir: Path, dest_path: Path) -> None:
    """Refuse any symlink component of `dest_path` below `work_dir`, without following.

    Mirrors `_reject_symlinks` on the destination side so a pre-existing
    descendant link cannot redirect copies or rollback outside the caller's
    disposable work root. `work_dir` itself is not inspected: production NFS
    mounts and test `/tmp` aliases may be the root, matching the `raw_root`
    exemption. Missing components end the walk; other `lstat` failures fail
    closed as `copy-failed`. A descendant symlink is also `copy-failed` — the
    nine-kind vocabulary is closed, and `source-symlink` stays source-side.
    Leaf `target-exists` is the later no-clobber check on an ordinary name
    that already exists; this walk runs first so a leaf link is never treated
    as a clobber of a regular file.
    """
    try:
        segments = dest_path.relative_to(work_dir).parts
    except ValueError as exc:  # 构造路径恒在 work_dir 之下，此支属防御性
        raise RawStagingError(
            f"目标路径 {dest_path} 不在 work_dir {work_dir} 之下",
            "copy-failed",
        ) from exc
    current = work_dir
    for segment in segments:
        current = current / segment
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RawStagingError(
                f"无法检查目标路径段 {current} 的链接形态：{exc}",
                "copy-failed",
            ) from exc
        if stat_module.S_ISLNK(mode):
            raise RawStagingError(
                f"目标路径段 {current} 是 symlink；work 树内的 symlink 一律拒绝写入"
                "（不跟随、不改写目标路径）",
                "copy-failed",
            )
