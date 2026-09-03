"""Issue #94 exact-work 危险删除闸：`work_dir == work_root/source/cycle` 逐字校验。

fixture（tasks.md `### Issue #26 fixture` Required evidence 19）：四轴（少一层 / 多一层 /
兄弟 source / 兄弟 cycle）各自构造 `PublishInputs` -> 在**任何 IO 之前**拒绝；正例通过。
独立于已 2066 行的 `test_publish.py`（本文件不膨胀既有账本）。
"""

from __future__ import annotations

import pathlib

import pytest
from run_once_fixtures import (
    CYCLE,
    EXPECTED_ROWS,
    REACH_COUNT,
    make_publish_inputs,
    write_config_local,
)

from yd_producer import publish


def _scene(tmp_path: pathlib.Path):
    """铺一棵合成树（不真实跑链）：work 根与兄弟 work 目录 + 快照。"""
    config, local = write_config_local(tmp_path)
    root = pathlib.Path(local.yd_root)
    scratch = pathlib.Path(local.scratch_root).resolve()
    work_root = scratch / "work"
    valid = work_root / "gfs" / "2026082612"
    valid.mkdir(parents=True)
    (valid / "model").mkdir()
    (work_root / "ifs" / "2026082612").mkdir(parents=True)
    (work_root / "gfs" / "2026082700").mkdir(parents=True)
    (root / "states" / "gfs").mkdir(parents=True)
    (root / "states" / "gfs" / "2026082612.cfg.ic").write_bytes(b"x")
    return config, local, root, work_root, valid


def _snapshot(root: pathlib.Path):
    return sorted(
        (str(p.relative_to(root)), p.lstat().st_mode) for p in root.rglob("*")
    )


@pytest.mark.parametrize(
    ("label", "work_rel"),
    [
        ("少一层", "gfs"),
        ("多一层", "gfs/2026082612/model"),
        ("兄弟 source", "ifs/2026082612"),
        ("兄弟 cycle", "gfs/2026082700"),
    ],
)
def test_exact_work_shape_rejected_before_any_io(
    tmp_path: pathlib.Path, label: str, work_rel: str
) -> None:
    config, local, root, work_root, valid = _scene(tmp_path)
    before_root = _snapshot(root)
    before_scratch = _snapshot(work_root.parent)

    with pytest.raises(ValueError, match="必须逐字等于"):
        make_publish_inputs(local=local, config=config, source="gfs").__class__(
            yd_root=local.yd_root,
            source="gfs",
            cycle=CYCLE,
            scratch_dat=valid / "output" / "yd.rivqdown.dat",
            scratch_checkpoint=valid / "model" / "c.cfg.ic",
            merged_log=valid / "job.log",
            work_dir=work_root / work_rel,
            work_root=work_root,
            expected_rows=EXPECTED_ROWS,
            reach_count=REACH_COUNT,
            variant_reach_count=REACH_COUNT,
        )

    # 零 IO：两棵树逐项不变（构造失败发生在 any IO 之前）。
    assert _snapshot(root) == before_root
    assert _snapshot(work_root.parent) == before_scratch


def test_exact_work_shape_positive_case_passes(tmp_path: pathlib.Path) -> None:
    config, local, _root, work_root, valid = _scene(tmp_path)
    inputs = make_publish_inputs(local=local, config=config, source="gfs")
    assert inputs.work_dir == valid
    # 正例只需构造通过（发布需要的产物不在本文件面内，构造即契约）。
    assert inputs.work_root == work_root
    assert inputs.source == "gfs"
    assert inputs.cycle == CYCLE


def test_work_guard_runs_at_construction_not_at_delete(
    tmp_path: pathlib.Path,
) -> None:
    """守卫在 `PublishInputs.__post_init__`（危险边界），而不是只靠唯一 caller。"""
    _config, local, _root, work_root, valid = _scene(tmp_path)
    # 直接构造非法形状：必须构造期失败，即使 publish 主干从未被调用。
    with pytest.raises(ValueError):
        publish.PublishInputs(
            yd_root=local.yd_root,
            source="gfs",
            cycle=CYCLE,
            scratch_dat=valid / "output" / "yd.rivqdown.dat",
            scratch_checkpoint=valid / "model" / "c.cfg.ic",
            merged_log=valid / "job.log",
            work_dir=work_root / "gfs",
            work_root=work_root,
            expected_rows=EXPECTED_ROWS,
            reach_count=REACH_COUNT,
            variant_reach_count=REACH_COUNT,
        )
