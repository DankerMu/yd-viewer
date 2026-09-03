"""`controller.run_once` 失败矩阵（收尾面）：publish 三态 / raw 同根 / variant 数。

fixture（tasks.md `### Issue #26 fixture`）Required evidence 10/12/18 的独立判别器；
与「失败矩阵文件」（evidence 5–9, 11, 13–17）同属一次 split——两文件都被 run_once 的
1000 行闸门约束，故把 publish/raw/variant 收尾面独立成文件。期望值全部本地字面登记，
不从被测实现回读。
"""

from __future__ import annotations

import pathlib

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

# --- Required evidence 18: publish 三态 -------------------------------------------


def test_publish_error_becomes_typed_run_error(tmp_path: pathlib.Path) -> None:
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
    import yd_producer.publish as publish_module

    original_publish = publish_module.publish

    def failing_publish(inputs):
        raise publish_module.PublishError("契约检查拒绝")

    publish_module.publish = failing_publish  # type: ignore[assignment]
    try:
        with pytest.raises(RunError) as excinfo:
            run_once(
                config=config,
                local=local,
                source="gfs",
                executor=hook_executor,
                driver=driver,
                poll_wait=lambda: None,
            )
    finally:
        publish_module.publish = original_publish  # type: ignore[assignment]
    assert excinfo.value.phase == "publish"
    assert excinfo.value.job_id == "fake-1"
    assert "发布失败" in str(excinfo.value) and "契约检查拒绝" in str(excinfo.value)
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()


def test_publish_cleanup_error_yields_cleanup_pending(
    tmp_path: pathlib.Path,
) -> None:
    """真实 post-DONE 清理失败注入（步骤 7 work 删除失败）：cleanup-pending 报告。

    判别器：publisher 真写 DONE 与正式产物，随后 work 删除（`remove_tree_allow_symlinks
    `）被注入失败 -> `PublishCleanupError`。报告 must 记 completed-pending：
    `published` None、`done_path` 为**已落盘** DONE、DONE 在盘、work/证据保留、job 成功。
    这不是把 `except PublishError` 吞成失败的合成形态，而是真实 post-DONE 清理失败面。
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
    import yd_producer.publish as pub_mod

    original_publish = pub_mod.publish
    original_remove = pub_mod.remove_tree_allow_symlinks

    def failing_work_removal(*args, **kwargs):
        # 只对 work 树（步骤 7 的删除目标）失败；对其它调用原样放行（避免误伤）。
        if any("2026082612" in str(arg) for arg in args):
            raise OSError(1, "injected work removal failure")
        return original_remove(*args, **kwargs)

    pub_mod.remove_tree_allow_symlinks = failing_work_removal  # type: ignore[assignment]
    try:
        report = run_once(
            config=config,
            local=local,
            source="gfs",
            executor=hook_executor,
            driver=driver,
            poll_wait=lambda: None,
        )
    finally:
        pub_mod.publish = original_publish  # type: ignore[assignment]
        pub_mod.remove_tree_allow_symlinks = original_remove  # type: ignore[assignment]
    assert report.outcome is RunOutcome.SUCCEEDED_CLEANUP_PENDING
    assert report.published is None
    # done_path 逐字取已落盘 DONE，且 DONE 确实在盘。
    assert report.done_path is not None and report.done_path.is_file()
    assert report.job is not None and report.job.state is JobState.SUCCEEDED
    # 正式产物与 work 证据保留：无失败 finalizer 删除任何已完成证据。
    work_dir = (
        pathlib.Path(local.scratch_root).resolve() / "work" / "gfs" / "2026082612"
    )
    assert work_dir.exists()
    assert (report.done_path.parent / "yd.rivqdown.dat").is_file()


# --- Required evidence 10: raw layout 同根 ---------------------------------------


def test_raw_layout_lands_in_object_store_root(tmp_path: pathlib.Path) -> None:
    """成功 stage 后所有 entry 解析为 `object-store/raw/...`，manifest 在同根。"""
    from run_once_fixtures import (
        make_terminal_hook,
    )

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
    work_root = pathlib.Path(local.scratch_root).resolve() / "work"
    captured_work = {}
    store_root_ok = {}

    def make_hook(*, job_id):
        request = request_slot["request"]
        captured_work["work_dir"] = request.work_dir
        # raw manifest 与 object-store 根逐字绑定（evidence 10）：manifest 必须就在
        # `request.object_store_root` 之下，且该根名逐字是 `object-store`。
        manifest = pathlib.Path(request.raw_manifest_path)
        store_root_ok["manifest_parent_is_store_root"] = (
            manifest.parent == request.object_store_root
        )
        store_root_ok["store_root_is_named_object_store"] = (
            request.object_store_root.name == "object-store"
        )
        # raw 副本在 submit 前已落 `<store-root>/raw/...`：hook 读到的 manifest 指向
        # 的副本必须真实存在（若 controller 误传 work 根，consumer 找不到 raw 即红）。
        if manifest.is_file():
            store_root_ok["raw_copy_resolvable"] = True
            import json as _json

            payload = _json.loads(manifest.read_bytes())
            store_root_ok["raw_copy_resolvable"] = all(
                (request.object_store_root / entry["local_key"]).is_file()
                for entry in payload.get("entries", [])
            )
        else:
            store_root_ok["raw_copy_resolvable"] = False
        make_terminal_hook(request, state)()

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
    # staging 侧：manifest 与 raw/ 都在 `<work>/object-store` 根下（submit 前已建立，
    # 但成功轮 work 删除；此处验证 hook 消费的同一 store 根——already asserted via hook）。
    assert captured_work["work_dir"] == work_root / "gfs" / "2026082612"
    assert store_root_ok["manifest_parent_is_store_root"]
    assert store_root_ok["store_root_is_named_object_store"]
    assert store_root_ok["raw_copy_resolvable"]


# --- Required evidence 12: variant count ------------------------------------------


def test_variant_count_mismatch_is_rejected_before_publish(
    tmp_path: pathlib.Path,
) -> None:
    """率定态 river 行数与 reach_count 不等：prepare phase 拒绝，零 DONE。"""
    config, local = write_config_local(tmp_path)
    write_state(local)
    write_raw_cycle(local)
    from run_once_fixtures import PROJECT
    from run_once_fixtures import write_variant as wv

    variant = wv(local)
    # 改写率定态：river 段只写 3 行（!= 8）。
    from cfg_ic_fixtures import build_cfg_ic

    (variant / f"{PROJECT}.cfg.ic").write_bytes(
        build_cfg_ic(
            mesh_count=2,
            river_count=3,
            minute="29795760.000000",
        ).payload
    )
    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(outcomes={}, clock=step_clock())
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.phase == "prepare"
    assert "reach_count" in str(excinfo.value)
    assert fake.submissions == ()
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()
