"""`yd_producer.rawscan.judge` 完整性判定测试（tasks.md 组 3 任务 3.1）。

全部用例使用**内联合成配置值**：仓库刻意不提供版本化 `config.toml` 生产实例，真实
`variables`/`bundles`/`lead_hours` 取值归 issue #29。下方的变量名与 bundle 模式只用
于行使判定规则，不代表生产取值。

期望清单一律在用例内**字面构造**（lead 升序 × `bundles` 声明序），不调用被测模块的
任何辅助函数——否则顺序断言会退化为拿实现自身当 oracle。
"""

import builtins
import errno
import os
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from yd_producer.config import (
    Config,
    ConfigError,
    CycleConfig,
    RawConfig,
    RawSourceConfig,
    SlurmSchema,
    VariantsConfig,
)
from yd_producer.rawscan import GFS_F000_UNAVAILABLE_VARIABLES, ScanVerdict, judge

# --- 内联合成 fixture --------------------------------------------------------

CYCLE = datetime(2026, 3, 4, 0, tzinfo=UTC)
CYCLE_DIR = "2026030400"

# 入参 source → raw 目录段。**逐字写死**，MUST NOT 从被测模块 import
# `SOURCE_DIR_NAMES`：两侧共用一个字面量就会同步漂移，把大小写断言变成恒真式。
# 事实来源：NWM@8ae9b8f2 packages/common/source_identity.py:5-9 的
# `_STORAGE_SOURCE_IDS = {"GFS": "gfs", "ERA5": "ERA5", "IFS": "IFS"}`。
DIR_SEGMENTS = {"ifs": "IFS", "gfs": "gfs"}

IFS_BUNDLES = (
    "ifs.t{cycle_hour:02d}z.f{lead:03d}.bundle.grib2",
    "ifs.t{cycle_hour:02d}z.f{lead:03d}.sfc.grib2",
)
GFS_BUNDLES = ("gfs.t{cycle_hour:02d}z.pgrb2.0p25.f{lead:03d}.bundle.grib2",)

IFS_LEADS = (0, 3, 6)
GFS_LEADS = (0, 3, 6)
IFS_VARIABLES = ("t2m", "tp")
GFS_VARIABLES = ("tmp2m", "apcp", "rh2m", "dswrf")


def make_source(
    *,
    lead_hours=IFS_LEADS,
    variables=IFS_VARIABLES,
    bundles=IFS_BUNDLES,
    f000_special=False,
) -> RawSourceConfig:
    return RawSourceConfig(
        lead_hours=tuple(lead_hours),
        variables=tuple(variables),
        bundles=tuple(bundles),
        f000_special=f000_special,
    )


def make_config(
    *,
    cycle_hours=(0, 12),
    ifs: RawSourceConfig | None = None,
    gfs: RawSourceConfig | None = None,
) -> Config:
    return Config(
        forecast_days=7,
        output_interval_minutes=60,
        checkpoint_hours=(12,),
        reach_count=3988,
        cycle=CycleConfig(hours=tuple(cycle_hours)),
        variants=VariantsConfig(gfs="input/models/yd_gfs", ifs="input/models/yd_ifs"),
        raw=RawConfig(
            ifs=ifs if ifs is not None else make_source(),
            gfs=gfs
            if gfs is not None
            else make_source(
                lead_hours=GFS_LEADS,
                variables=GFS_VARIABLES,
                bundles=GFS_BUNDLES,
                f000_special=True,
            ),
        ),
        slurm=SlurmSchema(required_fields=("partition", "account")),
    )


def cycle_dir(raw_root: Path, source: str) -> Path:
    return raw_root / DIR_SEGMENTS[source] / CYCLE_DIR


def literal_expected(raw_root: Path, source: str, leads, bundles) -> tuple[Path, ...]:
    """按 fixture 钉死的顺序字面构造预期清单：lead 升序，组内按 bundles 声明序。"""
    base = cycle_dir(raw_root, source)
    return tuple(
        base / pattern.format(cycle_hour=CYCLE.hour, lead=lead)
        for lead in sorted(leads)
        for pattern in bundles
    )


# 默认内容含**非 UTF-8 前导字节**：真实 bundle 是 GRIB2 二进制。若 `_is_readable` 退化
# 成文本模式 `open(path, "r")`，首次 `read(1)` 抛 `UnicodeDecodeError`（`ValueError` 子
# 类），被 `FS_PRIMITIVE_ERRORS` 吞掉，于是每个真实可读的原件都被记进 `unreadable_files`
# 而不报错。fixture 若全写 ASCII，这道门零判别力。
def populate(paths, content: bytes = b"GRIB\xff\x00stub") -> None:
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def tree_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    snapshot: dict[str, tuple[bytes, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            snapshot[str(path.relative_to(root))] = (
                path.read_bytes(),
                stat.st_mtime_ns,
            )
        else:
            snapshot[str(path.relative_to(root))] = (b"<dir>", 0)
    return snapshot


# --- 完整/不完整判定 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "leads", "bundles"),
    [
        ("ifs", IFS_LEADS, IFS_BUNDLES),
        ("gfs", GFS_LEADS, GFS_BUNDLES),
    ],
)
def test_complete_when_all_expected_files_exist(tmp_path, source, leads, bundles):
    expected = literal_expected(tmp_path, source, leads, bundles)
    populate(expected)

    verdict = judge(tmp_path, source, CYCLE, make_config())

    assert isinstance(verdict, ScanVerdict)
    assert verdict.expected_files == expected
    assert verdict.missing_files == ()
    assert verdict.unreadable_files == ()
    assert verdict.complete is True


def test_12z_cycle_uses_12z_dir_and_12z_filenames(tmp_path):
    """12Z 的 happy path（round 4 r4-cand-03）。

    全部产出 verdict 的用例都跑 00Z 时，把 cycle 小时带进路径的两道门（`{cycle_hour}`
    的实参、cycle 目录戳）与常量 `0` 不可区分。预期清单在此**逐字**构造：目录段
    `2026030412`、文件名含 `t12z`，MUST NOT 复用模块级 `CYCLE_DIR` 常量、`cycle_dir`
    或 `literal_expected` —— 复用即让 oracle 与被测实现共享同一个小时来源而恒真。
    """
    base = tmp_path / "gfs" / "2026030412"
    expected = (
        base / "gfs.t12z.pgrb2.0p25.f000.bundle.grib2",
        base / "gfs.t12z.pgrb2.0p25.f003.bundle.grib2",
        base / "gfs.t12z.pgrb2.0p25.f006.bundle.grib2",
    )
    populate(expected)

    verdict = judge(
        tmp_path, "gfs", datetime(2026, 3, 4, 12, tzinfo=UTC), make_config()
    )

    assert verdict.expected_files == expected
    assert verdict.complete is True


def test_missing_middle_lead_file_is_listed(tmp_path):
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    middle = cycle_dir(tmp_path, "gfs") / "gfs.t00z.pgrb2.0p25.f003.bundle.grib2"
    assert middle in expected
    middle.unlink()

    verdict = judge(tmp_path, "gfs", CYCLE, make_config())

    assert verdict.complete is False
    assert verdict.missing_files == (middle,)
    assert verdict.unreadable_files == ()
    assert verdict.expected_files == expected


def test_only_last_lead_is_not_complete(tmp_path):
    expected = literal_expected(tmp_path, "ifs", IFS_LEADS, IFS_BUNDLES)
    last_lead_files = literal_expected(tmp_path, "ifs", (max(IFS_LEADS),), IFS_BUNDLES)
    populate(last_lead_files)

    verdict = judge(tmp_path, "ifs", CYCLE, make_config())

    assert verdict.complete is False
    assert verdict.missing_files == tuple(
        path for path in expected if path not in last_lead_files
    )
    assert verdict.unreadable_files == ()


def test_absent_cycle_directory_is_incomplete_not_error(tmp_path):
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    assert not cycle_dir(tmp_path, "gfs").exists()

    verdict = judge(tmp_path, "gfs", CYCLE, make_config())

    assert verdict.complete is False
    assert verdict.missing_files == expected
    assert verdict.expected_files == expected
    assert verdict.unreadable_files == ()


def test_directory_at_expected_path_counts_as_missing(tmp_path):
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    victim = expected[1]
    victim.unlink()
    victim.mkdir()

    verdict = judge(tmp_path, "gfs", CYCLE, make_config())

    assert verdict.complete is False
    assert verdict.missing_files == (victim,)
    assert verdict.unreadable_files == ()


@pytest.mark.parametrize("kind", ["dir_symlink", "broken_symlink"])
def test_symlink_expected_paths_count_as_missing(tmp_path, kind):
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    victim = expected[1]
    victim.unlink()
    if kind == "dir_symlink":
        target = tmp_path / "some-directory"
        target.mkdir()
    else:
        target = tmp_path / "vanished.grib2"
    victim.symlink_to(target)

    verdict = judge(tmp_path, "gfs", CYCLE, make_config())

    assert verdict.complete is False
    assert verdict.missing_files == (victim,)
    assert verdict.unreadable_files == ()


def test_symlink_to_regular_file_counts_as_present(tmp_path):
    """判据是「**跟随** symlink 后仍须是普通文件」，两半都要取证。

    上一个用例只行使了"仍须是普通文件"那一半；这里行使"跟随"那一半：若实现改用
    `lstat()`，symlink 自身不是普通文件，这个真实存在、真实可读的原件会被误判成缺失。
    目标文件是 cycle 目录内的一个游离文件，不属预期集（判定期不列目录，故无副作用）。
    """
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    victim = expected[1]
    victim.unlink()
    target = victim.parent / "actual-payload.grib2"
    populate((target,))
    victim.symlink_to(target)
    assert victim.is_symlink()

    verdict = judge(tmp_path, "gfs", CYCLE, make_config())

    assert verdict.complete is True
    assert verdict.missing_files == ()
    assert verdict.unreadable_files == ()
    assert verdict.expected_files == expected


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root 绕过 DAC 权限位，chmod 0o000 仍可读"
)
def test_unreadable_file_is_reported_separately(tmp_path):
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    victim = expected[2]
    victim.chmod(0o000)
    try:
        verdict = judge(tmp_path, "gfs", CYCLE, make_config())
    finally:
        victim.chmod(0o644)

    assert verdict.complete is False
    assert verdict.unreadable_files == (victim,)
    assert verdict.missing_files == ()


def test_extraneous_files_are_not_discovered(tmp_path):
    """判定期不列目录：预期集严格由 lead_hours × bundles 构造。"""
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    strays = (
        cycle_dir(tmp_path, "gfs") / "gfs.t00z.pgrb2.0p25.f999.bundle.grib2",
        cycle_dir(tmp_path, "gfs") / "manifest.json",
    )
    populate(strays)

    verdict = judge(tmp_path, "gfs", CYCLE, make_config())

    assert verdict.expected_files == expected
    for stray in strays:
        assert stray not in verdict.expected_files
    assert verdict.complete is True


def test_judge_does_not_touch_raw_tree(tmp_path):
    expected = literal_expected(tmp_path, "ifs", IFS_LEADS, IFS_BUNDLES)
    populate(expected)
    before = tree_snapshot(tmp_path)

    verdict = judge(tmp_path, "ifs", CYCLE, make_config())

    assert verdict.complete is True
    assert tree_snapshot(tmp_path) == before


# --- f000 特例 ---------------------------------------------------------------


def test_f000_special_trims_lead_zero_variables_only(tmp_path):
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    f000_file = expected[0]

    verdict = judge(tmp_path, "gfs", CYCLE, make_config())

    assert f000_file in verdict.expected_files
    assert f000_file not in verdict.missing_files
    assert verdict.complete is True
    assert verdict.expected_variables[0] == tuple(
        name for name in GFS_VARIABLES if name not in ("apcp", "dswrf")
    )
    assert verdict.expected_variables[3] == GFS_VARIABLES
    assert verdict.expected_variables[6] == GFS_VARIABLES


def test_f000_special_disabled_keeps_full_variable_set(tmp_path):
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    config = make_config(
        gfs=make_source(
            lead_hours=GFS_LEADS,
            variables=GFS_VARIABLES,
            bundles=GFS_BUNDLES,
            f000_special=False,
        )
    )

    verdict = judge(tmp_path, "gfs", CYCLE, config)

    assert verdict.expected_variables[0] == GFS_VARIABLES
    assert verdict.complete is True


def test_f000_special_still_requires_the_f000_file(tmp_path):
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    f000_file = expected[0]
    f000_file.unlink()

    verdict = judge(tmp_path, "gfs", CYCLE, make_config())

    assert verdict.complete is False
    assert f000_file in verdict.expected_files
    assert verdict.missing_files == (f000_file,)


def test_f000_degenerate_variable_set_is_empty_tuple(tmp_path):
    config = make_config(
        gfs=make_source(
            lead_hours=GFS_LEADS,
            variables=("apcp", "dswrf"),
            bundles=GFS_BUNDLES,
            f000_special=True,
        )
    )
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)

    verdict = judge(tmp_path, "gfs", CYCLE, config)

    assert expected[0] in verdict.expected_files
    assert verdict.expected_variables[0] == ()
    assert verdict.expected_variables[3] == ("apcp", "dswrf")
    assert verdict.complete is True


def test_f000_unavailable_variables_constant_is_pinned():
    assert GFS_F000_UNAVAILABLE_VARIABLES == frozenset({"apcp", "dswrf"})


# --- 配置取值域校验 ----------------------------------------------------------


def test_cycle_hours_outside_domain_rejected(tmp_path):
    config = make_config(cycle_hours=(0, 6, 12))

    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "gfs", CYCLE, config)

    assert excinfo.value.path == "cycle.hours"
    # 报文 MUST 点名**犯规取值**：只断言 path 时，把报文里的 `illegal` 枚举删掉不会
    # 变红，运维就只知道"这项配错了"而不知道错在哪个小时。
    assert "6" in str(excinfo.value)


def test_empty_cycle_hours_rejected(tmp_path):
    """空 `cycle.hours` 必须由**取值域**那道门拒掉，且它排在词表校验之前。

    故意配一个词表外的 `source`：若删掉空列表守卫，`cycle.hour not in ()` 那道请求门
    会给出同样的 `path == "cycle.hours"`（零判别力）；而在词表外 source 下，删掉守卫
    后先抛的是 source 词表错误（`path is None`），用例即变红。
    """
    config = make_config(cycle_hours=())

    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "ecmwf", CYCLE, config)

    assert excinfo.value.path == "cycle.hours"


@pytest.mark.parametrize(
    "dotted_path",
    [
        "raw.ifs.lead_hours",
        "raw.ifs.variables",
        "raw.ifs.bundles",
        "raw.gfs.lead_hours",
        "raw.gfs.variables",
        "raw.gfs.bundles",
    ],
)
def test_empty_raw_list_rejected(tmp_path, dotted_path):
    """两个源都查：请求 `ifs` 时 `raw.gfs.*` 的空列表同样必须被拒。"""
    _, source, field = dotted_path.split(".")
    overrides = {
        "ifs": make_source(),
        "gfs": make_source(
            lead_hours=GFS_LEADS,
            variables=GFS_VARIABLES,
            bundles=GFS_BUNDLES,
            f000_special=True,
        ),
    }
    overrides[source] = make_source(
        **{
            "lead_hours": overrides[source].lead_hours,
            "variables": overrides[source].variables,
            "bundles": overrides[source].bundles,
            "f000_special": overrides[source].f000_special,
            field: (),
        }
    )

    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "ifs", CYCLE, make_config(**overrides))

    assert excinfo.value.path == dotted_path


# --- 请求校验（MUST 发生在任何文件系统访问之前）------------------------------


@pytest.mark.parametrize("raw_root_exists", [True, False])
def test_non_00_12_cycle_rejected(tmp_path, raw_root_exists):
    raw_root = tmp_path / "raw" if raw_root_exists else tmp_path / "absent-root"
    if raw_root_exists:
        raw_root.mkdir()
    cycle = datetime(2026, 3, 4, 6, tzinfo=UTC)

    with pytest.raises(ConfigError) as excinfo:
        judge(raw_root, "gfs", cycle, make_config())

    assert excinfo.value.path == "cycle.hours"


def test_cycle_hour_outside_configured_hours_rejected(tmp_path):
    """请求门的判据 MUST 是 `config.cycle.hours` 而非常量域（round 4 r4-cand-02）。

    `cycle.hours` 取 `{0, 12}` 的真子集是设计内合法取值（运维用 `cycle.hours = [0]`
    表达"本环境只跑 00Z"）。上方 06Z 用例走默认 `(0, 12)`，06 在两个判据下同样被拒，
    对本条零判别力；这里 12Z 落在常量域内、落在配置域外，两个判据由此分开。
    """
    with pytest.raises(ConfigError) as excinfo:
        judge(
            tmp_path,
            "gfs",
            datetime(2026, 3, 4, 12, tzinfo=UTC),
            make_config(cycle_hours=(0,)),
        )

    assert excinfo.value.path == "cycle.hours"
    # 报文 MUST 点名**配置声明的**小时集（而非常量域），否则删掉该枚举不变红。
    assert "00Z" in str(excinfo.value)


@pytest.mark.parametrize("source", ["ecmwf", "GFS", "", "raw"])
def test_unknown_source_rejected(tmp_path, source):
    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, source, CYCLE, make_config())

    assert excinfo.value.path is None
    # 报文 MUST 点名合法词表：`path is None` 这一条对报文内容零判别力。
    assert "'ifs'" in str(excinfo.value)
    assert "'gfs'" in str(excinfo.value)


@pytest.mark.parametrize(
    "cycle", [date(2026, 3, 4), "2026030400", 1772582400, None], ids=str
)
def test_non_datetime_cycle_rejected(tmp_path, cycle):
    """非 `datetime` 的 cycle MUST 落在类型守卫上，MUST NOT 让下一行 `utcoffset()`
    抛裸 `AttributeError`（judge 对外只抛 `ConfigError`）。

    `date` 是最现实的一种传参错：`date` 不是 `datetime` 的子类的反面——`datetime`
    **是** `date` 的子类，故上游任何写成 `date` 的日期戳都会静默走到这里。
    """
    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "gfs", cycle, make_config())

    assert excinfo.value.path is None


@pytest.mark.parametrize(
    ("label", "cycle"),
    [
        ("naive", datetime(2026, 3, 4, 0)),  # noqa: DTZ001 — 本用例专测 naive 被拒
        ("non_utc", datetime(2026, 3, 4, 0, tzinfo=timezone(timedelta(hours=8)))),
    ],
)
def test_cycle_must_be_utc_aware(tmp_path, label, cycle):
    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "gfs", cycle, make_config())

    assert excinfo.value.path is None


@pytest.mark.parametrize(
    ("label", "cycle"),
    [
        ("minute", datetime(2026, 3, 4, 0, 37, tzinfo=UTC)),
        ("second", datetime(2026, 3, 4, 0, 0, 9, tzinfo=UTC)),
        ("microsecond", datetime(2026, 3, 4, 0, 0, 0, 5, tzinfo=UTC)),
    ],
)
def test_cycle_with_nonzero_subhour_rejected(tmp_path, label, cycle):
    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "gfs", cycle, make_config())

    assert excinfo.value.path is None


@pytest.mark.parametrize(
    ("label", "tzinfo"),
    [
        ("zoneinfo_utc", ZoneInfo("UTC")),
        ("named_zero_offset", timezone(timedelta(0), name="Z")),
    ],
)
def test_named_zero_offset_timezone_accepted(tmp_path, label, tzinfo):
    """UTC 判据是零偏移量而非 tzinfo 身份。

    MUST NOT 用 `timezone(timedelta(0))` 取证：CPython 对无名零偏移直接返回
    `timezone.utc` 单例，那个取值传进去的就是 `timezone.utc` 本身，对本条零判别力。
    这里两个取值的 `tzinfo is timezone.utc` 均为 False。
    """
    assert tzinfo is not UTC
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    cycle = datetime(2026, 3, 4, 0, tzinfo=tzinfo)

    verdict = judge(tmp_path, "gfs", cycle, make_config())

    assert verdict.complete is True


# --- bundle 模式校验 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "pattern", "field", "reason"),
    [
        ("unknown_named", "gfs.{member}.f{lead:03d}.grib2", "member", "词表外的字段"),
        ("auto_positional", "gfs.{}.f{lead:03d}.grib2", "", "自动编号的位置字段"),
        ("indexed_positional", "gfs.{0}.f{lead:03d}.grib2", "0", "位置字段"),
        # 模式里另含一个裸 `{lead}`：否则 `fields` 只有 `lead.real`，"必须含
        # `{lead}`"那道门会先拦下它，而两道门的报文都含模式本身与 `lead.real`
        # 子串，本参数就分不出词表门在不在（削弱词表门后 `(0).real` 正常渲染，
        # judge 直接返回 verdict，用例变红）。
        (
            "attribute_access",
            "gfs.f{lead:03d}.{lead.real}.grib2",
            "lead.real",
            "词表外的字段",
        ),
        ("broken_syntax", "a{lead", None, "语法错误"),
        ("bad_format_spec", "gfs.f{lead:s}.grib2", None, "渲染失败"),
    ],
)
def test_bundle_pattern_vocabulary_enforced(tmp_path, label, pattern, field, reason):
    config = make_config(
        gfs=make_source(
            lead_hours=GFS_LEADS,
            variables=GFS_VARIABLES,
            bundles=(pattern,),
            f000_special=True,
        )
    )

    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "gfs", CYCLE, config)

    assert excinfo.value.path == "raw.gfs.bundles"
    message = str(excinfo.value)
    assert pattern in message
    # 拒绝**归因** MUST 落在真正犯规的那道门/那一类上：六个参数分属四种成因（词表外
    # 具名、自动编号位置、编号位置、模式语法、渲染），报文若把它们混为一谈，运维就得
    # 靠猜来改 `config.toml`。这条断言也是"分类支存在"的唯一取证——把 `field == ""`
    # 或 `field.isdigit()` 支变异掉不改变任何返回值，只改这段归因文本。
    assert reason in message
    if field is not None:
        # `is not None` 而非真值判断：`auto_positional` 的 field 是 `""`，真值判断会
        # 把它静默跳过，该参数就退化成"只要抛 ConfigError 就算过"。
        assert field in message
        # 词表门 MUST 是拒绝的**来源**，MUST NOT 由 `_render` 的异常兜底偶然满足：
        # 后者的 `__cause__` 是 KeyError/IndexError，且报文里没有词表枚举句。
        assert not isinstance(excinfo.value.__cause__, (KeyError, IndexError))
        assert "只接受" in message


@pytest.mark.parametrize(
    "pattern",
    [
        "gfs.f{lead:{member}}.grib2",
        "gfs.f{lead:{0}}.grib2",
        "gfs.f{lead:{lead[0]}}.grib2",
        "gfs.f{lead:{lead.nosuch}}.grib2",
    ],
)
def test_nested_spec_render_failure_is_configerror(tmp_path, pattern):
    """嵌套 format_spec 绕过词表门后的渲染失败（round 4 r4-cand-01）。

    `string.Formatter().parse` 不暴露嵌套格式说明符内的字段（实现注释 rawscan.py:212
    已载明），故内层字段对词表门不可见、`{lead}` 门放行，直到 `_render` 的
    `str.format` 才抛。四个内层形态分别触发 `KeyError`/`IndexError`/`TypeError`/
    `AttributeError`，是 `_render` 那个 except 五元组里除 `ValueError` 外四腿在本模块
    被声明输入域上的唯一取证。

    **MUST NOT 断言 `__cause__` 的异常类型**：日后若在 `_validate_pattern` 补
    format_spec 门，该异常将由词表门直接 raise 而无 `__cause__`，钉 cause 会让用例
    二次重写。
    """
    config = make_config(
        gfs=make_source(
            lead_hours=GFS_LEADS,
            variables=GFS_VARIABLES,
            bundles=(pattern,),
            f000_special=True,
        )
    )

    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "gfs", CYCLE, config)

    assert excinfo.value.path == "raw.gfs.bundles"


@pytest.mark.parametrize(
    ("label", "pattern"),
    [
        ("parent_escape", "../{lead:03d}.grib2"),
        ("subdirectory", "sub/{lead:03d}.grib2"),
        ("absolute", "/etc/{lead:03d}.grib2"),
    ],
)
def test_bundle_pattern_must_render_single_filename(tmp_path, label, pattern):
    config = make_config(
        gfs=make_source(
            lead_hours=GFS_LEADS,
            variables=GFS_VARIABLES,
            bundles=(pattern,),
            f000_special=True,
        )
    )

    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "gfs", CYCLE, config)

    assert excinfo.value.path == "raw.gfs.bundles"
    assert pattern in str(excinfo.value)


def test_bundle_pattern_of_other_source_is_not_rendered(tmp_path):
    """模式校验只针对被请求的源；phase 1 的双源校验只看空列表。"""
    config = make_config(
        ifs=make_source(bundles=("sub/{lead:03d}.grib2",)),
    )
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)

    verdict = judge(tmp_path, "gfs", CYCLE, config)

    assert verdict.complete is True


# --- 目录段身份（NWM 存储身份逐源非对称）------------------------------------


@pytest.mark.parametrize(("source", "segment"), [("ifs", "IFS"), ("gfs", "gfs")])
def test_source_directory_segment_is_pinned_case_sensitively(tmp_path, source, segment):
    """入参 `source`（恒小写）与目录段是两个身份：IFS 落大写、GFS 落小写。

    判别力来自路径的**字面**比对：macOS 文件系统大小写不敏感，只断言"文件存在"在
    小写实现下同样为真。期望段用本文件顶部逐字写死的 `DIR_SEGMENTS`，不复用实现。
    """
    verdict = judge(tmp_path, source, CYCLE, make_config())

    assert segment == DIR_SEGMENTS[source]
    for path in verdict.expected_files:
        assert path.parent.name == CYCLE_DIR
        assert path.parent.parent.name == segment
        assert path.parent.parent.parent == tmp_path


# --- 预期集单射性（模式必须含 {lead}）---------------------------------------


@pytest.mark.parametrize(
    ("label", "pattern"),
    [
        ("no_placeholder_at_all", "gfs.bundle.grib2"),
        ("only_cycle_hour", "gfs.t{cycle_hour:02d}z.bundle.grib2"),
        # `string.Formatter().parse` 不暴露嵌套格式说明符内的字段，故只把 `lead`
        # 写在 spec 里同样被拒（fail-closed，实现注释已写明该方向）。
        ("lead_only_inside_format_spec", "gfs.f{cycle_hour:0{lead}d}.grib2"),
    ],
)
def test_bundle_pattern_without_lead_rejected(tmp_path, label, pattern):
    config = make_config(
        gfs=make_source(
            lead_hours=GFS_LEADS,
            variables=GFS_VARIABLES,
            bundles=(pattern,),
            f000_special=True,
        )
    )

    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "gfs", CYCLE, config)

    assert excinfo.value.path == "raw.gfs.bundles"
    assert pattern in str(excinfo.value)
    assert "lead" in str(excinfo.value)


def test_expected_files_have_no_duplicates_on_legal_config(tmp_path):
    verdict = judge(tmp_path, "ifs", CYCLE, make_config())

    assert len(set(verdict.expected_files)) == len(verdict.expected_files)
    assert len(verdict.expected_files) == len(IFS_LEADS) * len(IFS_BUNDLES)


@pytest.mark.parametrize(
    ("label", "source_kwargs", "dotted_path"),
    [
        (
            "two_patterns_render_the_same_name",
            {
                "bundles": (
                    "gfs.f{lead:03d}.grib2",
                    "gfs.f{lead:0>3d}.grib2",
                )
            },
            "raw.gfs.bundles",
        ),
        (
            "duplicate_bundle_element",
            {"bundles": ("gfs.f{lead:03d}.grib2", "gfs.f{lead:03d}.grib2")},
            "raw.gfs.bundles",
        ),
        (
            "duplicate_lead_hours",
            {"lead_hours": (0, 3, 3, 6)},
            "raw.gfs.lead_hours",
        ),
    ],
)
def test_expected_set_must_be_injective(tmp_path, label, source_kwargs, dotted_path):
    """预期集塌缩与预期集为空是同一病理的两扇门，都必须 fail closed。"""
    kwargs = {
        "lead_hours": GFS_LEADS,
        "variables": GFS_VARIABLES,
        "bundles": GFS_BUNDLES,
        "f000_special": True,
        **source_kwargs,
    }
    config = make_config(gfs=make_source(**kwargs))

    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "gfs", CYCLE, config)

    assert excinfo.value.path == dotted_path
    if label == "duplicate_lead_hours":
        # 报文 MUST 点名重复的那个 lead。
        assert "lead 3" in str(excinfo.value)


@pytest.mark.parametrize(
    ("label", "pattern", "rendered"),
    [
        # `{lead!r:.0}` 把 lead 转成 str 后截断到 0 位精度，渲染出空串；三个模式都含
        # `{lead}`，故都越过"必须含 {lead}"那道门，专测渲染结果的单文件名约束。
        ("renders_empty", "{lead!r:.0}", ""),
        ("renders_dot", "{lead!r:.0}.", "."),
        ("renders_dotdot", "{lead!r:.0}..", ".."),
    ],
)
def test_bundle_pattern_rendering_to_degenerate_name_rejected(
    tmp_path, label, pattern, rendered
):
    """`""`/`"."`/`".."` 三个退化取值都必须被单文件名约束拒。

    构造上有两处刻意：模式 MUST 含 `{lead}`（裸 `".."` 会先被"必须含 `{lead}`"那道门
    拦下，而"抛 `ConfigError` 且 `path` 为 bundles 点分路径"在两道门下同样成立，于是
    对本约束零判别力）；`lead_hours` MUST 取单元素（多 lead 下这些模式会撞进单射性
    那道门）。断言里比对渲染结果的 `repr` 也是为此：它把本门的报文与 `{lead}` 门的
    报文区分开。
    """
    assert pattern.format(lead=0) == rendered
    config = make_config(
        gfs=make_source(
            lead_hours=(0,),
            variables=GFS_VARIABLES,
            bundles=(pattern,),
            f000_special=True,
        )
    )

    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "gfs", CYCLE, config)

    assert excinfo.value.path == "raw.gfs.bundles"
    assert repr(rendered) in str(excinfo.value)


# --- lead 升序（字面 oracle，不复用助手内的 sorted）--------------------------


def test_expected_files_are_ordered_by_ascending_lead(tmp_path):
    """`lead_hours` 书写顺序不作数：期望清单逐字写出，不经任何 `sorted`。"""
    config = make_config(
        gfs=make_source(
            lead_hours=(6, 0, 3),
            variables=GFS_VARIABLES,
            bundles=GFS_BUNDLES,
            f000_special=True,
        )
    )
    base = tmp_path / "gfs" / "2026030400"
    expected = (
        base / "gfs.t00z.pgrb2.0p25.f000.bundle.grib2",
        base / "gfs.t00z.pgrb2.0p25.f003.bundle.grib2",
        base / "gfs.t00z.pgrb2.0p25.f006.bundle.grib2",
    )
    populate(expected)

    verdict = judge(tmp_path, "gfs", CYCLE, config)

    assert verdict.expected_files == expected
    assert verdict.complete is True


# --- raw_root 形态 -----------------------------------------------------------


def test_relative_raw_root_is_promoted_to_absolute(tmp_path, monkeypatch):
    base = tmp_path / "raw-root" / "gfs" / "2026030400"
    expected = (
        base / "gfs.t00z.pgrb2.0p25.f000.bundle.grib2",
        base / "gfs.t00z.pgrb2.0p25.f003.bundle.grib2",
        base / "gfs.t00z.pgrb2.0p25.f006.bundle.grib2",
    )
    populate(expected)
    monkeypatch.chdir(tmp_path)

    verdict = judge("raw-root", "gfs", CYCLE, make_config())

    assert all(path.is_absolute() for path in verdict.expected_files)
    assert verdict.expected_files == expected
    assert verdict.complete is True


@pytest.mark.parametrize("raw_root", [123, None, 4.5, object()])
def test_non_pathlike_raw_root_rejected(raw_root):
    """不得外泄裸 `TypeError`：本模块对外只有 `ConfigError`。"""
    with pytest.raises(ConfigError) as excinfo:
        judge(raw_root, "gfs", CYCLE, make_config())

    assert excinfo.value.path is None


# --- 判定期不访问文件系统（哨兵）--------------------------------------------


# 被桩原语的调用记录。判别力 MUST 落在"有没有被调用"上，MUST NOT 落在"桩抛了什么
# 异常"：任何宽捕获（`except Exception`，乃至裸 `except:`）都能把桩抛出的异常吞掉，
# 那样的哨兵对"实现里多了一个带兜底的探针"零判别力。`AssertionError` 仍然抛（纵深：
# 桩被调用后立即中止，不让被测代码拿到伪造的返回值继续往下走），但断言看的是 `_CALLS`。
# MUST NOT 改用 `BaseException` 子类——裸 `except:` 同样吞得掉，换不来判别力。
_CALLS: list[str] = []


def _recording_boom(name: str):
    """记录式哨兵桩：先记下被调原语名，再抛 `AssertionError`。"""

    def stub(*args, **kwargs):
        _CALLS.append(name)
        raise AssertionError(f"判定期不得访问文件系统：{name}")

    return stub


def _reject_case_configs():
    """全部拒绝路径：每项为 (label, source, cycle, config)。"""
    return [
        (
            "cycle_hours_out_of_domain",
            "gfs",
            CYCLE,
            make_config(cycle_hours=(0, 6, 12)),
        ),
        ("empty_cycle_hours", "gfs", CYCLE, make_config(cycle_hours=())),
        (
            "empty_bundles",
            "gfs",
            CYCLE,
            make_config(gfs=make_source(bundles=(), lead_hours=GFS_LEADS)),
        ),
        (
            "empty_lead_hours",
            "gfs",
            CYCLE,
            make_config(gfs=make_source(lead_hours=(), bundles=GFS_BUNDLES)),
        ),
        (
            "empty_variables",
            "gfs",
            CYCLE,
            make_config(
                gfs=make_source(variables=(), lead_hours=GFS_LEADS, bundles=GFS_BUNDLES)
            ),
        ),
        ("unknown_source", "ecmwf", CYCLE, make_config()),
        ("naive_cycle", "gfs", datetime(2026, 3, 4, 0), make_config()),  # noqa: DTZ001
        (
            "non_utc_cycle",
            "gfs",
            datetime(2026, 3, 4, 0, tzinfo=timezone(timedelta(hours=8))),
            make_config(),
        ),
        ("non_hourly_cycle", "gfs", datetime(2026, 3, 4, 0, 37, tzinfo=UTC), None),
        (
            "bad_pattern",
            "gfs",
            CYCLE,
            make_config(
                gfs=make_source(
                    lead_hours=GFS_LEADS,
                    bundles=("gfs.{member}.f{lead:03d}.grib2",),
                )
            ),
        ),
        (
            "pattern_without_lead",
            "gfs",
            CYCLE,
            make_config(
                gfs=make_source(lead_hours=GFS_LEADS, bundles=("gfs.bundle.grib2",))
            ),
        ),
        (
            "pattern_escapes_cycle_dir",
            "gfs",
            CYCLE,
            make_config(
                gfs=make_source(lead_hours=GFS_LEADS, bundles=("sub/{lead:03d}.grib2",))
            ),
        ),
        (
            "pattern_renders_dotdot",
            "gfs",
            CYCLE,
            make_config(gfs=make_source(lead_hours=(0,), bundles=("{lead!r:.0}..",))),
        ),
        (
            "colliding_bundles",
            "gfs",
            CYCLE,
            make_config(
                gfs=make_source(
                    lead_hours=GFS_LEADS,
                    bundles=("gfs.f{lead:03d}.grib2", "gfs.f{lead:0>3d}.grib2"),
                )
            ),
        ),
        (
            "duplicate_lead_hours",
            "gfs",
            CYCLE,
            make_config(gfs=make_source(lead_hours=(0, 3, 3), bundles=GFS_BUNDLES)),
        ),
    ]


@pytest.mark.parametrize(
    ("label", "source", "cycle", "config"),
    _reject_case_configs(),
    ids=[case[0] for case in _reject_case_configs()],
)
def test_rejections_happen_before_any_filesystem_access(
    tmp_path, monkeypatch, label, source, cycle, config
):
    """哨兵取证：把文件系统入口换成必炸的桩，且**桩在 `os` 层**。

    只以"不存在的 `raw_root`"取证不够——它只杀得掉不吞异常的探针，任何带 `is_dir()`
    守卫或 `except OSError` 的探针照样存活。

    桩 MUST 落在 `os.stat`/`os.scandir`/`os.listdir` 这三个**汇流处**，MUST NOT 只点
    名 pathlib 侧的某个方法：`pathlib.Path.stat` 就是 `return os.stat(self, ...)`、
    `os.walk` 经 `os.scandir`，而点名具体拼法的桩会随实现换原语静默变成死桩（本文件
    上一版桩 `Path.is_file`，实现改用 `Path.stat()` 后五个探针变异体全部存活）。桩是
    **记录式**的（见 `_recording_boom`）：判据是 `_CALLS == []`，即"这些原语一次都没被
    调用"，而不是"桩抛出的异常穿出了 judge"——后者对任何带 `except Exception` 兜底的
    探针零判别力。pathlib 侧与 `builtins.open` 的桩保留作纵深。
    """
    _CALLS.clear()
    for module, attr in (
        (os, "stat"),
        (os, "scandir"),
        (os, "listdir"),
        (Path, "is_file"),
        (Path, "iterdir"),
        (Path, "rglob"),
        (Path, "glob"),
        (builtins, "open"),
    ):
        monkeypatch.setattr(module, attr, _recording_boom(f"{module.__name__}.{attr}"))

    try:
        with pytest.raises(ConfigError):
            judge(
                tmp_path, source, cycle, config if config is not None else make_config()
            )
    finally:
        # 显式在用例体内撤桩：`os.stat` 是全局名，桩若活到 fixture 拆卸期会连 pytest
        # 自己的 tmp_path 清理一起炸掉，把"用例红了"污染成整场会话的噪声。
        monkeypatch.undo()

    assert _CALLS == []


def test_happy_path_never_lists_directories(tmp_path, monkeypatch):
    """预期集严格由 `lead_hours × bundles` 构造：判定期一次目录枚举都不发生。

    只桩**枚举**原语（`os.scandir`/`os.listdir` 及 pathlib 侧拼法），MUST NOT 桩
    `os.stat`/`Path.stat`：逐文件 stat 正是 happy path 的规定行为，也 MUST NOT 桩
    `builtins.open`：`_is_readable` 的真实读同理。判据同样是 `_CALLS == []`。
    """
    _CALLS.clear()
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    for module, attr in (
        (os, "scandir"),
        (os, "listdir"),
        (Path, "iterdir"),
        (Path, "rglob"),
        (Path, "glob"),
        (Path, "walk"),
    ):
        monkeypatch.setattr(module, attr, _recording_boom(f"{module.__name__}.{attr}"))

    try:
        verdict = judge(tmp_path, "gfs", CYCLE, make_config())
    finally:
        monkeypatch.undo()

    assert _CALLS == []
    assert verdict.expected_files == expected
    assert verdict.complete is True


# --- 可读性判据与目录权限 ----------------------------------------------------


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root 绕过 DAC 权限位，chmod 0o000 仍可读"
)
def test_readability_is_decided_by_real_open_not_os_access(tmp_path, monkeypatch):
    """`chmod 0o000` 在普通 DAC 下两种实现结论一致，故必须直接机检调用面。"""
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    victim = expected[2]
    victim.chmod(0o000)
    _CALLS.clear()
    monkeypatch.setattr(os, "access", _recording_boom("os.access"))
    try:
        verdict = judge(tmp_path, "gfs", CYCLE, make_config())
    finally:
        victim.chmod(0o644)

    # 同记录式判据：`os.access` 一次都不该被调用。抛出的 `AssertionError` 若被实现里
    # 的宽捕获吞掉，本断言仍然红。
    assert _CALLS == []
    assert verdict.complete is False
    assert verdict.unreadable_files == (victim,)
    assert verdict.missing_files == ()


def test_open_succeeds_but_first_read_fails_is_unreadable(tmp_path, monkeypatch):
    """ "可读"的判据是**真的读出一个字节**，不是"`open` 没报错"。

    生产 raw 根在 NFS 上：陈旧文件句柄（ESTALE）与 EIO 都会让 `open` 成功、首次
    `read` 失败。这是本地权限位造不出来的形态，故在 `builtins.open` 这个系统边界上
    做定向注入——只对一个受害路径生效，其余路径走真实 `open`。
    """
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    victim = expected[1]
    real_open = builtins.open

    class _ReadFails:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._handle.__exit__(*exc_info)

        def read(self, *args, **kwargs):
            raise OSError(errno.EIO, "Input/output error")

    def fake_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        if os.fspath(file) == os.fspath(victim):
            return _ReadFails(handle)
        return handle

    monkeypatch.setattr(builtins, "open", fake_open)
    try:
        verdict = judge(tmp_path, "gfs", CYCLE, make_config())
    finally:
        monkeypatch.undo()

    assert verdict.complete is False
    assert verdict.unreadable_files == (victim,)
    assert verdict.missing_files == ()


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root 绕过 DAC 权限位，chmod 0o000 仍可搜索"
)
def test_unsearchable_cycle_directory_is_unreadable_not_an_exception(tmp_path):
    """生产 raw 根是 NFS 上由 NWM 以另一 uid 写入、cycle 目录常缺 x 位的形态。

    `Path.is_file()` 在 EACCES 上抛，未包裹就会以裸 `PermissionError` 逃出 `judge`；
    此时根本走不到 `open`，`unreadable_files` 分支反而不可达。
    """
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    victim_dir = cycle_dir(tmp_path, "gfs")
    victim_dir.chmod(0o000)
    try:
        verdict = judge(tmp_path, "gfs", CYCLE, make_config())
    finally:
        victim_dir.chmod(0o755)

    assert verdict.complete is False
    assert verdict.unreadable_files == expected
    assert verdict.missing_files == ()


def test_regular_file_at_cycle_directory_position_is_missing(tmp_path):
    """`<cycle>` 位置上是普通文件（NWM 半写、人工误放、`raw_root` 配错一层）。

    与上一个用例成对，钉死第 4 段的**两支分类**：`stat` 的 `NotADirectoryError`
    （ENOTDIR）归 `missing_files`，其余 `OSError` 归 `unreadable_files`。只有 ENOENT
    与 EACCES 的用例分不出 `NotADirectoryError` 落在哪一支——把它从 missing 支删掉，
    那两个用例照样全绿。macOS/Linux 对 `stat("<普通文件>/child")` 均抛
    `NotADirectoryError`，无平台依赖。
    """
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    impostor = cycle_dir(tmp_path, "gfs")
    impostor.parent.mkdir(parents=True)
    impostor.write_bytes(b"not a directory")

    verdict = judge(tmp_path, "gfs", CYCLE, make_config())

    assert verdict.complete is False
    assert verdict.missing_files == expected
    assert verdict.unreadable_files == ()


# --- 文件系统原语的异常面全部收敛（judge 对外只抛 ConfigError）---------------


def test_nul_in_bundle_pattern_does_not_leak_valueerror(tmp_path):
    """NUL 走的是文件系统原语的**第二条腿**：`ValueError`，不是 `OSError`。

    `tomllib` 接受 TOML 的 `\\u0000` 转义并产出含 NUL 的 str，装载器只做类型校验，
    故这是配置能承载的真实输入。含 NUL 的路径递不进系统调用，`Path.stat()`/`open()`
    抛裸 `ValueError`；被本模块替换掉的 `Path.is_file()` 原本把它吞成 False，漏接即
    是回归——`judge` 对外只抛 `ConfigError`，"不完整"必须以 verdict 返回。
    """
    pattern = "gfs.f{lead:03d}\x00.grib2"
    config = make_config(
        gfs=make_source(
            lead_hours=GFS_LEADS,
            variables=GFS_VARIABLES,
            bundles=(pattern,),
            f000_special=True,
        )
    )
    populate(literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES))

    verdict = judge(tmp_path, "gfs", CYCLE, config)

    assert verdict.complete is False
    assert verdict.missing_files == verdict.expected_files
    assert verdict.unreadable_files == ()
    assert all("\x00" in path.name for path in verdict.expected_files)


def test_nul_in_raw_root_does_not_leak_valueerror(tmp_path):
    """第二条腿的第二个入口：`raw_root` 自身含 NUL。

    这支**根本不经过** `_render`，故只在渲染侧加 NUL 守卫关不掉它；收敛必须落在
    文件系统原语的调用点上。
    """
    verdict = judge(f"{tmp_path}\x00", "gfs", CYCLE, make_config())

    assert verdict.complete is False
    assert verdict.missing_files == verdict.expected_files
    assert verdict.unreadable_files == ()


def test_unavailable_cwd_is_configerror_not_bare_oserror(tmp_path, monkeypatch):
    """相对 `raw_root` 的提升点 `Path.cwd()`（即 `os.getcwd()`）同样是原语。

    进程 cwd 被删除后 `os.getcwd()` 抛 `OSError`；不收敛就会以裸异常穿出 `judge`。
    """
    doomed = tmp_path / "doomed-cwd"
    doomed.mkdir()
    monkeypatch.chdir(doomed)
    doomed.rmdir()

    with pytest.raises(ConfigError) as excinfo:
        judge("raw-root", "gfs", CYCLE, make_config())

    assert excinfo.value.path is None
