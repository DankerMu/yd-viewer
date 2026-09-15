"""`yd_producer.rawcopy.stage_raw` 路径包含与 symlink 拒绝回归。"""

import json
import os
import stat
from pathlib import Path

import pytest
from rawcopy_fixtures import (
    CYCLE,
    CYCLE_DIR,
    CYCLE_ISO,
    DIR_SEGMENTS,
    LEADS,
    MANIFEST_NAME,
    SOURCE_MANIFEST_NAME,
    build_tree,
    bundle_bytes,
    bundle_name,
    content_snapshot,
    cycle_dir,
    expect_kind,
    make_config,
    snapshot,
    source_manifest_payload,
    staged,
    write_source_manifest,
)

from yd_producer import rawcopy as rawcopy_module
from yd_producer.config import ConfigError
from yd_producer.rawcopy import (
    RawStagingError,
    stage_raw,
)
from yd_producer.rawscan import judge

# --- Row：verdict-mismatch 与其相对路径对照 ----------------------------------


def test_raw_root_from_another_call_site_is_rejected(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    other_root = tmp_path / "other-raw"
    other_base = cycle_dir(other_root, "gfs")
    other_base.mkdir(parents=True)
    for lead in LEADS:
        (other_base / bundle_name("gfs", lead)).write_bytes(bundle_bytes(lead))
    write_source_manifest(other_root, "gfs", source_manifest_payload("gfs"))
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, other_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "verdict-mismatch")
    assert snapshot(work_dir) == {}


def test_relative_raw_root_from_the_same_call_site_stages_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """containment 检查 MUST NOT 误拒合法的相对 `raw_root` 调用（同法提升的判别器）。"""
    _raw_root, work_dir = build_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    config = make_config()
    verdict = judge("nwm-raw", "gfs", CYCLE, config)
    result = stage_raw(verdict, "nwm-raw", work_dir, "gfs", CYCLE, config)
    assert len(result.copied_files) == len(LEADS)
    assert result.manifest_path.is_file()


# --- Row：源侧 symlink（叶子与祖先段）----------------------------------------


def test_symlinked_bundle_is_refused_although_judge_says_complete(
    tmp_path: Path,
) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    base = cycle_dir(raw_root, "gfs")
    target = base / bundle_name("gfs", 3)
    real = base / "real-f003.grib2"
    target.rename(real)
    target.symlink_to(real)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    # 3.1/3.2 的有意不对称：judge 走 `is_file()`（跟随 symlink）判完整……
    assert verdict.complete is True
    # ……而 staging 拒绝为该链背书。
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "source-symlink")
    assert snapshot(work_dir) == {}


def test_symlinked_cycle_directory_segment_is_refused(tmp_path: Path) -> None:
    raw_root = tmp_path / "nwm-raw"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True)
    real_cycle = raw_root / "gfs" / "real-2026030400"
    real_cycle.mkdir(parents=True)
    for lead in LEADS:
        (real_cycle / bundle_name("gfs", lead)).write_bytes(bundle_bytes(lead))
    (real_cycle / SOURCE_MANIFEST_NAME).write_text(
        json.dumps(source_manifest_payload("gfs")), encoding="utf-8"
    )
    (raw_root / "gfs" / CYCLE_DIR).symlink_to(real_cycle, target_is_directory=True)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    assert verdict.complete is True
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "source-symlink")
    assert snapshot(work_dir) == {}


# --- Row：raw_root 与 work_dir 必须不相互包含 --------------------------------


def test_work_dir_under_raw_root_is_a_config_error(tmp_path: Path) -> None:
    """work 落在 raw 树内 -> `ConfigError`，raw 树逐字节不变。

    否则副本、目录与失败回滚的 unlink/rmdir 全都发生在 NWM raw 树里
    （`docs/compute-loop-design.md` §4.1 的硬约束）。归 `ConfigError`（「调用写错了」）
    而不是第十项 kind：九项词表由 fixture 钉死。
    """
    raw_root, _work_dir = build_tree(tmp_path)
    before = snapshot(raw_root)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, raw_root / "yd-work", "gfs", CYCLE, config)
    assert snapshot(raw_root) == before


def test_raw_root_under_work_dir_is_a_config_error(tmp_path: Path) -> None:
    """反向包含（raw 在 work 之下）同样拒绝——两个析取分支各自可判。"""
    raw_root, _work_dir = build_tree(tmp_path)
    before = snapshot(raw_root)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, tmp_path, "gfs", CYCLE, config)
    assert snapshot(raw_root) == before


def test_disjoint_sibling_roots_still_stage_normally(tmp_path: Path) -> None:
    """不相交的兄弟目录 MUST NOT 被上面那道闸门误拒（前缀相同也不算包含）。"""
    raw_root, _work_dir = build_tree(tmp_path)
    sibling = tmp_path / "nwm-raw-work"  # 与 raw_root 同前缀但不在其下
    sibling.mkdir()
    result = staged(raw_root, sibling)
    assert len(result.copied_files) == len(LEADS)


# --- Row：containment 的三种别名与一条合法路径 -------------------------------


def _case_insensitive(tmp_path: Path) -> bool:
    probe = tmp_path / "case-probe"
    probe.mkdir()
    return (tmp_path / "CASE-PROBE").is_dir()


def test_case_aliased_work_dir_inside_raw_root_is_refused(tmp_path: Path) -> None:
    """大小写别名：`<b>/NWM-RAW/work` 与 `<b>/nwm-raw` 词法不相交、物理同一棵树。

    `resolve()` 关不掉这条腿——CPython 的 posix 实现折叠 symlink 与 `..`，但**保留
    调用方给的非链组件大小写**。判据必须落到 inode 身份上。
    """
    raw_root, _work_dir = build_tree(tmp_path)
    if not _case_insensitive(tmp_path):
        pytest.skip("大小写敏感的卷上不存在该别名")
    alias_work = tmp_path / "NWM-RAW" / "work"
    # 前提取证：别名确实指向 raw_root 那个 inode，且两条路径词法不相交。
    assert os.path.samestat(os.stat(tmp_path / "NWM-RAW"), os.stat(raw_root))
    assert not alias_work.resolve().is_relative_to(raw_root.resolve())
    before = snapshot(raw_root)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, alias_work, "gfs", CYCLE, config)
    assert snapshot(raw_root) == before


def test_symlinked_work_dir_pointing_into_raw_root_is_refused(tmp_path: Path) -> None:
    """`work_dir` **自身**是一条指进 raw 树的链（#71 的目标侧逐段检查按设计跳过根，
    故那条工具在这里无效）。
    """
    raw_root, _work_dir = build_tree(tmp_path)
    real = raw_root / "work-real"
    real.mkdir()
    link = tmp_path / "worklink"
    link.symlink_to(real, target_is_directory=True)
    before = snapshot(raw_root)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, link, "gfs", CYCLE, config)
    assert snapshot(raw_root) == before


def test_dotdot_aliased_work_dir_inside_raw_root_is_refused(tmp_path: Path) -> None:
    """`..` 段：`<b>/side/../nwm-raw/work` 词法上不以 `<b>/nwm-raw` 为前缀。"""
    raw_root, _work_dir = build_tree(tmp_path)
    (tmp_path / "side").mkdir()
    alias_work = tmp_path / "side" / ".." / "nwm-raw" / "work"
    before = snapshot(raw_root)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, alias_work, "gfs", CYCLE, config)
    assert snapshot(raw_root) == before


def test_work_dir_reached_through_dotdot_outside_raw_root_stages_normally(
    tmp_path: Path,
) -> None:
    """合法路径 MUST NOT 被误拒：`<raw_root>/../work` 解析后是 raw 树的**兄弟**。

    这是该闸门唯一的无误拒判别器——既有三条用例（`raw_root/yd-work`、`tmp_path`、
    同前缀兄弟 `nwm-raw-work`）全部词法可判，在纯词法闸门下也照样绿。
    """
    raw_root, work_dir = build_tree(tmp_path)
    through_dotdot = raw_root / ".." / "work"
    # 前提取证：这条路径词法上以 raw_root 为前缀，解析后却在 raw 树之外。
    assert Path(through_dotdot).is_relative_to(raw_root)
    assert not through_dotdot.resolve().is_relative_to(raw_root.resolve())
    result = staged(raw_root, through_dotdot)
    assert len(result.copied_files) == len(LEADS)
    assert (work_dir / MANIFEST_NAME).is_file()


# --- Row：symlink 拒绝**先于**读源 manifest（顺序的判别器）-------------------


def test_symlinked_cycle_directory_is_refused_before_the_manifest_is_read(
    tmp_path: Path,
) -> None:
    """链目标里放一份畸形 `manifest.json`：两种顺序按 `kind` 分开。

    先读 manifest 的实现会以 `source-manifest` 失败（并且已经穿过那条 spec 说
    「不跟随」的链）；正确顺序以 `source-symlink` 失败。既有的链 cycle 目录用例背后
    是一份**合法** manifest，两种顺序同样报 `source-symlink`，判别不了顺序。
    """
    raw_root = tmp_path / "nwm-raw"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True)
    real_cycle = raw_root / "gfs" / "real-2026030400"
    real_cycle.mkdir(parents=True)
    for lead in LEADS:
        (real_cycle / bundle_name("gfs", lead)).write_bytes(bundle_bytes(lead))
    (real_cycle / SOURCE_MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    (raw_root / "gfs" / CYCLE_DIR).symlink_to(real_cycle, target_is_directory=True)
    # 前提取证：链后面那份 manifest 确实不可解析（先读就必然是 source-manifest）。
    with pytest.raises(json.JSONDecodeError):
        json.loads((real_cycle / SOURCE_MANIFEST_NAME).read_text(encoding="utf-8"))
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    assert verdict.complete is True
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "source-symlink")
    assert snapshot(work_dir) == {}


# --- Row：containment 的 inode 身份判据，`inner` **自身**分量 ------------------


def test_contains_by_identity_catches_inner_being_a_link_to_outer(
    tmp_path: Path,
) -> None:
    """`inner` 自身就是指向 `outer` 的链时，只有「自身分量」这一条腿咬得住。

    这是**无条件**判别器：`os.stat` 跟随 symlink，与卷的大小写敏感性无关，故它在
    ubuntu-latest（ext4，大小写敏感）与 darwin/APFS 上同样有判别力——而 seam 级的
    大小写别名用例在大小写敏感卷上必然自跳过（见本文件 `_case_insensitive`）。
    """
    outer = tmp_path / "nwm-raw"
    outer.mkdir()
    inner = tmp_path / "link-to-raw"
    inner.symlink_to(outer, target_is_directory=True)
    # 前提取证：`inner` 在**词法**上不在 `outer` 之下，故 `is_relative_to` 这类纯
    # 字符串前缀判据抓不到它。（原先这里比的是一个从未创建的 `elsewhere` 目录，对
    # 任意路径恒为 False——是恒真式，不是前提。）
    assert not inner.is_relative_to(outer)
    assert os.path.samestat(os.stat(inner), os.stat(outer))
    assert rawcopy_module._contains_by_identity(outer, inner) is True
    # 反向判别器：祖先段里没有 `outer` 时必须为 False（否则上一条恒真）。
    assert rawcopy_module._contains_by_identity(outer, tmp_path) is False


# --- Row：目标侧逐段 symlink 拒绝（#71 / raw-staging-target-containment）------
#
# 取证是 work 树与链目标的双向 no-follow 递归快照，不是只断言 manifest 缺席。
# `work_dir` 根豁免由合法根别名绿用例钉死；叶子链走 `copy-failed` 而不是
# `target-exists`（预检先于 `lexists`）。不覆盖独立 staging 的并发换根。


def _no_follow_snapshot(root: Path) -> dict[str, tuple[int, int, int, int]]:
    """递归快照，不跟随任何目录 symlink；非目录目标记录自身。"""
    if not os.path.lexists(root):
        return {}
    info = os.lstat(root)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return {
            ".": (
                info.st_size,
                info.st_mtime_ns,
                info.st_ino,
                info.st_mode,
            )
        }
    return snapshot(root)


def _destination_rel(component: str) -> Path:
    source_seg = DIR_SEGMENTS["gfs"]
    rels = {
        "raw": Path("raw"),
        "source": Path("raw") / source_seg,
        "cycle": Path("raw") / source_seg / CYCLE_DIR,
        "bundle": Path("raw") / source_seg / CYCLE_DIR / bundle_name("gfs", 6),
        "manifest": Path(MANIFEST_NAME),
    }
    return rels[component]


def _assert_zero_write_symlink_refusal(
    raw_root: Path, work_dir: Path, link_target: Path
) -> None:
    before_work = _no_follow_snapshot(work_dir)
    before_target = _no_follow_snapshot(link_target)
    before_source = snapshot(raw_root)
    before_source_content = content_snapshot(raw_root)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "copy-failed")
    assert _no_follow_snapshot(work_dir) == before_work
    assert _no_follow_snapshot(link_target) == before_target
    assert snapshot(raw_root) == before_source
    assert content_snapshot(raw_root) == before_source_content


@pytest.mark.parametrize(
    "component",
    ["raw", "source", "cycle", "bundle", "manifest"],
)
def test_target_descendant_symlink_is_refused_before_any_write(
    tmp_path: Path, component: str
) -> None:
    """外指链：raw / source / cycle 目录段与 bundle / manifest 叶子。"""
    raw_root, work_dir = build_tree(tmp_path)
    dest = work_dir / _destination_rel(component)
    dest.parent.mkdir(parents=True, exist_ok=True)
    elsewhere = tmp_path / f"elsewhere-{component}"
    if component in {"bundle", "manifest"}:
        elsewhere.write_bytes(b"external-payload")
        dest.symlink_to(elsewhere)
    else:
        elsewhere.mkdir()
        (elsewhere / "keep.txt").write_text("outside", encoding="utf-8")
        dest.symlink_to(elsewhere, target_is_directory=True)
    _assert_zero_write_symlink_refusal(raw_root, work_dir, elsewhere)


@pytest.mark.parametrize(
    "component",
    ["raw", "source", "cycle", "bundle", "manifest"],
)
def test_internal_destination_symlink_is_refused_before_any_write(
    tmp_path: Path, component: str
) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    dest = work_dir / _destination_rel(component)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if component in {"bundle", "manifest"}:
        inside = work_dir / "inside-payload"
        inside.write_bytes(b"internal-payload")
        dest.symlink_to(inside)
        target = inside
    else:
        inside = work_dir / "inside-dir"
        inside.mkdir()
        (inside / "keep.txt").write_text("inside", encoding="utf-8")
        dest.symlink_to(inside, target_is_directory=True)
        target = inside
    _assert_zero_write_symlink_refusal(raw_root, work_dir, target)


@pytest.mark.parametrize(
    "component",
    ["raw", "source", "cycle", "bundle", "manifest"],
)
def test_dangling_destination_symlink_is_refused_before_any_write(
    tmp_path: Path, component: str
) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    dest = work_dir / _destination_rel(component)
    dest.parent.mkdir(parents=True, exist_ok=True)
    missing = tmp_path / f"missing-{component}"
    dest.symlink_to(
        missing, target_is_directory=component not in {"bundle", "manifest"}
    )
    _assert_zero_write_symlink_refusal(raw_root, work_dir, missing)


def test_symlinked_work_root_alias_stages_normally(tmp_path: Path) -> None:
    """`work_dir` 根自身是指向独立目录的链：合法调用，副本与 manifest 落在物理根下。"""
    raw_root, _unused = build_tree(tmp_path)
    physical = tmp_path / "physical-work"
    physical.mkdir()
    alias = tmp_path / "work-alias"
    alias.symlink_to(physical, target_is_directory=True)
    before_source = snapshot(raw_root)
    before_source_content = content_snapshot(raw_root)

    result = staged(raw_root, alias)

    assert snapshot(raw_root) == before_source
    assert content_snapshot(raw_root) == before_source_content
    expected_copies = tuple(
        alias / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", lead) for lead in LEADS
    )
    assert result.copied_files == expected_copies
    assert result.manifest_path == alias / MANIFEST_NAME
    for lead, logical in zip(LEADS, expected_copies, strict=True):
        payload = bundle_bytes(lead)
        assert logical.read_bytes() == payload
        physical_copy = physical / logical.relative_to(alias)
        assert physical_copy.read_bytes() == payload
        assert not physical_copy.is_symlink()
    assert (physical / MANIFEST_NAME).is_file()
    assert not (physical / MANIFEST_NAME).is_symlink()
    written = json.loads((physical / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert written["source_id"] == "gfs"
    assert written["cycle_time"] == CYCLE_ISO
