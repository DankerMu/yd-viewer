"""NWM raw 目录的 source/cycle 完整性判定（spec `raw-scan`，tasks.md 任务 3.1）。

规则来源：`docs/compute-loop-design.md` §7.1（cycle 00/12、逐源 lead 全集、变量与
bundle 文件模式、GFS f000 特例）、openspec `raw-scan` 的 Requirement「基于显式规则的
完整性判定」。目录布局与文件名形态转录自 NWM pin `8ae9b8f2`（见下方溯源注释）。

设计约束：
- **纯函数、零写入**：只对预期文件做 `is_file()` 与 `open(..., "rb")` 读一个字节，
  不创建/修改/删除任何路径，不产生 manifest（manifest 归任务 3.2）。
- **不列目录**：预期文件集严格由 `lead_hours × bundles` 构造。以目录稳定时间、末
  lead 存在或任何动态推断替代逐文件检查是 spec 的 MUST NOT。
- **"不完整"不是异常**：以 `ScanVerdict` 返回并列出缺失/不可读清单。配置类与请求类
  失败才抛 `ConfigError`（复用 `yd_producer.config` 的同一异常类型，不另造第二个
  配置异常），且**一律短路在任何文件系统访问之前**。
- 只用 stdlib；MUST NOT 运行时 import NWM，MUST NOT 连接任何数据库。
"""

import os
import string
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from yd_producer.config import Config, ConfigError, RawSourceConfig

__all__ = [
    "GFS_F000_UNAVAILABLE_VARIABLES",
    "ScanVerdict",
    "judge",
]

# 合法 source 词表：目录名即该字面量小写形式
# （NWM@8ae9b8f2 workers/data_adapters/gfs_adapter.py:615 的
#  `raw/{source_id}/{compact_cycle}/{bundle_filename}`）。
SOURCES: tuple[str, ...] = ("ifs", "gfs")

# 合法 cycle 起报时刻（compute-loop §7.1；`config.cycle.hours` MUST 是其子集）。
CYCLE_HOURS_DOMAIN: frozenset[int] = frozenset({0, 12})

# cycle 目录名：UTC 紧凑戳
# （NWM@8ae9b8f2 workers/data_adapters/base.py `format_cycle_time`）。
CYCLE_DIR_FORMAT = "%Y%m%d%H"

# bundle 文件名模式允许的具名字段全集，恰好两个
# （NWM@8ae9b8f2 workers/data_adapters/gfs_adapter.py:1878-1880 与
#  workers/data_adapters/ifs_adapter.py:1688-1690 的文件名形态）。
BUNDLE_PATTERN_FIELDS: tuple[str, ...] = ("cycle_hour", "lead")

# f000（分析时刻）无定义的累积/平均量。
# 转录自 NWM@8ae9b8f2 workers/data_adapters/gfs_adapter.py:107
#   `GFS_F000_UNAVAILABLE_VARIABLES: frozenset[str] = frozenset({"apcp", "dswrf"})`
# pin 注释（同文件 L103-106）：累积/平均量在 f000 分析时刻无定义，cloud `.idx` 与
# NOMADS 均无 f000 的 APCP/DSWRF；f000 仍作为瞬时场保留在 manifest 内，故本模块只削
# 减 lead 0 的**变量集**，不削减文件集（`_effective_forecast_hours` L1624 为恒等映射）。
# 该特例由 `RawSourceConfig.f000_special` 布尔开关驱动，MUST NOT 按 source 名分支。
# 绑定（归 issue #29）：本集合的成员名必须落在 `raw.gfs.variables` 的命名词表内，
# 否则该过滤在生产上恒为空操作。
GFS_F000_UNAVAILABLE_VARIABLES: frozenset[str] = frozenset({"apcp", "dswrf"})


@dataclass(frozen=True, kw_only=True)
class ScanVerdict:
    """一次 source/cycle 判定的结果。

    `complete` 当且仅当 `missing_files` 与 `unreadable_files` 均为空。
    `missing_files`/`unreadable_files` 都是 `expected_files` 的保序子序列。
    `expected_variables` 供任务 3.2 的 manifest 逐变量扇出消费。
    """

    complete: bool
    expected_files: tuple[Path, ...]
    missing_files: tuple[Path, ...]
    unreadable_files: tuple[Path, ...]
    expected_variables: dict[int, tuple[str, ...]]


# --- 1. 配置取值域校验（tasks.md 组 1 Non-goals 路由到本任务）----------------


def _validate_config_domain(config: Config) -> None:
    """校验判定所依赖的配置取值域。

    两个源都查，而不是只查被请求的那个源：本段不依赖 `source` 合法，故排在词表校验
    之前，双重非法输入（词表外 `source` + 空列表）下的行为因此是确定的。

    空集必须拒绝：预期文件集为空会让"所有预期文件存在才算完整"恒真，把缺口判成完整。
    """
    hours = config.cycle.hours
    if not hours:
        raise ConfigError("配置项 `cycle.hours` 不得为空列表", "cycle.hours")
    illegal = sorted(set(hours) - CYCLE_HOURS_DOMAIN)
    if illegal:
        raise ConfigError(
            "配置项 `cycle.hours` 只接受 "
            + "、".join(str(hour) for hour in sorted(CYCLE_HOURS_DOMAIN))
            + "，实际含 "
            + "、".join(str(hour) for hour in illegal),
            "cycle.hours",
        )
    for name in SOURCES:
        source_config: RawSourceConfig = getattr(config.raw, name)
        for field in ("lead_hours", "variables", "bundles"):
            if not getattr(source_config, field):
                path = f"raw.{name}.{field}"
                raise ConfigError(f"配置项 `{path}` 不得为空列表", path)


# --- 2. 请求校验 -------------------------------------------------------------


def _validate_request(source: str, cycle: datetime, config: Config) -> None:
    if source not in SOURCES:
        raise ConfigError(
            f"source 取值非法：{source!r}，只接受 "
            + "、".join(repr(name) for name in SOURCES)
        )
    if not isinstance(cycle, datetime):
        raise ConfigError(f"cycle 必须是 datetime，实际 {type(cycle).__name__}")
    if cycle.utcoffset() != timedelta(0):
        # naive datetime 的 `utcoffset()` 返回 None，与非零偏移一并落在本支。判据取
        # 零偏移量而非 `tzinfo is timezone.utc`：任何零偏移时区格式化出的紧凑戳相同。
        raise ConfigError(
            f"cycle 必须是 tz-aware 的 UTC 时刻，实际 {cycle!r}"
            "（naive 或非 UTC 会让目录戳指向另一个 cycle）"
        )
    if (cycle.minute, cycle.second, cycle.microsecond) != (0, 0, 0):
        # 目录名只取到小时；非整点值若放行会被静默截断成另一个 cycle。
        raise ConfigError(f"cycle 必须是整点，实际 {cycle!r}（分/秒/微秒必须均为 0）")
    if cycle.hour not in config.cycle.hours:
        raise ConfigError(
            f"cycle 起报时刻 {cycle.hour:02d}Z 不在 `cycle.hours` 声明的 "
            + "、".join(f"{hour:02d}Z" for hour in config.cycle.hours)
            + " 内",
            "cycle.hours",
        )


# --- 3. bundle 模式校验与渲染 ------------------------------------------------


def _pattern_fields(pattern: str, path: str) -> list[str]:
    """取出模式内的具名字段，顺带把语法损坏转成 `ConfigError`。

    `string.Formatter().parse` 是惰性生成器，语法损坏在迭代中途才抛 `ValueError`，
    故必须在 try 内物化。
    """
    try:
        parsed = list(string.Formatter().parse(pattern))
    except ValueError as exc:
        raise ConfigError(
            f"配置项 `{path}` 的 bundle 模式语法错误：{pattern!r}（{exc}）", path
        ) from exc
    return [field for _, field, _, _ in parsed if field is not None]


def _validate_pattern(pattern: str, path: str) -> None:
    for field in _pattern_fields(pattern, path):
        if field in BUNDLE_PATTERN_FIELDS:
            continue
        if field == "":
            reason = "自动编号的位置字段 `{}`"
        elif field.isdigit():
            reason = f"位置字段 `{{{field}}}`"
        else:
            # 属性/下标访问（`{lead.real}`、`{lead[0]}`）同样落在本支：字段名必须
            # 逐字落在词表内，否则渲染结果不再由词表决定。
            reason = f"词表外的字段 `{{{field}}}`"
        raise ConfigError(
            f"配置项 `{path}` 的 bundle 模式 {pattern!r} 含{reason}；"
            "只接受 " + "、".join(f"`{{{name}}}`" for name in BUNDLE_PATTERN_FIELDS),
            path,
        )


def _render(pattern: str, cycle_hour: int, lead: int, path: str) -> str:
    try:
        rendered = pattern.format(cycle_hour=cycle_hour, lead=lead)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ConfigError(
            f"配置项 `{path}` 的 bundle 模式 {pattern!r} 渲染失败"
            f"（cycle_hour={cycle_hour}、lead={lead}）：{exc}",
            path,
        ) from exc
    separators = {"/", os.sep} | ({os.altsep} if os.altsep else set())
    if any(sep in rendered for sep in separators) or rendered in {"", ".", ".."}:
        # 渲染结果 MUST 是单个文件名：逃出 `<raw_root>/<source>/<cycle>/` 就使
        # "只读 NWM 原件"的边界失效。
        raise ConfigError(
            f"配置项 `{path}` 的 bundle 模式 {pattern!r} 渲染出 {rendered!r}，"
            "不是单个文件名（不得含路径分隔符或 `..` 段）",
            path,
        )
    return rendered


# --- 4. 逐文件检查 -----------------------------------------------------------


def _is_readable(path: Path) -> bool:
    """以真实 `open` 读一个字节为准。

    不用 `os.access(..., os.R_OK)`：它在部分挂载/权限模型下与真实 `open` 不一致。
    读到零字节（空文件）也算可读——本 issue 的"可读"只到能发起读为止，GRIB 内容校验
    归 M4 receipt。
    """
    try:
        with open(path, "rb") as handle:
            handle.read(1)
    except OSError:
        return False
    return True


# --- 判定入口 ---------------------------------------------------------------


def _expected_leads(source_config: RawSourceConfig) -> tuple[int, ...]:
    """预期 lead，按升序（`lead_hours` 在 `config.toml` 内的书写顺序不作数）。"""
    return tuple(sorted(source_config.lead_hours))


def _variables_for_lead(source_config: RawSourceConfig, lead: int) -> tuple[str, ...]:
    """该 lead 的预期变量集；f000 特例只在 `f000_special` 为真且 lead 为 0 时生效。

    退化情形（滤除后为空）镜像 pin 行为：文件仍属预期，变量集为空元组，不报错。
    """
    if source_config.f000_special and lead == 0:
        return tuple(
            name
            for name in source_config.variables
            if name not in GFS_F000_UNAVAILABLE_VARIABLES
        )
    return source_config.variables


def _iter_expected(
    cycle_root: Path,
    source_config: RawSourceConfig,
    leads: Iterable[int],
    cycle_hour: int,
    path: str,
) -> Iterator[Path]:
    for pattern in source_config.bundles:
        _validate_pattern(pattern, path)
    # lead 升序、组内按 `bundles` 声明序。
    for lead in leads:
        for pattern in source_config.bundles:
            yield cycle_root / _render(pattern, cycle_hour, lead, path)


def judge(
    raw_root: str | os.PathLike[str],
    source: str,
    cycle: datetime,
    config: Config,
) -> ScanVerdict:
    """判定 `<raw_root>/<source>/<YYYYMMDDHH>/` 下该轮 raw 是否完整。

    `raw_root` 是 NWM raw 根（其下为 `<source>/<YYYYMMDDHH>/`）；`source` 取
    `"ifs"`/`"gfs"`（大小写敏感，词表外 fail closed）；`cycle` MUST 是 tz-aware 的
    UTC 整点。

    判定顺序逐段短路：配置取值域 → 请求校验 → 模式校验与渲染 → 逐文件检查。前三段
    的失败一律抛 `ConfigError`，且发生在任何文件系统访问之前——`raw_root` 不存在时
    这些拒绝同样成立。cycle 目录整体不存在**不是**错误：返回 `complete=False` 且
    `missing_files == expected_files`（7 天扫描窗的绝大多数请求正落在这里）。
    """
    _validate_config_domain(config)
    _validate_request(source, cycle, config)

    try:
        root = Path(os.fspath(raw_root))
    except TypeError as exc:
        raise ConfigError(
            f"raw_root 必须是 str 或 os.PathLike，实际 {type(raw_root).__name__}"
        ) from exc
    if not root.is_absolute():
        root = Path.cwd() / root

    source_config: RawSourceConfig = getattr(config.raw, source)
    cycle_root = root / source / cycle.astimezone(UTC).strftime(CYCLE_DIR_FORMAT)
    leads = _expected_leads(source_config)
    # 预期集在任何 stat 之前完整构造：模式校验的失败不得等到扫到该文件时才暴露。
    expected_files = tuple(
        _iter_expected(
            cycle_root, source_config, leads, cycle.hour, f"raw.{source}.bundles"
        )
    )
    expected_variables = {
        lead: _variables_for_lead(source_config, lead) for lead in leads
    }

    missing: list[Path] = []
    unreadable: list[Path] = []
    for path in expected_files:
        if not path.is_file():
            # `is_file()` 跟随 symlink：指向目录的 symlink、断链 symlink 与目录本身
            # 都算缺失。
            missing.append(path)
        elif not _is_readable(path):
            unreadable.append(path)

    return ScanVerdict(
        complete=not missing and not unreadable,
        expected_files=expected_files,
        missing_files=tuple(missing),
        unreadable_files=tuple(unreadable),
        expected_variables=expected_variables,
    )
