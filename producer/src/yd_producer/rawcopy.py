"""NWM raw 原件的只读复制与本轮临时 `raw-manifest.json` 生成（spec `raw-scan`，
tasks.md 任务 3.2）。

规则来源：openspec `raw-scan` 的三条 Requirement「raw 只读与临时副本」「本轮临时 raw
manifest」「manifest 语义键承接与 fail-closed」、`docs/compute-loop-design.md` §4.1
（只读 NWM 原件的硬约束）与 §7.1–7.2。落盘形态、`local_key` 布局、entry 逐变量扇出、
`metadata` 六键与累积语义的承接方式转录自 NWM pin `8ae9b8f2`（见下方逐条溯源注释），
唯一桥是 `openspec/changes/m2-producer-core/nwm-snapshot-inventory.md` §3.1。

设计约束：
- **只读源、只写 work**：本模块 MUST NOT 写、删、改、重命名 `raw_root` 之下的任何
  路径；副本与 manifest 全部落在 `work_dir` 之下，MUST NOT 触及 `YD_ROOT` 发布面。
- **fail closed 且零部分产物**：任何准入检查不过一律抛 `RawStagingError` 且此前不做
  任何写入（含不建目录）；复制期失败则清理本轮已写入的 work 侧路径。
- **不发明语义**：entry 级语义键逐条承接自源 manifest；缺失/越域即报错，MUST NOT
  以默认值或推导补齐。
- 只用 stdlib；MUST NOT 运行时 import NWM，MUST NOT 连接任何数据库。
"""

import json
import os
import stat as stat_module
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from yd_producer.config import Config, ConfigError, RawSourceConfig
from yd_producer.raw.manifest import DownloadManifest, ManifestEntry
from yd_producer.rawscan import (
    CYCLE_DIR_FORMAT,
    SOURCE_DIR_NAMES,
    ScanVerdict,
    render_bundle_filename,
)

__all__ = [
    "ERROR_KINDS",
    "MANIFEST_FILENAME",
    "SOURCE_MANIFEST_FILENAME",
    "RawStagingError",
    "StagedRaw",
    "stage_raw",
]

# yd 自产的本轮清单文件名（落在 `work_dir` 根下，不是 object-store 对象）。
MANIFEST_FILENAME = "raw-manifest.json"

# NWM 在 raw cycle 目录内落盘的源清单文件名
# （NWM@8ae9b8f2 gfs_adapter.py `_persist_manifest_metadata`:1599-1609，调用点 :774；
#  ifs_adapter.py:667 构建期一次性写入）。
SOURCE_MANIFEST_FILENAME = "manifest.json"

# entry 级 `metadata` 的承接键，恰好 pin 构建期自写的 6 个
# （NWM@8ae9b8f2 gfs_adapter.py:623-634；ifs_adapter.py 同形）。逐条承接，缺一即
# fail closed——本仓 MUST NOT 发明其中任何一个的值。
ENTRY_METADATA_KEYS: tuple[str, ...] = (
    "cycle_time",
    "valid_time",
    "bundle",
    "grib_short_name",
    "cfgrib_filter_by_keys",
    "logical_remote_url",
)

# 下载期注入的累积语义键（NWM@8ae9b8f2 gfs_adapter.py:1068-1072、
# `IdxSelection.as_metadata`:248-258）。复数键按变量收全部选择器；单数键是消费端
# `_apcp_selector_metadata`(converter.py:677) 唯一会读的那个。
IDX_SELECTORS_KEY = "idx_selectors"
IDX_SELECTOR_KEY = "idx_selector"

# 需要累积语义的变量。按**变量名**判定，MUST NOT 按 `source == "gfs"` 硬分支：
# 作用域事实（勘察清单 §3.1「R4B2 的作用域与可用性」实测）是
# `IFS_VARIABLES = ("2t","2d","10u","10v","tp","sp","ssr","str")`（ifs_adapter.py:47）
# **不含 `apcp`**、且 IFS 侧全文无 `idx_selector`/`idx_selectors`，故本闸门自然只对
# GFS 生效。IFS 的 `tp`/`ssr`/`str` 同为累积量，但 pin 未给任何累积元数据，本 issue
# **不为 IFS 发明**该语义（Non-goal，记为已知限制）。
ACCUMULATION_VARIABLES: frozenset[str] = frozenset({"apcp"})

# 累积类型的闭合取值域（NWM@8ae9b8f2 converter.py `_apcp_accumulation_type_from_
# metadata`:681-691 的合法取值）。越域即报错，MUST NOT 继承 converter:1726 的
# `or "cumulative_since_cycle"` 静默默认。
ACCUMULATION_TYPES: frozenset[str] = frozenset(
    {"cumulative_since_cycle", "interval_bucket"}
)

# 累积类型与区间范围各自的「主键 + pin 侧别名」（converter.py:683/685 与 :696）。
ACCUMULATION_TYPE_KEYS: tuple[str, ...] = ("accumulation_type", "accumulation_policy")
STEP_RANGE_KEYS: tuple[str, ...] = ("step_range", "stepRange")

# 取 `interval_bucket` 时必须一并声明区间范围的那个类型。
INTERVAL_BUCKET = "interval_bucket"

# manifest 级 forecast hours 键。源侧**只强制** `forecast_hours`——它是 converter
# `_configured_forecast_hours`(:1611-1622) 唯一读的键，缺了会回落到 :1622 的
# `sorted({entry["forecast_hour"]})`，用「实际有的」当「应该有的」，完整性检查恒为真。
# `requested_forecast_hours` MUST NOT 对源侧强制：IFS 在 pin 上从不写该键
# （ifs_adapter.py:652-666 只写三键），对它强制会让每个 IFS cycle 无条件失败。
SOURCE_FORECAST_HOURS_KEY = "forecast_hours"

# yd 自产 manifest 的 manifest 级 metadata 四键，取值由 yd 自己确定而不从源 manifest
# 转抄（gfs_adapter.py:638-650 的同名四键）。
FIRST_FORECAST_HOUR_KEY = "first_forecast_hour"
LAST_FORECAST_HOUR_KEY = "last_forecast_hour"
REQUESTED_FORECAST_HOURS_KEY = "requested_forecast_hours"

# `RawStagingError.kind` 的闭合词表，恰好九项（tasks.md 任务 3.2 fixture 钉死）。
ERROR_KINDS: frozenset[str] = frozenset(
    {
        "incomplete-verdict",
        "unsupported-layout",
        "source-symlink",
        "source-manifest",
        "verdict-mismatch",
        "accumulation-metadata",
        "source-mutated",
        "target-exists",
        "copy-failed",
    }
)

# 复制的分块大小：raw bundle 是几十至数百 MB 的 GRIB2，不整读进内存。
COPY_CHUNK_BYTES = 1024 * 1024

# 最终路径段不跟随 symlink 的 open 标志。平台缺该标志时取 0（退回普通 open）——
# 叶子与祖先段的 symlink 已由 `_reject_symlinks` 在任何打开之前逐段 `lstat` 拒绝，
# 本标志只是同一判定在系统调用层的第二道闩。
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class RawStagingError(Exception):
    """staging 失败：本轮环境不满足复制/清单生成的前置条件。

    与 `ConfigError` 的分工是「配置写错了」对「本轮环境不满足前置」：取值域校验归
    `rawscan`，本类型不承担配置校验。`kind` 取自 `ERROR_KINDS` 闭合词表，供调用方与
    测试机检。裸 `OSError`/`KeyError`/`json.JSONDecodeError` 不会从 `stage_raw` 外泄。
    """

    def __init__(self, message: str, kind: str) -> None:
        if kind not in ERROR_KINDS:
            raise ValueError(f"RawStagingError.kind 取值非法：{kind!r}")
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, kw_only=True)
class StagedRaw:
    """一次 staging 的产物。

    `copied_files` 与 `verdict.expected_files` 同序同长；`entries` 按
    (lead 升序, `variables` 声明序)。
    """

    manifest_path: Path
    copied_files: tuple[Path, ...]
    entries: tuple[ManifestEntry, ...]


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
    目录、将来的卷特性）都成立的判据。

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


# --- 2. 源 manifest 承接 ------------------------------------------------------


def _load_source_manifest(cycle_root: Path) -> DownloadManifest:
    path = cycle_root / SOURCE_MANIFEST_FILENAME
    try:
        with open(path, "rb") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise RawStagingError(
            f"源 manifest {path} 不可读：{exc}", "source-manifest"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise RawStagingError(
            f"源 manifest {path} 不可解析：{exc}", "source-manifest"
        ) from exc
    if not isinstance(payload, Mapping):
        raise RawStagingError(
            f"源 manifest {path} 的顶层不是对象，实际 {type(payload).__name__}",
            "source-manifest",
        )
    try:
        return DownloadManifest.from_dict(dict(payload))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise RawStagingError(
            f"源 manifest {path} 的结构不合 NWM DownloadManifest 形态：{exc!r}",
            "source-manifest",
        ) from exc


def _source_forecast_hours(manifest: DownloadManifest, cycle_root: Path) -> set[int]:
    """源 manifest 声明的 forecast hours 全集；缺失或非 list 即报错。"""
    metadata = manifest.metadata
    if not isinstance(metadata, Mapping) or SOURCE_FORECAST_HOURS_KEY not in metadata:
        raise RawStagingError(
            f"源 manifest {cycle_root / SOURCE_MANIFEST_FILENAME} 缺 manifest 级 "
            f"`{SOURCE_FORECAST_HOURS_KEY}`；不得回落到消费端「由实际 entry 反推应有"
            "小时表」的自证式回退",
            "source-manifest",
        )
    declared = metadata[SOURCE_FORECAST_HOURS_KEY]
    if not isinstance(declared, list):
        raise RawStagingError(
            f"源 manifest 的 `{SOURCE_FORECAST_HOURS_KEY}` 必须是 list，"
            f"实际 {type(declared).__name__}",
            "source-manifest",
        )
    hours: set[int] = set()
    for value in declared:
        if isinstance(value, bool) or not isinstance(value, int | str):
            raise RawStagingError(
                f"源 manifest 的 `{SOURCE_FORECAST_HOURS_KEY}` 含非整数项 {value!r}",
                "source-manifest",
            )
        try:
            hours.add(int(value))
        except ValueError as exc:
            raise RawStagingError(
                f"源 manifest 的 `{SOURCE_FORECAST_HOURS_KEY}` 含非整数项 {value!r}",
                "source-manifest",
            ) from exc
    return hours


def _index_source_entries(
    manifest: DownloadManifest, cycle_root: Path
) -> dict[tuple[int, str], ManifestEntry]:
    """按 (forecast_hour, variable) 索引源 entry。

    `variable` 必须先判形态再当字典键：`ManifestEntry.from_dict` 对该字段**不做**
    任何强制（`forecast_hour` 有 `int(...)`、`metadata` 有 `dict(...)`，`variable`
    原样透传），故一份外部 JSON 里的 `"variable": ["tmp2m"]` 会在建索引时让
    `dict` 求哈希抛裸 `TypeError`；此处在 `stage_raw` 的 try 块**之前**，三层
    handler 一条也接不到。见 `_check_accumulation` 的同类说明。
    """
    index: dict[tuple[int, str], ManifestEntry] = {}
    for entry in manifest.entries:
        variable = entry.variable
        if not isinstance(variable, str):
            raise RawStagingError(
                f"源 manifest {cycle_root / SOURCE_MANIFEST_FILENAME} 的 entry 的 "
                f"`variable` 不是字符串，实际 {type(variable).__name__}",
                "source-manifest",
            )
        index[(entry.forecast_hour, variable)] = entry
    return index


def _carried_metadata(
    source_entry: ManifestEntry, lead: int, variable: str
) -> dict[str, Any]:
    """从源 entry 逐条承接语义键；本仓 MUST NOT 发明其中任何一个。"""
    metadata = source_entry.metadata
    if not isinstance(metadata, Mapping):
        raise RawStagingError(
            f"源 manifest 的 (lead={lead}, variable={variable!r}) entry 无 metadata",
            "source-manifest",
        )
    carried: dict[str, Any] = {}
    for key in ENTRY_METADATA_KEYS:
        if key not in metadata:
            raise RawStagingError(
                f"源 manifest 的 (lead={lead}, variable={variable!r}) entry 缺 "
                f"metadata 键 `{key}`；六键逐条承接，缺一即停",
                "source-manifest",
            )
        carried[key] = metadata[key]

    selectors = metadata.get(IDX_SELECTORS_KEY)
    if isinstance(selectors, Mapping):
        # 复数键原样承接以保持与 pin 的 raw manifest 同形；单数键按变量取，消费端
        # `_apcp_selector_metadata`(converter.py:677) 只读单数键，故 MUST NOT 只落
        # 复数键、也 MUST NOT 把整个复数 Mapping 塞进单数键。
        carried[IDX_SELECTORS_KEY] = dict(selectors)
        selector = selectors.get(variable)
        if isinstance(selector, Mapping):
            carried[IDX_SELECTOR_KEY] = dict(selector)
    # 源侧无 `idx_selectors`（IFS 的 pin 形态）时两个 idx 键均**缺席**：写一个空
    # Mapping 等于发明一个「查过了、是空的」声明。apcp 的缺失由 R4B2 闸门另行拦截。
    return carried


def _check_accumulation(metadata: Mapping[str, Any], lead: int, variable: str) -> None:
    """R4B2：apcp 的累积语义 fail-closed（勘察清单 §3.1 同名段）。

    MUST NOT 继承 pin converter:1726 的 `or "cumulative_since_cycle"` 静默默认；
    MUST NOT 依赖 converter:678「`idx_selector` 不是 Mapping 就回退到 entry metadata
    顶层」这条兜底——yd 的落盘位置固定为单数 `idx_selector` 子 Mapping。
    """
    if variable not in ACCUMULATION_VARIABLES:
        return
    selector = metadata.get(IDX_SELECTOR_KEY)
    if not isinstance(selector, Mapping):
        raise RawStagingError(
            f"(lead={lead}, variable={variable!r}) 缺累积语义子 Mapping "
            f"`{IDX_SELECTOR_KEY}`；不得静默默认为自起报累积",
            "accumulation-metadata",
        )
    accumulation_type = None
    for key in ACCUMULATION_TYPE_KEYS:
        if selector.get(key) is not None:
            accumulation_type = selector[key]
            break
    if accumulation_type is None:
        raise RawStagingError(
            f"(lead={lead}, variable={variable!r}) 的 `{IDX_SELECTOR_KEY}` 缺 "
            + "/".join(f"`{key}`" for key in ACCUMULATION_TYPE_KEYS),
            "accumulation-metadata",
        )
    if not isinstance(accumulation_type, str):
        # 形态先于取值域：源 manifest 是外部 JSON，`"accumulation_type": ["x"]` 是
        # 合法 JSON、反序列化成 `list`，而下一行的 `x not in frozenset(...)` 要对它
        # 求哈希，于是抛裸 `TypeError`。本闸门在 `stage_raw` 的 try 块**之前**执行
        # （`_build_entries` 整段都是），三层 handler 一条也接不到，裸异常直接逃出
        # 九项闭合词表。同类出口另有 `_index_source_entries` 的 `variable`。
        # §3.1 对该字段无任何类型约束——「pin 不会写 list」是对生成器的观察，不是
        # 对 yd 所读的那份落盘 JSON 的保证。
        raise RawStagingError(
            f"(lead={lead}, variable={variable!r}) 的累积类型必须是字符串，"
            f"实际 {type(accumulation_type).__name__}",
            "accumulation-metadata",
        )
    if accumulation_type not in ACCUMULATION_TYPES:
        raise RawStagingError(
            f"(lead={lead}, variable={variable!r}) 的累积类型 {accumulation_type!r} "
            "越域，只接受 " + "、".join(sorted(ACCUMULATION_TYPES)),
            "accumulation-metadata",
        )
    if accumulation_type == INTERVAL_BUCKET and not any(
        selector.get(key) is not None for key in STEP_RANGE_KEYS
    ):
        raise RawStagingError(
            f"(lead={lead}, variable={variable!r}) 取 {INTERVAL_BUCKET!r} 但缺 "
            + "/".join(f"`{key}`" for key in STEP_RANGE_KEYS),
            "accumulation-metadata",
        )


# --- 3. entry 构造 ------------------------------------------------------------


def _local_key(source: str, cycle: datetime, filename: str) -> str:
    """object-store key 形态，**不是**文件系统路径。

    「它经 `resolve_path` 被解析成路径」这条命题走**本仓侧论证**，不作任何 pin 断言：
    本仓 `store/object_store.py` 的 `LocalObjectStore` 每一条访问都走
    `self.resolve_path(key)`（:156/186/204/215/233/246/261/306，定义 :314），而本
    issue 让 object-store 根取 `work_dir`，于是解析结果恒为
    `<work_dir>/raw/<存储身份>/<YYYYMMDDHH>/<bundle>`，位于 `work/raw/` 之下。
    （§3.1 只记载 `packages/common/object_store.py` 有 `resolve_path`(L273-285) 与
    `normalize_object_key`(L44-75) 且二者**不做大小写归一**——由此得「存储身份必须
    逐源非对称」；§3.1 **没有**任何一行记载消费端把 `local_key` 交给 `resolve_path`，
    故原先那半句是无支撑的 pin 断言，与 `manifest_uri` 同一路线改掉——issue #7
    round 2 verifier CONFIRMED/FIX_NOW。）
    形态逐字沿用 pin 的 `f"raw/{source_id}/{compact_cycle}/{bundle_filename}"`
    （gfs_adapter.py:615）；存储身份逐源非对称，复用 `rawscan.SOURCE_DIR_NAMES`。
    """
    compact_cycle = cycle.astimezone(UTC).strftime(CYCLE_DIR_FORMAT)
    return f"raw/{SOURCE_DIR_NAMES[source]}/{compact_cycle}/{filename}"


def _build_entries(
    *,
    verdict: ScanVerdict,
    rebuilt: tuple[tuple[int, Path], ...],
    source: str,
    cycle: datetime,
    source_index: dict[tuple[int, str], ManifestEntry],
) -> tuple[ManifestEntry, ...]:
    """逐变量扇出：同一 (lead, bundle) 的全部变量 entry 共享同一个 `local_key`
    （NWM@8ae9b8f2 gfs_adapter.py:611-636 —— 外层 hour 算一次 key，内层逐变量产
    entry）。顺序为 lead 升序 × `variables` 声明序。
    """
    entries: list[ManifestEntry] = []
    for lead, source_path in rebuilt:
        local_key = _local_key(source, cycle, source_path.name)
        for variable in verdict.expected_variables[lead]:
            source_entry = source_index.get((lead, variable))
            if source_entry is None:
                raise RawStagingError(
                    f"源 manifest 无 (lead={lead}, variable={variable!r}) 的 entry；"
                    "其 entry 集合无法覆盖本轮预期的 (lead, variable) 全集",
                    "source-manifest",
                )
            metadata = _carried_metadata(source_entry, lead, variable)
            _check_accumulation(metadata, lead, variable)
            entries.append(
                ManifestEntry(
                    remote_url=source_entry.remote_url,
                    local_key=local_key,
                    variable=variable,
                    forecast_hour=lead,
                    # 三者一律 `None`，逐条理由：
                    # - `expected_checksum`/`expected_size_bytes` 在 pin 的**构建期**
                    #   同样是 `None`（下载期才可能有值）；yd 复制的是已落盘的字节、
                    #   没有独立 oracle，写进去等于制造一个无人校验的声明。
                    expected_checksum=None,
                    expected_size_bytes=None,
                    metadata=metadata,
                )
            )
    return tuple(entries)


# --- 4. 复制与落盘 ------------------------------------------------------------


def _identity(path: Path) -> tuple[int, int, int, int]:
    """源不可变取证的元组：(size, mtime_ns, ino, mode)。

    取 `os.lstat`（看链本身、不看目标）且比对全元组而非内容：只比内容抓不到 mtime
    被改，也抓不到「同内容不同 inode」的整体替换。
    """
    status = os.lstat(path)
    return (status.st_size, status.st_mtime_ns, status.st_ino, status.st_mode)


def _rollback_note(failures: tuple[str, ...]) -> str:
    """回滚失败的对外文案。清理失守时**必须**有信号：不变量是无条件的「不留任何
    部分产物」，代码不能单方面把它降级成沉默的尽力而为。
    """
    return f"清理本轮 work 侧写入时有 {len(failures)} 项失败，残留仍在：" + "；".join(
        failures
    )


class _Written:
    """本轮 work 侧写入的账本，供失败清理用（不留半套 raw）。"""

    def __init__(self) -> None:
        self.files: list[Path] = []
        self.dirs: list[Path] = []

    @staticmethod
    def _remove(remove: Any, path: Path, failures: list[str]) -> None:
        try:
            remove(path)
        except FileNotFoundError:
            # 账本按「走查时不存在的祖先段」反向多记（见 `_ensure_dir`），这些路径
            # 本轮可能根本没被建出来。它们不是残留，记成失败会让消息反向说谎。
            pass
        except Exception as exc:  # noqa: BLE001 —— 见 `rollback` 的不抛保证
            failures.append(f"{path}（{exc!r}）")

    def rollback(self) -> tuple[str, ...]:
        """清理本轮 work 侧写入；**保证不抛**，把失败逐条返回给调用方外抛。

        为什么是「保证不抛」而不是「多吞几种异常」：`rollback` 在三个 handler 里都
        运行在**已有异常正在外抛**的上下文中，它自己抛出的任何异常会**替换**那个
        异常——round-2 verifier 实测过一条纯入参路径（NUL 字节的 `work_dir` 让
        `os.rmdir` 抛裸 `ValueError`），裸异常因此顶掉正在构造的 `RawStagingError`
        并逃出九项闭合词表；三层 handler 一条也拦不住 `rollback` 自己。收口点因此
        只能在 `rollback` 内部，而不是在某一层 handler 上加一条 `except`。

        与之配对的是**不静默**：吞掉失败但不报告，等于把无条件的「不留任何部分
        产物」私自降级成尽力而为，且让 tier-2 的「已清理本轮 work 侧写入」变成假
        消息。故失败以清单返回，三个 handler 各自把它带进外抛的异常。

        `BaseException` 不吞：清理途中的 Ctrl-C MUST 照常传播。
        """
        failures: list[str] = []
        for path in reversed(self.files):
            self._remove(os.unlink, path, failures)
        for path in sorted(
            set(self.dirs), key=lambda item: len(item.parts), reverse=True
        ):
            self._remove(os.rmdir, path, failures)
        return tuple(failures)


def _ensure_dir(directory: Path, written: _Written) -> None:
    missing: list[Path] = []
    probe = directory
    while not probe.exists():
        missing.append(probe)
        if probe.parent == probe:
            # 文件系统根：`probe.parent == probe` 时再上溯就是死循环。该支要求根
            # 本身 `exists()` 为假，实际不可达（`while` 先退出），但它一旦成立就会
            # 把根登记进账本——`rollback` 的 `rmdir("/")` 只会以 EBUSY/EACCES 落进
            # 失败清单，不会删掉任何东西。与下方账本语义同源，一并记在此处。
            break
        probe = probe.parent
    # 账本**先于**效果登记：`mkdir(parents=True)` 是多步的，中途失败（ENOSPC/EDQUOT/
    # 并发 rmdir）会留下已建的祖先段，而失败路径不回到这里，账本就永远收不到它们。
    # 代价与其边界（round-2 verifier 证伪了原先的落地理由，这里换成正确的那条）：
    # 账本记的是「走查时不存在的段」而不是「本轮创建的段」，两者在走查与 `mkdir`
    # 之间的窗口里可能发散——窗口内被**别人**创建的同名目录会被 `rollback` 删掉。
    # 原注释写的「反向多记是安全的：`rmdir` 只会吞掉 OSError」论证的是「不会抛」，
    # 而风险是「会删不是本轮建的」，两个命题不同，故不成立。真正的边界是 fixture
    # 把 `work_dir` 定义为一次性隔离单元：窗口内的外来写入者在模型之外。收紧成
    # 「本轮创建的」需要 `mkdir` 逐级自建（放弃 `parents=True`），归组 12 的
    # work-dir 生命周期一并处理。
    written.dirs.extend(missing)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RawStagingError(
            f"无法创建目标目录 {directory}：{exc}", "copy-failed"
        ) from exc


def _copy_one(source_path: Path, target: Path, written: _Written) -> None:
    """复制一个 bundle：前后各取一次 `lstat` 元组，中间以 O_EXCL 写目标。"""
    try:
        before = _identity(source_path)
    except OSError as exc:
        raise RawStagingError(
            f"源文件 {source_path} 不可访问：{exc}", "copy-failed"
        ) from exc
    try:
        dest_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise RawStagingError(
            f"目标副本 {target} 已存在；work 是一次性隔离单元，不覆盖",
            "target-exists",
        ) from exc
    except OSError as exc:
        raise RawStagingError(
            f"无法创建目标副本 {target}：{exc}", "copy-failed"
        ) from exc
    written.files.append(target)
    try:
        with (
            os.fdopen(dest_fd, "wb") as dest,
            open(os.open(source_path, os.O_RDONLY | O_NOFOLLOW), "rb") as src,
        ):
            while True:
                chunk = src.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                dest.write(chunk)
    except OSError as exc:
        raise RawStagingError(
            f"复制 {source_path} -> {target} 失败：{exc}", "copy-failed"
        ) from exc
    try:
        after = _identity(source_path)
    except OSError as exc:
        raise RawStagingError(
            f"源文件 {source_path} 在复制后不可访问：{exc}", "copy-failed"
        ) from exc
    if before != after:
        raise RawStagingError(
            f"源文件 {source_path} 在复制窗口内被改动："
            f"lstat 元组 (size, mtime_ns, ino, mode) 由 {before} 变为 {after}",
            "source-mutated",
        )


def _manifest_metadata(leads: tuple[int, ...]) -> dict[str, Any]:
    """manifest 级四键全写，取值由 yd 自己确定，MUST NOT 从源 manifest 转抄。

    yd 不做 pin 那种 requested/effective 的裁剪，故两个小时表相等。
    """
    hours = list(leads)
    return {
        FIRST_FORECAST_HOUR_KEY: hours[0],
        LAST_FORECAST_HOUR_KEY: hours[-1],
        REQUESTED_FORECAST_HOURS_KEY: hours,
        SOURCE_FORECAST_HOURS_KEY: hours,
    }


def _render_manifest(
    *,
    source: str,
    cycle: datetime,
    leads: tuple[int, ...],
    entries: tuple[ManifestEntry, ...],
    cycle_root: Path,
) -> bytes:
    """把本轮 manifest 序列化成**字节**，在任何写入之前完成。

    序列化必须整体前置到准入期：`entries` 与 `leads` 在复制开始前就已完全确定，而
    序列化本身可能失败——源 manifest 是外部 JSON，`json.load` 会接受转义的孤代理
    （`\\ud800`），`json.dumps(ensure_ascii=False)` 也照样吐出它，直到写 UTF-8 流时
    才抛 `UnicodeEncodeError`（是 `ValueError`，不是 `OSError`）。留在写入期就意味着
    「副本全落地 + 一个 0 字节 manifest」这种半套产物。这里先 `encode("utf-8")` 把它
    变成准入期的 `source-manifest` 拒绝，零写入。

    `ensure_ascii=False` MUST 保留：改成 `True` 会把孤代理转义成 `\\ud800` 六字符、
    编码顺利通过，等于把一个不可编码的值偷渡进产出 manifest。
    """
    manifest = DownloadManifest(
        source_id=SOURCE_DIR_NAMES[source],
        cycle_time=cycle.astimezone(UTC),
        entries=entries,
        # `manifest_uri` 留 `None`。理由**不依赖任何 pin 事实**：§3.1 未记载该字段，
        # 故本仓不就它作 pin 声明（原注释称「pin 上它是 object-store URI
        # （ifs_adapter.py:667 一带）」，§3.1 无支撑，已删——同一 verifier 裁定）。
        # 本轮 manifest 落在 `<work_dir>/raw-manifest.json`，不是 object store 对象，
        # 写一个 `file://` 路径等于发明一个本仓不持有的身份。
        manifest_uri=None,
        metadata=_manifest_metadata(leads),
    )
    try:
        return json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2).encode(
            "utf-8"
        )
    except (UnicodeEncodeError, TypeError, ValueError) as exc:
        raise RawStagingError(
            f"源 manifest {cycle_root / SOURCE_MANIFEST_FILENAME} 承接来的值无法序列化"
            f"成 UTF-8 的本轮 manifest：{exc!r}",
            "source-manifest",
        ) from exc


def _write_manifest(
    *,
    manifest_path: Path,
    payload: bytes,
    written: _Written,
) -> None:
    try:
        fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise RawStagingError(
            f"目标 {manifest_path} 已存在；work 是一次性隔离单元，不覆盖",
            "target-exists",
        ) from exc
    except OSError as exc:
        raise RawStagingError(
            f"无法创建 {manifest_path}：{exc}", "copy-failed"
        ) from exc
    written.files.append(manifest_path)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
    except OSError as exc:
        raise RawStagingError(
            f"写入 {manifest_path} 失败：{exc}", "copy-failed"
        ) from exc


# --- staging 入口 -------------------------------------------------------------


def stage_raw(
    verdict: ScanVerdict,
    raw_root: str | os.PathLike[str],
    work_dir: str | os.PathLike[str],
    source: str,
    cycle: datetime,
    config: Config,
) -> StagedRaw:
    """把 `verdict.expected_files` 只读复制进 `work_dir`，并生成本轮 raw manifest。

    顺序逐段短路，且**任何写入之前**全部准入检查已过：完整性 → 形参守卫 → 单 bundle
    约束 → `raw_root`/`work_dir` 不相互包含 → 源路径重构与 containment（纯路径运算）
    → 源侧 symlink 拒绝 → 源 manifest 承接与覆盖检查 → R4B2 → **本轮 manifest 序列化**
    → 目标不存在预检 → 复制 → 落 manifest。symlink 拒绝排在读源 manifest **之前**：
    读源 manifest 会穿过 cycle 目录段，若该段是 symlink 而先读了它，就等于跟随了 spec
    说「不跟随」的那条链。序列化排在复制**之前**：见 `_render_manifest`。

    失败一律抛 `RawStagingError`（`kind` 取自 `ERROR_KINDS`），形参写错抛 `ConfigError`；
    复制/落盘期的**任何**异常（含裸的非 `RawStagingError`）都会先清掉本轮已写入的
    work 侧路径；清理**本身**失败时不静默——`rollback` 保证不抛（否则它会替换正在
    外抛的失败），失败清单进入外抛异常（tier-2 进消息、tier-1/3 进 `add_note`）。
    `raw_root` 之下**零写入**是本函数的硬约束
    （`docs/compute-loop-design.md` §4.1）。
    """
    if verdict.complete is not True:
        raise RawStagingError(
            "verdict.complete 不为 True，拒绝复制："
            f"缺 {len(verdict.missing_files)} 件、不可读 "
            f"{len(verdict.unreadable_files)} 件",
            "incomplete-verdict",
        )
    source_config = _validate_params(source, cycle, config)
    if len(source_config.bundles) != 1:
        # 单 bundle 约束：`entries` 是 lead × variables 而 `copied_files` 是
        # lead × bundles，多 bundle 下「变量落在哪个 bundle」在 config 与 ScanVerdict
        # 里都无处可查，manifest 侧语义不存在（pin 上 bundle 文件名逐 hour 只产一个：
        # gfs_adapter.py:1878-1880、ifs_adapter.py:1688-1690）。判据取 `len(bundles)`
        # 本身，不以「渲染出几个文件名」间接判。放开它需要 config 先长出
        # variable→bundle 映射（归 issue #29 / #32），本 issue 不发明。
        raise RawStagingError(
            f"source {source!r} 声明了 {len(source_config.bundles)} 个 bundle 模式；"
            "manifest 的 (lead, variable, file) 三元组只在恰好一个模式时有定义",
            "unsupported-layout",
        )

    raw_path = _absolute(raw_root, "raw_root")
    work_path = _absolute(work_dir, "work_dir")
    # 两个入参互相包含时，「只读 raw_root、只写 work_dir」这条硬约束在本函数内部
    # 不再可能同时成立：副本、目录与失败回滚的 unlink/rmdir 全都会落进 NWM raw 树
    # （`docs/compute-loop-design.md` §4.1）。这是「调用写错了」，归 `ConfigError`
    # 而不是第十项 kind——九项词表由 tasks.md 任务 3.2 fixture 钉死。
    #
    # 判据是**物理**包含，不是词法包含：`is_relative_to` 只比字符串前缀，round-2
    # verifier 实测三种别名（大小写别名、`work_dir` 自身是链、`..` 段）都能让副本
    # 落进 raw 树而闸门放行。`resolve()` 关掉后两种、inode 身份关掉第一种，两者
    # 缺一不可；两个根互为「外/内」各判一次，相等的情形两向都为真。
    raw_real = _normalized(raw_path, "raw_root")
    work_real = _normalized(work_path, "work_dir")
    if (
        work_real.is_relative_to(raw_real)
        or raw_real.is_relative_to(work_real)
        or _contains_by_identity(raw_real, work_real)
        or _contains_by_identity(work_real, raw_real)
    ):
        raise ConfigError(
            f"work_dir {work_path} 与 raw_root {raw_path} 互相包含"
            f"（物理路径 {work_real} 与 {raw_real}）；work 必须是 raw 树之外的独立"
            "目录，否则「raw_root 之下零写入」不可能成立"
        )
    rebuilt = _reconstruct_sources(
        raw_root=raw_path,
        source=source,
        cycle=cycle,
        source_config=source_config,
        verdict=verdict,
    )
    for _, source_path in rebuilt:
        _reject_symlinks(raw_path, source_path)

    cycle_root = rebuilt[0][1].parent if rebuilt else raw_path
    source_manifest = _load_source_manifest(cycle_root)
    declared_hours = _source_forecast_hours(source_manifest, cycle_root)
    leads = tuple(lead for lead, _ in rebuilt)
    uncovered = sorted(set(leads) - declared_hours)
    if uncovered:
        raise RawStagingError(
            "源 manifest 声明的 forecast hours 不覆盖本轮 lead "
            + "、".join(str(lead) for lead in uncovered)
            + "；不得以副本存在为由声明该轮齐全",
            "source-manifest",
        )
    entries = _build_entries(
        verdict=verdict,
        rebuilt=rebuilt,
        source=source,
        cycle=cycle,
        source_index=_index_source_entries(source_manifest, cycle_root),
    )

    manifest_payload = _render_manifest(
        source=source,
        cycle=cycle,
        leads=leads,
        entries=entries,
        cycle_root=cycle_root,
    )

    targets = tuple(
        work_path / Path(_local_key(source, cycle, path.name)) for _, path in rebuilt
    )
    manifest_path = work_path / MANIFEST_FILENAME
    for candidate in (*targets, manifest_path):
        if os.path.lexists(candidate):
            raise RawStagingError(
                f"目标 {candidate} 已存在；work 是一次性隔离单元，不覆盖、不续跑",
                "target-exists",
            )

    written = _Written()
    try:
        for (_, source_path), target in zip(rebuilt, targets, strict=True):
            _ensure_dir(target.parent, written)
            _copy_one(source_path, target, written)
        _write_manifest(
            manifest_path=manifest_path,
            payload=manifest_payload,
            written=written,
        )
    except RawStagingError as exc:
        failures = written.rollback()
        if failures:
            # 用 `add_note` 而不是重建异常：kind、`__cause__` 与调用方的 `is` 身份
            # 都必须原样保留，要加的只是「清理没做干净」这条信号。
            exc.add_note(_rollback_note(failures))
        raise
    except Exception as exc:
        # 清理触发器 MUST NOT 窄于它要维护的不变量：只接 `RawStagingError` 时，写入块
        # 里任何别的异常（NUL 字节路径让 `mkdir` 抛裸 `ValueError`、序列化面的
        # `UnicodeEncodeError`、被 monkeypatch 的原语抛出的任意异常）都会绕过回滚**并**
        # 逃出九项闭合词表。此支同时收口两侧：先回滚，再把它收敛成 `copy-failed`。
        failures = written.rollback()
        # 「已清理」这句话只有在真清理干净时才准说：清理失败时它是假消息，而残留
        # 会让下一次重试被 `lexists` 预检以 `target-exists` 硬拒、楔死整个 cycle。
        cleanup = "已清理本轮 work 侧写入" if not failures else _rollback_note(failures)
        raise RawStagingError(
            f"复制/落盘期出现未预期的异常 {exc!r}；{cleanup}",
            "copy-failed",
        ) from exc
    except BaseException as exc:
        # `KeyboardInterrupt`/`SystemExit` MUST NOT 被改写成 `RawStagingError`——那会
        # 让 Ctrl-C 看起来像一次 staging 失败。但清理照做：不留半套副本这条不变量与
        # 异常类型无关。这是本函数唯一一条外抛非 `{ConfigError, RawStagingError}` 的
        # 出口，且是有意为之。
        failures = written.rollback()
        if failures:
            exc.add_note(_rollback_note(failures))
        raise
    return StagedRaw(manifest_path=manifest_path, copied_files=targets, entries=entries)
