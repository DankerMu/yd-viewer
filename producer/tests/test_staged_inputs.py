"""Public contract tests for claimed-work staged inputs (#177)."""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import shutil
from collections import namedtuple
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from assembly_fixtures import (
    PARAMETER_EXPECTED,
    PARAMETER_TEMPLATE,
    SP_ATT,
    write_forcing_package,
)
from prepare_fixtures import (
    VARIANT_HANDOFF_NAME,
    binding_bytes,
    canonical_json_bytes,
    contract_payload,
    handoff_payload,
    sha256_literal,
    sp_att_bytes,
    station_payload,
    write_prepared_variant,
)

import yd_producer.staged_inputs as staged_module
from yd_producer._work_claim import WorkClaim, claim_exact_work
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
    stage_work_inputs,
)

CYCLE = datetime(2026, 8, 26, 12, tzinfo=UTC)
CYCLE_ID = "2026082612"
SOURCE = "gfs"
PROJECT = "yd"
GRID_ID = "fixture-grid-gfs"
MODEL_ID = "model-177"
MAX_MANIFEST_BYTES = MAX_ASSET_BYTES = MAX_STATE_BYTES = 65_536
LIMITS = {
    "max_manifest_bytes": MAX_MANIFEST_BYTES,
    "max_asset_bytes": MAX_ASSET_BYTES,
    "max_state_bytes": MAX_STATE_BYTES,
}
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
    ["yd.cfg.ic", "yd.para", "yd.binding", "yd.direct-grid-handoff.json", "gfs.sp.att"]
)
STAGED_FIELDS = ["source", "cycle", "work_dir", "variant_dir", "state_path", "manifest_path", "work_identity", "manifest_checksum", "file_checksums", "prepared", "project_name", "grid_id", "max_manifest_bytes", "max_asset_bytes", "max_state_bytes"]  # fmt: skip
STAGE_PARAMETERS = ["claim", "source_variant_dir", "source_state_path", "source", "cycle", "project_name", "grid_id", "max_manifest_bytes", "max_asset_bytes", "max_state_bytes"]  # fmt: skip
LOAD_PARAMETERS = ["work_dir", "source", "cycle", "project_name", "grid_id", "max_manifest_bytes", "max_asset_bytes", "max_state_bytes"]  # fmt: skip
STATE_KEY = f"input/states/{SOURCE}/{CYCLE_ID}.cfg.ic"
PARA_KEY = f"input/variant/{VARIANT_HYDRO_PARAM_NAME}"
VARIANT_IDS = {
    "model_id": MODEL_ID,
    "basin_id": "b",
    "basin_version_id": "v",
    "river_network_version_id": "r",
}
CALIBRATED_STATE = b"CALIBRATED-VARIANT-STATE\n"
DEFAULT_PARAMETER = b"# hydrologic parameters\nKsatH 1.0e-4\n"
SourceFixture = namedtuple("SourceFixture", "root variant_dir state_path files")
_checksum = sha256_literal


def _native_segmented_cfg_ic() -> bytes:
    minute = round(CYCLE.timestamp() / 60)
    return (
        f"2\t6\t{minute}\nIndex\tCanopy\tSnow\tSurface\tUnsat\tGW\n"
        "1\t0.100000\t1e-3\t-0.0\t2.5E+01\t0.000000\n"
        "2\t0.100000\t1e-3\t-0.0\t2.5E+01\t0.000000\n"
        "Index\tRiver_Stage\n1\t0.100000\n"
    ).encode()


def _ids(**overrides: object) -> dict[str, object]:
    values = dict(
        LIMITS, source=SOURCE, cycle=CYCLE, project_name=PROJECT, grid_id=GRID_ID
    )
    values.update(overrides)
    return values


def _write_source(root: Path, **kwargs: object) -> SourceFixture:
    source_root, variant_dir = root.resolve(), root.resolve() / "variant"
    state_path = source_root / "states" / SOURCE / f"{CYCLE_ID}.cfg.ic"
    binding = kwargs.get("binding") or binding_bytes(grid_id=GRID_ID, source_id=SOURCE)
    sp_att = kwargs.get("sp_att") or sp_att_bytes(source_id=SOURCE)
    stations = [station_payload(grid_id=GRID_ID, index=1)]
    if kwargs.get("two_stations"):
        extra = station_payload(grid_id=GRID_ID, index=2)
        extra.update(latitude=7.0, longitude=6.0, x=8.0, y=9.0, z=10.0)
        stations.append(extra)
    shared = {
        "source_id": SOURCE,
        "project_name": PROJECT,
        "grid_id": GRID_ID,
        "binding": binding,
        "sp_att": sp_att,
    }
    extras = {"grid_signature": "s", "model_input_package_id": "p"}
    contract = contract_payload(model_id=MODEL_ID, stations=stations, **shared) | extras
    write_prepared_variant(
        variant_dir,
        state=kwargs.get("calibrated_state", b"calibrated-state\n"),
        parameter=kwargs.get("parameter", DEFAULT_PARAMETER),
        payload=handoff_payload(ids=VARIANT_IDS, contract=contract, **shared),
        **shared,
    )
    state_bytes = _native_segmented_cfg_ic()
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(state_bytes)
    files = {name: (variant_dir / name).read_bytes() for name in VARIANT_FILES}
    staged = {f"input/variant/{name}": content for name, content in files.items()}
    staged[STATE_KEY] = state_bytes
    return SourceFixture(source_root, variant_dir, state_path, staged)


def _claim(tmp_path: Path) -> WorkClaim:
    return claim_exact_work(
        work_root=(tmp_path / "scratch/work").resolve(),
        source=SOURCE,
        cycle=CYCLE,
        cycle_name=CYCLE_ID,
    )


def _stage_pair(tmp_path: Path, leaf: str = "nfs-test-owned"):
    return _write_source(tmp_path / leaf), _claim(tmp_path)


def _stage_call(claim: WorkClaim, source: SourceFixture, **overrides: object):
    values: dict[str, object] = {
        "claim": claim,
        "source_variant_dir": source.variant_dir,
        "source_state_path": source.state_path,
        **_ids(),
    }
    values.update(overrides)
    return stage_work_inputs(**values)  # type: ignore[arg-type]


def _stage_fixture(tmp_path: Path) -> tuple[SourceFixture, WorkClaim, StagedWorkInputs]:
    source, claim = _stage_pair(tmp_path)
    return source, claim, _stage_call(claim, source)


def _load(work_dir: Path, **overrides: int) -> StagedWorkInputs:
    return load_staged_work_inputs(work_dir=work_dir, **_ids(**overrides))  # type: ignore[arg-type]


def _assert_required_signature(callable_: object, names: list[str]) -> None:
    parameters = inspect.signature(callable_).parameters
    assert list(parameters) == names
    for parameter in parameters.values():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


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
    staged: StagedWorkInputs, mutate: object, *, canonical: bool = True
) -> None:
    payload = json.loads(staged.manifest_path.read_bytes())
    mutate(payload)  # type: ignore[operator]
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


def test_public_api_shape_and_freeze(tmp_path: Path) -> None:
    assert STAGED_INPUTS_SCHEMA == "yd.run.staged-inputs.v1"
    assert STAGED_INPUT_DIRNAME == "input"
    assert STAGED_VARIANT_DIRNAME == "variant"
    assert STAGED_STATES_DIRNAME == "states"
    assert STAGED_INPUTS_MANIFEST_FILENAME == "yd.staged-inputs.json"
    _assert_required_signature(StagedWorkInputs, STAGED_FIELDS)
    _assert_required_signature(stage_work_inputs, STAGE_PARAMETERS)
    _assert_required_signature(load_staged_work_inputs, LOAD_PARAMETERS)
    names = [field.name for field in dataclasses.fields(StagedWorkInputs)]
    assert names == STAGED_FIELDS
    assert StagedWorkInputs.__dataclass_params__.frozen
    _, _, staged = _stage_fixture(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        staged.source = "ifs"  # type: ignore[misc]
    values = {field: getattr(staged, field) for field in STAGED_FIELDS}
    invalid_values = {
        "source": 1,
        "project_name": None,
        "grid_id": False,
        "manifest_checksum": b"sha256",
        "work_dir": str(staged.work_dir),
        "prepared": object(),
        "work_identity": [*staged.work_identity],
        "work_identity-bool": (True, staged.work_identity[1]),
        "work_identity-member": ("1", staged.work_identity[1]),
        "file_checksums": [*staged.file_checksums],
        "file_checksums-member": (("key", 1),),
        **{
            f"{field}-{kind}": value
            for field in ("max_manifest_bytes", "max_asset_bytes", "max_state_bytes")
            for kind, value in (("nonpositive", 0), ("bool", True))
        },
    }
    for case, value in invalid_values.items():
        field = case.rsplit("-", 1)[0] if "-" in case else case
        with pytest.raises((TypeError, ValueError)):
            StagedWorkInputs(**(values | {field: value}))


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
    expected_checksums = {key: _checksum(value) for key, value in source.files.items()}
    expected_manifest = _expected_manifest(work, source.files)
    manifest_bytes = staged.manifest_path.read_bytes()
    assert manifest_bytes == canonical_json_bytes(expected_manifest)
    manifest = json.loads(manifest_bytes)
    assert set(manifest) == set(expected_manifest)
    assert len(manifest["files"]) == 6
    assert set(manifest["files"]) == set(source.files)
    for key in expected_checksums:
        content = work.joinpath(*key.split("/")).read_bytes()
        assert content == source.files[key]
        assert manifest["files"][key] == _checksum(content)
        assert manifest["files"][key].startswith("sha256:")
        assert len(manifest["files"][key]) == 71
    assert "input/yd.staged-inputs.json" not in manifest["files"]
    for forbidden in (b'"st_dev"', b'"st_ino"', b'"job_id"', str(source.root).encode()):
        assert forbidden not in manifest_bytes
    assert staged.manifest_checksum == _checksum(manifest_bytes)
    assert dict(staged.file_checksums) == expected_checksums
    assert _load(work) == staged


def test_load_survives_source_disconnect(tmp_path: Path) -> None:
    source, claim, staged = _stage_fixture(tmp_path)
    shutil.rmtree(source.root)
    assert not source.root.exists()
    reloaded = _load(claim.work_dir)
    assert reloaded == staged
    for path in (reloaded.work_dir, reloaded.variant_dir, reloaded.state_path, reloaded.manifest_path):  # fmt: skip
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
    source, claim = (
        _write_source(tmp_path / "nfs-test-owned"),
        _claim(tmp_path / ("w" * 80)),
    )
    expected = canonical_json_bytes(_expected_manifest(claim.work_dir, source.files))
    handoff_size = len((source.variant_dir / VARIANT_HANDOFF_NAME).read_bytes())
    cap = len(expected) - 1
    assert cap >= handoff_size
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


WRONG_FIELDS = "source_id cycle_id work_dir schema_version".split()  # noqa: SIM905
MANIFEST_MUTATIONS = [
    ("top-list", "top", []),
    ("files-list", "files", []),
    *((f"{field}-wrong-type", field, False) for field in WRONG_FIELDS),
    ("missing-top-key", "pop", "source_id"),
    ("absolute-key", PARA_KEY, "/tmp/yd.para"),
    ("parent-key", PARA_KEY, "input/variant/../yd.para"),
    ("backslash-key", PARA_KEY, r"input\variant\yd.para"),
    ("nul-key", PARA_KEY, "input/variant/yd.para\0"),
    ("self-key", PARA_KEY, "input/yd.staged-inputs.json"),
    ("nested-key", PARA_KEY, "input/variant/nested/file"),
    ("wrong-state-key", STATE_KEY, f"input/states/ifs/{CYCLE_ID}.cfg.ic"),
    ("checksum-non-string", STATE_KEY, 1),
    ("path-alias", PARA_KEY, "input//variant/yd.para"),
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
        encoded = canonical_json_bytes(payload)  # type: ignore[arg-type]
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
            b'"schema_version":"yd.run.staged-inputs.v1",'
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
            (staged.variant_dir / "gfs.sp.att").write_bytes(b"corrupt sp.att\n")

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
        parameter=PARAMETER_TEMPLATE,
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
    handoff = source.files[f"input/variant/{VARIANT_HANDOFF_NAME}"]
    shutil.rmtree(source.root)
    assert not source.root.exists()
    _assert_required_signature(staged_module.assemble_staged, ASSEMBLE_PARAMETERS)
    result = _assemble(registry, staged, forcing)
    assert result.path == claim.work_dir / "model" and result.path.is_dir()
    assert result.state_path.read_bytes() == cycle_state
    assert result.state_path.read_bytes() != CALIBRATED_STATE
    assert result.parameter_path.read_bytes() == PARAMETER_EXPECTED
    expected_variant = {
        VARIANT_BINDING_NAME: ASSEMBLY_BINDING,
        VARIANT_HANDOFF_NAME: handoff,
        "gfs.sp.att": SP_ATT,
    }
    for name, content in expected_variant.items():
        assert (result.path / name).read_bytes() == content
    assert not (result.path / "yd.staged-inputs.json").exists()
    assert result.forcing_index_path.name == "yd.tsd.forc"
    assert result.forcing_index_path.read_bytes() == ASSEMBLY_INDEX
    assert tuple(path.name for path in result.forcing_csv_paths) == ("X1.csv", "X2.csv")
    assert tuple(path.read_bytes() for path in result.forcing_csv_paths) == (
        ASSEMBLY_CSV_ONE,
        ASSEMBLY_CSV_TWO,
    )
    assert not source.root.exists()


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


@pytest.mark.parametrize("case", ["binding", "parameter", "state"])
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
        "binding": (
            staged.variant_dir / VARIANT_BINDING_NAME,
            b"opaque\x00binding-drift\n",
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


def test_legacy_and_staged_assemblies_match_common_model_bytes(tmp_path: Path) -> None:
    staged_source, _, staged, staged_registry, staged_forcing = _assembly_fixture(tmp_path / "staged")  # fmt: skip
    legacy_source, legacy_claim, _, legacy_registry, legacy_forcing = _assembly_fixture(tmp_path / "legacy")  # fmt: skip
    nested = legacy_source.variant_dir / "nested"
    nested.mkdir()
    (nested / "ordinary.dat").write_bytes(b"legacy nested bytes\n")
    staged_result = _assemble(staged_registry, staged, staged_forcing)
    legacy_result = assemble(
        registry=legacy_registry,
        variant_dir=legacy_source.variant_dir,
        forcing=legacy_forcing,
        states_root=legacy_source.state_path.parents[1],
        state_path=legacy_source.state_path,
    )
    common = {
        "yd.cfg.ic",
        "yd.para",
        "yd.binding",
        "yd.direct-grid-handoff.json",
        "gfs.sp.att",
        "yd.tsd.forc",
        "X1.csv",
        "X2.csv",
    }
    staged_leaves = {p.relative_to(staged_result.path).as_posix() for p in staged_result.path.rglob("*") if p.is_file()}  # fmt: skip
    legacy_leaves = {p.relative_to(legacy_result.path).as_posix() for p in legacy_result.path.rglob("*") if p.is_file()}  # fmt: skip
    assert staged_leaves == common
    assert legacy_leaves == common | {"nested/ordinary.dat"}
    for relative in common:
        left = (staged_result.path / relative).read_bytes()
        right = (legacy_result.path / relative).read_bytes()
        assert left == right
    nested_bytes = (legacy_result.path / "nested/ordinary.dat").read_bytes()
    assert nested_bytes == b"legacy nested bytes\n"
    assert legacy_result.path == legacy_claim.work_dir / "model"
    assert staged_source.variant_dir.is_dir()
