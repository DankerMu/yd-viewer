"""Issue #136：同一 attempt receipt 到 T+12 tracker authority 的独立验收。"""

from __future__ import annotations

import ast
import hashlib
import inspect
import os
import stat
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cfg_ic_fixtures import build_cfg_ic

from yd_producer import state
from yd_producer import tracker as tracker_package
from yd_producer.assemble import RunDirectory, WorkIdentity
from yd_producer.store import safe_fs
from yd_producer.tracker import (
    CapturedCheckpoint,
    CheckpointTracker,
    TrackerError,
    ensure_twelve_hour_checkpoint,
)
from yd_producer.tracker import checkpoint_tracker as tracker_module

PROJECT = "demo"
HOUR = 12
MINUTE = 720.0
CANONICAL_NAME = f"{PROJECT}.f012.cfg.ic.update"
SOURCE_NAME = f"{PROJECT}.cfg.ic.update"


def _importer():  # type: ignore[no-untyped-def]
    """运行期查找使 pre-change red 进入 test body，而非 collection。"""
    return getattr(tracker_module, "import_verified_checkpoint")  # noqa: B009


def _payload(minute: str = "720.000000", *, mesh_count: int = 3) -> bytes:
    return build_cfg_ic(mesh_count=mesh_count, river_count=2, minute=minute).payload


def _truncated_payload() -> bytes:
    built = build_cfg_ic(mesh_count=3, river_count=2, minute="720.000000")
    removed = {built.mesh_data_indices[-1], built.river_data_indices[-1]}
    return "".join(
        line for index, line in enumerate(built.lines) if index not in removed
    ).encode()


def _record(tracker: CheckpointTracker, payload: bytes) -> CapturedCheckpoint:
    return CapturedCheckpoint(
        lead_hours=HOUR,
        relative_minute=MINUTE,
        path=_canonical(tracker),
        source_name=SOURCE_NAME,
        checksum=hashlib.sha256(payload).hexdigest(),
    )


def _canonical(tracker: CheckpointTracker) -> Path:
    return tracker.checkpoint_dir / CANONICAL_NAME


def _receipt_case(
    tmp_path: Path,
    *,
    payload: bytes | None = None,
    install: bool = True,
    checkpoint_hours: tuple[int, ...] = (HOUR,),
) -> tuple[CheckpointTracker, CapturedCheckpoint, Path, bytes]:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    tracker = CheckpointTracker(
        run_dir=run_dir,
        project_name=PROJECT,
        checkpoint_hours=checkpoint_hours,
    )
    current = _payload() if payload is None else payload
    canonical = _canonical(tracker)
    if install:
        canonical.parent.mkdir()
        canonical.write_bytes(current)
    return tracker, _record(tracker, current), canonical, current


def _entry_snapshot(path: Path) -> tuple[int, int, int, bytes] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    mode = stat.S_IFMT(info.st_mode)
    if stat.S_ISREG(info.st_mode):
        content = path.read_bytes()
    elif stat.S_ISLNK(info.st_mode):
        content = os.readlink(path).encode()
    else:
        content = b""
    return info.st_dev, info.st_ino, mode, content


def _ready_run_case(
    tmp_path: Path,
) -> tuple[RunDirectory, CheckpointTracker, CapturedCheckpoint, Path, bytes]:
    run_dir = tmp_path / "attempt" / "model"
    run_dir.mkdir(parents=True)
    identity = WorkIdentity(
        source_id="gfs",
        cycle_time=datetime(2026, 1, 1, tzinfo=UTC),
        model_id="demo-model",
        basin_id="demo-basin",
        basin_version_id="demo-basin-v1",
        river_network_version_id="demo-rivers-v1",
        project_name=PROJECT,
    )
    state_path = run_dir / f"{PROJECT}.cfg.ic"
    parameter_path = run_dir / f"{PROJECT}.para"
    forcing_index_path = run_dir / f"{PROJECT}.tsd.forc"
    forcing_csv = run_dir / "X1.csv"
    for path, content in (
        (state_path, b"initial state\n"),
        (parameter_path, b"parameter\n"),
        (forcing_index_path, b"forcing index\n"),
        (forcing_csv, b"forcing csv\n"),
    ):
        path.write_bytes(content)
    run_directory = RunDirectory(
        identity=identity,
        path=run_dir,
        project_name=PROJECT,
        state_path=state_path,
        parameter_path=parameter_path,
        forcing_index_path=forcing_index_path,
        forcing_csv_paths=(forcing_csv,),
    )
    tracker = CheckpointTracker(
        run_dir=run_dir,
        project_name=PROJECT,
        checkpoint_hours=(HOUR,),
    )
    payload = _payload()
    canonical = _canonical(tracker)
    canonical.parent.mkdir()
    canonical.write_bytes(payload)
    return run_directory, tracker, _record(tracker, payload), canonical, payload


class _ExplodingRunner:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *, run_directory: RunDirectory, output_dir: Path) -> int:
        del run_directory, output_dir
        self.calls += 1
        raise AssertionError("receipt import must not invoke recovery")


class _SetitemSpy(dict[int, CapturedCheckpoint]):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[tuple[int, CapturedCheckpoint]] = []

    def __setitem__(self, key: int, value: CapturedCheckpoint) -> None:
        self.writes.append((key, value))
        super().__setitem__(key, value)


def _dotted_name(node: ast.expr) -> str | None:
    pieces: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        pieces.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    pieces.append(current.id)
    return ".".join(reversed(pieces))


def _mutation_probes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """封住 import seam 可达的落盘原语；读取原语刻意不动。"""
    calls: list[str] = []

    def blocked(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        calls.append("mutation")
        raise AssertionError("receipt import attempted filesystem mutation")

    for name in (
        "ensure_directory_no_follow",
        "atomic_write_bytes_no_follow",
        "write_bytes_no_follow_exclusive",
        "unlink_no_follow",
        "rmtree_no_follow",
        "rename_entry_no_follow",
        "remove_tree_allow_symlinks",
        "verify_tree_no_symlinks",
    ):
        monkeypatch.setattr(safe_fs, name, blocked)
    for name in (
        "write_bytes",
        "write_text",
        "mkdir",
        "touch",
        "unlink",
        "rename",
        "replace",
        "rmdir",
        "chmod",
        "symlink_to",
        "hardlink_to",
    ):
        monkeypatch.setattr(Path, name, blocked)
    for name in (
        "write",
        "unlink",
        "remove",
        "rename",
        "replace",
        "mkdir",
        "rmdir",
        "fsync",
        "fchmod",
    ):
        monkeypatch.setattr(os, name, blocked)
    return calls


def test_public_receipt_import_shape_is_frozen_and_narrow() -> None:
    importer = _importer()
    package_importer = getattr(  # noqa: B009 — pre-change red 必须在 test body 内经 getattr 发生。
        tracker_package, "import_verified_checkpoint"
    )
    assert package_importer is importer
    expected_exports = (
        "CapturedCheckpoint",
        "CheckpointTracker",
        "RecoveryRunner",
        "TrackerError",
        "ensure_twelve_hour_checkpoint",
        "import_verified_checkpoint",
    )
    assert tuple(tracker_module.__all__) == expected_exports
    assert tuple(tracker_package.__all__) == expected_exports

    signature = inspect.signature(importer)
    assert tuple(signature.parameters) == ("tracker", "record")
    assert signature.return_annotation == "CapturedCheckpoint"
    for name, annotation in (
        ("tracker", "CheckpointTracker"),
        ("record", "CapturedCheckpoint"),
    ):
        parameter = signature.parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty
        assert parameter.annotation == annotation

    ensure_signature = inspect.signature(ensure_twelve_hour_checkpoint)
    assert tuple(ensure_signature.parameters) == ("tracker", "run_directory", "runner")
    assert ensure_signature.return_annotation == "CapturedCheckpoint"
    for name, annotation in (
        ("tracker", "CheckpointTracker"),
        ("run_directory", "RunDirectory"),
        ("runner", "RecoveryRunner"),
    ):
        parameter = ensure_signature.parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty
        assert parameter.annotation == annotation

    source = Path(tracker_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "import_verified_checkpoint"
    )
    segment = ast.get_source_segment(source, function)
    assert segment is not None
    call_names = [
        _dotted_name(call.func)
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
    ]
    assert call_names.count("_verify_captured_point_of_use") == 1
    assert not any(isinstance(node, ast.Try) for node in ast.walk(function))
    forbidden_calls = {
        "_candidate_gate",
        "state.parse",
        "ensure_twelve_hour_checkpoint",
        "_read_candidate",
        "_install_exclusive",
        "_require_absent",
    }
    assert not forbidden_calls.intersection(call_names)
    assert not any(
        name is not None and name.startswith(("safe_fs.", "os.", "Path."))
        for name in call_names
    )
    for forbidden_text in (
        "_candidate_gate",
        "state.parse",
        "safe_fs.",
        "RecoveryRunner",
    ):
        assert forbidden_text not in segment
    record_fields = {
        node.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "record"
    }
    assert record_fields == set()


def test_valid_receipt_is_imported_then_existing_ensure_rechecks_it(
    tmp_path: Path,
) -> None:
    run_directory, tracker, record, canonical, payload = _ready_run_case(tmp_path)
    before = _entry_snapshot(canonical)

    returned = _importer()(tracker=tracker, record=record)

    assert returned is record is tracker.captured[HOUR]
    assert tracker.missing_hours() == ()
    assert _entry_snapshot(canonical) == before

    runner = _ExplodingRunner()
    assert (
        ensure_twelve_hour_checkpoint(
            tracker=tracker,
            run_directory=run_directory,
            runner=runner,
        )
        is record
    )
    assert runner.calls == 0
    assert _entry_snapshot(canonical) == before

    drifted = _payload(mesh_count=4)
    assert drifted != payload
    canonical.write_bytes(drifted)
    with pytest.raises(TrackerError, match="checksum drifted"):
        ensure_twelve_hour_checkpoint(
            tracker=tracker,
            run_directory=run_directory,
            runner=runner,
        )
    assert runner.calls == 0
    assert tracker.captured[HOUR] is record


@pytest.mark.parametrize(
    "case",
    ["tracker", "record", "hours-720", "hours-6", "hours-6-12", "relative-root"],
)
def test_preflight_rejects_wrong_input_before_reader_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    run_dir = tmp_path / "preflight"
    run_dir.mkdir()
    tracker = CheckpointTracker(
        run_dir=run_dir,
        project_name=PROJECT,
        checkpoint_hours=(HOUR,),
    )
    record = _record(tracker, _payload())
    arguments: dict[str, object] = {"tracker": tracker, "record": record}
    guarded_tracker = tracker
    if case == "tracker":
        arguments["tracker"] = object()
    elif case == "record":
        arguments["record"] = object()
    elif case.startswith("hours-"):
        hours = {"hours-720": (720,), "hours-6": (6,), "hours-6-12": (6, 12)}[case]
        guarded_tracker = CheckpointTracker(
            run_dir=run_dir,
            project_name=PROJECT,
            checkpoint_hours=hours,
        )
        arguments["tracker"] = guarded_tracker
        arguments["record"] = _record(guarded_tracker, _payload())
    else:
        guarded_tracker = CheckpointTracker(
            run_dir=Path("relative-receipt-root"),
            project_name=PROJECT,
            checkpoint_hours=(HOUR,),
        )
        arguments["tracker"] = guarded_tracker
        arguments["record"] = _record(guarded_tracker, _payload())

    reads: list[Path] = []

    def unexpected_reader(path: Path, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
        del kwargs
        reads.append(path)
        raise AssertionError("preflight reached canonical reader")

    mutations = _mutation_probes(monkeypatch)
    monkeypatch.setattr(safe_fs, "read_bytes_limited_no_follow", unexpected_reader)
    with pytest.raises(TrackerError):
        _importer()(**arguments)  # type: ignore[arg-type]
    assert reads == []
    assert dict(guarded_tracker.captured) == {}
    assert mutations == []


@pytest.mark.parametrize(
    ("case", "expected_reads", "message"),
    [
        ("lead", 0, "lead_hours"),
        ("minute-1440", 0, "relative_minute"),
        ("minute-719.6", 0, "relative_minute"),
        ("minute-720.4", 0, "relative_minute"),
        ("minute-nan", 0, "relative_minute"),
        ("minute-inf", 0, "relative_minute"),
        ("outside-path", 0, "path differs"),
        ("relative-path", 0, "path differs"),
        ("wrong-canonical", 0, "path differs"),
        ("source", 0, "source_name"),
        ("checksum", 1, "checksum drifted"),
        ("checksum-none", 1, "checksum drifted"),
    ],
)
def test_record_field_gates_are_independent_and_do_not_mutate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_reads: int,
    message: str,
) -> None:
    tracker, record, canonical, payload = _receipt_case(tmp_path)
    outside: Path | None = None
    if case == "lead":
        record = replace(record, lead_hours=24)
    elif case == "minute-1440":
        record = replace(record, relative_minute=1440.0)
    elif case == "minute-719.6":
        record = replace(record, relative_minute=719.6)
    elif case == "minute-720.4":
        record = replace(record, relative_minute=720.4)
    elif case == "minute-nan":
        record = replace(record, relative_minute=float("nan"))
    elif case == "minute-inf":
        record = replace(record, relative_minute=float("inf"))
    elif case == "outside-path":
        outside = tmp_path / "outside.cfg.ic.update"
        outside.write_bytes(payload)
        record = replace(record, path=outside)
    elif case == "relative-path":
        record = replace(record, path=Path("relative.cfg.ic.update"))
    elif case == "wrong-canonical":
        record = replace(record, path=canonical.with_name("wrong.cfg.ic.update"))
    elif case == "source":
        record = replace(record, source_name="foreign.cfg.ic.update")
    elif case == "checksum":
        record = replace(record, checksum="0" * 64)
    elif case == "checksum-none":
        record = replace(record, checksum=None)  # type: ignore[arg-type]
    else:
        raise AssertionError(case)

    before, outside_before = _entry_snapshot(canonical), None
    if outside is not None:
        outside_before = _entry_snapshot(outside)
    reads: list[Path] = []
    real_reader, real_path_read = safe_fs.read_bytes_limited_no_follow, Path.read_bytes

    def reader(path: Path, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
        reads.append(path)
        if path == outside:
            raise AssertionError("record.path escaped to an external reader")
        return real_reader(path, **kwargs)

    def path_read(path: Path) -> bytes:
        if path == outside:
            raise AssertionError("record.path escaped to pathlib reader")
        return real_path_read(path)

    with monkeypatch.context() as patch:
        patch.setattr(safe_fs, "read_bytes_limited_no_follow", reader)
        patch.setattr(Path, "read_bytes", path_read)
        with pytest.raises(TrackerError, match=message):
            _importer()(tracker=tracker, record=record)

    assert reads == [canonical] * expected_reads
    assert dict(tracker.captured) == {}
    assert _entry_snapshot(canonical) == before
    if outside is not None:
        assert _entry_snapshot(outside) == outside_before


@pytest.mark.parametrize(
    "shape",
    [
        "missing",
        "leaf-symlink",
        "symlink-ancestor",
        "directory",
        "fifo",
        "reader-permission",
        "reader-safe-filesystem",
    ],
)
def test_canonical_shape_and_reader_failures_are_tracker_errors_without_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    tracker, record, canonical, payload = _receipt_case(tmp_path, install=False)
    watched: list[Path] = [canonical, canonical.parent]
    if shape == "leaf-symlink":
        canonical.parent.mkdir()
        outside = tmp_path / "outside-leaf.cfg.ic.update"
        outside.write_bytes(payload)
        canonical.symlink_to(outside)
        watched.append(outside)
    elif shape == "symlink-ancestor":
        outside_dir = tmp_path / "outside-checkpoints"
        outside_dir.mkdir()
        outside = outside_dir / CANONICAL_NAME
        outside.write_bytes(payload)
        canonical.parent.symlink_to(outside_dir, target_is_directory=True)
        watched.append(outside)
    elif shape == "directory":
        canonical.parent.mkdir()
        canonical.mkdir()
    elif shape == "fifo":
        canonical.parent.mkdir()
        os.mkfifo(canonical)
    elif shape.startswith("reader-"):
        canonical.parent.mkdir()
        canonical.write_bytes(payload)
        if shape == "reader-permission":
            error: OSError = PermissionError("receipt reader denied")
        else:
            error = safe_fs.SafeFilesystemError("receipt reader unsafe")

        def failing_reader(path: Path, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
            del path, kwargs
            raise error

        monkeypatch.setattr(safe_fs, "read_bytes_limited_no_follow", failing_reader)
    elif shape != "missing":
        raise AssertionError(shape)

    before = {path: _entry_snapshot(path) for path in watched}
    with pytest.raises(TrackerError):
        _importer()(tracker=tracker, record=record)
    assert dict(tracker.captured) == {}
    assert {path: _entry_snapshot(path) for path in watched} == before


@pytest.mark.parametrize(
    ("case", "expected_parse_calls", "message"),
    [
        ("checksum-drift", 0, "checksum drifted"),
        ("invalid-utf8", 0, "failed header/body recheck"),
        ("nonfinite-header", 0, "failed header/body recheck"),
        ("header-1440", 0, "failed header/body recheck"),
        ("truncated-body", 1, "failed header/body recheck"),
    ],
)
def test_checksum_header_and_body_gates_are_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_parse_calls: int,
    message: str,
) -> None:
    if case == "checksum-drift":
        tracker, record, canonical, _ = _receipt_case(tmp_path)
        payload = _payload(mesh_count=4)
        canonical.write_bytes(payload)
    else:
        if case == "invalid-utf8":
            payload = b"\xff\xfe\n"
        elif case == "nonfinite-header":
            payload = _payload("nan")
        elif case == "header-1440":
            payload = _payload("1440.000000")
            state.parse(payload)
        elif case == "truncated-body":
            payload = _truncated_payload()
            with pytest.raises(ValueError):
                state.parse(payload)
        else:
            raise AssertionError(case)
        tracker, record, canonical, _ = _receipt_case(tmp_path, payload=payload)

    before = _entry_snapshot(canonical)
    reads: list[Path] = []
    parsed: list[int] = []
    real_reader, real_parse = safe_fs.read_bytes_limited_no_follow, state.parse

    def reader(path: Path, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
        reads.append(path)
        return real_reader(path, **kwargs)

    def parse_spy(data: bytes, **kwargs):  # type: ignore[no-untyped-def]
        parsed.append(len(data))
        return real_parse(data, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(safe_fs, "read_bytes_limited_no_follow", reader)
        patch.setattr(tracker_module.state, "parse", parse_spy)
        with pytest.raises(TrackerError, match=message):
            _importer()(tracker=tracker, record=record)

    assert reads == [canonical]
    assert len(parsed) == expected_parse_calls
    assert dict(tracker.captured) == {}
    assert _entry_snapshot(canonical) == before


def test_oversize_receipt_uses_bounded_reader_then_original_state_size_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = _payload()
    assert prefix.split(b"\n", 1)[0].endswith(b"720.000000")
    state.parse(prefix)
    payload = prefix + b"x" * (state.MAX_STATE_IC_BYTES + 1 - len(prefix))
    assert len(payload) == state.MAX_STATE_IC_BYTES + 1
    tracker, record, canonical, _ = _receipt_case(tmp_path, payload=payload)
    before = canonical.lstat()
    reads: list[tuple[Path, int, Path | None]] = []
    parsed: list[int] = []
    parse_errors: list[str] = []
    real_reader, real_parse = safe_fs.read_bytes_limited_no_follow, state.parse

    def reader(path: Path, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
        reads.append((path, kwargs["max_bytes"], kwargs["containment_root"]))
        return real_reader(path, **kwargs)

    def parse_spy(data: bytes, **kwargs):  # type: ignore[no-untyped-def]
        parsed.append(len(data))
        try:
            return real_parse(data, **kwargs)
        except ValueError as error:
            parse_errors.append(str(error))
            raise

    with monkeypatch.context() as patch:
        patch.setattr(safe_fs, "read_bytes_limited_no_follow", reader)
        patch.setattr(tracker_module.state, "parse", parse_spy)
        with pytest.raises(TrackerError, match="failed header/body recheck"):
            _importer()(tracker=tracker, record=record)

    assert reads == [(canonical, state.MAX_STATE_IC_BYTES, tracker.run_dir)]
    assert parsed == [state.MAX_STATE_IC_BYTES + 1]
    assert parse_errors == [
        f"IC file exceeds size limit of {state.MAX_STATE_IC_BYTES} bytes"
    ]
    after = canonical.lstat()
    assert (after.st_dev, after.st_ino, after.st_size) == (
        before.st_dev,
        before.st_ino,
        before.st_size,
    )
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == record.checksum
    assert dict(tracker.captured) == {}


def test_same_object_rechecks_without_rewriting_and_equal_foreign_is_preread_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker, record, canonical, _ = _receipt_case(tmp_path)
    mapping = _SetitemSpy()
    tracker._captured = mapping
    reads: list[Path] = []
    real_reader = safe_fs.read_bytes_limited_no_follow

    def reader(path: Path, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
        reads.append(path)
        return real_reader(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(safe_fs, "read_bytes_limited_no_follow", reader)
        assert _importer()(tracker=tracker, record=record) is record
        assert mapping.writes == [(HOUR, record)]
        mapping.writes.clear()
        assert _importer()(tracker=tracker, record=record) is record
        assert mapping.writes == []

        canonical.write_bytes(_payload(mesh_count=4))
        with pytest.raises(TrackerError, match="checksum drifted"):
            _importer()(tracker=tracker, record=record)

    assert tracker.captured[HOUR] is record
    assert reads == [canonical, canonical, canonical]
    equal_foreign = replace(record)
    assert equal_foreign == record and equal_foreign is not record

    def unexpected_reader(path: Path, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
        del path, kwargs
        raise AssertionError("different object conflict reached canonical reader")

    with monkeypatch.context() as patch:
        patch.setattr(safe_fs, "read_bytes_limited_no_follow", unexpected_reader)
        with pytest.raises(TrackerError, match="authority differs"):
            _importer()(tracker=tracker, record=equal_foreign)
    assert tracker.captured[HOUR] is record


@pytest.mark.parametrize(
    ("failure", "authority"),
    [
        (failure, authority)
        for failure in (
            "field",
            "checksum",
            "header",
            "body",
            "reader-oserror",
            "reader-safe-filesystem",
        )
        for authority in ("fresh", "same", "foreign")
    ],
)
def test_verification_failures_are_atomic_for_fresh_same_and_foreign_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    authority: str,
) -> None:
    if failure == "header":
        payload = _payload("1440.000000")
    elif failure == "body":
        payload = _truncated_payload()
    else:
        payload = _payload()
    tracker, record, canonical, _ = _receipt_case(tmp_path, payload=payload)
    if failure == "field":
        record = replace(record, lead_hours=24)
    elif failure == "checksum":
        record = replace(record, checksum="f" * 64)
    existing = replace(record, checksum="e" * 64) if authority == "foreign" else record
    if authority != "fresh":
        tracker._captured[HOUR] = existing
    before = _entry_snapshot(canonical)
    if failure == "reader-oserror":

        def failing_reader(path: Path, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
            del path, kwargs
            raise OSError("receipt read failed")

        monkeypatch.setattr(safe_fs, "read_bytes_limited_no_follow", failing_reader)
    elif failure == "reader-safe-filesystem":

        def failing_reader(path: Path, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
            del path, kwargs
            raise safe_fs.SafeFilesystemError("receipt read unsafe")

        monkeypatch.setattr(safe_fs, "read_bytes_limited_no_follow", failing_reader)

    with pytest.raises(TrackerError):
        _importer()(tracker=tracker, record=record)
    if authority == "fresh":
        assert dict(tracker.captured) == {}
    else:
        assert tracker.captured[HOUR] is existing
    assert _entry_snapshot(canonical) == before


def test_foreign_authority_conflict_is_atomic_before_any_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker, existing, canonical, _ = _receipt_case(tmp_path)
    tracker._captured[HOUR] = existing
    foreign = replace(existing, lead_hours=24)
    before = _entry_snapshot(canonical)

    def unexpected_reader(path: Path, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
        del path, kwargs
        raise AssertionError("foreign authority conflict reached canonical reader")

    monkeypatch.setattr(safe_fs, "read_bytes_limited_no_follow", unexpected_reader)
    with pytest.raises(TrackerError, match="authority differs"):
        _importer()(tracker=tracker, record=foreign)
    assert tracker.captured[HOUR] is existing
    assert _entry_snapshot(canonical) == before


@pytest.mark.parametrize(
    "case", ["success", "type", "field", "bytes", "same-object", "conflict"]
)
def test_import_has_no_filesystem_mutation_on_every_reachable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    payload = _truncated_payload() if case == "bytes" else _payload()
    tracker, record, canonical, _ = _receipt_case(tmp_path, payload=payload)
    before = _entry_snapshot(canonical)
    mutations = _mutation_probes(monkeypatch)

    if case == "success":
        assert _importer()(tracker=tracker, record=record) is record
    elif case == "type":
        with pytest.raises(TrackerError):
            _importer()(tracker=object(), record=record)
    elif case == "field":
        with pytest.raises(TrackerError):
            _importer()(tracker=tracker, record=replace(record, lead_hours=24))
    elif case == "bytes":
        with pytest.raises(TrackerError):
            _importer()(tracker=tracker, record=record)
    elif case == "same-object":
        assert _importer()(tracker=tracker, record=record) is record
        assert _importer()(tracker=tracker, record=record) is record
    elif case == "conflict":
        assert _importer()(tracker=tracker, record=record) is record
        with pytest.raises(TrackerError):
            _importer()(tracker=tracker, record=replace(record))
    else:
        raise AssertionError(case)

    assert mutations == []
    assert _entry_snapshot(canonical) == before


def test_valid_canonical_without_explicit_import_remains_legacy_residue(
    tmp_path: Path,
) -> None:
    run_directory, tracker, _, canonical, _ = _ready_run_case(tmp_path)
    before = _entry_snapshot(canonical)
    runner = _ExplodingRunner()

    with pytest.raises(TrackerError, match="pre-existing residue"):
        ensure_twelve_hour_checkpoint(
            tracker=tracker,
            run_directory=run_directory,
            runner=runner,
        )

    assert runner.calls == 0
    assert dict(tracker.captured) == {}
    assert _entry_snapshot(canonical) == before


def test_issue_136_line_budgets() -> None:
    source_file, test_file = Path(tracker_module.__file__), Path(__file__)
    assert len(source_file.read_text(encoding="utf-8").splitlines()) <= 1000
    assert len(test_file.read_text(encoding="utf-8").splitlines()) < 1000
