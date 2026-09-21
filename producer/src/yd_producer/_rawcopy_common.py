"""yd structural glue: imports.

Moved bodies retain existing comments and structure.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yd_producer.raw.manifest import ManifestEntry

# yd 自产的本轮清单文件名（落在 `work_dir` 根下，不是 object-store 对象）。
MANIFEST_FILENAME = "raw-manifest.json"

# NWM 在 raw cycle 目录内落盘的源清单文件名
# （NWM@8ae9b8f2 gfs_adapter.py `_persist_manifest_metadata`:1599-1609，调用点 :774；
#  ifs_adapter.py:667 构建期一次性写入）。
SOURCE_MANIFEST_FILENAME = "manifest.json"

# entry 级 `metadata` 的承接键，恰好 pin 构建期自写的 6 个
# （NWM@8ae9b8f2 gfs_adapter.py:623-634；ifs_adapter.py 同形）。逐条承接，缺一即
# fail closed——本仓 MUST NOT 发明其中任何一个的值。
ENTRY_CYCLE_TIME_KEY = "cycle_time"
ENTRY_VALID_TIME_KEY = "valid_time"
ENTRY_GRIB_SHORT_NAME_KEY = "grib_short_name"
ENTRY_CFGRIB_FILTER_KEY = "cfgrib_filter_by_keys"
ENTRY_CFGRIB_SHORT_NAME_KEY = "shortName"
ENTRY_METADATA_KEYS: tuple[str, ...] = (
    ENTRY_CYCLE_TIME_KEY,
    ENTRY_VALID_TIME_KEY,
    "bundle",
    ENTRY_GRIB_SHORT_NAME_KEY,
    ENTRY_CFGRIB_FILTER_KEY,
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

# `ACCUMULATION_TYPES` 的另一项。承接侧只判「是否在词表内」用不到它，
# 但 bundle 自证（`_derive_accumulation_selector`）要**写出**这个取值。
CUMULATIVE_SINCE_CYCLE = "cumulative_since_cycle"

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

# 准入期收口器（`stage_raw` 的 floor）给未分类异常安的 kind。取 `source-manifest`
# 而不是新造第十项：九项词表由 tasks.md 任务 3.2 fixture 钉死；而准入段里**入参面**
# 的形态（source/cycle/config/路径）在任何外部读取之前就已由 `_validate_params`、
# `_absolute`、`_normalized` 以 `ConfigError` 拦掉，verdict 面由 `verdict-mismatch`
# 拦掉，故能走到兜底的余下形态以「源 manifest 这份外部 JSON 的值」为主——实测逃逸的
# 两条（`int(1e400)` 的 `OverflowError`、深嵌套 `json.load` 的 `RecursionError`）都在
# 该面上。这是一条判断，不是推论：兜底给的是**地板**，各消费点自己的 except 仍在，
# 落到这里就意味着「没有更准的归类」。
ADMISSION_FALLBACK_KIND = "source-manifest"

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


def _safe_repr(value: Any) -> str:
    """`repr()` 的不抛版本，供**异常处理路径**上的消息拼装使用。

    `repr` 本身可以抛：一个 `__repr__` 抛异常的自定义 exception 会让
    `f"{exc!r}"` 在 handler 内部炸掉，于是「保证不抛」的 `rollback`、以及把异常
    收敛成 `RawStagingError` 的两个收口器，都会在它们**自己的**兜底逻辑里失守
    （round-3 verifier 实测：`_Written._remove` 的 `{exc!r}` 让 `rollback` 抛
    `RuntimeError`）。凡在 handler 内部对**外来对象**取 repr，一律走本函数。
    """
    try:
        return repr(value)
    except BaseException:  # noqa: BLE001 —— 本函数的存在意义就是吞掉它
        try:
            return f"<{type(value).__name__} 对象，repr() 自身抛异常>"
        except BaseException:  # noqa: BLE001 —— 连类型名都取不到的病态对象
            return "<无法取 repr 的对象>"


@dataclass(frozen=True, kw_only=True)
class StagedRaw:
    """一次 staging 的产物。

    `copied_files` 与 `verdict.expected_files` 同序同长；`entries` 按
    (lead 升序, `variables` 声明序)。
    """

    manifest_path: Path
    copied_files: tuple[Path, ...]
    entries: tuple[ManifestEntry, ...]
