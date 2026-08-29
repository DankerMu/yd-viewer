# NWM@8ae9b8f2 tests/test_shud_runtime.py
r"""`yd_producer.tracker.checkpoint_tracker` 的行为测试（任务 9.1）。

重放纪律：SHUD 的就地覆写用「按序覆写 `source_path` + 按序调用 `capture_available()`」
确定性重放，不起真进程、不 sleep——本模块本来就不含轮询循环。

oracle 纪律：合成 `cfg.ic` 一律由 `cfg_ic_fixtures.build_cfg_ic` 生成，header 分钟由入参
给定，期望副本字节即「写进源文件的那一版字节」本身；`state.parse` 只作为既有面被当作
副本校验的 oracle 使用，不在此重测。

判别力纪律：正常捕获路径上，「相等判据放宽」「去掉 `round()`」「去掉已捕获跳过」「去掉
相邻去重」「补零改掉」「校验内存副本而非回读字节」六种改坏各自都有专属用例接住，见各
用例的 docstring。

结账表（fixture §G9 类 A 的产物；**#17 落补跑半时 MUST 按同一格式续表**）
=======================================================================

不变式：捕获阶段的每一个复合守卫的**每一个操作数**、每一个异常元组的**每一个成员**、
每一个 `is None` 身份判定、每一个传给 `safe_fs` 的关键字实参、每一个共享读取器的解析
维度，**要么有一个「改坏即变红」的见证，要么有一条书面等价理由**。「这一段有测试」不算
数；「这个操作数有测试」才算。下表每一格都由一个变异体实测过，不是推断。

**计数口径（Phase 6.2 审计第 2 条：同一张表不得读出两个总数）**：本表按**行**结账，共
**35 行 = 29 见证 + 5 等价 + 1 DEFER**。行数**不等于**单元数：轴 4 的三行按 `safe_fs` 的
**关键字实参名**分组登记，三行分别覆盖 6 / 3 / 1 个**调用点单元**（共 10 个），其余 32 行
一行一单元；故逐调用点口径下总数为 **42 单元**（32 + 10）。引用本表数目时 MUST 写明取的是
哪个口径。分轴：轴 1 = 8 行 / 8 单元、轴 2 = 6 / 6、轴 3 = 3 / 3、轴 4 = 3 行 / 10 单元、
轴 5 = 5 / 5、轴 6 = 10 / 10。

轴 1｜异常元组成员 × except 站点（8 行：8 见证）

- `OSError` @ `_capture`               -> `test_source_unlinked_between_the_two_reads_never_leaks`（异常外泄 `FileNotFoundError`）
- `SafeFilesystemError` @ `_capture`   -> `test_filesystem_failure_after_the_match_never_leaks`（异常外泄）
- `OSError` @ `_discard`               -> `test_discard_failure_never_leaks[oserror-half]`（异常外泄 `PermissionError`）
- `SafeFilesystemError` @ `_discard`   -> `test_discard_failure_never_leaks[safe-fs-half]`（异常外泄）
- `OSError` @ `_read_header_minute`    -> `test_missing_source_is_silently_no_result`、`test_hostile_source_shapes_never_leak_an_exception[absent-run-dir]`
- `SafeFilesystemError` @ `_read_header_minute` -> `test_hostile_source_shapes_never_leak_an_exception[symlinked-source]`、`[directory-source]`
- `_copy_is_intact` 的 `except ValueError` -> `test_torn_body_is_not_a_capture_and_leaves_no_copy` 等 5 条
- `_header_minute_of` 的 `except UnicodeDecodeError` -> `test_unreadable_header_is_silently_no_result[not-utf8]`

轴 2｜布尔操作数（**逐操作数**独立；捕获阶段一侧，构造期一侧见轴 6）（6 行：6 见证）

- `_copy_is_intact`：`header_minute is None` 析取 -> `test_copy_with_unreadable_header_is_not_captured[*]`（异常外泄 `TypeError`）
- `_copy_is_intact`：`not _header_minute_matches_checkpoint(...)` 合取 -> `test_source_advancing_between_the_two_reads_is_not_captured`
- `_header_minute_of`：`minute is None` 析取 -> `test_unreadable_header_is_silently_no_result[no-numeric-token]` 等
- `_header_minute_of`：`not math.isfinite(minute)` 析取 -> `test_non_finite_header_minute_is_silently_no_result[*]`
- `capture_available`：`not self._observed_header_minutes` 析取 -> 首次观测即 `IndexError`，`test_capture_over_an_overwrite_sequence` 等 23 条
- `capture_available`：`[-1] != header_minute` 析取 -> `test_repeated_identical_observations_are_deduplicated`

轴 3｜`is None` -> 真值判定（falsy-zero）（3 行：2 见证 + 1 等价）

- `capture_available` 的 header 判定 -> `test_zero_header_minute_is_a_real_observation`
- `_header_minute_of` 的返回判定 -> `test_zero_header_minute_is_a_real_observation`（同一条见证覆盖两处）
- `_copy_is_intact` 的 header 判定 -> **等价**（实测变异存活）。依据：构造期 `hour > 0` 的拒绝
  使目标分钟恒 ≥ 60，而 `_header_minute_matches_checkpoint` 是精确相等，故 header 为 `0.0`
  时两种写法都返回 `False`；`0.0` 之外的假值在 `float` 域内不存在（`nan` 已在
  `_header_minute_of` 出口拦掉）。

轴 4｜`safe_fs` 关键字实参（3 行 / 10 调用点单元：3 行全为等价，本轴零见证）

- `containment_root` ×6 站点 -> **等价**（§G9 已结账 + 本轮六处同时删除实测存活）。依据：
  目标路径全部由 `self._run_dir` 自构造，越界形态在声明域内不可达。
- `max_bytes` ×3 调用点（源读 / 回读 / header 读）-> 三者**各自单独**放开均为**等价**
  （§G9 已结账，本轮重跑 `a4-maxbytes-srcread-only` / `-copyread-only` / `-headerread-only`
  三个单站点变异体各 ×100，全绿）。依据：只有资源放大，无契约可见差异。
  **本行不携任何见证，且轴 4 三处同时放开仍是绿的**（Phase 6.2 审计实测，
  本轮复现）。`test_oversize_source_is_not_captured`（§G8 第三条）变红靠的是**另一组**
  三处上限：源读、回读，加上 `state.parse` **自带**的 `len(data) > max_bytes` 判定
  （`state/cfg_ic.py:313` 的 `IC file exceeds size limit of …`；fixture 的 Phase 6.2
  审计结论把它记成 `:166`，实测在 master 与本分支上都是 `:313`）。那一条是 `parse` 的内部
  判定、**不是传给 `safe_fs` 的关键字实参，因而不属轴 4 单元**，不在本表登记（它的
  结账在 §G8 第三条）。把它写成本行的见证，就是 Phase 6.2 审计判为「记账为假」
  的那一条（cand-15「转述即核实」同形）。
- `missing_ok=True` -> **等价**（§G9 已结账 + 本轮实测存活）。依据：`missing_ok=False` 产生的
  `FileNotFoundError` 是 `OSError`，被 `_discard` 自己的 `except` 吞掉。

轴 5｜共享读取器的解析维度（5 行：3 见证 + 1 等价 + 1 DEFER）

- 分词 `split()` -> `test_capture_succeeds_on_a_tab_delimited_payload`（真实 native `cfg.ic`
  是 Tab 分隔，见 `cfg_ic_fixtures` 模块头）
- 行选择 `lines[0]` -> **DEFER**（Known limits cand-14，归 M4：与 `cfg_ic.parse` 的「首个非空
  行」定义分歧，本实现与 pin 逐字一致）
- `splitlines()` -> **等价（限声明域）**（实测变异存活）。**声明域**：源文件由 SHUD 在
  Linux 上写出，行尾为 `\n`（单独的 `\r` 行尾不在域内）。域内，改成 `split("\n")`
  后唯一的差异是首行内出现 `\x85` / `\u2028` 一类 `splitlines()` 认、`split("\n")` 不认的
  分隔符，而这些字符对 `str.split()` 同样是空白，`cfg_ic_header_minute_time` 取「最后一个
  数值 token」的结果不变；空输入两种写法都收敛到 `None`。**域外不成立**（Phase 6.2
  审计 Note）：行尾是单独 `\r`（CR-only）时，`split("\n")` 会把整份文件并成一「行」，
  取到的是**全文最后一个数值 token** 而不是 header 的那个，两种写法分岔。若 M4 真跑
  发现真实 `cfg.ic.update` 的行尾不是 `\n`，本格 MUST 重新裁决。
- `if not lines` 守卫 -> `test_unreadable_header_is_silently_no_result[empty]`（异常外泄 `IndexError`）
- `decode("utf-8")` -> 同轴 1 的 `UnicodeDecodeError` 格（改 `errors="ignore"` 后变红）

轴 6｜`__init__` 构造期守卫（10 行：10 见证；Phase 6.2 审计第 3 条新纳入）

**范围缺口如实登记**：§G9 原「范围」是 `_capture` / `_copy_is_intact` / `_discard` /
`capture_available` 四个函数加两个共享读取器，**不含 `__init__`**。审计者顺手探到
`"/" in project_name or "\\" in project_name` 的第二个操作数无见证（删掉后全量套件纯绿），
根因是**范围本身**漏了构造期——这是本轮第三个同形复发点，不是一次孤立疏漏。范围据此
扩到 `__init__`，其十个操作数逐一结账如下（下列用例删掉对应守卫/操作数后各自变红）。

- `not hours` -> `test_construction_rejects_unusable_checkpoint_hours[hours0-为空]`
- `hour <= 0` -> 同上 `[hours1-非正]`（`(0,)`）与 `[hours2-非正]`（`(-12,)`）
- `len(set(hours)) != len(hours)` -> 同上 `[hours3-重复]`
- `not project_name` -> `test_construction_rejects_unusable_project_name[]`（空串）
- `"/" in project_name` 析取 -> 同上 `[a/b]`
- `"\\" in project_name` 析取（源码写作 `"\\"`，即单个反斜杠）-> 同上 `[a\\b]`
  （pytest id 对反斜杠做转义显示）。**Phase 6.2 审计第 3 条补入**：本格原先无见证。
  POSIX 下反斜杠不是路径分隔符，该操作数是对 Windows 的防御性收窄，用户可见影响低；
  纪律是逐操作数结账，不按影响打折
- `project_name in {".", ".."}` 的 `"."` 成员 -> 同上 `[.]`
- `project_name in {".", ".."}` 的 `".."` 成员 -> 同上 `[..]`
- `"\0" in project_name` -> `test_construction_rejects_nul_bytes_in_paths[project-name-nul]`
  （以 `ValueError` 从 `capture_available()` **外泄**的形态变红，不是断言失败）
- `"\0" in str(run_dir)` -> 同上 `[run-dir-nul]`（同形态）
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import pathlib

import pytest
from cfg_ic_fixtures import build_cfg_ic

from yd_producer import state
from yd_producer.store import safe_fs
from yd_producer.tracker import CapturedCheckpoint, CheckpointTracker, TrackerError
from yd_producer.tracker import checkpoint_tracker as tracker_module

PROJECT = "yd_gfs"
HOURS = (12,)

#: 生成一份 > `MAX_STATE_IC_BYTES`（64 MiB）的**结构合法**源文件所需的 mesh 规模。
#: 实测 ~73 MB / 0.7 秒 / RSS 峰值约 580 MB，与 fixture 记录的成本一致。
OVERSIZE_MESH_COUNT = 1_400_000


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


@pytest.mark.parametrize("project_name", ["", "a/b", "a\\b", ".", ".."])
def test_construction_rejects_unusable_project_name(
    tmp_path: pathlib.Path, project_name: str
) -> None:
    r"""构造期 fail closed 的名字一侧，**逐操作数**各一例（结账表轴 6）。

    `"a\\b"` 是 Phase 6.2 审计第 3 条补入的：`"/" in project_name or "\\" in project_name`
    的第二个操作数原先无见证——删掉它全量套件纯绿。POSIX 下反斜杠不是路径分隔符，该操作数
    是对 Windows 的防御性收窄，用户可见影响低；纪律是逐操作数结账，不按影响打折。
    """
    with pytest.raises(TrackerError):
        _tracker(tmp_path, project_name=project_name)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"project_name": "yd\x00gfs"}, "project_name"),
        ({"run_dir_suffix": "sub\x00dir"}, "run_dir"),
    ],
    ids=["project-name-nul", "run-dir-nul"],
)
def test_construction_rejects_nul_bytes_in_paths(
    tmp_path: pathlib.Path, kwargs: dict[str, str], reason: str
) -> None:
    """NUL 走的是 `ValueError` 而不是 `OSError`，`safe_fs` 也不转译它。

    不在构造期拦下，它就会在**观测期**从 `capture_available()` 外泄（`stat`/`open` 各一条
    路径），违偏离 8「唯一对外异常」；而 TOML 的基本字符串接受 `\u0000`，它可从配置到达。
    用例形态是刻意的：拒绝**必须发生在构造期**（`except` 分支只接构造那一句），而守卫缺席
    时构造会通过，于是控制流走到观测那一行——`ValueError` 在那里外泄，本条以**异常外泄**
    而不是「DID NOT RAISE」的断言失败变红，正是契约要钉的形态。
    """
    run_dir = tmp_path
    if "run_dir_suffix" in kwargs:
        run_dir = tmp_path / kwargs["run_dir_suffix"]
    try:
        tracker = _tracker(run_dir, project_name=kwargs.get("project_name", PROJECT))
    except TrackerError as error:
        assert reason in str(error)
        return
    tracker.capture_available()
    pytest.fail(f"构造期未拒绝含 NUL 的 {reason}")


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


# --- G8 捕获阶段的实现级 MUST ---


def test_source_advancing_between_the_two_reads_is_not_captured(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """钉死副本 header 复检（`_copy_is_intact` 的 header 合取）。

    观测已按 header `720` 命中，但在 `_capture` 重读源文件之前 SHUD 又覆写了一版**完全
    合法**的 `1440`。删掉 header 合取后，`f012` 会逐字节持有 1440 的 body 且
    `missing_hours()` 报空——正是 spec 禁止的「以更晚时刻的版本冒充 T+12」。既有的回读
    用例代替不了它：那条落盘的是**截断**内容，`state.parse` 先抛错，header 合取从来不是
    判别项。
    """
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _payload("720.000000"))
    payload_1440 = _payload("1440.000000")
    # 前置断言：这一版是完全合法的，失败不能由结构校验代劳。
    state.parse(payload_1440)
    real_ensure = safe_fs.ensure_directory_no_follow

    def advancing_ensure(path, **kwargs):  # type: ignore[no-untyped-def]
        # `_capture` 的第一步；在它重读源文件之前插入下一次就地覆写。
        _write(tracker.source_path, payload_1440)
        return real_ensure(path, **kwargs)

    monkeypatch.setattr(safe_fs, "ensure_directory_no_follow", advancing_ensure)
    tracker.capture_available()

    assert tracker.missing_hours() == (12,)
    assert dict(tracker.captured) == {}
    assert not (tracker.checkpoint_dir / f"{PROJECT}.f012.cfg.ic.update").exists()


def test_filesystem_failure_after_the_match_never_leaks(tmp_path: pathlib.Path) -> None:
    """捕获段的异常收敛：header 已命中之后的 FS 失败同样不得外泄。

    G5 的三条敌意形态全在 `_read_header_minute` 里就返回了，没有一条进入 `_capture`；这里
    用「`run_dir` 下有个名为 `state_checkpoints` 的**普通文件**占位」把失败推到命中之后。
    把 `_capture` 的 `except _FS_FAILURES` 收窄后，本条以外泄的 `SafeFilesystemError` 变红。
    """
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _payload("720.000000"))
    placeholder = _write(tracker.checkpoint_dir, b"not a directory\n")

    tracker.capture_available()

    assert tracker.missing_hours() == (12,)
    assert dict(tracker.captured) == {}
    assert tracker.checkpoint_dir.is_file()
    assert tracker.checkpoint_dir.read_bytes() == placeholder


def test_oversize_source_is_not_captured(tmp_path: pathlib.Path) -> None:
    """超限源文件走「校验失败」支：删副本、保持未捕获（§D 超限处置）。

    有界读在超限时返回 `max_bytes + 1` 字节，`state.parse` 对超限有**显式**判定并抛
    `ValueError`——不是靠截断后的内容碰巧解析失败。源读、回读、`state.parse` 三处上限
    各自放开都是语义等价（只有资源放大），故只有三处同时放开才会让本条变红。
    """
    tracker = _tracker(tmp_path)
    payload = build_cfg_ic(
        mesh_count=OVERSIZE_MESH_COUNT, river_count=2, minute="720.000000"
    ).payload
    assert len(payload) > state.MAX_STATE_IC_BYTES
    # 结构合法性由**同一生成器的同形小样本**作证：对 64 MiB 那份直接 parse 只是重复同一条
    # 判定，代价却是再翻一倍的内存峰值。
    state.parse(_payload("720.000000"))
    _write(tracker.source_path, payload)

    tracker.capture_available()

    # header 本身读到了（有界读的首行完好），拒绝发生在捕获校验而不是观测步骤。
    assert tracker.observed_header_minutes == (720.0,)
    assert tracker.missing_hours() == (12,)
    assert dict(tracker.captured) == {}
    assert not (tracker.checkpoint_dir / f"{PROJECT}.f012.cfg.ic.update").exists()


def test_copy_with_unreadable_header_is_not_captured(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, copy_bytes: bytes
) -> None:
    """钉死 `_copy_is_intact` 的 `header_minute is None` 析取（轴 2）。

    删掉该析取后，回读副本的 header 不可读时会把 `None` 喂进 `round()`，抛 `TypeError`
    并**外泄**——`_copy_is_intact` 在 `_capture` 的 `try` **之外**，没有任何东西接住它。
    三种域内形态：副本落成零字节 / 非 UTF-8 / 非有限 header。
    """
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _payload("720.000000"))
    real_write = safe_fs.atomic_write_bytes_no_follow

    def landing_write(path, content, **kwargs):  # type: ignore[no-untyped-def]
        return real_write(path, copy_bytes, **kwargs)

    monkeypatch.setattr(safe_fs, "atomic_write_bytes_no_follow", landing_write)
    tracker.capture_available()

    assert tracker.missing_hours() == (12,)
    assert dict(tracker.captured) == {}
    assert not (tracker.checkpoint_dir / f"{PROJECT}.f012.cfg.ic.update").exists()


test_copy_with_unreadable_header_is_not_captured = pytest.mark.parametrize(
    "copy_bytes",
    [b"", b"\xff\xfe\n", b"3988\tnan\n"],
    ids=["empty-copy", "not-utf8-copy", "non-finite-copy"],
)(test_copy_with_unreadable_header_is_not_captured)


def test_source_unlinked_between_the_two_reads_never_leaks(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_FS_FAILURES` 的 **`OSError` 半**在 `_capture` 站点的见证（轴 1）。

    只注入**时机**、不注入错误：SHUD 的 rename/unlink-in-place 让源文件在观测读与捕获读
    之间消失，`safe_fs` 抛的是朴素 `FileNotFoundError`（`OSError`），不是
    `SafeFilesystemError`。把本站点收窄成只接 `SafeFilesystemError` 后，本条以**异常外泄**
    变红。
    """
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _payload("720.000000"))
    real_ensure = safe_fs.ensure_directory_no_follow

    def unlinking_ensure(path, **kwargs):  # type: ignore[no-untyped-def]
        tracker.source_path.unlink()
        return real_ensure(path, **kwargs)

    monkeypatch.setattr(safe_fs, "ensure_directory_no_follow", unlinking_ensure)
    tracker.capture_available()

    assert tracker.missing_hours() == (12,)
    assert dict(tracker.captured) == {}
    assert not (tracker.checkpoint_dir / f"{PROJECT}.f012.cfg.ic.update").exists()


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(13, "Permission denied"),
        safe_fs.SafeFilesystemError("Target file must not be a symlink"),
    ],
    ids=["oserror-half", "safe-fs-half"],
)
def test_discard_failure_never_leaks(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """`_FS_FAILURES` **两个成员**在 `_discard` 站点各自的见证（轴 1）。

    副本校验失败后删不掉副本（只读挂载 / 目录权限被收走 → 朴素 `PermissionError`；落点被
    换成符号链接 → `SafeFilesystemError`）：观测仍 MUST NOT 抛，该小时保持未捕获。两半各
    一个参数，是为了不让本站点的两格由**同一个**用例产出——那正是被替换掉的采样签名。
    残留的未验证副本是 Known limits cand-08 的形态——API 仍诚实（`captured` 为空），下游
    只信 `captured` 记录。
    """
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _truncated_payload("720.000000"))

    def refusing_unlink(path, **kwargs):  # type: ignore[no-untyped-def]
        raise error

    monkeypatch.setattr(safe_fs, "unlink_no_follow", refusing_unlink)
    tracker.capture_available()

    assert tracker.missing_hours() == (12,)
    assert dict(tracker.captured) == {}
    # 删除失败 ⇒ 未验证副本留在规范文件名上（cand-08，归 #17）。
    assert (tracker.checkpoint_dir / f"{PROJECT}.f012.cfg.ic.update").is_file()


def test_capture_succeeds_on_a_tab_delimited_payload(tmp_path: pathlib.Path) -> None:
    """轴 5「分词」的**正向**见证：真实 native `cfg.ic` 是 Tab 分隔的。

    套里其余含 tab 的载荷全在「无结果」用例里，`split()` → `split(' ')` 变异下它们**因
    错误理由**通过；只有一条 tab 驱动的成功捕获能把全空白分词钉住。依据是本仓自己的
    `cfg_ic_fixtures` 模块头（真实 `.cfg.ic.update` 为 Tab 分隔），不是 M4 假设。
    """
    tracker = _tracker(tmp_path)
    payload = build_cfg_ic(
        mesh_count=3, river_count=2, minute="720.000000", delimiter="\t"
    ).payload
    assert b"\t" in payload
    _write(tracker.source_path, payload)

    tracker.capture_available()

    assert tracker.missing_hours() == ()
    assert tracker.observed_header_minutes == (720.0,)
    assert tracker.captured[12].path.read_bytes() == payload


def test_zero_header_minute_is_a_real_observation(tmp_path: pathlib.Path) -> None:
    """轴 3：`is None` MUST NOT 退化成真值判定（经典 falsy-zero）。

    模型相对时间 0 分钟（初态）是**可读到的真实 header**，它必须进观测轨迹。捕获侧的影响
    有界——构造期 `hour > 0` 使 `0.0` 永不命中任何目标，故错在这里不会丢捕获，只会让漏采
    诊断的现场证据缺一条。
    """
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _payload("0.000000"))

    tracker.capture_available()

    assert tracker.observed_header_minutes == (0.0,)
    assert tracker.missing_hours() == (12,)
    assert dict(tracker.captured) == {}


# --- G7 结构、只读性与模块自述 ---


def test_captured_view_is_not_writable(tmp_path: pathlib.Path) -> None:
    tracker = _tracker(tmp_path)
    with pytest.raises(TypeError):
        tracker.captured[12] = None  # type: ignore[index]


def test_constructor_takes_only_keyword_arguments() -> None:
    """§B「三者均 keyword-only」：去掉 `__init__` 的 `*` 后变红。"""
    params = inspect.signature(CheckpointTracker.__init__).parameters
    for name in ("run_dir", "project_name", "checkpoint_hours"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name


def test_captured_record_is_frozen(tmp_path: pathlib.Path) -> None:
    """`MappingProxyType` 只挡 `__setitem__`，挡不住成员改写——那一半由 `frozen=True` 守。"""
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _payload("720.000000"))
    tracker.capture_available()
    with pytest.raises(dataclasses.FrozenInstanceError):
        tracker.captured[12].lead_hours = 24  # type: ignore[misc]


def test_captured_record_rejects_positional_construction(
    tmp_path: pathlib.Path,
) -> None:
    """`kw_only=True`：五个字段里有三个是字符串/路径，位置构造错序不会有任何提示。"""
    with pytest.raises(TypeError):
        CapturedCheckpoint(12, 720.0, tmp_path / "x", "s", "c")  # type: ignore[misc]


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
