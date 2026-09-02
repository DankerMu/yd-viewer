# NWM@8ae9b8f2 tests/test_shud_runtime.py
r"""`ensure_twelve_hour_checkpoint` 的行为测试（任务 9.2 / 清单 cap 6b）。

溯源与适配：pin 的补跑族（`tests/test_shud_runtime.py` L5140-5936 的 19 个 `run_shud` 用例）按 Issue #17 fixture 的「明示不移植面」/「仍适用业务不变量」矩阵改写挂到 yd seam——`ensure_twelve_hour_checkpoint(*, tracker, run_directory, runner)` + 注入的同步假 SHUD runner；逐字复制 13 项 Python script stub 不属于本文件（yd 只承诺项目模式 END=0.5/720 一处改写、runner 由调用方注入、fixture 明确要求 fake 注入）。保留的 oracle：same-input、fresh-output、END=0.5/Update_IC_STEP=720、rc/异常、candidate 家族、stale residue、parameter restore、no-clobber。参数 writer 的 `end` 扩展证据落在 `test_assemble_parameters.py`。

**声明精度**：成功用例只证明「runner 同步调用一次」与「不写 manifest / outcome 文件」。9.2 的「控制器提交计数不变」需要 yd 自己的 run-record / executor 缝，归 #26 的 `run_once` 集成断言（清单 cap 6 行对 L5828 用例的处置同判）；本文件 MUST NOT 声称证明「没有第二次 scheduler submission」。

结账表（cap 6b；口径同 `test_checkpoint_tracker.py` 的捕获表：每个复合守卫操作数、每个异常元组成员、每个 `safe_fs` 关键字实参，要么有「改坏即变红」的见证，要么有书面等价理由；`->` 后是用例名，括号 = 参数化支数）。
- R1 入口与 preflight：`targets == (12,)` -> `runner_is_never_called_for_non_twelve_targets`(4)（`()` 已由 #16 构造期拒绝，等价）；三入参类型 / 可调用性 -> `wrong_arg_types_are_rejected_before_any_io`(5)。
- R1 路径身份：`path` 绝对 / 预存在 no-follow 目录、`project_name` 与 `identity.project_name` 双相等、三个静态字段 exact 顶层路径、CSV 顶层直属 / ASCII 文法 / 非空 / casefold 唯一 / `MAX_DIRECT_GRID_STATION_BINDINGS` -> `forged_run_directory_matrix`(22)。
- R1 目录归属：`path.name == "model"`（`elsewhere` 支封「关掉判据」，`model-old` 支封「相等放宽成子串包含」）与 `path == tracker.run_dir`（两支都不接受「同步伪造 tracker + 全部字段的整棵合法树」）-> `run_directory_must_be_the_trackers_own_model_directory`(3)。
- R1 输入形态：初态有界读 + `state.parse` -> `initial_state_must_be_parseable`；basename 文法**无**业务长度 cap -> `long_forcing_basename_is_accepted`。
- R2 authority：`CapturedCheckpoint` 五字段逐项 + 「valid 记录原样返回、runner 0 调用」 -> `captured_record_is_the_only_authority`(6)；回读的 checksum / header / body **三条 gate 各自独立可杀**（header、body 支各自同步更新记录 checksum 让前置 gate 通过，故无「前 gate 掩盖后 gate」的假覆盖）-> `captured_point_of_use_gates_are_independent`(3)。（#17 phase 2 审计第 2 条：原先的 `checksum` 类型/空值判据与独立尺寸判据**已从生产代码删除**——前者被摘要比对完全覆盖、后者由 `state.parse` 的同一 `MAX_STATE_IC_BYTES` 权威判定覆盖，不留死 guard 配等价变异体。）
- R2 残留隔离：canonical 与 recovery root 各三种既有形态 -> `preexisting_residue_is_never_touched`(6)；四个 exact 顶层路径 + candidate **原位**换 shape（目录 / 符号链接；FIFO/socket 不在 `assemble()` 产出形态域内，故不声明）-> `nonregular_shape_matrix`(10)。
- R2 no-clobber / 清理权（#17 R3：**canonical 一律不按路径名删除**，`safe_fs` 无 compare-and-unlink 且不在变更面内，O_EXCL 返回后的身份不可证明）：`_capture` 的 `FileExistsError` 不删除分支 -> `capture_no_clobber_and_valid_copy`(2)；创建前/创建期失败零删除尝试（`regular` / `directory` / `symlink` 三形态，regular 是删除可观测的那一支）-> `capture_never_unlinks_a_canonical_entry`(6)；commit 窗口竞争者（普通文件 bytes / 外指符号链接目录的 open 拒绝两支）-> `install_race_keeps_the_foreign_bytes`、`install_open_failure_behind_symlinked_dir_never_unlinks`；安装**之后**三条失败 lane（回读失败 / 换成合法但不同的一份 / 换成结构坏掉的一份）一律保留 residue、零删除、无 authority -> `post_install_readback_failure_preserves_residue_and_never_unlinks`(3)。捕获侧的对应两支（外来合法份 / 撕裂份 -> 不采纳不删除）在 `test_checkpoint_tracker.py::test_no_pathname_delete_after_the_exclusive_write`(2)；跨 attempt 未验证 residue -> `cross_attempt_torn_source_residue_is_not_adopted`。
- R3 参数窗口：唯一 writer 的 `end="0.5"` 窗口可读 + `try/finally` 恢复 + 输出目录恒等 + 记录五字段 + 整棵 run dir 只有 canonical 新增 -> `genuine_miss_success_installs_canonical_record`；另一棵装配树上独立复核「parameter 逐字恢复、无多余产物」-> `parameter_is_the_only_expected_change`。
- R3 forcing：forcing **无**业务体积上限（>8 MiB 合法 CSV 的完整成功路径 + descriptor-bound 流式摘要 + 调用前后内容对账）-> `large_forcing_csv_above_the_removed_cap_succeeds`。
- R3 失败传导：restore 写失败 -> `restore_failure_blocks_adoption`；restore 写完但落盘不确定（盘上已是原 bytes）仍 MUST 整轮失败 -> `restore_durability_failure_blocks_adoption_even_when_bytes_match`（与下一条各有独立可杀性：这条走读回逐字相同的窗口，字节比对 gate 不会响）；restore 未逐字还原 MUST NOT 记 authority -> `restore_readback_must_match_the_original_bytes`；runner 抛错与 restore 抛错**同时**发生 -> `double_failure_keeps_both_causes_inspectable`（cause 链保留 runner + note 记账 restore，结构断言）；state / index / CSV 三类后校验 -> `input_drift_after_runner_blocks_adoption`(3)（三支各对应一条独立矩阵腿，删单条判据不放走其余两类）。
- R3 等价理由：`atomic_write_bytes_no_follow` 两处站点（临时写 / finally 恢复）与三处 `containment_root=run_directory.path` = 同函数、同关键字、同字段，注入失败使两支同时变红，无独立可变异操作数。
- R4 runner 结局：普通异常收敛（`__cause__` 保留）/ 非 strict int / 非零且留下 gate-valid candidate 均不采纳 -> `runner_outcome_never_installs`(9)。
- R4 candidate：家族十形态（missing / 另名 / 嵌套 / 目录 / 外指符号链接 / 非 UTF-8 / 1440 / 非有限 / 截断 body / `max_bytes+1` oversize，含证据保留。`max_bytes` 是**冻结读界**（不是第二份尺寸判据：尺寸的唯一权威是 `state.parse` 自带的 `MAX_STATE_IC_BYTES`，`oversize` 支实际由「header 不可读」拦下——64 MiB 全 `x` 无数值 token），其逐字值 + containment 由 `candidate_single_read_drives_install` 的实参核对钉死，读界乘 4 因此是 KILLED 腿而非等价体）-> `candidate_family_is_rejected`(10)，其原位目录 / 符号链接两支由 `nonregular_shape_matrix`(2 支) 承担；单次有界读驱动安装 + 读界/containment 逐字核对 -> `candidate_single_read_drives_install`；output dir inode swap -> `output_dir_identity_swap_rejects_the_replacement_tree`；canonical 回读**只有一条权威判据**（盘上逐字等于已验证 payload；结构/尺寸判据在 `_read_candidate` 已跑完，逐字相等即蕴含合法，重复判定即死 guard）-> `post_install_readback_failure_preserves_residue_and_never_unlinks`(3)；candidate 的「header 读不到」「header 不比 720」「body 不可解析」三条判据各对应一条独立矩阵腿。
- R5 结构与账面：结构匹配 fake 通过 `isinstance(_, RecoveryRunner)` + 逐字签名与 keyword-only -> `public_seam_shape_is_frozen`；包 `__all__` 再导出两个 seam + R1-R8 偏离清单 -> `tracker_package_reexports_the_recovery_seam_and_documents_deviations`；零 manifest / outcome + runner 恰一次 -> `success_writes_no_manifest_or_outcome_file`。
- 已知缺口（fixture「Known limits」承接，不在此重测）：提交计数（#26）、真实 SHUD argv/日志/walltime（#26/M4）、`state_checkpoints.json` / outcome JSON / `DONE`（明示不做）、多目标 / timeout / backoff（产品固定 `[12]`）。
"""

from __future__ import annotations

import hashlib
import inspect
import os
import pathlib
import stat
from collections.abc import Callable
from dataclasses import replace

import pytest
from assembly_fixtures import PARAMETER_EXPECTED, prepared, run_assemble
from cfg_ic_fixtures import build_cfg_ic

from yd_producer import state
from yd_producer.assemble import RunDirectory
from yd_producer.forcing.direct_grid_contract import MAX_DIRECT_GRID_STATION_BINDINGS
from yd_producer.store import safe_fs
from yd_producer.tracker import (
    CapturedCheckpoint,
    CheckpointTracker,
    RecoveryRunner,
    TrackerError,
    ensure_twelve_hour_checkpoint,
)
from yd_producer.tracker import checkpoint_tracker as tracker_module

PROJECT = "demo"
ROOT = "state_checkpoint_recovery"
CANDIDATE = f"{PROJECT}.cfg.ic.update"
OUTSIDE = "outside.cfg.ic.update"
_HOURS = (12,)
_RAISE = "raise"
#: 曾经存在、本轮被删除的 forcing 业务上限；只用它构造「比它大」的合法输入。
_REMOVED_CAP = 8 * 1024 * 1024


def _payload(minute: str, *, mesh: int = 3, river: int = 2) -> bytes:
    """合成 `cfg.ic`（独立生成器，非解析器回读）：结构合法、header 由 `minute` 决定。"""
    return build_cfg_ic(mesh_count=mesh, river_count=river, minute=minute).payload


def _distinctive_body() -> bytes:
    """与主跑固定表**不同构**的合法状态（`mesh=2`）：证明采纳的是 candidate 本体。"""
    return _payload("720.000000", mesh=2)


def _other_valid_body() -> bytes:
    """另一份 header/body 都合法、字节不同的状态：只有「字节比对」这条 gate 拦得住。"""
    return _payload("720.000000", mesh=4)


def _header_1440() -> bytes:
    """结构完整、header 是 1440（更晚时刻）：`parse` 会成功，只有 header gate 拦得住。"""
    payload = _payload("1440.000000", mesh=2)
    state.parse(payload)  # 校准：结构合法 -> header gate 是唯一的拦截者
    return payload


def _truncated(minute: str = "720.000000") -> bytes:
    built = build_cfg_ic(mesh_count=2, river_count=2, minute=minute)
    removed = {built.mesh_data_indices[-1], built.river_data_indices[-1]}
    kept = "".join(line for i, line in enumerate(built.lines) if i not in removed)  # fmt: skip
    payload = kept.encode("utf-8")
    with pytest.raises(ValueError):
        state.parse(payload)
    return payload


def _tracker(root: pathlib.Path, hours: tuple[int, ...] = _HOURS) -> CheckpointTracker:
    return CheckpointTracker(run_dir=root, project_name=PROJECT, checkpoint_hours=hours)


def _write(path: pathlib.Path, payload: bytes) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _setup(tmp_path: pathlib.Path) -> tuple[RunDirectory, CheckpointTracker]:
    rd = run_assemble(prepared(tmp_path))  # oracle 与主跑同一接线
    return rd, _tracker(rd.path)


def _canonical(tracker: CheckpointTracker) -> pathlib.Path:
    return tracker.checkpoint_dir / f"{PROJECT}.f012.cfg.ic.update"


def _root_of(rd: RunDirectory) -> pathlib.Path:
    return rd.path.parent / ROOT


def _produced(rd: RunDirectory) -> pathlib.Path:
    """补跑输出目录（`<work>/state_checkpoint_recovery/f012`）里的精确 candidate 路径。"""
    return rd.path.parent / ROOT / "f012" / CANDIDATE


def _digest_tree(root: pathlib.Path) -> dict[str, tuple[str, bytes]]:
    """树的对账指纹：形态 + 内容摘要；键集合相等 = 没多没少，值相等 = 类型没换、bytes 没变。一律 `lstat`（`read_bytes` 会跟随符号链接），故残留形态可判。"""
    out: dict[str, tuple[str, bytes]] = {}
    for path in sorted(root.rglob("*")) if root.exists() else []:
        rel, mode = str(path.relative_to(root)), path.lstat().st_mode
        if stat.S_ISLNK(mode):
            out[rel] = ("symlink", os.readlink(path).encode())
        elif stat.S_ISDIR(mode):
            out[rel] = ("directory", b"")
        elif stat.S_ISREG(mode):
            out[rel] = ("file", hashlib.sha256(path.read_bytes()).digest())
        else:
            out[rel] = (f"mode:{stat.S_IFMT(mode)}", b"")
    return out


def _plant(path: pathlib.Path, shape: str, outside_root: pathlib.Path) -> None:
    """在**精确路径原位**换成外来普通文件 / 目录 / 指向外部的符号链接（不新建别的文件、不改字段）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if shape == "regular":
        return path.write_bytes(b"foreign regular bytes\n")
    if shape == "directory":
        return path.mkdir()
    outside = outside_root / f"foreign-{path.name}"
    outside.write_bytes(b"foreign bytes\n")
    path.symlink_to(outside)


#: 补跑成功后 run dir 里唯一允许新增的两条 entry（其余必须逐字节原样）。
_ADDED = {"state_checkpoints", f"state_checkpoints/{PROJECT}.f012.cfg.ic.update"}


def _assert_only_canonical_added(before: dict, after: dict) -> None:
    """前后对账：只新增 canonical 与其目录，parameter 已恢复原 bytes，其余一字不动。"""
    assert set(after) - set(before) == _ADDED
    kept = {key: value for key, value in before.items() if key not in _ADDED}
    assert {key: after[key] for key in kept} == kept
    para = hashlib.sha256(PARAMETER_EXPECTED).digest()
    assert after[f"{PROJECT}.para"] == ("file", para)  # C.3：finally 已恢复原 bytes


def _record_unlink(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """记录 `unlink_no_follow` 调用点（所有权推断式清理的唯一可观测痕迹）。"""
    real, calls = safe_fs.unlink_no_follow, []

    def probe(path, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(str(path))
        return real(path, **kwargs)

    monkeypatch.setattr(safe_fs, "unlink_no_follow", probe)
    return calls


def _ensure(tracker: CheckpointTracker, rd: RunDirectory, runner) -> CapturedCheckpoint:
    return ensure_twelve_hour_checkpoint(tracker=tracker, run_directory=rd, runner=runner)  # fmt: skip


class _Runner:
    """结构匹配的 fake runner（各用例的业务 oracle 由闭包 / 基类提供）。"""

    def __init__(
        self,
        *,
        rc: object = 0,
        write_candidate: bool = True,
        candidate: bytes | None = None,
        raise_error: Exception | None = None,
        on_call: Callable[..., None] | None = None,
    ):
        self.calls, self.rc, self.write_candidate = 0, rc, write_candidate
        self.candidate, self.raise_error, self.on_call = candidate, raise_error, on_call

    def __call__(self, *, run_directory, output_dir) -> int:
        self.calls += 1
        if self.on_call is not None:
            self.on_call(run_directory, output_dir)
        if self.write_candidate:
            (output_dir / f"{run_directory.project_name}.cfg.ic.update").write_bytes(
                self.candidate if self.candidate is not None else _payload("720.000000")
            )
        if self.raise_error is not None:
            raise self.raise_error
        return self.rc  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("tracker", object()),
        ("run_directory", object()),
        ("runner", 42),
        ("runner", None),
        ("runner", "runner"),
    ],
)
def test_wrong_arg_types_are_rejected_before_any_io(
    tmp_path: pathlib.Path, field: str, wrong
) -> None:
    rd, tracker = _setup(tmp_path)
    runner = _Runner(write_candidate=False)
    before = _digest_tree(rd.path.parent)
    good = {"tracker": tracker, "run_directory": rd, "runner": runner}
    good[field] = wrong
    with pytest.raises(TrackerError):
        ensure_twelve_hour_checkpoint(**good)  # type: ignore[arg-type]
    assert runner.calls == 0 and _digest_tree(rd.path.parent) == before
    assert not _root_of(rd).exists()


@pytest.mark.parametrize("hours", [(720,), (6,), (6, 12), (12, 24)])
def test_runner_is_never_called_for_non_twelve_targets(
    tmp_path: pathlib.Path, hours: tuple[int, ...]
) -> None:
    """分钟/小时混淆与多目标在 runner 0 调用、0 文件写入时拒绝（A.1、9.2 早拒绝）。  `(6, 12)` / `(12, 24)` 两支钉的是「恰为 `(12,)`」而不是「含 12」：把判据放宽成成员 检测后这两支会走到创建 recovery root，故 `targets-exactness-widened` 腿可杀。"""
    rd, tracker = _setup(tmp_path)
    wrong = _tracker(rd.path, hours)
    assert wrong.targets != tracker.targets == _HOURS
    runner = _Runner(write_candidate=False)
    before_param = rd.parameter_path.read_bytes()
    before_tree = _digest_tree(rd.path.parent)
    with pytest.raises(TrackerError):
        _ensure(wrong, rd, runner)
    assert runner.calls == 0 and not _root_of(rd).exists()
    assert rd.parameter_path.read_bytes() == before_param
    assert _digest_tree(rd.path.parent) == before_tree


def _legal_tree(rd: RunDirectory, work: str, leaf: str) -> RunDirectory:
    """以 `rd` 的 identity 为样板另造一棵 `<work>/<leaf>` 合法静态输入树（identity 借自真 `assemble()` 产物，两支之间唯一变量是目录名与它属于哪个 tracker）。"""
    root = rd.path.parent.parent / work / leaf
    root.mkdir(parents=True)
    made = replace(
        rd,
        path=root,
        state_path=root / f"{PROJECT}.cfg.ic",
        parameter_path=root / f"{PROJECT}.para",
        forcing_index_path=root / f"{PROJECT}.tsd.forc",
        forcing_csv_paths=(root / "X1.csv",),
    )
    for path, raw in (
        (made.state_path, _payload("360.000000")),
        (made.parameter_path, PARAMETER_EXPECTED),
        (made.forcing_index_path, b"index\n"),
        (made.forcing_csv_paths[0], b"csv\n"),
    ):
        path.write_bytes(raw)
    return made


@pytest.mark.parametrize(
    ("leaf", "tracker_leaf"),
    # `model-old` 支封「相等判据放宽成子串包含」：目录名含 `model` 却不等于 `model`。
    [("elsewhere", "elsewhere"), ("model-old", "model-old"), ("model", "other-model")],
)
def test_run_directory_must_be_the_trackers_own_model_directory(
    tmp_path: pathlib.Path, leaf: str, tracker_leaf: str
) -> None:
    """A.2 的两条身份 guard（三支见证）：目录名 MUST **等于** `model`，且 MUST 等于 `tracker.run_dir`。MUST NOT 用 `replace(rd, path=...)` 验它们——那会先被别的字段比对拦住，杀不到目标 guard。`elsewhere` 支：tracker 与四个字段一起指向 `<work>/elsewhere`，只有目录名不对；`model-old` 支同样整棵自洽、只有名字含 `model` 而不等于它；`other-model` 支：`RunDirectory.path` 是一棵合法的 `<work-A>/model`，tracker 指向**另一棵**同样合法的树，只有相等性不成立。三支都要求 runner 0 调用、root 0 创建、目录内容一字未动。"""
    base, _ = _setup(tmp_path)
    forged = _legal_tree(base, "work", leaf)
    twin = forged.path if leaf == tracker_leaf else _legal_tree(base, "other", tracker_leaf).path  # fmt: skip
    runner = _Runner(write_candidate=False)
    with pytest.raises(TrackerError):
        _ensure(_tracker(twin), forged, runner)
    assert runner.calls == 0 and not (forged.path.parent / ROOT).exists()
    # fmt: off
    assert {p.name for p in forged.path.iterdir()} == {f"{PROJECT}.cfg.ic",
        f"{PROJECT}.para", f"{PROJECT}.tsd.forc", "X1.csv"}
    # fmt: on


#: A.2 伪造表：名字 -> 交给 `replace(rd, **kwargs)` 的关键字实参；名字集合与
#: `_FORGED_NAMES` 逐支对齐（矩阵用例断言，漏一支即假覆盖）。
def _forged(rd: RunDirectory) -> dict[str, dict[str, object]]:
    path, first, tail = rd.path, rd.forcing_csv_paths[0], rd.forcing_csv_paths[1:]
    rel = pathlib.Path("relative/model")
    # fmt: off
    over = tuple(path / f"S{i:04d}.csv" for i in range(MAX_DIRECT_GRID_STATION_BINDINGS + 1))
    # fmt: on
    # fmt: off
    # 22 支伪造表两支一行：表密度是 1000 行闸与「不削结账表」之间唯一的余量来源，展开成
    # 每支一行就会把本文件挤出闸门。这是排版选择，不是 lint 豁免（与下面 `# fmt: on` 配对）。
    return {
        "path-relative": {"path": rel, "state_path": rel / "x"}, "path-mismatch": {"path": path / "other"},
        "path-absent": {"path": path.parent / "nope"}, "project-mismatch": {"project_name": "other"},
        "identity-project-mismatch": {"identity": replace(rd.identity, project_name="other")}, "state-outexact": {"state_path": path / "diff.cfg.ic"},
        "parameter-outexact": {"parameter_path": path / "diff.para"}, "index-outexact": {"forcing_index_path": path / "diff.tsd.forc"},
        "state-symlink": {"state_path": path / "sym.cfg.ic"}, "state-dir": {"state_path": path / "dir-state"},
        "parameter-symlink": {"parameter_path": path / "sym.para"}, "parameter-dir": {"parameter_path": path / "dir-parameter"},
        "index-symlink": {"forcing_index_path": path / "sym.tsd.forc"}, "index-dir": {"forcing_index_path": path / "dir-index"},
        "csv-nested": {"forcing_csv_paths": (path / "sub" / "X1.csv", *tail)}, "csv-unsafe-basename": {"forcing_csv_paths": (path / "bad;name.csv", *tail)},
        "csv-empty": {"forcing_csv_paths": ()}, "csv-duplicate": {"forcing_csv_paths": (first, first)},
        "csv-casefold-duplicate": {"forcing_csv_paths": (path / "X1.CSV", first)}, "csv-over-cap": {"forcing_csv_paths": over},
        "csv-symlink": {"forcing_csv_paths": (path / "csv-sym.csv",)}, "csv-dir": {"forcing_csv_paths": (path / "dir-csv",)},
    }
    # fmt: on


_FORGED_NAMES = """
csv-casefold-duplicate csv-dir csv-duplicate csv-empty csv-nested csv-over-cap
csv-symlink csv-unsafe-basename identity-project-mismatch index-dir index-outexact
index-symlink parameter-dir parameter-outexact parameter-symlink path-absent
path-mismatch path-relative project-mismatch state-dir state-outexact state-symlink
""".split()  # noqa: SIM905 —— 22 个表名紧凑成一段，展开成字面量只为讨好规则


@pytest.mark.parametrize("name", _FORGED_NAMES)
def test_forged_run_directory_matrix(tmp_path: pathlib.Path, name: str) -> None:
    """A.2 的伪造面家族：任何一支都在 runner 0 调用、recovery root 0 创建时拒绝。"""
    rd, tracker = _setup(tmp_path)
    for junk in ("bad;name.csv", "X1.CSV", *(f"S{i:04d}.csv" for i in range(5))):
        (rd.path / junk).write_bytes(b"1\n")
    for dirname in ("dir-state", "dir-parameter", "dir-index", "dir-csv"):
        (rd.path / dirname).mkdir()
    for link, target in (
        ("sym.cfg.ic", rd.state_path),
        ("sym.para", rd.parameter_path),
        ("sym.tsd.forc", rd.forcing_index_path),
        ("csv-sym.csv", rd.forcing_csv_paths[0]),
    ):
        (rd.path / link).symlink_to(target)
    forged = _forged(rd)
    assert set(forged) == set(_FORGED_NAMES), "伪造表与参数表必须逐支对齐"
    runner = _Runner(write_candidate=False)
    with pytest.raises(TrackerError):
        _ensure(tracker, replace(rd, **forged[name]), runner)
    assert runner.calls == 0 and not _root_of(rd).exists()


def test_initial_state_must_be_parseable(tmp_path: pathlib.Path) -> None:
    rd, tracker = _setup(tmp_path)
    rd.state_path.write_bytes(b"2 2 0\n1 0.1\n2 0.2\n")  # 非原生分段布局
    runner = _Runner(write_candidate=False)
    with pytest.raises(TrackerError):
        _ensure(tracker, rd, runner)
    # 快照发生在 recovery root 创建之后；初态拒绝时 root 已作为失败证据保留。
    assert runner.calls == 0 and _root_of(rd).is_dir()


def test_long_forcing_basename_is_accepted(tmp_path: pathlib.Path) -> None:
    """删除业务长度 cap 的正面证据：取 `os.pathconf(dir, "PC_NAME_MAX")`（POSIX 保证 >= 14，Linux/macOS 均 255）的合法 basename 不再被拒；核心断言是「业务 cap 不存在」，不是「OS cap 可突破」，故不谎称 200+ 在所有平台有效。"""
    rd, tracker = _setup(tmp_path)
    limit = os.pathconf(rd.path, "PC_NAME_MAX")
    name = "L" * (min(limit, 205) - 4) + ".csv"
    assert len(name) <= limit and (len(name) > 200 or limit < 205)
    long_csv = rd.path / name
    long_csv.write_bytes(b"1\t6\t20260507\t20260507\nTime_Day\n0\t1\n")
    forged = replace(rd, forcing_csv_paths=(long_csv, rd.forcing_csv_paths[1]))
    runner = _Runner(candidate=_distinctive_body())
    record = _ensure(tracker, forged, runner)
    assert runner.calls == 1 and record.path.read_bytes() == _distinctive_body()
    assert rd.parameter_path.read_bytes() == PARAMETER_EXPECTED


@pytest.mark.parametrize(
    "mutate",
    [
        None,
        lambda r: replace(r, lead_hours=24),
        lambda r: replace(r, relative_minute=1440.0),
        lambda r: replace(r, path=pathlib.Path("/elsewhere/x")),
        lambda r: replace(r, source_name="other.cfg.ic.update"),
        lambda r: replace(r, checksum="0" * 64),
    ],
    ids=["valid-record", "lead-hours", "relative-minute", "path", "source", "checksum"],
)
def test_captured_record_is_the_only_authority(tmp_path: pathlib.Path, mutate) -> None:
    """B.1：valid 记录原样返回、runner 0 调用；五字段任一漂移 fail closed 且不动证据。"""
    rd, tracker = _setup(tmp_path)
    payload = _write(tracker.source_path, _payload("720.000000"))
    tracker.capture_available()
    record = tracker.captured[12]
    forged = None if mutate is None else mutate(record)
    if forged is not None:
        tracker._captured[12] = forged
    runner = _Runner(write_candidate=False)
    if forged is None:
        assert _ensure(tracker, rd, runner) is record
    else:
        with pytest.raises(TrackerError):
            _ensure(tracker, rd, runner)
        assert tracker.captured[12] == forged  # 记录不被覆盖、证据不被删除
    assert runner.calls == 0 and not _root_of(rd).exists()
    assert _canonical(tracker).read_bytes() == payload


@pytest.mark.parametrize(
    ("gate", "content"),
    [
        # 记录仍是旧摘要：只有 checksum gate 命中。
        ("checksum", _other_valid_body),
        # 记录摘要同步更新 -> checksum 通过，单独咬住 header / body gate。
        ("header", _header_1440),
        ("body", _truncated),
    ],
)
def test_captured_point_of_use_gates_are_independent(
    tmp_path: pathlib.Path, gate: str, content
) -> None:
    """B.1 回读的三条 gate 各有独立见证：不存在前置 gate 掩盖后置 gate 的假覆盖。"""
    rd, tracker = _setup(tmp_path)
    _write(tracker.source_path, _payload("720.000000"))
    tracker.capture_available()
    replacement = content()
    canonical = _canonical(tracker)
    canonical.write_bytes(replacement)
    if gate != "checksum":  # 让 checksum gate 先行通过，把断言压到目标 gate 上
        digest = hashlib.sha256(replacement).hexdigest()
        tracker._captured[12] = replace(tracker.captured[12], checksum=digest)
    runner = _Runner(write_candidate=False)
    with pytest.raises(TrackerError) as captured:
        _ensure(tracker, rd, runner)
    assert runner.calls == 0 and gate in str(captured.value), str(captured.value)
    assert canonical.read_bytes() == replacement  # 证据不被删/覆盖


@pytest.mark.parametrize("where", ["canonical", "root"])
@pytest.mark.parametrize("shape", ["regular", "directory", "symlink"])
def test_preexisting_residue_is_never_touched(
    tmp_path: pathlib.Path, where: str, shape: str
) -> None:
    """B.2 / R5：canonical 与 recovery root 的三种既有形态都是未验证残留，原树不变。"""
    rd, tracker = _setup(tmp_path)
    victim = _canonical(tracker) if where == "canonical" else _root_of(rd)
    if shape == "regular":
        _write(victim, b"unverified\n")
    elif shape == "symlink":
        _plant(victim, "symlink", tmp_path)
    else:
        (victim / "f012").mkdir(parents=True)
    before = _digest_tree(rd.path.parent)
    runner = _Runner(write_candidate=False)
    with pytest.raises(TrackerError):
        _ensure(tracker, rd, runner)
    assert runner.calls == 0 and _digest_tree(rd.path.parent) == before


@pytest.mark.parametrize("shape", ["directory", "symlink"])
@pytest.mark.parametrize("target", ["state", "parameter", "index", "csv", "candidate"])
def test_nonregular_shape_matrix(
    tmp_path: pathlib.Path, target: str, shape: str
) -> None:
    """证据 6：在**精确路径原位**换形状，而不是 `replace(rd, state_path=...)`。  后者只杀 exact-path 字段比对，永远走不到 `_require_regular` / candidate 的 nonregular 分支——那半条 guard 无见证即假覆盖。静态输入的形态校验按冻结次序发生在 recovery root 创建**之前**（root MUST 不存在）；candidate 只在 runner 之后存在（root MUST 在）。 FIFO / socket 不在 `assemble()` 的产出形态域内，本用例不声明覆盖它们。"""
    rd, tracker = _setup(tmp_path)
    # fmt: off
    victim = {"state": rd.state_path, "parameter": rd.parameter_path,
              "index": rd.forcing_index_path, "csv": rd.forcing_csv_paths[0],
              "candidate": _produced(rd)}[target]
    # fmt: on
    pre = target != "candidate"

    def on_call(_run_directory, output_dir):
        if not pre:
            # 先按 fake SHUD 的正常产出写一份，再在同一精确路径原位换形状。
            (output_dir / CANDIDATE).write_bytes(_distinctive_body())
            (output_dir / CANDIDATE).unlink()
            _plant(output_dir / CANDIDATE, shape, tmp_path)

    runner = _Runner(write_candidate=pre, on_call=on_call)
    if pre:
        victim.unlink()
        _plant(victim, shape, tmp_path)
    with pytest.raises(TrackerError):
        _ensure(tracker, rd, runner)
    assert runner.calls == (0 if pre else 1)
    assert _root_of(rd).exists() == (not pre) and not _canonical(tracker).exists()
    if not pre:
        assert victim.is_dir() if shape == "directory" else victim.is_symlink()


@pytest.mark.parametrize("preexisting", [False, True])
def test_capture_no_clobber_and_valid_copy(
    tmp_path: pathlib.Path, preexisting: bool
) -> None:
    """B.3：O_EXCL 替换后捕获主路径不回归，且预存的 canonical 绝不被动。"""
    tracker = _tracker(tmp_path)
    payload = _write(tracker.source_path, _payload("720.000000"))
    residue, canonical = b"residue entry\n", _canonical(tracker)
    if preexisting:
        _write(canonical, residue)
    tracker.capture_available()
    if preexisting:
        assert tracker.missing_hours() == (12,) and dict(tracker.captured) == {}
        assert canonical.read_bytes() == residue
    else:
        assert tracker.missing_hours() == ()
        assert tracker.captured[12].path.read_bytes() == payload
        assert tracker.captured[12].checksum == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("site", ["open", "source-read"])
@pytest.mark.parametrize("shape", ["regular", "directory", "symlink"])
def test_capture_never_unlinks_a_canonical_entry(tmp_path: pathlib.Path, site: str, shape: str, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """`_capture` 从不按路径名删除 canonical（#17 R3）：创建前、创建期与创建后的任何失败都不许产生删除尝试。实测三种外来形态（普通文件 / 目录 / 指向外部文件的符号链接）下 `O_EXCL` 的 open 一律先抛 `FileExistsError`（EEXIST 早于 EACCES / ELOOP / EISDIR），故「创建期失败」与「外来 entry 在场」恒同时成立；`open` 支封「把 `FileExistsError` 并进 `_FS_FAILURES` 后探测式清理」，`source-read` 支封「在源读失败分支里补一次清理」。`regular` 形态是让删除**真的可观测**的那一支：目录 / 符号链接上 `safe_fs` 自己就会拒绝，删不删都看不出差别。共同判据是零删除尝试 + 外来 bytes / 形态原样 + 该小时如实 missing。"""
    calls = _record_unlink(monkeypatch)
    tracker = _tracker(tmp_path)
    _write(tracker.source_path, _payload("720.000000"))
    _plant(_canonical(tracker), shape, tmp_path)
    if site == "source-read":  # 只让 `_capture` 里那次源读失败（观测的读必须放过）
        real, reads = safe_fs.read_bytes_limited_no_follow, []

        def _fail(path, **kw):  # type: ignore[no-untyped-def]
            if str(path) == str(tracker.source_path):
                reads.append(path)  # 第 1 次属于观测步骤，第 2 次才在创建之前
                if len(reads) > 1:
                    raise PermissionError(13, "source unreadable")
            return real(path, **kw)

        monkeypatch.setattr(safe_fs, "read_bytes_limited_no_follow", _fail)
        assert len(reads) == 0
    tracker.capture_available()
    assert tracker.missing_hours() == (12,)
    foreign = _digest_tree(tmp_path)
    assert dict(tracker.captured) == {} and calls == []  # 零删除尝试
    assert _digest_tree(tmp_path) == foreign  # 外来 bytes / 形态原样


def test_install_open_failure_behind_symlinked_dir_never_unlinks(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """安装 open 抛 `SafeFilesystemError`（非 `FileExistsError`）且外来 entry 在场时一次都不许删：竞争者在 runner 期间把 `state_checkpoints` 换成指向「已含规范名文件」的外来目录的符号链接，`safe_fs` 在符号链接组件上直接拒绝，于是「本调用什么都没建过」与「canonical 解析到外来 entry」同时成立；探测式清理（把 `_FS_FAILURES` 也接上 best-effort unlink）会在这条 entry 上留下删除尝试，即使 `safe_fs` 同样拒绝跟随、外来 bytes 无损。"""
    calls = _record_unlink(monkeypatch)
    rd, tracker = _setup(tmp_path)
    canonical = _canonical(tracker)

    def hijack(_run_directory, _output_dir):
        elsewhere = _run_directory.path.parent / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / canonical.name).write_bytes(b"foreign bytes\n")
        canonical.parent.mkdir(parents=True)  # 先让本调用真的建过这个目录，再换掉它
        canonical.parent.rmdir()
        canonical.parent.symlink_to(elsewhere)

    with pytest.raises(TrackerError) as captured:
        _ensure(tracker, rd, _Runner(candidate=_distinctive_body(), on_call=hijack))
    assert "install" in str(captured.value), str(captured.value)
    target = rd.path.parent / "elsewhere" / canonical.name
    assert calls == [] and target.read_bytes() == b"foreign bytes\n"  # 零删除尝试


def test_install_race_keeps_the_foreign_bytes(tmp_path: pathlib.Path) -> None:
    rd, tracker = _setup(tmp_path)
    canonical, foreign = _canonical(tracker), b"foreign pre-entry bytes\n"

    def on_call(_run_directory, _output_dir):
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(foreign)

    with pytest.raises(TrackerError):
        _ensure(tracker, rd, _Runner(candidate=_distinctive_body(), on_call=on_call))  # fmt: skip
    assert canonical.read_bytes() == foreign and dict(tracker.captured) == {}


@pytest.mark.parametrize("lane", ["read", "replacement-valid", "replacement-torn"])
def test_post_install_readback_failure_preserves_residue_and_never_unlinks(tmp_path: pathlib.Path, lane: str, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """D.4/B.4（#17 R3 追加）：安装**之后**的三条失败 lane 一律「不删、不采纳、整轮失败」。

    三条 lane：`read` = 回读自身失败；另两条 = O_EXCL 返回之后、回读之前把 canonical 换成
    **另一份同样合法**的状态 / 换成**结构坏掉**的状态。安装后的唯一权威判据是「盘上逐字等于
    已验证的 payload」（payload 在 `_read_candidate` 已过 header/body/尺寸全套判据，逐字相等
    即蕴含合法，此处再判一遍属死 guard），故后两支由同一条比对拦住——合法但不同的那份本来
    任何结构判据都拦不住，逐字比对是唯一的拦截者。三条 lane 都在 O_EXCL 成功返回之后，而该
    返回只证明「那一刻本调用创建了 entry」、不证明「路径名现在指向的还是它」：按名删除就是删
    外来 bytes，而 `safe_fs` 无 compare-and-unlink 且不在本 issue 变更面内 ⇒ 唯一可证明的处置
    是零删除尝试 + 保留 residue + `_captured` 为空（整棵 work 归 #26）。
    """
    calls = _record_unlink(monkeypatch)
    rd, tracker = _setup(tmp_path)
    canonical = _canonical(tracker)
    replacement = _other_valid_body() if lane.endswith("valid") else _truncated()
    if lane == "read":
        real = safe_fs.read_bytes_limited_no_follow

        def failing_read(path, **kwargs):  # type: ignore[no-untyped-def]
            if "state_checkpoints" in str(path):
                raise PermissionError(13, "io error after install")
            return real(path, **kwargs)

        monkeypatch.setattr(safe_fs, "read_bytes_limited_no_follow", failing_read)
    else:
        real_write = safe_fs.write_bytes_no_follow_exclusive

        def swapping_write(path, content, **kwargs):  # type: ignore[no-untyped-def]
            result = real_write(path, content, **kwargs)
            canonical.write_bytes(replacement)  # 安装成功之后、回读之前替换
            return result

        monkeypatch.setattr(safe_fs, "write_bytes_no_follow_exclusive", swapping_write)
    with pytest.raises(TrackerError) as captured:
        _ensure(tracker, rd, _Runner(candidate=_distinctive_body()))
    assert {  # 后两支同由逐字比对拦住：换进来的合法份没有别的拦截者
        "read": "readback failed",
        "replacement-valid": "differs from installed",
        "replacement-torn": "differs from installed",
    }[lane] in str(captured.value), captured.value
    assert dict(tracker.captured) == {} and calls == []  # 无 authority、零删除尝试
    # residue 原样留在盘上：回读失败 lane 读的是本调用装进去的那份，另两条读的是替换份。
    expected = _distinctive_body() if lane == "read" else replacement
    assert canonical.is_file() and canonical.read_bytes() == expected


def test_cross_attempt_torn_source_residue_is_not_adopted(
    tmp_path: pathlib.Path,
) -> None:
    """tracker A 捕获 valid；B 看到 torn 720 source：B 无 authority 也不删 A 的副本。"""
    rd, tracker_a = _setup(tmp_path)
    payload = _write(tracker_a.source_path, _payload("720.000000"))
    tracker_a.capture_available()
    canonical = _canonical(tracker_a)
    tracker_b = _tracker(rd.path)
    _write(tracker_b.source_path, _truncated("720.000000"))
    tracker_b.capture_available()
    runner = _Runner(write_candidate=False)
    with pytest.raises(TrackerError):
        _ensure(tracker_b, rd, runner)
    assert runner.calls == 0  # 文件名存在从来不是 authority
    assert canonical.read_bytes() == payload and dict(tracker_b.captured) == {}


def test_genuine_miss_success_installs_canonical_record(tmp_path: pathlib.Path) -> None:
    rd, tracker = _setup(tmp_path)
    observed: dict[str, object] = {}
    before_state, before_index = rd.state_path.read_bytes(), rd.forcing_index_path.read_bytes()  # fmt: skip
    before_csvs = tuple(p.read_bytes() for p in rd.forcing_csv_paths)

    def on_call(run_directory, output_dir):
        observed.update(
            run_directory=run_directory,
            output_dir=output_dir,
            parameter=run_directory.parameter_path.read_bytes(),
            state=run_directory.state_path.read_bytes(),
            index=run_directory.forcing_index_path.read_bytes(),
            csv=run_directory.forcing_csv_paths[0].read_bytes(),
        )

    distinctive = _distinctive_body()
    before_tree = _digest_tree(rd.path)
    runner = _Runner(candidate=distinctive, on_call=on_call)
    record = _ensure(tracker, rd, runner)

    # D.1：恰一次，关键字实参逐字是原 `RunDirectory` 与 fresh `.../<work>/f012`。
    assert runner.calls == 1 and observed["run_directory"] is rd
    assert observed["output_dir"] == _root_of(rd) / "f012"
    params = observed["parameter"]
    assert isinstance(params, bytes)
    assert b"END = 0.5" in params and b"START = 0" in params
    assert b"Update_IC_STEP = 720" in params  # C.2：窗口内只 END 变、STEP 仍 720
    # 记录（D.4）：body 承接 candidate 而非由 END=0.5 合成；checksum 取 canonical 回读。
    assert isinstance(record, CapturedCheckpoint)
    assert (record.lead_hours, record.relative_minute) == (12, 720.0)
    assert record.path == _canonical(tracker) and record.path.read_bytes() == distinctive  # fmt: skip
    assert record.source_name == CANDIDATE
    assert record.checksum == hashlib.sha256(distinctive).hexdigest()
    assert tracker.captured[12] == record and tracker.missing_hours() == ()
    # 输入原样、参数恢复、recovery tree 留证（由 whole-work owner 回收）。
    assert rd.parameter_path.read_bytes() == PARAMETER_EXPECTED
    assert rd.state_path.read_bytes() == before_state
    assert rd.forcing_index_path.read_bytes() == before_index
    assert tuple(p.read_bytes() for p in rd.forcing_csv_paths) == before_csvs
    assert _produced(rd).is_file()
    _assert_only_canonical_added(before_tree, _digest_tree(rd.path))  # C.3


def test_parameter_is_the_only_expected_change(tmp_path: pathlib.Path) -> None:
    """另一棵装配树上复核恢复链：`finally` 之后 parameter 逐字等同主跑 bytes，且只新增 canonical。与 `genuine_miss_success` 同一条链，但这里快照对账独立发生，故「改写过没恢复」与「多写了别的文件」各有独立失败点。"""
    rd, tracker = _setup(tmp_path)
    before = _digest_tree(rd.path)
    _ensure(tracker, rd, _Runner())
    _assert_only_canonical_added(before, _digest_tree(rd.path))


def test_large_forcing_csv_above_the_removed_cap_succeeds(
    tmp_path: pathlib.Path,
) -> None:
    """forcing **没有**业务体积上限：>8 MiB 的合法 CSV 必须走完整成功路径。  数据按行分块拼接（每块约 1 MiB，共 9 块），不是一次性无意义的超大对象复制；摘要走 descriptor-bound 流式路径，峰值内存与文件大小无关。留着被删 cap 的对齐实现必红。"""
    rd, tracker = _setup(tmp_path)
    victim = rd.forcing_csv_paths[0]
    big = b"1\t6\t20260507\t20260507\nTime_Day\tPrecip\n" + (b"0\t1\n" * 262144) * 9
    assert len(big) > _REMOVED_CAP
    victim.write_bytes(big)
    digest_before, seen = hashlib.sha256(big).hexdigest(), {}

    def on_call(run_directory, output_dir):
        raw = run_directory.forcing_csv_paths[0].read_bytes()
        seen["during"] = hashlib.sha256(raw).hexdigest()

    runner = _Runner(candidate=_distinctive_body(), on_call=on_call)
    record = _ensure(tracker, rd, runner)
    assert runner.calls == 1 and seen["during"] == digest_before
    assert victim.read_bytes() == big  # 内容对账通过且未被改动
    assert record.checksum == hashlib.sha256(_distinctive_body()).hexdigest()
    assert rd.parameter_path.read_bytes() == PARAMETER_EXPECTED


def test_restore_failure_blocks_adoption(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """注入第二次参数 atomic write 失败（场景：恢复原 bytes 时 ENOSPC）。"""
    rd, tracker = _setup(tmp_path)
    real_write = safe_fs.atomic_write_bytes_no_follow
    original = rd.parameter_path.read_bytes()

    def failing_restore(path, content, **kwargs):  # type: ignore[no-untyped-def]
        if content == original:
            raise safe_fs.SafeFilesystemError("no space left", kind="io")
        return real_write(path, content, **kwargs)

    monkeypatch.setattr(safe_fs, "atomic_write_bytes_no_follow", failing_restore)
    runner = _Runner(candidate=_distinctive_body())
    with pytest.raises(TrackerError):
        _ensure(tracker, rd, runner)
    assert dict(tracker.captured) == {} and not _canonical(tracker).exists()
    assert _produced(rd).is_file()  # 候选不安装、证据留盘


def test_restore_durability_failure_blocks_adoption_even_when_bytes_match(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """证据 8 的第二支：restore **已写入但落盘不确定**（盘上就是原 bytes）仍须整轮失败。这条场景挡在 `parameter-restore-bytes-recheck-off` 的比对之前——读回逐字相同，那条 gate 不会响；只有「restore 失败本身阻断采纳」这条 gate 拒绝，故两条 gate 各有独立可杀性（`parameter-restore-failure-not-blocking` / `parameter-restore-bytes-recheck-off`）。"""
    rd, tracker = _setup(tmp_path)
    original = rd.parameter_path.read_bytes()
    real_write = safe_fs.atomic_write_bytes_no_follow
    injected: list[int] = []

    def nondurable(path, content, **kwargs):  # type: ignore[no-untyped-def]
        result = real_write(path, content, **kwargs)  # 先真的写回去
        if content == original:
            injected.append(1)  # 再报告「写完但 fsync 失败」
            raise safe_fs.SafeFilesystemError("fsync failed", kind="io")
        return result

    monkeypatch.setattr(safe_fs, "atomic_write_bytes_no_follow", nondurable)
    with pytest.raises(TrackerError):
        _ensure(tracker, rd, _Runner(candidate=_distinctive_body()))
    assert injected == [1] and rd.parameter_path.read_bytes() == original
    assert dict(tracker.captured) == {} and not _canonical(tracker).exists()


def test_double_failure_keeps_both_causes_inspectable(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """D.2 双失败：runner 抛错 **且** restore 抛错时，两支失败都必须可检视，且外部只见 `TrackerError`。结构断言，不测笼统消息：外层 `TrackerError` 的 `__cause__` 逐字是 primary（其 `__cause__` 逐字是注入的 runner 异常对象），restore 异常以 note 记账且**不进入** cause 链（否则偏离 8 外泄 `SafeFilesystemError`）。"""
    rd, tracker = _setup(tmp_path)
    boom = RuntimeError("boom")
    restore_error = safe_fs.SafeFilesystemError("restore failed", kind="io")
    real_write = safe_fs.atomic_write_bytes_no_follow
    original = rd.parameter_path.read_bytes()

    def failing_restore(path, content, **kwargs):  # type: ignore[no-untyped-def]
        if content == original:
            raise restore_error
        return real_write(path, content, **kwargs)

    monkeypatch.setattr(safe_fs, "atomic_write_bytes_no_follow", failing_restore)
    runner = _Runner(candidate=_distinctive_body(), rc=None, raise_error=boom)
    with pytest.raises(TrackerError) as captured:
        _ensure(tracker, rd, runner)
    outer = captured.value
    causes, cursor = [], outer.__cause__
    while cursor is not None:
        causes.append(cursor)
        cursor = cursor.__cause__
    assert any(c is boom for c in causes), causes  # runner 链完整保留
    assert not any(isinstance(c, safe_fs.SafeFilesystemError) for c in causes), causes
    notes = " | ".join(getattr(outer, "__notes__", []))
    assert "restore failed" in notes and "SafeFilesystemError" in notes, notes
    assert runner.calls == 1 and dict(tracker.captured) == {}
    assert not _canonical(tracker).exists() and _produced(rd).read_bytes() == _distinctive_body()  # fmt: skip


def test_restore_readback_must_match_the_original_bytes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C.3：restore 之后 MUST 逐字读回原 bytes；读回与快照不一致就不得记 authority。  注入「`finally` 写完之后的读侧看到别的字节」（第三方换掉了 parameter，或 restore 写的是 语义等价而非逐字的副本）。放宽这条比对，整条成功路径会照常走完并记下 `_captured[12]`。"""
    rd, tracker = _setup(tmp_path)
    original = rd.parameter_path.read_bytes()
    real_read = safe_fs.read_bytes_limited_no_follow
    ran: list[bool] = []

    def swapped(path, **kwargs):  # type: ignore[no-untyped-def]
        content = real_read(path, **kwargs)
        if ".para" in str(path) and ran:
            return original + b"\n"  # 同一套赋值、多一个空行：语义等价、字节不等
        return content

    patch = "read_bytes_limited_no_follow"  # fmt: skip
    monkeypatch.setattr(safe_fs, patch, swapped)  # fmt: skip
    runner = _Runner(candidate=_distinctive_body(), on_call=lambda *_a: ran.append(True))  # fmt: skip
    with pytest.raises(TrackerError) as captured:
        _ensure(tracker, rd, runner)
    assert "restore" in str(captured.value).lower()
    assert runner.calls == 1 and dict(tracker.captured) == {}
    assert not _canonical(tracker).exists()


@pytest.mark.parametrize("victim", ["state", "index", "csv"])
def test_input_drift_after_runner_blocks_adoption(
    tmp_path: pathlib.Path, victim: str
) -> None:
    rd, tracker = _setup(tmp_path)
    path, payload = {
        "state": (rd.state_path, _payload("700.000000")),
        "index": (rd.forcing_index_path, b"tampered\n"),
        "csv": (rd.forcing_csv_paths[0], b"tampered csv\n"),
    }[victim]
    index_before = rd.forcing_index_path.read_bytes()

    def tamper(_rd, _out):
        path.write_bytes(payload)

    runner = _Runner(candidate=_distinctive_body(), on_call=tamper)
    with pytest.raises(TrackerError) as captured:
        _ensure(tracker, rd, runner)
    message = str(captured.value).lower()
    assert victim in message and "changed" in message
    assert dict(tracker.captured) == {} and not _canonical(tracker).exists()
    assert rd.parameter_path.read_bytes() == PARAMETER_EXPECTED
    # 被改输入留作整轮失败证据，不由 tracker 悄悄改回。
    assert path.read_bytes() == payload
    if victim != "index":
        assert rd.forcing_index_path.read_bytes() == index_before


@pytest.mark.parametrize("outcome", [_RAISE, True, False, None, "0", 1, -1, 3, 7])
def test_runner_outcome_never_installs(tmp_path: pathlib.Path, outcome) -> None:
    """runner 抛普通异常 / 返回非 strict int / 非零：全部 `TrackerError`；非零留下的 gate-valid candidate 也不采纳（D.2），参数仍被 finally 恢复、证据留盘。"""
    rd, tracker = _setup(tmp_path)
    raised = outcome == _RAISE
    runner = _Runner(
        candidate=_distinctive_body(),
        rc=None if raised else outcome,
        raise_error=RuntimeError("boom") if raised else None,
    )
    with pytest.raises(TrackerError) as captured:
        _ensure(tracker, rd, runner)
    if raised:
        assert isinstance(captured.value.__cause__, RuntimeError)
    assert runner.calls == 1 and dict(tracker.captured) == {}
    assert not _canonical(tracker).exists()
    assert rd.parameter_path.read_bytes() == PARAMETER_EXPECTED
    assert _produced(rd).read_bytes() == _distinctive_body()


#: candidate 家族：名字 -> (在 output dir 里造出的形态, 写进去的 bytes)。
def _candidate_forms() -> dict[str, tuple[str, bytes | None]]:
    # fmt: off
    body, late = _distinctive_body(), _payload("1440.000000", mesh=2)
    return {  # 10 支形态表两支一行：同 `_forged`，是 1000 行闸的排版余量，不是 lint 豁免
        "missing": ("skip", None), "wrong-basename": ("other.cfg.ic.update", body),
        "nested-exact-name": ("sub", body), "directory": ("mkdir", b"irrelevant"),
        "symlink-to-outside": ("outside", body), "non-utf8": ("write", b"\xff\xfe\xfd"),
        "wrong-header-1440": ("write", late), "nonfinite-header": ("write", _payload("nan")),
        "truncated": ("write", _truncated()),
        "oversize": ("write", b"x" * (state.MAX_STATE_IC_BYTES + 1)),
    }
    # fmt: on


@pytest.mark.parametrize("mode", sorted(_candidate_forms()))
def test_candidate_family_is_rejected(tmp_path: pathlib.Path, mode: str) -> None:
    rd, tracker = _setup(tmp_path)
    kind, payload = _candidate_forms()[mode]

    def on_call(_run_directory, output_dir):
        candidate = output_dir / CANDIDATE
        if kind == "skip":
            return
        if kind == "other.cfg.ic.update":
            candidate = output_dir / kind
        elif kind == "sub":
            (output_dir / "sub").mkdir()
            candidate = output_dir / "sub" / CANDIDATE
        elif kind == "mkdir":
            return candidate.mkdir()
        elif kind == "outside":
            _write(output_dir.parent / OUTSIDE, _distinctive_body())
            return candidate.symlink_to(output_dir.parent / OUTSIDE)
        candidate.write_bytes(payload)

    runner = _Runner(write_candidate=False, on_call=on_call)
    with pytest.raises(TrackerError) as captured:
        _ensure(tracker, rd, runner)
    # 拒绝必须发生在 candidate gate：放宽该 gate 后改由回读 gate 拦下不算同一件事。
    assert "candidate" in str(captured.value).lower(), str(captured.value)
    assert runner.calls == 1 and dict(tracker.captured) == {}
    assert not _canonical(tracker).exists()
    assert rd.parameter_path.read_bytes() == PARAMETER_EXPECTED
    produced = _produced(rd)
    # fmt: off
    evidence = {  # 证据保留：candidate 留在 recovery tree，不被读侧改写或被删
        "truncated": lambda: produced.read_bytes() == _truncated(),
        "symlink-to-outside": lambda: (produced.parent.parent / OUTSIDE).read_bytes() == _distinctive_body(),  # 外指目标在 recovery **root**，不被读也不被删
        "missing": lambda: not produced.exists(), "directory": lambda: produced.is_dir(),
    }.get(mode, lambda: True)
    # fmt: on
    assert evidence()


def test_candidate_single_read_drives_install(tmp_path: pathlib.Path) -> None:
    rd, tracker = _setup(tmp_path)
    first, second, swapped = _distinctive_body(), _other_valid_body(), False
    read_kwargs: dict[str, object] = {}

    def on_call(_run_directory, output_dir):
        (output_dir / CANDIDATE).write_bytes(first)

    real_read = safe_fs.read_bytes_limited_no_follow

    def swapping_read(path, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal swapped
        result = real_read(path, **kwargs)
        # 首次有界读 candidate 之后、install 之前用另一份 regular 替换盘上文件。
        if ROOT in str(path) and not swapped:
            swapped, _ = True, _produced(rd).write_bytes(second)
            read_kwargs.update(kwargs)  # 逐字记录这次 candidate 读的冻结实参
        return result

    runner = _Runner(write_candidate=False, on_call=on_call)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(safe_fs, "read_bytes_limited_no_follow", swapping_read)
        record = _ensure(tracker, rd, runner)

    # 安装/记录严格对应首次已验证 bytes；第二份未验证 bytes 未被复制进 canonical。
    assert swapped and record.path.read_bytes() == first
    assert record.checksum == hashlib.sha256(first).hexdigest()
    # D.3「一次有界读」含冻结读界：实参逐字是 `MAX_STATE_IC_BYTES` + work 根 containment，
    # 读界乘 4 的放宽在内存峰值上可观测（矩阵 `candidate-read-bound-widened` 由本断言杀掉）。
    assert read_kwargs == {"max_bytes": state.MAX_STATE_IC_BYTES, "containment_root": rd.path.parent}  # fmt: skip


def test_output_dir_identity_swap_rejects_the_replacement_tree(
    tmp_path: pathlib.Path,
) -> None:
    rd, tracker = _setup(tmp_path)

    def on_call(run_directory, output_dir):
        moved = output_dir.parent / "moved"  # runner 把 f012 rename 走、以新 inode 替换
        output_dir.rename(moved)
        output_dir.mkdir()
        (output_dir / CANDIDATE).write_bytes(_distinctive_body())

    runner = _Runner(write_candidate=False, on_call=on_call)
    with pytest.raises(TrackerError) as captured:
        _ensure(tracker, rd, runner)
    assert "identity" in str(captured.value)
    assert dict(tracker.captured) == {} and not _canonical(tracker).exists()
    assert _produced(rd).read_bytes() == _distinctive_body()


def test_public_seam_shape_is_frozen() -> None:
    """`isinstance(fake, RecoveryRunner)` 是 runtime protocol 的见证；签名逐字冻结。"""
    assert isinstance(_Runner(write_candidate=False), RecoveryRunner)
    assert isinstance(object(), RecoveryRunner) is False
    params = inspect.signature(ensure_twelve_hour_checkpoint).parameters
    assert tuple(params) == ("tracker", "run_directory", "runner")
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())
    sig = inspect.signature(RecoveryRunner.__call__)
    assert tuple(sig.parameters) == ("self", "run_directory", "output_dir")
    kwonly = inspect.Parameter.KEYWORD_ONLY  # fmt: skip
    assert all(sig.parameters[n].kind is kwonly for n in ("run_directory", "output_dir"))  # fmt: skip


def test_tracker_package_reexports_the_recovery_seam_and_documents_deviations() -> None:
    import yd_producer.tracker as package  # 只有包对象带 `__all__`

    assert RecoveryRunner is tracker_module.RecoveryRunner
    assert ensure_twelve_hour_checkpoint is tracker_module.ensure_twelve_hour_checkpoint
    # R5：seam 由包对外广告；只再导出、不进 `__all__` 的放宽同样是账面回归
    assert {"RecoveryRunner", "ensure_twelve_hour_checkpoint"} <= set(package.__all__)
    doc = tracker_module.__doc__ or ""
    for marker in ("R1.", "R2.", "R3.", "R4.", "R5.", "R6.", "R7.", "R8."):
        assert marker in doc, marker


def test_success_writes_no_manifest_or_outcome_file(tmp_path: pathlib.Path) -> None:
    """**只**声明本用例能证明的两件事：runner 同步调用一次、零 manifest / outcome。  「没有第二次 scheduler submission」是 executor / `run_once` 的集成断言，归 #26；本模块 不持有 `JobExecutor`，此处不作该声明（fixture「Known limits」与清单 cap 6 行同判）。"""
    rd, tracker = _setup(tmp_path)
    runner = _Runner(candidate=_distinctive_body())
    record = _ensure(tracker, rd, runner)
    assert record is tracker.captured[12] and runner.calls == 1
    assert not (rd.path / "state_checkpoints" / "state_checkpoints.json").exists()
    assert not (_root_of(rd) / "outcome.json").exists()
