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

import errno
import itertools
import os
import pathlib
import stat
import struct
import tracemalloc
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

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
            mesh_count=3, river_count=REACH_COUNT, minute=RELATIVE_MINUTE
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
    # 精确目录快照：`products-contract.md` §4.5「不写 status.json / meta.json」，且
    # 逐名存在性断言放行任何多余条目（含泄漏的 `.tmp`）。
    assert sorted(
        p.name for p in (scene.root / "output" / T_TEXT / SOURCE).iterdir()
    ) == [
        "DONE",
        "yd.rivqdown.dat",
    ]
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
    """`work_root` 不由 `work_dir` 的父链反推：不含 `work_dir` 的根 -> 拒且零删除。

    #94 起该形态在 `PublishInputs` 构造期就被 exact-work 闸以 `ValueError` 拒绝
    （`work_dir != work_root/source/cycle`），先于任何 IO；work 树原样保留。
    """
    scene = build_scene(tmp_path)
    foreign_root = tmp_path.resolve() / "foreign"
    foreign_root.mkdir()

    with pytest.raises(ValueError, match="必须逐字等于"):
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
    """裁决 8 的目录侧：一棵**不含 `output/`** 的根上，三级都放宽到 0o755，且不外溢。

    这棵根刻意不带历史 cycle 目录：`output/` 一旦被预置（哪怕只是为了放一个历史
    `output/<T-12>/`），它就不再是本次自建的层级，按订正后的裁决 8 就**不该**被本模块改
    mode。放宽面对历史目录、`states/`、`logs/` 的不外溢由
    `test_preexisting_output_levels_keep_their_mode` 把守。
    """
    scene = build_scene(tmp_path, create_output_root=False)
    states_dir = scene.root / "states" / SOURCE
    logs_dir = scene.root / "logs"
    root_mode = _mode(scene.root)
    states_mode = _mode(states_dir)
    logs_mode = _mode(logs_dir)

    publish.publish(scene.make_inputs())

    assert _mode(scene.root / "output") & 0o777 == 0o755
    assert _mode(scene.root / "output" / T_TEXT) & 0o777 == 0o755
    assert _mode(scene.root / "output" / T_TEXT / SOURCE) & 0o777 == 0o755
    # 放宽面不外溢
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


# --- 错误域收口（round 1 pattern escalation：error-domain-leak） ---


def test_unreadable_scratch_dat_converges_to_publish_error(tmp_path: Path) -> None:
    """`chmod 0o000` 的 scratch DAT：`open_file_no_follow` 裸抛的 `PermissionError`
    MUST 被收敛（变异体 (ah) 的判别器之一）。

    `SafeFilesystemError` 是 `RuntimeError` 而不是 `OSError`，故两个都要列——只列前者时
    EACCES/EIO 直接穿透 `check_publish_contract` 的声明错误域。
    """
    scene = build_scene(tmp_path)
    scene.dat.chmod(0o000)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="读取失败"):
        publish.check_publish_contract(scene.make_inputs())
    with pytest.raises(publish.PublishError, match="读取失败"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


def test_column_table_read_error_converges_to_publish_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第二趟有界读（列编号表）自己的收敛臂（变异体 (ah) 的第二个判别器）。

    构造的是 `stat_no_follow` 与有界读之间的 ENOENT 竞态：头部读成功之后 DAT 被移走。
    只收窄 `_check_dat` 那一处的变异体靠上一条用例杀不掉。
    """
    scene = build_scene(tmp_path)
    real_read = publish.read_bytes_limited_no_follow
    calls: list[int] = []

    def flaky(path, *, max_bytes, **kwargs):  # type: ignore[no-untyped-def]
        if Path(path) == scene.dat:
            calls.append(max_bytes)
            if len(calls) == 2:
                raise FileNotFoundError(2, "No such file or directory", str(path))
        return real_read(path, max_bytes=max_bytes, **kwargs)

    monkeypatch.setattr(publish, "read_bytes_limited_no_follow", flaky)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="读取失败"):
        publish.publish(scene.make_inputs())

    assert len(calls) == 2
    _assert_unchanged(before, scene)


@pytest.mark.parametrize(
    "error",
    [
        SafeFilesystemError("Failed to create DONE: [Errno 5] Input/output error"),
        OSError(5, "Input/output error"),
    ],
    ids=["safe_fs", "bare_oserror"],
)
def test_done_landed_then_create_fails_is_cleanup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """`DONE` 已在盘、`_create_done` 仍抛错 -> `PublishCleanupError`（变异体 (ag)）。

    真实域触发点是 `store/safe_fs.py:294-301`：`O_EXCL` 的 `os.open` 之后还有 `fchmod`
    与 `os.fsync`（NFS 上的 EIO/ENOSPC/EDQUOT），失败臂只关 fd、不 unlink，于是文件对
    node-27 已经可见而调用方看到的却是「创建失败」。此处把那道窄缝原样重放：先让真实原语
    把 `DONE` 建出来，再抛错。
    """
    scene = build_scene(tmp_path, old_state_cycles=(T_MINUS_12,))
    real_exclusive = publish.write_bytes_no_follow_exclusive

    def exploding(path, content, **kwargs):  # type: ignore[no-untyped-def]
        real_exclusive(path, content, **kwargs)
        raise error

    monkeypatch.setattr(publish, "write_bytes_no_follow_exclusive", exploding)

    with pytest.raises(publish.PublishCleanupError) as excinfo:
        publish.publish(scene.make_inputs())

    source_dir = scene.root / "output" / T_TEXT / SOURCE
    done = source_dir / "DONE"
    assert excinfo.value.done_path == done
    assert done.is_file()
    assert (source_dir / "yd.rivqdown.dat").read_bytes() == scene.dat.read_bytes()
    assert (scene.root / "states" / SOURCE / f"{T_PLUS_12}.cfg.ic").exists()


def test_create_done_failure_without_done_on_disk_is_publish_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向配重：`DONE` **不在盘**时同一类错误必须仍是 `PublishError`。

    缺了这条，「一律抛 `PublishCleanupError`」也能让上一条全绿，而那会把一个没有 `DONE`
    的半成品报成已完成轮。
    """
    scene = build_scene(tmp_path)

    def exploding(path, content, **kwargs):  # type: ignore[no-untyped-def]
        raise SafeFilesystemError("Failed to create DONE: [Errno 28] No space left")

    monkeypatch.setattr(publish, "write_bytes_no_follow_exclusive", exploding)

    with pytest.raises(publish.PublishError, match="创建失败"):
        publish.publish(scene.make_inputs())

    assert not (scene.root / "output" / T_TEXT / SOURCE / "DONE").exists()


def test_state_listing_unreadable_after_done_is_cleanup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """步骤 6 的 `DiscoveryUnreadableError` 臂（round 1 cand-09）。

    `controller.DiscoveryUnreadableError` **不是** `OSError` 的子类，故它是那条 `except`
    元组里唯一一个靠 symlink 形态的旧状态测不到的成员：symlink 走的是 `unlink_no_follow`
    的 `SafeFilesystemError`。`chmod 0o000` 的 `states/` 不是可用触发器——步骤 4 的状态写入
    会先失败。
    """
    scene = build_scene(tmp_path, old_state_cycles=(T_MINUS_12,))

    def unreadable(directory):  # type: ignore[no-untyped-def]
        raise controller.DiscoveryUnreadableError(f"目录 {directory} 无法枚举（EIO）")

    monkeypatch.setattr(publish, "visible_state_cycles", unreadable)

    with pytest.raises(publish.PublishCleanupError) as excinfo:
        publish.publish(scene.make_inputs())

    source_dir = scene.root / "output" / T_TEXT / SOURCE
    done = source_dir / "DONE"
    assert excinfo.value.done_path == done
    assert done.is_file()
    assert (source_dir / "yd.rivqdown.dat").read_bytes() == scene.dat.read_bytes()
    assert (scene.root / "states" / SOURCE / f"{T_PLUS_12}.cfg.ic").exists()
    assert scene.work_dir.exists()


@pytest.mark.parametrize("dangling", [True, False], ids=["dangling", "to_real_file"])
def test_merged_log_symlink_rejects(tmp_path: Path, dangling: bool) -> None:
    """`merged_log` 是 symlink（断链与指向真实非空文件各一）-> `PublishError`。

    裁决 4 逐字写了四形态含 symlink。目录与 FIFO 的 `lstat` 是**成功**的（失败发生在其后的
    `S_ISREG`），不存在走 `FileNotFoundError` 臂，故 `stat_no_follow` 抛
    `SafeFilesystemError` 的那条臂只有本用例能执行到。
    """
    scene = build_scene(tmp_path, log_bytes=None)
    if dangling:
        scene.log.symlink_to(scene.scratch / "out" / "nowhere.log")
    else:
        real = scene.scratch / "out" / "real.log"
        real.write_bytes(b"job 4242 stdout\n")
        scene.log.symlink_to(real)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="不可用"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


# --- 权限：放宽面只覆盖本次自建的层级（round 1 cand-02） ---


def test_preexisting_output_levels_keep_their_mode(
    tmp_path: Path, strict_umask: None
) -> None:
    """预置为 `0o2750` 的 `output/` 与 `output/<T>/`：mode 逐位不变、setgid 存活。

    `docs/agent-ops.md` §10 把「共享组 + 目录 setgid」列为**首选**做法，故这是现场的正常
    形态而不是异形输入。无条件 `fchmod(0o755)` 既把一棵刻意收紧的树开成 world `r-x`
    （变异体 (ai)），又清掉 setgid、让其后新建的条目不再继承共享 gid（变异体 (aj)）。
    """
    scene = build_scene(tmp_path, create_output_root=False)
    builder = YdRootBuilder(scene.root)
    builder.write_done(T_MINUS_12, SOURCE)
    historic = scene.root / "output" / T_MINUS_12
    (historic / SOURCE).chmod(0o700)
    historic.chmod(0o700)
    output_root = scene.root / "output"
    cycle_dir = output_root / T_TEXT
    cycle_dir.mkdir(parents=True)
    output_root.chmod(0o2750)
    cycle_dir.chmod(0o2750)
    assert _mode(output_root) == 0o2750

    publish.publish(scene.make_inputs())

    assert _mode(output_root) == 0o2750
    assert _mode(cycle_dir) == 0o2750
    # 放宽面不递归：历史 cycle 目录不被 walk（变异体 (ab)）。
    assert _mode(historic) == 0o700
    assert _mode(historic / SOURCE) == 0o700
    assert _mode(output_root) & stat.S_ISGID
    assert _mode(cycle_dir) & stat.S_ISGID
    # 本次自建的只有最后一级：它被放宽，且从父目录继承来的高位被保留。
    source_dir = cycle_dir / SOURCE
    assert _mode(source_dir) & 0o777 == 0o755
    assert (source_dir / "DONE").is_file()


# --- 状态结构闸传权威计数（round 1 cand-04） ---


def test_truncated_river_section_is_refused(tmp_path: Path) -> None:
    """river 段行数少于 `reach_count` 的 checkpoint -> `PublishError`（变异体 (ak)）。

    这正是 `state_qc.py:474-481` 点名的形态：tracker 在 SHUD 非原子改写 `cfg.ic.update`
    期间捕获到一份 river 段只写了一半的文件。不传 `expected_river_count` 时
    `_check_row_counts` 对每一类都跳过，它照样拿到 `DONE`，而 `residue.plan_residue` 在
    `DONE(T)` 存在时清单整体为空——下游无人复检。
    """
    scene = build_scene(
        tmp_path,
        checkpoint_payload=build_cfg_ic(
            mesh_count=3, river_count=REACH_COUNT - 6, minute=RELATIVE_MINUTE
        ).payload,
    )
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="结构不完整"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


# --- 整读长度复核（round 1 cand-08） ---


def test_dat_changed_between_check_and_copy_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整读得到的字节数与校验时的 `st_size` 不符 -> 拒且不写 `DONE`（变异体 (al)）。

    校验读的是发布前那一刻的 `st_size`，步骤 3 的整读是另一次独立 open；scratch 树按裁决 6
    「按构造不可信」，滞留/重投的作业写入会让落地的 DAT 带上一条从未被校验过的半行尾巴。
    """
    scene = build_scene(tmp_path)
    real_read = publish.read_bytes_no_follow

    def grown(path, **kwargs):  # type: ignore[no-untyped-def]
        return real_read(path, **kwargs) + b"\x00" * FLOAT64_BYTES

    monkeypatch.setattr(publish, "read_bytes_no_follow", grown)

    with pytest.raises(publish.PublishError, match="在契约检查之后被改动"):
        publish.publish(scene.make_inputs())

    source_dir = scene.root / "output" / T_TEXT / SOURCE
    assert not (source_dir / "DONE").exists()
    assert not (source_dir / "yd.rivqdown.dat").exists()
    assert scene.work_dir.exists()


# --- scratch 侧的一条 symlink 策略（round 1 cand-05 / cand-06） ---


def test_checkpoint_symlink_out_of_scratch_is_refused(tmp_path: Path) -> None:
    """指向 scratch 树外的 checkpoint symlink -> `PublishError`（变异体 (am)）。

    `state.parse(Path)` 走的是 `cfg_ic.py:504-513` 的裸 `open()`，**跟随** symlink：那份
    外来文件会被重戳成正式的 `<T+12>.cfg.ic`，而同样构造在 `scratch_dat` 上是被拒的。
    改为 no-follow 有界读后两侧对称。
    """
    scene = build_scene(tmp_path)
    foreign = tmp_path.resolve() / "elsewhere" / "foreign.cfg.ic"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(
        build_cfg_ic(
            mesh_count=5, river_count=REACH_COUNT, minute=RELATIVE_MINUTE
        ).payload
    )
    scene.checkpoint.unlink()
    scene.checkpoint.symlink_to(foreign)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="读取失败"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)
    assert foreign.exists()


def test_publish_succeeds_when_scratch_is_reached_through_symlink(
    tmp_path: Path,
) -> None:
    """scratch 的**祖先**是 symlink 时整轮照常完成（变异体 (an)）。

    `/scratch -> /mnt/scratch` 这类现场布局下，未解析祖先会让 `safe_fs` 的逐分量
    `O_NOFOLLOW` 在 `DONE` 之前就拒掉每一轮；而只有 work 一条腿走 symlink 时更糟：产物全部
    落地、步骤 7 抛 `PublishCleanupError`，每个成功轮都留一个孤儿 work。故 `work_root` 与
    `work_dir` MUST 一起解析。
    """
    scene = build_scene(tmp_path, old_state_cycles=(T_MINUS_12,))
    base = tmp_path.resolve()
    slink = base / "slink"
    slink.symlink_to(base, target_is_directory=True)
    via = slink / "scratch"

    result = publish.publish(
        scene.make_inputs(
            scratch_dat=via / "out" / "yd.rivqdown.dat",
            scratch_checkpoint=via / "out" / "checkpoint.cfg.ic",
            merged_log=via / "out" / "merged.log",
            work_dir=via / "work" / SOURCE / T_TEXT,
            work_root=via / "work",
        )
    )

    assert (scene.root / "output" / T_TEXT / SOURCE / "DONE").is_file()
    assert result.removed_work_dir == scene.work_dir
    assert not scene.work_dir.exists()
    assert (scene.work_root / SOURCE / T_MINUS_12).exists()


# --- cycle 时区归一（round 1 cand-11） ---


def test_aware_non_utc_cycle_lands_on_the_utc_paths(tmp_path: Path) -> None:
    """`+08:00` 的 20 时 == `2026082612`Z：产物 MUST 落在 UTC 的两条路径上。

    往返自检对这条**没有**判别力：`replace(tzinfo=UTC)` 把它变成 `2026-08-26 20:00Z`，
    那同样是一个可被 `parse_cycle_id` 认回的整点 cycle——只是错的那个。故断言必须落在
    真实路径上。
    """
    scene = build_scene(tmp_path)
    cycle = datetime(2026, 8, 26, 20, tzinfo=timezone(timedelta(hours=8)))
    assert cycle == parse_cycle(T_TEXT)

    result = publish.publish(scene.make_inputs(cycle=cycle))

    assert result.cycle == parse_cycle(T_TEXT)
    assert (scene.root / "output" / T_TEXT / SOURCE / "DONE").is_file()
    assert (scene.root / "states" / SOURCE / f"{T_PLUS_12}.cfg.ic").is_file()
    assert not (scene.root / "output" / "2026082620").exists()


# --- `nc` 的有限整数判据（round 1 cand-12） ---


@pytest.mark.parametrize(
    ("label", "packed_nc"),
    [("NaN", float("nan")), ("非整数", 8.5)],
)
def test_non_integral_column_count_rejects(
    tmp_path: Path, label: str, packed_nc: float
) -> None:
    """`nc` 打包成 `NaN` / `8.5` -> `PublishError`（裁决 4 逐字「有限、整数值」）。

    判据删掉后 `int(8.5) == 8` 会通过 `nc != reach_count` 与整条大小算术，`DONE` 落在一份
    畸形 DAT 上；`int(nan)` 则抛一个裸 `ValueError` 穿透 `check_publish_contract`。
    字节手术打在 offset 1032（`nc` 的 float64 槽位）。
    """
    payload = bytearray(build_dat_bytes(nc=REACH_COUNT, rows=EXPECTED_ROWS))
    offset = TEXT_HEADER_BYTES + FLOAT64_BYTES
    payload[offset : offset + FLOAT64_BYTES] = struct.pack("<d", packed_nc)
    scene = build_scene(tmp_path, dat_payload=bytes(payload))
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="不是有限整数值"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


# --- 连续两轮：上一轮没删净的旧状态由下一轮步骤 6 收（round 1 cand-13） ---


def _second_round_inputs(scene: Scene) -> publish.PublishInputs:
    """T+12 那一轮的 scratch 产出（DAT 与日志复用，checkpoint 重新构造）。"""
    out = scene.scratch / "out2"
    out.mkdir(parents=True, exist_ok=True)
    dat = out / "yd.rivqdown.dat"
    dat.write_bytes(build_dat_bytes(nc=REACH_COUNT, rows=EXPECTED_ROWS))
    checkpoint = out / "checkpoint.cfg.ic"
    checkpoint.write_bytes(
        build_cfg_ic(
            mesh_count=3, river_count=REACH_COUNT, minute=RELATIVE_MINUTE
        ).payload
    )
    log = out / "merged.log"
    log.write_bytes(b"job 4243 stdout\n")
    work_dir = scene.work_root / SOURCE / T_PLUS_12
    (work_dir / "canonical").mkdir(parents=True, exist_ok=True)
    return scene.make_inputs(
        cycle=parse_cycle(T_PLUS_12),
        scratch_dat=dat,
        scratch_checkpoint=checkpoint,
        merged_log=log,
        work_dir=work_dir,
    )


def test_next_round_reclaims_states_the_previous_round_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant Matrix 的 Failure-paths 行：没删净的旧状态由**下一轮的步骤 6** 收。

    第一轮以 `PublishCleanupError` 收尾（步骤 6 的枚举失败），留下 T-24 / T-12 两份**普通
    文件**残留——这正是那一行成立所需的形态；本套件既有的清理失败用例留下的是 symlink，
    下一轮的 `unlink_no_follow` 会再次拒绝它，故它不能验证这条。
    """
    scene = build_scene(tmp_path, old_state_cycles=(T_MINUS_24, T_MINUS_12))
    states = scene.root / "states" / SOURCE

    with monkeypatch.context() as patched:

        def unreadable(directory):  # type: ignore[no-untyped-def]
            raise controller.DiscoveryUnreadableError(f"{directory} 无法枚举（EIO）")

        patched.setattr(publish, "visible_state_cycles", unreadable)
        with pytest.raises(publish.PublishCleanupError):
            publish.publish(scene.make_inputs())

    assert sorted(p.name for p in states.iterdir()) == [
        f"{T_MINUS_24}.cfg.ic",
        f"{T_MINUS_12}.cfg.ic",
        f"{T_TEXT}.cfg.ic",
        f"{T_PLUS_12}.cfg.ic",
    ]
    for name in (T_MINUS_24, T_MINUS_12):
        assert (states / f"{name}.cfg.ic").is_file()

    publish.publish(_second_round_inputs(scene))

    assert sorted(p.name for p in states.iterdir()) == [
        f"{T_PLUS_12}.cfg.ic",
        f"{T_PLUS_24}.cfg.ic",
    ]
    assert (scene.root / "output" / T_PLUS_12 / SOURCE / "DONE").is_file()


def test_widening_preserves_inherited_setgid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strict_umask: None
) -> None:
    """自建层级从父目录**继承**到的 setgid 在放宽时必须存活（变异体 (aj)）。

    平台注记：Linux 的 `mkdir` 会把父目录的 `S_ISGID` 继承给新建子目录，这正是
    `docs/agent-ops.md` §10「共享组 + 目录 setgid」在 node-22 上的落地方式；macOS
    （本套件的开发平台）**不**继承（实测父 `0o2750` -> 子 `0o755`），故这里在
    `ensure_directory_no_follow` 的模块内绑定处补上那一步继承，把生产平台的形态搬到断言
    面前。不这么做，「`fchmod` 写整个 mode 字」这个缺陷在本平台上没有任何判别器。
    """
    scene = build_scene(tmp_path, create_output_root=False)
    real_ensure = publish.ensure_directory_no_follow
    levels = (
        scene.root / "output",
        scene.root / "output" / T_TEXT,
        scene.root / "output" / T_TEXT / SOURCE,
    )

    def inheriting(directory, **kwargs):  # type: ignore[no-untyped-def]
        result = real_ensure(directory, **kwargs)
        for level in levels:
            if level.is_dir():
                level.chmod(_mode(level) | stat.S_ISGID)
        return result

    monkeypatch.setattr(publish, "ensure_directory_no_follow", inheriting)

    publish.publish(scene.make_inputs())

    for level in levels:
        assert _mode(level) == 0o2755, level


# --- 权限：可穿越是后置断言，不是「放宽跑过了」（round 2 cand-02） ---


def test_untraversable_output_level_is_refused(
    tmp_path: Path, strict_umask: None
) -> None:
    """已存在的 `output/` 不可穿越 -> `PublishError` 指名该层级，零 NFS 变更（变异体 (ap)）。

    这棵树的 `output/` 是在 `os.umask(0o077)` 下由上游建出来的，落地即 `0o700`——正是本轮
    P1 描述的闩死态的静态形态。今日行为（无后置断言）是 `DONE=True` 且 `output/` 留在
    `0o700`：`DONE` 封在一棵 node-27 连穿越都做不到的树上，状态链照常推进、无任何信号。

    断言里的递归快照是这条的承重部分：只在末尾复 stat 的实现会先把 `output/<T>/` 与
    `output/<T>/<source>/` `mkdir` 出来再拒绝，快照当场就变。
    """
    scene = build_scene(tmp_path)
    output_root = scene.root / "output"
    assert _mode(output_root) == 0o700, "前提：umask 0o077 下 output/ 落地即 0o700"
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError) as excinfo:
        publish.publish(scene.make_inputs())

    message = str(excinfo.value)
    assert str(output_root) in message
    assert "0o700" in message
    assert not (output_root / T_TEXT).exists()
    assert not (output_root / T_TEXT / SOURCE / "DONE").exists()
    _assert_unchanged(before, scene)


@pytest.mark.parametrize(
    ("failing_widen", "level_index"),
    [(1, 0), (2, 1)],
    ids=["output_root", "cycle_dir"],
)
def test_latched_output_level_is_refused_on_the_next_round(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict_umask: None,
    failing_widen: int,
    level_index: int,
) -> None:
    """闩死态的**动态**入口：首轮放宽抛 EIO -> 次轮仍必须拒（变异体 (ap)）。

    monkeypatch 打在 `os.fchmod` 这一层而不是整个 `_widen_publish_dir`：真实失败面就是
    `fchmod` 这一次系统调用（NFS EIO/ESTALE），换掉整个 helper 会把「stat 与放宽之间」这段
    窗口一起换掉，判别力反而下降。

    首轮之后该层级以 `0o700` 留在盘上；其后每一轮都把它看作「已存在」而不动，
    `residue._half_product_dirs` 也只删 `output/<T>/<source>/`、从不碰父级。故没有后置断言
    的实现在**次轮**会一路走到 `DONE`——那正是这条要杀的形态。`output/<T>/` 这一级单独跑
    一次：verifier 实测它同样会闩死，「只有 `output/` 会永久闩住」的说法已被证伪。
    """
    scene = build_scene(tmp_path, create_output_root=False)
    levels = (
        scene.root / "output",
        scene.root / "output" / T_TEXT,
        scene.root / "output" / T_TEXT / SOURCE,
    )
    latched = levels[level_index]
    real_fchmod = os.fchmod
    calls = {"n": 0}

    def flaky(fd: int, mode: int) -> None:
        calls["n"] += 1
        if calls["n"] == failing_widen:
            raise OSError(errno.EIO, "Input/output error")
        real_fchmod(fd, mode)

    with monkeypatch.context() as patched:
        patched.setattr(os, "fchmod", flaky)
        with pytest.raises(publish.PublishError):
            publish.publish(scene.make_inputs())

    assert _mode(latched) & (stat.S_IXGRP | stat.S_IXOTH) == 0, (
        "前提：该层级已以 0o700 闩死"
    )

    # 真实 fchmod 恢复后对**同一轮**重跑：不得把 DONE 封在一棵闩死的树上。
    with pytest.raises(publish.PublishError) as excinfo:
        publish.publish(scene.make_inputs())

    assert str(latched) in str(excinfo.value)
    assert not (levels[2] / "DONE").exists()


# --- `DONE` 复探的极性（round 2 cand-01 / cand-03a） ---


def test_done_reprobe_failure_converges_to_publish_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复探测不出来 -> 「本轮未完成」（变异体 (ao)）。

    构造：步骤 5 的写入抛错，**且**同时把 `output/<T>/<source>/` 置为 `0o000`，于是复探
    本身失败而 `DONE` 按构造不存在。今日行为是 `PublishCleanupError`（「本轮已完成」）——
    `stat_no_follow` 把 EACCES 包成 `SafeFilesystemError(kind="io")`，而旧复探把
    `SafeFilesystemError` 读成「条目存在」。

    `PublishCleanupError` **不是** `PublishError` 的子类，故这条 `pytest.raises` 本身就是
    判别器：旧实现会让 `PublishCleanupError` 直接逃出去。与既有的
    `test_done_landed_then_create_fails_is_cleanup_error` 构成一对反向配重。
    """
    scene = build_scene(tmp_path)
    source_dir = scene.root / "output" / T_TEXT / SOURCE

    def exploding(path, content, **kwargs):  # type: ignore[no-untyped-def]
        source_dir.chmod(0o000)
        raise SafeFilesystemError("Failed to create DONE: [Errno 5] Input/output error")

    monkeypatch.setattr(publish, "write_bytes_no_follow_exclusive", exploding)

    try:
        with pytest.raises(publish.PublishError, match="创建失败"):
            publish.publish(scene.make_inputs())
    finally:
        source_dir.chmod(0o755)

    assert not (source_dir / "DONE").exists()


def test_done_as_symlink_still_counts_as_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向半边：`DONE` 位置是 symlink 时复探仍判「在盘」（裸 `lstat` 的语义）。

    缺这条，把复探写成「`os.path.isfile` / 跟随 symlink 的 stat」也能让上一条全绿，而那会
    在一条 symlink `DONE` 上报「本轮未完成」——viewer 与 `decide_frontier` 判的是条目存在
    与否，那份产物对它们已经是完成态了。
    """
    scene = build_scene(tmp_path)
    source_dir = scene.root / "output" / T_TEXT / SOURCE

    def exploding(path, content, **kwargs):  # type: ignore[no-untyped-def]
        Path(path).symlink_to(scene.log)
        raise SafeFilesystemError("Failed to create DONE: [Errno 5] Input/output error")

    monkeypatch.setattr(publish, "write_bytes_no_follow_exclusive", exploding)

    with pytest.raises(publish.PublishCleanupError) as excinfo:
        publish.publish(scene.make_inputs())

    assert excinfo.value.done_path == source_dir / "DONE"
    assert (source_dir / "DONE").is_symlink()


# --- checkpoint 不可解析（round 2 cand-03c） ---


@pytest.mark.parametrize(
    "payload",
    [b"\xff\xfe\x00\x01\x02\x03" * 64, b""],
    ids=["binary_junk", "empty"],
)
def test_unparseable_checkpoint_converges_to_publish_error(
    tmp_path: Path, payload: bytes
) -> None:
    """`parse(raw)` 的 `ValueError` MUST 收敛为 `PublishError`（变异体 (ar)）。

    `parse` 的契约是「任何结构性不可用一律抛 `ValueError`」（`state/cfg_ic.py:290-291`）：
    二进制垃圾走「不是合法 UTF-8」、零字节走「空 IC 文件」。既有的 header 形状用例走的是
    其后的**重戳**臂，从未执行到这一条；该 `ValueError` 若穿透，14.1 的
    `except PublishError` 接不住。
    """
    scene = build_scene(tmp_path, checkpoint_payload=payload)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="无法解析"):
        publish.publish(scene.make_inputs())

    _assert_unchanged(before, scene)


def test_unstattable_scratch_dat_converges_to_publish_error(tmp_path: Path) -> None:
    """`_read_dat_head` 的 **stat** 臂（round 1 cand-03 收口后零覆盖的那一条）。

    既有的 `test_unreadable_scratch_dat_converges_to_publish_error` 只 `chmod 0o000` 文件
    本身：`lstat` 照样成功，红的是其后的有界读。要打到 stat 臂必须让**父目录**不可进入，
    故 DAT 单独放一个目录（`scratch/out/` 里还有 checkpoint 与日志，一起封掉会先在
    checkpoint 上失败）。`stat_no_follow` 把 EACCES 包成 `SafeFilesystemError`，此处必须
    收敛为 `PublishError` 而不是穿透。
    """
    scene = build_scene(tmp_path)
    dat_dir = scene.scratch / "datonly"
    dat_dir.mkdir()
    dat = dat_dir / "yd.rivqdown.dat"
    dat.write_bytes(scene.dat.read_bytes())
    inputs = scene.make_inputs(scratch_dat=dat)
    before = snapshot_tree(scene.root)

    dat_dir.chmod(0o000)
    try:
        with pytest.raises(publish.PublishError, match="不可用"):
            publish.publish(inputs)
    finally:
        dat_dir.chmod(0o755)

    _assert_unchanged(before, scene)


# --- 可穿越判据：穷举真值表 + 独立措辞 oracle（round 4 的第二份 Review Failure Retro） ---

#: 逐位分解表：只登记 `stat` 模块的**具名单比特**常量，不含任何组合掩码。
#: 被测实现用的是模块级组合常量 `_GROUP_READ_TRAVERSE`/`_OTHER_READ_TRAVERSE`（`0o050`/
#: `0o005`）。两边的单比特常量同源，但**不共享组合常量、也不共享组合逻辑**——改坏其中一处
#: 不会传染到另一处。
_NAMED_PERMISSION_BITS = (
    stat.S_ISUID,
    stat.S_ISGID,
    stat.S_ISVTX,
    stat.S_IRUSR,
    stat.S_IWUSR,
    stat.S_IXUSR,
    stat.S_IRGRP,
    stat.S_IWGRP,
    stat.S_IXGRP,
    stat.S_IROTH,
    stat.S_IWOTH,
    stat.S_IXOTH,
)

#: node-27 只可能落到的两类主体，各自的「列名字」位与「进得去」位。
#: owner 不在表里：发布进程自己永远进得去，算上它这条判据即恒真（变异体 (aq)）。
_NON_OWNER_CLASSES = (
    ("group", stat.S_IRGRP, stat.S_IXGRP),
    ("other", stat.S_IROTH, stat.S_IXOTH),
)


def _permission_bits(mode: int) -> set[int]:
    """把一个 mode 拆成它所置位的具名 `stat` 常量集合（组合掩码在这里不存在）。"""
    return {bit for bit in _NAMED_PERMISSION_BITS if mode & bit}


def _node27_can_list_and_enter(mode: int) -> bool:
    """独立 oracle：`{group, other}` 中**存在某一类**同时具备 `r` 与 `x`。

    这是 `docs/products-contract.md` §8（node-27 `nwm` 需要目录**遍历与读取**权限）与
    `docs/agent-ops.md` §10（「读/遍历」）的直译：node-27 只有一个身份，它要么走 group
    要么走 other；`r` 是 `readdir`（列得出 cycle 目录里的名字），`x` 是遍历（`open`/`stat`
    目录内的条目），两者缺一它都用不了这棵树。

    形式上刻意与被测实现**不同构**：这里先把 mode 拆成一组具名单比特常量，再按类循环做
    集合成员判定；被测实现是「组合掩码与自身相等」。故这不是同义反复——它既不共享常量，
    也不共享运算形状，改坏 `_GROUP_READ_TRAVERSE` 之类的一处不会同时改坏两边。
    """
    present = _permission_bits(mode)
    for _class_name, read_bit, traverse_bit in _NON_OWNER_CLASSES:
        can_list = read_bit in present
        can_enter = traverse_bit in present
        if can_list and can_enter:
            return True
    return False


def test_traversability_predicate_matches_an_independent_oracle() -> None:
    """判据的**整个输入域**与独立 oracle 逐值对拍（0o7777 全域，4096 个 mode）。

    这条是本 fixture 里判据类断言的验收形式，它取代了 round 3 retro 定下的「十格逐格枚
    举」。round 4 的 verifier 实测证明那十格与「拿上一轮反例调掩码」是同一个错误高了一
    层：三条自然变异体在十格下全部存活（跨类合取误放行 `0o741`/`0o714`；整位段相等误**拒**
    §10 首选的 `0o2770`；前置那趟只查首级），而且**即使扩到十三格，对自然掩码族做暴力扫描
    仍有 94 个变异体存活**。样本追不上域，只有穷举加独立 oracle 关得掉这条复发路径。

    oracle 见 :func:`_node27_can_list_and_enter`：按类循环的自然语言直译，MUST NOT 抄被测
    的掩码表达式。三重独立性写在这里，供 review 复核：

    1. **运算形状不同**——oracle 先逐位拆成具名 `stat` 常量再做集合成员判定，被测是组合掩码
       自等比较；
    2. **组合常量不共享**——oracle 只用 `stat.S_IRGRP`/`S_IXGRP`/`S_IROTH`/`S_IXOTH` 这些
       单比特常量（被测的组合常量也由它们拼出，同源的是单比特，不是组合），被测用
       `_GROUP_READ_TRAVERSE`/`_OTHER_READ_TRAVERSE`（`0o050`/`0o005`）。把被测的组合常量
       改成 `0o054` 之类不会传染到 oracle（这正是变异体 (aq7)）；
    3. **第三方配重**——放行格数由组合数**手算**得出并单独断言：owner 三位自由（8 种），
       非 owner 两个位段各 8 种、其中「`r` 与 `x` 同时置位」的有 2 种（`w` 自由），故不放行
       的是 `6 × 6`，放行的是 `8 × (8×8 − 6×6) = 224`。这个数既不来自被测也不来自 oracle
       的代码形状，oracle 自己写错也会当场变红。

    高位叠加取 `S_ISUID`/`S_ISGID`/`S_ISVTX` 的**全部 8 种组合**（fixture 要求的是三种单叠，
    这里取全组合是它的超集）：512 × 8 = 4096 恰好是 :func:`stat.S_IMODE` 能返回的**每一个
    值**，故这张表覆盖的是判据的完整输入域，一个字面值都没剩。只叠三个单高位会漏掉
    「setgid + sticky」这类组合——共享组目录上再加 sticky 是现场会出现的形态，而一条形如
    「…且不得同时置 setgid 与 sticky」的错判据能从那个洞里钻过去。两边在所有 8 种叠加下都
    必须与无高位时**同值**——现场按 `agent-ops.md` §10 首选做法设的 `0o2750` 靠的就是这条。

    owner 位被剥掉的那些 mode（如 `0o055`、`0o007`）在这里按**纯函数契约**断言：判据只看
    非 owner 两类，故 `0o055` 为真。端到端它们走不到这一步——`open_directory_no_follow`
    自己就先失败了，那是另一套机制。本用例测的是函数，不是那一轮，两者在这里合法地不同。
    """
    high_bits = (
        (stat.S_ISUID, "S_ISUID"),
        (stat.S_ISGID, "S_ISGID"),
        (stat.S_ISVTX, "S_ISVTX"),
    )
    overlays = [(0, "无高位")]
    for count in (1, 2, 3):
        for combination in itertools.combinations(high_bits, count):
            overlay = 0
            for bit, _label in combination:
                overlay |= bit
            overlays.append((overlay, "+".join(label for _bit, label in combination)))
    accepted = 0
    checked = 0
    disagreements: list[str] = []

    for low in range(0o1000):
        expected = _node27_can_list_and_enter(low)
        accepted += int(expected)
        for overlay, label in overlays:
            mode = low | overlay
            checked += 1
            if _node27_can_list_and_enter(mode) is not expected:
                disagreements.append(f"{mode:#o}[{label}]：oracle 自身被高位改了结论")
            if publish._is_readable_and_traversable(mode) is not expected:
                disagreements.append(
                    f"{mode:#o}[{label}]：判据={not expected}，oracle={expected}"
                )

    assert len(overlays) == 8, "前提：三个高位的全部 8 种组合"
    assert checked == 8 * 0o1000, (
        "前提：0o7777 这个域**全跑**——512 个低九位 × 三个高位的 8 种组合 = 4096，"
        "恰好是 stat.S_IMODE 能返回的每一个值"
    )
    assert accepted == 224, (
        f"oracle 自身的配重：手算放行格数 8 × (8×8 − 6×6) = 224，实得 {accepted}"
    )
    assert not disagreements, (
        f"判据与独立 oracle 在 {len(disagreements)} 处分歧，前 10 处："
        + "；".join(disagreements[:10])
    )


@pytest.mark.parametrize(
    ("preset_mode", "accepted"),
    [
        (0o755, True),
        (0o750, True),
        (0o705, True),
        (0o2750, True),
        (0o2751, True),
        (0o2770, True),
        (0o700, False),
        (0o744, False),
        (0o710, False),
        (0o711, False),
        (0o701, False),
        (0o741, False),
        (0o714, False),
    ],
    ids=[
        "0o755",
        "0o750",
        "0o705",
        "0o2750",
        "0o2751",
        "0o2770",
        "0o700",
        "0o744",
        "0o710",
        "0o711",
        "0o701",
        "0o741",
        "0o714",
    ],
)
def test_traversability_predicate_is_wired_into_publish(
    tmp_path: Path, strict_umask: None, preset_mode: int, accepted: bool
) -> None:
    """十三格**接线证据**：判据真的被 `publish()` 调用，拒绝真的发生在第一处 NFS 写入之前。

    判据本身的正确性由 :func:`test_traversability_predicate_matches_an_independent_oracle`
    的穷举真值表守住，**不由这张格子表守住**——这十三格挡不住自然掩码族的绝大多数变异体
    （round 4 verifier 实测：十三格下仍有 94 个存活）。这条用例只负责另一件穷举表做不到的
    事：把纯函数接到真实的一轮发布上，钉住「拒绝时 `output/<T>/` 未被创建、`YD_ROOT` 递归
    快照逐项不变」这个提交序性质。

    格子的来历：`0o744`/`0o710` 是初稿 `0o055` 与 round-2 `0o011` 各自漏掉的那一格；
    `0o741`/`0o714` 是 `r` 与 `x` 分处两类的形态（变异体 (aq4) 的判别器）；`0o2770` 是
    `agent-ops.md` §10 首选的共享组 + setgid 形态，误拒它等于该源永久停摆（变异体 (aq5)）；
    `0o2750`/`0o2751` 钉住高位原样穿过。
    """
    scene = build_scene(tmp_path)
    output_root = scene.root / "output"
    output_root.chmod(preset_mode)
    assert _mode(output_root) == preset_mode, "前提：预置 mode 落地"
    source_dir = output_root / T_TEXT / SOURCE

    if accepted:
        publish.publish(scene.make_inputs())
        assert (source_dir / "DONE").is_file()
        # 已存在的层级原样不动：放行不等于放宽（变异体 (ai)/(aj)）。
        assert _mode(output_root) == preset_mode
        return

    before = snapshot_tree(scene.root)
    with pytest.raises(publish.PublishError) as excinfo:
        publish.publish(scene.make_inputs())

    message = str(excinfo.value)
    assert str(output_root) in message
    assert f"{preset_mode:#o}" in message
    assert not (output_root / T_TEXT).exists()
    _assert_unchanged(before, scene)


def test_untraversable_middle_output_level_is_refused(
    tmp_path: Path, strict_umask: None
) -> None:
    """不合规的是**中间**层级（`output/` 合规、`output/<T>/` 为 `0o700`）（变异体 (aq8)）。

    前置那趟 MUST 逐级查，而不是只查首级：只查首级的实现会先把 `output/<T>/<source>/`
    `mkdir` 出来再由后置那趟拒绝，`YD_ROOT` 递归快照当场就变。故这条的判别力**不在**异常
    消息上（那个变异体照样抛 `PublishError` 且照样指名中间层级），而在下面两条：
    `output/<T>/<source>/` 未被创建、快照逐项不变。

    这一级同样会永久闩死：`residue._half_product_dirs` 只删 `output/<T>/<source>/`、从不碰
    父级，而已存在的层级本模块一律不改 mode。
    """
    scene = build_scene(tmp_path)
    output_root = scene.root / "output"
    output_root.chmod(0o755)
    middle = output_root / T_TEXT
    middle.mkdir()
    middle.chmod(0o700)
    assert _mode(output_root) == 0o755, "前提：首级合规"
    assert _mode(middle) == 0o700, "前提：中间层级不合规"
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError) as excinfo:
        publish.publish(scene.make_inputs())

    message = str(excinfo.value)
    assert str(middle) in message
    assert "0o700" in message
    assert not (middle / SOURCE).exists()
    assert not (middle / SOURCE / "DONE").exists()
    _assert_unchanged(before, scene)


# --- DAT 短于定长头部（round 3 cand-03） ---


def test_fixed_header_size_matches_the_independent_fixture_oracle() -> None:
    """定长头部长度的两处登记必须一致（下一条用例的期望长度取自 `publish` 侧）。

    `dat_fixtures.FIXED_HEADER_BYTES` 是独立于被测模块登记的 oracle；截断长度按 fixture
    要求从 `publish.DAT_FIXED_HEADER_BYTES` 推出（不得写死 1040），这条等式把两者钉在一起。
    """
    assert publish.DAT_FIXED_HEADER_BYTES == FIXED_HEADER_BYTES


@pytest.mark.parametrize(
    "size",
    [publish.DAT_FIXED_HEADER_BYTES - 1, 100],
    ids=["boundary", "tiny"],
)
def test_dat_shorter_than_fixed_header_is_refused(tmp_path: Path, size: int) -> None:
    """字节数少于定长头部的 DAT -> `PublishError`，零 NFS 变更（变异体 (av)）。

    钉的是异常**类型**而不只是「抛了错」：删掉该长度闸后 `head[1032:1040]` 是一段不足 8
    字节的切片，`struct.unpack` 抛 `struct.error`——它既不是 `OSError` 也不是 `ValueError`，
    沿途两处 `except (SafeFilesystemError, OSError)` 都接不住，`check_publish_contract` 与
    `publish` 两个公共入口都会被它穿透，14.1 的 `except PublishError` 更接不住。

    既有的 `test_column_table_read_error_converges_to_publish_error` 对这条**没有**判别力：
    它在定长前缀**之后**截断，根本走不到这条臂；文本头形状闸也拦不住——被截断的 v2 前缀
    仍然是「可打印 ASCII + 其后全 NUL」，照样通过。
    """
    payload = build_dat_bytes(nc=REACH_COUNT, rows=EXPECTED_ROWS)[:size]
    assert len(payload) < publish.DAT_FIXED_HEADER_BYTES
    scene = build_scene(tmp_path, dat_payload=payload)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError) as excinfo:
        publish.publish(scene.make_inputs())

    message = str(excinfo.value)
    assert "非 v2" in message
    assert "定长头部不足" in message
    _assert_unchanged(before, scene)


def test_dat_shorter_than_fixed_header_is_refused_by_the_contract_check(
    tmp_path: Path,
) -> None:
    """同一形态经 `check_publish_contract` 这个公共入口进来时同样收敛（变异体 (av)）。

    错误域不变量约束的是**两个**公共边界，而 `check_publish_contract` 沿途一处 `except`
    都没有（`publish` 至少还有步骤 3–5 的收敛点），故它是 `struct.error` 逃逸最短的一条路。
    """
    payload = build_dat_bytes(nc=REACH_COUNT, rows=EXPECTED_ROWS)[
        : publish.DAT_FIXED_HEADER_BYTES - 1
    ]
    scene = build_scene(tmp_path, dat_payload=payload)
    before = snapshot_tree(scene.root)

    with pytest.raises(publish.PublishError, match="定长头部不足"):
        publish.check_publish_contract(scene.make_inputs())

    _assert_unchanged(before, scene)
