"""`config.toml`（版本化业务规则）与 `local.toml`（现场值）的类型化装载。

字段来源：`docs/compute-loop-design.md` §5（config/local 字段全集、Slurm 资源字段
结构）、§6.1（模型变体相对路径）、§7.1（cycle 00/12、lead 0–168h、变量/bundle/
f000 特例）、`docs/products-contract.md` §5（`forecast_days`/`output_interval_minutes`
/`reach_count`）、spec `cli-config`（顶层 key 名逐字钉死）。

设计约束（design.md D4/D5）：只用 stdlib `tomllib`；dataclass 显式校验；任何必需
字段缺失或类型错误一律 fail closed；代码中零内置现场默认值——所有 dataclass 字段
都没有默认值，缺字段只能走报错路径。全部失败路径收敛到公开异常 `ConfigError`，涉及
具体字段的失败以 `ConfigError.path` 暴露该字段的完整点分路径。

全部 dataclass 一律 `kw_only=True` 构造：`VariantsConfig(gfs, ifs)` 与 `RawConfig(ifs,
gfs)` 字段名相同而顺序相反，`RawSourceConfig` 的 `variables`/`bundles` 相邻且同为
`tuple[str, ...]`——位置构造下互换实参是静默的，下游（#6 raw 完整性判定、#20 覆盖守卫）
只会看到"raw 永远缺"而非一条红测试。字段名、类型、`dataclasses.fields` 顺序与 `hash()`
均不受影响；`__match_args__` 变为空元组，位置式 `match` 解构不再可用。
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Config",
    "ConfigError",
    "CronLocal",
    "CycleConfig",
    "LocalConfig",
    "NwmLocal",
    "RawConfig",
    "RawSourceConfig",
    "SlurmSchema",
    "VariantsConfig",
    "load_config",
    "load_local",
]


class ConfigError(Exception):
    """配置装载失败。

    `load_config` 与 `load_local` 的全部失败路径都收敛到本类型：文件缺失、TOML 语法
    错误、编码错误、必需字段缺失、类型错误、`[slurm]` 键集不匹配。裸 `KeyError`/
    `TypeError`/`OSError`/`tomllib.TOMLDecodeError`/`UnicodeDecodeError` 不会外泄。

    `path` 是出错字段的完整点分路径（如 `raw.gfs.variables`），供调用方与测试机检
    定位；整文件级失败（文件不存在、编码错误、TOML 语法错误）为 `None`。消息里同时
    保留人读的点分路径。
    """

    def __init__(self, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


# --- config.toml（版本化业务规则）------------------------------------------


@dataclass(frozen=True, kw_only=True)
class CycleConfig:
    """cycle 起报时刻（compute-loop §7.1）。

    lead 全集逐源声明在 `raw.<source>.lead_hours`，此处不再另设窗口端点——两处并存
    即第二权威。
    """

    hours: tuple[int, ...]


@dataclass(frozen=True, kw_only=True)
class VariantsConfig:
    """两个模型变体相对 `yd_root` 的路径（compute-loop §6.1）。"""

    gfs: str
    ifs: str


@dataclass(frozen=True, kw_only=True)
class RawSourceConfig:
    """单个 source 的 raw 完整性规则（compute-loop §7.1）。

    `lead_hours` 是该源本轮**预期 lead 的全集**（逐源，而非两源共用）：预期文件集 =
    `lead_hours` × `bundles`，没有全集就无法发现中间某个 lead 缺失。
    """

    lead_hours: tuple[int, ...]
    variables: tuple[str, ...]
    bundles: tuple[str, ...]
    f000_special: bool


@dataclass(frozen=True, kw_only=True)
class RawConfig:
    """IFS/GFS 两份 source 规则。"""

    ifs: RawSourceConfig
    gfs: RawSourceConfig


@dataclass(frozen=True, kw_only=True)
class SlurmSchema:
    """Slurm 资源配置字段结构（compute-loop §5）：只声明字段名，值在 `local.toml`。

    `required_fields` 是 `local.toml` 的 `[slurm]` 表唯一的键集权威，装载器不对该表
    另设静态 schema。
    """

    required_fields: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class Config:
    """`config.toml` 的类型化视图；全部字段必需，无可选项、无默认值。"""

    forecast_days: int
    output_interval_minutes: int
    checkpoint_hours: tuple[int, ...]
    reach_count: int
    cycle: CycleConfig
    variants: VariantsConfig
    raw: RawConfig
    slurm: SlurmSchema


# --- local.toml（gitignored 现场值）-----------------------------------------


@dataclass(frozen=True, kw_only=True)
class NwmLocal:
    """NWM raw 根、checkout 根与解释器路径（compute-loop §5）。"""

    raw_root: str
    checkout_root: str
    python: str


@dataclass(frozen=True, kw_only=True)
class CronLocal:
    """cron lock 与日志位置（compute-loop §5）。"""

    lock_path: str
    log_dir: str


@dataclass(frozen=True, kw_only=True)
class LocalConfig:
    """`local.toml` 的类型化视图；全部字段必需，无可选项、无默认值。

    `slurm` 以映射暴露而非固定字段，键集由 `Config.slurm.required_fields` 决定。
    """

    yd_root: str
    scratch_root: str
    shud_binary: str
    nwm: NwmLocal
    slurm: dict[str, str | int]
    cron: CronLocal


# --- 显式校验原语 ------------------------------------------------------------


def _child(prefix: str, key: str) -> str:
    """拼出字段的完整点分路径，用于错误信息定位。"""
    return f"{prefix}.{key}" if prefix else key


def _type_name(value: Any) -> str:
    return type(value).__name__


def _is_scalar(value: Any, expected: str) -> bool:
    """按期望类型判定标量。TOML 的 bool 不得被当作 int（Python 中 bool 是 int 子类）。"""
    if expected == "bool":
        return isinstance(value, bool)
    if expected == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, str)


def _require(table: Mapping[str, Any], key: str, prefix: str) -> Any:
    path = _child(prefix, key)
    if key not in table:
        raise ConfigError(f"缺少必需配置项 `{path}`", path)
    return table[key]


def _require_scalar(
    table: Mapping[str, Any], key: str, prefix: str, expected: str
) -> Any:
    path = _child(prefix, key)
    value = _require(table, key, prefix)
    if not _is_scalar(value, expected):
        raise ConfigError(
            f"配置项 `{path}` 类型错误：期望 {expected}，实际 {_type_name(value)}",
            path,
        )
    return value


def _require_int(table: Mapping[str, Any], key: str, prefix: str = "") -> int:
    return _require_scalar(table, key, prefix, "int")


def _require_str(table: Mapping[str, Any], key: str, prefix: str = "") -> str:
    return _require_scalar(table, key, prefix, "str")


def _require_bool(table: Mapping[str, Any], key: str, prefix: str = "") -> bool:
    return _require_scalar(table, key, prefix, "bool")


def _require_list(
    table: Mapping[str, Any], key: str, prefix: str, expected: str
) -> tuple[Any, ...]:
    path = _child(prefix, key)
    value = _require(table, key, prefix)
    if not isinstance(value, list):
        raise ConfigError(
            f"配置项 `{path}` 类型错误：期望 list[{expected}]，实际 {_type_name(value)}",
            path,
        )
    for index, item in enumerate(value):
        if not _is_scalar(item, expected):
            raise ConfigError(
                f"配置项 `{path}` 下标 {index} 的元素类型错误："
                f"期望 {expected}，实际 {_type_name(item)}",
                path,
            )
    return tuple(value)


def _require_int_list(
    table: Mapping[str, Any], key: str, prefix: str = ""
) -> tuple[int, ...]:
    return _require_list(table, key, prefix, "int")


def _require_str_list(
    table: Mapping[str, Any], key: str, prefix: str = ""
) -> tuple[str, ...]:
    return _require_list(table, key, prefix, "str")


def _require_table(
    table: Mapping[str, Any], key: str, prefix: str = ""
) -> Mapping[str, Any]:
    path = _child(prefix, key)
    value = _require(table, key, prefix)
    if not isinstance(value, dict):
        raise ConfigError(
            f"配置项 `{path}` 类型错误：期望 table，实际 {_type_name(value)}",
            path,
        )
    return value


def _read_toml(path: str | os.PathLike[str], missing_message: str) -> dict[str, Any]:
    """读取并解析 TOML；任何 IO/语法失败都转成 `ConfigError`。"""
    location = os.fspath(path)
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(missing_message) from exc
    except OSError as exc:
        raise ConfigError(f"读取配置文件失败：{location}（{exc.strerror}）") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件 TOML 语法错误：{location}（{exc}）") from exc
    except UnicodeDecodeError as exc:
        # tomllib 先把字节按 UTF-8 解码再解析；非 UTF-8 存盘（GBK 注释、UTF-16）抛
        # UnicodeDecodeError，它是 ValueError 而非 OSError/TOMLDecodeError 的子类，
        # 没有别的子句会捕获它，必须单独收敛。它与 TOMLDecodeError 互不为子类，两个
        # 子句谁先谁后都不影响捕获结果。
        raise ConfigError(
            f"配置文件编码错误：{location} 不是 UTF-8（{exc.reason}），"
            "请以 UTF-8 重新存盘"
        ) from exc


# --- config.toml 装配 --------------------------------------------------------


def _build_cycle(table: Mapping[str, Any]) -> CycleConfig:
    return CycleConfig(hours=_require_int_list(table, "hours", "cycle"))


def _build_variants(table: Mapping[str, Any]) -> VariantsConfig:
    return VariantsConfig(
        gfs=_require_str(table, "gfs", "variants"),
        ifs=_require_str(table, "ifs", "variants"),
    )


def _build_raw_source(table: Mapping[str, Any], prefix: str) -> RawSourceConfig:
    return RawSourceConfig(
        lead_hours=_require_int_list(table, "lead_hours", prefix),
        variables=_require_str_list(table, "variables", prefix),
        bundles=_require_str_list(table, "bundles", prefix),
        f000_special=_require_bool(table, "f000_special", prefix),
    )


def _build_raw(table: Mapping[str, Any]) -> RawConfig:
    return RawConfig(
        ifs=_build_raw_source(_require_table(table, "ifs", "raw"), "raw.ifs"),
        gfs=_build_raw_source(_require_table(table, "gfs", "raw"), "raw.gfs"),
    )


def _build_slurm_schema(table: Mapping[str, Any]) -> SlurmSchema:
    required_fields = _require_str_list(table, "required_fields", "slurm")
    if not required_fields:
        raise ConfigError(
            "配置项 `slurm.required_fields` 不得为空列表", "slurm.required_fields"
        )
    duplicates = sorted(
        {name for name in required_fields if required_fields.count(name) > 1}
    )
    if duplicates:
        raise ConfigError(
            "配置项 `slurm.required_fields` 存在重复项："
            + "、".join(f"`{name}`" for name in duplicates),
            "slurm.required_fields",
        )
    return SlurmSchema(required_fields=required_fields)


def _build_config(data: Mapping[str, Any]) -> Config:
    return Config(
        forecast_days=_require_int(data, "forecast_days"),
        output_interval_minutes=_require_int(data, "output_interval_minutes"),
        checkpoint_hours=_require_int_list(data, "checkpoint_hours"),
        reach_count=_require_int(data, "reach_count"),
        cycle=_build_cycle(_require_table(data, "cycle")),
        variants=_build_variants(_require_table(data, "variants")),
        raw=_build_raw(_require_table(data, "raw")),
        slurm=_build_slurm_schema(_require_table(data, "slurm")),
    )


def load_config(path: str | os.PathLike[str]) -> Config:
    """装载版本化 `config.toml`。

    任何必需字段缺失或类型错误都抛 `ConfigError`，错误信息与 `ConfigError.path` 都
    含该字段的完整点分路径；绝不返回带默认值的半成品对象。
    """
    location = os.fspath(path)
    data = _read_toml(path, f"配置文件不存在：{location}")
    try:
        return _build_config(data)
    except ConfigError as exc:
        raise ConfigError(f"{location}：{exc}", exc.path) from exc


# --- local.toml 装配 ---------------------------------------------------------


def _build_nwm(table: Mapping[str, Any]) -> NwmLocal:
    return NwmLocal(
        raw_root=_require_str(table, "raw_root", "nwm"),
        checkout_root=_require_str(table, "checkout_root", "nwm"),
        python=_require_str(table, "python", "nwm"),
    )


def _build_cron(table: Mapping[str, Any]) -> CronLocal:
    return CronLocal(
        lock_path=_require_str(table, "lock_path", "cron"),
        log_dir=_require_str(table, "log_dir", "cron"),
    )


def _build_local_slurm(
    table: Mapping[str, Any], required_fields: tuple[str, ...]
) -> dict[str, str | int]:
    """按 `config.toml` 声明的字段名校验现场 `[slurm]`，键集必须完全相等。"""
    missing = sorted(set(required_fields) - set(table))
    extra = sorted(set(table) - set(required_fields))
    if missing or extra:
        # 缺项与多余项同时报出（现场把 `partition` 误写成 `partiton` 时两者并存）；
        # 机检用的 `path` 取确定性的第一项：先缺项，无缺项则取多余项。
        parts = []
        if missing:
            parts.append("缺少 " + "、".join(f"`slurm.{name}`" for name in missing))
        if extra:
            parts.append("多余 " + "、".join(f"`slurm.{name}`" for name in extra))
        raise ConfigError(
            "`[slurm]` 的键集必须与 config.toml 的 `slurm.required_fields` 完全一致："
            + "；".join(parts),
            _child("slurm", missing[0] if missing else extra[0]),
        )
    values: dict[str, str | int] = {}
    for name in required_fields:
        value = table[name]
        path = _child("slurm", name)
        if not (_is_scalar(value, "str") or _is_scalar(value, "int")):
            raise ConfigError(
                f"配置项 `{path}` 类型错误：期望 str 或 int，实际 {_type_name(value)}",
                path,
            )
        values[name] = value
    return values


def _build_local(data: Mapping[str, Any], config: Config) -> LocalConfig:
    return LocalConfig(
        yd_root=_require_str(data, "yd_root"),
        scratch_root=_require_str(data, "scratch_root"),
        shud_binary=_require_str(data, "shud_binary"),
        nwm=_build_nwm(_require_table(data, "nwm")),
        slurm=_build_local_slurm(
            _require_table(data, "slurm"), config.slurm.required_fields
        ),
        cron=_build_cron(_require_table(data, "cron")),
    )


def load_local(path: str | os.PathLike[str], config: Config) -> LocalConfig:
    """装载 gitignored `local.toml` 现场值。

    `[slurm]` 的键集以 `config.slurm.required_fields` 为唯一权威：缺项与多余项都抛
    `ConfigError` 并指名该键。文件或字段缺失一律报错，代码不内置任何现场默认值。
    """
    location = os.fspath(path)
    data = _read_toml(
        path,
        f"现场配置文件不存在：{location}；"
        "local.toml 不入库，须由现场按 docs/compute-loop-design.md §5 创建，"
        "代码不内置任何现场默认值",
    )
    try:
        return _build_local(data, config)
    except ConfigError as exc:
        raise ConfigError(f"{location}：{exc}", exc.path) from exc
