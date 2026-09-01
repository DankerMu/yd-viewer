"""Requirement-driven tests for checksum-bound forcing package consumption."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from assembly_fixtures import (
    CSV_ONE,
    INDEX,
    digest,
    forcing_package_key,
    prepared,
    sources,
    write_forcing_package,
)

from yd_producer import _assemble_fs
from yd_producer.assemble import AssemblyError, assemble
from yd_producer.store.object_store import MAX_OBJECT_MANIFEST_BYTES, LocalObjectStore


def _refuse(
    prepared_inputs, forcing, *, snapshot: bool = True, phase: str = "validate"
) -> AssemblyError:
    _value, work, registry, variant, states, state, _forcing = prepared_inputs
    before = sources(variant, states, registry.object_store_root)
    with pytest.raises(AssemblyError) as captured:
        assemble(
            registry=registry,
            variant_dir=variant,
            forcing=forcing,
            states_root=states,
            state_path=state,
        )
    assert captured.value.phase == phase
    assert not (work / "model").exists()
    if snapshot:
        assert sources(variant, states, registry.object_store_root) == before
    return captured.value


def test_canonical_only_and_legacy_only_indexes_succeed(tmp_path: Path) -> None:
    prepared_inputs = prepared(tmp_path)
    value, work, registry, variant, states, state, _forcing = prepared_inputs
    canonical = write_forcing_package(registry.object_store_root, value)
    result = assemble(
        registry=registry,
        variant_dir=variant,
        forcing=canonical,
        states_root=states,
        state_path=state,
    )
    assert result.forcing_index_path.read_bytes() == INDEX
    (work / "model").rename(work / "model-canonical")
    legacy = write_forcing_package(
        registry.object_store_root,
        value,
        index_relative="shud/qhh.tsd.forc",
    )
    result = assemble(
        registry=registry,
        variant_dir=variant,
        forcing=legacy,
        states_root=states,
        state_path=state,
    )
    assert result.forcing_index_path.read_bytes() == INDEX


def test_forcing_status_checksum_and_identity_rejections(tmp_path: Path) -> None:
    prepared_inputs = prepared(tmp_path)
    value, _work, registry, _variant, _states, _state, forcing = prepared_inputs
    _refuse(prepared_inputs, replace(forcing, status="failed"))
    _refuse(prepared_inputs, replace(forcing, status=1))
    _refuse(prepared_inputs, replace(forcing, checksum=None))
    _refuse(prepared_inputs, replace(forcing, checksum=b"no"))
    _refuse(prepared_inputs, replace(forcing, checksum="deadbeef"))
    _refuse(prepared_inputs, replace(forcing, file_uris="no"))
    _refuse(prepared_inputs, replace(forcing, file_uris={}))
    _refuse(
        prepared_inputs,
        replace(forcing, file_uris={"package_manifest": 1}),
    )
    _refuse(
        prepared_inputs,
        replace(
            forcing,
            file_uris={
                "package_manifest": forcing.file_uris["package_manifest"]
                + "/../escape.json"
            },
        ),
    )
    _refuse(
        prepared_inputs,
        replace(forcing, forcing_package_uri="forcing/gfs/other/basin_v1/demo_model/"),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            mutate_manifest=lambda manifest: manifest.update(source_id="ifs"),
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            mutate_manifest=lambda manifest: manifest.update(
                cycle_time="2026-05-07T12:00:00Z"
            ),
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            mutate_manifest=lambda manifest: manifest.update(model_id="other"),
        ),
    )
    _refuse(
        prepared_inputs,
        replace(forcing, forcing_version_id="other"),
    )


def test_forcing_json_malformed_oversize_deep_and_wide_fail(tmp_path: Path) -> None:
    prepared_inputs = prepared(tmp_path)
    value, _work, registry, *_rest = prepared_inputs
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root, value, raw_manifest=b"{not-json"
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root, value, raw_manifest=b"\xff\xfe"
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            raw_manifest=b"{" + b'"k":' * 70 + b"1" + b"}" * 70,
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            raw_manifest=b"[" + b"1," * 250_001 + b"1]",
        ),
        snapshot=False,
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            raw_manifest=b"{" + b"0" * (MAX_OBJECT_MANIFEST_BYTES + 1) + b"}",
        ),
        snapshot=False,
    )


def test_index_role_matrix_and_csv_shape_rejections(tmp_path: Path) -> None:
    prepared_inputs = prepared(tmp_path)
    value, _work, registry, *_rest = prepared_inputs
    _refuse(
        prepared_inputs,
        write_forcing_package(registry.object_store_root, value, include_legacy=True),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            mutate_manifest=lambda manifest: manifest.update(
                files=[
                    item for item in manifest["files"] if item["role"] != "shud_forcing"
                ]
            ),
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root, value, include_debug_index=True
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(registry.object_store_root, value, index_role="debug"),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            extra_files=[
                {
                    "role": "shud_forcing_csv",
                    "relative_path": "shud/nested/X3.csv",
                    "uri": f"forcing/{value.source_id}/{value.cycle_time:%Y%m%d%H}/{value.basin_version_id}/{value.model_id}/shud/nested/X3.csv",
                    "checksum": "deadbeef",
                }
            ],
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            extra_files=[
                {
                    "role": "shud_forcing_csv",
                    "relative_path": "shud/x1.csv",
                    "uri": f"forcing/{value.source_id}/{value.cycle_time:%Y%m%d%H}/{value.basin_version_id}/{value.model_id}/shud/x1.csv",
                    "checksum": "deadbeef",
                }
            ],
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root, value, index_checksum="deadbeef"
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root, value, csv_one_checksum="deadbeef"
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            mutate_manifest=lambda manifest: manifest["files"][2].update(
                relative_path="shud/not-declared.csv",
                uri=manifest["files"][2]["uri"].replace("X2.csv", "not-declared.csv"),
            ),
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            extra_files=[
                {
                    "role": "shud_forcing_csv",
                    "relative_path": "shud/X3.csv",
                    "uri": "../escape.csv",
                    "checksum": "deadbeef",
                }
            ],
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            extra_files=[
                {
                    "role": "shud_forcing_csv",
                    "relative_path": "shud/X3.csv",
                    "uri": f"forcing/{value.source_id}/{value.cycle_time:%Y%m%d%H}/{value.basin_version_id}/{value.model_id}/shud/X3.csv",
                    "checksum": 1,
                }
            ],
        ),
    )


def test_index_filename_set_and_undeclared_outside_objects_are_not_scanned(
    tmp_path: Path,
) -> None:
    prepared_inputs = prepared(tmp_path)
    value, work, registry, _variant, _states, _state, _forcing = prepared_inputs
    undeclared = {"outside.bin": b"do-not-read"}
    forcing = write_forcing_package(
        registry.object_store_root,
        value,
        undeclared=undeclared,
        mutate_manifest=lambda manifest: manifest["files"][0].update(checksum="0" * 64),
    )
    before = (registry.object_store_root / "forcing").joinpath(
        value.source_id,
        f"{value.cycle_time:%Y%m%d%H}",
        value.basin_version_id,
        value.model_id,
        "outside.bin",
    )
    marker = before.read_bytes()
    _refuse(prepared_inputs, forcing, snapshot=False)
    assert before.read_bytes() == marker
    assert not (work / "model").exists()
    forcing = write_forcing_package(
        registry.object_store_root,
        value,
        index=b"2 20260507\nshud\nID\tLon\tLat\tX\tY\tZ\tFilename\n1\t1\t2\t3\t4\t5\tX1.csv\n",
    )
    _refuse(prepared_inputs, forcing)
    forcing = write_forcing_package(
        registry.object_store_root,
        value,
        index=INDEX.replace(b"X1.csv", b"X1.csv\n1\t1\t2\t3\t4\t5\tX1.csv"),
    )
    _refuse(prepared_inputs, forcing)
    forcing = write_forcing_package(
        registry.object_store_root,
        value,
        index=b"no-header\n1\t1\t2\t3\t4\t5\tX1.csv\n",
    )
    _refuse(prepared_inputs, forcing)
    forcing = write_forcing_package(
        registry.object_store_root,
        value,
        extra_files=[],
        mutate_manifest=lambda manifest: manifest["files"].__setitem__(
            1,
            {
                "role": "shud_forcing_csv",
                "relative_path": "shud/X1.csv",
                "uri": manifest["files"][1]["uri"],
                "checksum": manifest["files"][1]["checksum"],
            },
        ),
    )
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root,
            value,
            csv_one=CSV_ONE,
            mutate_manifest=lambda manifest: manifest.update(
                files=[manifest["files"][0], manifest["files"][1]]
            ),
        ),
    )


def test_10001_csv_declarations_rejected_before_member_read(
    tmp_path: Path, monkeypatch
) -> None:
    prepared_inputs = prepared(tmp_path)
    value, work, registry, _variant, _states, _state, forcing = prepared_inputs
    prefix = forcing_package_key(value) + "/"
    extra = [
        {
            "role": "shud_forcing_csv",
            "relative_path": f"shud/S{i:05d}.csv",
            "uri": f"{prefix}shud/S{i:05d}.csv",
            "checksum": "0" * 64,
        }
        for i in range(10001 - 2)
    ]
    forcing = write_forcing_package(
        registry.object_store_root,
        value,
        extra_files=extra,
    )
    reads: list[str] = []
    original = LocalObjectStore.iter_bytes

    def spying(self, key_or_uri, *, chunk_size=1024 * 1024):
        reads.append(key_or_uri)
        return original(self, key_or_uri, chunk_size=chunk_size)

    monkeypatch.setattr(LocalObjectStore, "iter_bytes", spying)
    error = _refuse(prepared_inputs, forcing)
    assert error.phase == "validate"
    assert not list(work.glob(".model.assemble-stage-*"))
    assert not [uri for uri in reads if "/shud/" in uri]


def test_package_identity_result_uri_must_equal_derived_prefix(
    tmp_path: Path,
) -> None:
    prepared_inputs = prepared(tmp_path)
    value, _work, registry, _variant, _states, _state, _forcing = prepared_inputs
    outside = write_forcing_package(
        registry.object_store_root,
        value,
        mutate_result=lambda result: __import__("dataclasses").replace(
            result, forcing_package_uri="forcing/ifs/2026050700/basin_v1/demo_model/"
        ),
    )
    error = _refuse(prepared_inputs, outside)
    assert "forcing_package_uri differs" in str(error)


def test_package_identity_manifest_key_sibling_rejected(tmp_path: Path) -> None:
    prepared_inputs = prepared(tmp_path)
    value, work, registry, variant, states, state, _forcing = prepared_inputs
    sibling = write_forcing_package(
        registry.object_store_root,
        value,
        mutate_result=lambda result: __import__("dataclasses").replace(
            result,
            file_uris={
                "package_manifest": f"forcing/{value.source_id}/2026050700/other/model/forcing_package.json"
            },
        ),
    )
    before = sources(variant, states, registry.object_store_root)
    with pytest.raises(AssemblyError) as captured:
        assemble(
            registry=registry,
            variant_dir=variant,
            forcing=sibling,
            states_root=states,
            state_path=state,
        )
    assert captured.value.phase == "validate"
    assert "package manifest must be within" in str(captured.value)
    assert not (work / "model").exists()
    assert sources(variant, states, registry.object_store_root) == before


def test_package_identity_member_uri_wrong_prefix_rejected(tmp_path: Path) -> None:
    prepared_inputs = prepared(tmp_path)
    value, work, registry, variant, states, state, _forcing = prepared_inputs
    wrong_member = write_forcing_package(
        registry.object_store_root,
        value,
        mutate_manifest=lambda manifest: manifest["files"][1].update(
            uri=manifest["files"][1]["uri"].replace(
                f"{value.basin_version_id}/{value.model_id}/shud/X1.csv",
                f"{value.basin_version_id}/other/shud/X1.csv",
            )
        ),
    )
    before = sources(variant, states, registry.object_store_root)
    with pytest.raises(AssemblyError) as captured:
        assemble(
            registry=registry,
            variant_dir=variant,
            forcing=wrong_member,
            states_root=states,
            state_path=state,
        )
    assert captured.value.phase == "validate"
    assert "SHUD member URI differs" in str(captured.value)
    assert not (work / "model").exists()
    assert sources(variant, states, registry.object_store_root) == before


def test_index_parser_rejects_undeclared_row_without_consuming_later_chunks() -> None:
    header = b"ID\tLon\tLat\tX\tY\tZ\tFilename\n"
    first_undeclared = b"1\t1\t2\t3\t4\t5\tA1.csv\n"
    many = [f"{i}\t1\t2\t3\t4\t5\tB{i}.csv\n".encode() for i in range(1, 20_000)]
    content = b"".join([header, first_undeclared, *many])
    digest_value = __import__("hashlib").sha256(content).hexdigest()
    total_chunks = (len(content) + 63) // 64
    reads = {"count": 0}

    def counting_chunks():
        buf = bytearray()
        for offset in range(0, len(content), 64):
            buf[:] = content[offset : offset + 64]
            reads["count"] += 1
            yield bytes(buf)

    with pytest.raises(ValueError) as captured:
        _assemble_fs.parse_shud_index_stream(
            counting_chunks(), digest_value, ["X1.csv", "X2.csv"]
        )
    assert "not declared" in str(captured.value)
    assert reads["count"] <= 2, (
        f"parser consumed {reads['count']}/{total_chunks} chunks before rejecting"
    )
    assert reads["count"] < total_chunks


@pytest.mark.parametrize(
    "line_ending, file_ending",
    [("\n", "\n"), ("\r\n", "\r\n"), ("\n", "")],
)
def test_index_parser_exact_header_accepts_literal_and_preserves_endings(
    line_ending: str, file_ending: str
) -> None:
    # The two preamble lines are emitted by #14; header must be the exact
    # literal, and CRLF/LF/no-final-newline must all be accepted.
    header = "ID\tLon\tLat\tX\tY\tZ\tFilename"
    row = "1\t1\t2\t3\t4\t5\tX1.csv"
    content = ("2 20260507\nshud\n" + header + line_ending + row + file_ending).encode()
    names = _assemble_fs.parse_shud_index_stream(
        iter([content]), digest(content), ["X1.csv"]
    )
    assert names == ["X1.csv"]


@pytest.mark.parametrize(
    "bad_header",
    [
        # Suffix extra: seven fields but a non-literal suffix after the
        # exact prefix.
        b"ID\tLon\tLat\tX\tY\tZ\tFilenameX\n",
        # Prefix wrong at the very first character.
        b"IDz\tLon\tLat\tX\tY\tZ\tFilename\n",
        # Truncated final field name.
        b"ID\tLon\tLat\tX\tY\tZ\tFilena\n",
        # Extra field after the exact header.
        b"ID\tLon\tLat\tX\tY\tZ\tFilename\tExtra\n",
        # Extra trailing tab.
        b"ID\tLon\tLat\tX\tY\tZ\tFilename\t\n",
    ],
)
def test_index_parser_rejects_non_literal_header(bad_header: bytes) -> None:
    # A data row follows, so the parser rejects the malformed header as a
    # data row before the station header.
    content = bad_header + b"1\t1\t2\t3\t4\t5\tX1.csv\n"
    with pytest.raises(ValueError) as captured:
        _assemble_fs.parse_shud_index_stream(
            iter([content]), digest(content), ["X1.csv"]
        )
    assert "data row before the station header" in str(captured.value)


def test_index_parser_consumes_reused_chunks_without_joining() -> None:
    seen = {"count": 0}
    buf = bytearray()

    def chunks():
        content = INDEX
        for offset in range(0, len(content), 7):
            buf[:] = content[offset : offset + 7]
            seen["count"] += 1
            yield buf

    names = _assemble_fs.parse_shud_index_stream(
        chunks(), digest(INDEX), ["X1.csv", "X2.csv"]
    )
    assert seen["count"] > 1
    assert names == ["X1.csv", "X2.csv"]


def test_finite_state_parser_no_line_buffering_tracemalloc() -> None:
    import tracemalloc

    prefix = b"ID\tLon\tLat\tX\tY\tZ\tFilename\n1\t"
    suffix = b"\t2\t3\t4\t5\tX1.csv\n"
    total_middle = 32 * 1024 * 1024

    def big_chunks():
        yield prefix
        remaining = total_middle
        buf = bytearray(b"2" * 64 * 1024)
        while remaining > 0:
            take = min(len(buf), remaining)
            yield buf
            remaining -= take
        yield suffix

    digest_value = __import__("hashlib").sha256()
    for chunk in big_chunks():
        digest_value.update(chunk)
    tracemalloc.start()
    try:
        names = _assemble_fs.parse_shud_index_stream(
            big_chunks(), digest_value.hexdigest(), ["X1.csv", "X2.csv"]
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert names == ["X1.csv"]
    assert peak < 4 * 1024 * 1024, f"peak={peak}"


def test_index_utf8_split_across_chunks_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    prepared_inputs = prepared(tmp_path)
    value, work, registry, _variant, _states, _state, _forcing = prepared_inputs
    broken = b"\xc3" + INDEX[1:]
    forcing = write_forcing_package(registry.object_store_root, value, index=broken)

    def split_invalid(self, key_or_uri, *, chunk_size=1024 * 1024):
        content = Path(self.resolve_path(key_or_uri)).read_bytes()
        if content.startswith(b"\xc3"):
            yield content[:1]
            yield content[1:]
            return
        yield content

    monkeypatch.setattr(LocalObjectStore, "iter_bytes", split_invalid)
    error = _refuse(prepared_inputs, forcing)
    assert error.phase == "validate"
    assert not list(work.glob(".model.assemble-stage-*"))


def test_wrong_csv_checksum_is_rejected_before_any_staging_write(
    tmp_path: Path, monkeypatch
) -> None:
    prepared_inputs = prepared(tmp_path)
    value, work, registry, _variant, _states, _state, _forcing = prepared_inputs
    writes: list[str] = []
    original_write = __import__(
        "yd_producer._assemble_fs", fromlist=["write_new"]
    ).write_new
    original_copy = __import__(
        "yd_producer._assemble_fs", fromlist=["copy_regular"]
    ).copy_regular
    original_dir = __import__(
        "yd_producer._assemble_fs", fromlist=["directory"]
    ).directory

    def spy_write(path, content, root):
        writes.append(f"write:{path}")
        return original_write(path, content, root)

    def spy_copy(*args, **kwargs):
        writes.append("copy")
        return original_copy(*args, **kwargs)

    def spy_dir(path, root, *, create):
        if create:
            writes.append(f"mkdir:{path}")
        return original_dir(path, root, create=create)

    monkeypatch.setattr("yd_producer._assemble_fs.write_new", spy_write)
    monkeypatch.setattr("yd_producer._assemble_fs.copy_regular", spy_copy)
    monkeypatch.setattr("yd_producer._assemble_fs.directory", spy_dir)
    _refuse(
        prepared_inputs,
        write_forcing_package(
            registry.object_store_root, value, csv_one_checksum="deadbeef"
        ),
    )
    assert writes == []
    assert not list(work.glob(".model.assemble-stage-*"))
