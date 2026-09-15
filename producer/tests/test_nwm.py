import json
import os
import shutil
import struct
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from assembly_fixtures import NATIVE_PARAMETER_TEMPLATE
from cfg_ic_fixtures import build_cfg_ic
from cli_fixtures import write_config, write_fake_interpreter, write_local
from dat_fixtures import (
    DEFAULT_HEADER_TEXT,
    FIXED_HEADER_BYTES,
    FLOAT64_BYTES,
    TEXT_HEADER_BYTES,
    build_dat_bytes,
    expected_v2_size,
)
from prepare_fixtures import handoff_payload, write_prepared_variant

from yd_producer import config as config_module
from yd_producer import nwm
from yd_producer.config import ConfigError, load_config, load_local
from yd_producer.controller import AttemptRequest, RunError, RunOutcome, run_once
from yd_producer.executor import JobRecord, JobState
from yd_producer.nwm import (
    PREPARE_DRIVER_SCRIPT,
    RECEIPT_FILENAME,
    ProductionAttemptDriver,
    ProductionAttemptError,
    invoke_mapping_builder,
)


class RecordingRunner:
    def __init__(self, delegate=None):
        self.calls: list[tuple[list[str], dict]] = []
        self._delegate = delegate

    def __call__(self, command, *, cwd, env, capture_output, text):
        self.calls.append(
            (
                list(command),
                {
                    "cwd": cwd,
                    "env": env,
                    "capture_output": capture_output,
                    "text": text,
                },
            )
        )
        if self._delegate is not None:
            return self._delegate(
                command, cwd=cwd, env=env, capture_output=capture_output, text=text
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def _load(tmp_path, **local_kwargs):
    config = load_config(write_config(tmp_path))
    local = load_local(write_local(tmp_path, **local_kwargs), config)
    return local, config


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("missing-interpreter", "nwm.python"),
        ("directory-interpreter", "nwm.python"),
        ("non-executable-interpreter", "nwm.python"),
        ("relative-interpreter", "nwm.python"),
        ("slashless-interpreter", "nwm.python"),
        ("missing-checkout", "nwm.checkout_root"),
        ("file-checkout", "nwm.checkout_root"),
    ],
)
def test_invalid_prepare_runtime_starts_no_process(tmp_path, kind, field):
    interpreter = tmp_path.resolve() / "interpreter"
    checkout = tmp_path.resolve() / "checkout"
    if kind == "missing-interpreter":
        interpreter /= "absent"
        checkout.mkdir()
    elif kind == "directory-interpreter":
        interpreter.mkdir()
        checkout.mkdir()
    elif kind == "non-executable-interpreter":
        interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        interpreter.chmod(0o644)
        checkout.mkdir()
    elif kind == "relative-interpreter":
        interpreter = Path("nwm/.venv/bin/python")
        checkout.mkdir()
    elif kind == "slashless-interpreter":
        interpreter = Path("python")
        checkout.mkdir()
    else:
        interpreter = write_fake_interpreter(
            tmp_path.resolve() / "fake-python", tmp_path.resolve() / "record.json"
        )
        if kind == "file-checkout":
            checkout.write_text("not a directory\n", encoding="utf-8")
    local, _config = _load(tmp_path, python=interpreter, checkout_root=checkout)
    runner = RecordingRunner()
    with pytest.raises(ConfigError) as caught:
        invoke_mapping_builder(local, [], runner)
    assert caught.value.path == field
    assert runner.calls == []


def _run_fake(
    tmp_path,
    checkout_name="checkout",
    exit_code=0,
    args=("--source", "gfs"),
):
    checkout = tmp_path.resolve() / checkout_name
    checkout.mkdir()
    record = tmp_path.resolve() / f"record-{checkout_name}.json"
    script = write_fake_interpreter(
        tmp_path.resolve() / f"fake-python-{checkout_name}",
        record,
        exit_code=exit_code,
    )
    local, _config = _load(tmp_path, checkout_root=checkout, python=script)
    runner = RecordingRunner(subprocess.run)
    completed = invoke_mapping_builder(local, list(args), runner)
    return (
        script,
        checkout,
        json.loads(record.read_text(encoding="utf-8")),
        completed,
        runner,
    )


def test_mapping_builder_uses_only_configured_environment(tmp_path, monkeypatch):
    script, checkout, recorded, completed, _ = _run_fake(
        tmp_path, args=("--source", "gfs", "--grid-id", "fixture-grid-gfs")
    )
    assert completed.returncode == 0
    assert recorded["argv"] == [
        str(script),
        str(PREPARE_DRIVER_SCRIPT),
        "--source",
        "gfs",
        "--grid-id",
        "fixture-grid-gfs",
    ]
    assert recorded["cwd"] == recorded["pythonpath"] == str(checkout)
    assert "uv" not in " ".join(recorded["argv"])
    assert "--active" not in recorded["argv"]
    assert recorded["argv"][0] != sys.executable
    _, first_checkout, first, _, _ = _run_fake(tmp_path, checkout_name="checkout-a")
    _, second_checkout, second, _, _ = _run_fake(tmp_path, checkout_name="checkout-b")
    assert first_checkout != second_checkout
    assert first["cwd"] == first["pythonpath"] == str(first_checkout)
    assert second["cwd"] == second["pythonpath"] == str(second_checkout)
    _, _, _, failed, runner = _run_fake(tmp_path, checkout_name="nonzero", exit_code=7)
    assert failed.returncode == 7 and len(runner.calls) == 1
    monkeypatch.setenv("PYTHONPATH", "/inherited/path")
    monkeypatch.setenv("DATABASE_URL", "postgres://example")
    monkeypatch.setenv("PYTHONHOME", "/poison/home")
    isolated = tmp_path.resolve() / "isolated-checkout"
    isolated.mkdir()
    local, _config = _load(
        tmp_path,
        checkout_root=isolated,
        python=write_fake_interpreter(
            tmp_path.resolve() / "isolated-python", tmp_path.resolve() / "isolated.json"
        ),
    )
    runner = RecordingRunner()
    invoke_mapping_builder(local, ["--source", "ifs"], runner)
    kwargs = runner.calls[0][1]
    assert kwargs["capture_output"] is True and kwargs["text"] is True
    assert kwargs["env"]["PYTHONPATH"] == str(isolated)
    assert "DATABASE_URL" not in kwargs["env"] and "PYTHONHOME" not in kwargs["env"]


def test_symlinked_interpreter_is_invoked_verbatim_not_resolved(tmp_path):
    checkout = tmp_path.resolve() / "checkout"
    checkout.mkdir()
    record = tmp_path.resolve() / "record-symlink.json"
    target = write_fake_interpreter(tmp_path.resolve() / "real-python-target", record)
    venv_bin = tmp_path.resolve() / "nwm-venv" / "bin"
    venv_bin.mkdir(parents=True)
    link = venv_bin / "python"
    link.symlink_to(target)
    assert link.name != target.name
    assert link.resolve() == target
    local, _config = _load(tmp_path, checkout_root=checkout, python=link)
    completed = invoke_mapping_builder(local, [], RecordingRunner(subprocess.run))
    assert completed.returncode == 0
    recorded = json.loads(record.read_text(encoding="utf-8"))
    assert recorded["argv"][0] == str(link)
    assert recorded["argv"][0] != str(target)
    assert recorded["argv"][1] == str(PREPARE_DRIVER_SCRIPT)


SOURCE = "gfs"
CYCLE = datetime(2026, 1, 2, 0, tzinfo=UTC)
GRID_ID = "m2-synthetic-gfs-grid"
BINDING = b'{"schema_version":"m2.synthetic.binding.v1","source_id":"gfs"}\n'
BINDING_SHA256 = "2629f48afb580b6ce1c657a1c612ae97217016c36d23e94b5f7209f7d5100a30"
SP_ATT = b"1 1\nTRI\tA\tB\tC\tFORC\n1\t0\t0\t0\t1\n"
SP_ATT_SHA256 = "d0754333bd8783f3c95052cd9e53a8d22c1e165f3133ce787d686c5b59b55bad"
GRID_SIGNATURE = "912736a2ef764f4f5487bd185f86b678c3272c3e938f8a35c609a11382ddc6fa"
CONVERTER_GRID_SIGNATURE = (
    "7201e850602dce8782a5c8584d7f74b6f624f58501c026a47577775c30f808a6"
)
TWO_CELL_GRID_SIGNATURE = (
    "31bc3627e2aca5e13cf860b6bfd3fd09766c9bb4b1cd114c00adb14b448f6458"
)
VALID_CFG = (
    b"1 6 0 0\nIndex Canopy Snow Surface Unsat GW\n1 0 0 0 0 0\nIndex Stage\n1 0\n"
)
GFS_NATIVE = {
    "tmp2m": (280.0, 283.0),
    "apcp": (0.0, 3.0),
    "rh2m": (50.0, 50.0),
    "u10m": (1.0, 1.0),
    "v10m": (2.0, 2.0),
    "pressfc": (101325.0, 101325.0),
    "dswrf": (100.0, 100.0),
}
IFS_NATIVE = {
    "2t": (285.0, 286.0),
    "2d": (280.0, 281.0),
    "tp": (0.0, 0.003),
    "10u": (1.0, 1.5),
    "10v": (2.0, 2.5),
    "sp": (101325.0, 101300.0),
    "ssr": (0.0, 1_080_000.0),
    "str": (0.0, -540_000.0),
}
FORECAST_HOURS = (0, 3)
WORKER_STRIP = ("YD_ROOT", "NWM_RAW_ROOT", "NWM_CHECKOUT_ROOT", "DATABASE_URL")


def _write_handoff(
    variant: Path, source: str, grid_id: str, *, fixture: str, extra_cell: bool = False
) -> bytes:
    if fixture == "named":
        cell_id, signature = "m2-synthetic-cell", GRID_SIGNATURE
    elif extra_cell:
        cell_id, signature = "0", TWO_CELL_GRID_SIGNATURE
    else:
        cell_id, signature = "0", CONVERTER_GRID_SIGNATURE
    payload = handoff_payload(
        source_id=source,
        project_name="yd",
        grid_id=grid_id,
        binding=BINDING,
        sp_att=SP_ATT,
        checksums={
            "yd.cfg.ic": VALID_CFG,
            "yd.cfg.para": NATIVE_PARAMETER_TEMPLATE,
            "yd.binding": BINDING,
            "yd.sp.att": SP_ATT,
        },
    )
    contract = payload["direct_grid_forcing_contract"]
    contract["grid_signature"] = signature
    contract["station_bindings"][0].update(
        forcing_filename="m2-synthetic-station.csv",
        grid_cell_id=cell_id,
        latitude=0.0,
        longitude=0.0,
        station_id="m2-synthetic-station",
        x=0.0,
        y=0.0,
        z=0.0,
    )
    write_prepared_variant(
        variant,
        source_id=source,
        grid_id=grid_id,
        binding=BINDING,
        sp_att=SP_ATT,
        state=VALID_CFG,
        parameter=NATIVE_PARAMETER_TEMPLATE,
        payload=payload,
    )
    minute = round(CYCLE.timestamp() / 60)
    return VALID_CFG.replace(b"1 6 0 0\n", f"1 6 0 {minute}\n".encode(), 1)


def _write_shud(
    path: Path,
    *,
    dat: bytes,
    update: bytes,
    recovery_update: bytes | None = None,
    stdout: bytes = b"shud-ok\n",
) -> Path:
    dat_src = path.parent / "shud-dat.bin"
    update_src = path.parent / "shud-update.bin"
    recovery_src = path.parent / "shud-recovery-update.bin"
    stdout_src = path.parent / "shud-stdout.bin"
    dat_src.write_bytes(dat)
    update_src.write_bytes(update)
    recovery_src.write_bytes(update if recovery_update is None else recovery_update)
    stdout_src.write_bytes(stdout)
    path.write_text(
        "#!/bin/sh\n"
        '[ "$1" = "-o" ] && [ "$#" -eq 3 ] || exit 64\n'
        "out=$2; project=$3\n"
        '[ -f "input/yd/$project.cfg.ic" ] || exit 65\n'
        '[ -f "input/yd/$project.cfg.para" ] || exit 65\n'
        '[ -s "input/yd/$project.tsd.forc" ] || exit 65\n'
        'grep -Eq "^END[[:space:]]+(7|0\\.5)$" "input/yd/$project.cfg.para" || exit 65\n'
        f'if [ "${{out##*/}}" = f012 ]; then update="{recovery_src}"; '
        f'else update="{update_src}"; fi\n'
        'mkdir -p "$out"\n'
        f'cp "{dat_src}" "$out/$project.rivqdown.dat"\n'
        'cp "$update" "$out/$project.cfg.ic.update"\n'
        f'cat "{stdout_src}"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _synthetic_shud(
    path: Path, *, stdout: bytes = b"shud-ok\n", recovery: bool = False
) -> Path:
    valid = build_cfg_ic(mesh_count=1, river_count=1, minute="720.000000").payload
    update = build_cfg_ic(mesh_count=1, river_count=1, minute="0.000000").payload
    return _write_shud(
        path,
        dat=build_dat_bytes(nc=1, rows=168),
        update=update if recovery else valid,
        recovery_update=valid if recovery else None,
        stdout=stdout,
    )


def _encode_raw_bytes(
    native, lead, *, source: str, variables=None, extra_cell: bool = False
) -> bytes:
    import tempfile

    import xarray as xr
    from netcdf_fixture import CFGRIB_SHORT_NAMES

    lead_index = FORECAST_HOURS.index(lead)
    names = tuple(variables) if variables is not None else tuple(native)
    encoder_source = "IFS" if source == "ifs" else "gfs"
    lons = [0.0, 1.0] if extra_cell else [0.0]
    lats = [0.0, 1.0] if extra_cell else [0.0]
    data_vars = {}
    for variable in names:
        value = native[variable][lead_index]
        data_vars[variable] = (
            ["point"],
            [value, value + 1.0] if extra_cell else [value],
        )
    dataset = xr.Dataset(
        data_vars,
        coords={
            "point": list(range(len(lons))),
            "longitude": ("point", lons),
            "latitude": ("point", lats),
        },
        attrs={
            "source": encoder_source,
            "forecast_hour": lead,
            "cycle_time": CYCLE.isoformat(),
        },
    )
    for variable in names:
        short = CFGRIB_SHORT_NAMES.get(variable, variable)
        dataset[variable].attrs["GRIB_shortName"] = short
        dataset[variable].attrs["shortName"] = short
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.nc"
            dataset.to_netcdf(path, engine="netcdf4", format="NETCDF4")
            return path.read_bytes()
    finally:
        dataset.close()


def _canonical_cell_ids(work_dir: Path, source: str = SOURCE) -> list[str]:
    path = work_dir / f"object-store/canonical/{source}/grid/{GRID_ID}/grid.json"
    grid = json.loads(path.read_text(encoding="utf-8"))
    return [str(cell.get("grid_cell_id", cell.get("id"))) for cell in grid["cells"]]


def _plant_raw(
    work_dir: Path, *, source: str, native, extra_cell: bool = False
) -> None:
    from yd_producer.raw.manifest import DownloadManifest, ManifestEntry
    from yd_producer.rawscan import SOURCE_DIR_NAMES
    from yd_producer.store.object_store import LocalObjectStore

    store = LocalObjectStore(work_dir / "object-store")
    cycle_id = CYCLE.strftime("%Y%m%d%H")
    source_dir = SOURCE_DIR_NAMES[source]
    entries = []
    for lead in FORECAST_HOURS:
        for native_variable in native:
            filename = f"{native_variable}_f{lead:03d}.nc"
            local_key = f"raw/{source_dir}/{cycle_id}/{filename}"
            store.write_bytes_atomic(
                local_key,
                _encode_raw_bytes(
                    native,
                    lead,
                    source=source,
                    variables=(native_variable,),
                    extra_cell=extra_cell,
                ),
            )
            entries.append(
                ManifestEntry(
                    remote_url=f"file://{filename}",
                    local_key=local_key,
                    variable=native_variable,
                    forecast_hour=lead,
                    metadata={
                        "cycle_time": CYCLE.isoformat(),
                        "valid_time": (CYCLE + timedelta(hours=lead)).isoformat(),
                    },
                )
            )
    manifest = DownloadManifest(
        source_id=source_dir,
        cycle_time=CYCLE,
        entries=tuple(entries),
        metadata={"forecast_hours": list(FORECAST_HOURS)},
    )
    (work_dir / "object-store" / "raw-manifest.json").write_bytes(
        json.dumps(manifest.as_dict(), ensure_ascii=True, indent=2).encode("utf-8")
    )


def _stage_synthetic(
    tmp_path: Path, *, source=SOURCE, grid_id=GRID_ID, fixture="named", extra_cell=False
):
    from yd_producer._work_claim import claim_exact_work
    from yd_producer.staged_inputs import stage_work_inputs

    source_root = tmp_path.resolve() / f"source-only-{source}"
    source_root.mkdir()
    claim = claim_exact_work(
        work_root=tmp_path.resolve() / "work",
        source=source,
        cycle=CYCLE,
        cycle_name=CYCLE.strftime("%Y%m%d%H"),
    )
    variant_dir = source_root / "variant"
    variant_dir.mkdir()
    cycle_state = _write_handoff(
        variant_dir, source, grid_id, fixture=fixture, extra_cell=extra_cell
    )
    state_path = source_root / "states" / source / "2026010200.cfg.ic"
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(cycle_state)
    stage_work_inputs(
        claim=claim,
        source_variant_dir=variant_dir,
        source_state_path=state_path,
        source=source,
        cycle=CYCLE,
        project_name="yd",
        grid_id=grid_id,
        max_manifest_bytes=4096,
        max_asset_bytes=4096,
        max_state_bytes=4096,
    )
    return source_root, claim, variant_dir, state_path


def _request(
    claim, variant_dir, state_path, shud: Path, *, source=SOURCE
) -> AttemptRequest:
    return AttemptRequest(
        source=source,
        cycle=CYCLE,
        work_root=claim.work_dir.parent.parent,
        work_dir=claim.work_dir,
        object_store_root=claim.work_dir / "object-store",
        raw_manifest_path=claim.work_dir / "object-store" / "raw-manifest.json",
        variant_dir=variant_dir,
        state_path=state_path,
        shud_binary=str(shud),
        checkpoint_hours=(12,),
        forecast_days=7,
        output_interval_minutes=60,
        reach_count=1,
    )


def _record(job_id="job-9", **kwargs):
    return JobRecord(
        job_id=job_id,
        name=kwargs.get("name", "yd-gfs-2026010200"),
        state=kwargs.get("state", JobState.SUCCEEDED),
        resources=kwargs.get("resources") or {"partition": "cpu", "account": "a"},
        submitted_at=CYCLE,
        started_at=kwargs.get("started", CYCLE),
        ended_at=kwargs.get("ended", CYCLE),
    )


def _worker_env(*, extra: dict | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in WORKER_STRIP}
    env["SLURM_JOB_ID"] = "job-9"
    if extra:
        env.update(extra)
    return env


class _SubprocessJobExecutor:
    def __init__(self, after_worker=None) -> None:
        self._after_worker = after_worker
        self._spec = None
        self._record = None
        self._polls = 0

    def submit(self, spec):
        self._spec = spec
        self._record = _record(
            name=spec.name,
            state=JobState.PENDING,
            started=None,
            ended=None,
            resources=dict(spec.resources),
        )
        return self._record

    def poll(self, job_id: str) -> JobRecord:
        if self._record is None or self._spec is None or job_id != self._record.job_id:
            raise RuntimeError(f"unknown job {job_id}")
        self._polls += 1
        if self._polls == 1:
            self._record = replace(
                self._record,
                state=JobState.RUNNING,
                started_at=CYCLE,
                ended_at=None,
            )
            return self._record
        completed = subprocess.run(
            list(self._spec.command),
            check=False,
            capture_output=True,
            text=True,
            env=_worker_env(),
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr or completed.stdout or "worker failed")
        if self._after_worker is not None:
            self._after_worker(self._spec.work_dir)
        self._record = replace(
            self._record, state=JobState.SUCCEEDED, started_at=CYCLE, ended_at=CYCLE
        )
        return self._record


class _TamperAfterCollectDriver(ProductionAttemptDriver):
    def collect(self, *, attempt, terminal_record):
        products = super().collect(attempt=attempt, terminal_record=terminal_record)
        path = products.tracker.captured[12].path
        path.write_bytes(path.read_bytes() + b"tamper")
        return products


def _prepared_attempt(tmp_path, **kwargs):
    shud_name = kwargs.pop("shud_name", None)
    shud_stdout = kwargs.pop("shud_stdout", b"shud-ok\n")
    shud_recovery = kwargs.pop("shud_recovery", False)
    source = kwargs.get("source", SOURCE)
    grid_id = kwargs.get("grid_id", GRID_ID)
    source_root, claim, variant_dir, state_path = _stage_synthetic(tmp_path, **kwargs)
    shud = _synthetic_shud(
        tmp_path / (shud_name or f"shud-{source}"),
        stdout=shud_stdout,
        recovery=shud_recovery,
    )
    attempt = ProductionAttemptDriver(grid_id=grid_id).prepare(
        request=_request(claim, variant_dir, state_path, shud, source=source)
    )
    return source_root, claim, attempt


def _run_worker(tmp_path, monkeypatch, *, native=GFS_NATIVE, **kwargs):
    kwargs.setdefault("fixture", "converter")
    extra_cell = kwargs.get("extra_cell", False)
    source = kwargs.get("source", SOURCE)
    source_root, claim, attempt = _prepared_attempt(tmp_path, **kwargs)
    encoded = (claim.work_dir / "yd.attempt-handoff.json").read_text(encoding="utf-8")
    assert str(source_root) not in encoded
    assert "work_identity" not in encoded
    shutil.rmtree(source_root)
    _plant_raw(claim.work_dir, source=source, native=native, extra_cell=extra_cell)
    monkeypatch.setenv("SLURM_JOB_ID", "job-9")
    completed = subprocess.run(
        list(attempt.command),
        check=False,
        capture_output=True,
        text=True,
        env=_worker_env(),
    )
    return claim, attempt, completed


def _production_tree(tmp_path: Path):
    root = tmp_path.resolve()
    yd_root, scratch, raw_root = root / "yd", root / "scratch", root / "nwm" / "raw"
    variant = yd_root / "input" / "models" / "yd_gfs"
    states = yd_root / "states" / SOURCE
    variant.mkdir(parents=True)
    states.mkdir(parents=True)
    (yd_root / "output").mkdir()
    scratch.mkdir()
    cycle_state = _write_handoff(variant, SOURCE, GRID_ID, fixture="converter")
    (states / "2026010200.cfg.ic").write_bytes(cycle_state)
    _plant_nwm_raw_root(raw_root, source=SOURCE, native=GFS_NATIVE)
    config = config_module.Config(
        forecast_days=7,
        output_interval_minutes=60,
        checkpoint_hours=(12,),
        reach_count=1,
        nwm_canonical_grid_id=config_module.CanonicalGridConfig(
            gfs=GRID_ID, ifs="m2-synthetic-ifs-grid"
        ),
        cycle=config_module.CycleConfig(hours=(0, 12)),
        variants=config_module.VariantsConfig(
            gfs="input/models/yd_gfs", ifs="input/models/yd_ifs"
        ),
        raw=config_module.RawConfig(
            ifs=config_module.RawSourceConfig(
                lead_hours=FORECAST_HOURS,
                variables=tuple(IFS_NATIVE),
                bundles=("ifs.t{cycle_hour}z.f{lead}.bundle.grib2",),
                f000_special=False,
            ),
            gfs=config_module.RawSourceConfig(
                lead_hours=FORECAST_HOURS,
                variables=tuple(GFS_NATIVE),
                bundles=("gfs.t{cycle_hour}z.pgrb2.0p25.f{lead}.bundle.grib2",),
                f000_special=False,
            ),
        ),
        slurm=config_module.SlurmSchema(required_fields=("partition", "account")),
    )
    local = config_module.LocalConfig(
        yd_root=str(yd_root),
        scratch_root=str(scratch),
        shud_binary=str(_synthetic_shud(root / "shud-gfs-prod")),
        nwm=config_module.NwmLocal(
            raw_root=str(raw_root),
            checkout_root=str(root / "nwm" / "checkout"),
            python=str(sys.executable),
        ),
        slurm={"partition": "cpu", "account": "a"},
        cron=config_module.CronLocal(
            lock_path=str(root / "run" / "yd-producer.lock"), log_dir=str(root / "log")
        ),
    )
    return config, local, yd_root, scratch


def _run_production(config, local, executor, driver):
    return run_once(
        config=config,
        local=local,
        source=SOURCE,
        executor=executor,
        driver=driver,
        poll_wait=lambda: None,
    )


@pytest.mark.parametrize(
    ("source", "native", "grid_id", "recovery"),
    [
        (SOURCE, GFS_NATIVE, GRID_ID, False),
        ("ifs", IFS_NATIVE, "m2-synthetic-gfs-grid", True),
    ],
)
def test_independent_worker_converts_source_raw_and_writes_receipt(
    tmp_path, monkeypatch, source, native, grid_id, recovery
):
    assert (sha256(BINDING).hexdigest(), sha256(SP_ATT).hexdigest()) == (
        BINDING_SHA256,
        SP_ATT_SHA256,
    )
    assert (
        sha256(b'{"grid_points":[["m2-synthetic-cell",0.0,0.0]]}').hexdigest(),
        sha256(b'{"grid_points":[["0",0.0,0.0]]}').hexdigest(),
    ) == (GRID_SIGNATURE, CONVERTER_GRID_SIGNATURE)
    stdout = b"final-burst\n" * 20_000 if source == SOURCE else b"shud-ok\n"
    expected_log = stdout * (2 if recovery else 1)
    claim, attempt, completed = _run_worker(
        tmp_path,
        monkeypatch,
        source=source,
        native=native,
        grid_id=grid_id,
        shud_stdout=stdout,
        shud_recovery=recovery,
    )
    assert completed.returncode == 0, completed.stderr
    assert _canonical_cell_ids(claim.work_dir, source=source) == ["0"]
    receipt = json.loads(
        (claim.work_dir / RECEIPT_FILENAME).read_text(encoding="utf-8")
    )
    assert receipt["job_id"] == "job-9"
    assert receipt["source"] == source
    assert Path(receipt["scratch_dat"]) == attempt.scratch_dat
    assert attempt.scratch_dat == claim.work_dir / "model" / "yd.rivqdown.dat"
    native_dir = claim.work_dir / "model" / "input" / "yd"
    assert tuple(
        Path(receipt["run_directory"][name])
        for name in ("state_path", "parameter_path", "forcing_index_path")
    ) == (
        native_dir / "yd.cfg.ic",
        native_dir / "yd.cfg.para",
        native_dir / "yd.tsd.forc",
    )
    assert all(
        Path(path).parent == claim.work_dir / "model"
        for path in receipt["run_directory"]["forcing_csv_paths"]
    )
    assert (claim.work_dir / "job.log").read_bytes() == expected_log
    assert receipt["merged_log_checksum"] == (
        f"sha256:{sha256(expected_log).hexdigest()}"
    )
    if recovery:
        assert (
            claim.work_dir / "state_checkpoint_recovery" / "f012" / "yd.cfg.ic.update"
        ).is_file()
    products = ProductionAttemptDriver(grid_id=GRID_ID).collect(
        attempt=attempt, terminal_record=_record()
    )
    assert 12 in products.tracker.captured


@pytest.mark.parametrize("failure", ("missing_raw", "database_url"))
def test_worker_fails_closed_without_raw_or_database_url(
    tmp_path, monkeypatch, failure
):
    source_root, claim, attempt = _prepared_attempt(
        tmp_path, fixture="converter", shud_name=f"shud-{failure}"
    )
    shutil.rmtree(source_root)
    extra = {"DATABASE_URL": "postgresql://x"} if failure == "database_url" else None
    completed = subprocess.run(
        list(attempt.command),
        check=False,
        capture_output=True,
        text=True,
        env=_worker_env(extra=extra),
    )
    assert completed.returncode != 0
    assert not (claim.work_dir / RECEIPT_FILENAME).exists()


@pytest.mark.parametrize(
    "mutation",
    (
        "job",
        "source",
        "cycle",
        "work",
        "model",
        "binding",
        "spatt",
        "digest",
        "path",
        "checksum",
        "oversize",
        "partial",
    ),
)
def test_collect_rejects_tampered_receipt_from_success(tmp_path, monkeypatch, mutation):
    claim, attempt, completed = _run_worker(tmp_path, monkeypatch)
    assert completed.returncode == 0, completed.stderr
    path = claim.work_dir / RECEIPT_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    other_update = str(claim.work_dir / "model" / "other.update")
    replacements = {
        "job": (payload, "job_id", "other"),
        "source": (payload, "source", "ifs"),
        "cycle": (payload, "cycle", "2026-01-03T00:00:00+00:00"),
        "work": (payload, "work_dir", str(claim.work_dir.parent / "other")),
        "model": (payload["identity"], "model_id", "other-model"),
        "binding": (payload, "binding_checksum", "sha256:" + "0" * 64),
        "spatt": (payload, "sp_att_checksum", "sha256:" + "0" * 64),
        "digest": (payload, "attempt_payload_digest", "sha256:" + "0" * 64),
        "path": (payload["checkpoint"], "path", other_update),
        "checksum": (payload, "scratch_dat_checksum", "sha256:" + "0" * 64),
    }
    if mutation == "oversize":
        path.write_bytes(b"{" + b"x" * 70000 + b"}")
    elif mutation == "partial":
        path.write_text("{", encoding="utf-8")
    else:
        target, key, value = replacements[mutation]
        target[key] = value
        path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProductionAttemptError):
        ProductionAttemptDriver(grid_id=GRID_ID).collect(
            attempt=attempt, terminal_record=_record()
        )


def test_prepare_rejects_nfs_path_in_handoff(tmp_path):
    source_root, claim, attempt = _prepared_attempt(tmp_path, fixture="converter")
    text = (claim.work_dir / "yd.attempt-handoff.json").read_text(encoding="utf-8")
    assert str(source_root) not in text and "work_identity" not in text
    assert attempt.command[:3] == (sys.executable, "-m", "yd_producer.nwm")


def _plant_nwm_raw_root(raw_root: Path, *, source: str, native) -> None:
    from yd_producer.rawscan import SOURCE_DIR_NAMES

    segment = SOURCE_DIR_NAMES[source]
    cycle_id = CYCLE.strftime("%Y%m%d%H")
    cycle_hour = f"{CYCLE.hour:02d}"
    pattern = (
        "gfs.t{cycle_hour}z.pgrb2.0p25.f{lead}.bundle.grib2"
        if source == "gfs"
        else "ifs.t{cycle_hour}z.f{lead}.bundle.grib2"
    )
    base = raw_root / segment / cycle_id
    base.mkdir(parents=True)
    entries = []
    for lead in FORECAST_HOURS:
        name = pattern.replace("{cycle_hour}", cycle_hour).replace(
            "{lead}", f"{lead:03d}"
        )
        (base / name).write_bytes(
            _encode_raw_bytes(native, lead, source=source, variables=native)
        )
        cycle_iso = CYCLE.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        valid_iso = (CYCLE + timedelta(hours=lead)).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        remote = f"https://mirror.invalid/{segment}/{cycle_id}/{name}"
        for variable in native:
            metadata = {
                "cycle_time": cycle_iso,
                "valid_time": valid_iso,
                "bundle": {"layout": "per_forecast_hour", "variables": list(native)},
                "grib_short_name": variable,
                "cfgrib_filter_by_keys": {"shortName": variable},
                "logical_remote_url": remote,
            }
            if source == "gfs" and variable == "apcp":
                metadata["idx_selectors"] = {
                    "apcp": {"accumulation_type": "cumulative_since_cycle"}
                }
            entries.append(
                {
                    "remote_url": remote,
                    "local_key": f"mirror/raw/{segment}/{cycle_id}/{name}",
                    "variable": variable,
                    "forecast_hour": lead,
                    "expected_checksum": None,
                    "expected_size_bytes": None,
                    "metadata": metadata,
                }
            )
    (base / "manifest.json").write_text(
        json.dumps(
            {
                "source_id": f"mirror-{segment}",
                "cycle_time": CYCLE.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
                "manifest_uri": f"s3://nwm/raw/{segment}/{cycle_id}/manifest.json",
                "metadata": {
                    "first_forecast_hour": 0,
                    "last_forecast_hour": 3,
                    "forecast_hours": list(FORECAST_HOURS),
                    "requested_forecast_hours": list(FORECAST_HOURS),
                },
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "mutation", ("symlink", "fifo", "directory", "old", "import", "post_collect")
)
def test_controller_rejects_receipt_drift_without_done(tmp_path, monkeypatch, mutation):
    config, local, yd_root, scratch = _production_tree(tmp_path)
    external = tmp_path / "external-receipt"
    external.write_bytes(b"external")
    sibling = scratch / "work" / SOURCE / "sibling"
    sibling.mkdir(parents=True)
    (sibling / "sentinel").write_bytes(b"sibling")
    replacements = {
        "symlink": lambda path: path.symlink_to(external),
        "fifo": os.mkfifo,
        "directory": Path.mkdir,
    }

    def after_worker(work_dir):
        receipt = work_dir / RECEIPT_FILENAME
        if mutation == "old":
            old = receipt.with_suffix(".old")
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            payload["job_id"] = "old-job"
            old.write_text(json.dumps(payload), encoding="utf-8")
            old.replace(receipt)
            return
        receipt.unlink()
        replacements[mutation](receipt)

    if mutation == "import":
        importer = nwm.import_verified_checkpoint

        def mutate_then_import(*, tracker, record):
            record.path.write_bytes(record.path.read_bytes() + b"drift")
            return importer(tracker=tracker, record=record)

        monkeypatch.setattr(nwm, "import_verified_checkpoint", mutate_then_import)
    driver = ProductionAttemptDriver(grid_id=GRID_ID)
    if mutation == "post_collect":
        driver = _TamperAfterCollectDriver(grid_id=GRID_ID)
    executor = _SubprocessJobExecutor(
        after_worker if mutation in {*replacements, "old"} else None
    )
    with pytest.raises(RunError):
        _run_production(config, local, executor, driver)
    assert not list(yd_root.glob("**/DONE"))
    assert not list(scratch.glob("**/DONE"))
    assert external.read_bytes() == b"external"
    assert (sibling / "sentinel").read_bytes() == b"sibling"


def test_production_worker_through_controller_publish(tmp_path, monkeypatch):
    config, local, yd_root, scratch = _production_tree(tmp_path)
    report = _run_production(
        config,
        local,
        _SubprocessJobExecutor(),
        ProductionAttemptDriver(grid_id=GRID_ID),
    )
    done = yd_root / "output" / "2026010200" / SOURCE / "DONE"
    work = scratch / "work" / SOURCE / "2026010200"
    assert report.outcome is RunOutcome.SUCCEEDED
    assert done.is_file()
    assert not work.exists()
    dat_path = yd_root / "output" / "2026010200" / SOURCE / "yd.rivqdown.dat"
    payload = dat_path.read_bytes()
    expected_rows, expected_nc = 168, 1
    assert payload[: len(DEFAULT_HEADER_TEXT)] == DEFAULT_HEADER_TEXT.encode("ascii")
    assert payload[len(DEFAULT_HEADER_TEXT) : TEXT_HEADER_BYTES] == b"\x00" * (
        TEXT_HEADER_BYTES - len(DEFAULT_HEADER_TEXT)
    )
    _st, nc = struct.unpack("<dd", payload[TEXT_HEADER_BYTES:FIXED_HEADER_BYTES])
    assert nc == expected_nc
    table_end = FIXED_HEADER_BYTES + FLOAT64_BYTES * expected_nc
    stride = (expected_nc + 1) * FLOAT64_BYTES
    assert len(payload) == expected_v2_size(nc=expected_nc, rows=expected_rows)
    minutes = [
        struct.unpack(
            "<d",
            payload[
                table_end + row * stride : table_end + row * stride + FLOAT64_BYTES
            ],
        )[0]
        for row in range(expected_rows)
    ]
    assert minutes == [float(row * 60) for row in range(expected_rows)]


def test_named_manual_contract_with_real_converter_fails_closed(tmp_path, monkeypatch):
    claim, _attempt, completed = _run_worker(tmp_path, monkeypatch, fixture="named")
    assert completed.returncode != 0
    assert not (claim.work_dir / RECEIPT_FILENAME).exists()
    grid_path = (
        claim.work_dir / f"object-store/canonical/{SOURCE}/grid/{GRID_ID}/grid.json"
    )
    if grid_path.is_file():
        assert _canonical_cell_ids(claim.work_dir) == ["0"]
        assert "m2-synthetic-cell" not in grid_path.read_text(encoding="utf-8")


def test_worker_retains_unbound_extra_canonical_cell(tmp_path, monkeypatch):
    claim, attempt, completed = _run_worker(tmp_path, monkeypatch, extra_cell=True)
    assert completed.returncode == 0, completed.stderr
    assert _canonical_cell_ids(claim.work_dir) == ["0", "1"]
    products = ProductionAttemptDriver(grid_id=GRID_ID).collect(
        attempt=attempt, terminal_record=_record()
    )
    csv_path = products.run_directory.forcing_csv_paths[0]
    rows = csv_path.read_text(encoding="utf-8").splitlines()
    assert rows[1] == "Time_Day\tPrecip\tTemp\tRH\tWind\tRN"
    assert float(rows[2].split("\t")[2]) == pytest.approx(6.85)
