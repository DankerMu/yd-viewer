"""Issue #77: caller-owned realpath of scratch_root before AttemptRequest.

Regression closure of the already-landed Choice A freeze at
``_controller_run._run_once`` (claim / AttemptRequest). Two public scenes:

1. IFS/GFS public ``run_once`` through an unresolved absolute symlink ancestor
   of ``local.scratch_root`` (alias-parent -> real-parent; scratch leaf is a
   plain directory). Capture / publish / DONE / checksum / sentinel are the
   oracles, not constructor kwargs.
2. One real terminal-hook capture lifecycle that writes A-360, chdirs to a
   same-relative B decoy 720 without capturing, then captures A-720 and
   publishes A bytes.

Relative ``local.scratch_root`` stays the existing preflight refusal in
``test_controller_run_once_failures.py``
(``test_product_preflight_fails_before_driver_and_executor[relative_scratch]``).
The existing preflight regression is reused rather than duplicated. Tracker,
safe_fs, assemble, staged_inputs, and CLI production behavior are unchanged.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from cfg_ic_fixtures import build_cfg_ic
from run_once_fixtures import (
    CYCLE,
    IFS_JOB_NAME,
    JOB_NAME,
    PROJECT,
    REACH_COUNT,
    HookedExecutor,
    HookState,
    InProcessDriver,
    checkpoint_payload,
    cycle_text,
    make_terminal_hook,
    step_clock,
    success_outcome,
    write_config_local,
    write_raw_cycle,
    write_state,
    write_variant,
)

from yd_producer.controller import RunOutcome, run_once
from yd_producer.executor import FakeJobExecutor, FakeOutcome, JobState
from yd_producer.state import parse, render, restamp_to_absolute_time
from yd_producer.tracker import CheckpointTracker

#: Independent T+12 absolute minute (2026-08-27 00Z): 20692 * 1440 = 29796480.
T_PLUS_12_ABSOLUTE_MINUTE = "29796480.000000"
SENTINEL_BYTES = b"issue-77-outside-sentinel-unchanged\n"
RELATIVE_MINUTE_360 = "360.000000"
CANONICAL_NAME = f"{PROJECT}.f012.cfg.ic.update"
UPDATE_NAME = f"{PROJECT}.cfg.ic.update"


def _payload_360() -> bytes:
    return build_cfg_ic(
        mesh_count=3, river_count=REACH_COUNT, minute=RELATIVE_MINUTE_360
    ).payload


def _payload_720(*, mixed_notation: bool) -> bytes:
    return build_cfg_ic(
        mesh_count=3,
        river_count=REACH_COUNT,
        minute="720.000000",
        mixed_notation=mixed_notation,
    ).payload


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _expected_published_state(payload: bytes) -> bytes:
    """Independent T+12 restamp of a known-good 720 payload (not from publish)."""
    return render(restamp_to_absolute_time(parse(payload), CYCLE + timedelta(hours=12)))


def _plant_unresolved_scratch_alias(
    tmp_path: Path, local
) -> tuple[object, Path, Path, Path]:
    """Replace frozen ``scratch_root`` with unresolved ``alias-parent/scratch``.

    Layout: ``real-parent/scratch`` is a plain directory; ``alias-parent`` is a
    symlink to ``real-parent``. The scratch leaf itself is not a link. The
    returned LocalConfig field is the unresolved alias string; this helper does
    not pass the alias through ``make_local`` or ``work_dir_for``.
    """
    real_parent = (tmp_path / "real-parent").resolve()
    real_scratch = real_parent / "scratch"
    real_scratch.mkdir(parents=True)
    alias_parent = tmp_path / "alias-parent"
    alias_parent.symlink_to(real_parent)
    unresolved = str(alias_parent / "scratch")
    assert Path(unresolved) != Path(unresolved).resolve()
    assert Path(unresolved).resolve() == real_scratch
    assert not Path(unresolved).is_symlink()
    assert alias_parent.is_symlink()
    original = Path(local.scratch_root)
    if original.exists() and original.resolve() != real_scratch:
        original.rmdir()
    local = replace(local, scratch_root=unresolved)
    assert local.scratch_root == unresolved
    assert Path(local.scratch_root) != Path(local.scratch_root).resolve()
    sentinel = tmp_path / "outside-sentinel.bin"
    sentinel.write_bytes(SENTINEL_BYTES)
    return local, alias_parent, real_scratch, sentinel


def _snapshot_capture_then_collect(
    driver: InProcessDriver, expected_path: Path
) -> dict:
    """Snapshot canonical capture bytes before publisher cleanup, then collect."""
    original_collect = driver.collect
    snapshot: dict = {}

    def wrapping_collect(*, attempt, terminal_record):
        snapshot["exists"] = expected_path.is_file()
        snapshot["bytes"] = (
            expected_path.read_bytes() if expected_path.is_file() else None
        )
        snapshot["path"] = expected_path
        return original_collect(attempt=attempt, terminal_record=terminal_record)

    driver.collect = wrapping_collect  # type: ignore[method-assign]
    return snapshot


def _bind_success_executor(
    *, source: str, driver: InProcessDriver, state: HookState, before_worker=None
) -> tuple[HookedExecutor, dict]:
    job_name = IFS_JOB_NAME if source == "ifs" else JOB_NAME
    fake = FakeJobExecutor(
        outcomes={
            job_name: FakeOutcome(
                final_state=JobState.SUCCEEDED,
                polls_until_terminal=1,
                started=True,
            )
        },
        clock=step_clock(),
    )
    request_slot: dict = {}
    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]

    def make_hook(*, job_id):
        make_terminal_hook(
            request_slot["request"], state, before_worker=before_worker
        )()

    return HookedExecutor(fake, make_hook), request_slot


@pytest.mark.parametrize("source", ["ifs", "gfs"])
def test_unresolved_scratch_alias_ancestor_captures_and_publishes(
    tmp_path: Path, source: str
) -> None:
    """Public run_once through unresolved alias-parent/scratch succeeds on the real tree."""
    config, local = write_config_local(tmp_path, source=source)
    local, alias_parent, real_scratch, sentinel = _plant_unresolved_scratch_alias(
        tmp_path, local
    )
    write_variant(local, source=source)
    write_state(local, source=source)
    write_raw_cycle(local, source=source)

    cycle_name = cycle_text(CYCLE)
    real_work = real_scratch / "work" / source / cycle_name
    canonical = real_work / "model" / "state_checkpoints" / CANONICAL_NAME
    expected_payload = checkpoint_payload()
    expected_checksum = _sha256(expected_payload)
    expected_published = _expected_published_state(expected_payload)
    assert expected_published != expected_payload
    assert T_PLUS_12_ABSOLUTE_MINUTE.encode() in expected_published

    state = HookState()
    driver = InProcessDriver(state)
    snapshot = _snapshot_capture_then_collect(driver, canonical)
    executor, request_slot = _bind_success_executor(
        source=source, driver=driver, state=state
    )
    report = run_once(
        config=config,
        local=local,
        source=source,
        executor=executor,
        driver=driver,
        poll_wait=lambda: None,
    )
    request = request_slot["request"]

    assert local.scratch_root == str(alias_parent / "scratch")
    assert Path(local.scratch_root) != Path(local.scratch_root).resolve()
    assert request.work_dir == real_work
    assert request.work_root == real_scratch / "work"
    assert request.work_dir.is_absolute()
    assert "alias-parent" not in request.work_dir.parts

    assert report.outcome is RunOutcome.SUCCEEDED
    assert report.cycle == CYCLE
    assert report.stop_reason is None
    assert report.published is not None
    assert report.done_path == report.published.done_path
    assert report.done_path.is_file()
    assert report.done_path.read_bytes() == b""
    assert report.published.state_path.is_file()
    assert report.published.state_path.read_bytes() == expected_published
    assert report.published.next_cycle == CYCLE + timedelta(hours=12)
    assert report.published.removed_work_dir == real_work

    tracker = state.tracker
    assert tracker is not None
    assert tracker.observed_header_minutes == (720.0,)
    assert tracker.missing_hours() == ()
    captured = tracker.captured[12]
    assert captured.lead_hours == 12
    assert captured.relative_minute == 720.0
    assert captured.path == canonical
    assert captured.path.is_absolute()
    assert captured.path == captured.path.resolve()
    assert real_scratch in captured.path.parents
    assert "alias-parent" not in captured.path.parts
    assert captured.checksum == expected_checksum
    assert captured.source_name == UPDATE_NAME

    assert snapshot["exists"] is True
    assert snapshot["bytes"] == expected_payload
    assert snapshot["path"] == canonical
    assert _sha256(snapshot["bytes"]) == expected_checksum

    assert not real_work.exists()
    assert alias_parent.is_symlink()
    assert alias_parent.resolve() == (tmp_path / "real-parent").resolve()
    assert sentinel.read_bytes() == SENTINEL_BYTES
    assert Path(local.scratch_root).resolve() == real_scratch


def test_in_hook_cwd_shift_captures_a_720_not_b_decoy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A-360 observe, chdir B same-relative 720 no capture, then A-720 captures A."""
    original_cwd = Path.cwd()
    cwd_a = tmp_path.resolve()
    cwd_b = (tmp_path / "cwd-b").resolve()
    cwd_b.mkdir()
    config, local = write_config_local(cwd_a, source="gfs")
    write_variant(local, source="gfs")
    write_state(local, source="gfs")
    write_raw_cycle(local, source="gfs")

    payload_360 = _payload_360()
    payload_a_720 = _payload_720(mixed_notation=False)
    payload_b_720 = _payload_720(mixed_notation=True)
    assert payload_360 != payload_a_720
    assert payload_a_720 != payload_b_720
    parse(payload_360)
    parse(payload_a_720)
    parse(payload_b_720)
    expected_checksum = _sha256(payload_a_720)
    expected_published = _expected_published_state(payload_a_720)
    assert T_PLUS_12_ABSOLUTE_MINUTE.encode() in expected_published
    assert expected_published != payload_b_720

    a_absolute: dict = {}
    phases: list[tuple[str, tuple[float, ...], tuple[int, ...]]] = []
    original_capture = CheckpointTracker.capture_available

    def sequenced_capture(self: CheckpointTracker) -> None:
        a_model = a_absolute["model"]
        a_update = a_model / UPDATE_NAME
        relative_from_a = os.path.relpath(a_model, cwd_a)
        b_model = cwd_b / relative_from_a
        b_update = b_model / UPDATE_NAME
        b_canonical = b_model / "state_checkpoints" / CANONICAL_NAME
        if not phases:
            a_update.write_bytes(payload_360)
            original_capture(self)
            phases.append(("a360", self.observed_header_minutes, self.missing_hours()))
            assert self.observed_header_minutes == (360.0,)
            assert self.missing_hours() == (12,)
            assert 12 not in self.captured
            b_model.mkdir(parents=True, exist_ok=True)
            b_update.write_bytes(payload_b_720)
            os.chdir(cwd_b)
            original_capture(self)
            phases.append(("b720", self.observed_header_minutes, self.missing_hours()))
            assert Path.cwd() == cwd_b
            assert self.observed_header_minutes == (360.0,)
            assert self.missing_hours() == (12,)
            assert 12 not in self.captured
            assert not b_canonical.exists()
            a_update.write_bytes(payload_a_720)
            original_capture(self)
            phases.append(("a720", self.observed_header_minutes, self.missing_hours()))
            captured = self.captured[12]
            assert Path.cwd() == cwd_b
            assert captured.path == a_model / "state_checkpoints" / CANONICAL_NAME
            assert captured.path.is_absolute()
            assert captured.path.read_bytes() == payload_a_720
            assert captured.checksum == expected_checksum
            return
        original_capture(self)

    monkeypatch.setattr(CheckpointTracker, "capture_available", sequenced_capture)

    state = HookState()
    driver = InProcessDriver(state)
    fake = FakeJobExecutor(
        outcomes={JOB_NAME: success_outcome(polls_until_terminal=1)},
        clock=step_clock(),
    )
    request_slot: dict = {}
    original_prepare = driver.prepare

    def capturing_prepare(*, request):
        request_slot["request"] = request
        return original_prepare(request=request)

    driver.prepare = capturing_prepare  # type: ignore[method-assign]

    def before_worker(request) -> None:
        # Independent of tracker.run_dir so a relative-run_dir mutant still
        # reaches the wrong tree instead of a tautological field copy.
        a_absolute["model"] = (request.work_dir / "model").resolve()
        a_absolute["work"] = request.work_dir.resolve()

    def make_hook(*, job_id):
        try:
            make_terminal_hook(
                request_slot["request"], state, before_worker=before_worker
            )()
        finally:
            os.chdir(cwd_a)

    snapshot: dict = {}
    original_collect = driver.collect

    def collect_and_snapshot(*, attempt, terminal_record):
        canonical = a_absolute["model"] / "state_checkpoints" / CANONICAL_NAME
        snapshot["bytes"] = canonical.read_bytes()
        snapshot["path"] = canonical
        return original_collect(attempt=attempt, terminal_record=terminal_record)

    driver.collect = collect_and_snapshot  # type: ignore[method-assign]
    executor = HookedExecutor(fake, make_hook)
    os.chdir(cwd_a)
    try:
        report = run_once(
            config=config,
            local=local,
            source="gfs",
            executor=executor,
            driver=driver,
            poll_wait=lambda: None,
        )
    finally:
        os.chdir(original_cwd)

    a_model = a_absolute["model"]
    a_work = a_absolute["work"]
    canonical = a_model / "state_checkpoints" / CANONICAL_NAME
    relative_from_a = os.path.relpath(a_model, cwd_a)
    assert not Path(relative_from_a).is_absolute()
    assert ".." not in Path(relative_from_a).parts
    b_model = cwd_b / relative_from_a
    b_canonical = b_model / "state_checkpoints" / CANONICAL_NAME

    assert phases == [
        ("a360", (360.0,), (12,)),
        ("b720", (360.0,), (12,)),
        ("a720", (360.0, 720.0), ()),
    ]
    assert report.outcome is RunOutcome.SUCCEEDED
    assert report.published is not None
    assert report.done_path.is_file()
    assert report.published.state_path.read_bytes() == expected_published
    assert snapshot["bytes"] == payload_a_720
    assert snapshot["path"] == canonical
    assert snapshot["path"].is_absolute()

    tracker = state.tracker
    assert tracker is not None
    assert tracker.observed_header_minutes == (360.0, 720.0)
    assert tracker.missing_hours() == ()
    captured = tracker.captured[12]
    assert captured.path == canonical
    assert captured.path.is_absolute()
    assert captured.checksum == expected_checksum
    assert captured.lead_hours == 12
    assert captured.relative_minute == 720.0
    assert not b_canonical.exists()
    assert (b_model / UPDATE_NAME).read_bytes() == payload_b_720
    assert not a_work.exists()
    assert Path.cwd() == original_cwd
    assert report.published.removed_work_dir == a_work
