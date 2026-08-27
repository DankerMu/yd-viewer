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
    "cycle": {"hours": [0, 12], "lead_hours_start": 0, "lead_hours_end": 168},
    "variants": {"gfs": "input/models/yd_gfs", "ifs": "input/models/yd_ifs"},
    "raw": {
        "ifs": {
            "variables": ["fixture-var-a", "fixture-var-b"],
            "bundles": ["fixture-ifs-{lead}.grib2"],
            "f000_special": False,
        },
        "gfs": {
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
    "cycle.lead_hours_start",
    "cycle.lead_hours_end",
    "variants.gfs",
    "variants.ifs",
    "raw.ifs.variables",
    "raw.ifs.bundles",
    "raw.ifs.f000_special",
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
    if isinstance(value, int):
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
    assert config.cycle.lead_hours_start == 0
    assert config.cycle.lead_hours_end == 168
    assert config.variants.gfs == "input/models/yd_gfs"
    assert config.variants.ifs == "input/models/yd_ifs"
    assert config.raw.ifs.variables == ("fixture-var-a", "fixture-var-b")
    assert config.raw.ifs.bundles == ("fixture-ifs-{lead}.grib2",)
    assert config.raw.ifs.f000_special is False
    assert config.raw.gfs.variables == ("fixture-var-c", "fixture-var-d")
    assert config.raw.gfs.bundles == ("fixture-gfs-{lead}.grib2",)
    assert config.raw.gfs.f000_special is True
    assert config.slurm.required_fields == SLURM_REQUIRED_FIELDS


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


@pytest.mark.parametrize("missing_key", CONFIG_REQUIRED_KEYS)
def test_config_missing_required_key_fails_closed(tmp_path, missing_key):
    path = _write_toml(tmp_path / "config.toml", _without(VALID_CONFIG, missing_key))

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert missing_key in str(excinfo.value)


@pytest.mark.parametrize("missing_key", LOCAL_REQUIRED_KEYS)
def test_local_missing_required_key_fails_closed(tmp_path, missing_key):
    config = _loaded_config(tmp_path)
    path = _write_toml(tmp_path / "local.toml", _without(VALID_LOCAL, missing_key))

    with pytest.raises(ConfigError) as excinfo:
        load_local(path, config)

    assert missing_key in str(excinfo.value)


# --- 类型错误 ----------------------------------------------------------------


def test_config_reach_count_must_be_int(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["reach_count"] = "3988"

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    message = str(excinfo.value)
    assert "reach_count" in message
    assert "int" in message
    assert "str" in message


def test_config_cycle_hours_must_be_list(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["cycle"]["hours"] = 0

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    message = str(excinfo.value)
    assert "cycle.hours" in message
    assert "list[int]" in message


def test_config_f000_special_must_be_bool(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["raw"]["gfs"]["f000_special"] = 1

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    message = str(excinfo.value)
    assert "raw.gfs.f000_special" in message
    assert "bool" in message


def test_config_nested_table_type_error_names_dotted_path(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["raw"]["ifs"]["variables"] = ["ok", 3]

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    assert "raw.ifs.variables" in str(excinfo.value)


def test_local_slurm_value_type_error_names_dotted_path(tmp_path):
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    data["slurm"]["cpus"] = [8]

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    assert "slurm.cpus" in str(excinfo.value)


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


# --- required_fields 自身的合法性 -------------------------------------------


def test_empty_required_fields_rejected(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["slurm"]["required_fields"] = []

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    assert "slurm.required_fields" in str(excinfo.value)


def test_duplicate_required_fields_rejected(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["slurm"]["required_fields"] = [
        *SLURM_REQUIRED_FIELDS,
        SLURM_REQUIRED_FIELDS[0],
    ]

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    message = str(excinfo.value)
    assert "slurm.required_fields" in message
    assert SLURM_REQUIRED_FIELDS[0] in message


def test_non_string_required_fields_rejected(tmp_path):
    data = copy.deepcopy(VALID_CONFIG)
    data["slurm"]["required_fields"] = [*SLURM_REQUIRED_FIELDS, 7]

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    assert "slurm.required_fields" in str(excinfo.value)


# --- local.[slurm] 键集：唯一权威是 config.slurm.required_fields -------------


@pytest.mark.parametrize("missing_field", SLURM_REQUIRED_FIELDS)
def test_local_slurm_missing_declared_field_fails_closed(tmp_path, missing_field):
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    del data["slurm"][missing_field]

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    assert missing_field in str(excinfo.value)


def test_local_slurm_extra_key_is_not_silently_ignored(tmp_path):
    """现场把 `partition` 误写成 `partiton` 必须报错并指名多余键。"""
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    data["slurm"]["partiton"] = data["slurm"].pop("partition")

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    assert "partiton" in str(excinfo.value)


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


def test_loader_source_has_no_second_slurm_keyset():
    """键集第二次写死在装载器代码里即双权威——本用例把这条约束钉在源码上。"""
    source = Path(load_config.__globals__["__file__"]).read_text(encoding="utf-8")
    for name in SLURM_REQUIRED_FIELDS:
        assert name not in source


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
