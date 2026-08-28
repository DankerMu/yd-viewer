# NWM@8ae9b8f2 tests/test_shud_runtime.py
"""`yd_producer.tracker.checkpoint_tracker` 的行为测试（任务 9.1）。

重放纪律：SHUD 的就地覆写用「按序覆写 `source_path` + 按序调用 `capture_available()`」
确定性重放，不起真进程、不 sleep——本模块本来就不含轮询循环。

oracle 纪律：合成 `cfg.ic` 一律由 `cfg_ic_fixtures.build_cfg_ic` 生成，header 分钟由入参
给定，期望副本字节即「写进源文件的那一版字节」本身；`state.parse` 只作为既有面被当作
副本校验的 oracle 使用，不在此重测。

判别力纪律：正常捕获路径上，「相等判据放宽」「去掉 `round()`」「去掉已捕获跳过」「去掉
相邻去重」「补零改掉」「校验内存副本而非回读字节」六种改坏各自都有专属用例接住，见各
用例的 docstring。
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest
from cfg_ic_fixtures import build_cfg_ic

from yd_producer import state
from yd_producer.store import safe_fs
from yd_producer.tracker import CapturedCheckpoint, CheckpointTracker, TrackerError
from yd_producer.tracker import checkpoint_tracker as tracker_module

PROJECT = "yd_gfs"
HOURS = (12,)


def _payload(minute: str, *, mesh_count: int = 3) -> bytes:
    """一份 header 分钟为 `minute` 的完整原生分段 `cfg.ic`。"""
    return build_cfg_ic(mesh_count=mesh_count, river_count=2, minute=minute).payload


def _truncated_payload(minute: str) -> bytes:
    """header 完好、body 被截断（`state.parse` 必抛 `ValueError`）的合成内容。"""
    built = build_cfg_ic(mesh_count=3, river_count=2, minute=minute)
    kept = [
        line
        for index, line in enumerate(built.lines)
        if index != built.mesh_data_indices[-1]
    ]
    payload = "".join(kept).encode("utf-8")
    with pytest.raises(ValueError):
        # 前置断言：这份内容确实是解析器拒绝的那一类，否则整条失败路径的用例全是空转。
        state.parse(payload)
    return payload


def _write(path: pathlib.Path, payload: bytes) -> bytes:
    """模拟 SHUD 的一次就地覆写。"""
    path.write_bytes(payload)
    return payload


def _tracker(
    run_dir: pathlib.Path,
    *,
    project_name: str = PROJECT,
    checkpoint_hours: tuple[int, ...] = HOURS,
) -> CheckpointTracker:
    return CheckpointTracker(
        run_dir=run_dir, project_name=project_name, checkpoint_hours=checkpoint_hours
    )


# --- G2 构造期 fail closed ---


@pytest.mark.parametrize(
    ("hours", "reason"),
    [
        ((), "为空"),
        ((0,), "非正"),
        ((-12,), "非正"),
        ((12, 12), "重复"),
    ],
)
def test_construction_rejects_unusable_checkpoint_hours(
    tmp_path: pathlib.Path, hours: tuple[int, ...], reason: str
) -> None:
    """pin 对这三类输入静默过滤；本模块 fail closed（偏离 1）。"""
    with pytest.raises(TrackerError, match=reason):
        _tracker(tmp_path, checkpoint_hours=hours)


@pytest.mark.parametrize("project_name", ["", "a/b", ".", ".."])
def test_construction_rejects_unusable_project_name(
    tmp_path: pathlib.Path, project_name: str
) -> None:
    with pytest.raises(TrackerError):
        _tracker(tmp_path, project_name=project_name)


def test_construction_does_not_touch_the_filesystem(tmp_path: pathlib.Path) -> None:
    """SHUD 尚未启动时构造出的 tracker 也必须是安全的：零新增条目。"""
    _tracker(tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- G3 正常捕获 ---


def test_capture_over_an_overwrite_sequence(tmp_path: pathlib.Path) -> None:
    tracker = _tracker(tmp_path)
    payload_720 = _payload("720.000000")
    for payload in (_payload("360.000000"), payload_720, _payload("1440.000000")):
        _write(tracker.source_path, payload)
        tracker.capture_available()

    assert tracker.missing_hours() == ()
    record = tracker.captured[12]
    assert isinstance(record, CapturedCheckpoint)
    assert record.lead_hours == 12
    assert record.relative_minute == 720.0
    assert record.source_name == f"{PROJECT}.cfg.ic.update"
    assert (
        record.path == tmp_path / "state_checkpoints" / f"{PROJECT}.f012.cfg.ic.update"
    )
    assert record.path.is_file()
    # 「产物保持相对时间头」的判据：副本与写入 720 那一版**逐字节相等**，首行仍是相对
    # 分钟头，没有被改写成绝对时间。
    assert record.path.read_bytes() == payload_720
    assert record.checksum == hashlib.sha256(payload_720).hexdigest()
    assert tracker.observed_header_minutes == (360.0, 720.0, 1440.0)


def test_capture_is_one_shot_even_when_the_same_minute_reappears_torn(
    tmp_path: pathlib.Path,
) -> None:
    """钉死 `if hour in captured: continue`。

    去掉该跳过守卫后：第二次同值观测重入捕获 → 覆写好副本 → 校验失败 → 删副本，于是
    `captured[12].path` 变成悬空路径而 `missing_hours()` 仍报空——静默数据丢失伪装成成功。
    「继续到 1440 再观测」对该守卫零判别力（`round(1440) != round(720)`，本来就不重入）。
    """
    tracker = _tracker(tmp_path)
    payload_720 = _write(tracker.source_path, _payload("720.000000"))
    tracker.capture_available()
    before = tracker.captured[12]

    _write(tracker.source_path, _truncated_payload("720.000000"))
    tracker.capture_available()

    assert tracker.captured[12] == before
    assert before.path.is_file()
    assert before.path.read_bytes() == payload_720
    assert tracker.missing_hours() == ()


def test_capture_final_is_a_working_alias(tmp_path: pathlib.Path) -> None:
    """别名不得被实现成 no-op：逐字段与 `capture_available()` 的结果相同。"""
    by_final = _tracker(tmp_path / "a")
    by_available = _tracker(tmp_path / "b")
    for tracker in (by_final, by_available):
        tracker.source_path.parent.mkdir()
        _write(tracker.source_path, _payload("720.000000"))
    by_final.capture_final()
    by_available.capture_available()

    final_record = by_final.captured[12]
    available_record = by_available.captured[12]
    assert final_record.lead_hours == available_record.lead_hours
    assert final_record.relative_minute == available_record.relative_minute
    assert final_record.source_name == available_record.source_name
    assert final_record.checksum == available_record.checksum
    assert final_record.path.name == available_record.path.name


# --- G4 漏采如实报告 ---


def test_sequence_skipping_the_target_reports_the_miss(tmp_path: pathlib.Path) -> None:
    """相等判据放宽成 `<=` / `>=` 时，`360`（或 `1440`）会去试捕获而建出目录——故这里
    断言目录**根本不存在**，而不是「存在但为空」：后者放跑该变异。
    """
    tracker = _tracker(tmp_path)
    for payload in (_payload("360.000000"), _payload("1440.000000")):
        _write(tracker.source_path, payload)
        tracker.capture_available()

    assert tracker.missing_hours() == (12,)
    assert dict(tracker.captured) == {}
    assert not tracker.checkpoint_dir.exists()
    # 漏采时观测轨迹仍完整留痕，诊断可定位。
    assert tracker.observed_header_minutes == (360.0, 1440.0)


# --- G5 副本校验失败不算成功 ---


def test_torn_body_is_not_a_capture_and_leaves_no_copy(tmp_path: pathlib.Path) -> None:
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _truncated_payload("720.000000"))
    tracker.capture_available()

    assert tracker.missing_hours() == (12,)
    assert dict(tracker.captured) == {}
    assert not (tracker.checkpoint_dir / f"{PROJECT}.f012.cfg.ic.update").exists()


def test_a_torn_capture_is_retried_on_the_next_observation(
    tmp_path: pathlib.Path,
) -> None:
    """「这次撕裂了，下次再来」：校验失败不是终态。"""
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _truncated_payload("720.000000"))
    tracker.capture_available()
    assert tracker.missing_hours() == (12,)

    payload_720 = _write(tracker.source_path, _payload("720.000000"))
    tracker.capture_available()

    assert tracker.missing_hours() == ()
    assert tracker.captured[12].path.read_bytes() == payload_720


def test_validation_reads_the_copy_back_from_disk(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """唯一能把「校验回读字节」与「校验写前那份内存副本」区分开的形态。

    源文件完整，落盘却被截断：校验内存副本的实现会一路绿灯放行一个残缺状态。
    """
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _payload("720.000000"))
    torn = _truncated_payload("720.000000")
    real_write = safe_fs.atomic_write_bytes_no_follow

    def truncating_write(path, content, **kwargs):  # type: ignore[no-untyped-def]
        return real_write(path, torn, **kwargs)

    monkeypatch.setattr(safe_fs, "atomic_write_bytes_no_follow", truncating_write)
    tracker.capture_available()

    assert tracker.missing_hours() == (12,)
    assert dict(tracker.captured) == {}
    assert not (tracker.checkpoint_dir / f"{PROJECT}.f012.cfg.ic.update").exists()


def test_missing_source_is_silently_no_result(tmp_path: pathlib.Path) -> None:
    tracker = _tracker(tmp_path)
    tracker.capture_available()

    assert dict(tracker.captured) == {}
    assert tracker.observed_header_minutes == ()


@pytest.mark.parametrize(
    "payload",
    [b"", b"\n", b"mesh river\n", b"index stage\n", b"3988\t720\n\xff\xfe\n"],
    ids=["empty", "blank-line", "no-numeric-token", "column-header-only", "not-utf8"],
)
def test_unreadable_header_is_silently_no_result(
    tmp_path: pathlib.Path, payload: bytes
) -> None:
    """空文件 / 首行无 minute-time token / 非 UTF-8 字节：不抛错、不记观测值。"""
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, payload)
    tracker.capture_available()

    assert dict(tracker.captured) == {}
    assert tracker.observed_header_minutes == ()


@pytest.mark.parametrize("token", ["nan", "inf", "-inf"])
def test_non_finite_header_minute_is_silently_no_result(
    tmp_path: pathlib.Path, token: str
) -> None:
    """§C 步骤 1b：裸 `float()` 认 `nan`/`inf`，`round()` 随即抛错。

    去掉非有限判定后本条以**外泄的 `ValueError`/`OverflowError`** 变红（不是断言失败）：
    `round(nan)` 抛 `ValueError`、`round(inf)` 抛 `OverflowError`，两者都会穿透
    `capture_available()`。记进观测轨迹同样有害——`nan != nan` 让相邻去重永不生效。
    """
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, f"3988\t{token}\n".encode())
    tracker.capture_available()

    assert dict(tracker.captured) == {}
    assert tracker.observed_header_minutes == ()


def _make_symlinked_source(run_dir: pathlib.Path) -> CheckpointTracker:
    run_dir.mkdir()
    real = run_dir / "real.cfg.ic"
    real.write_bytes(_payload("720.000000"))
    tracker = _tracker(run_dir)
    tracker.source_path.symlink_to(real)
    return tracker


def _make_directory_source(run_dir: pathlib.Path) -> CheckpointTracker:
    run_dir.mkdir()
    tracker = _tracker(run_dir)
    tracker.source_path.mkdir()
    return tracker


def _make_absent_run_dir(run_dir: pathlib.Path) -> CheckpointTracker:
    """`run_dir` 整个不存在——SHUD 尚未建目录时的真实状态。"""
    return _tracker(run_dir)


@pytest.mark.parametrize(
    "make_tracker",
    [_make_symlinked_source, _make_directory_source, _make_absent_run_dir],
    ids=["symlinked-source", "directory-source", "absent-run-dir"],
)
def test_hostile_source_shapes_never_leak_an_exception(
    tmp_path: pathlib.Path, make_tracker
) -> None:
    """`safe_fs` 真会抛的三条路径都收敛成「本次观测无结果」（偏离 8）。"""
    tracker = make_tracker(tmp_path / "run")
    tracker.capture_available()

    assert dict(tracker.captured) == {}
    assert tracker.observed_header_minutes == ()


# --- G6 成功路径的输入归一化 ---


def test_rounded_header_minute_still_hits_the_target(tmp_path: pathlib.Path) -> None:
    """`round()` 的 oracle：SHUD 写出的 header 不保证是整数。"""
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _payload("719.600000"))
    tracker.capture_available()

    assert tracker.missing_hours() == ()
    # 记录的是**目标值**，不是观测到的 719.6。
    assert tracker.captured[12].relative_minute == 720.0
    assert tracker.observed_header_minutes == (719.6,)


def test_repeated_identical_observations_are_deduplicated(
    tmp_path: pathlib.Path,
) -> None:
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _payload("360.000000"))
    tracker.capture_available()
    tracker.capture_available()

    assert tracker.observed_header_minutes == (360.0,)


def test_only_adjacent_observations_are_deduplicated(tmp_path: pathlib.Path) -> None:
    """改成 `set` / 全局去重后变红：回退轨迹是漏采诊断的现场证据。"""
    tracker = _tracker(tmp_path)
    for minute in ("360.000000", "720.000000", "360.000000"):
        _write(tracker.source_path, _payload(minute))
        tracker.capture_available()

    assert tracker.observed_header_minutes == (360.0, 720.0, 360.0)


def test_checkpoint_filename_zero_pads_the_lead_hour(tmp_path: pathlib.Path) -> None:
    """`f"{hour:03d}"` 的 oracle：改成 `{hour}` 后文件名变成 `f5`。"""
    tracker = _tracker(tmp_path, checkpoint_hours=(5,))
    _write(tracker.source_path, _payload("300.000000"))
    tracker.capture_available()

    assert tracker.captured[5].path.name == f"{PROJECT}.f005.cfg.ic.update"
    assert tracker.captured[5].path.is_file()


# --- G7 结构、只读性与模块自述 ---


def test_captured_view_is_not_writable(tmp_path: pathlib.Path) -> None:
    tracker = _tracker(tmp_path)
    with pytest.raises(TypeError):
        tracker.captured[12] = None  # type: ignore[index]


def test_missing_hours_is_ascending_regardless_of_argument_order(
    tmp_path: pathlib.Path,
) -> None:
    tracker = _tracker(tmp_path, checkpoint_hours=(24, 12))
    assert tracker.missing_hours() == (12, 24)


def test_observed_paths_are_derived_from_the_explicit_arguments(
    tmp_path: pathlib.Path,
) -> None:
    tracker = _tracker(tmp_path)
    assert isinstance(tracker.source_path, pathlib.Path)
    assert tracker.source_path == tmp_path / f"{PROJECT}.cfg.ic.update"
    assert tracker.checkpoint_dir == tmp_path / "state_checkpoints"


def test_module_documents_the_deliberate_deviations() -> None:
    """偏离清单自称是全集，所以条数本身是可机检的声明，不是修辞。"""
    doc = tracker_module.__doc__
    assert doc is not None
    for ordinal in (
        "\n1. ",
        "\n2. ",
        "\n3. ",
        "\n4. ",
        "\n5. ",
        "\n6. ",
        "\n7. ",
        "\n8. ",
    ):
        assert ordinal in doc, ordinal
    assert "\n9. " not in doc
    # §D 要求的 pin 原语 → `safe_fs` 映射表。
    for helper in (
        "ensure_directory_no_follow",
        "read_bytes_limited_no_follow",
        "atomic_write_bytes_no_follow",
        "unlink_no_follow",
    ):
        assert helper in doc, helper


def test_module_contains_no_polling_loop() -> None:
    """零轮询循环（偏离 3）：观测由调用方驱动。

    整文件裸扫（含注释与 docstring），与 DB-free 禁区词的扫描口径一致——模块自述里因此
    刻意把偏离 3 写成「无 `sleep` 等待」，不写出这个字面量。
    """
    source = pathlib.Path(tracker_module.__file__).read_text(encoding="utf-8")
    assert "time.sleep" not in source
    assert "import time" not in source
