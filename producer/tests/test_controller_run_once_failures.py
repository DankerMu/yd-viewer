"""`controller.run_once` 失败矩阵：preflight/frontier/raw/identity/poll/products/publish。

fixture（tasks.md `### Issue #26 fixture`）Required evidence 5–18 的独立判别器每条
一次入参 -> 期望输出；期望值全部本地字面登记，不从被测实现回读。
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from run_once_fixtures import (
    CYCLE,
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

from yd_producer import controller
from yd_producer.config import Config, LocalConfig
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


def _tree_snapshot(root: pathlib.Path) -> dict[str, tuple[str, int]]:
    return {
        str(p.relative_to(root)): (p.lstat().st_mode, 0)
        for p in sorted(root.rglob("*"))
    }


def _assert_no_mutation(before, root: pathlib.Path) -> None:
    assert _tree_snapshot(root) == before


# --- Required evidence 5: partition preflight -----------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        "required_missing",
        "local_missing",
        "partition_blank",
        "partition_non_str",
        "keyset_extra",
        "keyset_missing",
    ],
)
def test_partition_preflight_fails_before_any_discovery(
    tmp_path: pathlib.Path, variant: str
) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    before = _tree_snapshot(pathlib.Path(local.yd_root))

    if variant == "required_missing":
        config = _replace_slurm_required(config, ("account",))
        # 键集必须相等，否则先在 keyset-equality 步失败，测不到「required 无 partition」
        # 这条 double-owner 闸（ownership 1 第 6 行）。
        local = _replace_slurm_local(local, {"account": "yd-forecast"})
    elif variant == "local_missing":
        local = _replace_slurm_local(local, {"account": "yd-forecast"})
    elif variant == "partition_blank":
        local = _replace_slurm_local(local, dict(local.slurm, partition=""))
    elif variant == "partition_non_str":
        local = _replace_slurm_local(local, dict(local.slurm, partition=8))
    elif variant == "keyset_extra":
        local = _replace_slurm_local(local, dict(local.slurm, extra="x"))
    elif variant == "keyset_missing":
        local = _replace_slurm_local(
            local, {k: v for k, v in local.slurm.items() if k != "account"}
        )

    driver = _CountingDriver()
    fake = _fake()
    # 零 discovery/raw/driver 探针：preflight 失败面不得触碰任何发现或写面。
    import yd_producer.rawscan as rawscan_module

    probes = []
    original_judge = rawscan_module.judge

    def probe_judge(*args, **kwargs):
        probes.append("judge")
        return original_judge(*args, **kwargs)

    rawscan_module.judge = probe_judge  # type: ignore[assignment]
    try:
        with pytest.raises(RunError) as excinfo:
            run_once(
                config=config,
                local=local,
                source="gfs",
                executor=fake,
                driver=driver,
                poll_wait=lambda: None,
            )
    finally:
        rawscan_module.judge = original_judge  # type: ignore[assignment]
    assert excinfo.value.phase == "preflight"
    assert driver.prepare_calls == 0
    assert fake.submissions == ()
    assert probes == []
    assert _tree_snapshot(pathlib.Path(local.yd_root)) == before
    # 每条变体必须命中**自己的**闸门消息，否则该闸门是死代码（变异测试实测）：
    # required 缺 partition / local 缺 partition / 键集不等 / partition 空白。
    message = str(excinfo.value)
    if variant == "required_missing":
        assert "slurm.required_fields 必须声明 partition" in message
    elif variant == "local_missing":
        assert "local.toml 的 [slurm] 缺少 partition" in message
    elif variant in ("keyset_extra", "keyset_missing"):
        assert "键集必须与 config.toml 的 `slurm.required_fields` 完全一致" in message
    elif variant in ("partition_blank", "partition_non_str"):
        assert "nonblank string" in message


def _replace_slurm_required(config: Config, fields) -> Config:
    from dataclasses import replace

    schema = replace(config.slurm, required_fields=tuple(fields))
    return replace(config, slurm=schema)


def _replace_slurm_local(local: LocalConfig, mapping) -> LocalConfig:
    from dataclasses import replace

    return replace(local, slurm=dict(mapping))


# --- Required evidence 6: product preflight -------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        "forecast_days",
        "interval",
        "checkpoint_hours",
        "reach_le",
        "source",
        "relative_yd",
        "relative_scratch",
        "relative_raw",
        "relative_shud",
    ],
)
def test_product_preflight_fails_before_driver_and_executor(
    tmp_path: pathlib.Path, variant: str
) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)

    if variant == "forecast_days":
        config = _replace_config(config, forecast_days=6)
    elif variant == "interval":
        config = _replace_config(config, output_interval_minutes=30)
    elif variant == "checkpoint_hours":
        config = _replace_config(config, checkpoint_hours=(6,))
    elif variant == "reach_le":
        config = _replace_config(config, reach_count=0)
    elif variant == "source":
        pass  # 由下面 source="era5" 触发
    elif variant == "relative_yd":
        local = _replace_yd(local, "relative/root")
    elif variant == "relative_scratch":
        local = _replace_scratch(local, "relative/scratch")
    elif variant == "relative_raw":
        local = _replace_raw(local, "relative/raw")
    elif variant == "relative_shud":
        local = _replace_shud(local, "relative/shud")

    driver = _CountingDriver()
    fake = _fake()
    source = "era5" if variant == "source" else "gfs"
    # 零 discovery/raw/driver 探针：preflight 在任何发现/写/删之前失败。
    import yd_producer.rawscan as rawscan_module

    probes = []
    original_judge = rawscan_module.judge

    def probe_judge(*args, **kwargs):
        probes.append("judge")
        return original_judge(*args, **kwargs)

    rawscan_module.judge = probe_judge  # type: ignore[assignment]
    before = _tree_snapshot(pathlib.Path(local.yd_root))
    try:
        with pytest.raises(RunError) as excinfo:
            run_once(
                config=config,
                local=local,
                source=source,
                executor=fake,
                driver=driver,
                poll_wait=lambda: None,
            )
    finally:
        rawscan_module.judge = original_judge  # type: ignore[assignment]
    assert excinfo.value.phase == "preflight"
    # 逐字命中该变体的 preflight 闸（phase 相等不足以证明到达：任一前置闸错误也会是
    # preflight）。source=era5 命中 source 闸；其余每条命中各自措辞。
    message = str(excinfo.value)
    if variant == "source":
        assert "source 取值非法" in message
    elif variant == "forecast_days":
        assert "forecast_days 必须为 7" in message
    elif variant == "interval":
        assert "output_interval_minutes 必须为 60" in message
    elif variant == "checkpoint_hours":
        assert "checkpoint_hours 必须恰为 (12,)" in message
    elif variant == "reach_le":
        assert "reach_count 必须为正整数" in message
    elif variant == "relative_yd":
        assert "yd_root" in message and "绝对路径" in message
    elif variant == "relative_scratch":
        assert "scratch_root" in message and "绝对路径" in message
    elif variant == "relative_raw":
        assert "nwm.raw_root" in message and "绝对路径" in message
    elif variant == "relative_shud":
        assert "shud_binary" in message and "绝对路径" in message
    assert driver.prepare_calls == 0
    assert fake.submissions == ()
    assert probes == []
    assert _tree_snapshot(pathlib.Path(local.yd_root)) == before


def _replace_config(config: Config, **overrides) -> Config:
    from dataclasses import replace

    return replace(config, **overrides)


def _replace_yd(local: LocalConfig, value) -> LocalConfig:
    from dataclasses import replace

    return replace(local, yd_root=value)


def _replace_scratch(local: LocalConfig, value) -> LocalConfig:
    from dataclasses import replace

    return replace(local, scratch_root=value)


def _replace_raw(local: LocalConfig, value) -> LocalConfig:
    from dataclasses import replace

    nwm = replace(local.nwm, raw_root=value)
    return replace(local, nwm=nwm)


def _replace_shud(local: LocalConfig, value) -> LocalConfig:
    from dataclasses import replace

    return replace(local, shud_binary=value)


# --- Required evidence 7: frontier normal stops ---------------------------------


@pytest.mark.parametrize(
    "kind", ["no_initial", "state_missing", "state_unreadable", "header_mismatch"]
)
def test_frontier_normal_stops_with_zero_side_effects(
    tmp_path: pathlib.Path, kind: str
) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_raw_cycle(local)
    if kind != "no_initial":
        write_state(local)
    root = pathlib.Path(local.yd_root)
    if kind == "state_missing":
        # 有 DONE(D) 而无 T 状态：STATE_MISSING（而非全新链 NO_INITIAL_STATE）。
        done = root / "output" / "2026082600" / "gfs" / "DONE"
        done.parent.mkdir(parents=True, exist_ok=True)
        done.write_bytes(b"")
        (root / "states" / "gfs" / "2026082612.cfg.ic").unlink()
    if kind == "state_unreadable":
        p = root / "states" / "gfs" / "2026082612.cfg.ic"
        p.write_bytes(b"\xff\xfe\x00bad\n")
    if kind == "header_mismatch":
        p = root / "states" / "gfs" / "2026082612.cfg.ic"
        p.write_bytes(
            b"1 6 1.000000\nIndex Canopy Snow Surface Unsat GW\n1 0 0 0 0 0\n"
        )
    before = _tree_snapshot(root)

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
    assert report.outcome is RunOutcome.STOPPED
    assert report.job is None
    expected_reason = {
        "no_initial": controller.StopReason.NO_INITIAL_STATE,
        "state_missing": controller.StopReason.STATE_MISSING,
        "state_unreadable": controller.StopReason.STATE_UNREADABLE,
        "header_mismatch": controller.StopReason.HEADER_TIME_MISMATCH,
    }[kind]
    assert report.stop_reason is expected_reason
    assert driver.prepare_calls == 0
    assert fake.submissions == ()
    assert _tree_snapshot(root) == before


# --- Required evidence 8: arbitrary hour ----------------------------------------


def test_arbitrary_hour_is_stopped_before_residue_raw_and_submit(
    tmp_path: pathlib.Path,
) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    # 唯一状态是 18Z：无可解析 DONE、无 00/12 状态。
    root = pathlib.Path(local.yd_root)
    states = root / "states" / "gfs"
    states.mkdir(parents=True, exist_ok=True)
    cycle18 = datetime(2026, 8, 26, 18, tzinfo=UTC)
    minute18 = round(cycle18.timestamp() / 60)
    (states / "2026082618.cfg.ic").write_bytes(
        b"2 6 %d.000000\nIndex Canopy Snow Surface Unsat GW\n1 0 0 0 0 0\n"
        b"Index River_Stage\n1 0.1\n" % minute18
    )
    driver = _CountingDriver()
    fake = _fake()

    import yd_producer.rawscan as rawscan_module
    import yd_producer.residue as residue_module

    calls = []

    original_plan = residue_module.plan_residue
    original_judge = rawscan_module.judge

    def probe_plan(*args, **kwargs):
        calls.append("plan_residue")
        return original_plan(*args, **kwargs)

    def probe_judge(*args, **kwargs):
        calls.append("judge")
        return original_judge(*args, **kwargs)

    residue_module.plan_residue = probe_plan  # type: ignore[assignment]
    rawscan_module.judge = probe_judge  # type: ignore[assignment]
    try:
        report = run_once(
            config=config,
            local=local,
            source="gfs",
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    finally:
        residue_module.plan_residue = original_plan  # type: ignore[assignment]
        rawscan_module.judge = original_judge  # type: ignore[assignment]
    assert report.outcome is RunOutcome.STOPPED
    assert report.cycle == cycle18
    assert report.stop_reason is controller.StopReason.RAW_INCOMPLETE
    assert report.job is None
    assert driver.prepare_calls == 0
    assert fake.submissions == ()
    # 显式零 residue/raw 调用、零 work：任意越域小时在删/扫之前收敛。
    assert calls == []
    assert not (pathlib.Path(local.scratch_root) / "work").exists()


# --- Required evidence 9: residue-before-raw ------------------------------------


def test_residue_is_cleaned_before_raw_incomplete_stop(tmp_path: pathlib.Path) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    root = pathlib.Path(local.yd_root)
    # 残留：T+12 状态 与 无 DONE 的 T 半成品目录。
    (root / "states" / "gfs" / "2026082700.cfg.ic").write_bytes(
        b"2 6 29796480.000000\nIndex Canopy Snow Surface Unsat GW\n1 0 0 0 0 0\n"
        b"Index River_Stage\n1 0.1\n"
    )
    half = root / "output" / "2026082612" / "gfs"
    half.mkdir(parents=True)
    (half / "yd.rivqdown.dat").write_bytes(b"partial\n")
    # 兄弟源 GFS 的正式产物不动。
    sibling = root / "output" / "2026082612" / "ifs"
    sibling.mkdir(parents=True)
    (sibling / "DONE").write_bytes(b"")

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
    assert report.outcome is RunOutcome.STOPPED
    assert report.cycle == CYCLE
    assert report.stop_reason is controller.StopReason.RAW_INCOMPLETE
    assert report.job is None
    # 残留精确清掉两项，兄弟源原样保留。
    assert not (root / "states" / "gfs" / "2026082700.cfg.ic").exists()
    assert not half.exists()
    assert (sibling / "DONE").is_file()
    assert fake.submissions == ()


# --- Required evidence 13: prepared-attempt 矩阵 ---------------------------------


def test_prepared_attempt_matrix_identity_rejections(
    tmp_path: pathlib.Path,
) -> None:
    _config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)

    class _BadDriver(InProcessDriver):
        def __init__(self, state, **mutate):
            super().__init__(state)
            self._mutate = mutate

        def prepare(self, *, request):
            result = super().prepare(request=request)
            from dataclasses import replace

            identity = result.identity
            return replace(result, identity=replace(identity, **self._mutate))

    cases = [
        ({"source_id": "ifs"}, "identity.source_id"),
        ({"cycle_time": datetime(2026, 8, 27, 0, tzinfo=UTC)}, "identity.cycle_time"),
        ({"project_name": "other"}, "identity.project_name"),
    ]
    for index, (mutate, fragment) in enumerate(cases):
        case_dir = tmp_path / f"case-{index}"
        case_dir.mkdir()
        case_config, case_local = write_config_local(case_dir)
        write_variant(case_local)
        write_state(case_local)
        write_raw_cycle(case_local)
        driver = _BadDriver(HookState(), **mutate)
        fake = _fake()
        with pytest.raises(RunError) as excinfo:
            run_once(
                config=case_config,
                local=case_local,
                source="gfs",
                executor=fake,
                driver=driver,
                poll_wait=lambda: None,
            )
        assert excinfo.value.phase == "prepare"
        # 每条必须命中自己的 identity 闸（phase 相等不足以证明到达该闸）。
        assert fragment in str(excinfo.value)
        assert fake.submissions == ()


def test_prepared_attempt_matrix_command_and_dat_rejections(
    tmp_path: pathlib.Path,
) -> None:
    _config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)

    class _BadDriver(InProcessDriver):
        def __init__(self, state, *, command=None, dat=None):
            super().__init__(state)
            self._command = command
            self._dat = dat

        def prepare(self, *, request):
            result = super().prepare(request=request)
            from dataclasses import replace

            command = self._command if self._command is not None else result.command
            dat = self._dat(request) if self._dat is not None else result.scratch_dat
            return replace(result, command=command, scratch_dat=dat)

    def request_work_escape(request):
        return pathlib.Path("/tmp") / "outside" / "dat"

    def request_preexisting_intree(request):
        # 预存 DAT 在**本轮 attempt work 内**：证明拒绝的是「提交前已存在」而非路径
        # 越界；若实现按旧逻辑「含 `..` 才拒」或按 /tmp 全局路径，本条即判别器。
        path = request.work_dir / "output" / "yd.rivqdown.dat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    def request_dotdot_parent(request):
        # 词法父路径含 `..`（解析后仍在 work 内也拒绝：词法/解析必须一致）。
        return request.work_dir / "output" / ".." / "output" / "dat"

    def request_symlink_parent(request):
        # 父目录是 symlink（指向 work 内合法位置也拒绝：解析后与词法分叉）。
        work = request.work_dir
        real = work / "real-output"
        real.mkdir(parents=True, exist_ok=True)
        link = work / "output"
        link.symlink_to(real)
        return link / "dat"

    # 每条路径都从 request 派生（tmp_path/request 内），不再写固定全局 /tmp 文件。
    # fragment = 该腿必须命中的闸门消息子串（phase 相等不足以证明到达目标守卫）。
    cases = [
        ({"command": ()}, "command 必须是非空 tuple"),
        ({"command": ("shud", "")}, "command 的每项必须是非空 str"),
        ({"command": ("shud", "a\x00b")}, "command 含 NUL 字节"),
        ({"command": None, "dat": request_work_escape}, "无歧义"),
        (
            {"command": None, "dat": request_preexisting_intree},
            "提交前必须不存在",
        ),
        (
            {"command": None, "dat": request_dotdot_parent},
            "无歧义",
        ),
        (
            {"command": None, "dat": request_symlink_parent},
            "无歧义",
        ),
    ]
    for index, (kwargs, fragment) in enumerate(cases):
        case_dir = tmp_path / f"cmd-{index}"
        case_dir.mkdir()
        case_config, case_local = write_config_local(case_dir)
        write_variant(case_local)
        write_state(case_local)
        write_raw_cycle(case_local)
        driver = _BadDriver(HookState(), **kwargs)
        fake = _fake()
        with pytest.raises(RunError) as excinfo:
            run_once(
                config=case_config,
                local=case_local,
                source="gfs",
                executor=fake,
                driver=driver,
                poll_wait=lambda: None,
            )
        assert excinfo.value.phase == "prepare"
        assert fragment in str(excinfo.value)
        assert fake.submissions == ()


# --- Required evidence 14: submit/poll identity ----------------------------------


def test_submit_returns_exact_spec_identity(tmp_path: pathlib.Path) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    driver = InProcessDriver(HookState())

    class _WrongExecutor(FakeJobExecutor):
        def submit(self, spec):
            record = super().submit(spec)
            from dataclasses import replace

            return replace(record, name="wrong-name")

    fake = _WrongExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.phase == "submit"
    assert "submit 返回的 name" in str(excinfo.value)
    assert len(fake.submissions) == 1  # 提交已发生（被身份守卫拒绝的是返回的 name）


def test_submit_whitespace_job_id_is_rejected(tmp_path: pathlib.Path) -> None:
    """submit 返回纯空白 job_id：`_require_spec_record` 以 phase submit 拒绝。

    判据是 `strip()` 探针（与 JobRunReport 同规）：空白 ID 拒绝，但有效 ID 原样保留、
    不归一。用精确编排的 fake 保证「被拒的是 job_id」而非「未被编排」的二次错误。
    """
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    driver = InProcessDriver(HookState())

    class _WhitespaceId(FakeJobExecutor):
        def submit(self, spec):
            record = super().submit(spec)
            from dataclasses import replace

            return replace(record, job_id="   ")

    fake = _WhitespaceId(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.phase == "submit"
    assert "空/纯空白 job_id" in str(excinfo.value)
    assert len(fake.submissions) == 1


def test_submit_terminal_is_rejected(tmp_path: pathlib.Path) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    driver = InProcessDriver(HookState())

    class _TerminalOnSubmit(FakeJobExecutor):
        def submit(self, spec):
            record = super().submit(spec)
            from yd_producer.executor import JobRecord

            return JobRecord(
                job_id=record.job_id,
                name=record.name,
                state=JobState.SUCCEEDED,
                resources=record.resources,
                submitted_at=record.submitted_at,
                started_at=record.submitted_at + timedelta(seconds=1),
                ended_at=record.submitted_at + timedelta(seconds=2),
            )

    fake = _TerminalOnSubmit(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.phase == "submit"
    assert "submit 不得返回终态" in str(excinfo.value)
    assert excinfo.value.job_id == "fake-1"
    assert len(fake.submissions) == 1  # 提交已发生（被拒的是返回的终态记录）


def test_poll_record_state_regression_is_rejected(tmp_path: pathlib.Path) -> None:
    """RUNNING 的 started_at 出现后消失：poll 单调性守卫拒绝。"""
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    driver = InProcessDriver(HookState())

    class _RegressionExecutor(FakeJobExecutor):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._running_returns = 0

        def poll(self, job_id):
            record = super().poll(job_id)
            if record.state is JobState.RUNNING and record.started_at is not None:
                self._running_returns += 1
                if self._running_returns >= 2:
                    from dataclasses import replace

                    # started_at 出现后又消失：poll 单调性守卫必须拒绝。
                    return replace(record, started_at=None)
            return record

    fake = _RegressionExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=2, started=True
            )
        },
        clock=step_clock(),
    )
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.phase == "poll"
    assert "started_at" in str(excinfo.value)
    assert excinfo.value.job_id == "fake-1"


def test_poll_first_immediately_and_wait_per_nonterminal(
    tmp_path: pathlib.Path,
) -> None:
    """首个 poll 立即发生；wait 调用数恰等于非终态 poll 结果数（0 终态首 poll -> 0 wait）。"""
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=0, started=True
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
    waits = []

    def make_hook(*, job_id):
        make_terminal_hook(request_slot["request"], state)()

    hook_executor = HookedExecutor(fake, make_hook)
    report = run_once(
        config=config,
        local=local,
        source="gfs",
        executor=hook_executor,
        driver=driver,
        poll_wait=lambda: waits.append(1),
    )
    assert report.outcome is RunOutcome.SUCCEEDED
    assert waits == []


# --- Required evidence 15/16: terminal timing & products matrix ------------------


def test_collect_creates_nothing_and_products_must_match(
    tmp_path: pathlib.Path,
) -> None:
    """让 driver.collect 才创建 DAT -> collect phase 拒绝（products 不存在）。"""
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)

    class _LazyCreateDriver(InProcessDriver):
        def collect(self, *, attempt, terminal_record):
            # 未执行 hook：collect 时创建 = 不诚实
            attempt.scratch_dat.parent.mkdir(parents=True, exist_ok=True)
            attempt.scratch_dat.write_bytes(b"x")
            return super().collect(attempt=attempt, terminal_record=terminal_record)

    driver = _LazyCreateDriver(HookState())
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=JobState.SUCCEEDED, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    with pytest.raises(RunError) as excinfo:
        run_once(
            config=config,
            local=local,
            source="gfs",
            executor=fake,
            driver=driver,
            poll_wait=lambda: None,
        )
    assert excinfo.value.phase == "collect"
    assert excinfo.value.job_id == "fake-1"
    assert "在 collect 前不存在" in str(excinfo.value) or "terminal hook 未在" in str(
        excinfo.value
    )


def test_checkpoint_disk_snapshot_without_memory_record_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """磁盘规范名 + 新鲜 tracker 无内存 record：`ensure_twelve_hour_checkpoint` 判残留。

    旧 hook 让 HookState 空置，失败其实发生在 products 校验（tracker 为 None），根本没
    走到 authority。修复后的判别器：真实 hook 生成合法 RunDirectory/DAT/log/canonical
    并登记进 tracker1；collect 交回**同 path/project 的 fresh tracker2**（无内存 record）。
    必须到达 `ensure_twelve_hour_checkpoint` 且因「disk canonical alone is residue」
    失败（canonical 已存在 -> 未验证残留，不采纳、runner 零调用），零 publish/DONE。
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

    original_collect = driver.collect
    runner_calls = []

    def fresh_tracker_collect(*, attempt, terminal_record):
        products = original_collect(attempt=attempt, terminal_record=terminal_record)
        # 同 path/project、无内存 record 的新 tracker：磁盘规范名不自证 authority。
        from yd_producer.tracker import CheckpointTracker

        runner_calls.append("fresh-tracker")
        fresh = CheckpointTracker(
            run_dir=products.run_directory.path,
            project_name=products.run_directory.project_name,
            checkpoint_hours=(12,),
        )
        from dataclasses import replace

        return replace(products, tracker=fresh)

    driver.collect = fresh_tracker_collect  # type: ignore[method-assign]

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
    assert "residue" in str(excinfo.value) or "absent" in str(excinfo.value)
    # 全新 tracker 无内存 record：ensure 走「canonical 必须 absent」分支 -> residue 拒绝，
    # 绝不让「盘上已有规范名」被采纳；runner（controller 侧必抛的 `_require_no_recovery`）
    # 若被调用则整轮改变——此处它只被捕获一次作为证据。
    assert runner_calls == ["fresh-tracker"]
    # fresh tracker 的 captured 为空的并集：controller 端 `captured[12] is None`，
    # 但在 ensure 已失败（TrackerError），DONE 必不存在。
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()


# --- Required evidence 17: FAILED/TIMEOUT ----------------------------------------


@pytest.mark.parametrize("final_state", [JobState.FAILED, JobState.TIMEOUT])
def test_failed_and_timeout_return_job_failed_without_publish(
    tmp_path: pathlib.Path, final_state: JobState
) -> None:
    config, local = write_config_local(tmp_path)
    write_variant(local)
    write_state(local)
    write_raw_cycle(local)
    driver = InProcessDriver(HookState())
    fake = FakeJobExecutor(
        outcomes={
            JOB_NAME: FakeOutcome(
                final_state=final_state, polls_until_terminal=1, started=True
            )
        },
        clock=step_clock(),
    )
    work_dir = (
        pathlib.Path(local.scratch_root).resolve() / "work" / "gfs" / "2026082612"
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
    assert report.job is not None and report.job.state is final_state
    assert report.job.ended_at is not None
    assert report.published is None and report.done_path is None
    assert not (
        pathlib.Path(local.yd_root) / "output" / "2026082612" / "gfs" / "DONE"
    ).exists()
    assert work_dir.exists()
