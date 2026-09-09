"""Per-call descriptor-bound assembly IO for the shared kernel."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from yd_producer import _assemble_fs
from yd_producer._work_claim import (
    _DIR_FLAGS,
    _FILE_EXCL_FLAGS,
    _FILE_READ_FLAGS,
    ClaimLostError,
    WorkClaim,
    _close_walk,
    _relative_parts,
    _walk_to_parent,
    open_claimed_root,
)
from yd_producer.forcing import DirectGridForcingContract
from yd_producer.store.object_store import LocalObjectStore, ObjectStoreError
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    list_directory_no_follow,
    open_file_no_follow,
    remove_tree_allow_symlinks,
    stat_no_follow,
)

_COPY_CHUNK = 1024 * 1024
_FILE_CLOSE_NOTE = "file descriptor close also failed"


class AssemblyIO(Protocol):
    def directory(self, path: Path, root: Path | None, *, create: bool) -> None: ...

    def regular(self, path: Path, root: Path | None) -> None: ...

    def absent(self, parent: Path, name: str, root: Path) -> None: ...

    def read_limited(self, path: Path, maximum: int, root: Path | None) -> bytes: ...

    def write_new(self, path: Path, content: bytes, root: Path) -> None: ...

    def copy_regular(
        self,
        source: Path,
        destination: Path,
        source_root: Path,
        destination_root: Path,
        *,
        expected_checksum: str | None = None,
    ) -> None: ...

    def rename(
        self,
        source_parent: Path,
        source: str,
        target_parent: Path,
        target: str,
        root: Path,
        *,
        operation=None,
    ) -> None: ...

    def clean(self, path: Path, work: Path) -> tuple[str, ...]: ...

    def require_named_root(self, work: Path) -> None: ...

    def close(self) -> None: ...

    def iter_regular(
        self, path: Path, root: Path, *, chunk_size: int = _COPY_CHUNK
    ) -> Iterator[bytes]: ...


class AssemblyInputs(NamedTuple):
    variant_root: Path
    states_root: Path
    state_path: Path
    variant_dirs: Sequence[Path]
    variant_files: Sequence[tuple[Path, Path, str | None]]
    parameter_check: tuple[int, str | None]
    state_check: tuple[int, str | None]
    containment_root: Path | None
    expected_contract: DirectGridForcingContract | None
    io: AssemblyIO


class SharedAssemblyIO:
    """Stateless shared-helper IO used by legacy assemble and registry staging."""

    def directory(self, path: Path, root: Path | None, *, create: bool) -> None:
        _assemble_fs.directory(path, root, create=create)

    def regular(self, path: Path, root: Path | None) -> None:
        _assemble_fs.regular(path, root)

    def absent(self, parent: Path, name: str, root: Path) -> None:
        _assemble_fs.absent(parent, name, root)

    def read_limited(self, path: Path, maximum: int, root: Path | None) -> bytes:
        return _assemble_fs.read_limited(path, maximum, root)

    def write_new(self, path: Path, content: bytes, root: Path) -> None:
        _assemble_fs.write_new(path, content, root)

    def copy_regular(
        self,
        source: Path,
        destination: Path,
        source_root: Path,
        destination_root: Path,
        *,
        expected_checksum: str | None = None,
    ) -> None:
        _assemble_fs.copy_regular(
            source,
            destination,
            source_root,
            destination_root,
            expected_checksum=expected_checksum,
        )

    def rename(
        self,
        source_parent: Path,
        source: str,
        target_parent: Path,
        target: str,
        root: Path,
        *,
        operation=None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if operation is not None:
            kwargs["operation"] = operation
        _assemble_fs.rename(
            source_parent, source, target_parent, target, root, **kwargs
        )

    def clean(self, path: Path, work: Path) -> tuple[str, ...]:
        return _assemble_fs.clean(path, work)

    def require_named_root(self, work: Path) -> None:
        return None

    def close(self) -> None:
        return None

    def iter_regular(
        self, path: Path, root: Path, *, chunk_size: int = _COPY_CHUNK
    ) -> Iterator[bytes]:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive.")
        fd = open_file_no_follow(path, containment_root=root)
        primary: BaseException | None = None
        try:
            while chunk := os.read(fd, chunk_size):
                yield chunk
        except BaseException as error:  # noqa: BLE001 - preserve primary close notes
            primary = error
        close_error = _close_fd(fd)
        if primary is not None:
            if close_error is not None:
                primary.add_note(f"{_FILE_CLOSE_NOTE}: {close_error}")
            raise primary
        if close_error is not None:
            raise close_error


SHARED_IO = SharedAssemblyIO()


def bind_store(store: LocalObjectStore, fs: AssemblyIO):
    if type(fs) is SharedAssemblyIO:
        return store
    return BoundStore(store, fs)


class BoundStore:
    """Route object-store reads through the current call's assembly IO."""

    def __init__(self, store: LocalObjectStore, fs: AssemblyIO) -> None:
        self._store = store
        self._fs = fs
        self.root = store.root

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    def normalize_key(self, key_or_uri: str) -> str:
        return self._store.normalize_key(key_or_uri)

    def resolve_path(self, key_or_uri: str) -> Path:
        return self._store.resolve_path(key_or_uri)

    def uri_for_key(self, key: str) -> str:
        return self._store.uri_for_key(key)

    def read_bytes_limited(self, key_or_uri: str, *, max_bytes: int) -> bytes:
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative.")
        path = self._store.resolve_path(key_or_uri)
        try:
            content = self._fs.read_limited(path, max_bytes, self.root)
        except (OSError, SafeFilesystemError, ValueError) as error:
            raise ObjectStoreError(
                f"Failed to read object {key_or_uri}: {error}"
            ) from error
        if len(content) > max_bytes:
            raise ObjectStoreError(
                f"Object {key_or_uri} exceeds read limit: observed more than {max_bytes} bytes"
            )
        return content

    def iter_bytes(
        self, key_or_uri: str, *, chunk_size: int = _COPY_CHUNK
    ) -> Iterator[bytes]:
        path = self._store.resolve_path(key_or_uri)
        try:
            yield from self._fs.iter_regular(path, self.root, chunk_size=chunk_size)
        except (OSError, SafeFilesystemError, ValueError) as error:
            raise ObjectStoreError(
                f"Failed to stream object {key_or_uri}: {error}"
            ) from error


def bind_work_io(work: Path, identity: tuple[int, int]) -> BoundAssemblyIO:
    return BoundAssemblyIO(work, identity)


class BoundAssemblyIO:
    """Pin one consumer-local work inode and serve kernel IO from that fd."""

    def __init__(self, work: Path, identity: tuple[int, int]) -> None:
        self.work = work
        self.identity = identity
        self._claim = WorkClaim(
            work_root=work.parent.parent, work_dir=work, identity=identity
        )
        self.root_fd = open_claimed_root(self._claim)
        self._closed = False
        self._streams: set[int] = set()
        self._generators: list[Iterator[bytes]] = []

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        primary: BaseException | None = None
        for stream in self._generators:
            try:
                stream.close()
            except BaseException as error:  # noqa: BLE001 - collect close errors, then re-raise after all owned FDs close
                if primary is None:
                    primary = error
                else:
                    primary.add_note(f"{_FILE_CLOSE_NOTE}: {error}")
        self._generators.clear()
        for fd in list(self._streams):
            error = _close_owned_stream(self._streams, fd)
            if error is None:
                continue
            if primary is None:
                primary = error
            else:
                primary.add_note(f"{_FILE_CLOSE_NOTE}: {error}")
        root_error = _close_fd(self.root_fd)
        if root_error is not None:
            if primary is None:
                primary = root_error
            else:
                primary.add_note(f"{_FILE_CLOSE_NOTE}: {root_error}")
        if primary is not None:
            raise primary

    def require_named_root(self, work: Path) -> None:
        if work != self.work:
            raise ValueError("work root identity changed during assembly.")
        try:
            named = os.lstat(work)
        except OSError as error:
            raise ValueError("work root identity changed during assembly.") from error
        if (named.st_dev, named.st_ino) != self.identity:
            raise ValueError("work root identity changed during assembly.")
        opened = os.fstat(self.root_fd)
        if (opened.st_dev, opened.st_ino) != self.identity:
            raise ValueError("work root identity changed during assembly.")

    def directory(self, path: Path, root: Path | None, *, create: bool) -> None:
        if not self._owns(root):
            SHARED_IO.directory(path, root, create=create)
            return
        try:
            if create:
                self._mkdir(path)
                return
            fd = self._dir(path)
        except ClaimLostError as error:
            raise ValueError(str(error)) from error
        _finish_close(fd)

    def regular(self, path: Path, root: Path | None) -> None:
        if not self._owns(root):
            SHARED_IO.regular(path, root)
            return
        try:
            fd = self._open(path, _FILE_READ_FLAGS)
        except ClaimLostError as error:
            raise ValueError(str(error)) from error
        _finish_close(fd)

    def absent(self, parent: Path, name: str, root: Path) -> None:
        if not self._owns(root):
            SHARED_IO.absent(parent, name, root)
            return
        _assemble_fs.component(name, "entry name")
        try:
            fd = self._dir(parent)
        except ClaimLostError as error:
            raise ValueError(str(error)) from error
        primary: BaseException | None = None
        try:
            os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        except BaseException as error:
            primary = error
            raise
        else:
            raise ValueError(f"destination already exists: {parent / name}")
        finally:
            _finish_close(fd, primary)

    def read_limited(self, path: Path, maximum: int, root: Path | None) -> bytes:
        if not self._owns(root):
            return SHARED_IO.read_limited(path, maximum, root)
        try:
            fd = self._open(path, _FILE_READ_FLAGS)
        except ClaimLostError as error:
            raise ValueError(str(error)) from error
        primary: BaseException | None = None
        result: bytes | None = None
        try:
            content = bytearray()
            limit = maximum + 1
            while len(content) < limit:
                chunk = os.read(fd, limit - len(content))
                if not chunk:
                    break
                content.extend(chunk)
            result = bytes(content)
        except BaseException as error:  # noqa: BLE001 - preserve primary close notes
            primary = error
        close_error = _close_fd(fd)
        if primary is not None:
            if close_error is not None:
                primary.add_note(f"{_FILE_CLOSE_NOTE}: {close_error}")
            raise primary
        if close_error is not None:
            raise close_error
        assert result is not None
        if len(result) > maximum:
            raise ValueError(f"file exceeds the {maximum} byte limit: {path}")
        return result

    def write_new(self, path: Path, content: bytes, root: Path) -> None:
        if not self._owns(root):
            SHARED_IO.write_new(path, content, root)
            return
        try:
            self._mkdir(path.parent)
            fd = self._open(path, _FILE_EXCL_FLAGS, 0o644)
        except ClaimLostError as error:
            raise ValueError(str(error)) from error
        primary: BaseException | None = None
        try:
            _write(fd, content)
            os.fsync(fd)
        except BaseException as error:  # noqa: BLE001 - preserve primary close notes
            primary = error
        _finish_close(fd, primary)

    def copy_regular(
        self,
        source: Path,
        destination: Path,
        source_root: Path,
        destination_root: Path,
        *,
        expected_checksum: str | None = None,
    ) -> None:
        if not self._owns(destination_root):
            SHARED_IO.copy_regular(
                source,
                destination,
                source_root,
                destination_root,
                expected_checksum=expected_checksum,
            )
            return
        input_fd: int | None = None
        output_fd: int | None = None
        primary: BaseException | None = None
        try:
            if self._owns(source_root):
                input_fd = self._open(source, _FILE_READ_FLAGS)
            else:
                input_fd = open_file_no_follow(source, containment_root=source_root)
            self._mkdir(destination.parent)
            output_fd = self._open(destination, _FILE_EXCL_FLAGS, 0o644)
            digest = hashlib.sha256()
            while chunk := os.read(input_fd, _COPY_CHUNK):
                digest.update(chunk)
                _write(output_fd, chunk)
            os.fsync(output_fd)
            if expected_checksum is not None and not _assemble_fs.checksum_matches(
                expected_checksum, digest.hexdigest()
            ):
                raise ValueError(f"copied file checksum does not match: {source}")
        except ClaimLostError as error:
            primary = ValueError(str(error))
            primary.__cause__ = error
        except BaseException as error:  # noqa: BLE001 - preserve primary close notes
            primary = error
        close_error = _close_optional(output_fd)
        input_close = _close_optional(input_fd)
        if close_error is None:
            close_error = input_close
        elif input_close is not None:
            close_error.add_note(f"{_FILE_CLOSE_NOTE}: {input_close}")
        if primary is not None:
            if close_error is not None:
                primary.add_note(f"{_FILE_CLOSE_NOTE}: {close_error}")
            raise primary
        if close_error is not None:
            raise close_error

    def rename(
        self,
        source_parent: Path,
        source: str,
        target_parent: Path,
        target: str,
        root: Path,
        *,
        operation=None,
    ) -> None:
        if not self._owns(root):
            SHARED_IO.rename(
                source_parent,
                source,
                target_parent,
                target,
                root,
                operation=operation,
            )
            return
        self.require_named_root(root)
        _assemble_fs.component(source, "entry name")
        _assemble_fs.component(target, "entry name")
        source_fd: int | None = None
        dest_fd: int | None = None
        primary: BaseException | None = None
        try:
            source_fd = self._dir(source_parent)
            dest_fd = (
                source_fd
                if target_parent == source_parent
                else self._dir(target_parent)
            )
            try:
                os.stat(target, dir_fd=dest_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError(
                    f"destination already exists: {target_parent / target}"
                )
            os.rename(source, target, src_dir_fd=source_fd, dst_dir_fd=dest_fd)
        except ClaimLostError as error:
            primary = ValueError(str(error))
            primary.__cause__ = error
        except BaseException as error:  # noqa: BLE001 - preserve primary close notes
            primary = error
        close_error = None
        if dest_fd is not None and dest_fd != source_fd:
            close_error = _close_fd(dest_fd)
        source_close = _close_optional(source_fd)
        if close_error is None:
            close_error = source_close
        elif source_close is not None:
            close_error.add_note(f"{_FILE_CLOSE_NOTE}: {source_close}")
        if primary is not None:
            if close_error is not None:
                primary.add_note(f"{_FILE_CLOSE_NOTE}: {close_error}")
            raise primary
        if close_error is not None:
            raise close_error

    def clean(self, path: Path, work: Path) -> tuple[str, ...]:
        if not self._owns(work):
            return SHARED_IO.clean(path, work)
        try:
            os.fstat(self.root_fd)
            if path.parent != self.work:
                raise ValueError(f"staging cleanup path escapes work root: {path}")
            info = os.stat(path.name, dir_fd=self.root_fd, follow_symlinks=False)
            remove_tree_allow_symlinks(
                path.parent,
                path.name,
                containment_root=self.work,
                missing_ok=True,
                expected_root_identity=(info.st_dev, info.st_ino),
            )
        except FileNotFoundError:
            return ()
        except (OSError, SafeFilesystemError, ClaimLostError, ValueError) as error:
            return (f"staging cleanup failed for {path}: {error}",)
        return ()

    def iter_regular(
        self, path: Path, root: Path, *, chunk_size: int = _COPY_CHUNK
    ) -> Iterator[bytes]:
        stream = self._iter_regular(path, root, chunk_size=chunk_size)
        self._generators.append(stream)
        return stream

    def _iter_regular(
        self, path: Path, root: Path, *, chunk_size: int
    ) -> Iterator[bytes]:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive.")
        if not self._owns(root):
            fd = open_file_no_follow(path, containment_root=root)
        else:
            try:
                fd = self._open(path, _FILE_READ_FLAGS)
            except ClaimLostError as error:
                raise ValueError(str(error)) from error
        self._streams.add(fd)
        primary: BaseException | None = None
        try:
            while chunk := os.read(fd, chunk_size):
                yield chunk
        except BaseException as error:  # noqa: BLE001 - preserve primary close notes
            primary = error
        close_error = _close_owned_stream(self._streams, fd)
        if primary is not None:
            if close_error is not None:
                primary.add_note(f"{_FILE_CLOSE_NOTE}: {close_error}")
            raise primary
        if close_error is not None:
            raise close_error

    def _owns(self, root: Path | None) -> bool:
        if root is None:
            return False
        try:
            Path(root).relative_to(self.work)
        except ValueError:
            return False
        return True

    def _dir(self, path: Path) -> int:
        if path == self.work:
            return os.dup(self.root_fd)
        return self._open(path, _DIR_FLAGS)

    def _mkdir(self, path: Path) -> None:
        if path == self.work:
            return
        fd = self.root_fd
        for part in _relative_parts(self._claim, path):
            try:
                next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, 0o755, dir_fd=fd)
                next_fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            previous = fd
            fd = next_fd
            _close_walk(previous, self.root_fd)
        _close_walk(fd, self.root_fd)

    def _open(self, path: Path, flags: int, mode: int = 0) -> int:
        parts = _relative_parts(self._claim, path)
        parent = _walk_to_parent(self.root_fd, parts, path=path)
        try:
            return os.open(parts[-1], flags, mode, dir_fd=parent)
        finally:
            _close_walk(parent, self.root_fd)


def _write(fd: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while staging assembly output")
        view = view[written:]


def _close_fd(fd: int) -> OSError | None:
    try:
        os.close(fd)
    except OSError as error:
        return error
    return None


def _close_optional(fd: int | None) -> OSError | None:
    if fd is None:
        return None
    return _close_fd(fd)


def _close_owned_stream(owned: set[int], fd: int) -> OSError | None:
    if fd not in owned:
        return None
    owned.discard(fd)
    return _close_fd(fd)


def _finish_close(fd: int, primary: BaseException | None = None) -> None:
    close_error = _close_fd(fd)
    if primary is not None:
        if close_error is not None:
            primary.add_note(f"{_FILE_CLOSE_NOTE}: {close_error}")
        raise primary
    if close_error is not None:
        raise close_error


def discover_variant_tree(
    root: Path, project: str
) -> tuple[list[Path], list[tuple[Path, Path]]]:
    _assemble_fs.directory(root, None, create=False)
    for path in (root / f"{project}.cfg.ic", root / f"{project}.para"):
        _assemble_fs.regular(path, None)
    dirs: list[Path] = [Path(".")]
    files: list[tuple[Path, Path]] = []
    pending = [(Path("."), root)]
    while pending:
        relative, directory = pending.pop()
        for name in sorted(
            list_directory_no_follow(directory, containment_root=None), reverse=True
        ):
            _assemble_fs.component(name, "variant entry")
            path = directory / name
            child = Path(name) if relative == Path(".") else relative / name
            mode = stat_no_follow(path, containment_root=None).st_mode
            if stat.S_ISDIR(mode):
                dirs.append(child)
                pending.append((child, path))
            elif stat.S_ISREG(mode):
                files.append((child, path))
            else:
                raise ValueError(f"variant contains a non-regular entry: {path}")
    return sorted(dirs), sorted(files)
