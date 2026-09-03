"""`controller.run_once` raw fanout 契约矩阵（contract-1 修复判别器）。

fixture（tasks.md `### Issue #26 fixture`）：raw-scan spec 的「逐变量扇出」——同一
`(lead, bundle)` 的各变量 entry 共享同一 `local_key`，`stage_raw` 每 lead 只复制一份
bundle 副本。故 `len(entries) = leads × variables` 而 `len(copied_files) = leads`；
controller 的 raw 校验必须是**集合/成员关系**校验，不是 `zip(entries, copied_files)`
的等基数位置校验。

本文件承载 evidence 10 附加扇出腿（正例 + foreign/orphan/duplicate 三条负例），
与既有失败矩阵文件按「1000 行闸门 -> 语义边界拆文件」的 fixture 规则独立成文件；
期望值全部本地字面登记，不从被测实现回读。
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import replace

import pytest
from run_once_fixtures import (
    CYCLE,
    JOB_NAME,
    MULTI_GFS_LEADS,
    MULTI_GFS_VARIABLES,
    HookedExecutor,
    HookState,
    InProcessDriver,
    make_local,
    make_multi_gfs_config,
    make_terminal_hook,
    step_clock,
    write_raw_cycle,
    write_state,
    write_variant,
)

from yd_producer import rawcopy as rawcopy_module
from yd_producer.controller import RunError, RunOutcome, run_once
from yd_producer.executor import FakeJobExecutor, FakeOutcome, JobState
from yd_producer.store.object_store import OBJECT_KIND_FILE, LocalObjectStore


class _CountingDriver:
    """记录型 driver：prepare 调用计数（raw 前零调用判别器）。"""

    def __init__(self, state: HookState) -> None:
        self._inner = InProcessDriver(state)
        self.prepare_calls = 0

    def prepare(self, *, request):
        self.prepare_calls += 1
        return self._inner.prepare(request=request)

    def collect(self, *, attempt, terminal_record):
        return self._inner.collect(attempt=attempt, terminal_record=terminal_record)


def _scene(tmp_path: pathlib.Path, *, bad_stage=None):
    """铺多变量合成树并返回 (config, local, fake, hook_executor, driver, restore)。"""
    config = make_multi_gfs_config()
    local = make_local(tmp_path, config=config)
    pathlib.Path(local.yd_root).mkdir(parents=True, exist_ok=True)
    pathlib.Path(local.scratch_root).mkdir(parents=True, exist_ok=True)
    write_variant(local)
    write_state(local)
    write_raw_cycle(
        local,
        leads=MULTI_GFS_LEADS,
        variables=MULTI_GFS_VARIABLES,
    )
    state = HookState()
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

    if bad_stage is not None:
        original_stage = rawcopy_module.stage_raw

        def tampering_stage(*args, **kwargs):
            staged = original_stage(*args, **kwargs)
            return bad_stage(staged)

        rawcopy_module.stage_raw = tampering_stage  # type: ignore[assignment]

    def restore():
        if bad_stage is not None:
            rawcopy_module.stage_raw = original_stage  # type: ignore[assignment]

    return config, local, fake, hook_executor, driver, restore


def _run_until_raw(tmp_path: pathlib.Path, *, bad_stage=None):
    """跑 run_once 到 raw 校验，返回 (excinfo, fake, driver, local)。"""
    config, local, fake, hook_executor, driver, restore = _scene(
        tmp_path, bad_stage=bad_stage
    )
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
        restore()
    return excinfo, fake, driver, local


def test_multi_variable_fanout_success(tmp_path: pathlib.Path) -> None:
    """3 变量 × 2 lead 合法成功：entry=6、copied=2、共享键、SUCCEEDED、DONE 在盘。

    老实现 `zip(entries, copied_files, strict=True)` 在此必红（raw phase）；本测试是
    contract-1 的独立正例判别器。

    oracle 无副作用纪律：`LocalObjectStore.__post_init__` 会 `ensure_directory_no_follow`
    根目录，而成功 `run_once` 已在 publish 第 7 步删除 exact work——返回后再构造
    `LocalObjectStore(<work>/.../object-store)` 会把已清理的 work 目录重新造出来，
    使「work 最终不存在」的断言失效。故同根 store 只在 `stage_raw` 的 capturing
    wrapper 存活窗口内构造（work 尚存），返回后只断言捕获的**纯值**（resolved 路径
    集合、每个 entry 对应对象当时的 no-follow 分类），并在返回后对
    `report.published.removed_work_dir` 做存在性断言——该断言同时抓测试自身或实现
    重建 work 的两种污染。
    """
    config = make_multi_gfs_config()
    local = make_local(tmp_path, config=config)
    pathlib.Path(local.yd_root).mkdir(parents=True, exist_ok=True)
    pathlib.Path(local.scratch_root).mkdir(parents=True, exist_ok=True)
    write_variant(local)
    write_state(local)
    write_raw_cycle(
        local,
        leads=MULTI_GFS_LEADS,
        variables=MULTI_GFS_VARIABLES,
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

    def make_hook(*, job_id):
        make_terminal_hook(request_slot["request"], state)()

    hook_executor = HookedExecutor(fake, make_hook)
    object_store_root = (
        pathlib.Path(local.scratch_root).resolve()
        / "work"
        / "gfs"
        / "2026082612"
        / "object-store"
    )
    staged_slot: dict[str, object] = {}
    resolved_slot: dict[str, object] = {}
    original_stage = rawcopy_module.stage_raw

    def capturing_stage(*args, **kwargs):
        staged = original_stage(*args, **kwargs)
        # 只在 stage_raw 存活窗口内（work 尚存）构造/使用同根 store；捕获的是纯值。
        store = LocalObjectStore(object_store_root)
        resolved = {store.resolve_path(entry.local_key) for entry in staged.entries}
        kinds = tuple(
            (entry.local_key, store.object_kind(entry.local_key))
            for entry in staged.entries
        )
        staged_slot["staged"] = staged
        resolved_slot["resolved"] = resolved
        resolved_slot["kinds"] = kinds
        return staged

    rawcopy_module.stage_raw = capturing_stage  # type: ignore[assignment]
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
        rawcopy_module.stage_raw = original_stage  # type: ignore[assignment]
    assert report.outcome is RunOutcome.SUCCEEDED
    assert len(fake.submissions) == 1
    assert report.done_path.is_file()
    staged = staged_slot["staged"]
    assert len(staged.entries) == len(MULTI_GFS_LEADS) * len(MULTI_GFS_VARIABLES)
    assert len(staged.copied_files) == len(MULTI_GFS_LEADS)
    # 同一 (lead, bundle) 的各变量 entry 共享同一 local_key，且每个 entry 都解析到
    # 逐字在 copied-file 集合内的同一 store 路径（纯值，在 wrapper 存活窗口内捕获）。
    resolved = resolved_slot["resolved"]
    assert resolved == set(staged.copied_files)
    for local_key, kind in resolved_slot["kinds"]:
        assert kind == OBJECT_KIND_FILE
    for lead in MULTI_GFS_LEADS:
        by_lead = sorted(e.variable for e in staged.entries if e.forecast_hour == lead)
        assert by_lead == sorted(MULTI_GFS_VARIABLES)
    # 成功返回后：publish 第 7 步已删除 exact work；显式断言 `removed_work_dir`
    # 非 None、逐字等于预先派生的 exact work 路径、且不存在（任何实现或测试自身
    # 重建 work 都会让该断言变红）。
    assert report.published is not None
    removed_work = report.published.removed_work_dir
    assert removed_work == object_store_root.parent
    assert not os.path.lexists(removed_work)
    assert not os.path.lexists(object_store_root)


def test_foreign_unmatched_entry_is_rejected_before_driver(
    tmp_path: pathlib.Path,
) -> None:
    """entry.local_key 解析到 copied 集合之外的路径 -> RunError(raw)，零副作用。

    判别器：把第一条 entry 的 `local_key` 改成一个解析后不在 `copied_files` 集合内的
    key（但仍在 object-store 根内的合法形态）。
    """

    def tamper(staged):
        entry = replace(
            staged.entries[0],
            local_key="raw/foreign/2026082612/not-a-copy.grib2",
        )
        return replace(staged, entries=(entry, *staged.entries[1:]))

    excinfo, fake, driver, local = _run_until_raw(tmp_path, bad_stage=tamper)
    assert excinfo.value.phase == "raw"
    assert "不在本轮 copied 副本集合" in str(excinfo.value)
    assert excinfo.value.source == "gfs"
    assert excinfo.value.cycle == CYCLE
    assert driver.prepare_calls == 0
    assert fake.submissions == ()
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()


def test_orphan_copied_file_is_rejected_before_driver(
    tmp_path: pathlib.Path,
) -> None:
    """copied_files 含无人引用的副本 -> RunError(raw)，零副作用。

    判别器：给 `copied_files` 追加一个合法形态但无 entry 引用的路径。
    """

    def tamper(staged):
        store_path = staged.copied_files[0]
        orphan = store_path.parent / "orphan.grib2"
        return replace(staged, copied_files=staged.copied_files + (orphan,))

    excinfo, fake, driver, local = _run_until_raw(tmp_path, bad_stage=tamper)
    assert excinfo.value.phase == "raw"
    assert "未被任何 entry 引用" in str(excinfo.value)
    assert driver.prepare_calls == 0
    assert fake.submissions == ()
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()


def test_duplicate_copied_file_path_is_rejected_before_driver(
    tmp_path: pathlib.Path,
) -> None:
    """copied_files 含重复路径 -> RunError(raw)，零副作用。"""

    def tamper(staged):
        return replace(
            staged, copied_files=staged.copied_files + (staged.copied_files[0],)
        )

    excinfo, fake, driver, local = _run_until_raw(tmp_path, bad_stage=tamper)
    assert excinfo.value.phase == "raw"
    assert "含重复路径" in str(excinfo.value)
    assert driver.prepare_calls == 0
    assert fake.submissions == ()
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()
