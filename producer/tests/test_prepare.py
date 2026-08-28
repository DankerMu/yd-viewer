"""`yd_producer.prepare` 编排的行为测试（spec `prepare-variants` 五类 Requirement）。

oracle 纪律：

* 变体内容由 `prepare_fixtures.RecordingBuilder` 按剧本写出，reach 期望值取该 builder
  **实际写入**的 river 行数（`written_river_rows`），不手写常量；
* 合成基线 GIS 走 `geometry_fixtures`，GeoJSON 的内容正确性归 10.2 的既有用例，这里只
  断言落点、数量与提交/清理语义；
* "无新写入"一律以 `YD_ROOT` **全树快照**（相对路径 + 文件字节，目录也在内）逐一比对，
  不用单点存在性探测——后者对"写到别处去了"的实现恒真。

`yd_root` 与 `scratch_root` 是两棵**不同的树**（生产上前者在 NFS、后者在本地盘，
agent-ops §4.1/§4.2；`test_cli.py:220-222` 已就此立过约定）。本文件的 st_dev/EXDEV 两条
用例把这条现场约束翻译成本地可判的断言。
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest
from cli_fixtures import (
    ALT_CANONICAL_GRID_IDS,
    CANONICAL_GRID_IDS,
    write_config,
    write_local,
)
from geometry_fixtures import write_bowtie_domain_layer
from prepare_fixtures import (
    BASELINE_HYDRO_PARAM_BYTES,
    RecordingBuilder,
    RenameProbe,
    SyntheticBaselinePackage,
    VariantScript,
    binding_bytes,
    tree_snapshot,
    write_baseline_package,
)

from yd_producer import prepare as prepare_module
from yd_producer.config import Config, LocalConfig, load_config, load_local
from yd_producer.prepare import (
    VARIANT_BINDING_NAME,
    VARIANT_CALIBRATED_STATE_NAME,
    VARIANT_HYDRO_PARAM_NAME,
    BuilderUnavailableError,
    PrepareError,
    run_prepare,
    variant_targets,
    viewer_targets,
)
from yd_producer.store import safe_fs

#: 本文件的合成 reach 数（生产值 3988 属 #29 的生产实例复核）。取 3 而非 0：0 会让
#: "缺 river 段被当成 0 条"这一类实现在多数用例里静默通过。
REACH_COUNT = 3

#: 成功一次后 `YD_ROOT` 相对执行前**恰好**新增的条目集合（含必要父目录）。
EXPECTED_NEW_ENTRIES = {
    "input",
    "input/models",
    "input/models/yd_gfs",
    f"input/models/yd_gfs/{VARIANT_HYDRO_PARAM_NAME}",
    f"input/models/yd_gfs/{VARIANT_BINDING_NAME}",
    f"input/models/yd_gfs/{VARIANT_CALIBRATED_STATE_NAME}",
    "input/models/yd_ifs",
    f"input/models/yd_ifs/{VARIANT_HYDRO_PARAM_NAME}",
    f"input/models/yd_ifs/{VARIANT_BINDING_NAME}",
    f"input/models/yd_ifs/{VARIANT_CALIBRATED_STATE_NAME}",
    "input/viewer",
    "input/viewer/rivers.geojson",
    "input/viewer/boundary.geojson",
}


@dataclass
class Env:
    """一次编排所需的全部现场对象。"""

    config: Config
    local: LocalConfig
    yd_root: Path
    scratch_root: Path
    package: SyntheticBaselinePackage


def make_env(
    tmp_path: Path,
    *,
    variants: dict[str, str] | None = None,
    reach_count: int = REACH_COUNT,
    grid_ids: dict[str, str] | None = None,
    river_count: int = 3,
) -> Env:
    """建一份齐备现场：真 TOML -> 真装载器 -> 真 `Config`/`LocalConfig`。

    刻意不手工构造 dataclass：新增的必需字段 `nwm_canonical_grid_id` 必须真的经过装载
    路径，否则本文件对 `grid_id` 的断言与装载器脱钩。

    `YD_ROOT` 预置一份**与本次无关的既有内容**（`output/<cycle>/gfs/DONE`），使"既有内容
    逐字节不变"这条断言有东西可咬——空根上它恒真。
    """
    config_path = write_config(
        tmp_path, variants=variants, reach_count=reach_count, grid_ids=grid_ids
    )
    local_path = write_local(tmp_path)
    config = load_config(config_path)
    local = load_local(local_path, config)
    yd_root = Path(local.yd_root)
    scratch_root = Path(local.scratch_root)
    yd_root.mkdir(parents=True, exist_ok=True)
    scratch_root.mkdir(parents=True, exist_ok=True)
    done_dir = yd_root / "output" / "2025010100" / "gfs"
    done_dir.mkdir(parents=True)
    (done_dir / "DONE").write_bytes(b"")
    (done_dir / "yd.rivqdown.dat").write_bytes(b"pre-existing product bytes\n")
    package = write_baseline_package(tmp_path / "baseline", river_count=river_count)
    return Env(
        config=config,
        local=local,
        yd_root=yd_root,
        scratch_root=scratch_root,
        package=package,
    )


@pytest.fixture
def env(tmp_path) -> Env:
    return make_env(tmp_path)


def make_builder(env: Env, scripts: dict[str, VariantScript] | None = None):
    return RecordingBuilder(
        env.package, river_count=env.config.reach_count, scripts=scripts
    )


def run(env: Env, builder):
    return run_prepare(
        local=env.local,
        config=env.config,
        baseline_root=env.package.root,
        builder=builder,
    )


def assert_untouched(env: Env, before: dict, builder=None) -> None:
    """`YD_ROOT` 全树逐字节回到执行前，且 scratch 下无任何残留。"""
    assert tree_snapshot(env.yd_root) == before
    assert tree_snapshot(env.scratch_root) == {}
    if builder is not None:
        assert builder.count == 0


# --- 成功路径 ----------------------------------------------------------------


def test_success_commits_exactly_four_targets_and_leaves_no_residue(env):
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    report = run(env, builder)

    after = tree_snapshot(env.yd_root)
    # 全树条目集合 == 执行前 ∪ 恰好四个终名及其必要父目录：无 staging 残留、无多余目录。
    # 单点探测（`input/models/` 下有几个条目）对"staging 留在 YD_ROOT 顶层"恒真。
    assert set(after) == set(before) | EXPECTED_NEW_ENTRIES
    # 既有内容逐字节不变。
    assert {key: after[key] for key in before} == before
    # products-contract §2：该目录**恰**两个条目。
    assert sorted(p.name for p in (env.yd_root / "input" / "viewer").iterdir()) == [
        "boundary.geojson",
        "rivers.geojson",
    ]
    # scratch 工作目录已删除（成功路径同样清理）。
    assert tree_snapshot(env.scratch_root) == {}

    assert report.variants == {
        "gfs": env.yd_root / "input" / "models" / "yd_gfs",
        "ifs": env.yd_root / "input" / "models" / "yd_ifs",
    }
    assert report.rivers_geojson == env.yd_root / "input/viewer/rivers.geojson"
    assert report.boundary_geojson == env.yd_root / "input/viewer/boundary.geojson"


def test_builder_called_once_per_source_with_distinct_inputs(env):
    builder = make_builder(env)

    run(env, builder)

    assert builder.count == 2
    assert [request.source_id for request in builder.requests] == ["gfs", "ifs"]
    grid_ids = [request.grid_id for request in builder.requests]
    # 取自 config 且互不相等。两条缺一不可：只断言"不相等"时，一个把两个 grid_id 写死成
    # 两个字面量的实现照样绿（`cli_fixtures` 的两组值就是为此存在）。
    assert grid_ids == [
        env.config.nwm_canonical_grid_id.gfs,
        env.config.nwm_canonical_grid_id.ifs,
    ]
    assert grid_ids[0] != grid_ids[1]
    assert grid_ids == [CANONICAL_GRID_IDS["gfs"], CANONICAL_GRID_IDS["ifs"]]

    variant_roots = [request.variant_root for request in builder.requests]
    assert variant_roots[0] != variant_roots[1]
    for root in variant_roots:
        assert root.is_relative_to(env.scratch_root)
    # 每个 variant_root 在调用当时都是**全新的空目录**，且整个 scratch 工作目录在本次
    # 运行前不存在（`make_env` 里 scratch_root 是空的）。
    assert builder.variant_root_was_empty_dir == [True, True]
    assert {request.baseline_root for request in builder.requests} == {env.package.root}


def test_grid_ids_follow_config_not_a_hardcoded_literal(tmp_path):
    """判别性：换第二组 `nwm_canonical_grid_id` 取值，两次 `grid_id` 必须跟着走。"""
    env = make_env(tmp_path, grid_ids=ALT_CANONICAL_GRID_IDS)
    builder = make_builder(env)

    run(env, builder)

    assert CANONICAL_GRID_IDS != ALT_CANONICAL_GRID_IDS
    assert [request.grid_id for request in builder.requests] == [
        ALT_CANONICAL_GRID_IDS["gfs"],
        ALT_CANONICAL_GRID_IDS["ifs"],
    ]


def test_hydro_params_are_shared_and_bindings_are_not(env):
    builder = make_builder(env)

    report = run(env, builder)

    gfs, ifs = report.variants["gfs"], report.variants["ifs"]
    # 水文参数与率定状态来自同一基线：字节一致。
    assert (gfs / VARIANT_HYDRO_PARAM_NAME).read_bytes() == BASELINE_HYDRO_PARAM_BYTES
    assert (ifs / VARIANT_HYDRO_PARAM_NAME).read_bytes() == BASELINE_HYDRO_PARAM_BYTES
    # 网格 binding MUST NOT 共用。
    assert (gfs / VARIANT_BINDING_NAME).read_bytes() != (
        ifs / VARIANT_BINDING_NAME
    ).read_bytes()


def test_reach_count_expectation_comes_from_the_generator(env):
    """reach 期望值由生成器写入的 river 行数给定，不是本文件手写的常量。"""
    builder = make_builder(env)

    run(env, builder)

    assert builder.written_river_rows == {
        "gfs": env.config.reach_count,
        "ifs": env.config.reach_count,
    }


def test_geojson_feature_counts_match_the_baseline(env):
    import json

    builder = make_builder(env)

    report = run(env, builder)

    rivers = json.loads(report.rivers_geojson.read_text(encoding="utf-8"))
    boundary = json.loads(report.boundary_geojson.read_text(encoding="utf-8"))
    assert len(rivers["features"]) == env.package.river_feature_count
    assert len(boundary["features"]) == 1


# --- 提交面：同盘 rename、staging 位置、提交顺序 -----------------------------


def _probe_rename(monkeypatch, *, fail_at: int | None = None) -> RenameProbe:
    probe = RenameProbe(
        delegate=safe_fs.rename_entry_no_follow,
        fail_at=fail_at,
    )
    monkeypatch.setattr(prepare_module.safe_fs, "rename_entry_no_follow", probe)
    return probe


def test_every_commit_renames_within_yd_root_on_one_device(env, monkeypatch):
    probe = _probe_rename(monkeypatch)

    builder = make_builder(env)
    run(env, builder)

    assert probe.count == 4
    for source, _target in probe.calls:
        # 源在 `YD_ROOT` 之内——把 staging 放回 scratch 的实现在这条上必红。
        assert source.is_relative_to(env.yd_root)
        # 且不在 `input/viewer/` 之内（products-contract §2 只允许该目录存在两个文件）。
        assert not source.is_relative_to(env.yd_root / "input" / "viewer")
    # 每次 rename 的源与终名同 `st_dev`（生产 NFS/scratch 跨设备的判别式）。
    for source_dev, target_dev in probe.devices:
        assert source_dev == target_dev
    # 提交顺序钉死：两变体 -> rivers -> boundary。
    assert [target.name for _, target in probe.calls] == [
        "yd_gfs",
        "yd_ifs",
        "rivers.geojson",
        "boundary.geojson",
    ]
    # staging 位置成功后已不存在。
    for source, _ in probe.calls:
        assert not os.path.lexists(source)


def test_commit_survives_a_filesystem_that_refuses_cross_device_rename(
    env, monkeypatch
):
    """判别性 EXDEV：源不在 `yd_root` 内时按生产行为拒绝，本次编排仍须成功。

    生产上 `yd_root` 在 NFS、`scratch_root` 在本地盘，`rename_entry_no_follow` 明写
    `EXDEV` 是硬错误且**刻意没有** fallback copy（`safe_fs.py:630-631`）。本地测试两根
    同盘，`st_dev` 断言恒真也测不出"直接把 scratch 目录 rename 过去"的实现——这条用例
    把跨设备语义**注入**进来，使那种实现在本地就红。
    """
    real_rename = safe_fs.rename_entry_no_follow

    def rename_with_exdev(parent, name, dest_parent, dest_name, **kwargs):
        if not Path(parent).is_relative_to(env.yd_root):
            raise safe_fs.SafeFilesystemError(
                f"Failed to rename {Path(parent) / name}: [Errno 18] "
                "Invalid cross-device link",
                kind="io",
            )
        return real_rename(parent, name, dest_parent, dest_name, **kwargs)

    monkeypatch.setattr(
        prepare_module.safe_fs, "rename_entry_no_follow", rename_with_exdev
    )

    report = run(env, make_builder(env))

    assert report.variants["gfs"].is_dir()
    assert report.rivers_geojson.is_file()


def test_published_entries_do_not_inherit_scratch_modes(env):
    """发布权限新建条目：MUST NOT 把计算节点的 mode 原样带进 NFS（agent-ops §10）。"""
    recording = make_builder(env)

    def builder(request):
        recording(request)
        for child in request.variant_root.iterdir():
            child.chmod(0o600)

    report = run(env, builder)

    umask = os.umask(0o022)
    os.umask(umask)
    expected = 0o666 & ~umask
    for name in (VARIANT_HYDRO_PARAM_NAME, VARIANT_BINDING_NAME):
        mode = stat.S_IMODE((report.variants["gfs"] / name).stat().st_mode)
        assert mode != 0o600
        assert mode == expected


# --- 拒绝覆盖（在任何写入与任何 builder 调用之前）---------------------------


def test_existing_variant_directory_is_refused(env):
    (env.yd_root / "input" / "models" / "yd_gfs").mkdir(parents=True)
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert str(env.yd_root / "input" / "models" / "yd_gfs") in str(excinfo.value)
    assert_untouched(env, before, builder)


@pytest.mark.parametrize("name", ["rivers.geojson", "boundary.geojson"])
def test_existing_viewer_geojson_is_refused_byte_for_byte(env, name):
    viewer = env.yd_root / "input" / "viewer"
    viewer.mkdir(parents=True)
    known = b'{"type": "FeatureCollection", "features": []}\n'
    (viewer / name).write_bytes(known)
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert name in str(excinfo.value)
    assert (viewer / name).read_bytes() == known
    assert not (env.yd_root / "input" / "models").exists()
    assert_untouched(env, before, builder)


# --- 变体相对路径的 fail-closed 闸门 ----------------------------------------


def test_absolute_variant_path_is_refused(tmp_path):
    outside = tmp_path / "outside-yd-root"
    env = make_env(
        tmp_path, variants={"gfs": str(outside), "ifs": "input/models/yd_ifs"}
    )
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    message = str(excinfo.value)
    assert "variants.gfs" in message
    assert str(outside) in message
    assert not os.path.lexists(outside)
    assert_untouched(env, before, builder)


def test_escaping_variant_path_is_refused(tmp_path):
    env = make_env(
        tmp_path,
        variants={"gfs": "input/../../escaped/yd_gfs", "ifs": "input/models/yd_ifs"},
    )
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    message = str(excinfo.value)
    assert "variants.gfs" in message
    assert "input/../../escaped/yd_gfs" in message
    assert not os.path.lexists(env.yd_root.parent / "escaped")
    assert_untouched(env, before, builder)


def test_identical_variant_paths_are_refused_before_any_write(tmp_path):
    same = "input/models/yd_same"
    env = make_env(tmp_path, variants={"gfs": same, "ifs": same})
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    message = str(excinfo.value)
    assert "variants.gfs" in message
    assert "variants.ifs" in message
    assert_untouched(env, before, builder)


def test_nested_variant_paths_are_refused_before_any_write(tmp_path):
    """互为祖先：两个 `lexists` 守卫与产物校验都发现不了，提交后 ifs 会躺在 gfs 内部。"""
    env = make_env(
        tmp_path,
        variants={"gfs": "input/models/a", "ifs": "input/models/a/b"},
    )
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    message = str(excinfo.value)
    assert "variants.gfs" in message
    assert "variants.ifs" in message
    assert_untouched(env, before, builder)


def test_variant_ancestor_of_viewer_directory_is_refused(tmp_path):
    env = make_env(tmp_path, variants={"gfs": "input", "ifs": "input/models/yd_ifs"})
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError):
        run(env, builder)

    assert_untouched(env, before, builder)


# --- 守卫/写入同源判别性 -----------------------------------------------------


def test_overwrite_guard_follows_config_not_the_default_literal(tmp_path):
    """守卫跟着 config 走：非默认相对值上的既有目录必须被拒绝。"""
    env = make_env(
        tmp_path, variants={"gfs": "models/alt_gfs", "ifs": "input/models/yd_ifs"}
    )
    (env.yd_root / "models" / "alt_gfs").mkdir(parents=True)
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert str(env.yd_root / "models" / "alt_gfs") in str(excinfo.value)
    assert_untouched(env, before, builder)


def test_commit_lands_where_config_points_not_at_the_default_literal(tmp_path):
    """反向：字面量 `input/models/yd_gfs` 存在，但 config 指向别处 -> 照 config 提交。"""
    env = make_env(
        tmp_path, variants={"gfs": "models/alt_gfs", "ifs": "input/models/yd_ifs"}
    )
    literal = env.yd_root / "input" / "models" / "yd_gfs"
    literal.mkdir(parents=True)
    (literal / "unrelated").write_bytes(b"not ours\n")

    report = run(env, make_builder(env))

    assert report.variants["gfs"] == env.yd_root / "models" / "alt_gfs"
    assert (report.variants["gfs"] / VARIANT_BINDING_NAME).is_file()
    # 既有的同名字面量目录逐字节不变（既没被覆盖，也没被当成本次产物）。
    assert sorted(p.name for p in literal.iterdir()) == ["unrelated"]
    assert (literal / "unrelated").read_bytes() == b"not ours\n"


# --- 产物校验 ----------------------------------------------------------------


def test_reach_count_mismatch_refuses_commit(env):
    before = tree_snapshot(env.yd_root)
    builder = make_builder(
        env, {"ifs": VariantScript(river_count=env.config.reach_count + 1)}
    )

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    message = str(excinfo.value)
    assert "ifs" in message
    assert str(env.config.reach_count) in message
    assert str(builder.written_river_rows["ifs"]) in message
    assert builder.written_river_rows["ifs"] != env.config.reach_count
    assert_untouched(env, before)


def test_missing_river_section_refuses_commit(env):
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env, {"gfs": VariantScript(include_river_section=False)})

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    message = str(excinfo.value)
    assert "river" in message
    # 与"数量不符"的措辞可区分：缺段是结构性缺陷，不是一个数量。
    assert "不符" not in message
    assert_untouched(env, before)


def test_missing_river_section_is_not_treated_as_zero_reaches(tmp_path):
    """判别性：`reach_count == 0` 时，把 `river is None` 当成 0 条的实现会静默通过。"""
    env = make_env(tmp_path, reach_count=0)
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env, {"gfs": VariantScript(include_river_section=False)})

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert env.config.reach_count == 0
    assert "river" in str(excinfo.value)
    assert_untouched(env, before)


def test_scratch_residue_refuses_commit(env):
    before = tree_snapshot(env.yd_root)
    residue = f"{VARIANT_BINDING_NAME}.tmp"
    builder = make_builder(env, {"gfs": VariantScript(extra_entries=(residue,))})

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert residue in str(excinfo.value)
    assert not (env.yd_root / "input" / "models").exists()
    assert_untouched(env, before)


def test_missing_variant_entry_refuses_commit(env):
    before = tree_snapshot(env.yd_root)
    builder = make_builder(
        env, {"ifs": VariantScript(omit_entries=(VARIANT_BINDING_NAME,))}
    )

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert VARIANT_BINDING_NAME in str(excinfo.value)
    assert_untouched(env, before)


def test_unparsable_calibrated_state_refuses_commit(env):
    """`cfg_ic.parse` 的 `ValueError` MUST NOT 逃逸出 `prepare`。"""
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env, {"gfs": VariantScript(corrupt_state=True)})

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert VARIANT_CALIBRATED_STATE_NAME in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, ValueError)
    assert_untouched(env, before)


def test_missing_variant_root_refuses_commit(env):
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env, {"ifs": VariantScript(remove_variant_root=True)})

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert "ifs" in str(excinfo.value)
    assert_untouched(env, before)


def test_partial_success_commits_nothing(env):
    """`ifs` builder 抛异常（`gfs` 已建好）-> `gfs` 变体 MUST NOT 被提交。"""
    before = tree_snapshot(env.yd_root)
    builder = make_builder(
        env, {"ifs": VariantScript(raises=RuntimeError("builder blew up"))}
    )

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert "ifs" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert builder.count == 2
    assert_untouched(env, before)


def test_geometry_failure_rolls_back_validated_variants(env):
    """`GeometryError` MUST NOT 逃逸；两个变体已校验通过也不得提交。"""
    write_bowtie_domain_layer(env.package.gis.domain_shp)
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    from yd_producer.geometry import GeometryError

    assert isinstance(excinfo.value.__cause__, GeometryError)
    assert builder.count == 2
    assert_untouched(env, before)


# --- 提交中途失败的回滚 ------------------------------------------------------


def test_first_commit_failure_leaves_no_new_entries(env, monkeypatch):
    """`SafeFilesystemError` 是 `RuntimeError` 子类，`except OSError` 兜不住它。"""
    probe = _probe_rename(monkeypatch, fail_at=1)
    before = tree_snapshot(env.yd_root)

    with pytest.raises(PrepareError) as excinfo:
        run(env, make_builder(env))

    assert isinstance(excinfo.value.__cause__, safe_fs.SafeFilesystemError)
    assert probe.count == 1
    assert_untouched(env, before)


@pytest.mark.parametrize("fail_at", [2, 3, 4])
def test_late_commit_failure_rolls_back_already_committed_targets(
    env, monkeypatch, fail_at
):
    """第 N 次 rename 失败 -> 此前已提交的每一个终名都必须被撤回。

    总不变量是**全有或全无**：只回滚 staging 与父目录、把已提交的变体留在 `YD_ROOT`
    里，会留下一个半提交态，而 `prepare` 无 `--force` 且四名任一存在即拒绝——半提交态
    目前没有文档化出路（已接受残留只到 SIGKILL 窗口）。

    `fail_at` 三个取值覆盖三种撤回形态：只回滚一个变体**目录**（2）、回滚两个变体目录
    （3）、以及回滚里含一个已提交的普通**文件**（4，`rivers.geojson` 已落终名）——第三
    种此前从未被行使，而 `rmtree_no_follow` 对目录与非目录走的是两条不同分支。
    """
    probe = _probe_rename(monkeypatch, fail_at=fail_at)
    before = tree_snapshot(env.yd_root)

    with pytest.raises(PrepareError):
        run(env, make_builder(env))

    assert probe.count == fail_at
    assert_untouched(env, before)


# --- 生产 builder 绑定 fail-closed ------------------------------------------


def test_production_builder_binding_fails_before_any_subprocess(env, monkeypatch):
    import subprocess

    from yd_producer import nwm

    calls: list[tuple] = []
    monkeypatch.setattr(
        nwm, "invoke_mapping_builder", lambda *a, **k: calls.append(("shell", a))
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: calls.append(("subprocess", a))
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(("popen", a)))
    before = tree_snapshot(env.yd_root)

    with pytest.raises(BuilderUnavailableError) as excinfo:
        run_prepare(local=env.local, config=env.config, baseline_root=env.package.root)

    assert prepare_module.BUILDER_OWNER in str(excinfo.value)
    assert calls == []
    assert_untouched(env, before)


def test_builder_unavailable_is_a_prepare_error_subclass():
    """两级异常不得合并，但基类捕获仍须覆盖它（`cli` 靠先后顺序分码）。"""
    assert issubclass(BuilderUnavailableError, PrepareError)
    assert BuilderUnavailableError is not PrepareError


# --- 终名纯函数 --------------------------------------------------------------


def test_variant_targets_is_the_single_source_of_final_names(tmp_path):
    env = make_env(tmp_path, variants={"gfs": "models/alt_gfs", "ifs": "other/alt_ifs"})

    targets = variant_targets(env.local, env.config)

    assert targets == {
        "gfs": env.yd_root / "models" / "alt_gfs",
        "ifs": env.yd_root / "other" / "alt_ifs",
    }


def test_viewer_targets_are_the_contract_literals(env):
    assert viewer_targets(env.local) == {
        "rivers": env.yd_root / "input" / "viewer" / "rivers.geojson",
        "boundary": env.yd_root / "input" / "viewer" / "boundary.geojson",
    }


# --- I1 清理/回滚容错 --------------------------------------------------------


def _staging_entries(yd_root: Path) -> list[str]:
    """`YD_ROOT` 顶层残留的本次 staging 目录名（成功后必须为空）。"""
    return sorted(
        p.name
        for p in yd_root.iterdir()
        if p.name.startswith(prepare_module._STAGING_PREFIX)
    )


def _fail_rmtree_when(monkeypatch, predicate):
    """令 `safe_fs.rmtree_no_follow` 只对满足 `predicate` 的路径失败。

    模拟 NFS 上的瞬时 `EIO`/`ESTALE`——`safe_fs` 自己就把这一类归为 `kind="io"`。
    """
    real = safe_fs.rmtree_no_follow

    def flaky(path, **kwargs):
        if predicate(Path(path)):
            raise safe_fs.SafeFilesystemError(
                f"injected cleanup failure: {path}", kind="io"
            )
        return real(path, **kwargs)

    monkeypatch.setattr(prepare_module.safe_fs, "rmtree_no_follow", flaky)


def test_one_failing_rollback_step_does_not_cancel_the_others(env, monkeypatch):
    """回滚里第一步删除失败 -> 其余每一步照跑，且原始异常原样上浮（I1 / cand-01）。

    裸序列的回滚在这条上必红：第一个 `_remove_tree` 抛出会取消后面所有撤回，`YD_ROOT`
    停在「两个变体已提交、无 GeoJSON 对、本次新建的父目录全部搁浅」的半提交态，而逃逸
    出去的还是一条清理错误——原始的提交失败被顶掉了。
    """
    _probe_rename(monkeypatch, fail_at=4)
    rivers = env.yd_root / "input" / "viewer" / "rivers.geojson"
    _fail_rmtree_when(monkeypatch, lambda path: path == rivers)

    with pytest.raises(PrepareError) as excinfo:
        run(env, make_builder(env))

    # 上浮的是**原始**失败（第 4 次 rename），不是清理失败。
    assert "提交失败" in str(excinfo.value)
    assert "boundary.geojson" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, safe_fs.SafeFilesystemError)
    # 清理失败没有被丢掉，也没有替换原始异常：作为 note 附在它身上。
    notes = getattr(excinfo.value, "__notes__", [])
    assert any("injected cleanup failure" in note for note in notes)
    assert any(str(rivers) in note for note in notes)

    # 失败那一步之后的每一步都真的跑了。
    assert not os.path.lexists(env.yd_root / "input" / "models" / "yd_gfs")
    assert not os.path.lexists(env.yd_root / "input" / "models" / "yd_ifs")
    assert not os.path.lexists(env.yd_root / "input" / "models")
    assert _staging_entries(env.yd_root) == []
    assert tree_snapshot(env.scratch_root) == {}
    # 只有那一步自己的残留还在（连同盛着它的、非递归因此删不掉的父目录）。
    assert rivers.is_file()


def test_builder_unavailable_survives_a_cleanup_failure(env, monkeypatch):
    """清理失败 MUST NOT 把 `BuilderUnavailableError` 降级成基类（I1 / cand-02）。

    这是今天唯一生产可达的那一支：`cli` 传的就是生产 `default_builder`。降级即退出码
    从 `3` 掉到 `1`，运维会去改一份没有问题的配置。
    """

    def refuse(parent, name, **kwargs):
        raise safe_fs.SafeFilesystemError(
            f"injected cleanup failure: {Path(parent) / name}", kind="io"
        )

    monkeypatch.setattr(prepare_module.safe_fs, "remove_tree_allow_symlinks", refuse)
    before = tree_snapshot(env.yd_root)

    with pytest.raises(BuilderUnavailableError) as excinfo:
        run_prepare(local=env.local, config=env.config, baseline_root=env.package.root)

    assert type(excinfo.value) is BuilderUnavailableError
    assert "归属 M4" in str(excinfo.value)
    notes = getattr(excinfo.value, "__notes__", [])
    assert any("injected cleanup failure" in note for note in notes)
    assert tree_snapshot(env.yd_root) == before


def test_success_survives_a_staging_cleanup_failure(env, monkeypatch):
    """成功提交后 staging 删不掉 -> 仍然返回成功报告（I1 / cand-03）。

    把它报成失败会让四个终名已经全部提交的运行拿到退出码 `1`，而重跑立刻撞上拒绝覆盖
    守卫（无 `--force`）——一次成功的运行变成死局。
    """
    before = tree_snapshot(env.yd_root)
    _fail_rmtree_when(
        monkeypatch,
        lambda path: path.name.startswith(prepare_module._STAGING_PREFIX),
    )

    report = run(env, make_builder(env))

    after = tree_snapshot(env.yd_root)
    assert report.rivers_geojson.is_file()
    assert report.boundary_geojson.is_file()
    assert report.variants["gfs"].is_dir()
    assert report.variants["ifs"].is_dir()
    # 清理失败没有被吞掉：它在返回值上。
    assert report.cleanup_warnings
    assert any("injected cleanup failure" in w for w in report.cleanup_warnings)
    # 残留确实还在（告警说的是实话），且 scratch 侧照样清了（两步互不取消）。
    assert _staging_entries(env.yd_root) != []
    assert tree_snapshot(env.scratch_root) == {}
    assert set(after) >= set(before) | EXPECTED_NEW_ENTRIES


def test_scratch_cleanup_failure_does_not_gate_the_staging_cleanup(env, monkeypatch):
    """本地 scratch（可弃）删不掉 MUST NOT 卡住 `YD_ROOT` 内 staging（承载不变量）的清理。"""

    def refuse(parent, name, **kwargs):
        raise safe_fs.SafeFilesystemError(
            f"injected scratch failure: {Path(parent) / name}", kind="io"
        )

    monkeypatch.setattr(prepare_module.safe_fs, "remove_tree_allow_symlinks", refuse)

    report = run(env, make_builder(env))

    assert _staging_entries(env.yd_root) == []
    assert any("injected scratch failure" in w for w in report.cleanup_warnings)
    assert tree_snapshot(env.scratch_root) != {}


def test_success_reports_no_cleanup_warnings_when_cleanup_is_clean(env):
    """判别性反面：正常成功路径上 `cleanup_warnings` 必须是空的。"""
    report = run(env, make_builder(env))

    assert report.cleanup_warnings == ()


def test_builder_symlink_residue_is_refused_and_scratch_is_fully_removed(env):
    """builder 在 `variant_root` 里留 symlink -> 拒绝提交并点名它，scratch 全清（cand-04）。

    `rmtree_no_follow` 对 symlink 条目**拒绝删除**（在 `YD_ROOT` 侧那是正确策略：symlink
    是篡改证据）。但 scratch 的内容由 builder 写、按构造不可信，用同一个原语会让 scratch
    树永久搁浅，而"变体含未预期条目"这条真因还被一条清理错误顶掉。
    """
    before = tree_snapshot(env.yd_root)
    builder = make_builder(
        env, {"gfs": VariantScript(symlink_entries=(("yd.link", "/etc/passwd"),))}
    )

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert "yd.link" in str(excinfo.value)
    assert "未预期条目" in str(excinfo.value)
    assert getattr(excinfo.value, "__notes__", []) == []
    assert_untouched(env, before)


# --- I2 本次条目登记与删除范围 ----------------------------------------------


def test_mid_chain_directory_creation_failure_leaves_no_new_entries(env, monkeypatch):
    """`ensure_directory_no_follow` 建到一半失败 -> `YD_ROOT` 条目集合回到执行前（cand-05）。

    该原语逐层 `os.mkdir` 且**没有 unwind**；登记在创建之后，中途失败时已建好的那几层
    永远不进 `created`，回滚够不着它们。
    """
    before = tree_snapshot(env.yd_root)
    real = safe_fs.ensure_directory_no_follow
    victim = env.yd_root / "input" / "models"

    def partial_then_fail(path, **kwargs):
        if Path(path) == victim:
            # 忠实模拟 EDQUOT/EACCES 打断循环：前一层已经落盘了。
            real(env.yd_root / "input")
            raise safe_fs.SafeFilesystemError(
                f"injected mid-chain failure: {path}", kind="io"
            )
        return real(path, **kwargs)

    monkeypatch.setattr(
        prepare_module.safe_fs, "ensure_directory_no_follow", partial_then_fail
    )

    with pytest.raises(PrepareError) as excinfo:
        run(env, make_builder(env))

    assert "injected mid-chain failure" in str(excinfo.value)
    assert set(tree_snapshot(env.yd_root)) == set(before)
    assert tree_snapshot(env.scratch_root) == {}


def test_rollback_never_recursively_deletes_a_shared_parent(env, monkeypatch):
    """本次新建的父目录里出现别的写入者的内容 -> 回滚 MUST NOT 递归删掉它（cand-06）。

    `prepare` 全程不持锁，四个 `lexists` 守卫到提交循环之间隔着整个 builder 运行时长，
    两个并发/重复派发的运行都能通过守卫。父目录用递归删除时，回滚会连带删掉另一个运行
    已经提交的产物——直接违反「任何既有条目 MUST NOT 被覆盖或删除」。
    """
    real = safe_fs.rename_entry_no_follow
    foreign = env.yd_root / "input" / "models" / "foreign_from_run_a"

    def plant_then_fail(parent, name, dest_parent, dest_name, **kwargs):
        foreign.write_bytes(b"another writer's committed product\n")
        raise safe_fs.SafeFilesystemError(
            f"injected rename failure: {Path(dest_parent) / dest_name}", kind="io"
        )

    monkeypatch.setattr(
        prepare_module.safe_fs, "rename_entry_no_follow", plant_then_fail
    )
    assert real is not plant_then_fail

    with pytest.raises(PrepareError) as excinfo:
        run(env, make_builder(env))

    # 别人的产物逐字节还在。
    assert foreign.read_bytes() == b"another writer's committed product\n"
    # 而且是**响亮**地留下的：删不掉的父目录进了 note，不是静默递归删除。
    notes = getattr(excinfo.value, "__notes__", [])
    assert any(str(env.yd_root / "input" / "models") in note for note in notes)
    assert any("拒绝递归删除" in note for note in notes)
    assert _staging_entries(env.yd_root) == []
    assert tree_snapshot(env.scratch_root) == {}


def test_probe_loop_refuses_to_rebuild_a_vanished_run_root(env):
    """`yd_root` 在运行途中消失 -> 硬失败，MUST NOT 沿着不存在的祖先链把它重造出来。"""
    import shutil

    recording = make_builder(env)

    def builder(request):
        recording(request)
        if request.source_id == "ifs":
            shutil.rmtree(env.yd_root)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert str(env.yd_root) in str(excinfo.value)
    assert "拒绝重建" in str(excinfo.value)
    assert not os.path.lexists(env.yd_root)
    assert tree_snapshot(env.scratch_root) == {}


# --- I3 同一路径拼写（运行根预检）------------------------------------------


def _replace_local(env: Env, **overrides) -> Env:
    """替换已装载 `LocalConfig` 的运行根字段。

    装载器对 `yd_root`/`scratch_root` 只做存在性与类型检查（`specs/cli-config/spec.md`
    钉死），故它原样透传字符串——`replace` 忠实复现装载器会给出的对象。
    """
    from dataclasses import replace

    return Env(
        config=env.config,
        local=replace(env.local, **overrides),
        yd_root=env.yd_root,
        scratch_root=env.scratch_root,
        package=env.package,
    )


@pytest.mark.parametrize("field_name", ["yd_root", "scratch_root"])
@pytest.mark.parametrize("spelling", ["~/yd", "relative/yd", "./yd"])
def test_non_absolute_run_roots_are_refused_before_any_builder_call(
    env, field_name, spelling
):
    """非绝对的运行根一律拒绝（I3 / cand-12）。

    `safe_fs` 的每个原语都先 `expanduser()` 并用 `Path.cwd()` 锚定相对路径，而拒绝覆盖
    守卫的 `os.path.lexists` 与 `geometry.write_viewer_geojson` 都不展开。同一个配置值
    在三个消费者眼里成了两个文件系统对象，回滚就会去删真实 `$HOME` 里的既有内容。
    """
    before = tree_snapshot(env.yd_root)
    scoped = _replace_local(env, **{field_name: spelling})
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(scoped, builder)

    message = str(excinfo.value)
    assert field_name in message
    assert "绝对路径" in message
    assert_untouched(env, before, builder)


def test_tilde_run_root_never_touches_the_real_home(env, tmp_path, monkeypatch):
    """`yd_root = "~/yd"` 的判别性证据：真实 `$HOME` 下的既有产物逐字节存活。"""
    home = tmp_path / "home"
    victim_dir = home / "yd" / "input" / "viewer"
    victim_dir.mkdir(parents=True)
    victim = victim_dir / "rivers.geojson"
    victim.write_bytes(b"OPERATOR BYTES\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    home_before = tree_snapshot(home)
    builder = make_builder(env)

    with pytest.raises(PrepareError):
        run(_replace_local(env, yd_root="~/yd"), builder)

    assert victim.read_bytes() == b"OPERATOR BYTES\n"
    assert tree_snapshot(home) == home_before
    # 也没有在 CWD 下留一棵字面量 `~` 的孤儿树。
    assert not os.path.lexists(tmp_path / "~")
    assert builder.count == 0


@pytest.mark.parametrize("field_name", ["yd_root", "scratch_root"])
def test_missing_run_roots_are_refused_before_any_builder_call(env, field_name):
    """运行根不存在 -> 拒绝执行，MUST NOT 凭空造出影子根并报成功（I3 / cand-07）。

    生产上 `yd_root` 是 NFS 发布根 `/ghdc/data/yd`（agent-ops §4.1）。打错一个字或一次
    瞬时未挂载，如果默许创建，整棵根会被造出来、运行返回 **0**、产物躺在 viewer 永远
    读不到的地方。
    """
    absent = Path(env.local.yd_root).parent / "typo-root" / "deep"
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(_replace_local(env, **{field_name: str(absent)}), builder)

    assert field_name in str(excinfo.value)
    assert str(absent) in str(excinfo.value)
    assert not os.path.lexists(absent)
    assert not os.path.lexists(absent.parent)
    assert builder.count == 0


def test_symlinked_run_root_is_refused(env, tmp_path):
    """运行根含 symlink 组件 -> 拒绝（`verify_directory_no_follow` 逐层 no-follow）。"""
    link = tmp_path / "yd-link"
    link.symlink_to(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(_replace_local(env, yd_root=str(link)), builder)

    assert "yd_root" in str(excinfo.value)
    assert builder.count == 0


# --- G3 词法闸门的扩展 -------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["input/viewer/yd_gfs", "input/viewer", "input/viewer/nested/yd_gfs", "output"],
)
def test_variant_target_on_the_viewer_read_surface_is_refused(tmp_path, value):
    """变体终名落在 `input/viewer/` 或 `output/` 之内 -> 任何写入之前拒绝（cand-08）。

    变体目录是两个 GeoJSON 终名的**兄弟**：两两互异过、互为祖先过、两个 `lexists` 也过，
    于是变体的三个文件直接落到 viewer 的读取面上，破坏 products-contract §2 的「恰两个
    文件」。
    """
    env = make_env(tmp_path, variants={"gfs": value, "ifs": "input/models/yd_ifs"})
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert "variants.gfs" in str(excinfo.value)
    assert_untouched(env, before, builder)


def test_output_subtree_variant_target_is_refused(tmp_path):
    """`output/` 子树同类：viewer 的另一半读取面（products-contract §2/§7）。"""
    env = make_env(
        tmp_path, variants={"gfs": "output/yd_gfs", "ifs": "input/models/yd_ifs"}
    )
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    assert "variants.gfs" in str(excinfo.value)
    assert "output" in str(excinfo.value)
    assert_untouched(env, before, builder)


@pytest.mark.parametrize(
    "value",
    [
        "input/../input/models/yd_gfs",
        "input/models/../models/yd_gfs",
        "../outside/yd_gfs",
    ],
)
def test_any_pardir_component_in_a_variant_path_is_refused(tmp_path, value):
    """`variants.*` 含**任一** `..` 组件即拒绝（cand-09）。

    `specs/prepare-variants/spec.md` 写的是「绝对路径或含 `..` 的路径 MUST 拒绝执行」，
    而只查"规范化后是否逃出 `yd_root`"会放行 `input/../input/models/yd_gfs`。
    """
    env = make_env(tmp_path, variants={"gfs": value, "ifs": "input/models/yd_ifs"})
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env)

    with pytest.raises(PrepareError) as excinfo:
        run(env, builder)

    message = str(excinfo.value)
    assert "variants.gfs" in message
    assert value in message
    assert ".." in message
    assert_untouched(env, before, builder)


# --- G4 提交前复探（TOCTOU 窄化）--------------------------------------------


def test_target_appearing_after_the_first_guard_is_refused_before_commit(
    env, monkeypatch
):
    """守卫之后、提交之前落到终名上的既有文件 MUST NOT 被静默替换（cand-10）。

    `rename_entry_no_follow` 发的是裸 `renameat`（没有 `RENAME_NOREPLACE`），第一次探测
    与提交之间隔着两次 builder 调用的全部时长。复探把窗口压到微秒级；POSIX 没有可移植
    的原子 rename-noreplace，剩下的窗口**不可消解**，本用例也不假装它被关掉了。
    """
    real_write = prepare_module.write_viewer_geojson
    victim = env.yd_root / "input" / "viewer" / "rivers.geojson"

    def write_then_plant(**kwargs):
        result = real_write(**kwargs)
        victim.parent.mkdir(parents=True, exist_ok=True)
        victim.write_bytes(b"PRE-EXISTING OPERATOR BYTES\n")
        return result

    monkeypatch.setattr(prepare_module, "write_viewer_geojson", write_then_plant)
    probe = _probe_rename(monkeypatch)

    with pytest.raises(PrepareError) as excinfo:
        run(env, make_builder(env))

    assert "提交前复探" in str(excinfo.value)
    assert str(victim) in str(excinfo.value)
    # 既有字节逐字节存活，且一次 rename 都没发生。
    assert victim.read_bytes() == b"PRE-EXISTING OPERATOR BYTES\n"
    assert probe.count == 0
    assert not os.path.lexists(env.yd_root / "input" / "models")
    assert _staging_entries(env.yd_root) == []
    assert tree_snapshot(env.scratch_root) == {}


# --- G5 变体内容与终名的绑定、归属字面量、每次运行专属命名 -------------------


def test_each_final_name_receives_its_own_source_content(env):
    """`yd_gfs` 里必须是 gfs 的 binding，`yd_ifs` 里必须是 ifs 的（cand-13）。

    判别性：把两次 staging 复制的源对调，全套 919+ 用例原本全绿——两个变体的水文参数
    本来就同源一致，而"两个 binding 互不相等"在对调下恒真。内容必须按**终名**回读。
    """
    report = run(env, make_builder(env))

    for source in ("gfs", "ifs"):
        expected = binding_bytes(
            grid_id=getattr(env.config.nwm_canonical_grid_id, source),
            source_id=source,
        )
        assert (report.variants[source] / VARIANT_BINDING_NAME).read_bytes() == expected
    # 反向：另一个 source 的字节 MUST NOT 出现在这个终名下。
    gfs_bytes = (report.variants["gfs"] / VARIANT_BINDING_NAME).read_bytes()
    ifs_bytes = (report.variants["ifs"] / VARIANT_BINDING_NAME).read_bytes()
    assert b"source_id=gfs" in gfs_bytes
    assert b"source_id=ifs" in ifs_bytes


def test_production_binding_names_its_owner_with_a_literal(env):
    """归属断言取**字面量**，不取模块常量（cand-14）。

    `assert prepare_module.BUILDER_OWNER in str(exc)` 是自指的：把 `BUILDER_OWNER` 置空
    并删掉消息里的归属子句，该断言照样绿。
    """
    with pytest.raises(BuilderUnavailableError) as excinfo:
        run_prepare(local=env.local, config=env.config, baseline_root=env.package.root)

    assert "归属 M4" in str(excinfo.value)


def test_two_runs_get_distinct_scratch_and_staging_names(env):
    """同一对 `scratch_root`/`yd_root` 上跑两次 -> 工作目录名必须不同（cand-15）。

    把 per-run token 换成常量，全套用例原本全绿（每个用例都用全新的 `tmp_path`，固定名
    在套件内永不相撞）。而在现场，两次运行共用一个 `work_dir` 与一个 staging，无条件的
    清理会让其中一次删掉另一次在途的 staging。
    """
    work_dirs = []

    def builder(request):
        work_dirs.append(request.variant_root.parent)
        raise RuntimeError("stop before any commit")

    for _ in range(2):
        with pytest.raises(PrepareError):
            run(env, builder)

    assert len(work_dirs) == 2
    assert work_dirs[0] != work_dirs[1]
    assert work_dirs[0].parent == env.scratch_root
    assert work_dirs[1].parent == env.scratch_root
    assert tree_snapshot(env.scratch_root) == {}
