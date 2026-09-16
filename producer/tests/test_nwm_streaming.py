import json
import shutil
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from assembly_fixtures import NATIVE_PARAMETER_EXPECTED
from test_nwm import (
    GFS_NATIVE,
    GRID_ID,
    IFS_NATIVE,
    SOURCE,
    WORKER_STRIP,
    _plant_raw,
    _prepared_attempt,
    _record,
)

from yd_producer import nwm
from yd_producer.nwm import (
    RECEIPT_FILENAME,
    ProductionAttemptDriver,
    ProductionAttemptError,
    run_private_worker,
)

STREAM_BYTES = 520_000
MERGED_BYTES = 1_040_000
PRIMARY_TAIL = b"PRIMARY-EOF-TAIL\n"
RECOVERY_TAIL = b"RECOVERY-EOF-TAIL\n"
RECOVERY_EXIT = 7
CHECKPOINT_NAME = "yd.f012.cfg.ic.update"
RECOVERY_UPDATE = Path("state_checkpoint_recovery") / "f012" / "yd.cfg.ic.update"
PARAMETER_PATH = Path("model") / "input" / "yd" / "yd.cfg.para"


def _merged_stream(*, recovery: bool) -> bytes:
    tail = RECOVERY_TAIL if recovery else PRIMARY_TAIL
    stdout_fill = b"R" if recovery else b"P"
    stderr_fill = b"F" if recovery else b"E"
    body = STREAM_BYTES - len(tail)
    stdout_n = body // 2
    stderr_n = body - stdout_n
    return stdout_fill * stdout_n + stderr_fill * stderr_n + tail


def _write_stream_parts(directory: Path, *, recovery: bool) -> None:
    payload = _merged_stream(recovery=recovery)
    tail = RECOVERY_TAIL if recovery else PRIMARY_TAIL
    body = payload[: -len(tail)]
    stdout_n = (STREAM_BYTES - len(tail)) // 2
    kind = "recovery" if recovery else "primary"
    (directory / f"stream-{kind}-out.bin").write_bytes(body[:stdout_n])
    (directory / f"stream-{kind}-err.bin").write_bytes(body[stdout_n:])
    (directory / f"stream-{kind}-tail.bin").write_bytes(tail)


def _install_streaming_shud(path: Path) -> None:
    directory = path.parent
    dat_src = directory / "shud-dat.bin"
    update_src = directory / "shud-update.bin"
    recovery_src = directory / "shud-recovery-update.bin"
    _write_stream_parts(directory, recovery=False)
    _write_stream_parts(directory, recovery=True)
    path.write_text(
        f"""#!{sys.executable}
import os
import sys
from pathlib import Path

if len(sys.argv) != 4 or sys.argv[1] != "-o":
    raise SystemExit(64)
out = Path(sys.argv[2])
project = sys.argv[3]
ic = Path("input/yd") / f"{{project}}.cfg.ic"
para = Path("input/yd") / f"{{project}}.cfg.para"
forc = Path("input/yd") / f"{{project}}.tsd.forc"
if not ic.is_file() or not para.is_file() or not forc.stat().st_size:
    raise SystemExit(65)
text = para.read_text(encoding="utf-8")
if not any(
    line.startswith("END") and line.split()[-1] in {{"7", "0.5"}}
    for line in text.splitlines()
):
    raise SystemExit(65)
recovery = out.name == "f012"
update = Path({str(recovery_src)!r} if recovery else {str(update_src)!r})
out.mkdir(parents=True, exist_ok=True)
(out / f"{{project}}.rivqdown.dat").write_bytes(Path({str(dat_src)!r}).read_bytes())
(out / f"{{project}}.cfg.ic.update").write_bytes(update.read_bytes())
base = Path(__file__).resolve().parent
kind = "recovery" if recovery else "primary"
sys.stdout.buffer.write((base / f"stream-{{kind}}-out.bin").read_bytes())
sys.stdout.buffer.flush()
sys.stderr.buffer.write((base / f"stream-{{kind}}-err.bin").read_bytes())
sys.stderr.buffer.flush()
sys.stdout.buffer.write((base / f"stream-{{kind}}-tail.bin").read_bytes())
sys.stdout.buffer.flush()
if recovery:
    raise SystemExit(int(os.environ.get("YD_STREAM_RECOVERY_EXIT", "0")))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _isolate_worker_env(
    monkeypatch: pytest.MonkeyPatch, *, recovery_exit: int | None = None
) -> None:
    for key in WORKER_STRIP:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SLURM_JOB_ID", "job-9")
    if recovery_exit is None:
        monkeypatch.delenv("YD_STREAM_RECOVERY_EXIT", raising=False)
    else:
        monkeypatch.setenv("YD_STREAM_RECOVERY_EXIT", str(recovery_exit))


def _prepare_streaming_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: str,
    native: dict,
    recovery: bool,
    recovery_exit: int | None = None,
):
    source_root, claim, attempt = _prepared_attempt(
        tmp_path,
        source=source,
        grid_id=GRID_ID,
        fixture="converter",
        shud_recovery=recovery,
        shud_name=f"shud-stream-{source}",
    )
    shutil.rmtree(source_root)
    _plant_raw(claim.work_dir, source=source, native=native)
    handoff = json.loads(
        (claim.work_dir / "yd.attempt-handoff.json").read_text(encoding="utf-8")
    )
    _install_streaming_shud(Path(handoff["shud_binary"]))
    _isolate_worker_env(monkeypatch, recovery_exit=recovery_exit)
    return claim, attempt


def _spy_append_sizes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    sizes: list[int] = []
    real_append = nwm._append_job_log

    def forwarding(
        log_path: Path, work_dir: Path, root_id: tuple[int, int], data: bytes
    ) -> None:
        sizes.append(len(data))
        return real_append(log_path, work_dir, root_id, data)

    monkeypatch.setattr(nwm, "_append_job_log", forwarding)
    return sizes


def _recovery_append_sizes(sizes: list[int], primary_len: int) -> list[int]:
    consumed = 0
    recovered: list[int] = []
    for size in sizes:
        if size == 0:
            continue
        if consumed < primary_len:
            consumed += size
            if consumed > primary_len:
                raise AssertionError("primary append crossed the emitted primary bytes")
            continue
        recovered.append(size)
    if consumed != primary_len:
        raise AssertionError(
            f"primary appends summed to {consumed}, expected {primary_len}"
        )
    return recovered


def test_primary_only_job_log_matches_emitted_stream(tmp_path, monkeypatch) -> None:
    expected = _merged_stream(recovery=False)
    assert len(expected) == STREAM_BYTES
    claim, attempt = _prepare_streaming_worker(
        tmp_path,
        monkeypatch,
        source=SOURCE,
        native=GFS_NATIVE,
        recovery=False,
    )
    run_private_worker(work_dir=claim.work_dir)
    log = (claim.work_dir / "job.log").read_bytes()
    receipt = json.loads(
        (claim.work_dir / RECEIPT_FILENAME).read_text(encoding="utf-8")
    )
    assert log == expected
    assert len(log) == STREAM_BYTES
    assert receipt["merged_log_checksum"] == f"sha256:{sha256(expected).hexdigest()}"
    products = ProductionAttemptDriver(grid_id=GRID_ID).collect(
        attempt=attempt, terminal_record=_record()
    )
    assert 12 in products.tracker.captured


def test_recovery_keeps_ordered_log_and_bounded_appends(tmp_path, monkeypatch) -> None:
    primary = _merged_stream(recovery=False)
    recovered_stream = _merged_stream(recovery=True)
    expected = primary + recovered_stream
    assert len(primary) == STREAM_BYTES
    assert len(expected) == MERGED_BYTES
    claim, attempt = _prepare_streaming_worker(
        tmp_path,
        monkeypatch,
        source="ifs",
        native=IFS_NATIVE,
        recovery=True,
    )
    appends = _spy_append_sizes(monkeypatch)
    run_private_worker(work_dir=claim.work_dir)
    log = (claim.work_dir / "job.log").read_bytes()
    receipt = json.loads(
        (claim.work_dir / RECEIPT_FILENAME).read_text(encoding="utf-8")
    )
    assert log == expected
    assert len(log) == MERGED_BYTES
    assert receipt["merged_log_checksum"] == f"sha256:{sha256(expected).hexdigest()}"
    products = ProductionAttemptDriver(grid_id=GRID_ID).collect(
        attempt=attempt, terminal_record=_record()
    )
    assert 12 in products.tracker.captured
    recovered = _recovery_append_sizes(appends, STREAM_BYTES)
    assert sum(recovered) == STREAM_BYTES
    assert len(recovered) > 1
    assert all(size <= nwm._LOG_CHUNK for size in recovered)


def test_nonzero_recovery_keeps_log_without_receipt(tmp_path, monkeypatch) -> None:
    primary = _merged_stream(recovery=False)
    recovered_stream = _merged_stream(recovery=True)
    expected = primary + recovered_stream
    claim, _attempt = _prepare_streaming_worker(
        tmp_path,
        monkeypatch,
        source="ifs",
        native=IFS_NATIVE,
        recovery=True,
        recovery_exit=RECOVERY_EXIT,
    )
    with pytest.raises(
        ProductionAttemptError, match="T\\+12 checkpoint recovery failed"
    ):
        run_private_worker(work_dir=claim.work_dir)
    assert (claim.work_dir / "job.log").read_bytes() == expected
    assert not (claim.work_dir / RECEIPT_FILENAME).exists()
    assert (claim.work_dir / RECOVERY_UPDATE).is_file()
    assert not (
        claim.work_dir / "model" / "state_checkpoints" / CHECKPOINT_NAME
    ).exists()
    assert (claim.work_dir / PARAMETER_PATH).read_bytes() == NATIVE_PARAMETER_EXPECTED
