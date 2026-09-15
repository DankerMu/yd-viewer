"""yd structural glue: imports.

Moved bodies retain existing comments and structure.
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from yd_producer._rawcopy_common import (
    ACCUMULATION_TYPE_KEYS,
    ACCUMULATION_TYPES,
    ACCUMULATION_VARIABLES,
    ENTRY_CFGRIB_FILTER_KEY,
    ENTRY_CFGRIB_SHORT_NAME_KEY,
    ENTRY_CYCLE_TIME_KEY,
    ENTRY_GRIB_SHORT_NAME_KEY,
    ENTRY_METADATA_KEYS,
    ENTRY_VALID_TIME_KEY,
    IDX_SELECTOR_KEY,
    IDX_SELECTORS_KEY,
    INTERVAL_BUCKET,
    SOURCE_MANIFEST_FILENAME,
    STEP_RANGE_KEYS,
    RawStagingError,
    _safe_repr,
)
from yd_producer.raw.manifest import DownloadManifest, ManifestEntry, parse_cycle_time

# --- 2. 源 manifest 承接 ------------------------------------------------------


def _reject_lossy_forecast_hours(payload: Mapping[str, Any], path: Path) -> None:
    """entry 级 `forecast_hour` 的**形态闸门**，在 `from_dict` 之前对**原始**值判。

    必须在 `from_dict` 之前：`ManifestEntry.from_dict`（`raw/manifest.py:197`）对该
    字段做 `int(value["forecast_hour"])`，`3.9 -> 3`、`"3" -> 3`、`True -> 1`
    全部**静默有损归一**，之后再看 `entry.forecast_hour` 已经是归一后的 `int`，原始
    形态不可恢复。而该字段随后被 `_index_source_entries` 当作 `(lead, variable)` 的
    **键**用，于是一条 `"forecast_hour": 3.9` 的影子 entry 会占住真实 lead 3 的槽位，
    把自己的 `remote_url` 与六键喂进产出 manifest，且 staging **正常成功**——这是本
    模块唯一一条「成功返回 + 输出静默错误」的路径（round-3 verifier 实测四种形态）。
    `tasks.md:349` 把该类型校验明确路由给消费端自建（pin 文件 `raw/manifest.py` 不改），
    `tasks.md:339` 指明本信封的下游消费就是任务 3.2 = 本模块。

    判据取「`int()` 是否有损」而不是枚举 `float`/`str`/`bool`：严格要求 `int` 且
    排除 `bool`（`bool` 是 `int` 子类，`int(True) == 1`）。`3.0` 也拒——它同样让
    「源里写的」与「yd 读到的」不是同一个值。（§3.1 **没有**记载该字段的值类型，故
    此处不作任何 pin 行为断言；本仓逐字副本 `raw/manifest.py` 的
    `generate_segmented_forecast_hours` 返回 `list[int]`，这是本仓侧的旁证而不是
    pin 事实——闸门的理由是「有损归一改变取值」本身，不依赖源侧写什么。）
    """
    entries = payload.get("entries")
    if not isinstance(entries, list):
        # 结构面归 `DownloadManifest.from_dict`，本闸门只管形态；缺失/非 list 会在
        # 下一步以 `源 manifest 的结构不合 NWM DownloadManifest 形态` 报出。
        return
    for position, entry in enumerate(entries):
        if not isinstance(entry, Mapping) or "forecast_hour" not in entry:
            continue
        value = entry["forecast_hour"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise RawStagingError(
                f"源 manifest {path} 第 {position} 条 entry 的 `forecast_hour` "
                f"不是整数，实际 {type(value).__name__} {_safe_repr(value)}；"
                "该字段是 (lead, variable) 的索引键，`int()` 的静默归一会让它占住"
                "另一个 lead 的槽位",
                "source-manifest",
            )


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
    _reject_lossy_forecast_hours(payload, path)
    try:
        return DownloadManifest.from_dict(dict(payload))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise RawStagingError(
            f"源 manifest {path} 的结构不合 NWM DownloadManifest 形态：{exc!r}",
            "source-manifest",
        ) from exc


def _entry_instant(value: Any, *, key: str, lead: int, variable: str) -> datetime:
    """把承接来的 entry 时间解析成**时刻**；不可解析即 fail closed。

    走本仓 `raw/manifest.py` 的 `parse_cycle_time`（与 manifest 级 `cycle_time` 同一
    个解析器），故比较的是时刻而不是文本：`…Z`、`…+00:00`、`…-05:00` 是同一时刻的
    三种合法 ISO-8601 写法，源侧写哪种不受本仓约束。**逐字承接的仍是原文本**，本函数
    只用于核对，不改写落盘值。
    该解析器把**不带偏移**的写法按 UTC 解释（`ensure_utc`，`raw/manifest.py:12-16`）：
    这是本仓既有约定，此处沿用而不另立一套 —— 是选择，不是遗漏。
    """
    try:
        return parse_cycle_time(value)
    except (AttributeError, ValueError) as exc:
        # 两个分量各自可达、各自有判别器：非字符串（`json.load` 会产出 `int`/`list`/
        # `None`）在 `value.strip()` 上抛 `AttributeError`，不合 ISO-8601 的字符串在
        # `fromisoformat`/`strptime` 上抛 `ValueError`。**不列 `TypeError`**：从
        # `json.load` 的输出里没有一条路径能走到它（要走到需要 `bytes`），列进来就是
        # 一条没有判别器的腿；真出现时准入地板仍会兜住，只是给泛化诊断。
        raise RawStagingError(
            f"源 manifest 的 (lead={lead}, variable={variable!r}) entry 的 `{key}` "
            f"不是可解析的时刻，实际 {type(value).__name__} {_safe_repr(value)}",
            "source-manifest",
        ) from exc


def _check_carried_times(
    carried: Mapping[str, Any], *, cycle: datetime, lead: int, variable: str
) -> None:
    """承接来的 entry 时间 MUST 对应它被归档到的 (cycle, lead) 槽位。

    承接是逐字的，但「逐字承接一个与本轮槽位不符的时刻」会让产出物**自相矛盾**：
    manifest 级 `cycle_time` 由 yd 自算（= 形参 `cycle`，`_render_manifest`），而
    entry 级的两个时间来自源 manifest；源侧若填的是另一轮的时间，落盘的
    `raw-manifest.json` 就会在 manifest 级声明一个 cycle、在每条 entry 上声明另一个，
    且全部 lead 共用同一个 `valid_time`。这份不一致 yd 从自己的入参就能判出来，
    故 fail closed 而不是把它写进产物。
    （**不**在此声称下游 converter 如何使用 `valid_time`：勘察清单 §3.1 没有记载
    converter 读 entry 级 `valid_time` 的任何一行，可锚定的危害只是产物自相矛盾。）

    两个分量各自独立判：`cycle_time` 对应本轮 cycle，`valid_time` 对应 cycle + lead。
    """
    expected_cycle = cycle.astimezone(UTC)
    declared_cycle = _entry_instant(
        carried[ENTRY_CYCLE_TIME_KEY],
        key=ENTRY_CYCLE_TIME_KEY,
        lead=lead,
        variable=variable,
    )
    if declared_cycle != expected_cycle:
        raise RawStagingError(
            f"源 manifest 的 (lead={lead}, variable={variable!r}) entry 的 "
            f"`{ENTRY_CYCLE_TIME_KEY}` {_safe_repr(carried[ENTRY_CYCLE_TIME_KEY])} "
            f"不是本轮 cycle {expected_cycle.isoformat()}；承接一个别轮的时刻会让"
            "产出 manifest 在 manifest 级与 entry 级声明两个不同的 cycle",
            "source-manifest",
        )
    expected_valid = expected_cycle + timedelta(hours=lead)
    declared_valid = _entry_instant(
        carried[ENTRY_VALID_TIME_KEY],
        key=ENTRY_VALID_TIME_KEY,
        lead=lead,
        variable=variable,
    )
    if declared_valid != expected_valid:
        raise RawStagingError(
            f"源 manifest 的 (lead={lead}, variable={variable!r}) entry 的 "
            f"`{ENTRY_VALID_TIME_KEY}` {_safe_repr(carried[ENTRY_VALID_TIME_KEY])} "
            f"不是 cycle + lead = {expected_valid.isoformat()}；该 entry 被归档在 "
            f"lead {lead} 的槽位上",
            "source-manifest",
        )


def _check_carried_grib_short_names(
    carried: Mapping[str, Any], *, lead: int, variable: str
) -> None:
    """承接来的两个 GRIB 身份 MUST 是同一值的两次书写。

    `grib_short_name` 与 `cfgrib_filter_by_keys["shortName"]` 按 pin 同源；yd 不持有
    别名表，核对的是两个已承接值之间的关系，不是按变量名推导。形态先于取值：
    `cfgrib_filter_by_keys` 是外部 JSON，可能不是 Mapping 或缺 `shortName` 子键。
    缺键即使对端是 `None` 也无效——键缺席与「两边同为 null」不是一回事。
    落盘的仍是源侧原值，本函数只核对、不改写、不互相覆写。
    """
    filters = carried[ENTRY_CFGRIB_FILTER_KEY]
    if not isinstance(filters, Mapping):
        raise RawStagingError(
            f"源 manifest 的 (lead={lead}, variable={variable!r}) entry 的 "
            f"`{ENTRY_CFGRIB_FILTER_KEY}` 不是 Mapping，"
            f"实际 {type(filters).__name__} {_safe_repr(filters)}",
            "source-manifest",
        )
    if ENTRY_CFGRIB_SHORT_NAME_KEY not in filters:
        raise RawStagingError(
            f"源 manifest 的 (lead={lead}, variable={variable!r}) entry 的 "
            f"`{ENTRY_CFGRIB_FILTER_KEY}` 缺 `{ENTRY_CFGRIB_SHORT_NAME_KEY}`；"
            f"对端 `{ENTRY_GRIB_SHORT_NAME_KEY}` 为 "
            f"{_safe_repr(carried[ENTRY_GRIB_SHORT_NAME_KEY])}",
            "source-manifest",
        )
    declared = filters[ENTRY_CFGRIB_SHORT_NAME_KEY]
    expected = carried[ENTRY_GRIB_SHORT_NAME_KEY]
    if declared != expected:
        raise RawStagingError(
            f"源 manifest 的 (lead={lead}, variable={variable!r}) entry 的 "
            f"`{ENTRY_GRIB_SHORT_NAME_KEY}` {_safe_repr(expected)} 与 "
            f"`{ENTRY_CFGRIB_FILTER_KEY}.{ENTRY_CFGRIB_SHORT_NAME_KEY}` "
            f"{_safe_repr(declared)} 不一致；承接两个不同的 GRIB 身份会让产出 "
            "manifest 在同一条 entry 上声明两个变量",
            "source-manifest",
        )


def _carried_metadata(
    source_entry: ManifestEntry, lead: int, variable: str, cycle: datetime
) -> dict[str, Any]:
    """从源 entry 逐条承接语义键；本仓 MUST NOT 发明其中任何一个。

    「不发明」不等于「不核对」：两个时间键另由 `_check_carried_times` 与本 entry 被
    归档到的 (cycle, lead) 槽位对账；`grib_short_name` 与
    `cfgrib_filter_by_keys["shortName"]` 另由 `_check_carried_grib_short_names`
    对账二者相等。落盘的仍是源侧原值。
    """
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
    _check_carried_times(carried, cycle=cycle, lead=lead, variable=variable)
    _check_carried_grib_short_names(carried, lead=lead, variable=variable)

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
        # 求哈希，于是抛裸 `TypeError`。裸异常本身已由 `stage_raw` 的准入期收口块
        # 兜住（round-4），故本闸门的理由是**更准的 kind**：这里报
        # `accumulation-metadata` 并指名字段，地板只能给泛化的 `source-manifest`。
        # 同类出口另有 `_index_source_entries` 的 `variable`。
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
