"""`controller.run_once` 终态时序闸（Required evidence 15 修复）：submit 前后三件产物
缺失/存在、pre-collect 三件「已是 exact work 内普通文件」、collect 制造任何一件即拒绝。

fixture（tasks.md `### Issue #26 fixture`）：
- evidence 15：DAT/log/canonical checkpoint 在 submit 前均不存在；terminal hook 创建后、
  collect 前均存在；让 `driver.collect` 才创建任一项 -> 在 collect 调用前被拒（修复点：
  旧实现把检查放在 collect **之后**，collect 先创建即可通过）；
- 附：canonical symlink/非普通形态在 pre-collect 拒绝；timing ledger 证明三件产物在
  submit 时不存在、终态后（hook 已跑、collect 前）存在。
"""

from __future__ import annotations

import os
import pathlib
from datetime import timedelta

import pytest
from run_once_fixtures import (
    CYCLE,
    JOB_NAME,
    HookedExecutor,
    HookState,
    InProcessDriver,
    make_terminal_hook,
    write_config_local,
    write_raw_cycle,
    write_state,
    write_variant,
)

from yd_producer.controller import RunError, RunOutcome, run_once
from yd_producer.executor import FakeJobExecutor, FakeOutcome, JobState, StepClock

T0 = CYCLE.replace(hour=0, minute=0, second=0)


def _clock():
    return StepClock(start=T0, step=timedelta(seconds=10))


def _fake():
    return FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=_clock(),
    )


def _scene(tmp_path: pathlib.Path):
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    return config, local


def _run_with_collect(tmp_path: pathlib.Path, mutator):
    """真实 hook 跑完（三件产物必已存在）后，由 mutator 在 collect 前改写/删一件。"""
    config, local = _scene(tmp_path)
    state = HookState()
    driver = InProcessDriver(state)
    fake = _fake()
    request_slot = {}

    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]
    original_collect = driver.collect

    def collecting(*, attempt, terminal_record):
        return mutator(
            original_collect(attempt=attempt, terminal_record=terminal_record)
        )

    driver.collect = collecting  # type: ignore[method-assign]

    def make_hook(*, job_id):
        make_terminal_hook(request_slot["request"], state)()

    hook_executor = HookedExecutor(fake, make_hook)
    return run_once(
        config=config,
        local=local,
        source="gfs",
        executor=hook_executor,
        driver=driver,
        poll_wait=lambda: None,
    ), local


# --- evidence 15（修复点）：collect 在 SUCCEEDED 跃迁内制造任何一件即拒于 collect 前-----


@pytest.mark.parametrize("missing", ["dat", "log", "checkpoint"])
def test_collect_creating_missing_terminal_artifact_is_rejected_before_collect(
    tmp_path: pathlib.Path, missing: str
) -> None:
    """让 terminal hook **跳过**创建某一件，且 `driver.collect` 尝试补创建。

    collect 会返回「合法 products」（若允许其补建）。修复后的 pre-collect 闸必须在该
    collect 调用之前以 `RunError(collect)` 拒绝：collect 调用计数 0、无 publish/DONE。
    旧实现（collect 后才查）在这三条下全绿——本组测试是修复的独立判别器。
    """
    config, local = _scene(tmp_path)
    state = HookState()
    driver = InProcessDriver(state)
    fake = _fake()
    request_slot = {}
    collect_calls = []

    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]

    original_collect = driver.collect

    def make_hook(*, job_id):
        request = request_slot["request"]
        hook = make_terminal_hook(request, state)
        # 先正常跑 hook，再删掉指定终态产物（模拟「hook 没产出」）。
        hook()
        if missing == "dat":
            (request.work_dir / "output" / "yd.rivqdown.dat").unlink()
        elif missing == "log":
            (request.work_dir / "job.log").unlink()
        else:
            canonical = (
                request.work_dir
                / "model"
                / "state_checkpoints"
                / "yd.f012.cfg.ic.update"
            )
            canonical.unlink()

    def collecting(*, attempt, terminal_record):
        collect_calls.append("collect")
        # collect 自身补建：DAT/log/checkpoint 若缺则在此创建（路径取自 captured
        # `AttemptRequest.work_dir` —— `PreparedAttempt` 没有 work_dir 字段）。
        request = request_slot["request"]
        if missing == "dat":
            (request.work_dir / "output" / "yd.rivqdown.dat").write_bytes(b"x")
        elif missing == "log":
            (request.work_dir / "job.log").write_bytes(b"x")
        else:
            canonical = (
                request.work_dir
                / "model"
                / "state_checkpoints"
                / "yd.f012.cfg.ic.update"
            )
            canonical.parent.mkdir(parents=True, exist_ok=True)
            canonical.write_bytes(
                b"2 6 720.000000\nIndex Canopy Snow Surface Unsat GW\n"
                b"1 0 0 0 0 0\nIndex River_Stage\n1 0.1\n"
            )
        return original_collect(attempt=attempt, terminal_record=terminal_record)

    driver.collect = collecting  # type: ignore[method-assign]
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
    # 逐字命中 pre-collect 闸：缺失项不得被 collect 补建（消息必须出自该闸的
    # 「在 collect 前不存在」分支，不是 "driver.collect 失败"——后者意味着 collect
    # 被调用了）。
    assert "在 collect 前不存在" in str(excinfo.value)
    # collect 调用计数必须为 0：pre-collect 闸在首次 collect 调用之前拒绝。
    assert collect_calls == []
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()


@pytest.mark.parametrize("artifact", ["dat", "log", "checkpoint"])
@pytest.mark.parametrize("shape", ["symlink", "dir"])
def test_pre_collect_rejects_symlink_and_directory_shapes(
    tmp_path: pathlib.Path, artifact: str, shape: str
) -> None:
    """hook 后把终态产物换成 symlink（指向外部合法内容）或目录：pre-collect 拒绝。

    判别器：若 pre-collect 闸被删或放到 collect 之后，collect 会把这种形态当「已存在」
    交回，下游 tracker 校验可能照常放行（对 symlink 指向合法状态尤其明显）——因此本
    矩阵必须带零 collect 计数 + job_id + 外部哨兵不变断言。
    """
    config, local = _scene(tmp_path)
    outside = tmp_path.resolve() / "outside-sentinel"
    outside.write_bytes(
        b"2 6 720.000000\nIndex Canopy Snow Surface Unsat GW\n1 0 0 0 0 0\n"
        b"Index River_Stage\n1 0.1\n"
    )
    state = HookState()
    driver = InProcessDriver(state)
    fake = _fake()
    request_slot = {}
    collect_calls = []

    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]
    original_collect = driver.collect

    def counting_collect(*, attempt, terminal_record):
        collect_calls.append("collect")
        return original_collect(attempt=attempt, terminal_record=terminal_record)

    driver.collect = counting_collect  # type: ignore[method-assign]

    def artifact_path(request):
        if artifact == "dat":
            return request.work_dir / "output" / "yd.rivqdown.dat"
        if artifact == "log":
            return request.work_dir / "job.log"
        return (
            request.work_dir / "model" / "state_checkpoints" / "yd.f012.cfg.ic.update"
        )

    def make_hook(*, job_id):
        request = request_slot["request"]
        make_terminal_hook(request, state)()
        target = artifact_path(request)
        target.unlink()
        if shape == "symlink":
            target.symlink_to(outside)
        else:
            target.mkdir()

    hook_executor = HookedExecutor(fake, make_hook)
    before = outside.read_bytes()
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
    # 决定性断言在前：collect 调用计数必须为 0——若 pre-collect 闸被删，任何下游
    # tracker 校验都不能掩盖 collect 已被调用的事实。
    assert collect_calls == []
    # 再逐字命中 pre-collect 闸的形态分支：symlink -> 不是 no-follow 普通文件；
    # 目录 -> 不是普通文件。不能是 "driver.collect 失败"（那意味着 collect 被调用）。
    if shape == "symlink":
        assert "不是 no-follow 普通文件" in str(excinfo.value)
    else:
        assert "不是普通文件" in str(excinfo.value)
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()
    assert outside.read_bytes() == before


def test_raw_manifest_reference_removed_fails_hook_before_collect_and_publish(
    tmp_path: pathlib.Path,
) -> None:
    """fake oracle 判别器：删除一条 staged raw 副本后执行**真实 terminal hook**。

    证明 `make_terminal_hook` 内部确实消费本轮 `raw_manifest_path` 的每条 `local_key`
    （经同一 `LocalObjectStore` 回读），而不是只认 `object_store_root` 存在。若 hook
    忽略 manifest，本条用例全绿（歧义即失败）。
    """
    config, local = _scene(tmp_path)
    state = HookState()
    driver = InProcessDriver(state)
    fake = _fake()
    request_slot = {}
    collect_calls = []

    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]
    original_collect = driver.collect

    def counting_collect(*, attempt, terminal_record):
        collect_calls.append(1)
        return original_collect(attempt=attempt, terminal_record=terminal_record)

    driver.collect = counting_collect  # type: ignore[method-assign]

    def make_hook(*, job_id):
        request = request_slot["request"]
        # 先破坏一条 staged raw 副本（manifest 仍引用它），再跑真实 hook。
        from yd_producer.store.object_store import LocalObjectStore

        store = LocalObjectStore(request.object_store_root)
        import json

        manifest = json.loads(
            (request.object_store_root / "raw-manifest.json").read_bytes()
        )
        first_key = manifest["entries"][0]["local_key"]
        (store.resolve_path(first_key)).unlink()
        make_terminal_hook(request, state)()

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
    assert excinfo.value.phase == "poll"
    # 逐字命中 hook 内的 manifest 消费失败（read/空副本/无 entries 之一），证明失败
    # 出自 fake oracle 的真实消费而非其它 poll 错误。
    assert "manifest" in str(excinfo.value) or "object" in str(excinfo.value)
    assert collect_calls == []
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()


def test_timing_ledger_absent_at_submit_present_between_hook_and_collect(
    tmp_path: pathlib.Path,
) -> None:
    """时序账本：submit 时三件 lexists 全假；hook 后、collect 前全部是 no-follow 普通文件。

    账本在三个边界快照：executor.submit 调回**之前**（submit 前）、terminal hook 完成
    后（poll 返回前）、collect 被调回**之前**（交接前）。断言精确有序序列
    `["at-submit", "after-terminal", "at-collect"]` 与每个快照的三项值。
    """
    from yd_producer.store import safe_fs

    config, local = _scene(tmp_path)
    state = HookState()
    driver = InProcessDriver(state)
    fake = _fake()
    request_slot = {}
    ledger: dict[str, object] = {}
    actions: list[str] = []

    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]

    def paths_of(request):
        return {
            "dat": request.work_dir / "output" / "yd.rivqdown.dat",
            "log": request.work_dir / "job.log",
            "checkpoint": (
                request.work_dir
                / "model"
                / "state_checkpoints"
                / "yd.f012.cfg.ic.update"
            ),
        }

    def snapshot_absent(request):
        return {k: os.path.lexists(v) for k, v in paths_of(request).items()}

    def snapshot_regular(request):
        result = {}
        for key, path in paths_of(request).items():
            try:
                info = safe_fs.stat_no_follow(path, containment_root=request.work_root)
                import stat as stat_module

                result[key] = stat_module.S_ISREG(info.st_mode)
            except (OSError, safe_fs.SafeFilesystemError):
                result[key] = False
        return result

    class _LedgerExecutor:
        def __init__(self, executor):
            self._executor = executor

        def submit(self, spec):
            actions.append("at-submit")
            ledger["at-submit"] = snapshot_absent(request_slot["request"])
            return self._executor.submit(spec)

        def poll(self, job_id):
            return self._executor.poll(job_id)

        @property
        def submissions(self):
            return self._executor.submissions

        @property
        def max_inflight(self):
            return self._executor.max_inflight

        def inflight(self):
            return self._executor.inflight()

    ledger_executor = _LedgerExecutor(fake)
    original_collect = driver.collect

    def collecting(*, attempt, terminal_record):
        actions.append("at-collect")
        ledger["at-collect"] = snapshot_regular(request_slot["request"])
        return original_collect(attempt=attempt, terminal_record=terminal_record)

    driver.collect = collecting  # type: ignore[method-assign]

    def make_hook(*, job_id):
        request = request_slot["request"]
        make_terminal_hook(request, state)()
        # hook 完成、SUCCEEDED 跃迁内、collect 之前 -> 三件产物必须已是普通文件。
        actions.append("after-terminal")
        ledger["after-terminal"] = snapshot_regular(request)

    hook_executor = HookedExecutor(ledger_executor, make_hook)
    report = run_once(
        config=config,
        local=local,
        source="gfs",
        executor=hook_executor,
        driver=driver,
        poll_wait=lambda: None,
    )
    assert report.outcome is RunOutcome.SUCCEEDED
    # 精确有序序列：submit 前（absent 快照）-> hook 后（regular 快照）-> collect 前
    # （regular 快照）。collect 恰在 at-collect 之后交回。
    assert actions == ["at-submit", "after-terminal", "at-collect"]
    assert ledger["at-submit"] == {"dat": False, "log": False, "checkpoint": False}
    assert ledger["after-terminal"] == {
        "dat": True,
        "log": True,
        "checkpoint": True,
    }
    assert ledger["at-collect"] == {
        "dat": True,
        "log": True,
        "checkpoint": True,
    }
