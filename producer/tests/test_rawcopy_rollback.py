"""`yd_producer.rawcopy.stage_raw` 复制失败回滚与目标已存在回归。"""

import errno
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from rawcopy_fixtures import (
    CYCLE,
    CYCLE_DIR,
    GFS_VARIABLES,
    LEADS,
    MANIFEST_NAME,
    SOURCE_MANIFEST_NAME,
    build_tree,
    bundle_name,
    content_snapshot,
    cycle_dir,
    expect_kind,
    make_config,
    snapshot,
    source_entry,
    source_manifest_payload,
    staged,
)

from yd_producer import rawcopy as rawcopy_module
from yd_producer.config import ConfigError
from yd_producer.rawcopy import (
    RawStagingError,
    stage_raw,
)
from yd_producer.rawscan import judge

# --- Row：目标已存在 ---------------------------------------------------------


def test_existing_target_file_is_never_overwritten(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    target = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"pre-existing")
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "target-exists")
    assert target.read_bytes() == b"pre-existing"
    assert not (work_dir / MANIFEST_NAME).exists()
    assert sorted(p.name for p in target.parent.iterdir()) == [target.name]


def test_existing_raw_manifest_is_never_overwritten(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    (work_dir / MANIFEST_NAME).write_text("stale", encoding="utf-8")
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "target-exists")
    assert (work_dir / MANIFEST_NAME).read_text(encoding="utf-8") == "stale"
    assert not (work_dir / "raw").exists()


# --- Row：复制期失败的两条清理路径 -------------------------------------------


def test_source_mutated_during_copy_leaves_no_partial_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 k 个源文件在复制窗口内被替换 -> `source-mutated`，work 侧不留半套。

    注入点选在 `os.lstat`：当第 k 个副本已经落盘（即该文件的复制已开始）时，把源
    文件真实改掉——复制后的那次 `lstat` 于是自然拿到不同的元组。这不绕过被测闸门，
    被测的是「前后元组比对」本身。
    """
    raw_root, work_dir = build_tree(tmp_path)
    victim = cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)
    victim_copy = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)
    real_lstat = os.lstat
    state = {"done": False}

    def hooked_lstat(path, *args, **kwargs):
        if (
            not state["done"]
            and str(path) == str(victim)
            and os.path.exists(victim_copy)
        ):
            state["done"] = True
            # 等长替换：size 不变，只有 mtime_ns 变——这是「只比对内容/大小不算」
            # 的判别器（`_identity` 若退化成只比 size，本用例即无法变红）。
            victim.write_bytes(b"GRIB\xff\x00lead-XXX")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", hooked_lstat)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    expect_kind(excinfo, "source-mutated")
    assert state["done"] is True
    assert snapshot(work_dir) == {}


def test_copy_failure_leaves_no_partial_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 k 个文件的目标不可写（ENOSPC/权限）-> `copy-failed`，work 侧不留半套。"""
    raw_root, work_dir = build_tree(tmp_path)
    doomed = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 6)
    real_open = os.open

    def hooked_open(path, flags, *args, **kwargs):
        if str(path) == str(doomed):
            raise OSError(errno.ENOSPC, "No space left on device", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", hooked_open)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    expect_kind(excinfo, "copy-failed")
    assert snapshot(work_dir) == {}


def test_unwritable_work_directory_reports_copy_failed(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    mode = work_dir.stat().st_mode
    os.chmod(work_dir, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(RawStagingError) as excinfo:
            staged(raw_root, work_dir)
        expect_kind(excinfo, "copy-failed")
    finally:
        os.chmod(work_dir, mode)
    assert snapshot(work_dir) == {}


# --- Row：不变的兄弟面（源树与 YD_ROOT 模拟根）-------------------------------


def test_source_tree_is_byte_and_metadata_identical_after_staging(
    tmp_path: Path,
) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    before = snapshot(raw_root)
    before_content = content_snapshot(raw_root)
    staged(raw_root, work_dir)
    assert snapshot(raw_root) == before
    assert content_snapshot(raw_root) == before_content


def test_yd_root_mock_is_untouched_and_holds_no_raw_copy(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    yd_root = tmp_path / "yd-root"
    (yd_root / "output").mkdir(parents=True)
    (yd_root / "output" / "keep.txt").write_text("published", encoding="utf-8")
    before = snapshot(yd_root)
    staged(raw_root, work_dir)
    assert snapshot(yd_root) == before
    assert list(yd_root.rglob("raw")) == []
    assert list(yd_root.rglob("*.grib2")) == []


# --- Row：复制期的**任何**异常都清理，且不外泄九项词表之外 --------------------


def test_bare_exception_inside_the_write_block_still_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写入块里抛出的非 `RawStagingError` 也必须触发回滚并收敛成九项之一。

    只接 `RawStagingError` 的清理触发器窄于它要维护的不变量：写入块里任何别的异常
    （裸 `ValueError`、`UnicodeEncodeError`、被替换的原语抛出的任意异常）都会绕过
    `written.rollback()` **并**逃出闭合词表。注入点选在第三个目标的 `os.open`，此时
    前两份副本与两级目录都已落盘，故存活的残留是可见的。
    """
    raw_root, work_dir = build_tree(tmp_path)
    doomed = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 6)
    real_open = os.open
    landed: list[str] = []

    def hooked_open(path, flags, *args, **kwargs):
        if str(path) == str(doomed):
            # 刻意不是 OSError：`_copy_one` 的 except OSError 腿接不到它。
            raise RuntimeError("注入的非 OSError 故障")
        fd = real_open(path, flags, *args, **kwargs)
        landed.append(str(path))
        return fd

    monkeypatch.setattr(os, "open", hooked_open)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    # 注入点确实在「已有副本落盘之后」触发，残留是可构造的。
    assert len([p for p in landed if p.startswith(str(work_dir))]) == 2
    expect_kind(excinfo, "copy-failed")
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert snapshot(work_dir) == {}


def test_keyboard_interrupt_mid_copy_still_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`KeyboardInterrupt` 照样清理，但**不**被改写成 `RawStagingError`。

    「不留半套副本」与异常类型无关，故 `BaseException` 也要触发回滚；而把 Ctrl-C
    改写成一次 staging 失败会让操作者看到一个假的 `copy-failed`，故这一支原样外抛
    ——它是本函数唯一有意保留的、九项词表之外的出口。
    """
    raw_root, work_dir = build_tree(tmp_path)
    doomed = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 6)
    real_open = os.open

    def hooked_open(path, flags, *args, **kwargs):
        if str(path) == str(doomed):
            raise KeyboardInterrupt
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", hooked_open)
    with pytest.raises(KeyboardInterrupt):
        staged(raw_root, work_dir)
    monkeypatch.undo()
    assert snapshot(work_dir) == {}


def test_non_utf8_encodable_carried_value_is_refused_before_any_write(
    tmp_path: Path,
) -> None:
    """承接来的值不可 UTF-8 编码 -> 准入期 `source-manifest`，零写入。

    源 manifest 是外部 JSON：`json.load` 接受转义的孤代理 `\\ud800` 并还原成真正的
    孤代理 str，`json.dumps(ensure_ascii=False)` 也照样吐出它，直到写 UTF-8 流才抛
    `UnicodeEncodeError`。

    序列化前置于复制买到的**不是**「避免半套产物」：`_render_manifest` 自己抛
    `RawStagingError(kind="source-manifest")`，即便它留在复制之后，写入段的
    `except RawStagingError` 也会回滚并原样再抛，0 字节 raw-manifest.json 从未被创建、
    本用例的 `snapshot(work_dir) == {}` 照样成立（round 5 实测）。真实收益是：这段
    **注定失败**的输入，其失败点留在任何复制之前，于是零写入**不依赖回滚自身成功**
    ——回滚失败在本仓是被承认的可失败动作，届时残留会让下一次重试被 `lexists` 预检
    以 `target-exists` 硬拒。位置属性本身由
    `test_manifest_serialization_call_site_is_inside_the_admission_floor` 判别，本用例
    判别的是「承接值能否编码」。
    """
    payload = source_manifest_payload("gfs")
    metadata = source_entry(payload, LEADS[0], GFS_VARIABLES[0])["metadata"]
    metadata["grib_short_name"] = "2t\ud800"
    metadata["cfgrib_filter_by_keys"]["shortName"] = "2t\ud800"
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    on_disk = (cycle_dir(raw_root, "gfs") / SOURCE_MANIFEST_NAME).read_text(
        encoding="utf-8"
    )
    # 源文件本身是纯 ASCII（孤代理以 6 字符转义存在），不依赖任何非法落盘字节。
    assert on_disk.isascii() and "\\ud800" in on_disk
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert not isinstance(excinfo.value, ValueError)
    assert snapshot(work_dir) == {}


@pytest.mark.parametrize(
    "nonfinite",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "+inf", "-inf"],
)
def test_nonfinite_carried_cfgrib_filter_fails_closed(
    tmp_path: Path, nonfinite: float
) -> None:
    payload = source_manifest_payload("gfs")
    source_entry(payload, LEADS[0], GFS_VARIABLES[0])["metadata"][
        "cfgrib_filter_by_keys"
    ]["level"] = nonfinite
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    before_work = snapshot(work_dir)
    before_source = snapshot(raw_root)
    before_source_content = content_snapshot(raw_root)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == before_work == {}
    assert snapshot(raw_root) == before_source
    assert content_snapshot(raw_root) == before_source_content


def test_finite_carried_cfgrib_filter_value_survives_strict_parse(
    tmp_path: Path,
) -> None:
    finite_level = 850.25
    payload = source_manifest_payload("gfs")
    source_entry(payload, LEADS[0], GFS_VARIABLES[0])["metadata"][
        "cfgrib_filter_by_keys"
    ]["level"] = finite_level
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)

    def reject_constant(name: str) -> None:
        raise ValueError(name)

    written = json.loads(
        result.manifest_path.read_text(encoding="utf-8"),
        parse_constant=reject_constant,
    )
    assert (
        source_entry(written, LEADS[0], GFS_VARIABLES[0])["metadata"][
            "cfgrib_filter_by_keys"
        ]["level"]
        == finite_level
    )


def test_mkdir_failing_midway_leaves_no_directories_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`mkdir(parents=True)` 建到一半失败 -> 已建的祖先段也必须被回滚掉。

    注入方式是**数成功的创建次数**：`Path.mkdir(parents=True)` 先试叶子、拿
    `FileNotFoundError` 再回溯建父目录，用「第 n 次调用即失败」会打在还没创建任何
    目录的探测腿上，抓不到本用例要抓的中途失败。
    """
    raw_root, work_dir = build_tree(tmp_path)
    real_mkdir = os.mkdir
    created: list[str] = []

    def hooked_mkdir(path, *args, **kwargs):
        if len(created) >= 2:
            raise OSError(errno.EDQUOT, "Disk quota exceeded", str(path))
        real_mkdir(path, *args, **kwargs)
        created.append(str(path))

    monkeypatch.setattr(os, "mkdir", hooked_mkdir)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    # 前提取证：确实先成功建了两级目录，才轮到第三级失败。
    assert created == [
        str(work_dir / "raw"),
        str(work_dir / "raw" / "gfs"),
    ]
    expect_kind(excinfo, "copy-failed")
    assert snapshot(work_dir) == {}


# --- Row：`_identity` 四元组的逐分量判别器 -----------------------------------


def _mutate_source_during_copy(
    monkeypatch: pytest.MonkeyPatch, victim: Path, victim_copy: Path, mutate
) -> dict[str, bool]:
    """在第 k 份副本已落盘、复制后那次 `lstat` 之前改动源文件。

    注入点选在 `os.lstat`：被测的是「复制前后元组比对」本身，不绕过任何闸门。
    """
    real_lstat = os.lstat
    state = {"done": False}

    def hooked_lstat(path, *args, **kwargs):
        if (
            not state["done"]
            and str(path) == str(victim)
            and os.path.exists(victim_copy)
        ):
            state["done"] = True
            mutate(real_lstat(victim))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", hooked_lstat)
    return state


def _expect_source_mutated(raw_root: Path, work_dir: Path, state: dict[str, bool]):
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    assert state["done"] is True
    expect_kind(excinfo, "source-mutated")
    assert snapshot(work_dir) == {}


def test_source_replaced_by_an_equal_stat_file_is_caught_by_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整体替换：size/mtime_ns/mode 全部还原，只有 `st_ino` 变——`ino` 分量的判别器。

    这正是 `_identity` docstring 自称能抓的「同内容不同 inode 的整体替换」；没有本
    用例时把 `st_ino` 从元组里删掉，整套仍然全绿。
    """
    raw_root, work_dir = build_tree(tmp_path)
    victim = cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)
    victim_copy = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)
    replacement = tmp_path / "replacement.grib2"

    def swap(before: os.stat_result) -> None:
        replacement.write_bytes(victim.read_bytes())  # 等长、等内容
        os.replace(replacement, victim)  # 换 inode
        os.chmod(victim, stat.S_IMODE(before.st_mode))
        os.utime(victim, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(victim)
        # 前提取证：确实只有 ino 变了，别的分量都还原了。
        assert after.st_ino != before.st_ino
        assert (after.st_size, after.st_mtime_ns, after.st_mode) == (
            before.st_size,
            before.st_mtime_ns,
            before.st_mode,
        )

    state = _mutate_source_during_copy(monkeypatch, victim, victim_copy, swap)
    _expect_source_mutated(raw_root, work_dir, state)


def test_source_chmodded_during_copy_is_caught_by_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只改权限位：size/mtime_ns/ino 全不变——`mode` 分量的判别器。"""
    raw_root, work_dir = build_tree(tmp_path)
    victim = cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)
    victim_copy = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)

    def chmod(before: os.stat_result) -> None:
        os.chmod(victim, stat.S_IMODE(before.st_mode) ^ stat.S_IWUSR)
        after = os.stat(victim)
        assert after.st_mode != before.st_mode
        assert (after.st_size, after.st_mtime_ns, after.st_ino) == (
            before.st_size,
            before.st_mtime_ns,
            before.st_ino,
        )

    state = _mutate_source_during_copy(monkeypatch, victim, victim_copy, chmod)
    _expect_source_mutated(raw_root, work_dir, state)


def test_source_appended_during_copy_is_caught_by_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只改长度：mtime_ns 还原、ino/mode 不变——`size` 分量的判别器。"""
    raw_root, work_dir = build_tree(tmp_path)
    victim = cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)
    victim_copy = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)

    def append(before: os.stat_result) -> None:
        with open(victim, "ab") as handle:
            handle.write(b"\x00")
        os.utime(victim, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(victim)
        assert after.st_size != before.st_size
        assert (after.st_mtime_ns, after.st_ino, after.st_mode) == (
            before.st_mtime_ns,
            before.st_ino,
            before.st_mode,
        )

    state = _mutate_source_during_copy(monkeypatch, victim, victim_copy, append)
    _expect_source_mutated(raw_root, work_dir, state)


# --- Row：回滚自身失败（`rollback` 保证不抛 + 失败带进外抛异常）---------------


def _copy_failure_with_broken_rollback(
    monkeypatch: pytest.MonkeyPatch, work_dir: Path, failure: BaseException
):
    """让第三份副本的创建失败，并让**回滚原语**抛一个非 `OSError`。

    两个注入合在一起才是本类的判别器：只让复制失败，回滚会成功、什么也测不到；
    只让回滚失败，没有触发回滚的失败路径。
    """
    doomed = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 6)
    real_open = os.open

    def hooked_open(path, flags, *args, **kwargs):
        if str(path) == str(doomed):
            raise failure
        return real_open(path, flags, *args, **kwargs)

    def hooked_unlink(path, *args, **kwargs):
        # 刻意不是 OSError：`rollback` 原先只吞 `OSError`，这条会**替换**正在外抛的
        # `RawStagingError` 并逃出九项闭合词表（round-2 verifier 在 NUL 路径上实测过
        # 同一机制，`os.rmdir` 抛裸 `ValueError`）。
        raise ValueError(f"注入的非 OSError 清理故障：{path}")

    monkeypatch.setattr(os, "open", hooked_open)
    monkeypatch.setattr(os, "unlink", hooked_unlink)


def test_rollback_failure_is_reported_and_never_replaces_the_staging_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回滚原语抛非 `OSError` 时：外抛的仍是 `RawStagingError`，且残留有信号。

    `rollback` 在三个 handler 里都跑在「已有异常正在外抛」的上下文里，它自己抛出的
    异常会**替换**那个异常——于是一个纯入参就能让裸 `ValueError` 逃出 `stage_raw`。
    收口点只能在 `rollback` 内部（handler 加 `except` 拦不住它自己）。配套的另一半
    是**不静默**：清理失败必须进入外抛的异常，否则「不留任何部分产物」这条无条件
    不变量失守时无任何信号，而残留会让下一次重试被 `target-exists` 楔死。
    """
    raw_root, work_dir = build_tree(tmp_path)
    _copy_failure_with_broken_rollback(
        monkeypatch,
        work_dir,
        OSError(errno.ENOSPC, "No space left on device"),
    )
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    expect_kind(excinfo, "copy-failed")
    assert not isinstance(excinfo.value, ValueError)
    notes = "".join(getattr(excinfo.value, "__notes__", []))
    assert "清理" in notes and "残留" in notes
    # 信号必须与事实一致：这两份副本确实还在。
    survivors = sorted(p.name for p in (work_dir / "raw" / "gfs" / CYCLE_DIR).iterdir())
    assert survivors == [bundle_name("gfs", lead) for lead in (0, 3)]
    for name in survivors:
        assert name in notes


def test_tier2_message_stops_claiming_cleanup_when_rollback_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非 `RawStagingError` 腿的消息 MUST NOT 在清理失败时仍宣称「已清理」。

    这是本轮唯一被实测出会说假话的路径（tier-1 的消息不含清理声明）：残留 2 份副本
    的同时，异常消息逐字写着「已清理本轮 work 侧写入」。
    """
    raw_root, work_dir = build_tree(tmp_path)
    _copy_failure_with_broken_rollback(
        monkeypatch, work_dir, RuntimeError("注入的非 OSError 故障")
    )
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    expect_kind(excinfo, "copy-failed")
    message = str(excinfo.value)
    assert "已清理本轮 work 侧写入" not in message
    assert "残留" in message
    assert snapshot(work_dir) != {}


def test_successful_rollback_still_reports_a_clean_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向判别器：回滚成功时消息仍宣称「已清理」，且不挂任何残留 note。

    没有这条，「一律不说已清理」的实现也能让上面那条变绿。
    """
    raw_root, work_dir = build_tree(tmp_path)
    doomed = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 6)
    real_open = os.open

    def hooked_open(path, flags, *args, **kwargs):
        if str(path) == str(doomed):
            raise RuntimeError("注入的非 OSError 故障")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", hooked_open)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    assert "已清理本轮 work 侧写入" in str(excinfo.value)
    assert getattr(excinfo.value, "__notes__", []) == []
    assert snapshot(work_dir) == {}


def test_null_byte_work_dir_is_refused_without_leaking_a_bare_value_error(
    tmp_path: Path,
) -> None:
    """NUL 字节的 `work_dir`：纯入参、无注入，原先让裸 `ValueError` 逃出 `stage_raw`。

    链条是 `Path.exists()`/`os.path.lexists` 都自吞 `ValueError` 返 `False`，于是整条
    NUL 祖先链被登记进账本，`mkdir` 抛裸 `ValueError` -> tier-2 -> `rollback` 的
    `os.rmdir` 再抛裸 `ValueError` 把它顶掉。现在在归一闸门上以 `ConfigError`（形参
    写错）短路，零写入。
    """
    raw_root, work_dir = build_tree(tmp_path)
    before = snapshot(work_dir)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError) as excinfo:
        stage_raw(verdict, raw_root, f"{work_dir}/w\x00x", "gfs", CYCLE, config)
    assert not isinstance(excinfo.value, ValueError)
    assert snapshot(work_dir) == before


# --- Row：tier-3（`BaseException`）的残留信号 --------------------------------


def test_keyboard_interrupt_with_a_failing_rollback_carries_the_residue_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tier-3 的 `if failures: exc.add_note(...)` 的判别器。

    既有的唯一一条 `KeyboardInterrupt` 用例里回滚是**成功**的，于是 `failures` 恒空、
    该分支从不进入，删掉整段 `add_note` 全套件不变红（round-3 verifier 变异体 E4
    存活）。判别器必须**同时**注入两处：中断复制 + 让回滚原语失败。
    """
    raw_root, work_dir = build_tree(tmp_path)
    _copy_failure_with_broken_rollback(monkeypatch, work_dir, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    # 类型 MUST NOT 被改写成 `RawStagingError`：Ctrl-C 不是一次 staging 失败。
    assert not isinstance(excinfo.value, RawStagingError)
    notes = "".join(getattr(excinfo.value, "__notes__", []))
    assert "清理" in notes and "残留" in notes
    survivors = sorted(p.name for p in (work_dir / "raw" / "gfs" / CYCLE_DIR).iterdir())
    assert survivors == [bundle_name("gfs", lead) for lead in (0, 3)]
    for name in survivors:
        assert name in notes


# --- Row：`rollback`「保证不抛」在 repr 抛异常时也成立 ------------------------


def test_rollback_does_not_raise_when_the_exception_repr_itself_raises() -> None:
    """`{exc!r}` 在 handler 内部求值，`repr` 自身抛异常就会击穿「保证不抛」。

    这是 `rollback` 的兜底逻辑**低一层**的洞：三个 handler 都跑在「已有异常正在外抛」
    的上下文里，`rollback` 抛出的任何异常都会替换那个异常并逃出九项词表。
    """

    class NastyError(OSError):
        def __repr__(self) -> str:
            raise RuntimeError("repr 自身炸了")

    written = rawcopy_module._Written()
    written.files.append(Path("/nonexistent/probe-file"))

    def exploding_unlink(path: Any) -> None:
        raise NastyError(errno.EIO, "注入的清理故障")

    real_unlink = os.unlink
    # 直接替换而不是 monkeypatch：`rollback` 读的是 `os.unlink` 这个全局绑定。
    os.unlink = exploding_unlink
    try:
        failures = written.rollback()
    finally:
        os.unlink = real_unlink
    assert len(failures) == 1
    assert "probe-file" in failures[0]
    assert "repr" in failures[0]  # 兜底文案，而不是抛出
