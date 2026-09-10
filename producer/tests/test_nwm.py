"""`yd_producer.nwm` interpreter wrapper and production worker tests."""

import json
import os
import shutil
import struct
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from cfg_ic_fixtures import build_cfg_ic
from cli_fixtures import (
    ALT_MAPPING_BUILDER_MODULE,
    MAPPING_BUILDER_MODULE,
    write_config,
    write_fake_interpreter,
    write_local,
)
from dat_fixtures import (
    DEFAULT_HEADER_TEXT,
    FIXED_HEADER_BYTES,
    FLOAT64_BYTES,
    TEXT_HEADER_BYTES,
    build_dat_bytes,
    expected_v2_size,
)

from yd_producer.config import ConfigError, load_config, load_local
from yd_producer.controller import AttemptRequest
from yd_producer.executor import JobRecord, JobState
from yd_producer.nwm import (
    RECEIPT_FILENAME,
    ProductionAttemptDriver,
    ProductionAttemptError,
    invoke_mapping_builder,
)
from yd_producer.prepare import calibrated_state_path


class RecordingRunner:
    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        return subprocess.CompletedProcess(command, 0)


class CountingRunner:
    def __init__(self):
        self.calls = 0

    def __call__(self, command, **kwargs):
        self.calls += 1
        return subprocess.run(command, check=False, **kwargs)


def _load(tmp_path, module=MAPPING_BUILDER_MODULE, **local_kwargs):
    config = load_config(write_config(tmp_path, module=module))
    local = load_local(write_local(tmp_path, **local_kwargs), config)
    return local, config


def test_missing_interpreter_raises_and_starts_no_process(tmp_path):
    local, config = _load(tmp_path, python=tmp_path.resolve() / "absent" / "python")
    runner = RecordingRunner()
    with pytest.raises(ConfigError) as excinfo:
        invoke_mapping_builder(local, config, ["--package-path", "x"], runner)
    assert excinfo.value.path == "nwm.python"
    assert "不存在" in str(excinfo.value)
    assert runner.calls == []


def test_directory_interpreter_raises_and_starts_no_process(tmp_path):
    directory = tmp_path.resolve() / "not-a-file"
    directory.mkdir()
    local, config = _load(tmp_path, python=directory)
    runner = RecordingRunner()
    with pytest.raises(ConfigError) as excinfo:
        invoke_mapping_builder(local, config, [], runner)
    assert excinfo.value.path == "nwm.python"
    assert "不是普通文件" in str(excinfo.value)
    assert runner.calls == []


def test_non_executable_interpreter_raises_and_starts_no_process(tmp_path):
    script = tmp_path.resolve() / "python-no-x"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o644)
    local, config = _load(tmp_path, python=script)
    runner = RecordingRunner()
    with pytest.raises(ConfigError) as excinfo:
        invoke_mapping_builder(local, config, [], runner)
    assert excinfo.value.path == "nwm.python"
    assert "不可执行" in str(excinfo.value)
    assert runner.calls == []


def _run_fake(
    tmp_path,
    checkout_name="checkout",
    exit_code=0,
    args=("--dry-run",),
    module=MAPPING_BUILDER_MODULE,
):
    checkout = tmp_path.resolve() / checkout_name
    checkout.mkdir()
    record = tmp_path.resolve() / f"record-{checkout_name}.json"
    script = write_fake_interpreter(
        tmp_path.resolve() / f"fake-python-{checkout_name}",
        record,
        exit_code=exit_code,
    )
    local, config = _load(
        tmp_path, module=module, checkout_root=checkout, python=script
    )
    runner = CountingRunner()
    completed = invoke_mapping_builder(local, config, list(args), runner)
    recorded = json.loads(record.read_text(encoding="utf-8"))
    return script, checkout, recorded, completed, runner


def test_fake_interpreter_receives_exact_command_and_context(tmp_path):
    script, checkout, recorded, completed, _ = _run_fake(
        tmp_path, args=("--package-path", "baseline")
    )
    assert completed.returncode == 0
    assert recorded["argv"][0].endswith(str(script))
    assert recorded["argv"][1:3] == ["-m", MAPPING_BUILDER_MODULE]
    assert recorded["argv"][3:] == ["--package-path", "baseline"]
    assert recorded["cwd"] == str(checkout)
    assert recorded["pythonpath"].split(os.pathsep)[0] == str(checkout)


def test_checkout_root_change_moves_cwd_and_pythonpath(tmp_path):
    _, first_checkout, first, _, _ = _run_fake(tmp_path, checkout_name="checkout-a")
    _, second_checkout, second, _, _ = _run_fake(tmp_path, checkout_name="checkout-b")
    assert first_checkout != second_checkout
    assert first["cwd"] == str(first_checkout)
    assert second["cwd"] == str(second_checkout)
    assert first["pythonpath"].split(os.pathsep)[0] == str(first_checkout)
    assert second["pythonpath"].split(os.pathsep)[0] == str(second_checkout)


def test_module_name_follows_config_value(tmp_path):
    assert MAPPING_BUILDER_MODULE != ALT_MAPPING_BUILDER_MODULE
    _, _, first, _, _ = _run_fake(tmp_path, checkout_name="module-a")
    _, _, second, _, _ = _run_fake(
        tmp_path, checkout_name="module-b", module=ALT_MAPPING_BUILDER_MODULE
    )
    assert first["argv"][1:3] == ["-m", MAPPING_BUILDER_MODULE]
    assert second["argv"][1:3] == ["-m", ALT_MAPPING_BUILDER_MODULE]


def test_command_contains_no_interpreter_fallback(tmp_path):
    script, _, recorded, _, _ = _run_fake(tmp_path)
    joined = " ".join(recorded["argv"])
    assert "uv" not in joined
    assert "--active" not in joined
    assert recorded["argv"][0] != sys.executable
    assert Path(recorded["argv"][0]).name == script.name


def test_nonzero_exit_reported_faithfully(tmp_path):
    _, _, _, completed, runner = _run_fake(tmp_path, exit_code=7)
    assert completed.returncode == 7
    assert runner.calls == 1


def test_pythonpath_prepends_checkout_without_dropping_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/inherited/path")
    _, checkout, recorded, _, _ = _run_fake(tmp_path)
    assert recorded["pythonpath"].split(os.pathsep) == [
        str(checkout),
        "/inherited/path",
    ]


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
    local, config = _load(tmp_path, checkout_root=checkout, python=link)
    completed = invoke_mapping_builder(local, config, [], CountingRunner())
    assert completed.returncode == 0
    recorded = json.loads(record.read_text(encoding="utf-8"))
    assert recorded["argv"][0] == str(link)
    assert recorded["argv"][0] != str(target)


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


def _handoff_payload(
    source: str, grid_id: str, *, fixture: str = "named", extra_cell: bool = False
) -> dict:
    if fixture == "named":
        cell_id, signature = "m2-synthetic-cell", GRID_SIGNATURE
    elif extra_cell:
        cell_id, signature = "0", TWO_CELL_GRID_SIGNATURE
    else:
        cell_id, signature = "0", CONVERTER_GRID_SIGNATURE
    return {
        "basin_id": "m2-synthetic-basin",
        "basin_version_id": "m2-synthetic-basin-v1",
        "direct_grid_forcing_contract": {
            "applicable_source_ids": [source],
            "binding_checksum": "sha256:" + BINDING_SHA256,
            "binding_uri": "models/m2-synthetic-model/direct-grid/binding.json",
            "forcing_mapping_mode": "direct_grid",
            "grid_id": grid_id,
            "grid_signature": signature,
            "model_input_package_id": "m2-synthetic-package",
            "sp_att_checksum": "sha256:" + SP_ATT_SHA256,
            "sp_att_path": "input/yd.sp.att",
            "station_bindings": [
                {
                    "forcing_filename": "m2-synthetic-station.csv",
                    "grid_cell_id": cell_id,
                    "grid_id": grid_id,
                    "latitude": 0.0,
                    "longitude": 0.0,
                    "shud_forcing_index": 1,
                    "station_id": "m2-synthetic-station",
                    "x": 0.0,
                    "y": 0.0,
                    "z": 0.0,
                }
            ],
        },
        "model_id": "m2-synthetic-model",
        "project_name": "yd",
        "river_network_version_id": "m2-synthetic-rivnet-v1",
        "schema_version": "yd.prepare.direct-grid-handoff.v1",
        "source_id": source,
        "sp_att_asset_name": "explicit-synthetic.sp.att",
    }


def _write_handoff(
    variant: Path, source: str, grid_id: str, *, fixture: str, extra_cell: bool = False
) -> None:
    payload = _handoff_payload(source, grid_id, fixture=fixture, extra_cell=extra_cell)
    (variant / "yd.direct-grid-handoff.json").write_bytes(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


def _write_shud(path: Path, *, dat: bytes, update: bytes) -> Path:
    dat_src = path.parent / "shud-dat.bin"
    update_src = path.parent / "shud-update.bin"
    dat_src.write_bytes(dat)
    update_src.write_bytes(update)
    path.write_text(
        "#!/bin/sh\n"
        "mkdir -p ../output\n"
        f"cp '{dat_src}' ../output/yd.rivqdown.dat\n"
        f"cp '{update_src}' yd.cfg.ic.update\n"
        "echo shud-ok\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _synthetic_shud(path: Path) -> Path:
    update = build_cfg_ic(mesh_count=1, river_count=1, minute="720.000000").payload
    return _write_shud(path, dat=build_dat_bytes(nc=1, rows=168), update=update)


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
    calibrated_state_path(variant_dir).write_bytes(VALID_CFG)
    (variant_dir / "yd.para").write_bytes(b"# m2 synthetic parameters\n")
    (variant_dir / "yd.binding").write_bytes(BINDING)
    (variant_dir / "explicit-synthetic.sp.att").write_bytes(SP_ATT)
    _write_handoff(variant_dir, source, grid_id, fixture=fixture, extra_cell=extra_cell)
    minute = round(CYCLE.timestamp() / 60)
    cycle_state = VALID_CFG.replace(b"1 6 0 0\n", f"1 6 0 {minute}\n".encode(), 1)
    state_path = source_root / "states" / source / "2026010200.cfg.ic"
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(cycle_state)
    staged = stage_work_inputs(
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
    return source_root, claim, variant_dir, state_path, staged, cycle_state


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
    def __init__(self) -> None:
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
            self._record = _record(
                job_id,
                name=self._spec.name,
                state=JobState.RUNNING,
                ended=None,
                resources=dict(self._spec.resources),
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
        self._record = _record(
            job_id, name=self._spec.name, resources=dict(self._spec.resources)
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
    source = kwargs.get("source", SOURCE)
    grid_id = kwargs.get("grid_id", GRID_ID)
    source_root, claim, variant_dir, state_path, _staged, _cycle_state = (
        _stage_synthetic(tmp_path, **kwargs)
    )
    shud = _synthetic_shud(tmp_path / (shud_name or f"shud-{source}"))
    request = _request(claim, variant_dir, state_path, shud, source=source)
    attempt = ProductionAttemptDriver(grid_id=grid_id).prepare(request=request)
    return source_root, claim, variant_dir, state_path, attempt


def _run_worker(tmp_path, monkeypatch, *, native=GFS_NATIVE, **kwargs):
    kwargs.setdefault("fixture", "converter")
    extra_cell = kwargs.get("extra_cell", False)
    source = kwargs.get("source", SOURCE)
    source_root, claim, _variant_dir, _state_path, attempt = _prepared_attempt(
        tmp_path, **kwargs
    )
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


def _production_tree(tmp_path: Path, *, source: str = SOURCE):
    from yd_producer.config import (
        CanonicalGridConfig,
        Config,
        CronLocal,
        CycleConfig,
        LocalConfig,
        NwmLocal,
        RawConfig,
        RawSourceConfig,
        SlurmSchema,
        VariantsConfig,
    )

    root = tmp_path.resolve()
    yd_root = root / "yd"
    scratch = root / "scratch"
    raw_root = root / "nwm" / "raw"
    variant = yd_root / "input" / "models" / ("yd_gfs" if source == "gfs" else "yd_ifs")
    states = yd_root / "states" / source
    variant.mkdir(parents=True)
    states.mkdir(parents=True)
    (yd_root / "output").mkdir()
    scratch.mkdir()
    (root / "run").mkdir()
    calibrated_state_path(variant).write_bytes(VALID_CFG)
    (variant / "yd.para").write_bytes(b"# m2 synthetic parameters\n")
    (variant / "yd.binding").write_bytes(BINDING)
    (variant / "explicit-synthetic.sp.att").write_bytes(SP_ATT)
    _write_handoff(variant, source, GRID_ID, fixture="converter")
    minute = round(CYCLE.timestamp() / 60)
    cycle_state = VALID_CFG.replace(b"1 6 0 0\n", f"1 6 0 {minute}\n".encode(), 1)
    (states / "2026010200.cfg.ic").write_bytes(cycle_state)
    native = GFS_NATIVE if source == "gfs" else IFS_NATIVE
    _plant_nwm_raw_root(raw_root, source=source, native=native)
    shud = _synthetic_shud(root / f"shud-{source}-prod")
    config = Config(
        forecast_days=7,
        output_interval_minutes=60,
        checkpoint_hours=(12,),
        reach_count=1,
        nwm_mapping_builder_module="workers.mapping_builder.cli",
        nwm_canonical_grid_id=CanonicalGridConfig(
            gfs=GRID_ID, ifs="m2-synthetic-ifs-grid"
        ),
        cycle=CycleConfig(hours=(0, 12)),
        variants=VariantsConfig(gfs="input/models/yd_gfs", ifs="input/models/yd_ifs"),
        raw=RawConfig(
            ifs=RawSourceConfig(
                lead_hours=FORECAST_HOURS,
                variables=tuple(IFS_NATIVE),
                bundles=("ifs.t{cycle_hour}z.f{lead}.bundle.grib2",),
                f000_special=False,
            ),
            gfs=RawSourceConfig(
                lead_hours=FORECAST_HOURS,
                variables=tuple(GFS_NATIVE),
                bundles=("gfs.t{cycle_hour}z.pgrb2.0p25.f{lead}.bundle.grib2",),
                f000_special=False,
            ),
        ),
        slurm=SlurmSchema(required_fields=("partition", "account")),
    )
    local = LocalConfig(
        yd_root=str(yd_root),
        scratch_root=str(scratch),
        shud_binary=str(shud),
        nwm=NwmLocal(
            raw_root=str(raw_root),
            checkout_root=str(root / "nwm" / "checkout"),
            python=str(sys.executable),
        ),
        slurm={"partition": "cpu", "account": "a"},
        cron=CronLocal(
            lock_path=str(root / "run" / "yd-producer.lock"), log_dir=str(root / "log")
        ),
    )
    return config, local, yd_root, scratch


def test_independent_worker_converts_gfs_raw_and_writes_receipt(tmp_path, monkeypatch):
    assert sha256(BINDING).hexdigest() == BINDING_SHA256
    assert sha256(SP_ATT).hexdigest() == SP_ATT_SHA256
    assert sha256(b'{"grid_points":[["m2-synthetic-cell",0.0,0.0]]}').hexdigest() == (
        GRID_SIGNATURE
    )
    assert sha256(b'{"grid_points":[["0",0.0,0.0]]}').hexdigest() == (
        CONVERTER_GRID_SIGNATURE
    )
    claim, attempt, completed = _run_worker(tmp_path, monkeypatch)
    assert completed.returncode == 0, completed.stderr
    assert _canonical_cell_ids(claim.work_dir) == ["0"]
    receipt = json.loads(
        (claim.work_dir / RECEIPT_FILENAME).read_text(encoding="utf-8")
    )
    assert receipt["job_id"] == "job-9"
    assert receipt["source"] == SOURCE
    assert receipt["identity"]["model_id"] == "m2-synthetic-model"
    assert "binding_checksum" in receipt and "sp_att_checksum" in receipt
    products = ProductionAttemptDriver(grid_id=GRID_ID).collect(
        attempt=attempt, terminal_record=_record()
    )
    assert products.job_id == "job-9"
    assert products.run_directory.path == claim.work_dir / "model"
    assert 12 in products.tracker.captured


def test_independent_worker_converts_ifs_raw_lowercase_grid(tmp_path, monkeypatch):
    claim, attempt, completed = _run_worker(
        tmp_path,
        monkeypatch,
        source="ifs",
        native=IFS_NATIVE,
        grid_id="m2-synthetic-gfs-grid",
    )
    assert completed.returncode == 0, completed.stderr
    assert _canonical_cell_ids(claim.work_dir, source="ifs") == ["0"]
    receipt = json.loads(
        (claim.work_dir / RECEIPT_FILENAME).read_text(encoding="utf-8")
    )
    assert receipt["source"] == "ifs"
    products = ProductionAttemptDriver(grid_id=GRID_ID).collect(
        attempt=attempt, terminal_record=_record()
    )
    assert 12 in products.tracker.captured


def test_worker_fails_closed_without_raw_manifest(tmp_path, monkeypatch):
    source_root, claim, _variant_dir, _state_path, attempt = _prepared_attempt(
        tmp_path, fixture="converter", shud_name="shud-missing"
    )
    shutil.rmtree(source_root)
    completed = subprocess.run(
        list(attempt.command),
        check=False,
        capture_output=True,
        text=True,
        env=_worker_env(),
    )
    assert completed.returncode != 0
    assert not (claim.work_dir / RECEIPT_FILENAME).exists()


@pytest.mark.parametrize(
    "mutation",
    [
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
    ],
)
def test_collect_rejects_tampered_receipt_from_success(tmp_path, monkeypatch, mutation):
    claim, attempt, completed = _run_worker(tmp_path, monkeypatch)
    assert completed.returncode == 0, completed.stderr
    path = claim.work_dir / RECEIPT_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "job":
        payload["job_id"] = "other"
    elif mutation == "source":
        payload["source"] = "ifs"
    elif mutation == "cycle":
        payload["cycle"] = "2026-01-03T00:00:00+00:00"
    elif mutation == "work":
        payload["work_dir"] = str(claim.work_dir.parent / "other")
    elif mutation == "model":
        payload["identity"]["model_id"] = "other-model"
    elif mutation == "binding":
        payload["binding_checksum"] = "sha256:" + "0" * 64
    elif mutation == "spatt":
        payload["sp_att_checksum"] = "sha256:" + "0" * 64
    elif mutation == "digest":
        payload["attempt_payload_digest"] = "sha256:" + "0" * 64
    elif mutation == "path":
        payload["checkpoint"]["path"] = str(claim.work_dir / "model" / "other.update")
    elif mutation == "checksum":
        payload["scratch_dat_checksum"] = "sha256:" + "0" * 64
    elif mutation == "oversize":
        path.write_bytes(b"{" + b"x" * 70000 + b"}")
        with pytest.raises(ProductionAttemptError):
            ProductionAttemptDriver(grid_id=GRID_ID).collect(
                attempt=attempt, terminal_record=_record()
            )
        return
    elif mutation == "partial":
        path.write_text("{", encoding="utf-8")
        with pytest.raises(ProductionAttemptError):
            ProductionAttemptDriver(grid_id=GRID_ID).collect(
                attempt=attempt, terminal_record=_record()
            )
        return
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProductionAttemptError):
        ProductionAttemptDriver(grid_id=GRID_ID).collect(
            attempt=attempt, terminal_record=_record()
        )


def test_prepare_rejects_nfs_path_in_handoff(tmp_path):
    _source_root, claim, variant_dir, state_path, _staged, _cycle_state = (
        _stage_synthetic(tmp_path, fixture="converter")
    )
    shud = tmp_path / "shud"
    shud.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shud.chmod(0o755)
    request = _request(claim, variant_dir, state_path, shud)
    attempt = ProductionAttemptDriver(grid_id=GRID_ID).prepare(request=request)
    text = (claim.work_dir / "yd.attempt-handoff.json").read_text(encoding="utf-8")
    assert str(variant_dir) not in text
    assert str(state_path) not in text
    assert attempt.command[1:3] == ("-m", "yd_producer.nwm")
    assert attempt.command[0] == sys.executable


def test_worker_refuses_database_url(tmp_path, monkeypatch):
    source_root, claim, _variant_dir, _state_path, attempt = _prepared_attempt(
        tmp_path, fixture="converter", shud_name="shud-db"
    )
    shutil.rmtree(source_root)
    completed = subprocess.run(
        list(attempt.command),
        check=False,
        capture_output=True,
        text=True,
        env=_worker_env(extra={"DATABASE_URL": "postgresql://x"}),
    )
    assert completed.returncode != 0
    assert not (claim.work_dir / RECEIPT_FILENAME).exists()


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
    variables = tuple(native)
    entries = []
    for lead in FORECAST_HOURS:
        name = pattern.replace("{cycle_hour}", cycle_hour).replace(
            "{lead}", f"{lead:03d}"
        )
        (base / name).write_bytes(
            _encode_raw_bytes(native, lead, source=source, variables=variables)
        )
        cycle_iso = CYCLE.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        valid_iso = (CYCLE + timedelta(hours=lead)).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        remote = f"https://mirror.invalid/{segment}/{cycle_id}/{name}"
        for variable in variables:
            metadata = {
                "cycle_time": cycle_iso,
                "valid_time": valid_iso,
                "bundle": {"layout": "per_forecast_hour", "variables": list(variables)},
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


def test_tamper_checkpoint_after_collect_import_zero_done(tmp_path, monkeypatch):
    from yd_producer.controller import RunError, run_once

    config, local, yd_root, scratch = _production_tree(tmp_path)
    with pytest.raises(RunError):
        run_once(
            config=config,
            local=local,
            source=SOURCE,
            executor=_SubprocessJobExecutor(),
            driver=_TamperAfterCollectDriver(grid_id=GRID_ID),
            poll_wait=lambda: None,
        )
    assert not list(yd_root.glob("**/DONE"))
    assert not list(scratch.glob("**/DONE"))


def test_production_worker_through_controller_publish(tmp_path, monkeypatch):
    from yd_producer.controller import RunOutcome, run_once

    config, local, yd_root, scratch = _production_tree(tmp_path)
    assert list(scratch.rglob("catalog.json")) == []
    report = run_once(
        config=config,
        local=local,
        source=SOURCE,
        executor=_SubprocessJobExecutor(),
        driver=ProductionAttemptDriver(grid_id=GRID_ID),
        poll_wait=lambda: None,
    )
    done = yd_root / "output" / "2026010200" / SOURCE / "DONE"
    work = scratch / "work" / SOURCE / "2026010200"
    assert report.outcome is RunOutcome.SUCCEEDED
    assert report.done_path == done
    assert done.is_file()
    assert not work.exists()
    dat_path = yd_root / "output" / "2026010200" / SOURCE / "yd.rivqdown.dat"
    assert dat_path.is_file()
    payload = dat_path.read_bytes()
    expected_rows = 168
    expected_nc = 1
    last_minute = float((expected_rows - 1) * 60)
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
    assert minutes[0] == 0.0 and minutes[-1] == last_minute == 10020.0


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
    assert 12 in products.tracker.captured
