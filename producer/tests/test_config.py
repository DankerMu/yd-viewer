"""`yd_producer.config` 装载器测试。

全部用例使用内联 TOML 写入 `tmp_path`：仓库刻意不提供版本化 `config.toml` 生产实例，
也不提供 `local.toml.example`（`raw.*` 的真实取值出自后续 NWM 勘察）。下方 fixture
中的 `raw` 变量名与 bundle 模式是**合成测试值**，只用于验证 schema，不代表生产取值。
"""

import copy
import dataclasses
import json
import re
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
    "nwm_mapping_builder_module": "workers.mapping_builder.cli",
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
    # spec cli-config「config.toml 装载与校验」Requirement 已用反引号把该 key 钉在顶层，
    # 与上面四个同判据（issue #3 fixture 记录下来的决定，非默认）。
    "nwm_mapping_builder_module",
)

# --- 第二本账：从 fixture 手工转录的必需 key 全集 ----------------------------
#
# 来源：`openspec/changes/m2-producer-core/tasks.md` →「### Issue #2 fixture（任务
# 1.1–1.2）」→「TOML key schema」代码块。**权威锚点是该块标题**，不是行号：下面各处的
# tasks.md 行号（config 侧 42-70、local 侧 91-112）只是撰写时的位置提示，文件上游插入
# 内容导致行号漂移不使本转录失效，按块标题重新定位即可。下面两份清单按该代码块的
# **阅读顺序**排列，逐行转录。
#
# 维护规则（round 3 的失败就出在这里，不要重蹈）：这两份清单是 `_required_keys` 的
# **独立第二本账**，MUST 手工对着 fixture 维护，MUST NOT 由 `_required_keys` 的输出
# 反向生成或"照着跑出来的结果补齐"。round 3 时它们照抄了推导器的输出，而推导器只走
# dataclass 叶子、表达不出"表本身也是必需 key"，于是两本账共用同一个盲区，8 个表 key
# 无人覆盖、6 个内置默认值变异体全部存活。同理 MUST NOT 让测试去解析 fixture markdown
# ——那只是换一套有自己盲区的推导器，并把测试绑死在文档排版上。
#
# 表 key 与叶子 key 同为必需项：fixture 写明"全部 key 必需"，整表缺失是独立于表内字段
# 缺失的现场故障。两处转录细节：
# - `raw` 在 fixture 里没有字面表头，它是 `[raw.ifs]`/`[raw.gfs]` 的隐含父表，按 TOML
#   语义在 `[raw.ifs]` 处首次出现，故转录在 `raw.ifs` 之前；
# - `local` 侧只钉 `slurm` 表本身，表内键名不属于本 schema——其键集的唯一权威是
#   `config.slurm.required_fields`（tasks.md:81-85），由下方 SLURM_REQUIRED_FIELDS 驱动。
PINNED_CONFIG_KEYS = (
    # tasks.md:43-47 顶层标量（spec cli-config 反引号钉死，不得加表前缀）
    "forecast_days",
    "output_interval_minutes",
    "checkpoint_hours",
    "reach_count",
    # issue #3 fixture 的 TOML key schema 在 `reach_count` 之后加入本键（#32 三步之第 1 步）
    "nwm_mapping_builder_module",
    # tasks.md:49-50 [cycle]
    "cycle",
    "cycle.hours",
    # tasks.md:52-54 [variants]
    "variants",
    "variants.gfs",
    "variants.ifs",
    # tasks.md:56-60 [raw.ifs]（`raw` 为其隐含父表）
    "raw",
    "raw.ifs",
    "raw.ifs.lead_hours",
    "raw.ifs.variables",
    "raw.ifs.bundles",
    "raw.ifs.f000_special",
    # tasks.md:62-66 [raw.gfs]
    "raw.gfs",
    "raw.gfs.lead_hours",
    "raw.gfs.variables",
    "raw.gfs.bundles",
    "raw.gfs.f000_special",
    # tasks.md:68-69 [slurm]
    "slurm",
    "slurm.required_fields",
)

PINNED_LOCAL_KEYS = (
    # tasks.md:92-94 顶层标量
    "yd_root",
    "scratch_root",
    "shud_binary",
    # tasks.md:96-99 [nwm]
    "nwm",
    "nwm.raw_root",
    "nwm.checkout_root",
    "nwm.python",
    # tasks.md:101-107 [slurm]：只钉表本身，键集权威在 config
    "slurm",
    # tasks.md:109-111 [cron]
    "cron",
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
    """从 dataclass 树推导必需 key 清单——新增字段即自动新增一条参数化用例。

    **表本身也产出一条 key**，再递归表内字段：`[cycle]` 整表缺失与 `cycle.hours` 缺失
    是两种不同的现场故障。只枚举叶子时，「整表缺失就填一份内置默认」的实现无人能发现
    （round 3 F1：6 个此形态的变异体在 77 条测试下全部存活，其中一个把 partition/
    account/cpus/memory/walltime 五字段清单重新写死回代码里）。

    本函数只是第一本账，且只能表达 dataclass 树表达得出的东西；第二本账
    `PINNED_CONFIG_KEYS`/`PINNED_LOCAL_KEYS` 必须独立按 fixture 手工维护——见其上方注释。
    """
    hints = typing.get_type_hints(cls)
    keys: list[str] = []
    for field in dataclasses.fields(cls):
        path = f"{prefix}.{field.name}" if prefix else field.name
        keys.append(path)
        field_type = hints[field.name]
        if isinstance(field_type, type) and dataclasses.is_dataclass(field_type):
            keys.extend(_required_keys(field_type, path))
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


# --- 类型错误轴的推导器（第一本账，带类型）----------------------------------

_SCALAR_TYPES = (str, int, bool)


def _scalar_leaves(cls: type, prefix: str = "") -> list[tuple[str, type]]:
    """从 dataclass 树推导 **(点分路径, 标注类型)**——类型错误轴的推导器。

    与 `_required_keys` 同源同走法，只是多带一份类型信息：新增任何标量字段即自动新增
    一条类型错误用例，不必编辑测试。round 4 R4-TE-01 的成因就是本轴此前**没有推导器**，
    只有手挑的单例，而 `_require_scalar(..., "str")` 一个单例都没抽到——10 个 `str` 字段
    的类型守卫可以被整体换成裸 `_require` 而 95 条测试全绿。

    verifier 的更正（`verify-r4.md`「Proposed closure — verified, with one correction」），
    逐字照搬：把这条推导**驱动自 `PINNED_CONFIG_KEYS`/`PINNED_LOCAL_KEYS` 行不通**——
    那两份清单只带点分名、不带类型信息，无从判断 33 个 key 里哪 10 个是 `str` 标量。
    **dataclass 树是唯一带类型的来源。**

    非标量叶子（`tuple[int, ...]`、`tuple[str, ...]`、`dict[str, str | int]`）**显式跳过**，
    不属于本轴：它们走 `_require_list`/`_require_table` 而非 `_require_scalar`，"无歧义错
    类型"的形态也不同（非 list、元素类型错、非 table），已由本文件的列表/表用例分别覆盖。
    这里写成显式的 `field_type in _SCALAR_TYPES` 白名单判定，而不是"取不到类型就算了"，
    是为了让跳过是一个有理由的决定，而非真值性事故——后者正是本 PR 前四轮盲区的成因。

    判定用**类型对象相等**而非 `issubclass`：`issubclass(bool, int)` 为真，会把 bool 字段
    误并入 int；而 `tuple[int, ...]` / `str | int` 这类 GenericAlias、UnionType 与类型对象
    比较恒为 False，天然落在白名单外。
    """
    hints = typing.get_type_hints(cls)
    leaves: list[tuple[str, type]] = []
    for field in dataclasses.fields(cls):
        path = f"{prefix}.{field.name}" if prefix else field.name
        field_type = hints[field.name]
        if isinstance(field_type, type) and dataclasses.is_dataclass(field_type):
            leaves.extend(_scalar_leaves(field_type, path))
        elif field_type in _SCALAR_TYPES:
            leaves.append((path, field_type))
    return leaves


CONFIG_SCALAR_LEAVES = _scalar_leaves(Config)
LOCAL_SCALAR_LEAVES = _scalar_leaves(LocalConfig)

# 每个标量类型的"无歧义错类型"替换值。**刻意不跨 int/bool 互试**：Python 里 bool 是 int
# 的子类，拿 `true` 去试 int 字段、或拿 `1` 去试 bool 字段，断言的都是本轴契约没有要求的
# 行为——`isinstance(True, int)` 为真，一个只写 `isinstance(v, int)` 的实现放行 `true` 并
# 不违反"类型错误 fail closed"这条契约本身。bool/int 必须互斥是**另一条**契约，由
# `test_config_bool_is_not_accepted_as_int` 等负例单独钉死，不在本轴内重复表达。
# 于是：`str` 字段用 int 试探，`int` 与 `bool` 字段一律用 str 试探。
_WRONG_VALUE_BY_TYPE: dict[type, Any] = {
    str: 5,
    int: "not-an-int",
    bool: "not-a-bool",
}


# --- 第三本账：fixture dict 的点分闭包 --------------------------------------
#
# `VALID_CONFIG`/`VALID_LOCAL` 是与 `PINNED_*_KEYS`、`_required_keys` 都不同形状的第三份
# 转录（嵌套 dict of 值 vs 扁平点分清单 vs dataclass 树）。它之所以是**真正的第三本账**，
# 不是因为形状不同，而是因为它**自身独立承重**：其中每一个 key 都被 round-trip 用例
# （`test_load_config_returns_all_fields` / `test_load_local_returns_all_site_fields`）逐值
# 钉死，想靠删 key 让一次漂移变绿，必然在别处变红。
#
# 它封掉的残留（round 4 实测）：协同漂移——同时把 `_required_keys` 退回只走叶子、并从两份
# pinned 清单里删掉 8 个表 key——原本 87 条全绿，round 3 的 `_DEFAULT_SLURM_FIELDS` 缺陷
# 可以原样复活且无人可见。加上下面两条闭包断言后该漂移变红。
#
# 不解析 fixture markdown：那只是换一套自带盲区的推导器，并把测试绑死在文档排版上。

# `local.toml` 的 `[slurm]` 表在本 schema 里只钉到表本身：**表内键集的唯一权威是
# `config.slurm.required_fields`**（tasks.md「TOML key schema」块下方 `[slurm].required_fields`
# 一节，撰写时位于 tasks.md:81-85），已由 `SLURM_REQUIRED_FIELDS` 驱动的那组用例覆盖，装载器
# 不对该表另设静态 schema。因此凡是遍历 fixture 的走查都 MUST 在此止步。
#
# 这是账本内部的一处**硬编码例外**，而硬编码例外正是前三个盲区的搭建方式，所以它必须写明
# 权威出处：未来若有人想把别的 key 塞进 stop 来抹平一次真实漂移，得先推翻上面这条权威。
_LOCAL_WALK_STOP = ("slurm",)


def _dotted_closure(
    data: Mapping[str, Any], prefix: str = "", stop: tuple[str, ...] = ()
) -> list[str]:
    """把嵌套 fixture dict 展开成点分 key 清单（表本身也产出一条），按阅读顺序。"""
    keys: list[str] = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        keys.append(path)
        if isinstance(value, dict) and path not in stop:
            keys.extend(_dotted_closure(value, path, stop))
    return keys


def _fixture_scalar_leaves(
    data: Mapping[str, Any], prefix: str = "", stop: tuple[str, ...] = ()
) -> list[tuple[str, type]]:
    """fixture dict 里的标量叶子及其**实际值类型**：表递归、列表跳过、其余全收。

    这里的取舍规则 MUST 是**排除法**（非表非列表即标量），MUST NOT 复用 `_scalar_leaves`
    的 `_SCALAR_TYPES` 白名单。实测过：两边共用该白名单时，从白名单里删掉 `bool` 会让
    2 条 bool 用例**整体消失**而套件仍 112 全绿——两本账共用同一个盲区，正是 round 3 的
    失败形态。排除法让 fixture 侧的规则由 fixture 里实际有什么决定，收窄白名单立刻变红。
    """
    leaves: list[tuple[str, type]] = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            if path not in stop:
                leaves.extend(_fixture_scalar_leaves(value, path, stop))
        elif not isinstance(value, list):
            leaves.append((path, type(value)))
    return leaves


def _type_names(leaves: list[tuple[str, type]]) -> dict[str, str]:
    return {path: leaf_type.__name__ for path, leaf_type in leaves}


def _with(data: Mapping[str, Any], dotted_key: str, value: Any) -> dict[str, Any]:
    """深拷贝后把某个点分 key 换成给定值。"""
    clone = copy.deepcopy(dict(data))
    node: Any = clone
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return clone


def _loaded_config(tmp_path: Path, data: Mapping[str, Any] | None = None) -> Config:
    return load_config(
        _write_toml(tmp_path / "config.toml", VALID_CONFIG if data is None else data)
    )


# --- schema 钉死 -------------------------------------------------------------


def test_config_required_keys_match_pinned_schema():
    assert CONFIG_REQUIRED_KEYS == list(PINNED_CONFIG_KEYS)


def test_local_required_keys_match_pinned_schema():
    assert LOCAL_REQUIRED_KEYS == list(PINNED_LOCAL_KEYS)


def test_config_fixture_closure_matches_pinned_schema():
    """第三本账：`VALID_CONFIG` 的点分闭包 == pinned 清单（集合与顺序都相等）。"""
    assert _dotted_closure(VALID_CONFIG) == list(PINNED_CONFIG_KEYS)


def test_local_fixture_closure_matches_pinned_schema():
    closure = _dotted_closure(VALID_LOCAL, stop=_LOCAL_WALK_STOP)
    assert closure == list(PINNED_LOCAL_KEYS)


def test_config_scalar_leaves_agree_with_fixture_value_types():
    """类型推导器 MUST NOT 静默收窄。

    参数化用例数由 `_scalar_leaves` 决定：推导器一旦漏掉某个类型（例如白名单里丢掉
    `bool`），对应用例会**整体消失**而不是变红——空掉的参数化是绿的。这里拿独立承重的
    fixture dict 里标量值的**实际类型**作对照，把"少了一条用例"变成一条红测试。
    """
    assert _type_names(CONFIG_SCALAR_LEAVES) == _type_names(
        _fixture_scalar_leaves(VALID_CONFIG)
    )


def test_local_scalar_leaves_agree_with_fixture_value_types():
    assert _type_names(LOCAL_SCALAR_LEAVES) == _type_names(
        _fixture_scalar_leaves(VALID_LOCAL, stop=_LOCAL_WALK_STOP)
    )


def test_spec_pinned_keys_stay_top_level():
    """`forecast_days` 等四个 key 由 spec 反引号钉死在顶层，不得被加表前缀。

    两条断言缺一不可。自 `_required_keys` 开始产出表路径起，只断言"在清单里"已不足以
    判别：把 `forecast_days` 做成一张表后，`forecast_days` 仍会作为表路径出现在清单里
    （实测 old=['forecast_days.days']、new=['forecast_days', 'forecast_days.days']）。
    第二条断言要求它下面没有子路径，即它必须是叶子标量本身，判别力回到原理性。
    """
    for key in SPEC_PINNED_TOP_LEVEL_KEYS:
        assert key in CONFIG_REQUIRED_KEYS
        nested = [k for k in CONFIG_REQUIRED_KEYS if k.startswith(f"{key}.")]
        assert not nested, f"`{key}` 被做成了表：{nested}"


# --- 齐备装载 ----------------------------------------------------------------


def test_load_config_returns_all_fields(tmp_path):
    config = _loaded_config(tmp_path)

    assert config.forecast_days == 7
    assert config.output_interval_minutes == 60
    assert config.checkpoint_hours == (12,)
    assert config.reach_count == 3988
    assert config.nwm_mapping_builder_module == "workers.mapping_builder.cli"
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


def test_mapping_builder_module_follows_fixture_value(tmp_path):
    """module 名取自 `config.toml`，而非装载器里的常量。

    `test_load_config_returns_all_fields` 的那条 round-trip 断言拦不住这一类：期望值就是
    fixture 里的唯一取值，一个"照常 `_require_str` 校验、结果丢掉、存回字面量"的实现
    （缺 key 仍报错、类型错仍报错，两本账都杀不掉）照样绿。判别力只能来自第二个值。

    刻意写字面量而不从 `cli_fixtures` 导入：本文件的 fixture 与那份**刻意不共用**（见
    `cli_fixtures` 模块头），共用即两本账共享同一个盲区。
    """
    data = _with(VALID_CONFIG, "nwm_mapping_builder_module", "other.builder.entry")

    config = _loaded_config(tmp_path, data)

    assert VALID_CONFIG["nwm_mapping_builder_module"] != "other.builder.entry"
    assert config.nwm_mapping_builder_module == "other.builder.entry"


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

_BACKTICKED = re.compile(r"`([^`]+)`")

# 缺字段消息的"别名域"：任一必需字段的点分路径。消息只许指名出错的那一个。
ALL_REQUIRED_KEYS = frozenset(CONFIG_REQUIRED_KEYS) | frozenset(LOCAL_REQUIRED_KEYS)


def _assert_locates(excinfo, dotted_key: str) -> None:
    assert excinfo.value.path == dotted_key
    assert f"`{dotted_key}`" in str(excinfo.value)


def _assert_names_no_other_field(
    excinfo, dotted_key: str, universe: frozenset[str]
) -> None:
    """定位必须是**指名**而非**罗列**：消息 MUST NOT 顺带列出其它必需字段。

    只断言 `` `x` `` 出现在消息里是没有判别力的——把 `_require` 换成"每次都列出全部
    必需项"的目录式消息后，25 条参数化用例仍然全绿（`path` 机检存活，运维侧定位失
    效）。因此这里额外要求：消息里反引号包裹的词元中，不得出现除出错字段之外的任何
    必需字段路径。

    本断言只用于**缺字段**类用例。键集不等（多余键）与 `required_fields` 重复项的消
    息天然要同时指名多个键，套用本规则会造成假红。
    """
    named = set(_BACKTICKED.findall(str(excinfo.value)))
    others = named & (universe - {dotted_key})
    assert not others, f"消息除 `{dotted_key}` 外还罗列了其它必需字段：{sorted(others)}"


@pytest.mark.parametrize("missing_key", CONFIG_REQUIRED_KEYS)
def test_config_missing_required_key_fails_closed(tmp_path, missing_key):
    path = _write_toml(tmp_path / "config.toml", _without(VALID_CONFIG, missing_key))

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    _assert_locates(excinfo, missing_key)
    _assert_names_no_other_field(excinfo, missing_key, ALL_REQUIRED_KEYS)


@pytest.mark.parametrize("missing_key", LOCAL_REQUIRED_KEYS)
def test_local_missing_required_key_fails_closed(tmp_path, missing_key):
    config = _loaded_config(tmp_path)
    path = _write_toml(tmp_path / "local.toml", _without(VALID_LOCAL, missing_key))

    with pytest.raises(ConfigError) as excinfo:
        load_local(path, config)

    _assert_locates(excinfo, missing_key)
    _assert_names_no_other_field(excinfo, missing_key, ALL_REQUIRED_KEYS)


@pytest.mark.parametrize("loader", ["config", "local"])
def test_whole_file_failures_carry_no_field_path(tmp_path, loader):
    """整文件级失败不指向具体字段，`path` MUST 为 None（避免误导定位）。

    两个装载器都要断言：`load_local` 多包了一层现场提示，只测 `load_config` 会漏掉
    该层给整文件失败补上字段 `path` 的情况。
    """
    config = _loaded_config(tmp_path)

    def _load(target: Path) -> None:
        if loader == "config":
            load_config(target)
        else:
            load_local(target, config)

    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path / f"absent-{loader}.toml")
    assert excinfo.value.path is None

    broken = tmp_path / f"broken-{loader}.toml"
    broken.write_text('forecast_days = "unterminated\n', encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        _load(broken)
    assert excinfo.value.path is None

    directory = tmp_path / f"dir-{loader}.toml"
    directory.mkdir()
    with pytest.raises(ConfigError) as excinfo:
        _load(directory)
    assert excinfo.value.path is None


# --- 类型错误 fail closed（类型推导驱动参数化）------------------------------
#
# 断言同样以 `ConfigError.path` 为准，并要求消息里出现反引号包裹的点分路径（反引号是
# 必要的：pytest 的 tmp_path 目录名由测试名与参数拼出，裸子串探测会被目录名恒真满足）。
#
# 第三条断言取 `期望 {类型名}` 而非裸类型名，是刻意的：消息里同时出现期望类型与实际类型，
# 裸子串对"把期望与实际写反"的实现恒真——`str` 字段填 int 时，正确消息与颠倒消息都同时
# 含 "str" 与 "int"，逐类型逐字段都是如此。裸子串在本轴上零判别力，属于本 PR 反复出现的
# "偶然判别力"形态，故此处绑定 `期望` 一词。这不触及 pre-adjudicated 的
# `_require` -> `return None` 处置（那条涉及的是缺字段消息的 `缺少` 一词）。


@pytest.mark.parametrize(
    ("dotted_key", "annotated"),
    CONFIG_SCALAR_LEAVES,
    ids=[path for path, _ in CONFIG_SCALAR_LEAVES],
)
def test_config_scalar_type_error_fails_closed(tmp_path, dotted_key, annotated):
    data = _with(VALID_CONFIG, dotted_key, _WRONG_VALUE_BY_TYPE[annotated])

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, dotted_key)
    assert f"期望 {annotated.__name__}" in str(excinfo.value)


@pytest.mark.parametrize(
    ("dotted_key", "annotated"),
    LOCAL_SCALAR_LEAVES,
    ids=[path for path, _ in LOCAL_SCALAR_LEAVES],
)
def test_local_scalar_type_error_fails_closed(tmp_path, dotted_key, annotated):
    config = _loaded_config(tmp_path)
    data = _with(VALID_LOCAL, dotted_key, _WRONG_VALUE_BY_TYPE[annotated])

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    _assert_locates(excinfo, dotted_key)
    assert f"期望 {annotated.__name__}" in str(excinfo.value)


# --- 类型错误：单例（非标量轴与 bool/int 互斥，均不在上方推导轴内）-----------


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
    data["raw"]["gfs"]["lead_hours"] = ["6", 12]

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
    data["checkpoint_hours"] = [True, 12]

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


def test_local_slurm_bool_value_rejected(tmp_path):
    """`cpus = true` MUST NOT 落进 `LocalConfig.slurm`（Python 里 bool 是 int 子类）。

    放行后 Slurm 提交行会渲染成 `--cpus-per-task=True`。守卫必须走 `_is_scalar` 的 bool
    判别；写成 `isinstance(value, (str, int))` 即失守，而该写法在本用例之前全绿。
    """
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    data["slurm"]["cpus"] = True

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    _assert_locates(excinfo, "slurm.cpus")
    assert "bool" in str(excinfo.value)


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


@pytest.mark.parametrize("loader", ["config", "local"])
def test_unreadable_path_raises_config_error(tmp_path, loader):
    """路径指向目录（现场把 `--config` 传成配置目录）-> `ConfigError`，不外泄 OSError。

    `IsADirectoryError` 是 `OSError` 而非 `FileNotFoundError` 的子类，只有 `_read_toml`
    的 `except OSError` 分支能收敛它；该分支此前零测试。
    """
    config = _loaded_config(tmp_path)
    directory = tmp_path / f"{loader}-as-dir.toml"
    directory.mkdir()

    with pytest.raises(ConfigError) as excinfo:
        if loader == "config":
            load_config(directory)
        else:
            load_local(directory, config)

    assert str(directory) in str(excinfo.value)


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
    data["slurm"]["required_fields"] = [7, *SLURM_REQUIRED_FIELDS]

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_toml(tmp_path / "config.toml", data))

    _assert_locates(excinfo, "slurm.required_fields")


# --- local.[slurm] 键集：唯一权威是 config.slurm.required_fields -------------


SLURM_FIELD_PATHS = frozenset(f"slurm.{name}" for name in SLURM_REQUIRED_FIELDS)


@pytest.mark.parametrize("missing_field", SLURM_REQUIRED_FIELDS)
def test_local_slurm_missing_declared_field_fails_closed(tmp_path, missing_field):
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    del data["slurm"][missing_field]

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    _assert_locates(excinfo, f"slurm.{missing_field}")
    # 只缺一项时消息也只许指名这一项：否则"每次列出全部 required_fields"的目录式
    # 消息能让这 5 条参数化用例整体退化成 1 条
    _assert_names_no_other_field(excinfo, f"slurm.{missing_field}", SLURM_FIELD_PATHS)


def test_local_slurm_multiple_missing_fields_locate_the_first(tmp_path):
    """同时缺多项：消息全报，`path` 取排序后的第一项（代码承诺的确定性）。

    只有单项缺失的用例时，`missing[0]`/`missing[-1]`/随机取值都同样绿。
    """
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    del data["slurm"]["partition"]
    del data["slurm"]["account"]

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    _assert_locates(excinfo, "slurm.account")
    assert "`slurm.partition`" in str(excinfo.value)


@pytest.mark.parametrize("stale_field", SLURM_REQUIRED_FIELDS)
def test_local_slurm_stale_key_after_required_field_removal_rejected(
    tmp_path, stale_field
):
    """config 删掉某个 `required_fields` 项、现场 `local.toml` 忘删 -> 必须报错。

    生产五字段（partition/account/cpus/memory/walltime）逐项覆盖：装载器若对这些名字
    网开一面（把它们从"多余键"里减掉），残留的现场值会被静默忽略，现场以为改生效了。
    """
    config_data = copy.deepcopy(VALID_CONFIG)
    config_data["slurm"]["required_fields"] = [
        name for name in SLURM_REQUIRED_FIELDS if name != stale_field
    ]
    config = _loaded_config(tmp_path, config_data)

    # local.toml 原样保留全部五个键，其中 stale_field 已不在 required_fields 内
    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", VALID_LOCAL), config)

    _assert_locates(excinfo, f"slurm.{stale_field}")


def test_local_slurm_production_named_values_are_kept_verbatim(tmp_path):
    """生产名字段的值 MUST 原样透传：不 trim、不改大小写、不改标量类型。

    取值刻意对大小写与首尾空白敏感——Slurm 的 partition/account 名区分大小写，装载器
    做任何"顺手规整"都会让作业提交到别的分区或记到别的账户。用生产名（而非合成名）
    是必要的：按名字硬编码的规整只在这些名字上触发。
    """
    verbatim = {
        "partition": "  CPU-Long  ",
        "account": "YD-Forecast",
        "cpus": 8,
        "memory": "32G",
        "walltime": "04:00:00",
    }
    data = copy.deepcopy(VALID_LOCAL)
    data["slurm"] = copy.deepcopy(verbatim)

    config = _loaded_config(tmp_path)
    local = load_local(_write_toml(tmp_path / "local.toml", data), config)

    assert local.slurm == verbatim
    assert type(local.slurm["cpus"]) is int


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


def test_local_slurm_multiple_extra_keys_locate_the_first(tmp_path):
    """同时多出多项：消息全报，`path` 取排序后的第一项（代码承诺的确定性）。

    只有单项多余的用例时，`extra[0]`/`extra[-1]`/随机取值都同样绿。
    """
    config = _loaded_config(tmp_path)
    data = copy.deepcopy(VALID_LOCAL)
    data["slurm"]["qos"] = "normal"
    data["slurm"]["array"] = "0-3"

    with pytest.raises(ConfigError) as excinfo:
        load_local(_write_toml(tmp_path / "local.toml", data), config)

    _assert_locates(excinfo, "slurm.array")
    assert "`slurm.qos`" in str(excinfo.value)


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

# 零默认值断言的遍历范围必须自证：`_dataclass_tree` 若不递归，嵌套类里的默认值会被
# 整体跳过而测试照绿。此处以独立字面量清单钉死它必须走到的类。
EXPECTED_DATACLASSES = {
    "Config",
    "CycleConfig",
    "VariantsConfig",
    "RawConfig",
    "RawSourceConfig",
    "SlurmSchema",
    "LocalConfig",
    "NwmLocal",
    "CronLocal",
}


def test_dataclass_tree_reaches_every_nested_dataclass():
    walked = _dataclass_tree(Config) + _dataclass_tree(LocalConfig)

    assert {klass.__name__ for klass in walked} == EXPECTED_DATACLASSES


@pytest.mark.parametrize(
    "klass",
    # dict.fromkeys 去重：`RawSourceConfig` 在树里出现两次（raw.ifs / raw.gfs）
    list(dict.fromkeys(_dataclass_tree(Config) + _dataclass_tree(LocalConfig))),
    ids=lambda klass: klass.__name__,
)
def test_dataclass_rejects_positional_construction(klass):
    """全部 dataclass MUST 只接受关键字构造（`kw_only=True`）。

    `VariantsConfig(gfs, ifs)` 与 `RawConfig(ifs, gfs)` 用同一对字段名而顺序相反，
    `RawSourceConfig` 的 `variables`/`bundles` 相邻且同为 `tuple[str, ...]`：位置构造下
    互换实参不会报错，下游（#6 raw 完整性判定、#20 覆盖守卫）拿到的是一份"raw 永远缺"
    的静默错配，而不是一条红测试。

    实参个数刻意取满字段数：少给实参时未加 `kw_only` 的类也会因"缺必需位置参数"而抛
    `TypeError`，那是偶然判别力。给满实参时，只有 `kw_only=True` 能让它抛。
    """
    args = [object()] * len(dataclasses.fields(klass))

    with pytest.raises(TypeError):
        klass(*args)


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
