"""Round 1 contract: mixed mapping keys raise ValueError, never TypeError."""

from __future__ import annotations

import inspect
import pathlib

import pytest
from controller_sources_fixtures import (
    GFS_EXIT,
    RecordingProvider,
    noop_wait,
    success_driver,
    write_dual_tree,
)
from frontier_fixtures import snapshot_tree
from run_once_fixtures import CYCLE, JOB_NAME, step_clock

from yd_producer.controller import (
    RunError,
    RunOutcome,
    RunReport,
    RunSourcesError,
    StopReason,
    run_sources,
)
from yd_producer.executor import FakeJobExecutor, FakeOutcome, JobState


class _ObjectKey:
    def __repr__(self) -> str:
        return "object-key"


def _good_kwargs(tmp_path: pathlib.Path):
    config, local = write_dual_tree(tmp_path)
    ifs_driver, _, _ = success_driver()
    gfs_driver, _, _ = success_driver()
    executors = {
        "ifs": FakeJobExecutor(
            outcomes={
                "yd-ifs-2026082612": FakeOutcome(
                    final_state=JobState.SUCCEEDED,
                    polls_until_terminal=1,
                    started=True,
                )
            },
            clock=step_clock(),
        ),
        "gfs": FakeJobExecutor(
            outcomes={
                JOB_NAME: FakeOutcome(
                    final_state=JobState.SUCCEEDED,
                    polls_until_terminal=1,
                    started=True,
                )
            },
            clock=step_clock(),
        ),
    }
    return {
        "config": config,
        "local": local,
        "executors": executors,
        "drivers": {"ifs": ifs_driver, "gfs": gfs_driver},
        "poll_waits": {"ifs": noop_wait, "gfs": noop_wait},
        "failure_exit_codes": {
            "ifs": RecordingProvider("ifs"),
            "gfs": RecordingProvider("gfs", GFS_EXIT),
        },
    }


@pytest.mark.parametrize(
    "mapping_name", ["executors", "drivers", "poll_waits", "failure_exit_codes"]
)
def test_mixed_mapping_keys_raise_value_error_before_any_io(
    tmp_path: pathlib.Path, mapping_name: str
) -> None:
    kwargs = _good_kwargs(tmp_path)
    local = kwargs["local"]
    sample = kwargs[mapping_name]["ifs"]
    original_executors = kwargs["executors"]
    kwargs[mapping_name] = {"ifs": sample, 1: sample}
    before_yd = snapshot_tree(pathlib.Path(local.yd_root))
    before_scratch = snapshot_tree(pathlib.Path(local.scratch_root))
    with pytest.raises(ValueError) as info:
        run_sources(**kwargs)
    assert "TypeError" not in type(info.value).__name__
    assert snapshot_tree(pathlib.Path(local.yd_root)) == before_yd
    assert snapshot_tree(pathlib.Path(local.scratch_root)) == before_scratch
    assert original_executors["ifs"].submissions == ()
    assert original_executors["gfs"].submissions == ()


def test_mixed_run_sources_error_keys_raise_value_error() -> None:
    ok = RunReport(
        source="ifs",
        cycle=CYCLE,
        outcome=RunOutcome.STOPPED,
        stop_reason=StopReason.NO_INITIAL_STATE,
        detail="ok",
        job=None,
        published=None,
        done_path=None,
    )
    err = RunError("boom", phase="raw", source="ifs")
    with pytest.raises(ValueError):
        RunSourcesError({"ifs": (ok,), 7: ()}, {"ifs": err})
    with pytest.raises(ValueError):
        RunSourcesError(
            {"ifs": (), "gfs": ()},
            {"ifs": err, _ObjectKey(): err},
        )


def test_key_formatter_does_not_use_sorted_on_mixed_types() -> None:
    from yd_producer import _controller_sources as sources

    source = inspect.getsource(sources)
    assert "sorted(keys)" not in source
    assert "sorted(report_keys)" not in source
    assert "sorted(error_keys)" not in source
    mixed = {"ifs", 1}
    with pytest.raises(TypeError):
        sorted(mixed)
    formatted = sources._format_keys(mixed)
    assert isinstance(formatted, str)
    assert "ifs" in formatted
