r"""`yd_producer.publish` 的行为测试（NFS 提交顺序与 `DONE` 语义，任务 13.1）。

oracle 纪律：期望值都是**手算**或由构造侧登记的——DAT 的合法大小由
`dat_fixtures.expected_v2_size` 独立算出（该模块不 import `yd_producer`），状态的绝对分钟
由 `frontier_fixtures.absolute_minute` 独立算出，删除集合由构造时写下的路径列出，都不由
被测模块回读。

承重条不是「happy path 全绿」。真正判别的是：
- 五个终名的相对序**逐对**钉死（合成一条「序列相等」断言在只错一处时给不出定位）；
- 每一条失败用例都断言 `YD_ROOT` 的**递归快照逐项相等**，而不是只看「`DONE` 不存在」——
  后者放行「DAT 已 rename 但没写 `DONE`」这种真实缺陷；
- 行数三向（少一行 / 多一行 / 半行尾巴）+ 恰好一条正例作反向配重，否则「一律拒绝」这种
  恒假实现也能全绿；
- v2 判据用 v1 布局做判别器（`nc == reach_count` 时 v1 的前 8 字节正是该值的 float64
  表示，「文件够大」与「nc 恰好相等」两种退化判据都会放行它）；
- 旧状态删除的两处边界（`<` 而不是 `<=`；解析后 cycle 比较而不是文件名字符串比较）；
- 权限断言在 `os.umask(0o077)` 下**重跑一次**——默认 umask 0o022 下 `0o666 & ~umask`
  恰好等于 0o644、`mkdir(0o755)` 恰好等于 0o755，两种写法在那里不可分辨。

全部测试树用 `tmp_path.resolve()`：`safe_fs._open_directory_no_follow` 会把
`containment_root` 自身的每个祖先分量重新过一遍 `O_NOFOLLOW`（`safe_fs.py:824-843`），而
macOS 的 `/var` 是 symlink，未 resolve 的 `tmp_path` 会得到与被测逻辑无关的红。
"""

from __future__ import annotations

import os
import pathlib
import stat
import struct
import tracemalloc
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from cfg_ic_fixtures import build_cfg_ic, build_cfg_ic_rows
from dat_fixtures import (
    FIXED_HEADER_BYTES,
    FLOAT64_BYTES,
    TEXT_HEADER_BYTES,
    build_dat_bytes,
    build_text_header,
    expected_v2_size,
    write_sparse_dat,
)
from frontier_fixtures import (
    YdRootBuilder,
    absolute_minute,
    parse_cycle,
    snapshot_tree,
)

from yd_producer import controller, publish, residue
from yd_producer.state import (
    MAX_STATE_IC_BYTES,
    cfg_ic_header_minute_time,
    parse,
)
from yd_producer.store import safe_fs
from yd_producer.store.safe_fs import SafeFilesystemError

Path = pathlib.Path

#: 锚点 cycle（手算：2026-08-26 12Z）。
T_MINUS_24 = "2026082512"
T_MINUS_12 = "2026082600"
T_TEXT = "2026082612"
T_PLUS_12 = "2026082700"
T_PLUS_24 = "2026082712"

SOURCE = "ifs"
#: 小规模 reach 数：布局判定与规模无关，168×3988 只会让每个用例慢。
REACH_COUNT = 8
#: `config.forecast_days * 24`。
EXPECTED_ROWS = 168
#: tracker 捕获时 checkpoint 的**相对**分钟（T+12 = 720 分钟）。
RELATIVE_MINUTE = "720.000000"


def _absolute_minute_text(cycle_text: str) -> str:
    return f"{absolute_minute(parse_cycle(cycle_text))}.000000"


@dataclass
class Scene:
    """一棵合成的 `YD_ROOT` + 独立 scratch 根，及其构造期望值。"""

    root: Path
    scratch: Path
    work_root: Path
    work_dir: Path
    dat: Path
    checkpoint: Path
    log: Path
    yd_root_arg: Path

    def make_inputs(self, **overrides: object) -> publish.PublishInputs:
        kwargs: dict[str, object] = {
            "yd_root": self.yd_root_arg,
            "source": SOURCE,
            "cycle": parse_cycle(T_TEXT),
            "scratch_dat": self.dat,
            "scratch_checkpoint": self.checkpoint,
            "merged_log": self.log,
            "work_dir": self.work_dir,
            "work_root": self.work_root,
            "expected_rows": EXPECTED_ROWS,
            "reach_count": REACH_COUNT,
            "variant_reach_count": REACH_COUNT,
        }
        kwargs.update(overrides)
        return publish.PublishInputs(**kwargs)  # type: ignore[arg-type]


def build_scene(
    tmp_path: Path,
    *,
    dat_payload: bytes | None = None,
    checkpoint_payload: bytes | None = None,
    log_bytes: bytes | None = b"job 4242 stdout\n",
    old_state_cycles: tuple[str, ...] = (),
    scratch_mode: int | None = None,
    create_output_root: bool = True,
    yd_root_via_symlink: bool = False,
) -> Scene:
    """构造 `YD_ROOT`（含 `states/<source>/<T>.cfg.ic`）与 scratch 侧的一轮产出。"""
    base = tmp_path.resolve()
    root = base / "yd"
    builder = YdRootBuilder(root)
    builder.write_state(T_TEXT, SOURCE)
    for cycle_text in old_state_cycles:
        builder.write_state(cycle_text, SOURCE)
    if create_output_root:
        (root / "output").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    scratch = base / "scratch"
    work_root = scratch / "work"
    work_dir = work_root / SOURCE / T_TEXT
    (work_dir / "canonical").mkdir(parents=True, exist_ok=True)
    (work_dir / "canonical" / "forcing.csv").write_text("x\n", encoding="utf-8")
    (work_dir / "raw-manifest.json").write_text("{}\n", encoding="utf-8")
    (work_root / SOURCE / T_MINUS_12).mkdir(parents=True, exist_ok=True)

    dat = scratch / "out" / "yd.rivqdown.dat"
    dat.parent.mkdir(parents=True, exist_ok=True)
    if dat_payload is None:
        dat_payload = build_dat_bytes(nc=REACH_COUNT, rows=EXPECTED_ROWS)
    dat.write_bytes(dat_payload)

    checkpoint = scratch / "out" / "checkpoint.cfg.ic"
    if checkpoint_payload is None:
        checkpoint_payload = build_cfg_ic(
            mesh_count=3, river_count=2, minute=RELATIVE_MINUTE
        ).payload
    checkpoint.write_bytes(checkpoint_payload)

    log = scratch / "out" / "merged.log"
    if log_bytes is not None:
        log.write_bytes(log_bytes)

    if scratch_mode is not None:
        dat.chmod(scratch_mode)
        checkpoint.chmod(scratch_mode)

    yd_root_arg = root
    if yd_root_via_symlink:
        link = base / "link"
        link.symlink_to(base, target_is_directory=True)
        yd_root_arg = link / "yd"

    return Scene(
        root=root,
        scratch=scratch,
        work_root=work_root,
        work_dir=work_dir,
        dat=dat,
        checkpoint=checkpoint,
        log=log,
        yd_root_arg=yd_root_arg,
    )


@pytest.fixture
def strict_umask() -> Iterator[None]:
    """`os.umask(0o077)`：默认 0o022 下 `fchmod` 与「裸 open(mode)」不可分辨。"""
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _assert_unchanged(
    before: dict[str, tuple[str, int, int, str, int]], scene: Scene
) -> None:
    """失败路径的共用断言：`YD_ROOT` 递归快照逐项相等，且 `work_dir` **仍存在**。

    快照维度是路径、类型、mode、大小、内容摘要、mtime。MUST NOT 退化成「`DONE` 不存在」
    单条断言——那放行「DAT 已 rename 但没写 DONE」这种真实缺陷；work 存活这一维同样不可省——
    失败侧回收归任务 13.2，发布器在 `DONE` 之前一律不碰 work。
    """
    assert snapshot_tree(scene.root) == before
    assert scene.work_dir.exists()


# --- 顺序录制（裁决 10：monkeypatch 模块内绑定的 `safe_fs` 名，零生产面） ---


def _install_recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    record: list[tuple[str, str]] = []
    real_atomic = publish.atomic_write_bytes_no_follow
    real_exclusive = publish.write_bytes_no_follow_exclusive
    real_unlink = publish.unlink_no_follow
    real_rmtree = publish.remove_tree_allow_symlinks

    def atomic(path, content, **kwargs):  # type: ignore[no-untyped-def]
        result = real_atomic(path, content, **kwargs)
        record.append(("rename", Path(path).name))
        return result

    def exclusive(path, content, **kwargs):  # type: ignore[no-untyped-def]
        result = real_exclusive(path, content, **kwargs)
        record.append(("create", Path(path).name))
        return result

    def unlink(path, **kwargs):  # type: ignore[no-untyped-def]
        result = real_unlink(path, **kwargs)
        record.append(("unlink", Path(path).name))
        return result

    def rmtree(parent, name, **kwargs):  # type: ignore[no-untyped-def]
        result = real_rmtree(parent, name, **kwargs)
        record.append(("rmtree", name))
        return result

    monkeypatch.setattr(publish, "atomic_write_bytes_no_follow", atomic)
    monkeypatch.setattr(publish, "write_bytes_no_follow_exclusive", exclusive)
    monkeypatch.setattr(publish, "unlink_no_follow", unlink)
    monkeypatch.setattr(publish, "remove_tree_allow_symlinks", rmtree)
    return record


def test_commit_order_is_observable_pairwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec Scenario「提交顺序可观测」：五个终名的相对序**逐对**成立。"""
    scene = build_scene(tmp_path, old_state_cycles=(T_MINUS_12,))
    record = _install_recorder(monkeypatch)

    result = publish.publish(scene.make_inputs())

    assert isinstance(result, publish.PublishResult)
    dat_at = record.index(("rename", "yd.rivqdown.dat"))
    state_at = record.index(("rename", f"{T_PLUS_12}.cfg.ic"))
    done_at = record.index(("create", "DONE"))
    unlink_at = record.index(("unlink", f"{T_MINUS_12}.cfg.ic"))
    work_at = record.index(("rmtree", T_TEXT))

    assert dat_at < state_at, record
    assert state_at < done_at, record
    assert done_at < unlink_at, record
    assert unlink_at < work_at, record


def test_successful_round_lands_five_terminal_names(tmp_path: Path) -> None:
    """合法一轮：五个终名落地，`states/` 只剩 T 与 T+12，work 不存在，三份产物 0o644。"""
    scene = build_scene(tmp_path, old_state_cycles=(T_MINUS_24, T_MINUS_12))

    result = publish.publish(scene.make_inputs())

    dat = scene.root / "output" / T_TEXT / SOURCE / "yd.rivqdown.dat"
    done = scene.root / "output" / T_TEXT / SOURCE / "DONE"
    state = scene.root / "states" / SOURCE / f"{T_PLUS_12}.cfg.ic"
    assert dat.read_bytes() == scene.dat.read_bytes()
    assert done.read_bytes() == b""
    assert state.exists()
    assert sorted(p.name for p in (scene.root / "states" / SOURCE).iterdir()) == [
        f"{T_TEXT}.cfg.ic",
        f"{T_PLUS_12}.cfg.ic",
    ]
    assert not scene.work_dir.exists()
    assert scene.work_dir.parent.exists()
    assert (scene.work_root / SOURCE / T_MINUS_12).exists()
    for path in (dat, state, done):
        assert _mode(path) == 0o644, path

    assert result.dat_path == dat
    assert result.done_path == done
    assert result.state_path == state
    assert result.removed_state_files == (
        scene.root / "states" / SOURCE / f"{T_MINUS_24}.cfg.ic",
        scene.root / "states" / SOURCE / f"{T_MINUS_12}.cfg.ic",
    )
    assert result.removed_work_dir == scene.work_dir
    assert result.next_cycle == parse_cycle(T_PLUS_12)


def test_checkpoint_is_restamped_to_absolute_next_cycle(tmp_path: Path) -> None:
    """spec Scenario「checkpoint 发布前定戳」：相对 720 分钟 -> 绝对 T+12。"""
    scene = build_scene(tmp_path)

    publish.publish(scene.make_inputs())

    state = scene.root / "states" / SOURCE / f"{T_PLUS_12}.cfg.ic"
    doc = parse(state)
    tokens = doc.lines[doc.header_index].split()
    assert tokens[-1] == _absolute_minute_text(T_PLUS_12)
    assert cfg_ic_header_minute_time(tokens) == absolute_minute(parse_cycle(T_PLUS_12))
    # scratch 原文件是失败路径要回收的证据：MUST NOT 被回写。
    scratch_doc = parse(scene.checkpoint)
    assert scratch_doc.lines[scratch_doc.header_index].split()[-1] == RELATIVE_MINUTE


def test_published_state_closes_the_frontier_chain(tmp_path: Path) -> None:
    """治理不变量的端到端判别器：写出去的状态正是下一轮前沿要读的那份。"""
    scene = build_scene(tmp_path)

    publish.publish(scene.make_inputs())

    decision = controller.decide_frontier(
        yd_root=scene.root, source=SOURCE, raw_complete=lambda _cycle: True
    )
    assert decision.stop_reason is None, decision.detail
    assert decision.cycle == parse_cycle(T_PLUS_12)


# --- 行数 / 列数 / v2 判据 ---


@pytest.mark.parametrize(
    ("label", "rows", "extra_bytes"),
    [
        ("少一行", EXPECTED_ROWS - 1, 0),
        ("多一行", EXPECTED_ROWS + 1, 0),
        ("半行尾巴", EXPECTED_ROWS, FLOAT64_BYTES),
    ],
)
def test_row_count_boundary_rejects(
    tmp_path: Path, label: str, rows: int, extra_bytes: int
) -> None:
    """行数三向：少一行 / 多一行 / 多半行都拒，且 `YD_ROOT` 递归快照逐项不变。"""
    scene = build_scene(
        tmp_path,
        dat_payload=build_dat_bytes(nc=REACH_COUNT, rows=rows, extra_bytes=extra_bytes),
    )
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="数据区字节数不符"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)
    assert not (scene.root / "output" / T_TEXT).exists()
    assert scene.work_dir.exists()


def test_exact_row_count_is_accepted(tmp_path: Path) -> None:
    """反向配重：恰好 `expected_rows` 行必须通过，否则「一律拒绝」也能全绿。"""
    scene = build_scene(tmp_path)
    payload = scene.dat.read_bytes()
    assert len(payload) == expected_v2_size(nc=REACH_COUNT, rows=EXPECTED_ROWS)

    publish.check_publish_contract(scene.make_inputs())


def test_reach_count_mismatch_rejects(tmp_path: Path) -> None:
    """spec Scenario「reach 数不符不写 DONE」：DAT 的 `nc` 少一列。"""
    scene = build_scene(
        tmp_path,
        dat_payload=build_dat_bytes(nc=REACH_COUNT - 1, rows=EXPECTED_ROWS),
    )
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="不等于 reach_count") as excinfo:
        publish.publish(scene.make_inputs())

    assert "变体" not in str(excinfo.value)
    _assert_unchanged(before, scene)


def test_variant_reach_count_mismatch_rejects(tmp_path: Path) -> None:
    """`reach_count` 与变体 reach 数不相等（DAT 本身与 `reach_count` 一致）。"""
    scene = build_scene(tmp_path)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="变体 reach 数") as excinfo:
        publish.publish(scene.make_inputs(variant_reach_count=REACH_COUNT + 1))

    assert "不等于 reach_count" not in str(excinfo.value)
    _assert_unchanged(before, scene)


def test_v1_layout_is_rejected_as_non_v2(tmp_path: Path) -> None:
    """v2 判据的唯一判别器：v1 布局（`nc` 在 offset 0，且 `nc == reach_count`）。"""
    payload = build_dat_bytes(nc=REACH_COUNT, rows=EXPECTED_ROWS, layout="v1")
    # 手算判别力：v1 的前 8 字节正是 `reach_count` 的 float64 表示，
    # 「文件够大」与「nc 恰好等于 reach_count」两种退化判据都会放行它。
    assert struct.unpack("<d", payload[:8])[0] == float(REACH_COUNT)
    assert len(payload) > FIXED_HEADER_BYTES
    scene = build_scene(tmp_path, dat_payload=payload)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="非 v2"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


def test_text_header_shape_rejects_non_nul_after_nul(tmp_path: Path) -> None:
    """`[0:1024)` 含 NUL 之后又出现非 NUL 字节 -> 拒。"""
    header = bytearray(build_text_header("SHUD v2"))
    header[900] = ord("X")
    scene = build_scene(
        tmp_path,
        dat_payload=build_dat_bytes(
            nc=REACH_COUNT, rows=EXPECTED_ROWS, header_bytes=bytes(header)
        ),
    )

    with pytest.raises(publish.PublishError, match="非 NUL 字节"):
        publish.check_publish_contract(scene.make_inputs())


def test_text_header_shape_rejects_non_printable(tmp_path: Path) -> None:
    """`[0:1024)` 的可打印前缀里出现控制字节 -> 拒。"""
    header = bytearray(build_text_header("SHUD v2"))
    header[3] = 0x01
    scene = build_scene(
        tmp_path,
        dat_payload=build_dat_bytes(
            nc=REACH_COUNT, rows=EXPECTED_ROWS, header_bytes=bytes(header)
        ),
    )

    with pytest.raises(publish.PublishError, match="非可打印"):
        publish.check_publish_contract(scene.make_inputs())


def test_text_header_all_nul_is_accepted(tmp_path: Path) -> None:
    """全 NUL（空描述）是 SHUD `char header[1024] = {}` 的合法形态 -> 接受。"""
    scene = build_scene(
        tmp_path,
        dat_payload=build_dat_bytes(
            nc=REACH_COUNT,
            rows=EXPECTED_ROWS,
            header_bytes=b"\x00" * TEXT_HEADER_BYTES,
        ),
    )

    publish.check_publish_contract(scene.make_inputs())


def test_column_id_table_must_be_complete(tmp_path: Path) -> None:
    """列编号表被截断（文件在表中间就结束）-> 拒。"""
    scene = build_scene(
        tmp_path,
        dat_payload=build_dat_bytes(nc=REACH_COUNT, rows=0, truncate_column_table=2),
    )

    with pytest.raises(publish.PublishError, match="列编号表不完整"):
        publish.check_publish_contract(scene.make_inputs())


# --- 期望值正数闸 ---


@pytest.mark.parametrize(
    ("field_name", "pattern"),
    [
        ("expected_rows", "expected_rows 必须为正"),
        ("reach_count", "reach_count 必须为正"),
        ("variant_reach_count", "variant_reach_count 必须为正"),
    ],
)
def test_positive_expectation_gate(
    tmp_path: Path, field_name: str, pattern: str
) -> None:
    """三个期望值任一非正 -> `PublishError`，且**先于**读文件。

    `expected_rows = 0` 那条刻意配一个数据区为空的 DAT：否则它会被行数判据顺带挡住，
    这条闸门就失去判别力。
    """
    scene = build_scene(tmp_path, dat_payload=build_dat_bytes(nc=REACH_COUNT, rows=0))
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match=pattern):
        publish.publish(scene.make_inputs(**{field_name: 0}))

    _assert_unchanged(before, scene)


# --- T+12 状态可读 ---


def test_incomplete_restamped_state_rejects(tmp_path: Path) -> None:
    """重戳后文档缺 river 段（`state_ic_structure_complete` 判不完整）-> 拒。"""
    scene = build_scene(
        tmp_path,
        checkpoint_payload=build_cfg_ic(
            mesh_count=3, river_count=0, minute=RELATIVE_MINUTE
        ).payload,
    )
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="结构不完整"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


def test_invalid_header_shape_converges_to_publish_error(tmp_path: Path) -> None:
    """2 token 的 header：`restamp_to_absolute_time` 的 `ValueError` MUST 被收敛。"""
    payload = build_cfg_ic_rows(
        mesh_rows=[["1", "0.1", "0.1", "0.1", "0.1", "0.1"]],
        river_rows=[["1", "0.1"]],
        header_tokens=["1", "6"],
    ).payload
    scene = build_scene(tmp_path, checkpoint_payload=payload)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="无法重戳"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


def test_restamped_absolute_time_predicate_has_teeth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重戳后 header 仍是相对分钟 -> 拒（变异体 (x) 的判别器）。

    判别构造：把 `restamp_to_absolute_time` 换成恒等映射，重戳「成功」但 header 仍是
    相对 720 分钟。删掉绝对时间校验的实现会照常写 `DONE`，把一份下一轮读不出 T+12 的状态
    发布出去。
    """
    scene = build_scene(tmp_path)
    monkeypatch.setattr(publish, "restamp_to_absolute_time", lambda doc, target: doc)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="不对应绝对 T\\+12"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


# --- 合并日志 ---


def test_merged_log_missing_rejects(tmp_path: Path) -> None:
    scene = build_scene(tmp_path, log_bytes=None)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="不存在"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


def test_merged_log_directory_rejects(tmp_path: Path) -> None:
    scene = build_scene(tmp_path, log_bytes=None)
    scene.log.mkdir(parents=True)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="不是普通文件"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


def test_merged_log_fifo_rejects(tmp_path: Path) -> None:
    scene = build_scene(tmp_path, log_bytes=None)
    os.mkfifo(scene.log)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="不是普通文件"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


def test_merged_log_empty_rejects(tmp_path: Path) -> None:
    scene = build_scene(tmp_path, log_bytes=b"")
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="0 字节"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


# --- `check_publish_contract` 零写入 ---


def test_check_publish_contract_writes_nothing_on_success(tmp_path: Path) -> None:
    scene = build_scene(tmp_path)
    root_before = snapshot_tree(scene.root)
    scratch_before = snapshot_tree(scene.scratch)

    publish.check_publish_contract(scene.make_inputs())

    assert snapshot_tree(scene.root) == root_before
    assert snapshot_tree(scene.scratch) == scratch_before


def test_check_publish_contract_writes_nothing_on_failure(tmp_path: Path) -> None:
    scene = build_scene(
        tmp_path,
        dat_payload=build_dat_bytes(nc=REACH_COUNT, rows=EXPECTED_ROWS - 1),
    )
    root_before = snapshot_tree(scene.root)
    scratch_before = snapshot_tree(scene.scratch)

    with pytest.raises(publish.PublishError):
        publish.check_publish_contract(scene.make_inputs())

    assert snapshot_tree(scene.root) == root_before
    assert snapshot_tree(scene.scratch) == scratch_before


# --- `DONE` 双闸 ---


def _preexisting_done_scene(tmp_path: Path, *, shape: str) -> tuple[Scene, Path]:
    scene = build_scene(tmp_path)
    source_dir = scene.root / "output" / T_TEXT / SOURCE
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "yd.rivqdown.dat").write_bytes(b"already published\n")
    done = source_dir / "DONE"
    if shape == "file":
        done.write_bytes(b"")
    elif shape == "dir":
        done.mkdir()
    else:
        done.symlink_to(source_dir / "nowhere")
    return scene, done


@pytest.mark.parametrize("shape", ["file", "dir", "symlink"])
def test_preexisting_done_is_refused(tmp_path: Path, shape: str) -> None:
    """`DONE` 前置闸：任何形态的既有 `DONE` 都拒，且既有产物字节不变。"""
    scene, _done = _preexisting_done_scene(tmp_path, shape=shape)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="已存在"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)
    assert (
        scene.root / "output" / T_TEXT / SOURCE / "yd.rivqdown.dat"
    ).read_bytes() == b"already published\n"


def test_o_excl_is_the_second_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`O_EXCL` 兜底闸：前置探测恒报「不存在」时，步骤 5 仍拒且不覆盖。"""
    scene, done = _preexisting_done_scene(tmp_path, shape="file")
    done.write_bytes(b"first round\n")
    monkeypatch.setattr(publish, "_check_done_absent", lambda inputs: None)

    with pytest.raises(publish.PublishError, match="并发创建"):
        publish.publish(scene.make_inputs())

    assert done.read_bytes() == b"first round\n"


def test_second_call_with_same_inputs_is_refused(tmp_path: Path) -> None:
    """幂等/重入：同一份入参连调两次，第二次稳定拒绝而不是二次提交。"""
    scene = build_scene(tmp_path, old_state_cycles=(T_MINUS_12,))
    inputs = scene.make_inputs()
    publish.publish(inputs)
    after_first = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="已存在"):
        publish.publish(inputs)

    # 这里不用 `_assert_unchanged`：第一轮已按序删掉 work，work 存活那一维不适用。
    assert snapshot_tree(scene.root) == after_first
    assert sorted(p.name for p in (scene.root / "states" / SOURCE).iterdir()) == [
        f"{T_TEXT}.cfg.ic",
        f"{T_PLUS_12}.cfg.ic",
    ]


# --- 旧状态删除集合 ---


def test_only_two_states_survive(tmp_path: Path) -> None:
    """spec Scenario「状态只保留两份」：预置 T-24 / T-12 / T -> 发布后只剩 T 与 T+12。"""
    scene = build_scene(tmp_path, old_state_cycles=(T_MINUS_24, T_MINUS_12))

    publish.publish(scene.make_inputs())

    assert sorted(p.name for p in (scene.root / "states" / SOURCE).iterdir()) == [
        f"{T_TEXT}.cfg.ic",
        f"{T_PLUS_12}.cfg.ic",
    ]


def test_current_and_later_states_are_never_removed(tmp_path: Path) -> None:
    """边界方向：`== T` 永不删（`<` 而不是 `<=`）；`> T+12` 的更晚状态也不删。"""
    scene = build_scene(tmp_path)
    builder = YdRootBuilder(scene.root)
    later = builder.write_state(T_PLUS_24, SOURCE)
    current = scene.root / "states" / SOURCE / f"{T_TEXT}.cfg.ic"
    current_bytes = current.read_bytes()
    later_bytes = later.read_bytes()

    publish.publish(scene.make_inputs())

    assert current.read_bytes() == current_bytes
    assert later.read_bytes() == later_bytes


def test_invisible_entries_are_never_removed(tmp_path: Path) -> None:
    """不可见条目永不删。

    `2026-08-25.cfg.ic` 是「文件名字符串比较」这种错误实现的**唯一**判别器：
    `'-'`=0x2d < `'0'`=0x30，字符串序下它排在任何 2026 cycle 之前而被误判为「更旧」；
    `nine.cfg.ic`（`'n'`=0x6e）与 `9999123123.cfg.ic`（`'9'`=0x39）都 > `'2'`=0x32，
    字符串序下排在更后，错误实现照样不删它们。
    """
    scene = build_scene(tmp_path)
    states = scene.root / "states" / SOURCE
    clutter = [
        f"{T_MINUS_12}.cfg.ic.tmp",
        "nine.cfg.ic",
        "9999123123.cfg.ic",
        ".DS_Store",
        "2026-08-25.cfg.ic",
    ]
    for name in clutter:
        (states / name).write_bytes(b"clutter\n")

    publish.publish(scene.make_inputs())

    for name in clutter:
        assert (states / name).exists(), name


def test_other_source_is_untouched(tmp_path: Path) -> None:
    """逐源隔离：GFS 侧的更旧状态与产物在发布 IFS 后逐项不变。"""
    scene = build_scene(tmp_path, old_state_cycles=(T_MINUS_12,))
    builder = YdRootBuilder(scene.root)
    builder.write_state(T_MINUS_24, "gfs")
    builder.write_state(T_MINUS_12, "gfs")
    builder.write_done(T_MINUS_12, "gfs")
    gfs_states_before = snapshot_tree(scene.root / "states" / "gfs")
    gfs_output_before = snapshot_tree(scene.root / "output" / T_MINUS_12 / "gfs")

    publish.publish(scene.make_inputs())

    assert snapshot_tree(scene.root / "states" / "gfs") == gfs_states_before
    assert (
        snapshot_tree(scene.root / "output" / T_MINUS_12 / "gfs") == gfs_output_before
    )


# --- work 删除 ---


def test_work_tree_with_escaping_symlink_is_removed(tmp_path: Path) -> None:
    """work 树内指向 scratch 根外的 symlink：链接被 unlink，其目标存活。"""
    scene = build_scene(tmp_path)
    outside = tmp_path.resolve() / "outside.txt"
    outside.write_text("survivor\n", encoding="utf-8")
    (scene.work_dir / "raw").mkdir(parents=True, exist_ok=True)
    (scene.work_dir / "raw" / "link").symlink_to(outside)

    publish.publish(scene.make_inputs())

    assert not scene.work_dir.exists()
    assert outside.read_text(encoding="utf-8") == "survivor\n"


def test_work_containment_root_is_not_derived(tmp_path: Path) -> None:
    """`work_root` 不由 `work_dir` 的父链反推：不含 `work_dir` 的根 -> 拒且零删除。"""
    scene = build_scene(tmp_path)
    foreign_root = tmp_path.resolve() / "foreign"
    foreign_root.mkdir()

    with pytest.raises(publish.PublishCleanupError):
        publish.publish(scene.make_inputs(work_root=foreign_root))

    assert scene.work_dir.exists()
    assert (scene.work_dir / "canonical" / "forcing.csv").exists()


# --- 权限 ---


@pytest.mark.parametrize("strict", [False, True])
def test_published_files_do_not_inherit_scratch_mode(
    tmp_path: Path, strict: bool
) -> None:
    """spec Scenario「发布文件不带 scratch 权限」：scratch 0600 -> 三份产物 0o644。

    `strict=True` 那条在 `os.umask(0o077)` 下重跑：默认 umask 0o022 下
    `0o666 & ~umask == 0o644`，`fchmod` 与「裸 open(mode)」两种写法不可分辨。
    """
    scene = build_scene(tmp_path, scratch_mode=0o600)
    assert _mode(scene.dat) == 0o600
    previous = os.umask(0o077) if strict else None
    try:
        publish.publish(scene.make_inputs())
    finally:
        if previous is not None:
            os.umask(previous)

    source_dir = scene.root / "output" / T_TEXT / SOURCE
    for path in (
        source_dir / "yd.rivqdown.dat",
        source_dir / "DONE",
        scene.root / "states" / SOURCE / f"{T_PLUS_12}.cfg.ic",
    ):
        assert _mode(path) == 0o644, path


def test_publish_directories_are_traversable(
    tmp_path: Path, strict_umask: None
) -> None:
    """裁决 8 的目录侧：`output/` 三级都放宽到 0o755，且放宽面既不外溢也不递归。"""
    scene = build_scene(tmp_path, create_output_root=False)
    builder = YdRootBuilder(scene.root)
    builder.write_done(T_MINUS_12, SOURCE)
    historic = scene.root / "output" / T_MINUS_12
    historic.chmod(0o700)
    (historic / SOURCE).chmod(0o700)
    states_dir = scene.root / "states" / SOURCE
    logs_dir = scene.root / "logs"
    root_mode = _mode(scene.root)
    states_mode = _mode(states_dir)
    logs_mode = _mode(logs_dir)

    publish.publish(scene.make_inputs())

    assert _mode(scene.root / "output") == 0o755
    assert _mode(scene.root / "output" / T_TEXT) == 0o755
    assert _mode(scene.root / "output" / T_TEXT / SOURCE) == 0o755
    # 放宽面不外溢、不递归
    assert _mode(historic) == 0o700
    assert _mode(historic / SOURCE) == 0o700
    assert _mode(scene.root) == root_mode
    assert _mode(states_dir) == states_mode
    assert _mode(logs_dir) == logs_mode


def test_publish_module_never_copies_metadata() -> None:
    """源码机检（裁决 8 的结构判据）：不出现 `copy2` / `copystat` / `os.link` / `shutil`。

    `.chmod(` 的匹配**显式排除** `os.fchmod(`：生产代码里跟随 symlink 的写法是
    `some_dir.chmod(0o755)`，按字面禁 `Path.chmod` 这个串对它零判别力。
    """
    source = Path(publish.__file__).read_text(encoding="utf-8")
    for banned in ("copy2", "copystat", "os.link", "shutil"):
        assert banned not in source, banned
    assert source.replace("os.fchmod(", "").count(".chmod(") == 0


# --- `yd_root` 的一次性 resolve ---


def test_publish_succeeds_when_yd_root_is_reached_through_symlink(
    tmp_path: Path,
) -> None:
    """含 symlink 分量的 `yd_root`：五个终名照常落地（裁决 5 的入口 resolve）。

    `tmp_path.resolve()` 对这条没有判别力——它已经是解析后的路径，所以必须单列。
    """
    scene = build_scene(
        tmp_path, old_state_cycles=(T_MINUS_12,), yd_root_via_symlink=True
    )
    assert scene.yd_root_arg != scene.root

    result = publish.publish(scene.make_inputs())

    assert (
        result.dat_path == scene.root / "output" / T_TEXT / SOURCE / "yd.rivqdown.dat"
    )
    assert (scene.root / "output" / T_TEXT / SOURCE / "DONE").is_file()
    assert sorted(p.name for p in (scene.root / "states" / SOURCE).iterdir()) == [
        f"{T_TEXT}.cfg.ic",
        f"{T_PLUS_12}.cfg.ic",
    ]
    assert not scene.work_dir.exists()


# --- 失败面 ---


def test_state_rename_failure_leaves_half_product(tmp_path: Path) -> None:
    """步骤 4 失败：DAT 保留、`DONE` 不存在、work 仍在；12.2 能把它判入清单。"""
    scene = build_scene(tmp_path)
    state_path = scene.root / "states" / SOURCE / f"{T_PLUS_12}.cfg.ic"
    state_path.symlink_to(scene.root / "states" / SOURCE / "nowhere")

    with pytest.raises(publish.PublishError):
        publish.publish(scene.make_inputs())

    source_dir = scene.root / "output" / T_TEXT / SOURCE
    assert (source_dir / "yd.rivqdown.dat").read_bytes() == scene.dat.read_bytes()
    assert not (source_dir / "DONE").exists()
    assert scene.work_dir.exists()

    decision = controller.decide_frontier(
        yd_root=scene.root, source=SOURCE, raw_complete=lambda _cycle: True
    )
    plan = residue.plan_residue(yd_root=scene.root, source=SOURCE, decision=decision)
    assert plan is not None
    assert source_dir in plan.half_product_dirs


def test_post_done_cleanup_failure_is_distinguishable(tmp_path: Path) -> None:
    """`DONE` 之后失败可分辨：抛 `PublishCleanupError`，三份产物俱在且字节正确。"""
    scene = build_scene(tmp_path)
    stale = scene.root / "states" / SOURCE / f"{T_MINUS_12}.cfg.ic"
    stale.symlink_to(scene.root / "states" / SOURCE / f"{T_TEXT}.cfg.ic")

    with pytest.raises(publish.PublishCleanupError) as excinfo:
        publish.publish(scene.make_inputs())

    source_dir = scene.root / "output" / T_TEXT / SOURCE
    done = source_dir / "DONE"
    assert excinfo.value.done_path == done
    assert done.is_file()
    assert (source_dir / "yd.rivqdown.dat").read_bytes() == scene.dat.read_bytes()
    state = scene.root / "states" / SOURCE / f"{T_PLUS_12}.cfg.ic"
    doc = parse(state)
    assert doc.lines[doc.header_index].split()[-1] == _absolute_minute_text(T_PLUS_12)


def test_cleanup_error_is_not_a_publish_error_subclass() -> None:
    """14.1 的 `except PublishError` MUST NOT 把已完成轮吞成失败。"""
    assert not issubclass(publish.PublishCleanupError, publish.PublishError)
    assert not issubclass(publish.PublishError, publish.PublishCleanupError)


# --- 输入域 ---


@pytest.mark.parametrize("source", ["", ".", "..", "a/b", "ifs/"])
def test_source_domain_is_fail_closed(tmp_path: Path, source: str) -> None:
    """`source` 五形态 -> `ValueError`，且两棵树递归快照逐项不变。"""
    scene = build_scene(tmp_path)
    root_before = snapshot_tree(scene.root)
    scratch_before = snapshot_tree(scene.scratch)

    with pytest.raises(ValueError, match="单个非空路径分量"):
        publish.publish(scene.make_inputs(source=source))

    assert snapshot_tree(scene.root) == root_before
    assert snapshot_tree(scene.scratch) == scratch_before


@pytest.mark.parametrize(
    "cycle",
    [
        datetime(2026, 8, 26, 12, 30, tzinfo=UTC),
        datetime(9999, 12, 31, 23, tzinfo=UTC),
    ],
)
def test_cycle_domain_is_fail_closed(tmp_path: Path, cycle: datetime) -> None:
    """非整点 cycle 与 `+12h` 溢出的 cycle 都拒：它们写出的状态下一轮**看不见**。"""
    scene = build_scene(tmp_path)
    root_before = snapshot_tree(scene.root)

    with pytest.raises(ValueError, match="整点 cycle"):
        publish.publish(scene.make_inputs(cycle=cycle))

    assert snapshot_tree(scene.root) == root_before


def test_naive_cycle_is_treated_as_utc(tmp_path: Path) -> None:
    """naive datetime 视为 UTC（与 `state.restamp._ensure_utc` 同向）。"""
    scene = build_scene(tmp_path)

    result = publish.publish(
        scene.make_inputs(cycle=datetime(2026, 8, 26, 12))  # noqa: DTZ001
    )

    assert result.cycle == parse_cycle(T_TEXT)
    assert (scene.root / "output" / T_TEXT / SOURCE / "DONE").is_file()


# --- 有界读（裁决 12） ---


def test_contract_check_reads_only_the_dat_header(tmp_path: Path) -> None:
    """检查阶段的峰值内存与**头部**同量级，而不是与文件大小同量级。

    构造一个 `st_size` 巨大（稀疏）但头部合法的 DAT，与一份正常大小的 DAT 做**同一公开
    seam** 的峰值对比：两者只差数据区，故峰值差就是「检查阶段为数据区付出的内存」。
    期望量级由从 `publish` 导入的 `DAT_FIXED_HEADER_BYTES` 推出，不写死字面量。
    （取差值而不是绝对值：`state.parse` 的有界读会为**任何**大小的 checkpoint 预留
    `MAX_STATE_IC_BYTES` 量级的常量缓冲，那是与 DAT 读取无关的项。同一条常量项也决定了
    DAT 必须**大过** `MAX_STATE_IC_BYTES`：否则整读的峰值被那个常量项盖住，「整读」与
    「有界读」在观测上不可分辨——实测 14 MiB 的 DAT 就是这样让整读的变异体存活的。）
    """
    small = build_scene(tmp_path / "small")
    huge = build_scene(tmp_path / "huge")
    row_bytes = (REACH_COUNT + 1) * FLOAT64_BYTES
    huge_rows = (2 * MAX_STATE_IC_BYTES) // row_bytes
    write_sparse_dat(huge.dat, nc=REACH_COUNT, rows=huge_rows)
    size = huge.dat.stat().st_size
    head_scale = publish.DAT_FIXED_HEADER_BYTES + FLOAT64_BYTES * REACH_COUNT
    assert size > MAX_STATE_IC_BYTES
    assert size > head_scale * 1000

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        publish.check_publish_contract(small.make_inputs())
        _, small_peak = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        publish.check_publish_contract(huge.make_inputs(expected_rows=huge_rows))
        _, huge_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert huge_peak - small_peak < head_scale * 64, (
        huge_peak,
        small_peak,
        head_scale,
        size,
    )


# --- `safe_fs` 的 `mode` 扩展（裁决 9）：缺省路径逐字节不变 ---


@pytest.mark.parametrize("umask_value", [0o077, 0o022])
def test_exclusive_write_default_mode_is_unchanged(
    tmp_path: Path, umask_value: int
) -> None:
    """不传 `mode` 时落地位仍是 `0o666 & ~umask`（既有调用方行为逐字节不变）。"""
    root = tmp_path.resolve()
    target = root / "legacy"
    previous = os.umask(umask_value)
    try:
        safe_fs.write_bytes_no_follow_exclusive(target, b"x", containment_root=root)
    finally:
        os.umask(previous)

    assert _mode(target) == 0o666 & ~umask_value
    assert target.read_bytes() == b"x"


def test_exclusive_write_mode_defeats_umask(tmp_path: Path) -> None:
    """传 `mode=0o644` 时 `fchmod` 抵消 umask（`DONE` 在 0o077 现场仍可被 node-27 读）。"""
    root = tmp_path.resolve()
    target = root / "done"
    previous = os.umask(0o077)
    try:
        safe_fs.write_bytes_no_follow_exclusive(
            target, b"", containment_root=root, mode=0o644
        )
    finally:
        os.umask(previous)

    assert _mode(target) == 0o644


def test_exclusive_write_still_refuses_existing_target(tmp_path: Path) -> None:
    """`O_EXCL` 语义不因 `mode` 扩展而漂移。"""
    root = tmp_path.resolve()
    target = root / "done"
    safe_fs.write_bytes_no_follow_exclusive(target, b"", containment_root=root)

    with pytest.raises(FileExistsError):
        safe_fs.write_bytes_no_follow_exclusive(
            target, b"", containment_root=root, mode=0o644
        )


def test_state_symlink_still_refused_by_safe_fs(tmp_path: Path) -> None:
    """状态侧的 symlink 策略未变：`unlink_no_follow` 拒删 symlink。"""
    root = tmp_path.resolve()
    link = root / "link"
    link.symlink_to(root / "nowhere")

    with pytest.raises(SafeFilesystemError, match="Refusing to unlink symlink"):
        safe_fs.unlink_no_follow(link, containment_root=root)
