"""NWM mapping-builder 薄外壳与生产 AttemptDriver / 私有 worker。

`prepare` 是全仓唯一主动进入 NWM 活动环境的代码路径。`check_interpreter` 与
`invoke_mapping_builder` 保持既有指定解释器 / cwd / PYTHONPATH 规则。日常 worker
只以 yd 自己的解释器和精确 argv 启动本模块，不借用 NWM 环境。
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yd_producer.assemble import RunDirectory, WorkIdentity, stage_work_registry
from yd_producer.canonical.converter import (
    CanonicalConversionError,
    CanonicalConverter,
    CanonicalConverterConfig,
    IFSCanonicalConverter,
    IFSCanonicalConverterConfig,
)
from yd_producer.config import Config, ConfigError, LocalConfig
from yd_producer.controller import AttemptProducts, AttemptRequest, PreparedAttempt
from yd_producer.executor import JobRecord, JobState
from yd_producer.forcing.bounded_json import BoundedJSONError, load_bounded_json
from yd_producer.forcing.file_store import FileForcingRepository
from yd_producer.forcing.producer import ForcingProducer, ForcingProducerConfig
from yd_producer.prepare import calibrated_state_path
from yd_producer.prepare_handoff import (
    MAX_PREPARED_VARIANT_ASSET_BYTES,
    MAX_PREPARED_VARIANT_MANIFEST_BYTES,
    PreparedVariantHandoff,
    load_prepared_variant_handoff,
)
from yd_producer.rawcopy import MANIFEST_FILENAME
from yd_producer.staged_inputs import assemble_staged, load_staged_work_inputs
from yd_producer.state import MAX_STATE_IC_BYTES
from yd_producer.store.object_store import (
    MAX_OBJECT_MANIFEST_BYTES,
    LocalObjectStore,
    sha256_bytes,
)
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    directory_identity_no_follow,
    open_file_no_follow,
    read_bytes_limited_no_follow,
    stat_no_follow,
    write_bytes_no_follow_exclusive,
)
from yd_producer.tracker import (
    CapturedCheckpoint,
    CheckpointTracker,
    TrackerError,
    ensure_twelve_hour_checkpoint,
    import_verified_checkpoint,
)

__all__ = [
    "ATTEMPT_HANDOFF_FILENAME",
    "ATTEMPT_HANDOFF_SCHEMA",
    "POLL_INTERVAL_SECONDS",
    "RECEIPT_FILENAME",
    "RECEIPT_SCHEMA",
    "WORKER_ENTRY",
    "ProductionAttemptDriver",
    "ProductionAttemptError",
    "check_interpreter",
    "invoke_mapping_builder",
]

_INTERPRETER_FIELD = "nwm.python"

POLL_INTERVAL_SECONDS = 10
WORKER_ENTRY = "yd_producer.nwm"
ATTEMPT_HANDOFF_FILENAME = "yd.attempt-handoff.json"
ATTEMPT_HANDOFF_SCHEMA = "yd.run.attempt-handoff.v1"
RECEIPT_FILENAME = "yd.attempt-receipt.json"
RECEIPT_SCHEMA = "yd.run.attempt-receipt.v1"
_HANDOFF_KEYS = frozenset(
    {
        "schema_version",
        "source",
        "cycle",
        "work_dir",
        "project_name",
        "grid_id",
        "manifest_checksum",
        "max_manifest_bytes",
        "max_asset_bytes",
        "max_state_bytes",
        "scratch_dat",
        "job_log",
        "shud_binary",
        "checkpoint_hours",
        "forecast_days",
        "output_interval_minutes",
        "reach_count",
        "raw_manifest_name",
        "attempt_payload_digest",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "source",
        "cycle",
        "work_dir",
        "job_id",
        "identity",
        "run_directory",
        "scratch_dat",
        "scratch_dat_checksum",
        "merged_log",
        "merged_log_checksum",
        "checkpoint",
        "binding_checksum",
        "sp_att_checksum",
        "attempt_payload_digest",
    }
)
_IDENTITY_KEYS = (
    "source_id",
    "cycle_time",
    "project_name",
    "model_id",
    "basin_id",
    "basin_version_id",
    "river_network_version_id",
)
_RUN_DIR_KEYS = (
    "path",
    "project_name",
    "state_path",
    "parameter_path",
    "forcing_index_path",
    "forcing_csv_paths",
)
_CHECKPOINT_KEYS = (
    "lead_hours",
    "relative_minute",
    "path",
    "source_name",
    "checksum",
)
_HANDOFF_STR_KEYS = (
    "schema_version",
    "source",
    "cycle",
    "work_dir",
    "project_name",
    "grid_id",
    "manifest_checksum",
    "scratch_dat",
    "job_log",
    "shud_binary",
    "raw_manifest_name",
    "attempt_payload_digest",
)
_HANDOFF_INT_KEYS = (
    "max_manifest_bytes",
    "max_asset_bytes",
    "max_state_bytes",
    "forecast_days",
    "output_interval_minutes",
    "reach_count",
)
_RECEIPT_MAX_BYTES = 65536
_HANDOFF_MAX_BYTES = 65536
_SHA256_PREFIX = "sha256:"
_NFS_ENV_KEYS = ("YD_ROOT", "NWM_RAW_ROOT", "NWM_CHECKOUT_ROOT")
_LOG_CHUNK = 65536
_CAPTURE_WAIT = 0.05


class ProductionAttemptError(RuntimeError):
    """Login-side prepare/collect or worker receipt failure."""


def check_interpreter(local: LocalConfig) -> str:
    """校验 NWM 解释器路径可用，返回 `local.toml` 里配置的**原样路径**。"""
    configured = local.nwm.python
    candidate = Path(configured)
    if not candidate.exists():
        raise ConfigError(
            f"NWM 解释器路径不存在：{configured}；"
            "yd 不安装、不升级、不修复 NWM .venv（agent-ops §7.2），"
            "不回退到任何其它解释器",
            _INTERPRETER_FIELD,
        )
    if not candidate.is_file():
        raise ConfigError(
            f"NWM 解释器路径不是普通文件：{configured}",
            _INTERPRETER_FIELD,
        )
    if not os.access(candidate, os.X_OK):
        raise ConfigError(
            f"NWM 解释器不可执行：{configured}",
            _INTERPRETER_FIELD,
        )
    return configured


def invoke_mapping_builder(
    local: LocalConfig,
    config: Config,
    args: Sequence[str] = (),
    runner: Callable[..., Any] = subprocess.run,
) -> subprocess.CompletedProcess[Any]:
    """以 NWM 解释器调用 `config.nwm_mapping_builder_module`。"""
    interpreter = check_interpreter(local)
    checkout_root = local.nwm.checkout_root
    env = dict(os.environ)
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        checkout_root if not inherited else checkout_root + os.pathsep + inherited
    )
    command = [interpreter, "-m", config.nwm_mapping_builder_module, *args]
    return runner(command, cwd=checkout_root, env=env)


class ProductionAttemptDriver:
    """Production AttemptDriver: login prepare + receipt collect, no login SHUD."""

    def __init__(self, *, grid_id: str | None = None) -> None:
        self._grid_id = grid_id

    def prepare(self, *, request: AttemptRequest) -> PreparedAttempt:
        _pin_work_root(request.work_dir)
        project_name = calibrated_state_path(request.variant_dir).name.removesuffix(
            ".cfg.ic"
        )
        if not project_name:
            raise ProductionAttemptError("calibrated state filename has empty project")
        grid_id = self._grid_id
        if not grid_id:
            raise ProductionAttemptError(
                "production driver requires configured grid_id"
            )
        source = load_prepared_variant_handoff(
            variant_root=request.variant_dir,
            source_id=request.source,
            project_name=project_name,
            grid_id=grid_id,
            max_manifest_bytes=MAX_PREPARED_VARIANT_MANIFEST_BYTES,
            max_asset_bytes=MAX_PREPARED_VARIANT_ASSET_BYTES,
        )
        staged = load_staged_work_inputs(
            work_dir=request.work_dir,
            source=request.source,
            cycle=request.cycle,
            project_name=project_name,
            grid_id=grid_id,
            max_manifest_bytes=MAX_PREPARED_VARIANT_MANIFEST_BYTES,
            max_asset_bytes=MAX_PREPARED_VARIANT_ASSET_BYTES,
            max_state_bytes=MAX_STATE_IC_BYTES,
        )
        if staged.prepared != source:
            raise ProductionAttemptError(
                "staged #171 snapshot differs from login-node source"
            )
        identity = _identity_from_prepared(
            source=request.source, cycle=request.cycle, prepared=source
        )
        scratch_dat = request.work_dir / "output" / "yd.rivqdown.dat"
        job_log = request.work_dir / "job.log"
        command = (
            sys.executable,
            "-m",
            WORKER_ENTRY,
            "--work-dir",
            str(request.work_dir),
        )
        payload = {
            "schema_version": ATTEMPT_HANDOFF_SCHEMA,
            "source": request.source,
            "cycle": request.cycle.isoformat(),
            "work_dir": str(request.work_dir),
            "project_name": project_name,
            "grid_id": grid_id,
            "manifest_checksum": staged.manifest_checksum,
            "max_manifest_bytes": MAX_PREPARED_VARIANT_MANIFEST_BYTES,
            "max_asset_bytes": MAX_PREPARED_VARIANT_ASSET_BYTES,
            "max_state_bytes": MAX_STATE_IC_BYTES,
            "scratch_dat": str(scratch_dat),
            "job_log": str(job_log),
            "shud_binary": request.shud_binary,
            "checkpoint_hours": list(request.checkpoint_hours),
            "forecast_days": request.forecast_days,
            "output_interval_minutes": request.output_interval_minutes,
            "reach_count": request.reach_count,
            "raw_manifest_name": MANIFEST_FILENAME,
            "attempt_payload_digest": _SHA256_PREFIX
            + sha256_bytes(_canonical_json({"command": list(command)})),
        }
        encoded = _canonical_json(payload)
        _reject_nfs_leak(encoded, request)
        write_bytes_no_follow_exclusive(
            request.work_dir / ATTEMPT_HANDOFF_FILENAME,
            encoded,
            containment_root=request.work_dir,
        )
        return PreparedAttempt(
            identity=identity, command=command, scratch_dat=scratch_dat
        )

    def collect(
        self, *, attempt: PreparedAttempt, terminal_record: JobRecord
    ) -> AttemptProducts:
        work_dir = _work_dir_from_attempt(attempt)
        _pin_work_root(work_dir)
        if terminal_record.state is not JobState.SUCCEEDED:
            raise ProductionAttemptError("collect requires a SUCCEEDED terminal record")
        receipt = _load_receipt(work_dir)
        _verify_receipt_envelope(receipt, attempt, terminal_record, work_dir)
        run_directory = _run_directory_from_receipt(receipt, attempt.identity)
        record = _checkpoint_from_receipt(receipt["checkpoint"])
        tracker = CheckpointTracker(
            run_dir=run_directory.path,
            project_name=run_directory.project_name,
            checkpoint_hours=(12,),
        )
        import_verified_checkpoint(tracker=tracker, record=record)
        return AttemptProducts(
            job_id=terminal_record.job_id,
            run_directory=run_directory,
            tracker=tracker,
            scratch_dat=Path(receipt["scratch_dat"]),
            merged_log=Path(receipt["merged_log"]),
        )


def _identity_from_prepared(
    *, source: str, cycle: datetime, prepared: PreparedVariantHandoff
) -> WorkIdentity:
    return WorkIdentity(
        source_id=source,
        cycle_time=cycle,
        project_name=prepared.project_name,
        model_id=prepared.model_id,
        basin_id=prepared.basin_id,
        basin_version_id=prepared.basin_version_id,
        river_network_version_id=prepared.river_network_version_id,
    )


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_nfs_leak(encoded: bytes, request: AttemptRequest) -> None:
    text = encoded.decode("utf-8")
    forbidden = (
        str(request.variant_dir),
        str(request.state_path),
        str(Path(request.variant_dir).parent),
    )
    for item in forbidden:
        if item and item in text:
            raise ProductionAttemptError("attempt handoff must not contain NFS paths")
    if "work_identity" in text:
        raise ProductionAttemptError("attempt handoff must not serialize work_identity")


def _work_dir_from_attempt(attempt: PreparedAttempt) -> Path:
    return attempt.scratch_dat.parent.parent


def _pin_work_root(work_dir: Path) -> tuple[int, int]:
    try:
        return directory_identity_no_follow(work_dir)
    except (OSError, SafeFilesystemError) as error:
        raise ProductionAttemptError(
            f"work_dir identity unreadable: {work_dir}"
        ) from error


def _require_same_root(work_dir: Path, expected: tuple[int, int]) -> None:
    current = _pin_work_root(work_dir)
    if current != expected:
        raise ProductionAttemptError("work_dir identity drifted during this process")


def _load_json_object(
    path: Path,
    work_dir: Path,
    *,
    max_bytes: int,
    label: str,
    keys: frozenset[str] | None = None,
    schema: str | None = None,
) -> dict[str, Any]:
    try:
        info = stat_no_follow(path, containment_root=work_dir)
    except (OSError, SafeFilesystemError) as error:
        raise ProductionAttemptError(
            f"{label} is not a regular file: {error}"
        ) from error
    if not stat.S_ISREG(info.st_mode):
        raise ProductionAttemptError(f"{label} is not a regular file: {path}")
    if stat.S_ISFIFO(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ProductionAttemptError(f"{label} must not be a FIFO or symlink: {path}")
    if info.st_size > max_bytes:
        raise ProductionAttemptError(f"{label} exceeds bounded size")
    content = read_bytes_limited_no_follow(
        path, max_bytes=max_bytes, containment_root=work_dir
    )
    if len(content) > max_bytes:
        raise ProductionAttemptError(f"{label} exceeds bounded size")
    try:
        payload = load_bounded_json(content, max_bytes=max_bytes)
    except BoundedJSONError as error:
        raise ProductionAttemptError(f"{label} JSON is invalid: {error}") from error
    if not isinstance(payload, dict):
        raise ProductionAttemptError(f"{label} is not a JSON object")
    if keys is not None and set(payload) != keys:
        raise ProductionAttemptError(f"{label} envelope keys are not exact")
    if schema is not None and payload["schema_version"] != schema:
        raise ProductionAttemptError(f"{label} schema_version is not current")
    return payload


def _load_receipt(work_dir: Path) -> dict[str, Any]:
    return _load_json_object(
        work_dir / RECEIPT_FILENAME,
        work_dir,
        max_bytes=_RECEIPT_MAX_BYTES,
        label="receipt",
        keys=_RECEIPT_KEYS,
        schema=RECEIPT_SCHEMA,
    )


def _verify_receipt_envelope(
    receipt: Mapping[str, Any],
    attempt: PreparedAttempt,
    terminal_record: JobRecord,
    work_dir: Path,
) -> None:
    identity = attempt.identity
    if receipt["source"] != identity.source_id:
        raise ProductionAttemptError("receipt source does not match attempt")
    if datetime.fromisoformat(str(receipt["cycle"])) != identity.cycle_time:
        raise ProductionAttemptError("receipt cycle does not match attempt")
    if Path(receipt["work_dir"]) != work_dir:
        raise ProductionAttemptError("receipt work_dir does not match attempt")
    if receipt["job_id"] != terminal_record.job_id:
        raise ProductionAttemptError("receipt job_id does not match terminal record")
    declared = receipt["identity"]
    if not isinstance(declared, dict) or set(declared) != set(_IDENTITY_KEYS):
        raise ProductionAttemptError("receipt identity keys are not exact")
    expected = {
        "source_id": identity.source_id,
        "cycle_time": identity.cycle_time.isoformat(),
        "project_name": identity.project_name,
        "model_id": identity.model_id,
        "basin_id": identity.basin_id,
        "basin_version_id": identity.basin_version_id,
        "river_network_version_id": identity.river_network_version_id,
    }
    if declared != expected:
        raise ProductionAttemptError("receipt WorkIdentity does not match attempt")
    digest = _SHA256_PREFIX + sha256_bytes(
        _canonical_json({"command": list(attempt.command)})
    )
    if receipt["attempt_payload_digest"] != digest:
        raise ProductionAttemptError("receipt attempt payload digest does not match")
    for label, value, expected_path, checksum_key in (
        (
            "scratch_dat",
            receipt["scratch_dat"],
            attempt.scratch_dat,
            "scratch_dat_checksum",
        ),
        (
            "merged_log",
            receipt["merged_log"],
            work_dir / "job.log",
            "merged_log_checksum",
        ),
    ):
        path = Path(value)
        if path != expected_path:
            raise ProductionAttemptError(f"receipt {label} is not the declared path")
        _require_regular(path, work_dir)
        actual = _stream_checksum(path, work_dir)
        if actual != receipt[checksum_key]:
            raise ProductionAttemptError(f"receipt {label} checksum does not match")
    _verify_run_directory_member(receipt["run_directory"], work_dir, identity)
    _verify_checkpoint_member(receipt["checkpoint"], work_dir, identity)
    _verify_asset_checksums(receipt, work_dir)


def _verify_asset_checksums(receipt: Mapping[str, Any], work_dir: Path) -> None:
    staged = load_staged_work_inputs(
        work_dir=work_dir,
        source=str(receipt["source"]),
        cycle=datetime.fromisoformat(str(receipt["cycle"])),
        project_name=str(receipt["identity"]["project_name"]),
        grid_id=_grid_id_from_work(work_dir),
        max_manifest_bytes=MAX_PREPARED_VARIANT_MANIFEST_BYTES,
        max_asset_bytes=MAX_PREPARED_VARIANT_ASSET_BYTES,
        max_state_bytes=MAX_STATE_IC_BYTES,
    )
    prepared = staged.prepared
    binding = _SHA256_PREFIX + sha256_bytes(prepared.binding_content)
    sp_att = _SHA256_PREFIX + sha256_bytes(prepared.sp_att_content)
    if receipt["binding_checksum"] != binding:
        raise ProductionAttemptError("receipt binding checksum does not match staged")
    if receipt["sp_att_checksum"] != sp_att:
        raise ProductionAttemptError("receipt sp.att checksum does not match staged")


def _grid_id_from_work(work_dir: Path) -> str:
    handoff = _load_handoff(work_dir, _pin_work_root(work_dir))
    return str(handoff["grid_id"])


def _require_regular(path: Path, work_dir: Path) -> os.stat_result:
    if not path.is_absolute() or not path.is_relative_to(work_dir):
        raise ProductionAttemptError(f"path escapes claimed work: {path}")
    try:
        info = stat_no_follow(path, containment_root=work_dir)
    except (OSError, SafeFilesystemError) as error:
        raise ProductionAttemptError(f"path is not a regular file: {path}") from error
    if not stat.S_ISREG(info.st_mode):
        raise ProductionAttemptError(f"path is not a regular file: {path}")
    if stat.S_ISFIFO(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ProductionAttemptError(f"path must not be a FIFO or symlink: {path}")
    return info


def _stream_checksum(path: Path, work_dir: Path) -> str:
    digest = hashlib.sha256()
    fd = open_file_no_follow(path, containment_root=work_dir)
    try:
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(fd)
    return _SHA256_PREFIX + digest.hexdigest()


def _verify_run_directory_member(
    payload: object, work_dir: Path, identity: WorkIdentity
) -> None:
    if not isinstance(payload, dict) or set(payload) != set(_RUN_DIR_KEYS):
        raise ProductionAttemptError("receipt RunDirectory keys are not exact")
    path = Path(payload["path"])
    if path != work_dir / "model":
        raise ProductionAttemptError("receipt RunDirectory.path is not work/model")
    if payload["project_name"] != identity.project_name:
        raise ProductionAttemptError("receipt RunDirectory.project_name mismatches")
    expected = {
        "state_path": path / f"{identity.project_name}.cfg.ic",
        "parameter_path": path / f"{identity.project_name}.para",
        "forcing_index_path": path / f"{identity.project_name}.tsd.forc",
    }
    for name, expected_path in expected.items():
        actual = Path(payload[name])
        if actual != expected_path:
            raise ProductionAttemptError(f"receipt RunDirectory.{name} is not expected")
        _require_regular(actual, work_dir)
    csvs = payload["forcing_csv_paths"]
    if not isinstance(csvs, list) or not csvs:
        raise ProductionAttemptError("receipt forcing CSV list is empty")
    for item in csvs:
        csv_path = Path(item)
        if csv_path.parent != path:
            raise ProductionAttemptError("receipt forcing CSV escapes model dir")
        _require_regular(csv_path, work_dir)


def _verify_checkpoint_member(
    payload: object, work_dir: Path, identity: WorkIdentity
) -> None:
    if not isinstance(payload, dict) or set(payload) != set(_CHECKPOINT_KEYS):
        raise ProductionAttemptError("receipt checkpoint keys are not exact")
    if payload["lead_hours"] != 12 or payload["relative_minute"] != 720.0:
        raise ProductionAttemptError("receipt checkpoint is not T+12")
    path = Path(payload["path"])
    expected = (
        work_dir
        / "model"
        / "state_checkpoints"
        / f"{identity.project_name}.f012.cfg.ic.update"
    )
    if path != expected:
        raise ProductionAttemptError("receipt checkpoint path is not the declared leaf")
    info = _require_regular(path, work_dir)
    if info.st_size > MAX_STATE_IC_BYTES:
        raise ProductionAttemptError("checkpoint exceeds state size bound")
    content = read_bytes_limited_no_follow(
        path, max_bytes=MAX_STATE_IC_BYTES, containment_root=work_dir
    )
    if len(content) > MAX_STATE_IC_BYTES:
        raise ProductionAttemptError("checkpoint exceeds state size bound")
    actual = _SHA256_PREFIX + sha256_bytes(content)
    declared = str(payload["checksum"])
    if declared.removeprefix(_SHA256_PREFIX) != actual.removeprefix(_SHA256_PREFIX):
        raise ProductionAttemptError("checkpoint checksum does not match current bytes")


def _run_directory_from_receipt(
    receipt: Mapping[str, Any], identity: WorkIdentity
) -> RunDirectory:
    payload = receipt["run_directory"]
    return RunDirectory(
        identity=identity,
        path=Path(payload["path"]),
        project_name=payload["project_name"],
        state_path=Path(payload["state_path"]),
        parameter_path=Path(payload["parameter_path"]),
        forcing_index_path=Path(payload["forcing_index_path"]),
        forcing_csv_paths=tuple(Path(item) for item in payload["forcing_csv_paths"]),
    )


def _checkpoint_from_receipt(payload: Mapping[str, Any]) -> CapturedCheckpoint:
    checksum = str(payload["checksum"])
    if checksum.startswith(_SHA256_PREFIX):
        checksum = checksum.removeprefix(_SHA256_PREFIX)
    return CapturedCheckpoint(
        lead_hours=int(payload["lead_hours"]),
        relative_minute=float(payload["relative_minute"]),
        path=Path(payload["path"]),
        source_name=str(payload["source_name"]),
        checksum=checksum,
    )


def _load_handoff(work_dir: Path, root_id: tuple[int, int]) -> dict[str, Any]:
    _require_same_root(work_dir, root_id)
    payload = _load_json_object(
        work_dir / ATTEMPT_HANDOFF_FILENAME,
        work_dir,
        max_bytes=_HANDOFF_MAX_BYTES,
        label="attempt handoff",
        keys=_HANDOFF_KEYS,
        schema=ATTEMPT_HANDOFF_SCHEMA,
    )
    if Path(payload["work_dir"]) != work_dir:
        raise ProductionAttemptError("attempt handoff work_dir is not this work")
    for key in _HANDOFF_STR_KEYS:
        if not isinstance(payload[key], str) or not payload[key]:
            raise ProductionAttemptError(
                f"attempt handoff {key} must be a nonempty str"
            )
    for key in _HANDOFF_INT_KEYS:
        if type(payload[key]) is not int or payload[key] <= 0:
            raise ProductionAttemptError(
                f"attempt handoff {key} must be a positive int"
            )
    if payload["checkpoint_hours"] != [12]:
        raise ProductionAttemptError("attempt handoff checkpoint_hours must be [12]")
    for name in ("scratch_dat", "job_log", "shud_binary"):
        path = Path(payload[name])
        if not path.is_absolute():
            raise ProductionAttemptError(f"attempt handoff {name} must be absolute")
        if name != "shud_binary" and not path.is_relative_to(work_dir):
            raise ProductionAttemptError(f"attempt handoff {name} escapes claimed work")
    if (
        ".." in Path(payload["scratch_dat"]).parts
        or ".." in Path(payload["job_log"]).parts
    ):
        raise ProductionAttemptError("attempt handoff path contains traversal")
    return payload


def _worker_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in (*_NFS_ENV_KEYS, "DATABASE_URL"):
        if key in env:
            raise ProductionAttemptError(f"worker env must not leak {key}")
    return env


def _build_converter(staged, store: LocalObjectStore) -> CanonicalConverter:
    source = str(staged.source)
    grid_id = str(staged.grid_id)
    kwargs = {
        "workspace_root": staged.work_dir,
        "object_store_root": Path(store.root),
        "object_store_prefix": "",
        "grid_id": grid_id,
        "grid_definition_uri": f"canonical/{source}/grid/{grid_id}/grid.json",
    }
    if source == "ifs":
        return IFSCanonicalConverter(
            config=IFSCanonicalConverterConfig(**kwargs), object_store=store
        )
    return CanonicalConverter(
        config=CanonicalConverterConfig(source_id=source, **kwargs), object_store=store
    )


def _convert_canonical(staged, store: LocalObjectStore) -> None:
    manifest = _load_json_object(
        Path(store.root) / MANIFEST_FILENAME,
        Path(store.root),
        max_bytes=MAX_OBJECT_MANIFEST_BYTES,
        label="raw manifest",
    )
    try:
        result = _build_converter(staged, store).convert_manifest(manifest)
    except CanonicalConversionError as error:
        raise ProductionAttemptError(f"canonical conversion failed: {error}") from error
    if result.status != "canonical_ready":
        raise ProductionAttemptError(
            f"canonical conversion did not succeed: {result.status}"
        )


def _shud_argv(binary: str, project_name: str) -> tuple[str, ...]:
    return (binary, project_name)


def _append_job_log(log_path: Path, work_dir: Path, data: bytes) -> None:
    if not data:
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(str(log_path.parent), flags)
    try:
        fd = os.open(
            log_path.name,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
            dir_fd=parent_fd,
        )
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def _run_shud_live(
    *,
    argv: Sequence[str],
    cwd: Path,
    work_dir: Path,
    log_path: Path,
    tracker: CheckpointTracker,
    env: Mapping[str, str],
) -> int:
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=dict(env),
    )
    stream = process.stdout
    try:
        while True:
            tracker.capture_available()
            if stream is None:
                break
            ready, _, _ = select.select([stream], (), (), _CAPTURE_WAIT)
            if ready:
                chunk = os.read(stream.fileno(), _LOG_CHUNK)
                if chunk:
                    _append_job_log(log_path, work_dir, chunk)
                else:
                    break
            if process.poll() is not None:
                leftover = (
                    os.read(stream.fileno(), _LOG_CHUNK) if stream is not None else b""
                )
                if leftover:
                    _append_job_log(log_path, work_dir, leftover)
                break
        process.wait()
        tracker.capture_available()
        return int(process.returncode or 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def _write_receipt(
    *,
    work_dir: Path,
    job_id: str,
    identity: WorkIdentity,
    run_directory: RunDirectory,
    scratch_dat: Path,
    merged_log: Path,
    checkpoint: CapturedCheckpoint,
    prepared: PreparedVariantHandoff,
    attempt_payload_digest: str,
    root_id: tuple[int, int],
) -> None:
    _require_same_root(work_dir, root_id)
    payload = {
        "schema_version": RECEIPT_SCHEMA,
        "source": identity.source_id,
        "cycle": identity.cycle_time.isoformat(),
        "work_dir": str(work_dir),
        "job_id": job_id,
        "identity": {
            "source_id": identity.source_id,
            "cycle_time": identity.cycle_time.isoformat(),
            "project_name": identity.project_name,
            "model_id": identity.model_id,
            "basin_id": identity.basin_id,
            "basin_version_id": identity.basin_version_id,
            "river_network_version_id": identity.river_network_version_id,
        },
        "run_directory": {
            "path": str(run_directory.path),
            "project_name": run_directory.project_name,
            "state_path": str(run_directory.state_path),
            "parameter_path": str(run_directory.parameter_path),
            "forcing_index_path": str(run_directory.forcing_index_path),
            "forcing_csv_paths": [
                str(path) for path in run_directory.forcing_csv_paths
            ],
        },
        "scratch_dat": str(scratch_dat),
        "scratch_dat_checksum": _stream_checksum(scratch_dat, work_dir),
        "merged_log": str(merged_log),
        "merged_log_checksum": _stream_checksum(merged_log, work_dir),
        "checkpoint": {
            "lead_hours": checkpoint.lead_hours,
            "relative_minute": checkpoint.relative_minute,
            "path": str(checkpoint.path),
            "source_name": checkpoint.source_name,
            "checksum": (
                checkpoint.checksum
                if checkpoint.checksum.startswith(_SHA256_PREFIX)
                else _SHA256_PREFIX + checkpoint.checksum
            ),
        },
        "binding_checksum": _SHA256_PREFIX + sha256_bytes(prepared.binding_content),
        "sp_att_checksum": _SHA256_PREFIX + sha256_bytes(prepared.sp_att_content),
        "attempt_payload_digest": attempt_payload_digest,
    }
    encoded = _canonical_json(payload)
    if "work_identity" in encoded.decode("utf-8"):
        raise ProductionAttemptError("receipt must not serialize work_identity")
    if set(payload) != _RECEIPT_KEYS:
        raise ProductionAttemptError("receipt envelope keys are not exact")
    reread = load_bounded_json(encoded, max_bytes=_RECEIPT_MAX_BYTES)
    if reread != payload:
        raise ProductionAttemptError("receipt snapshot is not canonical")
    write_bytes_no_follow_exclusive(
        work_dir / RECEIPT_FILENAME,
        encoded,
        containment_root=work_dir,
    )
    _require_same_root(work_dir, root_id)


def run_private_worker(*, work_dir: Path) -> None:
    """Independent Slurm-job worker: staged reload → compute → atomic receipt last."""
    env = _worker_env()
    work = Path(work_dir)
    if not work.is_absolute():
        raise ProductionAttemptError("worker work_dir must be absolute")
    root_id = _pin_work_root(work)
    handoff = _load_handoff(work, root_id)
    cycle = datetime.fromisoformat(str(handoff["cycle"]))
    if cycle.tzinfo is None:
        cycle = cycle.replace(tzinfo=UTC)
    staged = load_staged_work_inputs(
        work_dir=work,
        source=str(handoff["source"]),
        cycle=cycle,
        project_name=str(handoff["project_name"]),
        grid_id=str(handoff["grid_id"]),
        max_manifest_bytes=int(handoff["max_manifest_bytes"]),
        max_asset_bytes=int(handoff["max_asset_bytes"]),
        max_state_bytes=int(handoff["max_state_bytes"]),
    )
    if staged.manifest_checksum != handoff["manifest_checksum"]:
        raise ProductionAttemptError("staged manifest digest drifted before compute")
    prepared = staged.prepared
    identity = _identity_from_prepared(
        source=staged.source, cycle=staged.cycle, prepared=prepared
    )
    registry = stage_work_registry(
        work_root=staged.work_dir.parent.parent,
        identity=identity,
        contract=prepared.contract,
        binding_content=prepared.binding_content,
        sp_att_content=prepared.sp_att_content,
        max_asset_bytes=int(handoff["max_asset_bytes"]),
    )
    store = LocalObjectStore(registry.object_store_root)
    _convert_canonical(staged, store)
    forcing = ForcingProducer(
        config=ForcingProducerConfig(
            workspace_root=staged.work_dir,
            object_store_root=registry.object_store_root,
            object_store_prefix="",
        ),
        repository=FileForcingRepository(store, registry.registry_manifest),
        object_store=store,
    ).produce(
        source_id=identity.source_id,
        cycle_time=identity.cycle_time,
        model_id=identity.model_id,
        basin_id=identity.basin_id,
        basin_version_id=identity.basin_version_id,
        river_network_version_id=identity.river_network_version_id,
    )
    run_directory = assemble_staged(
        registry=registry, staged_inputs=staged, forcing=forcing
    )
    scratch_dat = Path(handoff["scratch_dat"])
    job_log = Path(handoff["job_log"])
    tracker = CheckpointTracker(
        run_dir=run_directory.path,
        project_name=identity.project_name,
        checkpoint_hours=tuple(handoff["checkpoint_hours"]),
    )
    argv = _shud_argv(str(handoff["shud_binary"]), identity.project_name)
    code = _run_shud_live(
        argv=argv,
        cwd=run_directory.path,
        work_dir=staged.work_dir,
        log_path=job_log,
        tracker=tracker,
        env=env,
    )
    if code != 0:
        raise ProductionAttemptError(f"SHUD exited {code} in {run_directory.path}")

    def recovery_runner(*, run_directory: RunDirectory, output_dir: Path) -> int:
        recovered = subprocess.Popen(
            list(argv),
            cwd=output_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=dict(env),
        )
        stdout, _stderr = recovered.communicate()
        _append_job_log(job_log, staged.work_dir, stdout)
        return int(recovered.returncode or 0)

    try:
        captured = ensure_twelve_hour_checkpoint(
            tracker=tracker,
            run_directory=run_directory,
            runner=recovery_runner,
        )
    except TrackerError as error:
        raise ProductionAttemptError(
            f"T+12 checkpoint recovery failed: {error}"
        ) from error
    if not scratch_dat.is_file():
        raise ProductionAttemptError(f"SHUD did not write DAT {scratch_dat}")
    slurm_job = os.environ.get("SLURM_JOB_ID")
    if not slurm_job:
        raise ProductionAttemptError("SLURM_JOB_ID is required to bind the receipt")
    log_digest = _stream_checksum(job_log, staged.work_dir)
    _write_receipt(
        work_dir=staged.work_dir,
        job_id=slurm_job,
        identity=identity,
        run_directory=run_directory,
        scratch_dat=scratch_dat,
        merged_log=job_log,
        checkpoint=captured,
        prepared=prepared,
        attempt_payload_digest=str(handoff["attempt_payload_digest"]),
        root_id=root_id,
    )
    if _stream_checksum(job_log, staged.work_dir) != log_digest:
        raise ProductionAttemptError("job log changed after receipt checksum")


def _parse_worker_argv(argv: Sequence[str]) -> Path:
    if len(argv) != 2 or argv[0] != "--work-dir":
        raise ProductionAttemptError(
            "private worker argv must be exactly --work-dir <absolute-work>"
        )
    return Path(argv[1])


if __name__ == "__main__":
    run_private_worker(work_dir=_parse_worker_argv(sys.argv[1:]))
