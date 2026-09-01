"""Private no-follow filesystem primitives for work-local assembly."""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from yd_producer.forcing.bounded_json import BoundedJSONError, load_bounded_json
from yd_producer.store.safe_fs import (
    SafeFilesystemError,
    ensure_directory_no_follow,
    open_directory_no_follow,
    open_file_no_follow,
    read_bytes_limited_no_follow,
    remove_tree_allow_symlinks,
    rename_entry_no_follow,
)

_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_CSV = re.compile(r"^[A-Za-z0-9_.-]+\.csv$")
_INDEX_HEADER = "ID\tLon\tLat\tX\tY\tZ\tFilename"
_COPY_CHUNK = 1024 * 1024


def absolute(value: Path | str, label: str) -> Path:
    if not isinstance(value, Path | str):
        raise TypeError(f"{label} must be a path.")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} must be absolute.")
    return path


def component(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or value.startswith("-")
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or _COMPONENT.fullmatch(value) is None
    ):
        raise ValueError(f"{label} must be a safe single ASCII path component.")


def directory(path: Path, root: Path | None, *, create: bool) -> None:
    if create:
        ensure_directory_no_follow(path, containment_root=root)
    else:
        fd = open_directory_no_follow(path, containment_root=root)
        os.close(fd)


def regular(path: Path, root: Path | None) -> None:
    fd = open_file_no_follow(path, containment_root=root)
    os.close(fd)


def read_limited(path: Path, maximum: int, root: Path | None) -> bytes:
    content = read_bytes_limited_no_follow(
        path, max_bytes=maximum, containment_root=root
    )
    if len(content) > maximum:
        raise ValueError(f"file exceeds the {maximum} byte limit: {path}")
    return content


def absent(parent: Path, name: str, root: Path) -> None:
    component(name, "entry name")
    fd = open_directory_no_follow(parent, containment_root=root)
    try:
        try:
            os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise ValueError(f"destination already exists: {parent / name}")
    finally:
        os.close(fd)


def rename(
    source_parent: Path,
    source: str,
    target_parent: Path,
    target: str,
    root: Path,
    *,
    operation=rename_entry_no_follow,
) -> None:
    absent(target_parent, target, root)
    operation(source_parent, source, target_parent, target, containment_root=root)


def clean(path: Path, work: Path) -> tuple[str, ...]:
    try:
        remove_tree_allow_symlinks(
            path.parent, path.name, containment_root=work, missing_ok=True
        )
    except (OSError, SafeFilesystemError) as error:
        return (f"staging cleanup failed for {path}: {error}",)
    return ()


def json_bytes(value) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def json_object(content: bytes, label: str) -> dict:
    try:
        value = load_bounded_json(content, max_bytes=len(content))
    except BoundedJSONError as error:
        raise ValueError(f"{label} must be JSON.") from error
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object.")
    return value


def checksum(expected: str, content: bytes, label: str) -> None:
    if normalize_checksum(expected) != hashlib.sha256(content).hexdigest():
        raise ValueError(f"{label} checksum does not match its bytes.")


def stream_checksum(chunks: Iterable[bytes], expected: str, label: str) -> None:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
    if not checksum_matches(expected, digest.hexdigest()):
        raise ValueError(f"{label} checksum does not match its bytes.")


def parse_shud_index_stream(
    chunks: Iterable[bytes],
    expected_checksum: str,
    declared_names: Sequence[str],
) -> list[str]:
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    parser = _IndexParser(
        max_len=max((len(name) for name in declared_names), default=0),
        declared_names=declared_names,
    )
    try:
        for chunk in chunks:
            digest.update(chunk)
            parser.feed(decoder.decode(chunk, False))
        parser.feed(decoder.decode(b"", True), final=True)
    except UnicodeDecodeError as error:
        raise ValueError("SHUD index must be strict UTF-8.") from error
    if not checksum_matches(expected_checksum, digest.hexdigest()):
        raise ValueError("SHUD index checksum does not match its bytes.")
    names = parser.names
    if parser.header_count != 1:
        raise ValueError("SHUD index must have exactly one station header.")
    if not names or len(names) != len(set(names)):
        raise ValueError("SHUD index must list a non-empty unique filename set.")
    return names


class _IndexParser:
    """Finite-state station-index parser: header, then rows of seven fields.

    Per row only the current field is retained (field 0 digit count, field 6
    filename prefix capped at the declared maximum); numeric and geometry
    fields are discarded as they stream. No whole-line or whole-file buffer.
    """

    def __init__(self, *, max_len: int, declared_names: Sequence[str]) -> None:
        self.max_len = max_len
        self.declared = frozenset(declared_names)
        self.seen: set[str] = set()
        self.header_count = 0
        self.after_header = False
        self.names: list[str] = []
        self._reset_row()
        self._header_match = 0
        self._header_ok = True

    def feed(self, text: str, *, final: bool = False) -> None:
        for char in text:
            if char == "\r":
                self._pending_cr = True
                continue
            if self._pending_cr:
                self._pending_cr = False
                if self._started:
                    self._end_row()
                if char == "\n":
                    continue
            if char == "\n":
                if self._started:
                    self._end_row()
                continue
            self._consume(char)
        if final:
            if self._pending_cr:
                self._pending_cr = False
                if self._started:
                    self._end_row()
            elif self._started:
                self._end_row()

    def _consume(self, char: str) -> None:
        self._started = True
        if self._header_ok:
            if self._header_match < len(_INDEX_HEADER):
                if char == _INDEX_HEADER[self._header_match]:
                    self._header_match += 1
                else:
                    self._header_ok = False
            else:
                # The literal header prefix matched exactly; any further
                # character in this row means the row is not the exact header.
                self._header_ok = False
        if char == "\t":
            if self._field_index >= 6:
                self._extra = True
            self._end_field()
            return
        if self._field_index == 0:
            if char.isascii() and char in "0123456789":
                self._id_count += 1
            else:
                self._id_ok = False
        elif self._field_index == 6:
            if (
                self._name_ok
                and len(self._name) < self.max_len
                and _COMPONENT.fullmatch(char)
            ):
                self._name.append(char)
            else:
                self._name_ok = False
        elif self._field_index > 6:
            self._extra = True

    def _end_field(self) -> None:
        if self._field_index == 0:
            self._id_valid = self._id_ok and self._id_count > 0
        elif self._field_index == 6:
            self._row_name = "".join(self._name)
        self._field_index += 1

    def _end_row(self) -> None:
        if self._field_index < 7:
            self._end_field()
        fields = self._field_index
        is_header = (
            self._header_ok
            and self._header_match == len(_INDEX_HEADER)
            and fields == 7
            and not self._extra
        )
        if is_header:
            if self.after_header:
                raise ValueError("SHUD index must have exactly one station header.")
            self.header_count += 1
            self.after_header = True
            self._reset_row()
            return
        if self.after_header:
            if fields != 7 or self._extra:
                raise ValueError("SHUD index has an invalid station row.")
            if (
                not self._id_valid
                or not self._row_name
                or _CSV.fullmatch(self._row_name) is None
            ):
                raise ValueError("SHUD index has an invalid station row.")
            if self._row_name in self.seen:
                raise ValueError(
                    "SHUD index must list a non-empty unique filename set."
                )
            if self._row_name not in self.declared:
                raise ValueError("SHUD index names a CSV not declared by the manifest.")
            self.seen.add(self._row_name)
            self.names.append(self._row_name)
            self._reset_row()
            return
        if fields == 7 and not self._extra:
            raise ValueError("SHUD index has a data row before the station header.")
        self._reset_row()

    def _reset_row(self) -> None:
        self._started = False
        self._field_index = 0
        self._id_ok = True
        self._id_count = 0
        self._id_valid = False
        self._name: list[str] = []
        self._name_ok = True
        self._row_name = ""
        self._extra = False
        self._pending_cr = False
        self._header_match = 0
        self._header_ok = True


def checksum_matches(expected: str, actual: str) -> bool:
    try:
        return normalize_checksum(expected) == actual.strip().lower()
    except (AttributeError, TypeError, ValueError):
        return False


def normalize_checksum(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("checksum must be a non-empty string.")
    return value.strip().lower().removeprefix("sha256:")


def write_new(path: Path, content: bytes, root: Path) -> None:
    directory(path.parent, root, create=True)
    parent_fd = open_directory_no_follow(path.parent, containment_root=root)
    try:
        fd = os.open(
            path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o644,
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)
    try:
        _write(fd, content)
        os.fsync(fd)
    finally:
        os.close(fd)


def copy_regular(
    source: Path,
    destination: Path,
    source_root: Path,
    destination_root: Path,
    *,
    expected_checksum: str | None = None,
) -> None:
    input_fd = open_file_no_follow(source, containment_root=source_root)
    output_fd: int | None = None
    try:
        directory(destination.parent, destination_root, create=True)
        parent_fd = open_directory_no_follow(
            destination.parent, containment_root=destination_root
        )
        try:
            output_fd = os.open(
                destination.name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o644,
                dir_fd=parent_fd,
            )
        finally:
            os.close(parent_fd)
        digest = hashlib.sha256()
        while chunk := os.read(input_fd, _COPY_CHUNK):
            digest.update(chunk)
            _write(output_fd, chunk)
        os.fsync(output_fd)
        if expected_checksum is not None and not checksum_matches(
            expected_checksum, digest.hexdigest()
        ):
            raise ValueError(f"copied file checksum does not match: {source}")
    finally:
        if output_fd is not None:
            os.close(output_fd)
        os.close(input_fd)


def _write(fd: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write while staging assembly output")
        view = view[written:]
