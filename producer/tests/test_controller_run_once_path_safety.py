"""`controller.run_once` 路径安全与前沿契约：发现不可读/写前 exact work 父路径/
partition 空白（supplement to evidence 5/7/11；「失败矩阵文件」接近 1000 行上限，
按语义边界拆出独立文件，不删断言）。

fixture（tasks.md `### Issue #26 fixture`）：
- Required evidence 5 附加：partition 空白（含纯空白）拒绝、带环绕空格的原值保留；
- Required evidence 7 附加：discovery unreadable -> STOPPED 非 RunError、零 residue/raw；
- Required evidence 11 附加（fix item 3）：`work_root/source` symlink 指向树外、T leaf
  缺席时 raw 前拒绝、外部/兄弟零字节变化。
"""

from __future__ import annotations

import os
import pathlib
import stat

import pytest
from run_once_fixtures import (
    CYCLE,
    JOB_NAME,
    HookState,
    InProcessDriver,
    step_clock,
    write_config_local,
    write_raw_cycle,
    write_state,
    write_variant,
)

from yd_producer import controller
from yd_producer.controller import RunError, RunOutcome, run_once
from yd_producer.executor import FakeJobExecutor, FakeOutcome, JobState


def _fake(**kwargs):
    return FakeJobExecutor(outcomes=kwargs.get("outcomes", {}), clock=step_clock())


class _CountingDriver:
    """记录型 driver：prepare/collect 调用计数 + 可编排返回/异常。"""

    def __init__(self, *, prepare_result=None, prepare_error=None, collect_error=None):
        self.prepare_calls = 0
        self.collect_calls = 0
        self.prepare_result = prepare_result
        self.prepare_error = prepare_error
        self.collect_error = collect_error

    def prepare(self, *, request):
        self.prepare_calls += 1
        if self.prepare_error is not None:
            raise self.prepare_error
        if self.prepare_result is not None:
            return self.prepare_result
        return InProcessDriver(HookState()).prepare(request=request)

    def collect(self, *, attempt, terminal_record):
        self.collect_calls += 1
        if self.collect_error is not None:
            raise self.collect_error
        raise RuntimeError("collect 未编排")


# --- Required evidence 7 附加: discovery unreadable -> STOPPED（非 RunError）------


def test_discovery_unreadable_is_stopped_not_typed_error(
    tmp_path: pathlib.Path,
) -> None:
    """`_target_and_state` 抬 `DiscoveryUnreadableError` 时收敛为本源 STOPPED。

    与既有 `decide_frontier` 契约逐字同形（必须与 alias 的 detail 一致），零
    residue/raw/work/driver/submit；不得放大成 `RunError(phase="frontier")`。
    """
    if os.geteuid() == 0:
        pytest.skip("root 无视 mode 位，chmod 0o000 无判别力")
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    root = pathlib.Path(local.yd_root)
    # `output/` 不可枚举：`_iter_entry_names` 抬 DiscoveryUnreadableError。
    output = root / "output"
    output.mkdir(parents=True)
    original = stat.S_IMODE(output.stat().st_mode) if False else 0o700
    output.chmod(0o000)
    try:
        # rawscan.judge/residue 的探针（会因 output 不可读被调用的面）全部给零调用
        # 判别器：STOPPED 路径绝不允许到达。
        import yd_producer.rawscan as rawscan_module
        import yd_producer.residue as residue_module

        probes = []
        original_judge = rawscan_module.judge
        original_plan = residue_module.plan_residue

        def probe_judge(*args, **kwargs):
            probes.append("judge")
            return original_judge(*args, **kwargs)

        def probe_plan(*args, **kwargs):
            probes.append("plan")
            return original_plan(*args, **kwargs)

        rawscan_module.judge = probe_judge  # type: ignore[assignment]
        residue_module.plan_residue = probe_plan  # type: ignore[assignment]
        try:
            driver = _CountingDriver()
            fake = _fake()
            report = run_once(
                config=config,
                local=local,
                source="gfs",
                executor=fake,
                driver=driver,
                poll_wait=lambda: None,
            )
        finally:
            rawscan_module.judge = original_judge  # type: ignore[assignment]
            residue_module.plan_residue = original_plan  # type: ignore[assignment]
    finally:
        output.chmod(original)
    assert report.outcome is RunOutcome.STOPPED
    assert report.stop_reason is controller.StopReason.DISCOVERY_UNREADABLE
    assert report.cycle is None
    assert report.job is None
    assert str(output) in report.detail
    assert probes == []
    assert driver.prepare_calls == 0
    assert fake.submissions == ()


# --- Required evidence 11: preexisting work -------------------------------------


@pytest.mark.parametrize("shape", ["dir", "file", "symlink"])
def test_preexisting_work_shape_is_rejected(tmp_path: pathlib.Path, shape: str) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    work_dir = (
        pathlib.Path(local.scratch_root).resolve() / "work" / "gfs" / "2026082612"
    )
    if shape == "dir":
        work_dir.mkdir(parents=True)
    elif shape == "file":
        work_dir.parent.mkdir(parents=True)
        work_dir.write_bytes(b"x")
    else:
        work_dir.parent.mkdir(parents=True)
        work_dir.symlink_to(tmp_path.resolve() / "outside")
    outside = tmp_path.resolve() / "outside"
    if not outside.exists():
        outside.mkdir()

    driver = _CountingDriver()
    fake = _fake()
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.phase == "raw"
    assert "终名 work 已存在" in str(excinfo.value)
    assert driver.prepare_calls == 0
    assert fake.submissions == ()
    # 兄弟 cycle work 与 YD_ROOT 快照不变。
    assert (
        pathlib.Path(local.yd_root) / "states" / "gfs" / "2026082612.cfg.ic"
    ).is_file()


# --- Required evidence (fix item 3): exact work 父路径 symlink -> 写前拒绝 ---------


def test_work_parent_symlink_outside_tree_rejected_before_raw_copy(
    tmp_path: pathlib.Path,
) -> None:
    """`work_root/source` 指向树外、T leaf 缺席：raw 前拒绝，外部/兄弟零字节变化。

    旧实现 `work_root = resolved / "work"` + `lexists(work_dir)` 会先跟着 symlink 把
    raw 副本写进外部树；本用例证明修复后的 run_once 在 driver/submit 之前、任何写
    之前拒绝。
    """
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    scratch = pathlib.Path(local.scratch_root).resolve()
    work_root = scratch / "work"
    work_root.mkdir(parents=True)
    outside = scratch / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"keep-me")
    (work_root / "gfs").symlink_to(outside)

    driver = _CountingDriver()
    fake = _fake()
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.phase == "raw"
    assert "存在符号链接/别名" in str(excinfo.value)
    assert driver.prepare_calls == 0
    assert fake.submissions == ()
    # 外部树零新字节；work 下的 gfs symlink 原样保留（仅拒绝、不清理别人的条目），
    # 但没有 T leaf / object-store 从该链头上长出来。
    assert sentinel.read_bytes() == b"keep-me"
    assert not os.path.lexists(work_root / "gfs" / "2026082612")
    assert os.path.islink(work_root / "gfs")
    assert not (outside / "2026082612").exists()
    assert not (outside / "object-store").exists()


# --- Required evidence 5 附加: partition 空白（含纯空白）拒绝 --------------------


@pytest.mark.parametrize(
    "partition",
    ["", "   ", "\t", " \n "],
)
def test_partition_whitespace_only_is_rejected(
    tmp_path: pathlib.Path, partition: str
) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    from dataclasses import replace

    local = replace(local, slurm=dict(local.slurm, partition=partition))
    driver = _CountingDriver()
    fake = _fake()
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.phase == "preflight"
    assert "nonblank string" in str(excinfo.value)
    assert driver.prepare_calls == 0
    assert fake.submissions == ()


def test_partition_with_surrounding_whitespace_passes_preflight(
    tmp_path: pathlib.Path,
) -> None:
    """nonblank 判据是 strip 探针：` gpu-1 ` 必须过 preflight（值不被归一）。"""
    config, local = write_config_local(tmp_path)
    from dataclasses import replace

    local = replace(local, slurm=dict(local.slurm, partition=" gpu-1 "))
    # 无 states -> NO_INITIAL_STATE STOPPED：run_once 完整走完（preflight 通过）。
    fake = _fake()
    report = run_once(
        config=config,
        local=local,
        source="gfs",
        executor=fake,
        driver=InProcessDriver(HookState()),
        poll_wait=lambda: None,
    )
    assert report.outcome is RunOutcome.STOPPED
    assert report.stop_reason is controller.StopReason.NO_INITIAL_STATE


# --- Required evidence 15 附加: submit 前预埋终态产物任何形态拒绝 ------------------


@pytest.mark.parametrize("artifact", ["dat", "log", "checkpoint"])
def test_seeded_terminal_artifact_at_submit_is_rejected(
    tmp_path: pathlib.Path, artifact: str
) -> None:
    """提交前 DAT/log/canonical 预埋 -> 提交前拒绝、零提交、零 hook。

    所有权拆分：DAT 预存由 `_validate_prepared`（prepare phase）拒绝；job log/canonical
    预存由 `_require_absent_before_submit`（submit phase）拒绝——两条都是「submit 前」，
    但必须逐字命中各自 owner 的闸门消息，并证明 fake 已用**精确编排名**（若只按
    `cycle_id` 编排，log/checkpoint 会在 fake 的「未被编排」错误上假绿——那个错误不是
    本闸，且把 `_require_absent_before_submit` 变成 no-op 后测试仍通过）。
    """
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    from run_once_fixtures import HookedExecutor

    state = HookState()
    driver = InProcessDriver(state)
    # 精确编排名：若提交真发生，fake 必须成功（不得用「未被编排」误挡）。
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    original_prepare = driver.prepare
    hook_fired = []

    def seeding_prepare(*, request):
        result = original_prepare(request=request)
        if artifact == "dat":
            result.scratch_dat.parent.mkdir(parents=True, exist_ok=True)
            result.scratch_dat.write_bytes(b"x")
        elif artifact == "log":
            (request.work_dir / "job.log").write_bytes(b"x")
        else:
            canonical = (
                request.work_dir
                / "model"
                / "state_checkpoints"
                / "yd.f012.cfg.ic.update"
            )
            canonical.parent.mkdir(parents=True, exist_ok=True)
            canonical.write_bytes(b"x")
        return result

    driver.prepare = seeding_prepare  # type: ignore[method-assign]

    def make_hook(*, job_id):
        hook_fired.append(job_id)
        raise AssertionError("hook 不应触发")

    hook_executor = HookedExecutor(fake, make_hook)
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=hook_executor,
            driver=driver,
            poll_wait=lambda: None,
        )
    # 逐字 owner/消息断言：log/checkpoint 必须命中 `_require_absent_before_submit`
    # 的「提交前必须不存在」；DAT 必须命中 `_validate_prepared` 的同一措辞（其 owner
    # 在 prepare）。若闸被 no-op，fake（精确编排）会成功提交并触发 hook -> hook_fired
    # 非空且 phase 变 poll，本条失败（判别器，不是二次 submit 错误）。
    assert excinfo.value.phase == ("prepare" if artifact == "dat" else "submit")
    assert "提交前必须不存在" in str(excinfo.value)
    assert fake.submissions == ()
    assert hook_fired == []
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()


# --- 异常分类契约: BaseException 不包 ---------------------------------------------


def test_keyboard_interrupt_propagates_unwrapped(tmp_path: pathlib.Path) -> None:
    """`KeyboardInterrupt` 不得被包成 `RunError`（BaseException 不包）。"""
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    state = HookState()
    driver = InProcessDriver(state)

    class _KiExecutor:
        def submit(self, spec):
            raise KeyboardInterrupt()

        def poll(self, job_id: str):
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=_KiExecutor(),
            driver=driver,
            poll_wait=lambda: None,
        )


def test_system_exit_propagates_unwrapped(tmp_path: pathlib.Path) -> None:
    """`SystemExit` 不得被包成 `RunError`（BaseException 不包）。"""
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    driver = InProcessDriver(HookState())

    class _ExExecutor:
        def submit(self, spec):
            raise SystemExit(3)

        def poll(self, job_id: str):
            raise SystemExit(3)

    with pytest.raises(SystemExit) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=_ExExecutor(),
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.code == 3


# --- Required evidence 13 附: scratch DAT 的安全未来叶（fix item 4 判别器）----------


def test_prepared_dat_rejects_symlink_parent_to_outside_without_writing_outside(
    tmp_path: pathlib.Path,
) -> None:
    """scratch DAT 父路径是 symlink（指向树外）：prepare 阶段拒绝、外部零字节变化。"""
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    from run_once_fixtures import HookedExecutor

    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes={
            controller.cycle_id(CYCLE): FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )

    outside = tmp_path.resolve() / "outside-dat-tree"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"keep-me")

    class _LinkedDriver(InProcessDriver):
        def prepare(self, *, request):
            result = super().prepare(request=request)
            from dataclasses import replace

            # output 目录是 symlink -> outside；DAT 落 `output/dat`。
            work = request.work_dir
            work.mkdir(parents=True, exist_ok=True)
            link = work / "output"
            link.symlink_to(outside)
            return replace(result, scratch_dat=link / "dat")

    driver = _LinkedDriver(state)

    def make_hook(*, job_id):
        raise AssertionError("hook 不应触发")

    hook_executor = HookedExecutor(fake, make_hook)
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=hook_executor,
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.phase == "prepare"
    assert (
        "无歧义" in str(excinfo.value)
        or "symlink/别名" in str(excinfo.value)
        or "安全未来 leaf" in str(excinfo.value)
    )
    assert fake.submissions == ()
    assert sentinel.read_bytes() == b"keep-me"
    assert not (outside / "dat").exists()
