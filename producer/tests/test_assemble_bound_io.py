"""Bound-IO regressions for staged assemble (#177 / PR #181)."""

from __future__ import annotations

import errno
import os
import shutil
import threading
from pathlib import Path

import pytest
import run_once_fixtures as fixtures
from assembly_fixtures import NATIVE_PARAMETER_EXPECTED, write_forcing_package

from yd_producer._assemble_io import BoundAssemblyIO
from yd_producer._work_claim import _FILE_READ_FLAGS
from yd_producer.assemble import AssemblyError, stage_work_registry
from yd_producer.staged_inputs import assemble_staged, stage_work_inputs


def _staged_assembly(tmp_path: Path):
    _local, source_variant, source_state, claim, ids = fixtures._stage_public(tmp_path)
    staged = stage_work_inputs(
        claim=claim,
        source_variant_dir=source_variant,
        source_state_path=source_state,
        **ids,
    )
    identity = fixtures.run_identity()
    registry = stage_work_registry(
        work_root=claim.work_root,
        identity=identity,
        contract=staged.prepared.contract,
        binding_content=staged.prepared.binding_content,
        sp_att_content=staged.prepared.sp_att_content,
        max_asset_bytes=ids["max_asset_bytes"],
    )
    forcing = write_forcing_package(registry.object_store_root, identity)
    return claim, staged, registry, forcing


def test_assemble_staged_rejects_root_replacement_after_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yd_producer.staged_inputs as staged_module

    claim, staged, registry, forcing = _staged_assembly(tmp_path)
    original_kernel = staged_module._assemble_kernel
    old_root = claim.work_dir.with_name(claim.work_dir.name + "-original")

    def swap_before_kernel(*args, **kwargs):
        claim.work_dir.rename(old_root)
        shutil.copytree(old_root, claim.work_dir)
        assert claim.work_dir.stat().st_ino != old_root.stat().st_ino
        return original_kernel(*args, **kwargs)

    monkeypatch.setattr(staged_module, "_assemble_kernel", swap_before_kernel)
    with pytest.raises(AssemblyError) as captured:
        assemble_staged(registry=registry, staged_inputs=staged, forcing=forcing)
    assert captured.value.phase == "validate"
    assert not (old_root / "model").exists()
    assert not (claim.work_dir / "model").exists()
    assert list(claim.work_dir.glob(".model.assemble-stage-*")) == []
    assert list(old_root.glob(".model.assemble-stage-*")) == []


@pytest.mark.parametrize("boundary", ["copy", "commit", "cleanup"])
def test_assemble_staged_late_root_swap_does_not_mutate_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    from yd_producer._assemble_io import BoundAssemblyIO

    claim, staged, registry, forcing = _staged_assembly(tmp_path)
    old_root = claim.work_dir.with_name(claim.work_dir.name + "-original")
    swapped = {"done": False}
    cloned: dict[str, object] = {}

    def _tree_snapshot(
        root: Path,
    ) -> dict[str, tuple[str, int, int, int, int, bytes | None]]:
        snapshot: dict[str, tuple[str, int, int, int, int, bytes | None]] = {}
        for path in [root, *root.rglob("*")]:
            info = path.lstat()
            kind = "dir" if path.is_dir() else "file"
            payload = path.read_bytes() if path.is_file() else None
            snapshot[str(path.relative_to(root))] = (
                kind,
                info.st_mode,
                info.st_mtime_ns,
                info.st_dev,
                info.st_ino,
                payload,
            )
        return snapshot

    def swap_named_root() -> None:
        if swapped["done"]:
            return
        swapped["done"] = True
        claim.work_dir.rename(old_root)
        shutil.copytree(old_root, claim.work_dir)
        cloned["tree"] = _tree_snapshot(claim.work_dir)

    real_copy = BoundAssemblyIO.copy_regular
    real_rename = BoundAssemblyIO.rename
    real_clean = BoundAssemblyIO.clean

    def copying(self, *args, **kwargs):
        if boundary == "copy":
            swap_named_root()
        return real_copy(self, *args, **kwargs)

    def renaming(self, *args, **kwargs):
        if boundary == "commit":
            swap_named_root()
        return real_rename(self, *args, **kwargs)

    def cleaning(self, path, work_root):
        if boundary == "cleanup":
            swap_named_root()
        return real_clean(self, path, work_root)

    monkeypatch.setattr(BoundAssemblyIO, "copy_regular", copying)
    monkeypatch.setattr(BoundAssemblyIO, "rename", renaming)
    monkeypatch.setattr(BoundAssemblyIO, "clean", cleaning)
    with pytest.raises(AssemblyError):
        assemble_staged(registry=registry, staged_inputs=staged, forcing=forcing)
    assert swapped["done"]
    replacement_model = claim.work_dir / "model"
    if boundary != "cleanup":
        assert not replacement_model.exists()
    assert (old_root / "model").is_dir() or list(
        old_root.glob(".model.assemble-stage-*")
    )
    assert cloned["tree"] == _tree_snapshot(claim.work_dir)


def test_overlapping_assemble_staged_calls_keep_distinct_root_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yd_producer.staged_inputs as staged_module
    from yd_producer._assemble_io import BoundAssemblyIO

    claim_a, staged_a, registry_a, forcing_a = _staged_assembly(tmp_path / "one")
    claim_b, staged_b, registry_b, forcing_b = _staged_assembly(tmp_path / "two")
    original_kernel = staged_module._assemble_kernel
    original_write = BoundAssemblyIO.write_new
    b_bound, a_held, b_done = threading.Event(), threading.Event(), threading.Event()
    errors: dict[str, BaseException] = {}
    results: dict[str, object] = {}
    old_a = claim_a.work_dir.with_name(claim_a.work_dir.name + "-original")
    swapped = {"done": False}

    def tracking_kernel(*args, **kwargs):
        inputs = args[2] if len(args) > 2 else kwargs.get("inputs")
        if (
            getattr(inputs, "io", None) is not None
            and inputs.io.work == claim_b.work_dir
        ):
            b_bound.set()
            assert a_held.wait(timeout=10)
        return original_kernel(*args, **kwargs)

    def holding_write(self, path, content, root):
        if self.work == claim_a.work_dir and not a_held.is_set():
            a_held.set()
            assert b_done.wait(timeout=10)
            claim_a.work_dir.rename(old_a)
            named = claim_a.work_dir
            named.mkdir()
            shutil.copytree(old_a / "input", named / "input")
            shutil.copytree(old_a / "object-store", named / "object-store")
            swapped["done"] = True
        return original_write(self, path, content, root)

    def run_a() -> None:
        assert b_bound.wait(timeout=10)
        try:
            results["a"] = assemble_staged(
                registry=registry_a, staged_inputs=staged_a, forcing=forcing_a
            )
        except BaseException as error:  # noqa: BLE001 - collect worker error
            errors["a"] = error

    def run_b() -> None:
        try:
            results["b"] = assemble_staged(
                registry=registry_b, staged_inputs=staged_b, forcing=forcing_b
            )
        except BaseException as error:  # noqa: BLE001 - collect worker error
            errors["b"] = error
        finally:
            b_done.set()

    monkeypatch.setattr(staged_module, "_assemble_kernel", tracking_kernel)
    monkeypatch.setattr(BoundAssemblyIO, "write_new", holding_write)
    thread_b = threading.Thread(target=run_b)
    thread_a = threading.Thread(target=run_a)
    thread_b.start()
    thread_a.start()
    thread_b.join(timeout=15)
    thread_a.join(timeout=15)
    assert not thread_b.is_alive()
    assert not thread_a.is_alive()
    assert swapped["done"]
    assert "b" not in errors
    assert isinstance(errors.get("a"), AssemblyError)
    assert results["b"].path == claim_b.work_dir / "model"
    assert (
        claim_b.work_dir / "model" / "input" / "yd" / "yd.cfg.para"
    ).read_bytes() == NATIVE_PARAMETER_EXPECTED
    assert not (claim_a.work_dir / "model").exists()
    assert list(claim_a.work_dir.glob(".model.assemble-stage-*")) == []
    assert (old_a / "model").is_dir() or list(old_a.glob(".model.assemble-stage-*"))
    assert not any(
        "model" in path.parts or path.name.startswith(".model.")
        for path in claim_a.work_dir.rglob("*")
    )


@pytest.mark.parametrize("inject_close_error", [False, True])
def test_assemble_staged_closes_forcing_index_fd_while_error_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inject_close_error: bool
) -> None:
    from yd_producer._assemble_io import BoundAssemblyIO

    claim, staged, registry, forcing = _staged_assembly(tmp_path)
    index_path = (
        registry.object_store_root
        / forcing.forcing_package_uri
        / "shud"
        / "stations.tsd.forc"
    )
    index_path.write_bytes(
        b"2 20260826\nshud\nID\tLon\tLat\tX\tY\tZ\tFilename\nx\t1\t2\t3\t4\t5\tX1.csv\n"
    )
    expected_id = (index_path.stat().st_dev, index_path.stat().st_ino)
    fd_root = Path("/dev/fd") if Path("/dev/fd").exists() else Path("/proc/self/fd")
    if inject_close_error:
        original_close = BoundAssemblyIO.close

        def failing_close(self):
            original_close(self)
            raise OSError("unique-close-failure")

        monkeypatch.setattr(BoundAssemblyIO, "close", failing_close)
    with pytest.raises(AssemblyError) as captured:
        assemble_staged(registry=registry, staged_inputs=staged, forcing=forcing)
    error = captured.value
    assert error.phase == "validate"
    leaked = []
    for entry in fd_root.iterdir():
        try:
            info = os.fstat(int(entry.name))
        except (OSError, ValueError):
            continue
        if (info.st_dev, info.st_ino) == expected_id:
            leaked.append(entry.name)
    assert leaked == []
    assert not (claim.work_dir / "model").exists()
    if inject_close_error:
        notes = getattr(error, "__notes__", [])
        assert sum("unique-close-failure" in note for note in notes) == 1


def test_assemble_staged_parent_close_after_parameter_open_closes_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yd_producer.staged_inputs as staged_module

    claim, staged, registry, forcing = _staged_assembly(tmp_path)
    identity = claim.identity
    real_bind = staged_module.bind_work_io
    real_open, real_close = os.open, os.close
    live: dict[int, str] = {}
    injected = {"done": False}

    def bind(*args, **kwargs):
        bound = real_bind(*args, **kwargs)

        def tracked_open(*open_args, **open_kwargs):
            fd = real_open(*open_args, **open_kwargs)
            live[fd] = str(open_args[0])
            return fd

        def tracked_close(fd):
            name = live.get(fd, "")
            should_fail = (
                not injected["done"]
                and name == "variant"
                and any(value.endswith(".para") for value in live.values())
            )
            real_close(fd)
            live.pop(fd, None)
            if should_fail:
                injected["done"] = True
                raise OSError(errno.EIO, "injected predecessor-close")

        monkeypatch.setattr(os, "open", tracked_open)
        monkeypatch.setattr(os, "close", tracked_close)
        return bound

    monkeypatch.setattr(staged_module, "bind_work_io", bind)
    with pytest.raises(AssemblyError) as captured:
        assemble_staged(registry=registry, staged_inputs=staged, forcing=forcing)
    error = captured.value
    assert injected["done"]
    assert error.phase == "validate"
    assert "injected predecessor-close" in str(error)
    assert live == {}
    assert not (claim.work_dir / "model").exists()
    assert claim.identity == identity


@pytest.mark.parametrize("kind", ["fifo", "device", "open-swap"])
def test_assemble_staged_post_bind_fifo_device_and_inode_swap_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    import yd_producer.staged_inputs as staged_module

    claim, staged, registry, forcing = _staged_assembly(tmp_path)
    identity = claim.identity
    real_bind = staged_module.bind_work_io
    real_open, real_read = os.open, os.read
    fifo_fds: list[int] = []
    device_fds: set[int] = set()
    fifo_identity: tuple[int, int] | None = None

    def read_then_finish(fd, size):
        assert fd not in device_fds
        content = real_read(fd, size)
        info = os.fstat(fd)
        if content and fifo_identity == (info.st_dev, info.st_ino):
            while fifo_fds:
                os.close(fifo_fds.pop())
        return content

    def bind(*args, **kwargs):
        nonlocal fifo_identity
        bound = real_bind(*args, **kwargs)
        path = staged.variant_dir / "yd.cfg.para"
        content = path.read_bytes()
        if kind == "fifo":
            path.unlink()
            os.mkfifo(path)
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
            fifo_fds.append(fd)
            assert os.write(fd, content) == len(content)
            fifo_identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
            monkeypatch.setattr(os, "read", read_then_finish)
            return bound

        def swapping_open(target, flags, *rest, **extra):
            leaf = target if isinstance(target, str) else getattr(target, "name", None)
            if leaf != "yd.cfg.para":
                return real_open(target, flags, *rest, **extra)
            if kind == "device":
                fd = real_open(os.devnull, os.O_RDONLY)
                device_fds.add(fd)
                return fd
            named = os.stat(
                "yd.cfg.para", dir_fd=extra["dir_fd"], follow_symlinks=False
            )
            fd = real_open(target, flags, *rest, **extra)
            replacement = path.parent / "replacement.para"
            replacement.write_bytes(content)
            os.replace(replacement, path)
            swapped = real_open(path, flags)
            os.close(fd)
            assert os.fstat(swapped).st_ino != named.st_ino
            return swapped

        monkeypatch.setattr(os, "open", swapping_open)
        monkeypatch.setattr(os, "read", read_then_finish)
        return bound

    monkeypatch.setattr(staged_module, "bind_work_io", bind)
    try:
        with pytest.raises(AssemblyError) as captured:
            assemble_staged(registry=registry, staged_inputs=staged, forcing=forcing)
        error = captured.value
        assert error.phase == "validate"
        assert not (claim.work_dir / "model").exists()
        assert claim.identity == identity
    finally:
        for fd in fifo_fds:
            os.close(fd)


def test_bound_mkdir_predecessor_close_closes_nested_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim, staged, _registry, _forcing = _staged_assembly(tmp_path)
    bound = BoundAssemblyIO(staged.work_dir, staged.work_identity)
    nested = staged.work_dir / "stage-a" / "stage-b"
    real_open, real_close = os.open, os.close
    live: dict[int, str] = {}
    injected = {"done": False}
    root_fd = bound.root_fd

    def tracked_open(*open_args, **open_kwargs):
        fd = real_open(*open_args, **open_kwargs)
        live[fd] = str(open_args[0])
        return fd

    def tracked_close(fd):
        name = live.get(fd, "")
        should_fail = (
            not injected["done"] and name == "stage-a" and "stage-b" in live.values()
        )
        if fd == root_fd:
            raise AssertionError("borrowed root fd must not be closed by mkdir")
        real_close(fd)
        live.pop(fd, None)
        if should_fail:
            injected["done"] = True
            raise OSError(errno.EIO, "injected predecessor-close")

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "close", tracked_close)
    try:
        with pytest.raises(OSError, match="injected predecessor-close"):
            bound._mkdir(nested)
        assert injected["done"]
        assert "stage-b" not in live.values()
        os.fstat(root_fd)
        absent = staged.work_dir / "stage-a" / "absent.para"
        with pytest.raises((FileNotFoundError, ValueError, OSError)):
            bound._open(absent, _FILE_READ_FLAGS)
    finally:
        monkeypatch.undo()
        bound.close()
    assert not (claim.work_dir / "model").exists()
    assert claim.identity == staged.work_identity
