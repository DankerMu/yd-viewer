"""Regression coverage for shared in-process terminal-hook serialization."""

from __future__ import annotations

import pathlib
import threading

import run_once_fixtures
from controller_sources_fixtures import (
    CYCLE_T,
    GFS_EXIT,
    IFS_EXIT,
    DualBarrier,
    RecordingProvider,
    TerminalHookGate,
    hooked_success_cycles,
    noop_wait,
    require_source_tuple,
    write_dual_tree,
)

from yd_producer import publish as publish_module
from yd_producer.controller import RunOutcome, run_sources


class _TerminalTopology:
    """Event-driven proof of shared gate topology at the real NetCDF seam."""

    def __init__(self, canonical_netcdf) -> None:
        self._canonical_netcdf = canonical_netcdf
        self._lock = threading.Lock()
        self._before_gate = threading.Barrier(2, timeout=5)
        self.arrivals = 0
        self.distinct_gates = 0
        self.body_active = 0
        self.body_max_active = 0
        self.canonical_active = 0
        self.canonical_max_active = 0
        self.canonical_sources: list[str] = []
        self._gate_ids: set[int] = set()
        self._canonical_arrived = threading.Event()
        self._canonical_release = threading.Event()
        self._second_canonical_arrived = threading.Event()
        self._call_lock = threading.Lock()
        self._calls = 0

    def before_gate(self, gate: object) -> None:
        with self._lock:
            self.arrivals += 1
            self._gate_ids.add(id(gate))
            if self.arrivals == 2:
                self.distinct_gates = len(self._gate_ids)
        self._before_gate.wait()

    def enter_body(self) -> None:
        with self._lock:
            self.body_active += 1
            self.body_max_active = max(self.body_max_active, self.body_active)

    def leave_body(self) -> None:
        with self._lock:
            self.body_active -= 1

    def canonical_netcdf(self, variable, value, unit, number, lead):
        with self._call_lock:
            self._calls += 1
            first_call = self._calls == 1
        if first_call:
            self._canonical_arrived.set()
            if not self._canonical_release.wait(timeout=5):
                raise TimeoutError("canonical seam release did not arrive")
        else:
            self._second_canonical_arrived.set()
        with self._lock:
            self.canonical_active += 1
            self.canonical_max_active = max(
                self.canonical_max_active, self.canonical_active
            )
            self.canonical_sources.append(value.source_id)
        try:
            return self._canonical_netcdf(variable, value, unit, number, lead)
        finally:
            with self._lock:
                self.canonical_active -= 1


class _ProbedTerminalHookGate(TerminalHookGate):
    """Rendezvous immediately before acquiring the inherited terminal-body lock."""

    def __init__(self, topology: _TerminalTopology) -> None:
        super().__init__()
        self._topology = topology

    def run(self, body) -> None:
        self._topology.before_gate(self)

        def counted_body() -> None:
            self._topology.enter_body()
            try:
                body()
            finally:
                self._topology.leave_body()

        super().run(counted_body)


def test_shared_terminal_gate_serializes_body_netcdf_and_not_controller_work(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Both terminal bodies share one gate; job/collect stay parallel, publish does not."""
    config, local = write_dual_tree(tmp_path)
    jobs = DualBarrier()
    topology = _TerminalTopology(run_once_fixtures.canonical_netcdf)
    gate = _ProbedTerminalHookGate(topology)
    monkeypatch.setattr(
        run_once_fixtures,
        "canonical_netcdf",
        topology.canonical_netcdf,
    )

    def release_first_canonical_call() -> None:
        if not topology._canonical_arrived.wait(timeout=5):
            raise TimeoutError("first terminal body did not reach canonical NetCDF")
        if topology.distinct_gates == 2 and not topology._second_canonical_arrived.wait(
            timeout=5
        ):
            raise TimeoutError("second terminal body did not reach canonical NetCDF")
        topology._canonical_release.set()

    canonical_releaser = threading.Thread(
        target=release_first_canonical_call, daemon=True
    )
    canonical_releaser.start()
    drivers = {}
    executors = {}
    for source in ("ifs", "gfs"):
        driver, executor = hooked_success_cycles(
            source,
            (CYCLE_T,),
            barrier=jobs,
            gate=gate,
        )
        drivers[source] = driver
        executors[source] = executor

    collect_pair = threading.Barrier(2, timeout=5)
    collect_lock = threading.Lock()
    collect_active = 0
    collect_max_active = 0
    for driver in drivers.values():
        original_collect = driver.collect

        def counting_collect(*, attempt, terminal_record, _original=original_collect):
            nonlocal collect_active, collect_max_active
            with collect_lock:
                collect_active += 1
                collect_max_active = max(collect_max_active, collect_active)
            try:
                collect_pair.wait()
                return _original(attempt=attempt, terminal_record=terminal_record)
            finally:
                with collect_lock:
                    collect_active -= 1

        driver.collect = counting_collect  # type: ignore[method-assign]

    publish_lock = threading.Lock()
    publish_active = 0
    publish_max_active = 0
    original_publish = publish_module.publish

    def counting_publish(inputs):
        nonlocal publish_active, publish_max_active
        with publish_lock:
            publish_active += 1
            publish_max_active = max(publish_max_active, publish_active)
        try:
            return original_publish(inputs)
        finally:
            with publish_lock:
                publish_active -= 1

    monkeypatch.setattr(publish_module, "publish", counting_publish)
    report = run_sources(
        config=config,
        local=local,
        executors=executors,
        drivers=drivers,
        poll_waits={"ifs": noop_wait, "gfs": noop_wait},
        failure_exit_codes={
            "ifs": RecordingProvider("ifs", IFS_EXIT),
            "gfs": RecordingProvider("gfs", GFS_EXIT),
        },
    )

    canonical_releaser.join(timeout=5)
    assert not canonical_releaser.is_alive()
    ifs = require_source_tuple(report.ifs, "ifs")
    gfs = require_source_tuple(report.gfs, "gfs")
    assert [item.outcome for item in ifs] == [RunOutcome.SUCCEEDED, RunOutcome.STOPPED]
    assert [item.outcome for item in gfs] == [RunOutcome.SUCCEEDED, RunOutcome.STOPPED]
    assert topology.arrivals == 2
    assert topology._calls >= 14
    assert topology.distinct_gates == 1
    assert topology.body_max_active == 1
    assert topology.canonical_max_active == 1
    assert set(topology.canonical_sources) == {"ifs", "gfs"}
    assert gate.max_active == 1
    assert jobs.max_inflight == 2
    assert collect_max_active == 2
    assert publish_max_active == 1
