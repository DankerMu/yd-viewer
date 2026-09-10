"""Public contract tests for claimed-work staged inputs (#177)."""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from assembly_fixtures import (
    NATIVE_PARAMETER_EXPECTED,
    NATIVE_PARAMETER_TEMPLATE,
    PARAMETER_EXPECTED,
    PARAMETER_TEMPLATE,
    SP_ATT,
    stage,
    stock_runtime_values,
    write_forcing_package,
    write_state,
    write_variant,
)
from assembly_fixtures import STAGED_CALIBRATED_STATE as CALIBRATED_STATE
from assembly_fixtures import STAGED_CYCLE as CYCLE
from assembly_fixtures import STAGED_CYCLE_ID as CYCLE_ID
from assembly_fixtures import STAGED_PROJECT as PROJECT
from assembly_fixtures import STAGED_SOURCE as SOURCE
from assembly_fixtures import STAGED_VARIANT_IDS as VARIANT_IDS
from assembly_fixtures import native_segmented_cfg_ic as _native_segmented_cfg_ic
from assembly_fixtures import stage_inputs as _stage_call
from assembly_fixtures import staged_claim as _claim
from assembly_fixtures import staged_fixture as _stage_fixture
from assembly_fixtures import staged_ids as _ids
from assembly_fixtures import staged_pair as _stage_pair
from assembly_fixtures import write_staged_source as _write_source
from cfg_ic_fixtures import build_cfg_ic
from prepare_fixtures import (
    NATIVE_VARIANT_FILES,
    VARIANT_HANDOFF_NAME,
    canonical_json_bytes,
    sha256_literal,
)

import yd_producer.staged_inputs as staged_module
from yd_producer._work_claim import WorkClaim
from yd_producer.assemble import (
    AssemblyError,
    WorkIdentity,
    assemble,
    stage_work_registry,
)
from yd_producer.forcing.bounded_json import MAX_JSON_DEPTH, MAX_JSON_NODES
from yd_producer.prepare import (
    VARIANT_BINDING_NAME,
    VARIANT_HYDRO_PARAM_NAME,
)
from yd_producer.staged_inputs import (
    STAGED_INPUT_DIRNAME,
    STAGED_INPUTS_MANIFEST_FILENAME,
    STAGED_INPUTS_SCHEMA,
    STAGED_STATES_DIRNAME,
    STAGED_VARIANT_DIRNAME,
    StagedWorkInputs,
    load_staged_work_inputs,
)
from yd_producer.tracker import CheckpointTracker, ensure_twelve_hour_checkpoint

MAX_MANIFEST_BYTES = MAX_ASSET_BYTES = MAX_STATE_BYTES = 65_536
ASSEMBLE_PARAMETERS = ["registry", "staged_inputs", "forcing"]
VALID_PARAMETER_DRIFT = b"# valid parameter drift\nKsatH 2.0e-4\n"
ASSEMBLY_BINDING = b"opaque\x00binding\n"
ASSEMBLY_INDEX = (
    b"2 20260826\nshud\nID\tLon\tLat\tX\tY\tZ\tFilename\n"
    b"1\t1\t2\t3\t4\t5\tX1.csv\n2\t6\t7\t8\t9\t10\tX2.csv\n"
)
ASSEMBLY_CSV_ONE = (
    b"1\t6\t20260826\t20260826\nTime_Day\tPrecip\tTemp\tRH\tWind\tRN\n"
    b"0\t1\t2\t3\t4\t5\n"
)
ASSEMBLY_CSV_TWO = ASSEMBLY_CSV_ONE.replace(b"0\t1\t2\t3\t4\t5", b"0\t6\t7\t8\t9\t10")
VARIANT_FILES = frozenset(
    [*NATIVE_VARIANT_FILES, "yd.binding", "yd.direct-grid-handoff.json"]
)
STATE_KEY = f"input/states/{SOURCE}/{CYCLE_ID}.cfg.ic"
PARA_KEY = f"input/variant/{VARIANT_HYDRO_PARAM_NAME}"
_checksum = sha256_literal


def _load(work_dir: Path, **overrides: int) -> StagedWorkInputs:
    return load_staged_work_inputs(work_dir=work_dir, **_ids(**overrides))


def _replace_minute(content: bytes, minute: str) -> bytes:
    lines = content.decode("ascii").splitlines(keepends=True)
    header = lines[0].rstrip("\r\n").split("\t")
    header[-1] = minute
    ending = "\r\n" if lines[0].endswith("\r\n") else "\n"
    lines[0] = "\t".join(header) + ending
    return "".join(lines).encode("ascii")


def _input_absent(claim: WorkClaim) -> None:
    assert claim.work_dir.is_dir()
    assert not os.path.lexists(claim.work_dir / STAGED_INPUT_DIRNAME)


def _rewrite_manifest(
    staged: StagedWorkInputs,
    mutate: Callable[[dict[str, Any]], None],
    *,
    canonical: bool = True,
) -> None:
    payload = json.loads(staged.manifest_path.read_bytes())
    mutate(payload)
    encoded = canonical_json_bytes(payload)
    staged.manifest_path.write_bytes(encoded if canonical else encoded + b"\n")


def _symlink_over(path: Path, outside: Path) -> None:
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)


def _expected_manifest(work: Path, files: dict[str, bytes]) -> dict[str, Any]:
    return {
        "cycle_id": CYCLE_ID,
        "files": {key: _checksum(value) for key, value in files.items()},
        "schema_version": STAGED_INPUTS_SCHEMA,
        "source_id": SOURCE,
        "work_dir": work.as_posix(),
    }


def _refuse_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
    *,
    mutate: object | None = None,
    bomb: str | None = None,
    **load_overrides: int,
) -> tuple[WorkClaim, StagedWorkInputs]:
    _, claim, staged = _stage_fixture(tmp_path)
    if mutate is not None:
        mutate(claim, staged)
    if bomb is not None:
        assert monkeypatch is not None
        monkeypatch.setattr(
            staged_module, "_read_declared", lambda *_a, **_k: pytest.fail(bomb)
        )
    with pytest.raises(staged_module.StagedWorkInputsError) as captured:
        _load(claim.work_dir, **load_overrides)
    if bomb is not None:
        assert type(captured.value) is staged_module.StagedWorkInputsError
        assert not (claim.work_dir / "model").exists()
    assert staged.manifest_path.exists()
    return claim, staged


def _assemble(registry: Any, staged: StagedWorkInputs, forcing: Any):
    return staged_module.assemble_staged(
        registry=registry, staged_inputs=staged, forcing=forcing
    )


def _refuse_assemble(
    registry: Any,
    staged: StagedWorkInputs,
    forcing: Any,
    *,
    work: Path,
    extra: Path | None = None,
) -> None:
    with pytest.raises(AssemblyError) as captured:
        _assemble(registry, staged, forcing)
    assert captured.value.phase == "validate"
    assert not (work / "model").exists()
    if extra is not None:
        assert not (extra / "model").exists()


def test_stage_manifest_and_reload_are_exact(tmp_path: Path) -> None:
    source, claim, staged = _stage_fixture(tmp_path)
    work = (tmp_path / "scratch" / "work" / SOURCE / CYCLE_ID).resolve()
    input_dir = work / "input"
    assert claim.work_dir == work == staged.work_dir
    assert staged.work_identity == claim.identity
    assert staged.variant_dir == input_dir / "variant"
    assert staged.state_path == work / STATE_KEY
    assert staged.manifest_path == input_dir / STAGED_INPUTS_MANIFEST_FILENAME
    assert set(os.listdir(input_dir)) == {
        STAGED_VARIANT_DIRNAME,
        STAGED_STATES_DIRNAME,
        STAGED_INPUTS_MANIFEST_FILENAME,
    }
    assert set(os.listdir(input_dir / STAGED_VARIANT_DIRNAME)) == VARIANT_FILES
    states = input_dir / STAGED_STATES_DIRNAME
    assert set(os.listdir(states)) == {SOURCE}
    assert set(os.listdir(states / SOURCE)) == {f"{CYCLE_ID}.cfg.ic"}
    expected_manifest = _expected_manifest(work, source.files)
    manifest_bytes = staged.manifest_path.read_bytes()
    assert manifest_bytes == canonical_json_bytes(expected_manifest)
    manifest = json.loads(manifest_bytes)
    assert len(manifest["files"]) == 15
    for key, content in source.files.items():
        staged_content = work.joinpath(*key.split("/")).read_bytes()
        assert staged_content == content
        assert manifest["files"][key] == _checksum(staged_content)
    for forbidden in (b'"st_dev"', b'"st_ino"', b'"job_id"', str(source.root).encode()):
        assert forbidden not in manifest_bytes
    assert staged.manifest_checksum == _checksum(manifest_bytes)
    assert dict(staged.file_checksums) == expected_manifest["files"]
    assert _load(work) == staged


def test_load_survives_source_disconnect(tmp_path: Path) -> None:
    source, claim, staged = _stage_fixture(tmp_path)
    shutil.rmtree(source.root)
    assert not source.root.exists()
    reloaded = _load(claim.work_dir)
    assert reloaded == staged
    for path in (
        reloaded.work_dir,
        reloaded.variant_dir,
        reloaded.state_path,
        reloaded.manifest_path,
    ):
        assert path == claim.work_dir or path.is_relative_to(claim.work_dir)


@pytest.mark.parametrize(
    "case",
    [
        "wrong-absolute-minute",
        "relative-720",
        "non-finite-minute",
        "state-over-cap",
        "asset-over-cap",
        "fixed-file-symlink",
        "state-symlink",
        "state-fifo",
        "extra-entry",
        "missing-entry",
        "overlap",
    ],
)
def test_stage_preflight_and_source_safety_precede_target_creation(
    tmp_path: Path, case: str
) -> None:
    source, claim = _stage_pair(tmp_path)
    overrides: dict[str, object] = {}
    minutes = {
        "wrong-absolute-minute": str(round(CYCLE.timestamp() / 60) + 1),
        "relative-720": "720",
        "non-finite-minute": "nan",
    }
    if case in minutes:
        source.state_path.write_bytes(
            _replace_minute(source.state_path.read_bytes(), minutes[case])
        )
    elif case == "state-over-cap":
        overrides["max_state_bytes"] = len(source.state_path.read_bytes()) - 1
    elif case == "asset-over-cap":
        binding = source.variant_dir / VARIANT_BINDING_NAME
        overrides["max_asset_bytes"] = len(binding.read_bytes()) - 1
    elif case == "fixed-file-symlink":
        victim = source.variant_dir / VARIANT_HYDRO_PARAM_NAME
        _symlink_over(victim, source.root / "outside-parameter")
    elif case == "state-symlink":
        _symlink_over(source.state_path, source.root / "outside-state")
    elif case == "state-fifo":
        source.state_path.unlink()
        os.mkfifo(source.state_path)
    elif case == "extra-entry":
        (source.variant_dir / "foreign").write_bytes(b"foreign\n")
    elif case == "missing-entry":
        (source.variant_dir / VARIANT_HYDRO_PARAM_NAME).unlink()
    else:
        overlaps = claim.work_dir / "source-variant"
        overlaps.mkdir()
        overrides["source_variant_dir"] = overlaps
    with pytest.raises(staged_module.StagedWorkInputsError):
        _stage_call(claim, source, **overrides)
    _input_absent(claim)


def test_stage_rejects_preexisting_input_without_adoption(tmp_path: Path) -> None:
    source, claim = _stage_pair(tmp_path)
    input_dir = claim.work_dir / STAGED_INPUT_DIRNAME
    input_dir.mkdir()
    with pytest.raises(staged_module.StagedWorkInputsError):
        _stage_call(claim, source)
    assert input_dir.is_dir()
    assert os.listdir(input_dir) == []


def test_stage_exclusive_create_race_preserves_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, claim = _stage_pair(tmp_path)
    real_open, sentinel, raced = staged_module.open_claimed_excl, b"racer-owned\n", []

    def racing_open(target_claim: WorkClaim, path: Path, *, mode: int = 0o644) -> int:
        if not raced:
            raced.append(path)
            path.write_bytes(sentinel)
        return real_open(target_claim, path, mode=mode)

    monkeypatch.setattr(staged_module, "open_claimed_excl", racing_open)
    with pytest.raises(staged_module.StagedWorkInputsError):
        _stage_call(claim, source)
    assert len(raced) == 1 and raced[0].read_bytes() == sentinel
    assert not (claim.work_dir / "input" / STAGED_INPUTS_MANIFEST_FILENAME).exists()


def test_stage_zero_write_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, claim = _write_source(tmp_path / "order/source"), _claim(tmp_path / "order")
    real_claimed, order, ready = staged_module._write_claimed, [], False

    def ordered_write(target_claim: WorkClaim, path: Path, content: bytes) -> None:
        nonlocal ready
        key = path.relative_to(claim.work_dir).as_posix()
        if ready:
            raise OSError(f"declared member attempted after readiness: {key}")
        real_claimed(target_claim, path, content)
        order.append(key)
        ready = path.name == STAGED_INPUTS_MANIFEST_FILENAME

    with monkeypatch.context() as patch:
        patch.setattr(staged_module, "_write_claimed", ordered_write)
        staged = _stage_call(claim, source)
    assert order == [*sorted(source.files), "input/yd.staged-inputs.json"]
    assert staged.manifest_path.exists()
    source, claim = _write_source(tmp_path / "zero/source"), _claim(tmp_path / "zero")
    real_write, calls = staged_module.os.write, []

    def zero_write(fd: int, content: object) -> int:
        calls.append(fd)
        return 0 if len(calls) == 1 else real_write(fd, content)

    monkeypatch.setattr(staged_module.os, "write", zero_write)
    with pytest.raises(staged_module.StagedWorkInputsError):
        _stage_call(claim, source)
    assert len(calls) == 1
    assert not (claim.work_dir / "input" / STAGED_INPUTS_MANIFEST_FILENAME).exists()


def test_stage_source_checksum_drift_after_readiness_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, claim = _stage_pair(tmp_path)
    parameter = source.variant_dir / VARIANT_HYDRO_PARAM_NAME
    real_write, changed = staged_module._write_claimed, []

    def mutate_after_ready(target_claim: WorkClaim, path: Path, content: bytes) -> None:
        real_write(target_claim, path, content)
        if path.name == STAGED_INPUTS_MANIFEST_FILENAME:
            parameter.write_bytes(VALID_PARAMETER_DRIFT)
            changed.append(path)

    monkeypatch.setattr(staged_module, "_write_claimed", mutate_after_ready)
    with pytest.raises(staged_module.StagedWorkInputsError):
        _stage_call(claim, source)
    assert changed == [claim.work_dir / "input" / STAGED_INPUTS_MANIFEST_FILENAME]
    assert parameter.read_bytes() == VALID_PARAMETER_DRIFT
    assert changed[0].exists()


def test_generated_manifest_cap_is_checked_before_target_creation(
    tmp_path: Path,
) -> None:
    work_root = tmp_path / ("w" * 80) / ("x" * 80) / ("y" * 80)
    source, claim = (
        _write_source(tmp_path / "nfs-test-owned"),
        _claim(work_root),
    )
    expected = canonical_json_bytes(_expected_manifest(claim.work_dir, source.files))
    handoff_size = len((source.variant_dir / VARIANT_HANDOFF_NAME).read_bytes())
    cap = len(expected) - 1
    assert handoff_size <= cap
    with pytest.raises(staged_module.StagedWorkInputsError):
        _stage_call(claim, source, max_manifest_bytes=cap)
    _input_absent(claim)


def _manifest_mutation(case: str, staged: StagedWorkInputs) -> None:
    def mutate(payload: dict[str, Any]) -> None:
        files = payload["files"]
        if case == "extra-top-key":
            payload["extra"] = "nope"
        elif case == "wrong-schema":
            payload["schema_version"] = "yd.run.staged-inputs.v0"
        elif case == "wrong-source":
            payload["source_id"] = "ifs"
        elif case == "wrong-cycle":
            payload["cycle_id"] = "2026082600"
        elif case == "wrong-work":
            payload["work_dir"] += "-other"
        elif case == "uppercase-checksum":
            files[STATE_KEY] = files[STATE_KEY].upper()
        elif case == "unprefixed-checksum":
            files[STATE_KEY] = files[STATE_KEY].removeprefix("sha256:")
        elif case == "missing-file-key":
            del files[STATE_KEY]
        elif case == "extra-file-key":
            files["input/variant/foreign"] = _checksum(b"foreign")

    _rewrite_manifest(staged, mutate, canonical=case != "noncanonical")


@pytest.mark.parametrize(
    "case",
    [
        "noncanonical",
        "extra-top-key",
        "wrong-schema",
        "wrong-source",
        "wrong-cycle",
        "wrong-work",
        "uppercase-checksum",
        "unprefixed-checksum",
        "missing-file-key",
        "extra-file-key",
    ],
)
def test_load_rejects_manifest_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    _refuse_load(
        tmp_path,
        monkeypatch,
        mutate=lambda _claim, staged: _manifest_mutation(case, staged),
        bomb="declared files read before manifest rejection",
    )


WRONG_FIELDS = (
    "source_id",
    "cycle_id",
    "work_dir",
    "schema_version",
)
MANIFEST_MUTATIONS = [
    ("top-list", "top", []),
    ("files-list", "files", []),
    *((f"{field}-wrong-type", field, False) for field in WRONG_FIELDS),
    ("missing-top-key", "pop", "source_id"),
    ("absolute-key", PARA_KEY, "/tmp/yd.cfg.para"),
    ("parent-key", PARA_KEY, "input/variant/../yd.cfg.para"),
    ("backslash-key", PARA_KEY, r"input\variant\yd.cfg.para"),
    ("nul-key", PARA_KEY, "input/variant/yd.cfg.para\0"),
    ("self-key", PARA_KEY, "input/yd.staged-inputs.json"),
    ("nested-key", PARA_KEY, "input/variant/nested/file"),
    ("wrong-state-key", STATE_KEY, f"input/states/ifs/{CYCLE_ID}.cfg.ic"),
    ("checksum-non-string", STATE_KEY, 1),
    ("path-alias", PARA_KEY, "input//variant/yd.cfg.para"),
]


@pytest.mark.parametrize(("case", "target", "value"), MANIFEST_MUTATIONS)
def test_load_rejects_manifest_schema_and_path_grammar_before_declared_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    target: str,
    value: object,
) -> None:
    def mutate(_claim: WorkClaim, staged: StagedWorkInputs) -> None:
        payload = json.loads(staged.manifest_path.read_bytes())
        if target == "top":
            payload = value
        elif target == "pop":
            payload.pop(value)
        elif target in payload:
            payload[target] = value
        elif isinstance(value, str):
            payload["files"][value] = payload["files"].pop(target)
        else:
            payload["files"][target] = value
        encoded = canonical_json_bytes(payload)
        staged.manifest_path.write_bytes(encoded)

    _refuse_load(
        tmp_path,
        monkeypatch,
        mutate=mutate,
        bomb="declared files read before manifest rejection",
    )


@pytest.mark.parametrize("case", ["invalid-utf8", "malformed", "nan", "depth", "nodes"])
def test_load_rejects_noncanonical_or_unbounded_json_before_declared_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    samples = {
        "invalid-utf8": b'{"bad":"\xff"}',
        "malformed": b'{"source_id":"gfs"',
        "nan": (
            b'{"cycle_id":"2026082612","files":{},'
            b'"schema_version":"yd.run.staged-inputs.v2",'
            b'"source_id":NaN,"work_dir":"/tmp"}'
        ),
        "depth": (
            "[" * (MAX_JSON_DEPTH + 1) + "0" + "]" * (MAX_JSON_DEPTH + 1)
        ).encode(),
    }
    content = samples.get(case)
    if content is None:
        content = json.dumps([0] * MAX_JSON_NODES, separators=(",", ":")).encode()

    def mutate(_claim: WorkClaim, staged: StagedWorkInputs) -> None:
        staged.manifest_path.write_bytes(content)

    _refuse_load(
        tmp_path,
        monkeypatch,
        mutate=mutate,
        bomb="declared files read before bounded JSON rejection",
        max_manifest_bytes=len(content) + 1 if case == "nodes" else MAX_MANIFEST_BYTES,
    )


@pytest.mark.parametrize(
    "case",
    [
        "missing-entry",
        "extra-entry",
        "declared-symlink",
        "declared-fifo",
        "corrupt-state",
        "corrupt-binding",
        "corrupt-parameter-valid",
        "corrupt-sp-att",
    ],
)
def test_load_rejects_staged_tree_and_declared_byte_mutations(
    tmp_path: Path, case: str
) -> None:
    def mutate(_claim: WorkClaim, staged: StagedWorkInputs) -> None:
        victim = staged.variant_dir / VARIANT_HYDRO_PARAM_NAME
        if case == "missing-entry":
            victim.unlink()
        elif case == "extra-entry":
            (staged.variant_dir / "foreign").write_bytes(b"foreign\n")
        elif case == "declared-symlink":
            _symlink_over(victim, tmp_path / "outside")
        elif case == "declared-fifo":
            victim.unlink()
            os.mkfifo(victim)
        elif case == "corrupt-state":
            staged.state_path.write_bytes(b"corrupt state\n")
        elif case == "corrupt-binding":
            binding = staged.variant_dir / VARIANT_BINDING_NAME
            binding.write_bytes(b"corrupt binding\n")
        elif case == "corrupt-parameter-valid":
            victim.write_bytes(VALID_PARAMETER_DRIFT)
        else:
            (staged.variant_dir / "yd.sp.att").write_bytes(b"corrupt sp.att\n")

    _refuse_load(tmp_path, mutate=mutate)


def test_load_validates_state_semantics_after_checksum_success(tmp_path: Path) -> None:
    held: list[bytes] = []

    def mutate(_claim: WorkClaim, staged: StagedWorkInputs) -> None:
        state = _replace_minute(staged.state_path.read_bytes(), "720")
        staged.state_path.write_bytes(state)
        held.append(state)

        def rewrite(payload: dict[str, Any]) -> None:
            payload["files"][STATE_KEY] = _checksum(state)

        _rewrite_manifest(staged, rewrite)

    _, staged = _refuse_load(tmp_path, mutate=mutate)
    assert staged.state_path.read_bytes() == held[0]


def test_loader_prepared_snapshot_sandwich_rejects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, claim, staged = _stage_fixture(tmp_path)
    real_load, calls = staged_module._load_prepared, 0

    def drifting_load(variant_dir: Path, caller: object) -> object:
        nonlocal calls
        calls += 1
        prepared = real_load(variant_dir, caller)
        if calls == 2:
            return dataclasses.replace(prepared, basin_id="basin-drift")
        return prepared

    monkeypatch.setattr(staged_module, "_load_prepared", drifting_load)
    with pytest.raises(staged_module.StagedWorkInputsError):
        _load(claim.work_dir)
    assert calls == 2
    assert staged.manifest_path.exists()


@pytest.mark.parametrize("mode", ["checksum-tuple", "real-file-tamper"])
def test_loader_declared_bytes_sandwich_rejects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _, claim, staged = _stage_fixture(tmp_path)
    real_read, calls = staged_module._read_declared, 0

    def drifting_read(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        result = real_read(*args, **kwargs)
        if mode == "checksum-tuple" and calls == 2:
            contents, checksums = result
            key, digest = checksums[0]
            other = "sha256:" + ("0" if digest[-1] != "0" else "1") * 64
            return contents, ((key, other), *checksums[1:])
        if mode == "real-file-tamper" and calls == 1:
            staged.state_path.write_bytes(staged.state_path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(staged_module, "_read_declared", drifting_read)
    with pytest.raises(staged_module.StagedWorkInputsError):
        _load(claim.work_dir)
    assert calls == 2
    assert staged.manifest_path.exists()


def test_loader_final_work_identity_check_rejects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, claim, staged = _stage_fixture(tmp_path)
    real_identity, work_calls = staged_module.directory_identity_no_follow, 0

    def drifting_identity(path: Path) -> tuple[int, int]:
        nonlocal work_calls
        identity = real_identity(path)
        if path == claim.work_dir:
            work_calls += 1
            if work_calls == 2:
                return identity[0], identity[1] + 1
        return identity

    monkeypatch.setattr(
        staged_module, "directory_identity_no_follow", drifting_identity
    )
    with pytest.raises(staged_module.StagedWorkInputsError):
        _load(claim.work_dir)
    assert work_calls == 2
    assert staged.manifest_path.exists()


def _assembly_fixture(tmp_path: Path):
    source = _write_source(
        tmp_path / "nfs-test-owned",
        binding=ASSEMBLY_BINDING,
        sp_att=SP_ATT,
        parameter=NATIVE_PARAMETER_TEMPLATE,
        calibrated_state=CALIBRATED_STATE,
        two_stations=True,
    )
    claim = _claim(tmp_path)
    staged = _stage_call(claim, source)
    identity = WorkIdentity(
        source_id=SOURCE, cycle_time=CYCLE, project_name=PROJECT, **VARIANT_IDS
    )
    registry = stage_work_registry(
        work_root=claim.work_root,
        identity=identity,
        contract=staged.prepared.contract,
        binding_content=ASSEMBLY_BINDING,
        sp_att_content=SP_ATT,
        max_asset_bytes=MAX_ASSET_BYTES,
    )
    version = "forc-gfs-2026082612-model-177"
    forcing = write_forcing_package(
        registry.object_store_root,
        identity,
        index=ASSEMBLY_INDEX,
        csv_one=ASSEMBLY_CSV_ONE,
        csv_two=ASSEMBLY_CSV_TWO,
        mutate_manifest=lambda manifest: manifest.update(forcing_version_id=version),
        mutate_result=lambda result: dataclasses.replace(
            result, forcing_version_id=version
        ),
    )
    return source, claim, staged, registry, forcing


def test_assemble_staged_signature_and_work_local_success(tmp_path: Path) -> None:
    source, claim, staged, registry, forcing = _assembly_fixture(tmp_path)
    cycle_state = source.state_path.read_bytes()
    shutil.rmtree(source.root)
    assert not source.root.exists()
    parameters = inspect.signature(staged_module.assemble_staged).parameters
    assert list(parameters) == ASSEMBLE_PARAMETERS
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is inspect.Parameter.empty
        for parameter in parameters.values()
    )
    result = _assemble(registry, staged, forcing)
    assert result.path == claim.work_dir / "model" and result.path.is_dir()
    assert result.state_path.read_bytes() == cycle_state
    assert result.state_path.read_bytes() != CALIBRATED_STATE
    assert result.parameter_path.read_bytes() == NATIVE_PARAMETER_EXPECTED
    assert (result.path / "input" / "yd" / "yd.sp.att").read_bytes() == SP_ATT
    assert not (result.path / "yd.staged-inputs.json").exists()
    assert result.forcing_index_path == result.path / "input" / "yd" / "yd.tsd.forc"
    assert result.forcing_index_path.read_bytes().splitlines()[1] == b"."
    assert tuple(path.name for path in result.forcing_csv_paths) == ("X1.csv", "X2.csv")
    assert tuple(path.parent for path in result.forcing_csv_paths) == (
        result.path,
        result.path,
    )
    assert tuple(path.read_bytes() for path in result.forcing_csv_paths) == (
        ASSEMBLY_CSV_ONE,
        ASSEMBLY_CSV_TWO,
    )


@pytest.mark.parametrize("case", ["file", "work-root", "capability"])
def test_assemble_staged_rejects_point_of_use_tamper(tmp_path: Path, case: str) -> None:
    _, claim, staged, registry, forcing = _assembly_fixture(tmp_path)
    old_root = claim.work_dir.with_name(f"{claim.work_dir.name}-old")
    if case == "file":
        (staged.variant_dir / VARIANT_BINDING_NAME).write_bytes(b"tampered\n")
    elif case == "work-root":
        claim.work_dir.rename(old_root)
        claim.work_dir.mkdir()
        shutil.copytree(old_root / "input", claim.work_dir / "input")
        shutil.copytree(old_root / "object-store", claim.work_dir / "object-store")
    else:
        staged = dataclasses.replace(
            staged, state_path=staged.state_path.with_name("other.cfg.ic")
        )
    _refuse_assemble(registry, staged, forcing, work=claim.work_dir)
    if case == "work-root":
        assert claim.work_dir.is_dir() and old_root.is_dir()


@pytest.mark.parametrize("case", ["sp-att", "parameter", "state"])
def test_assemble_staged_checksums_files_at_kernel_point_of_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    _, claim, staged, registry, forcing = _assembly_fixture(tmp_path)
    real_kernel, calls = staged_module._assemble_kernel, 0
    state_drift = _native_segmented_cfg_ic().replace(
        b"1\t0.100000\t1e-3\t-0.0\t2.5E+01\t0.000000",
        b"1\t0.200000\t1e-3\t-0.0\t2.5E+01\t0.000000",
        1,
    )
    path, mutation = {
        "sp-att": (
            staged.variant_dir / "yd.sp.att",
            b"sp-att-drift\n",
        ),
        "parameter": (
            staged.variant_dir / VARIANT_HYDRO_PARAM_NAME,
            VALID_PARAMETER_DRIFT,
        ),
        "state": (staged.state_path, state_drift),
    }[case]

    def mutate_then_assemble(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        path.write_bytes(mutation)
        return real_kernel(*args, **kwargs)

    monkeypatch.setattr(staged_module, "_assemble_kernel", mutate_then_assemble)
    with pytest.raises(AssemblyError):
        _assemble(registry, staged, forcing)
    assert calls == 1
    assert path.read_bytes() == mutation
    assert not (claim.work_dir / "model").exists()


def test_assemble_staged_requires_full_reload_equality_before_kernel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, claim, staged, registry, forcing = _assembly_fixture(tmp_path)
    other_sha = "sha256:" + ("0" if staged.manifest_checksum[-1] != "0" else "1") * 64
    reloaded = dataclasses.replace(staged, manifest_checksum=other_sha)
    kernel_calls = 0

    def bomb(*_args: object, **_kwargs: object) -> None:
        nonlocal kernel_calls
        kernel_calls += 1
        raise AssertionError("shared assembly kernel reached")

    monkeypatch.setattr(
        staged_module, "load_staged_work_inputs", lambda **_kwargs: reloaded
    )
    monkeypatch.setattr("yd_producer._assemble_fs.directory", bomb)
    _refuse_assemble(registry, staged, forcing, work=claim.work_dir)
    assert kernel_calls == 0


@pytest.mark.parametrize("field", ["work", "source", "cycle", "project"])
def test_assemble_staged_rejects_registry_mismatch_before_model(
    tmp_path: Path, field: str
) -> None:
    _, claim, staged, registry, forcing = _assembly_fixture(tmp_path)
    other_work = (tmp_path / field / "work" / SOURCE / CYCLE_ID).resolve()
    other_work.mkdir(parents=True)
    object_root = other_work / "object-store"
    shutil.copytree(registry.object_store_root, object_root)
    mismatched = registry.__class__(
        identity=registry.identity,
        work_dir=other_work,
        object_store_root=object_root,
        registry_manifest=registry.registry_manifest,
        model_package_uri=registry.model_package_uri,
        model_manifest_uri=registry.model_manifest_uri,
    )
    overrides = {
        "work": {},
        "source": {"source": "ifs"},
        "cycle": {"cycle": CYCLE.replace(hour=0)},
        "project": {"project_name": "other"},
    }[field]
    staged = dataclasses.replace(staged, **overrides)
    _refuse_assemble(
        mismatched, staged, forcing, work=claim.work_dir, extra=mismatched.work_dir
    )


def _leaves(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }


def test_legacy_and_staged_assemblies_match_common_model_bytes(tmp_path: Path) -> None:
    staged_source, _, staged, staged_registry, staged_forcing = _assembly_fixture(
        tmp_path / "staged"
    )
    legacy_root = tmp_path / "legacy"
    legacy_identity, legacy_work, legacy_registry = stage(
        legacy_root / "scratch" / "work",
        WorkIdentity(
            source_id=SOURCE,
            cycle_time=CYCLE,
            project_name=PROJECT,
            **VARIANT_IDS,
        ),
    )
    legacy_variant = write_variant(
        legacy_root / "variant",
        legacy_identity,
        parameter=PARAMETER_TEMPLATE,
    )
    assert (legacy_variant / f"{PROJECT}.para").read_bytes() == PARAMETER_TEMPLATE
    assert not (legacy_variant / "yd.cfg.para").exists()
    legacy_states = legacy_root / "states"
    legacy_state = write_state(
        legacy_states,
        legacy_identity,
        content=staged_source.state_path.read_bytes(),
    )
    legacy_forcing = write_forcing_package(
        legacy_registry.object_store_root,
        legacy_identity,
        index=ASSEMBLY_INDEX,
        csv_one=ASSEMBLY_CSV_ONE,
        csv_two=ASSEMBLY_CSV_TWO,
    )
    staged_result = _assemble(staged_registry, staged, staged_forcing)
    legacy_result = assemble(
        registry=legacy_registry,
        variant_dir=legacy_variant,
        forcing=legacy_forcing,
        states_root=legacy_states,
        state_path=legacy_state,
    )
    native_common = {f"input/yd/{name}" for name in NATIVE_VARIANT_FILES}
    native_common.update({"input/yd/yd.tsd.forc", "X1.csv", "X2.csv"})
    legacy_common = {
        f"{PROJECT}.cfg.ic",
        f"{PROJECT}.para",
        f"{PROJECT}.tsd.forc",
        "X1.csv",
        "X2.csv",
        "nested/ordinary.dat",
    }
    staged_leaves = _leaves(staged_result.path)
    legacy_leaves = _leaves(legacy_result.path)
    assert staged_leaves == native_common
    assert legacy_leaves == legacy_common
    assert (
        staged_result.state_path.read_bytes() == legacy_result.state_path.read_bytes()
    )
    assert tuple(path.read_bytes() for path in staged_result.forcing_csv_paths) == (
        tuple(path.read_bytes() for path in legacy_result.forcing_csv_paths)
    )
    assert legacy_result.parameter_path.read_bytes() == PARAMETER_EXPECTED
    assert (legacy_result.path / "nested/ordinary.dat").read_bytes() == (
        b"nested bytes\n"
    )
    assert legacy_result.path == legacy_work / "model"
    assert staged_source.variant_dir.is_dir()


def test_native_assembly_to_tracker_captured_and_genuine_miss(tmp_path: Path) -> None:

    source, _, staged, registry, forcing = _assembly_fixture(tmp_path)
    shutil.rmtree(source.root)
    result = _assemble(registry, staged, forcing)
    assert stock_runtime_values(result.parameter_path.read_bytes())["END"] == 7.0
    payload = build_cfg_ic(mesh_count=2, river_count=2, minute="720.000000").payload
    (result.path / f"{PROJECT}.cfg.ic.update").write_bytes(payload)
    tracker = CheckpointTracker(
        run_dir=result.path, project_name=PROJECT, checkpoint_hours=(12,)
    )
    tracker.capture_available()
    captured = tracker.captured[12]
    assert captured.path.read_bytes() == payload

    calls = 0

    def zero(*, run_directory, output_dir):
        nonlocal calls
        calls += 1
        return 0

    record = ensure_twelve_hour_checkpoint(
        tracker=tracker, run_directory=result, runner=zero
    )
    assert record is captured
    assert calls == 0 and record.path.read_bytes() == payload
    other, _, other_staged, other_registry, other_forcing = _assembly_fixture(
        tmp_path / "miss"
    )
    shutil.rmtree(other.root)
    missed = _assemble(other_registry, other_staged, other_forcing)
    original, seen = missed.parameter_path.read_bytes(), {}

    def miss(*, run_directory, output_dir):
        seen["p"] = run_directory.parameter_path.read_bytes()
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{PROJECT}.cfg.ic.update").write_bytes(payload)
        return 0

    recovered = ensure_twelve_hour_checkpoint(
        tracker=CheckpointTracker(
            run_dir=missed.path, project_name=PROJECT, checkpoint_hours=(12,)
        ),
        run_directory=missed,
        runner=miss,
    )
    assert stock_runtime_values(seen["p"])["END"] == 0.5
    assert missed.parameter_path.read_bytes() == original
    assert recovered.relative_minute == 720.0
    assert recovered.path.read_bytes() == payload
