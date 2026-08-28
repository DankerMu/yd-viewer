"""`yd_producer.rawscan.judge` 完整性判定测试（tasks.md 组 3 任务 3.1）。

全部用例使用**内联合成配置值**：仓库刻意不提供版本化 `config.toml` 生产实例，真实
`variables`/`bundles`/`lead_hours` 取值归 issue #29。下方的变量名与 bundle 模式只用
于行使判定规则，不代表生产取值。

期望清单一律在用例内**字面构造**（lead 升序 × `bundles` 声明序），不调用被测模块的
任何辅助函数——否则顺序断言会退化为拿实现自身当 oracle。
"""

import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

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
    return raw_root / source / CYCLE_DIR


def literal_expected(raw_root: Path, source: str, leads, bundles) -> tuple[Path, ...]:
    """按 fixture 钉死的顺序字面构造预期清单：lead 升序，组内按 bundles 声明序。"""
    base = cycle_dir(raw_root, source)
    return tuple(
        base / pattern.format(cycle_hour=CYCLE.hour, lead=lead)
        for lead in sorted(leads)
        for pattern in bundles
    )


def populate(paths, content: bytes = b"GRIB-fixture") -> None:
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


def test_empty_cycle_hours_rejected(tmp_path):
    config = make_config(cycle_hours=())

    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, "gfs", CYCLE, config)

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


@pytest.mark.parametrize("source", ["ecmwf", "GFS", "", "raw"])
def test_unknown_source_rejected(tmp_path, source):
    with pytest.raises(ConfigError) as excinfo:
        judge(tmp_path, source, CYCLE, make_config())

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


def test_zero_offset_named_timezone_accepted(tmp_path):
    """UTC 判据是零偏移量而非 tzinfo 身份：`timezone(timedelta(0))` 同样合法。"""
    expected = literal_expected(tmp_path, "gfs", GFS_LEADS, GFS_BUNDLES)
    populate(expected)
    cycle = datetime(2026, 3, 4, 0, tzinfo=timezone(timedelta(0)))

    verdict = judge(tmp_path, "gfs", cycle, make_config())

    assert verdict.complete is True


# --- bundle 模式校验 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "pattern", "field"),
    [
        ("unknown_named", "gfs.{member}.f{lead:03d}.grib2", "member"),
        ("auto_positional", "gfs.{}.f{lead:03d}.grib2", ""),
        ("indexed_positional", "gfs.{0}.f{lead:03d}.grib2", "0"),
        ("attribute_access", "gfs.f{lead.real:03d}.grib2", "lead.real"),
        ("broken_syntax", "a{lead", None),
        ("bad_format_spec", "gfs.f{lead:s}.grib2", None),
    ],
)
def test_bundle_pattern_vocabulary_enforced(tmp_path, label, pattern, field):
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
    if field:
        assert field in message


@pytest.mark.parametrize(
    ("label", "pattern"),
    [
        ("parent_escape", "../{lead:03d}.grib2"),
        ("subdirectory", "sub/{lead:03d}.grib2"),
        ("absolute", "/etc/{lead:03d}.grib2"),
        ("dotdot_only", ".."),
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
