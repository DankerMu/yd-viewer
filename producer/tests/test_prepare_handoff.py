"""Prepared-variant direct-grid handoff loader and prepare staging evidence (#171)."""

from __future__ import annotations

import dataclasses
import errno
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import shutil
from pathlib import Path
from types import MappingProxyType

import pytest
from assembly_fixtures import consume_in_process, consumer_builder
from cfg_ic_fixtures import build_cfg_ic
from prepare_fixtures import (
    CONTRACT_KEYS,
    ENVELOPE_KEYS,
    FIXED_NAMES,
    GFS,
    GFS_GRID,
    HANDOFF_FIELDS,
    IFS,
    IFS_GRID,
    LOADER_FIELDS,
    PREPARE_PARENT_ENTRIES,
    SYNTHETIC_PROJECT_NAME,
    VARIANT_HANDOFF_NAME,
    RenameProbe,
    VariantScript,
    alternate_project_builder,
    assert_untouched,
    binding_bytes,
    canonical_json_bytes,
    inject_copy_growth,
    inject_early_root_drift,
    inject_entry_fault,
    inject_mutable_parser,
    inject_read_fault,
    inject_staging_drift,
    inject_state_point_of_use,
    make_builder,
    make_env,
    run,
    sha256_literal,
    sp_att_bytes,
    station_payload,
    synthetic_variant_ids,
    tree_snapshot,
    variant_asset_name,
)
from prepare_fixtures import envelope_fixture as _envelope
from prepare_fixtures import handoff_error as _error
from prepare_fixtures import handoff_module as _module
from prepare_fixtures import load_handoff as _load
from prepare_fixtures import refuse_handoff as _refuse
from prepare_fixtures import variant_fixture as _variant

from yd_producer import prepare as prepare_module
from yd_producer.forcing.bounded_json import (
    BoundedJSONError,
)
from yd_producer.forcing.direct_grid_contract import (
    REQUIRED_STATION_FIELDS,
    DirectGridContractError,
    parse_direct_grid_forcing_contract,
)
from yd_producer.prepare import (
    VARIANT_BINDING_NAME,
    VARIANT_CALIBRATED_STATE_NAME,
    VARIANT_HYDRO_PARAM_NAME,
    PrepareError,
)
from yd_producer.store import safe_fs
from yd_producer.store.object_store import MAX_OBJECT_MANIFEST_BYTES


def test_public_structure_is_exact():
    module = _module()
    filenames = {
        "CALIBRATED_STATE_FILENAME": "yd.cfg.ic",
        "PARAMETER_FILENAME": "yd.cfg.para",
        "BINDING_FILENAME": "yd.binding",
        "HANDOFF_FILENAME": "yd.direct-grid-handoff.json",
        "HANDOFF_SCHEMA": "yd.prepare.direct-grid-handoff.v2",
    }
    expected = {"PREPARED_VARIANT_" + key for key in filenames}
    expected.update(
        (
            "MAX_PREPARED_VARIANT_MANIFEST_BYTES",
            "MAX_PREPARED_VARIANT_ASSET_BYTES",
            "PreparedVariantHandoffError",
            "PreparedVariantHandoff",
            "load_prepared_variant_handoff",
        )
    )
    assert set(module.__all__) == expected
    assert len(module.__all__) == len(expected)
    for suffix, literal in filenames.items():
        assert getattr(module, "PREPARED_VARIANT_" + suffix) == literal
    assert (
        module.MAX_PREPARED_VARIANT_MANIFEST_BYTES
        is module.MAX_PREPARED_VARIANT_ASSET_BYTES
        is MAX_OBJECT_MANIFEST_BYTES
    )
    assert issubclass(module.PreparedVariantHandoffError, ValueError)
    params = inspect.signature(module.PreparedVariantHandoff).parameters
    assert list(params) == HANDOFF_FIELDS
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())
    assert module.PreparedVariantHandoff.__dataclass_params__.frozen
    loader = inspect.signature(module.load_prepared_variant_handoff)
    assert list(loader.parameters) == LOADER_FIELDS
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY
        and p.default is inspect.Parameter.empty
        for p in loader.parameters.values()
    )
    assert loader.return_annotation == module.PreparedVariantHandoff
    assert prepare_module.VARIANT_REQUIRED_ENTRIES == frozenset(
        {"yd.cfg.ic", "yd.cfg.para", "yd.binding"}
    )
    assert (
        VARIANT_CALIBRATED_STATE_NAME,
        VARIANT_HYDRO_PARAM_NAME,
        VARIANT_BINDING_NAME,
    ) == ("yd.cfg.ic", "yd.cfg.para", "yd.binding")


def test_valid_loader_returns_independent_frozen_snapshot(tmp_path):
    binding = binding_bytes(grid_id=GFS_GRID, source_id=GFS)
    sp_att = sp_att_bytes(source_id=GFS)
    payload = _envelope()
    payload["direct_grid_forcing_contract"]["station_bindings"].append(
        station_payload(grid_id=GFS_GRID, index=2)
    )
    root = _variant(tmp_path, binding=binding, sp_att=sp_att, payload=payload)
    first = _load(root)
    ids = synthetic_variant_ids(GFS)
    assert first.source_id == GFS
    assert first.project_name == SYNTHETIC_PROJECT_NAME
    assert first.model_id == ids["model_id"]
    assert first.basin_id == ids["basin_id"]
    assert first.basin_version_id == ids["basin_version_id"]
    assert first.river_network_version_id == ids["river_network_version_id"]
    assert first.sp_att_asset_name == "yd.sp.att"
    assert first.binding_content == binding
    assert type(first.binding_content) is bytes
    assert first.sp_att_content == sp_att
    assert type(first.sp_att_content) is bytes
    assert first.contract.grid_id == GFS_GRID
    assert first.contract.applicable_source_ids == (GFS,)
    assert type(first.contract.stations) is tuple
    assert tuple(s.grid_cell_id for s in first.contract.stations) == (
        "cell-1",
        "cell-2",
    )
    assert isinstance(first.contract.stations[0].properties, MappingProxyType)
    second = _load(root)
    assert first == second
    assert first is not second
    assert first.contract is not second.contract
    assert first.contract.stations is not second.contract.stations


def test_deep_freeze_survives_caller_mutation(tmp_path):
    payload = _envelope()
    binding = binding_bytes(grid_id=GFS_GRID, source_id=GFS)
    sp_att = bytearray(sp_att_bytes(source_id=GFS))
    root = _variant(
        tmp_path,
        binding=binding,
        sp_att=bytes(sp_att),
        payload=payload,
    )
    snapshot = _load(root)
    payload["source_id"] = "ifs"
    payload["direct_grid_forcing_contract"]["grid_id"] = "mutated"
    payload["direct_grid_forcing_contract"]["station_bindings"][0]["station_id"] = "mut"
    payload["direct_grid_forcing_contract"]["station_bindings"][0]["properties"] = {
        "x": 1
    }
    sp_att.extend(b"tamper")
    assert snapshot.source_id == GFS
    assert snapshot.contract.grid_id == GFS_GRID
    assert snapshot.contract.stations[0].station_id == "station-1"
    assert dict(snapshot.contract.stations[0].properties) == {}
    assert snapshot.sp_att_content == sp_att_bytes(source_id=GFS)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.source_id = "ifs"
    with pytest.raises((TypeError, AttributeError)):
        snapshot.contract.stations[0].properties["k"] = "v"


def test_input_preflight_does_not_touch_filesystem(tmp_path, monkeypatch):
    module = _module()
    root = tmp_path / "variant"
    root.mkdir()

    def boom(*_args, **_kwargs):
        raise AssertionError("filesystem must not be called during preflight")

    monkeypatch.setattr(module, "list_directory_no_follow_limited", boom)
    monkeypatch.setattr(module, "read_bytes_limited_no_follow", boom)
    monkeypatch.setattr(module, "directory_identity_no_follow", boom)
    domains = {
        "variant_root": ["relative/variant", object()],
        "source_id": ["", "   ", 1, "unknown"],
        "project_name": [
            "",
            " ",
            1,
            "../yd",
            "-yd",
            "yd/name",
            "yd\\name",
            "yd\x00",
            "模型",
            "yd..x",
        ],
        "grid_id": ["", " ", 1],
        "max_manifest_bytes": [True, False, 1.5, "8", 0, -1],
        "max_asset_bytes": [True, False, 1.5, "8", 0, -1],
    }
    cases = [{field: value} for field, values in domains.items() for value in values]
    for kwargs in cases:
        call = {
            "source_id": GFS,
            "project_name": SYNTHETIC_PROJECT_NAME,
            "grid_id": GFS_GRID,
            "max_manifest_bytes": 64,
            "max_asset_bytes": 64,
        }
        call.update(kwargs)
        target = call.pop("variant_root", None)
        with pytest.raises(_error()):
            module.load_prepared_variant_handoff(
                variant_root=root if target is None else target,
                **call,
            )


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff{",
        b"{not-json",
        b"[]",
        b"null",
        b'{"schema_version":"yd.prepare.direct-grid-handoff.v2","schema_version":"x"}',
        canonical_json_bytes(_envelope())[:-1] + b" ",
        canonical_json_bytes(_envelope()) + b"\n",
    ],
    ids=["non-utf8", "malformed", "list", "null", "duplicate", "space", "newline"],
)
def test_noncanonical_json_is_rejected(tmp_path, raw):
    root = _variant(tmp_path, manifest_bytes=raw)
    _refuse(root)


def test_nan_and_infinity_are_rejected(tmp_path):
    for token in ("NaN", "Infinity", "-Infinity"):
        raw = canonical_json_bytes(_envelope()).replace(
            b'"direct_grid"', token.encode("ascii"), 1
        )
        _refuse(_variant(tmp_path / token, manifest_bytes=raw))


@pytest.mark.parametrize("key", ENVELOPE_KEYS)
def test_envelope_missing_key_is_rejected(tmp_path, key):
    payload = _envelope()
    del payload[key]
    _refuse(_variant(tmp_path, payload=payload))


@pytest.mark.parametrize("key", ENVELOPE_KEYS)
def test_envelope_unknown_and_wrong_type_are_rejected(tmp_path, key):
    payload = _envelope()
    payload["extra"] = "nope"
    _refuse(_variant(tmp_path / "extra", payload=payload))
    payload = _envelope()
    payload[key] = 1 if key != "direct_grid_forcing_contract" else []
    _refuse(_variant(tmp_path / f"type-{key}", payload=payload))
    if key != "direct_grid_forcing_contract":
        payload = _envelope()
        payload[key] = " "
        _refuse(_variant(tmp_path / f"blank-{key}", payload=payload))


@pytest.mark.parametrize("schema", ["yd.prepare.direct-grid-handoff.v0", "unknown"])
def test_unknown_schema_is_rejected(tmp_path, schema):
    _refuse(_variant(tmp_path, payload=_envelope(schema_version=schema)))


def test_nested_contract_shape_matrix(tmp_path):
    for key in (*CONTRACT_KEYS, "unknown"):
        payload = _envelope()
        contract = payload["direct_grid_forcing_contract"]
        if key == "unknown":
            contract[key] = 1
        else:
            del contract[key]
        _refuse(_variant(tmp_path / key, payload=payload))
    for key in (*REQUIRED_STATION_FIELDS, "extra", "properties"):
        payload = _envelope()
        row = payload["direct_grid_forcing_contract"]["station_bindings"][0]
        if key in {"extra", "properties"}:
            row[key] = {}
        else:
            del row[key]
        _refuse(_variant(tmp_path / ("station-" + key), payload=payload))
    for index, rows in enumerate(
        (
            {},
            [],
            ["row"],
            [station_payload(grid_id=GFS_GRID, index=i) for i in range(1, 10002)],
        )
    ):
        payload = _envelope()
        payload["direct_grid_forcing_contract"]["station_bindings"] = rows
        _refuse(
            _variant(tmp_path / str(index), payload=payload),
            max_manifest_bytes=8_000_000,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("forcing_mapping_mode", "idw"),
        ("shud_forcing_index", 2),
        ("forcing_filename", "../x.csv"),
        ("grid_id", "other-grid"),
        ("longitude", "nan"),
        ("grid_cell_id", "cell-1"),
    ],
    ids=["mode", "index", "filename", "station-grid", "coordinate", "cell"],
)
def test_parser_semantic_failures_are_rejected(tmp_path, field, value):
    payload = _envelope()
    contract = payload["direct_grid_forcing_contract"]
    if field == "forcing_mapping_mode":
        contract[field] = value
    else:
        row = contract["station_bindings"][0]
        if field == "grid_cell_id":
            row = station_payload(grid_id=GFS_GRID, index=2)
            contract["station_bindings"].append(row)
        row[field] = value
    cause = _refuse(_variant(tmp_path, payload=payload)).__cause__
    assert isinstance(cause, DirectGridContractError)
    assert cause.field == field
    if field == "grid_cell_id":
        assert cause.station_id == "station-2"
        assert cause.details == {"duplicate_grid_cell_id": "cell-1"}


@pytest.mark.parametrize(
    "value",
    [1, "", ".", "..", "-lead", "a/b", "a\\b", "a\x00b", "模型", "a..b", "-v"],
)
@pytest.mark.parametrize(
    "field_name",
    ["model_id", "basin_id", "basin_version_id", "river_network_version_id"],
)
def test_identifier_domain_is_rejected(tmp_path, field_name, value):
    _refuse(_variant(tmp_path, payload=_envelope(**{field_name: value})))


def test_identifier_safe_boundary_is_accepted(tmp_path):
    payload = _envelope(
        model_id="A",
        basin_id="z0",
        basin_version_id="A-z.0_9",
        river_network_version_id="9_.-x",
    )
    payload["direct_grid_forcing_contract"]["binding_uri"] = (
        "models/A/direct-grid/binding.json"
    )
    snapshot = _load(_variant(tmp_path, payload=payload))
    assert snapshot.model_id == "A"
    assert snapshot.basin_id == "z0"


def test_source_project_grid_and_singleton_mismatch_are_rejected(tmp_path):
    _refuse(_variant(tmp_path / "src", payload=_envelope(source_id=IFS)))
    _refuse(_variant(tmp_path / "proj", payload=_envelope(project_name="other")))
    payload = _envelope()
    payload["direct_grid_forcing_contract"]["grid_id"] = IFS_GRID
    payload["direct_grid_forcing_contract"]["station_bindings"][0]["grid_id"] = IFS_GRID
    _refuse(_variant(tmp_path / "grid", payload=payload))
    payload = _envelope()
    payload["direct_grid_forcing_contract"]["applicable_source_ids"] = [IFS]
    _refuse(_variant(tmp_path / "other-source", payload=payload))
    payload = _envelope()
    payload["direct_grid_forcing_contract"]["applicable_source_ids"] = [GFS, IFS]
    _refuse(_variant(tmp_path / "dual", payload=payload))
    snapshot = _load(_variant(tmp_path / "ok"))
    assert snapshot.contract.applicable_source_ids == (GFS,)


def test_d11_keys_are_exact_and_not_used_as_read_paths(tmp_path, monkeypatch):
    payload = _envelope()
    payload["direct_grid_forcing_contract"]["binding_uri"] = (
        "models/other/direct-grid/binding.json"
    )
    _refuse(_variant(tmp_path / "uri", payload=payload))
    payload = _envelope()
    payload["direct_grid_forcing_contract"]["sp_att_path"] = "input/other.sp.att"
    _refuse(_variant(tmp_path / "path", payload=payload))
    payload = _envelope(model_id="yd_gfs_model_x", project_name="ydx")
    payload["direct_grid_forcing_contract"]["binding_uri"] = (
        "models/yd_gfs_model/direct-grid/binding.json"
    )
    payload["direct_grid_forcing_contract"]["sp_att_path"] = "input/yd.sp.att"
    _refuse(_variant(tmp_path / "changed-ids", payload=payload, project_name="ydx"))
    module = _module()
    reads: list[str] = []
    real = module.read_bytes_limited_no_follow

    def recording(path, *, max_bytes, containment_root=None):
        reads.append(Path(path).name)
        return real(path, max_bytes=max_bytes, containment_root=containment_root)

    monkeypatch.setattr(module, "read_bytes_limited_no_follow", recording)
    root = _variant(tmp_path / "ok")
    _load(root)
    assert VARIANT_HANDOFF_NAME in reads
    assert VARIANT_BINDING_NAME in reads
    assert "yd.sp.att" in reads
    assert "binding.json" not in reads
    assert "gfs.sp.att" not in reads


@pytest.mark.parametrize(
    "asset",
    [
        "/tmp/x.sp.att",
        ".",
        "..",
        "a/b.sp.att",
        "a\\b.sp.att",
        "a\x00b.sp.att",
        "a..b.sp.att",
        "-lead.sp.att",
        "gfs.att",
        "gfs.sp.att",
        *(name for name in FIXED_NAMES if name != "yd.sp.att"),
    ],
)
def test_unsafe_asset_name_is_rejected_before_asset_read(tmp_path, monkeypatch, asset):
    module = _module()
    reads: list[str] = []
    real = module.read_bytes_limited_no_follow

    def recording(path, *, max_bytes, containment_root=None):
        reads.append(Path(path).name)
        return real(path, max_bytes=max_bytes, containment_root=containment_root)

    monkeypatch.setattr(module, "read_bytes_limited_no_follow", recording)
    payload = _envelope(sp_att_asset_name=asset)
    root = _variant(tmp_path, payload=payload, asset_name="yd.sp.att")
    _refuse(root)
    assert reads == [VARIANT_HANDOFF_NAME]


def test_second_sp_att_is_exact_set_refusal_not_scan(tmp_path):
    root = _variant(tmp_path, extra={"other.sp.att": b"second\n"})
    error = _refuse(root)
    assert "other.sp.att" in str(error)


@pytest.mark.parametrize("field_name", ["binding_checksum", "sp_att_checksum"])
@pytest.mark.parametrize(
    "value",
    [
        hashlib.sha256(b"x").hexdigest(),
        "SHA256:" + "a" * 64,
        "sha256:" + "a" * 63,
        "sha256:" + "g" * 64,
    ],
    ids=["no-prefix", "upper", "short", "nonhex"],
)
def test_checksum_grammar_is_rejected(tmp_path, field_name, value):
    payload = _envelope()
    payload["direct_grid_forcing_contract"][field_name] = value
    _refuse(_variant(tmp_path, payload=payload))


@pytest.mark.parametrize(
    "leg",
    [
        "binding-declaration",
        "sp-declaration",
        "binding-bytes",
        "sp-bytes",
        "utf8",
        "opaque",
    ],
)
def test_checksum_and_utf8_are_independent(tmp_path, leg):
    payload = _envelope()
    values = {}
    contract = payload["direct_grid_forcing_contract"]
    if leg.endswith("declaration"):
        field = "binding_checksum" if leg.startswith("binding") else "sp_att_checksum"
        contract[field] = sha256_literal(b"other")
    elif leg.endswith("bytes"):
        values["binding" if leg.startswith("binding") else "sp_att"] = b"tampered"
    else:
        key, field = (
            ("sp_att", "sp_att_checksum")
            if leg == "utf8"
            else ("binding", "binding_checksum")
        )
        content = b"\xff"
        values[key] = content
        contract[field] = sha256_literal(content)
        payload["file_checksums"][
            "yd.sp.att" if key == "sp_att" else VARIANT_BINDING_NAME
        ] = sha256_literal(content)
    root = _variant(tmp_path, payload=payload, **values)
    if leg == "opaque":
        assert _load(root).binding_content == b"\xff"
    else:
        _refuse(root)


def test_byte_depth_node_and_entry_caps(tmp_path, monkeypatch):
    module = _module()
    recorded = []
    real_list, real_read = (
        module.list_directory_no_follow_limited,
        module.read_bytes_limited_no_follow,
    )

    def listing(path, *, max_entries, containment_root=None):
        recorded.append(("list", max_entries))
        return real_list(
            path, max_entries=max_entries, containment_root=containment_root
        )

    def reading(path, *, max_bytes, containment_root=None):
        recorded.append((Path(path).name, max_bytes))
        assert containment_root == root
        return real_read(path, max_bytes=max_bytes, containment_root=containment_root)

    monkeypatch.setattr(module, "list_directory_no_follow_limited", listing)
    monkeypatch.setattr(module, "read_bytes_limited_no_follow", reading)
    root = _variant(tmp_path)
    size = (root / VARIANT_HANDOFF_NAME).stat().st_size
    _load(root, max_manifest_bytes=size, max_asset_bytes=128)
    assert recorded.count(("list", 14)) == 2
    names = sorted(item for item in recorded if item[0] != "list")
    expected = sorted(
        [(VARIANT_HANDOFF_NAME, size)]
        + [(name, 128) for name in FIXED_NAMES if name != VARIANT_HANDOFF_NAME]
    )
    assert names == expected
    _refuse(root, max_manifest_bytes=size - 1)
    (root / "extra.txt").write_bytes(b"x")
    _refuse(root)


def test_root_identity_and_entry_drift_are_rejected(tmp_path, monkeypatch):
    module = _module()
    root = _variant(tmp_path)
    inject_early_root_drift(monkeypatch, module, root)
    _refuse(root)
    root = _variant(tmp_path / "stable")
    snapshot = _load(root)
    assert snapshot.source_id == GFS


def test_loader_reuses_existing_parser_and_does_not_copy_it(tmp_path, monkeypatch):
    module = _module()
    calls: list[object] = []
    real = parse_direct_grid_forcing_contract

    def wrapped(manifest, *, source_id=None):
        calls.append(source_id)
        return real(manifest, source_id=source_id)

    monkeypatch.setattr(module, "parse_direct_grid_forcing_contract", wrapped)
    _load(_variant(tmp_path))
    assert calls == [GFS]
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "def parse_direct_grid_forcing_contract" not in source
    assert "shud_forcing_index must be unique" not in source


@pytest.mark.parametrize("leg", ["root", "entries"])
def test_visible_root_drift_after_last_asset_read(tmp_path, monkeypatch, leg):
    root = _variant(tmp_path)
    real_read = os.read
    armed = False

    def drift(fd, count):
        nonlocal armed
        data = real_read(fd, count)
        if data == sp_att_bytes(source_id=GFS) and not armed:
            armed = True
            if leg == "root":
                root.rename(root.with_name("old"))
                shutil.copytree(root.with_name("old"), root)
            else:
                (root / "foreign").write_bytes(b"preserve")
        return data

    monkeypatch.setattr(os, "read", drift)
    error = _refuse(root)
    assert armed
    assert ("changed during loading" if leg == "root" else "foreign") in str(error)


@pytest.mark.parametrize("name", FIXED_NAMES)
@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "device",
        "link",
        "directory",
        "fifo",
        "open-swap",
        "EACCES",
        "EIO",
        "ESTALE",
    ],
)
def test_each_asset_filesystem_boundary(tmp_path, monkeypatch, name, kind):
    root = _variant(tmp_path)
    victim = root / name
    outside = tmp_path / "external"
    original = victim.read_bytes()
    outside.write_bytes(original)
    fired = inject_entry_fault(monkeypatch, root, name, kind, original, outside)
    error = _refuse(root)
    assert error.__cause__ is not None
    assert outside.read_bytes() == original
    if kind not in {"missing", "link", "directory", "fifo"}:
        assert fired


@pytest.mark.parametrize("name", ["binding", "sp_att", "state", "parameter"])
def test_each_asset_exact_byte_cap(tmp_path, name):
    values = {"binding": b"b", "sp_att": b"s", "state": b"c", "parameter": b"p"}
    values[name] = b"x" * 32
    root = _variant(tmp_path, **values)
    _load(root, max_asset_bytes=32)
    error = _refuse(root, max_asset_bytes=31)
    assert "byte limit" in str(error)


@pytest.mark.parametrize("leg", ["depth", "nodes", "recursion"])
def test_bounded_json_owner_is_reached_before_schema(tmp_path, leg):
    if leg == "depth":
        raw = b"[" * 65 + b"0" + b"]" * 65
    elif leg == "nodes":
        raw = b"[" + b"0," * 250000 + b"0]"
    else:
        raw = b"[" * 20000 + b"0" + b"]" * 20000
    error = _refuse(_variant(tmp_path, manifest_bytes=raw), max_manifest_bytes=len(raw))
    assert type(error.__cause__) is BoundedJSONError


@pytest.mark.parametrize(
    "leg", ["duplicate", "whitespace", "order", "escape", "number"]
)
def test_noncanonical_otherwise_valid_envelope(tmp_path, leg):
    raw = canonical_json_bytes(_envelope())
    if leg == "duplicate":
        raw = raw.replace(b'"source_id":"gfs"', b'"source_id":"gfs","source_id":"gfs"')
    elif leg == "whitespace":
        raw += b" "
    elif leg == "order":
        value = json.loads(raw)
        raw = json.dumps(
            dict(reversed(list(value.items()))), separators=(",", ":")
        ).encode()
    elif leg == "escape":
        raw = raw.replace(b'"gfs"', b'"\\u0067fs"')
    else:
        raw = raw.replace(b'"x":3.0', b'"x":3e0')
    error = _refuse(_variant(tmp_path, manifest_bytes=raw))
    assert "canonical" in str(error)


@pytest.mark.parametrize("sources", [["gfs", "gfs"], ["GFS"]])
def test_source_project_grid_and_singleton_rejects_normalized_alias(tmp_path, sources):
    payload = _envelope()
    payload["direct_grid_forcing_contract"]["applicable_source_ids"] = sources
    _refuse(_variant(tmp_path, payload=payload))


@pytest.mark.parametrize("leg", ["depth", "nodes"])
def test_json_exact_resource_boundary_reaches_shape_gate(tmp_path, leg):
    raw = (
        b"[" * 63 + b"0" + b"]" * 63
        if leg == "depth"
        else b"[" + b"0," * 249998 + b"0]"
    )
    error = _refuse(_variant(tmp_path, manifest_bytes=raw), max_manifest_bytes=len(raw))
    assert type(error.__cause__) is not BoundedJSONError
    assert "JSON object" in str(error)


@pytest.mark.parametrize(
    "field",
    CONTRACT_KEYS,
)
@pytest.mark.parametrize("value", [None, 0, True, " "])
def test_each_contract_type_has_valid_other_legs(tmp_path, field, value):
    payload = _envelope()
    payload["direct_grid_forcing_contract"][field] = value
    _refuse(_variant(tmp_path, payload=payload))


@pytest.mark.parametrize(
    "field",
    REQUIRED_STATION_FIELDS,
)
@pytest.mark.parametrize("value", [None, True, " "])
def test_each_station_type_has_valid_other_legs(tmp_path, field, value):
    payload = _envelope()
    payload["direct_grid_forcing_contract"]["station_bindings"][0][field] = value
    _refuse(_variant(tmp_path, payload=payload))


@pytest.mark.parametrize(
    "field,value", [("longitude", 361), ("latitude", 91), ("shud_forcing_index", 0)]
)
def test_station_semantic_domain_is_not_copied(tmp_path, field, value):
    payload = _envelope()
    payload["direct_grid_forcing_contract"]["station_bindings"][0][field] = value
    _refuse(_variant(tmp_path, payload=payload))


@pytest.fixture(name="env")
def env(tmp_path):
    return make_env(tmp_path)


def _probe_rename(monkeypatch):
    probe = RenameProbe(safe_fs.rename_entry_no_follow)
    monkeypatch.setattr(safe_fs, "rename_entry_no_follow", probe)
    return probe


def test_prepare_success_commits_source_specific_complete_entries(env):
    builder = make_builder(env)
    before = tree_snapshot(env.yd_root)
    report = run(env, builder)
    assert builder.count == 2
    assert [r.source_id for r in builder.requests] == [GFS, IFS]
    expected = set(PREPARE_PARENT_ENTRIES)
    for source, root in report.variants.items():
        assert tree_snapshot(root) == builder.written_files[source]
        assert len(builder.written_files[source]) == 14
        expected.update(
            f"input/models/yd_{source}/{name}" for name in builder.written_files[source]
        )
        value = _load(
            root,
            source_id=source,
            grid_id=getattr(env.config.nwm_canonical_grid_id, source),
        )
        assert value.model_id == f"yd_{source}_model"
        assert value.contract.applicable_source_ids == (source,)
        assert (
            value.binding_content == builder.written_files[source][VARIANT_BINDING_NAME]
        )
    assert set(tree_snapshot(env.yd_root)) == set(before) | expected
    assert {k: tree_snapshot(env.yd_root)[k] for k in before} == before
    assert tree_snapshot(env.scratch_root) == {}


def test_prepare_second_source_failure_commits_nothing(tmp_path):
    env = make_env(tmp_path)
    before = tree_snapshot(env.yd_root)

    def corrupt(root: Path) -> None:
        path = root / VARIANT_HANDOFF_NAME
        path.write_bytes(path.read_bytes() + b"\n")

    builder = make_builder(env, {IFS: VariantScript(mutate=corrupt)})
    with pytest.raises(PrepareError) as captured:
        run(env, builder)
    assert IFS in str(captured.value)
    assert isinstance(captured.value.__cause__, _error())
    assert_untouched(env, before)


def test_non_utf8_sp_att_prepare_commits_nothing(env):
    before = tree_snapshot(env.yd_root)
    builder = make_builder(env, {IFS: VariantScript(sp_att_content=b"\xff")})
    with pytest.raises(PrepareError) as captured:
        run(env, builder)
    assert isinstance(captured.value.__cause__, _error())
    assert_untouched(env, before)


def test_staging_revalidation_refuses_copy_drift_before_rename(env, monkeypatch):
    before = tree_snapshot(env.yd_root)
    fired = inject_staging_drift(monkeypatch, "noncanonical")
    probe = _probe_rename(monkeypatch)
    with pytest.raises(PrepareError):
        run(env, make_builder(env))
    assert fired and probe.count == 0
    assert_untouched(env, before)


def test_legacy_three_file_variant_has_no_fallback(tmp_path):
    env = make_env(tmp_path)
    before = tree_snapshot(env.yd_root)
    builder = make_builder(
        env,
        {
            GFS: VariantScript(
                omit_entries=(VARIANT_HANDOFF_NAME, variant_asset_name(GFS))
            )
        },
    )
    with pytest.raises(PrepareError) as captured:
        run(env, builder)
    assert VARIANT_HANDOFF_NAME in str(captured.value) or "gfs" in str(captured.value)
    assert_untouched(env, before)


@pytest.mark.parametrize(
    "field",
    [
        "model_id",
        "basin_id",
        "basin_version_id",
        "river_network_version_id",
        "binding",
        "sp_att",
        "missing",
        "extra",
    ],
)
def test_staging_complete_snapshot_is_authority(env, monkeypatch, field):
    before = tree_snapshot(env.yd_root)
    fired = inject_staging_drift(monkeypatch, field)
    probe = _probe_rename(monkeypatch)
    with pytest.raises(PrepareError) as captured:
        run(env, make_builder(env))
    assert fired
    assert "ifs" in str(captured.value)
    assert probe.count == 0
    assert_untouched(env, before)


@pytest.mark.parametrize("source", ["gfs", "ifs"])
def test_independent_process_consumes_final_variant_through_real_forcing(env, source):
    report = run(env, consumer_builder(env))
    completed = consume_in_process(env, report, source)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (
        f"prepared-handoff-to-real-forcing-assemble {source} yd_{source}_model"
        in completed.stdout
    )


def test_prepare_loads_staging_carrier_before_rename(env, monkeypatch):
    real_open = os.open
    reads = []
    recording = make_builder(env)
    has_loader = importlib.util.find_spec("yd_producer.prepare_handoff") is not None

    def builder(request):
        recording(request)
        if not has_loader:
            (request.variant_root / VARIANT_HANDOFF_NAME).unlink()
            (request.variant_root / variant_asset_name(request.source_id)).unlink()

    def observed_open(path, flags, *args, **kwargs):
        if path == VARIANT_HANDOFF_NAME and not flags & os.O_WRONLY:
            reads.append(path)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", observed_open)
    probe = _probe_rename(monkeypatch)
    run(env, builder)
    assert reads == [VARIANT_HANDOFF_NAME] * 6
    assert probe.count == 4


def test_project_follows_actual_calibrated_state_path_and_snapshot(env, monkeypatch):
    builder, names = alternate_project_builder(env, monkeypatch)
    report = run(env, builder)
    for source, root in report.variants.items():
        value = _load(
            root,
            source_id=source,
            project_name=names[source].removesuffix(".cfg.ic"),
            grid_id=getattr(env.config.nwm_canonical_grid_id, source),
        )
        assert value.project_name == f"actual_{source}"


def test_prepared_snapshot_detaches_mutable_parser_containers(tmp_path, monkeypatch):
    module = _module()
    owned = inject_mutable_parser(monkeypatch, module)
    root = _variant(tmp_path)
    snapshot = _load(root)
    assert snapshot.contract.stations[0] is not owned["station"]
    owned["properties"]["foreign"] = ["mutable"]
    owned["stations"].clear()
    owned["sources"].append("ifs")
    assert snapshot.contract.applicable_source_ids == ("gfs",)
    assert type(snapshot.contract.stations) is tuple
    assert len(snapshot.contract.stations) == 1
    assert snapshot.contract.stations[0].station_id == "station-1"
    assert dict(snapshot.contract.stations[0].properties) == {}
    with pytest.raises(TypeError):
        snapshot.contract.stations[0].properties["key"] = "value"


@pytest.mark.parametrize("name", FIXED_NAMES)
@pytest.mark.parametrize("error_number", [5, 13, 70])
def test_each_carrier_read_error_is_domain_and_read_only(
    tmp_path, monkeypatch, name, error_number
):
    root = _variant(tmp_path)
    before = tree_snapshot(root)
    fired = inject_read_fault(monkeypatch, name, error_number)
    error = _refuse(root)
    assert isinstance(error.__cause__, safe_fs.SafeFilesystemError)
    assert error.__cause__.__cause__.errno == error_number
    assert len(fired) == 1
    assert tree_snapshot(root) == before


def test_ancestor_symlink_is_refused(tmp_path):
    real = _variant(tmp_path / "real")
    link = tmp_path / "alias"
    link.symlink_to(real.parent)
    _refuse(link / real.name)


@pytest.mark.parametrize("name", FIXED_NAMES)
def test_copy_growth_is_bounded_before_staging_write(env, monkeypatch, name):
    before = tree_snapshot(env.yd_root)
    recording = make_builder(env)
    evidence = inject_copy_growth(monkeypatch, env, recording, name)
    probe = _probe_rename(monkeypatch)
    with pytest.raises(PrepareError) as captured:
        run(env, recording)
    assert "byte limit" in str(captured.value)
    assert evidence["reads"] == [evidence["limit"] + 1]
    assert evidence["writes"] == []
    assert probe.count == 0
    assert_untouched(env, before)


@pytest.mark.parametrize("leg", ["symlink", "EIO"])
def test_state_point_of_use_read_is_no_follow_and_domain(env, monkeypatch, leg):
    recording = make_builder(env)
    before = tree_snapshot(env.yd_root)
    outside = env.package.root / "external.cfg.ic"
    outside.write_bytes(build_cfg_ic(mesh_count=3, river_count=3).payload)
    expected = outside.read_bytes()
    fired = inject_state_point_of_use(monkeypatch, leg, outside)
    probe = _probe_rename(monkeypatch)
    with pytest.raises(PrepareError) as captured:
        run(env, recording)
    assert "gfs 变体率定态安全读取失败" in str(captured.value)
    assert fired and probe.count == 0
    assert isinstance(captured.value.__cause__, safe_fs.SafeFilesystemError)
    if leg == "EIO":
        assert captured.value.__cause__.__cause__.errno == errno.EIO
    assert outside.read_bytes() == expected
    assert_untouched(env, before)
