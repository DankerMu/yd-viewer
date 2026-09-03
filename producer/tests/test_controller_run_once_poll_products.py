"""`controller.run_once` 身份绑定失败矩阵（evidence 14/16 的逐腿判别器）。

fixture（tasks.md `### Issue #26 fixture`）Required evidence 14（submit/poll identity
每腿）与 16（products 矩阵每腿）的独立判别器；与「失败矩阵文件」
（evidence 5–9, 11, 13, 15, 17）同属一次 split——按「若两份 controller 测试任一接近
1000 行上限，按语义边界继续拆文件」的 fixture 允许，把 submit/poll identity 与 products
绑定腿独立成文件。期望值全部本地字面登记，不从被测实现回读。
"""

from __future__ import annotations

import pathlib
from dataclasses import replace
from datetime import timedelta

import pytest
from run_once_fixtures import (
    JOB_NAME,
    HookedExecutor,
    HookState,
    InProcessDriver,
    make_terminal_hook,
    step_clock,
    write_config_local,
    write_raw_cycle,
    write_state,
    write_variant,
)

from yd_producer.controller import RunError, RunOutcome, run_once
from yd_producer.executor import FakeJobExecutor, FakeOutcome, JobState
from yd_producer.tracker import CheckpointTracker

# --- evidence 14: submit/poll identity 逐腿 -------------------------------------


def _poll_scene(tmp_path: pathlib.Path, executor):
    """铺完整树并让 run_once 跑到 submit/poll；返回异常上下文。"""
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    driver = InProcessDriver(HookState())
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=executor,
            driver=driver,
            poll_wait=lambda: None,
        )
    return excinfo


def test_submit_wrong_resources_is_rejected(tmp_path: pathlib.Path) -> None:
    """submit 返回的 resources 不等于 JobSpec.resources -> phase submit。"""

    class _WrongResources(FakeJobExecutor):
        def submit(self, spec):
            record = super().submit(spec)
            return replace(record, resources={"partition": "other"})

    excinfo = _poll_scene(
        tmp_path,
        _WrongResources(
            outcomes={
                JOB_NAME: FakeOutcome(
                    final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
                )
            },
            clock=step_clock(),
        ),
    )
    assert excinfo.value.phase == "submit"
    assert "submit 返回的 resources 不等于" in str(excinfo.value)
    assert excinfo.value.job_id == "fake-1"


def test_poll_wrong_job_id_is_rejected(tmp_path: pathlib.Path) -> None:
    """poll 返回的 job_id 不与提交返回一致 -> phase poll。"""

    class _WrongId(FakeJobExecutor):
        def poll(self, job_id):
            return replace(super().poll(job_id), job_id="fake-999")

    excinfo = _poll_scene(
        tmp_path,
        _WrongId(
            outcomes={
                JOB_NAME: FakeOutcome(
                    final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
                )
            },
            clock=step_clock(),
        ),
    )
    assert excinfo.value.phase == "poll"
    assert "poll 返回的 job_id" in str(excinfo.value)
    assert excinfo.value.job_id == "fake-1"


def test_poll_wrong_name_is_rejected(tmp_path: pathlib.Path) -> None:
    """poll 返回的 name 不等于 JobSpec.name -> phase poll。"""

    class _WrongName(FakeJobExecutor):
        def poll(self, job_id):
            return replace(super().poll(job_id), name="wrong-name")

    excinfo = _poll_scene(
        tmp_path,
        _WrongName(
            outcomes={
                JOB_NAME: FakeOutcome(
                    final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
                )
            },
            clock=step_clock(),
        ),
    )
    assert excinfo.value.phase == "poll"
    assert "poll 返回的 name" in str(excinfo.value)
    assert excinfo.value.job_id == "fake-1"


def test_poll_wrong_resources_is_rejected(tmp_path: pathlib.Path) -> None:
    """poll 返回的 resources 不等于提交时的 JobSpec.resources -> phase poll。"""

    class _WrongResources(FakeJobExecutor):
        def poll(self, job_id):
            return replace(super().poll(job_id), resources={"partition": "other"})

    excinfo = _poll_scene(
        tmp_path,
        _WrongResources(
            outcomes={
                JOB_NAME: FakeOutcome(
                    final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
                )
            },
            clock=step_clock(),
        ),
    )
    assert excinfo.value.phase == "poll"
    assert "poll 返回的 resources 不等于" in str(excinfo.value)
    assert excinfo.value.job_id == "fake-1"


def test_poll_wrong_submitted_at_is_rejected(tmp_path: pathlib.Path) -> None:
    """poll 返回的 submitted_at 改变/消失 -> phase poll。"""

    class _WrongSubmittedAt(FakeJobExecutor):
        def poll(self, job_id):
            record = super().poll(job_id)
            return replace(
                record, submitted_at=record.submitted_at - timedelta(seconds=60)
            )

    excinfo = _poll_scene(
        tmp_path,
        _WrongSubmittedAt(
            outcomes={
                JOB_NAME: FakeOutcome(
                    final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
                )
            },
            clock=step_clock(),
        ),
    )
    assert excinfo.value.phase == "poll"
    assert "submitted_at" in str(excinfo.value)
    assert excinfo.value.job_id == "fake-1"


def test_poll_started_at_change_is_rejected(tmp_path: pathlib.Path) -> None:
    """RUNNING 的 started_at 出现后改变（非消失）也拒绝 -> phase poll。"""

    class _ChangedStartedAt(FakeJobExecutor):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._running_seen = 0

        def poll(self, job_id):
            record = super().poll(job_id)
            if record.state is JobState.RUNNING and record.started_at is not None:
                self._running_seen += 1
                if self._running_seen >= 2:
                    return replace(
                        record, started_at=record.started_at + timedelta(seconds=5)
                    )
            return record

    excinfo = _poll_scene(
        tmp_path,
        _ChangedStartedAt(
            outcomes={
                JOB_NAME: FakeOutcome(
                    final_state=JobState.SUCCEEDED, polls_until_terminal=2, started=True
                )
            },
            clock=step_clock(),
        ),
    )
    assert excinfo.value.phase == "poll"
    assert "started_at" in str(excinfo.value)
    assert excinfo.value.job_id == "fake-1"


def test_poll_running_to_pending_rollback_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """RUNNING 回退 PENDING（保留 started_at 以隔离跃迁守卫）-> phase poll。"""

    class _Rollback(FakeJobExecutor):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._running_seen = 0

        def poll(self, job_id):
            record = super().poll(job_id)
            if record.state is JobState.RUNNING and record.started_at is not None:
                self._running_seen += 1
                if self._running_seen >= 2:
                    return replace(record, state=JobState.PENDING)
            return record

    excinfo = _poll_scene(
        tmp_path,
        _Rollback(
            outcomes={
                JOB_NAME: FakeOutcome(
                    final_state=JobState.SUCCEEDED, polls_until_terminal=2, started=True
                )
            },
            clock=step_clock(),
        ),
    )
    assert excinfo.value.phase == "poll"
    assert "非法状态跃迁" in str(excinfo.value)
    assert excinfo.value.job_id == "fake-1"


def test_poll_pending_repeat_until_failed_passes(tmp_path: pathlib.Path) -> None:
    """正常 PENDING 重复 + PENDING->FAILED（未起跑）通过；不误报跃迁违规。"""
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    driver = InProcessDriver(HookState())
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.FAILED, polls_until_terminal=1, started=False
            )
        },
        clock=step_clock(),
    )
    report = run_once(
        config=config,
        local=local,
        source="gfs",
        executor=fake,
        driver=driver,
        poll_wait=lambda: None,
    )
    assert report.outcome is RunOutcome.JOB_FAILED
    assert report.job is not None and report.job.state is JobState.FAILED
    assert report.job.started_at is None
    assert report.published is None


# --- evidence 14 附：JobSpec 派生逐字（ownership 6） ------------------------------


def test_job_spec_is_derived_exactly_by_controller(tmp_path: pathlib.Path) -> None:
    """JobSpec 的 name/work_dir/log_path/resources 全部由 controller 逐字派生。

    ownership 6：driver 无权选 name/work/log/resources；这里用包装 executor 在
    submit 处快照 JobSpec，逐字断言派生式。work 根 = `scratch_root/work`，
    work_dir = `<work>/gfs/<T>`，log = `<work_dir>/job.log`，name = `yd-<source>-<T>`，
    resources = 完整 `dict(local.slurm)`。
    """
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    captured_spec = {}
    original_submit = fake.submit

    def capturing_submit(spec):
        captured_spec["spec"] = spec
        return original_submit(spec)

    fake.submit = capturing_submit  # type: ignore[method-assign]
    request_slot = {}
    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]

    def make_hook(*, job_id):
        make_terminal_hook(request_slot["request"], state)()

    hook_executor = HookedExecutor(fake, make_hook)
    report = run_once(
        config=config,
        local=local,
        source="gfs",
        executor=hook_executor,
        driver=driver,
        poll_wait=lambda: None,
    )
    assert report.outcome is RunOutcome.SUCCEEDED
    spec = captured_spec["spec"]
    work_root = pathlib.Path(local.scratch_root).resolve() / "work"
    work_dir = work_root / "gfs" / "2026082612"
    assert spec.work_dir == work_dir
    assert spec.name == JOB_NAME
    assert spec.log_path == work_dir / "job.log"
    assert dict(spec.resources) == {"partition": "gpu-1", "account": "yd-forecast"}
    assert spec.command == (
        local.shud_binary,
        "--cycle",
        "2026082612",
    )


def test_capture_round_submits_exactly_once(tmp_path: pathlib.Path) -> None:
    """成功 capture 轮：submissions 恰 1（无二次提交/recovery 双提交）。"""
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    original_submit = fake.submit
    submitted_ids = []

    def capturing_submit(spec):
        record = original_submit(spec)
        submitted_ids.append(record.job_id)
        return record

    fake.submit = capturing_submit  # type: ignore[method-assign]
    request_slot = {}
    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]

    def make_hook(*, job_id):
        make_terminal_hook(request_slot["request"], state)()

    hook_executor = HookedExecutor(fake, make_hook)
    report = run_once(
        config=config,
        local=local,
        source="gfs",
        executor=hook_executor,
        driver=driver,
        poll_wait=lambda: None,
    )
    assert report.outcome is RunOutcome.SUCCEEDED
    assert len(submitted_ids) == 1
    assert len(fake.submissions) == 1


# --- evidence 16: products 矩阵逐腿 ---------------------------------------------


def _products_scene(tmp_path: pathlib.Path, mutator):
    """真实 hook 跑完（products 已真实存在）后由 mutator 改写一项再交回。"""
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    request_slot = {}
    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]
    original_collect = driver.collect

    def collecting(*, attempt, terminal_record):
        products = original_collect(attempt=attempt, terminal_record=terminal_record)
        return mutator(products)

    driver.collect = collecting  # type: ignore[method-assign]

    def make_hook(*, job_id):
        make_terminal_hook(request_slot["request"], state)()

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
    return excinfo


def _wrong_job_id(products):
    return replace(products, job_id="fake-999")


def _wrong_dat(products):
    return replace(products, scratch_dat=pathlib.Path("/tmp/other.dat"))


def _wrong_log(products):
    return replace(products, merged_log=pathlib.Path("/tmp/other.log"))


def _wrong_run_directory_identity(products):
    identity = replace(products.run_directory.identity, model_id="other")
    return replace(
        products, run_directory=replace(products.run_directory, identity=identity)
    )


def _wrong_run_directory_path(products):
    return replace(
        products,
        run_directory=replace(
            products.run_directory, path=products.run_directory.path.parent / "wrong"
        ),
    )


def _wrong_tracker_run_dir(products):
    tracker = CheckpointTracker(
        run_dir=products.run_directory.path.parent / "elsewhere",
        project_name="yd",
        checkpoint_hours=(12,),
    )
    return replace(products, tracker=tracker)


def _wrong_tracker_project(products):
    tracker = CheckpointTracker(
        run_dir=products.run_directory.path,
        project_name="other",
        checkpoint_hours=(12,),
    )
    return replace(products, tracker=tracker)


def _wrong_tracker_targets(products):
    tracker = CheckpointTracker(
        run_dir=products.run_directory.path, project_name="yd", checkpoint_hours=(6,)
    )
    return replace(products, tracker=tracker)


@pytest.mark.parametrize(
    ("label", "mutator", "fragment"),
    [
        ("job_id", _wrong_job_id, "products.job_id"),
        ("scratch_dat", _wrong_dat, "products.scratch_dat"),
        ("merged_log", _wrong_log, "products.merged_log"),
        (
            "run_directory_identity",
            _wrong_run_directory_identity,
            "RunDirectory.identity",
        ),
        ("run_directory_path", _wrong_run_directory_path, "RunDirectory.path"),
        ("tracker_run_dir", _wrong_tracker_run_dir, "tracker.run_dir"),
        ("tracker_project", _wrong_tracker_project, "tracker.project_name"),
        ("tracker_targets", _wrong_tracker_targets, "tracker.targets"),
    ],
)
def test_products_matrix_each_leg_is_rejected(
    tmp_path: pathlib.Path, label: str, mutator, fragment: str
) -> None:
    excinfo = _products_scene(tmp_path, mutator)
    assert excinfo.value.phase == "collect"
    assert excinfo.value.job_id == "fake-1"
    # 逐字命中该腿的 products 绑定闸（若被换成"类型错误"/"意外异常"/"driver.collect
    # 失败"，说明没到达目标守卫——phase 相等不足以证明到达）。
    assert fragment in str(excinfo.value)
    # 零 publish：DONE 不可能出现。
    assert not (
        pathlib.Path(tmp_path).resolve()
        / "yd"
        / "output"
        / "2026082612"
        / "gfs"
        / "DONE"
    ).exists()


def test_checkpoint_returns_foreign_record_is_rejected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ensure 重验返回与 tracker.captured[12] 不同对象（同值异对象）-> collect 拒绝。

    证明 controller 对 checkpoint authority 的认领是**对象同一性**而非「存在」：
    若只查 `captured.get(12) is not None`，一个外来替换记录会被误采纳并发布。
    修补面是**公开** `yd_producer.tracker` 模块函数（run_once 私有支撑模块持有同一
    模块对象），绝不触碰任何 `yd_producer` 私有支撑模块。
    """
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    request_slot = {}
    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]

    def make_hook(*, job_id):
        make_terminal_hook(request_slot["request"], state)()

    hook_executor = HookedExecutor(fake, make_hook)
    import yd_producer.tracker as tracker_mod

    original_ensure = tracker_mod.ensure_twelve_hour_checkpoint

    def foreign_ensure(*, tracker, run_directory, runner):
        real = original_ensure(
            tracker=tracker, run_directory=run_directory, runner=runner
        )
        # 同值异对象：identity 检查必须拒绝。
        from dataclasses import replace

        return replace(real)

    monkeypatch.setattr(tracker_mod, "ensure_twelve_hour_checkpoint", foreign_ensure)
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=hook_executor,
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.phase == "collect"
    assert excinfo.value.job_id == "fake-1"
    assert "同一对象" in str(excinfo.value)
    assert not (
        pathlib.Path(tmp_path).resolve()
        / "yd"
        / "output"
        / "2026082612"
        / "gfs"
        / "DONE"
    ).exists()


def test_verify_canonical_regular_failure_carries_exact_job_id(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_verify_canonical_is_regular` 在 recheck 之后失败：RunError 携带精确 job_id。

    在 terminal hook 完成后、tracker 点用重验成功后，把 canonical 换成 symlink（指向
    外部合法状态）再交回 —— `_verify_canonical_is_regular` 的 no-follow stat 必须失败，
    且错误必须带本轮精确 `job_id`（"fake-1"）。若该 helper 的 RunError 漏 job_id，
    本条即判别器。修补面是公开 `yd_producer.tracker`（monkeypatch 自动还原）。
    """
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    outside = tmp_path.resolve() / "outside-canonical"
    outside.write_bytes(
        b"2 6 720.000000\nIndex Canopy Snow Surface Unsat GW\n1 0 0 0 0 0\n"
        b"Index River_Stage\n1 0.1\n"
    )
    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    request_slot = {}
    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]
    import yd_producer.tracker as tracker_mod

    original_ensure = tracker_mod.ensure_twelve_hour_checkpoint

    def swapping_ensure(*, tracker, run_directory, runner):
        real = original_ensure(
            tracker=tracker, run_directory=run_directory, runner=runner
        )
        canonical = run_directory.path / "state_checkpoints" / "yd.f012.cfg.ic.update"
        canonical.unlink()
        canonical.symlink_to(outside)
        return real

    monkeypatch.setattr(tracker_mod, "ensure_twelve_hour_checkpoint", swapping_ensure)

    def make_hook(*, job_id):
        make_terminal_hook(request_slot["request"], state)()

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
    assert excinfo.value.phase == "collect"
    assert excinfo.value.job_id == "fake-1"
    assert "canonical" in str(excinfo.value) and "普通文件" in str(excinfo.value)
    assert outside.read_bytes() == (
        b"2 6 720.000000\nIndex Canopy Snow Surface Unsat GW\n1 0 0 0 0 0\n"
        b"Index River_Stage\n1 0.1\n"
    )


def test_point_of_use_canonical_byte_drift_fails_at_recheck_without_done(
    tmp_path: pathlib.Path,
) -> None:
    """捕获后 canonical 字节被改（保留合法结构）：point-of-use 重验失败，零 DONE。

    判别器：hook 结束后（collect 前）改写 canonical 文件的一个 byte，使 checksum 与
    `tracker.captured[12]` 漂移。`ensure_twelve_hour_checkpoint` 的既有 record 分支
    必须点用重验 checksum 并失败——不得采纳盘上「同名但字节已变」的条目。
    """
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    request_slot = {}
    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]

    def make_hook(*, job_id):
        request = request_slot["request"]
        make_terminal_hook(request, state)()
        canonical = (
            request.work_dir / "model" / "state_checkpoints" / "yd.f012.cfg.ic.update"
        )
        data = bytearray(canonical.read_bytes())
        # 改动一个 body byte（保留 720 header 与结构）：checksum 必须漂移。
        data[-2] ^= 0x01
        canonical.write_bytes(bytes(data))

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
    assert excinfo.value.phase == "collect"
    assert excinfo.value.job_id == "fake-1"
    assert "checksum" in str(excinfo.value) or "drift" in str(excinfo.value)
    assert not (
        pathlib.Path(tmp_path).resolve()
        / "yd"
        / "output"
        / "2026082612"
        / "gfs"
        / "DONE"
    ).exists()


def test_collect_happens_exactly_once_on_success(tmp_path: pathlib.Path) -> None:
    """SUCCEEDED 轮 collect 恰一次（零重复 collect/publish）。"""
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    state = HookState()

    class _CountingDriver(InProcessDriver):
        def __init__(self, state):
            super().__init__(state)
            self.collect_calls = 0

        def collect(self, *, attempt, terminal_record):
            self.collect_calls += 1
            return super().collect(attempt=attempt, terminal_record=terminal_record)

    driver = _CountingDriver(state)
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    request_slot = {}
    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]

    def make_hook(*, job_id):
        make_terminal_hook(request_slot["request"], state)()

    hook_executor = HookedExecutor(fake, make_hook)
    report = run_once(
        config=config,
        local=local,
        source="gfs",
        executor=hook_executor,
        driver=driver,
        poll_wait=lambda: None,
    )
    assert report.outcome is RunOutcome.SUCCEEDED
    assert driver.collect_calls == 1
