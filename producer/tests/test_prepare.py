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


def test_late_commit_failure_rolls_back_already_committed_targets(env, monkeypatch):
    """第三次 rename（rivers）失败 -> 两个已提交的变体目录必须被撤回。

    总不变量是**全有或全无**：只回滚 staging 与父目录、把已提交的两个变体留在
    `YD_ROOT` 里，会留下一个"有变体、无 GeoJSON"的半提交态，而 `prepare` 无 `--force`
    且四名任一存在即拒绝——半提交态目前没有文档化出路（已接受残留只到 SIGKILL 窗口）。
    """
    probe = _probe_rename(monkeypatch, fail_at=3)
    before = tree_snapshot(env.yd_root)

    with pytest.raises(PrepareError):
        run(env, make_builder(env))

    assert probe.count == 3
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
