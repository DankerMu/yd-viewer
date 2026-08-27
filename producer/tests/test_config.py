"""`yd_producer.config` 装载器测试。

全部用例使用内联 TOML 写入 `tmp_path`：仓库刻意不提供版本化 `config.toml` 生产实例，
也不提供 `local.toml.example`（`raw.*` 的真实取值出自后续 NWM 勘察）。下方 fixture
中的 `raw` 变量名与 bundle 模式是**合成测试值**，只用于验证 schema，不代表生产取值。
"""

import copy
import dataclasses
import json
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from yd_producer.config import (
    Config,
    ConfigError,
    LocalConfig,
    load_config,
    load_local,
)

# --- 内联 TOML fixture -------------------------------------------------------

VALID_CONFIG: dict[str, Any] = {
    "forecast_days": 7,
    "output_interval_minutes": 60,
    "checkpoint_hours": [12],
    "reach_count": 3988,
    "cycle": {"hours": [0, 12]},
    "variants": {"gfs": "input/models/yd_gfs", "ifs": "input/models/yd_ifs"},
    "raw": {
        "ifs": {
            "lead_hours": [0, 3, 6],
            "variables": ["fixture-var-a", "fixture-var-b"],
            "bundles": ["fixture-ifs-{lead}.grib2"],
            "f000_special": False,
        },
        "gfs": {
            # 与 ifs 刻意取不同的 lead 集：lead 全集逐源，两源共用即缺陷
            "lead_hours": [0, 6, 12],
            "variables": ["fixture-var-c", "fixture-var-d"],
            "bundles": ["fixture-gfs-{lead}.grib2"],
            "f000_special": True,
        },
    },
    "slurm": {
        "required_fields": ["partition", "account", "cpus", "memory", "walltime"],
    },
}

VALID_LOCAL: dict[str, Any] = {
    "yd_root": "/fixture/yd",
    "scratch_root": "/fixture/scratch",
    "shud_binary": "/fixture/bin/shud",
    "nwm": {
        "raw_root": "/fixture/nwm/raw",
        "checkout_root": "/fixture/nwm/checkout",
        "python": "/fixture/nwm/.venv/bin/python",
    },
    "slurm": {
        "partition": "cpu",
        "account": "yd-forecast",
        "cpus": 8,
        "memory": "32G",
        "walltime": "04:00:00",
    },
    "cron": {
        "lock_path": "/fixture/run/yd-producer.lock",
        "log_dir": "/fixture/log/yd-producer",
    },
}

# spec cli-config 反引号钉死的顶层 key，MUST NOT 被加上表前缀
SPEC_PINNED_TOP_LEVEL_KEYS = (
    "forecast_days",
    "output_interval_minutes",
    "checkpoint_hours",
    "reach_count",
)

# fixture「TOML key schema」逐字钉死的必需叶子 key（点分路径）
PINNED_CONFIG_KEYS = (
    "forecast_days",
    "output_interval_minutes",
    "checkpoint_hours",
    "reach_count",
    "cycle.hours",
    "variants.gfs",
    "variants.ifs",
    "raw.ifs.lead_hours",
    "raw.ifs.variables",
    "raw.ifs.bundles",
    "raw.ifs.f000_special",
    "raw.gfs.lead_hours",
    "raw.gfs.variables",
    "raw.gfs.bundles",
    "raw.gfs.f000_special",
    "slurm.required_fields",
)

PINNED_LOCAL_KEYS = (
    "yd_root",
    "scratch_root",
    "shud_binary",
    "nwm.raw_root",
    "nwm.checkout_root",
    "nwm.python",
    "slurm",
    "cron.lock_path",
    "cron.log_dir",
)

# `local.[slurm]` 键集的唯一权威是 config；此处只用于驱动参数化，不得进入装载器代码
SLURM_REQUIRED_FIELDS = tuple(VALID_CONFIG["slurm"]["required_fields"])


# --- 工具 --------------------------------------------------------------------


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_render_value(item) for item in value) + "]"
    raise AssertionError(f"fixture 不支持的值类型：{type(value).__name__}")


def _render_toml(data: Mapping[str, Any], prefix: str = "") -> str:
    """把嵌套 dict 渲染为 TOML；始终写出表头，删空某表也不会改变错误定位层级。"""
    lines: list[str] = []
    if prefix:
        lines.append(f"[{prefix}]")
    for key, value in data.items():
        if not isinstance(value, dict):
            lines.append(f"{key} = {_render_value(value)}")
    for key, value in data.items():
        if isinstance(value, dict):
            child = f"{prefix}.{key}" if prefix else key
            lines.append(_render_toml(value, child))
    return "\n".join(lines) + "\n"


def _write_toml(path: Path, data: Mapping[str, Any]) -> Path:
    path.write_text(_render_toml(data), encoding="utf-8")
    return path


def _without(data: Mapping[str, Any], dotted_key: str) -> dict[str, Any]:
    clone = copy.deepcopy(dict(data))
    node: Any = clone
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        node = node[part]
    del node[parts[-1]]
    return clone


def _required_keys(cls: type, prefix: str = "") -> list[str]:
    """从 dataclass 树推导必需 key 清单——新增字段即自动新增一条参数化用例。"""
    hints = typing.get_type_hints(cls)
    keys: list[str] = []
    for field in dataclasses.fields(cls):
        path = f"{prefix}.{field.name}" if prefix else field.name
        field_type = hints[field.name]
        if isinstance(field_type, type) and dataclasses.is_dataclass(field_type):
            keys.extend(_required_keys(field_type, path))
        else:
            keys.append(path)
    return keys


def _dataclass_tree(cls: type) -> list[type]:
    classes = [cls]
    hints = typing.get_type_hints(cls)
    for field in dataclasses.fields(cls):
        field_type = hints[field.name]
        if isinstance(field_type, type) and dataclasses.is_dataclass(field_type):
            classes.extend(_dataclass_tree(field_type))
    return classes


CONFIG_REQUIRED_KEYS = _required_keys(Config)
LOCAL_REQUIRED_KEYS = _required_keys(LocalConfig)


def _loaded_config(tmp_path: Path, data: Mapping[str, Any] | None = None) -> Config:
    return load_config(
        _write_toml(tmp_path / "config.toml", VALID_CONFIG if data is None else data)
    )


# --- schema 钉死 -------------------------------------------------------------


def test_config_required_keys_match_pinned_schema():
    assert CONFIG_REQUIRED_KEYS == list(PINNED_CONFIG_KEYS)


def test_local_required_keys_match_pinned_schema():
    assert LOCAL_REQUIRED_KEYS == list(PINNED_LOCAL_KEYS)


def test_spec_pinned_keys_stay_top_level():
    """`forecast_days` 等四个 key 由 spec 反引号钉死在顶层，不得被加表前缀。"""
    for key in SPEC_PINNED_TOP_LEVEL_KEYS:
        assert key in CONFIG_REQUIRED_KEYS


# --- 齐备装载 ----------------------------------------------------------------


def test_load_config_returns_all_fields(tmp_path):
    config = _loaded_config(tmp_path)

    assert config.forecast_days == 7
    assert config.output_interval_minutes == 60
    assert config.checkpoint_hours == (12,)
    assert config.reach_count == 3988
    assert config.cycle.hours == (0, 12)
    assert config.variants.gfs == "input/models/yd_gfs"
    assert config.variants.ifs == "input/models/yd_ifs"
    assert config.raw.ifs.lead_hours == (0, 3, 6)
    assert config.raw.gfs.lead_hours == (0, 6, 12)
    assert config.raw.ifs.variables == ("fixture-var-a", "fixture-var-b")
    assert config.raw.ifs.bundles == ("fixture-ifs-{lead}.grib2",)
    assert config.raw.ifs.f000_special is False
    assert config.raw.gfs.variables == ("fixture-var-c", "fixture-var-d")
    assert config.raw.gfs.bundles == ("fixture-gfs-{lead}.grib2",)
    assert config.raw.gfs.f000_special is True
    assert config.slurm.required_fields == SLURM_REQUIRED_FIELDS


def test_raw_sources_carry_independent_lead_hours(tmp_path):
    """lead 全集逐源：两源取不同 lead 集时各自原样返回，不共用、不互串。"""
    data = copy.deepcopy(VALID_CONFIG)
    data["raw"]["ifs"]["lead_hours"] = [0, 3, 6, 9]
    data["raw"]["gfs"]["lead_hours"] = [0, 1]

    config = _loaded_config(tmp_path, data)

    assert config.raw.ifs.lead_hours == (0, 3, 6, 9)
    assert config.raw.gfs.lead_hours == (0, 1)


def test_load_local_returns_all_site_fields(tmp_path):
    config = _loaded_config(tmp_path)
    local = load_local(_write_toml(tmp_path / "local.toml", VALID_LOCAL), config)

    assert local.yd_root == "/fixture/yd"
    assert local.scratch_root == "/fixture/scratch"
    assert local.shud_binary == "/fixture/bin/shud"
    assert local.nwm.raw_root == "/fixture/nwm/raw"
    assert local.nwm.checkout_root == "/fixture/nwm/checkout"
    assert local.nwm.python == "/fixture/nwm/.venv/bin/python"
    assert local.cron.lock_path == "/fixture/run/yd-producer.lock"
    assert local.cron.log_dir == "/fixture/log/yd-producer"
    assert local.slurm == VALID_LOCAL["slurm"]


# --- 缺字段 fail closed（schema 驱动参数化）---------------------------------
#
# 断言以结构化的 `ConfigError.path` 为准（与措辞解耦）；同时要求消息里出现反引号
# 包裹的点分路径，因为运维读的是消息。反引号是必要的：pytest 的 tmp_path 目录名由
# 测试名与参数拼出，裸子串探测会被目录名恒真地满足。


def _assert_locates(excinfo, dotted_key: str) -> None:
    assert excinfo.value.path == dotted_key
    assert f"`{dotted_key}`" in str(excinfo.value)


@pytest.mark.parametrize("missing_key", CONFIG_REQUIRED_KEYS)
def test_config_missing_required_key_fails_closed(tmp_path, missing_key):
    path = _write_toml(tmp_path / "config.toml", _without(VALID_CONFIG, missing_key))

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    _assert_locates(excinfo, missing_key)


@pytest.mark.parametrize("missing_key", LOCAL_REQUIRED_KEYS)
def test_local_missing_required_key_fails_closed(tmp_path, missing_key):
    config = _loaded_config(tmp_path)
    path = _write_toml(tmp_path / "local.toml", _without(VALID_LOCAL, missing_key))

    with pytest.raises(ConfigError) as excinfo:
        load_local(path, config)

    _assert_locates(excinfo, missing_key)


def test_whole_file_failures_carry_no_field_path(tmp_path):
    """整文件级失败不指向具体字段，`path` MUST 为 None（避免误导定位）。"""
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path / "config.toml")
    assert excinfo.value.path is None

    broken = tmp_path / "broken.toml"
    broken.write_text('forecast_days = "unterminated\n', encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(broken)
    assert excinfo.value.path is None


# --- 类型错误 ----------------------------------------------------------------


def test_config_reach_count_must_be_int(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["reach_count"] = "3988"

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, "reach_count")
    message = str(excinfo.value)
    assert "int" in message
    assert "str" in message


def test_config_cycle_hours_must_be_list(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["cycle"]["hours"] = 0

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, "cycle.hours")
    assert "list[int]" in str(excinfo.value)


def test_config_f000_special_must_be_bool(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["raw"]["gfs"]["f000_special"] = 1

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, "raw.gfs.f000_special")
    assert "bool" in str(excinfo.value)


def test_config_nested_table_type_error_names_dotted_path(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["raw"]["ifs"]["variables"] = ["ok", 3]

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    # 列表元素错：`path` 取字段本身（不带下标），下标只出现在人读消息里
    _assert_locates(excinfo, "raw.ifs.variables")
    assert "下标 1" in str(excinfo.value)


def test_config_lead_hours_must_be_int_list(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["raw"]["gfs"]["lead_hours"] = ["6"]

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, "raw.gfs.lead_hours")


def test_local_slurm_value_type_error_names_dotted_path(tmp_path):
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    data["slurm"]["cpus"] = [8]

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    _assert_locates(excinfo, "slurm.cpus")


# --- 类型判别负例：bool 不是 int，float/table 不是 slurm 标量 ----------------


def test_config_bool_is_not_accepted_as_int(tmp_path):
    """TOML `true` MUST NOT 被当作 int（Python 里 bool 是 int 子类）。"""
    data = copy.deepcopy(VALID_CONFIG)
    data["forecast_days"] = True

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, "forecast_days")
    assert "bool" in str(excinfo.value)


def test_config_bool_is_not_accepted_as_int_list_element(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["checkpoint_hours"] = [True]

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, "checkpoint_hours")
    assert "bool" in str(excinfo.value)


def test_config_table_field_holding_scalar_is_rejected(tmp_path):
    """表类型字段填标量 -> 报错且定位到该表本身，而非表内某个子字段。"""
    data = copy.deepcopy(VALID_CONFIG)
    data["cycle"] = 5

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, "cycle")
    assert "table" in str(excinfo.value)


def test_local_slurm_float_value_rejected(tmp_path):
    """`cpus = 8.5` MUST NOT 流到 Slurm 提交行。"""
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    data["slurm"]["cpus"] = 8.5

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    _assert_locates(excinfo, "slurm.cpus")
    assert "float" in str(excinfo.value)


def test_local_slurm_table_value_rejected(tmp_path):
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    data["slurm"]["partition"] = {"a": 1}

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    _assert_locates(excinfo, "slurm.partition")


# --- 文件级失败 --------------------------------------------------------------


def test_missing_local_file_asks_site_to_create_it(tmp_path):
    config = _loaded_config(tmp_path)

    with pytest.raises(ConfigError) as excinfo:
        load_local(tmp_path / "local.toml", config)

    message = str(excinfo.value)
    assert "local.toml" in message
    assert "创建" in message


def test_missing_config_file_fails_closed(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path / "config.toml")

    assert "config.toml" in str(excinfo.value)


def test_broken_toml_config_raises_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('forecast_days = "unterminated\n', encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert "TOML" in str(excinfo.value)


def test_broken_toml_local_raises_config_error(tmp_path):
    config = _loaded_config(tmp_path)
    path = tmp_path / "local.toml"
    path.write_text('yd_root = "unterminated\n', encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_local(path, config)

    assert "TOML" in str(excinfo.value)


@pytest.mark.parametrize("encoding", ["gbk", "utf-16"])
@pytest.mark.parametrize("loader", ["config", "local"])
def test_non_utf8_file_raises_config_error(tmp_path, loader, encoding):
    """非 UTF-8 存盘 MUST NOT 外泄 `UnicodeDecodeError`（它是 ValueError 子类，既非
    OSError 也非 TOMLDecodeError，不会被其它 except 子句捕获）。"""
    config = _loaded_config(tmp_path)
    path = tmp_path / f"{loader}-{encoding}.toml"
    path.write_bytes('# 中文注释\nyd_root = "/fixture/yd"\n'.encode(encoding))

    with pytest.raises(ConfigError) as excinfo:
        if loader == "config":
            load_config(path)
        else:
            load_local(path, config)

    message = str(excinfo.value)
    assert str(path) in message
    assert "UTF-8" in message
    assert excinfo.value.path is None


# --- required_fields 自身的合法性 -------------------------------------------


def test_empty_required_fields_rejected(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["slurm"]["required_fields"] = []

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, "slurm.required_fields")


def test_duplicate_required_fields_rejected(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["slurm"]["required_fields"] = [
        *SLURM_REQUIRED_FIELDS,
        SLURM_REQUIRED_FIELDS[0],
    ]

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, "slurm.required_fields")
    assert SLURM_REQUIRED_FIELDS[0] in str(excinfo.value)


def test_non_string_required_fields_rejected(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["slurm"]["required_fields"] = [*SLURM_REQUIRED_FIELDS, 7]

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, "slurm.required_fields")


# --- local.[slurm] 键集：唯一权威是 config.slurm.required_fields -------------


@pytest.mark.parametrize("missing_field", SLURM_REQUIRED_FIELDS)
def test_local_slurm_missing_declared_field_fails_closed(tmp_path, missing_field):
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    del data["slurm"][missing_field]

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    _assert_locates(excinfo, f"slurm.{missing_field}")


def test_local_slurm_extra_key_is_not_silently_ignored(tmp_path):
    """现场把 `partition` 误写成 `partiton` 必须报错并同时指名缺项与多余键。"""
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    data["slurm"]["partiton"] = data["slurm"].pop("partition")

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    # 缺项与多余项并存：消息两者都报，`path` 取确定性的第一项（先缺项）
    _assert_locates(excinfo, "slurm.partition")
    assert "`slurm.partiton`" in str(excinfo.value)


def test_local_slurm_pure_extra_key_locates_that_key(tmp_path):
    """只多不缺时 `path` 指向该多余键本身。"""
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    data["slurm"]["qos"] = "normal"

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    _assert_locates(excinfo, "slurm.qos")


def test_local_slurm_keyset_follows_added_required_field(tmp_path):
    config_data = copy.deepcopy(VALID_CONFIG)
    config_data["slurm"]["required_fields"] = [*SLURM_REQUIRED_FIELDS, "qos"]
    local_data = copy.deepcopy(VALID_LOCAL)
    local_data["slurm"]["qos"] = "normal"

    config = _loaded_config(tmp_path, config_data)
    local = load_local(_write_toml(tmp_path / "local.toml", local_data), config)

    assert set(local.slurm) == set(config.slurm.required_fields)
    assert local.slurm["qos"] == "normal"


def test_local_slurm_keyset_follows_removed_required_field(tmp_path):
    dropped = "account"
    config_data = copy.deepcopy(VALID_CONFIG)
    config_data["slurm"]["required_fields"] = [
        name for name in SLURM_REQUIRED_FIELDS if name != dropped
    ]
    local_data = copy.deepcopy(VALID_LOCAL)
    del local_data["slurm"][dropped]

    config = _loaded_config(tmp_path, config_data)
    local = load_local(_write_toml(tmp_path / "local.toml", local_data), config)

    assert set(local.slurm) == set(config.slurm.required_fields)
    assert dropped not in local.slurm


def test_local_slurm_keyset_shares_no_name_with_production_fields(tmp_path):
    """键集权威唯一的行为判据：与生产五字段零重名的 `required_fields` 照样装载成功。

    代码里若还残留一份 partition/account/cpus/memory/walltime 的固定字段清单（无论
    以字面量还是拼接方式写死），本用例必然失败——它不依赖源码文本扫描。
    """
    zero_overlap = ["alpha", "beta"]
    assert not set(zero_overlap) & set(SLURM_REQUIRED_FIELDS)

    config_data = copy.deepcopy(VALID_CONFIG)
    config_data["slurm"]["required_fields"] = zero_overlap
    local_data = copy.deepcopy(VALID_LOCAL)
    local_data["slurm"] = {"alpha": "a-value", "beta": 2}

    config = _loaded_config(tmp_path, config_data)
    local = load_local(_write_toml(tmp_path / "local.toml", local_data), config)

    assert local.slurm == {"alpha": "a-value", "beta": 2}


# --- 零默认值 ----------------------------------------------------------------


def test_no_dataclass_field_carries_a_default():
    """任何字段都不得有默认值，缺失只能走 fail-closed 路径而非静默填值。"""
    for klass in _dataclass_tree(Config) + _dataclass_tree(LocalConfig):
        for field in dataclasses.fields(klass):
            assert field.default is dataclasses.MISSING, (
                f"{klass.__name__}.{field.name}"
            )
            assert field.default_factory is dataclasses.MISSING, (
                f"{klass.__name__}.{field.name}"
            )
