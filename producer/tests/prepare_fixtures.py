"""`prepare` 编排用的合成基线包、**记录型可编排假 builder** 与全树快照工具。"""

from __future__ import annotations

import errno
import hashlib
import importlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from cfg_ic_fixtures import build_cfg_ic
from cli_fixtures import write_config, write_local
from geometry_fixtures import SyntheticBaseline, write_synthetic_baseline

from yd_producer import prepare as prepare_module
from yd_producer.config import Config, LocalConfig, load_config, load_local
from yd_producer.prepare import (
    VARIANT_BINDING_NAME,
    VARIANT_CALIBRATED_STATE_NAME,
    VariantBuildRequest,
    run_prepare,
)
from yd_producer.store import safe_fs

BASELINE_HYDRO_PARAM_NAME = "yd.cfg.para"

BASELINE_HYDRO_PARAM_BYTES = b"# synthetic hydrologic parameters\nKsatH 1.0e-4\n"

SYNTHETIC_MESH_COUNT = 2

VARIANT_HANDOFF_NAME = "yd.direct-grid-handoff.json"
VARIANT_HANDOFF_SCHEMA = "yd.prepare.direct-grid-handoff.v2"
SYNTHETIC_PROJECT_NAME = "yd"
NATIVE_VARIANT_FILES = (
    "yd.cfg.ic",
    "yd.cfg.para",
    "yd.cfg.calib",
    "yd.sp.mesh",
    "yd.sp.att",
    "yd.sp.riv",
    "yd.sp.rivseg",
    "yd.para.lc",
    "yd.para.soil",
    "yd.para.geol",
    "yd.tsd.lai",
    "yd.tsd.mf",
)
NATIVE_CHECKSUM_FILES = (*NATIVE_VARIANT_FILES, "yd.binding")


def binding_bytes(*, grid_id: str, source_id: str) -> bytes:
    return f"grid_id={grid_id}\nsource_id={source_id}\n".encode()


def sp_att_bytes(*, source_id: str) -> bytes:
    return f"synthetic {source_id} sp.att\n".encode()


def variant_asset_name(source_id: str) -> str:
    return "yd.sp.att"


def native_placeholder(name: str) -> bytes:
    return f"synthetic {name}\n".encode()


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_literal(content: bytes) -> str:
    return f"sha256:{sha256_hex(content)}"


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def synthetic_variant_ids(source_id: str) -> dict[str, str]:
    return {
        "model_id": f"yd_{source_id}_model",
        "basin_id": f"yd_{source_id}_basin",
        "basin_version_id": f"yd_{source_id}_basin_v1",
        "river_network_version_id": f"yd_{source_id}_rivnet_v1",
    }


def station_payload(*, grid_id: str, index: int = 1) -> dict[str, Any]:
    return {
        "forcing_filename": f"X{index}.csv",
        "grid_cell_id": f"cell-{index}",
        "grid_id": grid_id,
        "latitude": float(index + 1),
        "longitude": float(index),
        "shud_forcing_index": index,
        "station_id": f"station-{index}",
        "x": float(index + 2),
        "y": float(index + 3),
        "z": float(index + 4),
    }


def contract_payload(
    *,
    source_id: str,
    project_name: str,
    model_id: str,
    grid_id: str,
    binding: bytes,
    sp_att: bytes,
    stations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "applicable_source_ids": [source_id],
        "binding_checksum": sha256_literal(binding),
        "binding_uri": f"models/{model_id}/direct-grid/binding.json",
        "forcing_mapping_mode": "direct_grid",
        "grid_id": grid_id,
        "grid_signature": f"{source_id}-grid-signature",
        "model_input_package_id": f"{source_id}-model-input-v1",
        "sp_att_checksum": sha256_literal(sp_att),
        "sp_att_path": f"input/{project_name}.sp.att",
        "station_bindings": stations
        if stations is not None
        else [station_payload(grid_id=grid_id)],
    }


def handoff_payload(
    *,
    source_id: str,
    project_name: str,
    grid_id: str,
    binding: bytes,
    sp_att: bytes,
    asset_name: str | None = None,
    ids: Mapping[str, str] | None = None,
    contract: Mapping[str, Any] | None = None,
    checksums: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    names = dict(ids or synthetic_variant_ids(source_id))
    files = dict(checksums or {})
    files.setdefault("yd.binding", binding)
    files.setdefault("yd.sp.att", sp_att)
    for name in NATIVE_VARIANT_FILES:
        files.setdefault(name, native_placeholder(name))
    return {
        "basin_id": names["basin_id"],
        "basin_version_id": names["basin_version_id"],
        "direct_grid_forcing_contract": contract
        if contract is not None
        else contract_payload(
            source_id=source_id,
            project_name=project_name,
            model_id=names["model_id"],
            grid_id=grid_id,
            binding=binding,
            sp_att=sp_att,
        ),
        "file_checksums": {
            name: sha256_literal(files[name]) for name in NATIVE_CHECKSUM_FILES
        },
        "model_id": names["model_id"],
        "project_name": project_name,
        "river_network_version_id": names["river_network_version_id"],
        "schema_version": VARIANT_HANDOFF_SCHEMA,
        "source_id": source_id,
        "sp_att_asset_name": asset_name or variant_asset_name(source_id),
    }


def write_prepared_variant(
    directory: Path,
    *,
    source_id: str,
    grid_id: str,
    project_name: str = SYNTHETIC_PROJECT_NAME,
    binding: bytes | None = None,
    sp_att: bytes | None = None,
    asset_name: str | None = None,
    state: bytes = b"calibrated-state\n",
    parameter: bytes = BASELINE_HYDRO_PARAM_BYTES,
    payload: Mapping[str, Any] | None = None,
    manifest_bytes: bytes | None = None,
    extra: Mapping[str, bytes] | None = None,
    omit: tuple[str, ...] = (),
) -> Path:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    binding_content = (
        binding
        if binding is not None
        else binding_bytes(grid_id=grid_id, source_id=source_id)
    )
    sp_content = sp_att if sp_att is not None else sp_att_bytes(source_id=source_id)
    asset = asset_name or variant_asset_name(source_id)
    files: dict[str, bytes] = {
        name: native_placeholder(name) for name in NATIVE_VARIANT_FILES
    }
    files["yd.cfg.ic"] = state
    files["yd.cfg.para"] = parameter
    files["yd.sp.att"] = sp_content
    files[VARIANT_BINDING_NAME] = binding_content
    if extra:
        files.update(extra)
    envelope = (
        payload
        if payload is not None
        else handoff_payload(
            source_id=source_id,
            project_name=project_name,
            grid_id=grid_id,
            binding=binding_content,
            sp_att=sp_content,
            asset_name=asset,
            checksums=files,
        )
    )
    files[VARIANT_HANDOFF_NAME] = (
        manifest_bytes if manifest_bytes is not None else canonical_json_bytes(envelope)
    )
    files[asset] = sp_content
    for name, content in files.items():
        if name in omit:
            continue
        (root / name).write_bytes(content)
    return root


@dataclass(frozen=True)
class SyntheticBaselinePackage:
    """一份合成基线模型包及其 oracle。"""

    root: Path
    gis: SyntheticBaseline
    hydro_param: Path
    river_feature_count: int

    @property
    def hydro_param_bytes(self) -> bytes:
        return self.hydro_param.read_bytes()


def write_baseline_package(
    directory: Path, *, river_count: int = 3, unit_count: int = 2
) -> SyntheticBaselinePackage:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    gis = write_synthetic_baseline(
        root / "gis",
        river_count=river_count,
        unit_count=unit_count,
        rivers_stem="river",
        domain_stem="domain",
    )
    hydro_param = root / BASELINE_HYDRO_PARAM_NAME
    hydro_param.write_bytes(BASELINE_HYDRO_PARAM_BYTES)
    att_rows = "\n".join(
        f"{index}\t1\t1\t11\t{index}\t1\t0\t0\t0" for index in range(1, unit_count + 1)
    )
    (root / "yd.sp.att").write_text(
        f"{unit_count}\t9\nINDEX\tSOIL\tGEOL\tLC\tFORC\tMF\tBC\tSS\tLAKE\n{att_rows}\n",
        encoding="utf-8",
    )
    riv_rows = "\n".join(
        f"{index}\t-3\t1\t0.01\t1.0\t0" for index in range(1, river_count + 1)
    )
    (root / "yd.sp.riv").write_text(
        f"{river_count}\t6\nIndex\tDown\tType\tSlope\tLength\tBC\n{riv_rows}\n",
        encoding="utf-8",
    )
    return SyntheticBaselinePackage(
        root=root,
        gis=gis,
        hydro_param=hydro_param,
        river_feature_count=river_count,
    )


@dataclass
class VariantScript:
    """单个 source 的产出剧本；默认值即"完全合法的变体"。"""

    river_count: int | None = None
    include_river_section: bool = True
    extra_entries: tuple[str, ...] = ()
    symlink_entries: tuple[tuple[str, str], ...] = ()
    omit_entries: tuple[str, ...] = ()
    remove_variant_root: bool = False
    corrupt_state: bool = False
    raises: BaseException | None = None
    asset_name: str | None = None
    sp_att_content: bytes | None = None
    manifest_bytes: bytes | None = None
    mutate: Callable[[Path], None] | None = None


class RecordingBuilder:
    """记录型 + 可编排的假 builder。"""

    def __init__(
        self,
        package: SyntheticBaselinePackage,
        *,
        river_count: int,
        scripts: dict[str, VariantScript] | None = None,
    ) -> None:
        self._package = package
        self._river_count = river_count
        self._scripts = dict(scripts or {})
        self.requests: list[VariantBuildRequest] = []
        self.variant_root_was_empty_dir: list[bool] = []
        self.written_river_rows: dict[str, int] = {}
        self.written_files: dict[str, dict[str, bytes]] = {}

    @property
    def count(self) -> int:
        return len(self.requests)

    def __call__(self, request: VariantBuildRequest) -> None:
        self.requests.append(request)
        root = request.variant_root
        self.variant_root_was_empty_dir.append(
            root.is_dir() and not any(root.iterdir())
        )
        script = self._scripts.get(request.source_id, VariantScript())
        if script.raises is not None:
            raise script.raises

        if script.remove_variant_root:
            for child in sorted(root.iterdir()):
                child.unlink()
            root.rmdir()
            return

        root.mkdir(parents=True, exist_ok=True)
        river_count = (
            self._river_count if script.river_count is None else script.river_count
        )
        if not script.include_river_section:
            river_count = 0
        document = build_cfg_ic(
            mesh_count=SYNTHETIC_MESH_COUNT, river_count=river_count
        )
        self.written_river_rows[request.source_id] = len(document.river_data_indices)

        binding = binding_bytes(grid_id=request.grid_id, source_id=request.source_id)
        sp_content = (
            script.sp_att_content
            if script.sp_att_content is not None
            else sp_att_bytes(source_id=request.source_id)
        )
        state = (
            b"\xff\xfe truncated-not-utf8" if script.corrupt_state else document.payload
        )
        root = write_prepared_variant(
            root,
            source_id=request.source_id,
            grid_id=request.grid_id,
            binding=binding,
            sp_att=sp_content,
            asset_name=script.asset_name,
            state=state,
            parameter=self._package.hydro_param.read_bytes(),
            manifest_bytes=script.manifest_bytes,
            omit=script.omit_entries,
        )
        written = {
            path.name: path.read_bytes() for path in root.iterdir() if path.is_file()
        }
        for name in script.extra_entries:
            (root / name).write_bytes(b"residue\n")
            written[name] = b"residue\n"
        for name, link_target in script.symlink_entries:
            (root / name).symlink_to(link_target)
        if script.mutate is not None:
            script.mutate(root)
        self.written_files[request.source_id] = written


def tree_snapshot(root: Path) -> dict[str, object]:
    root = Path(root)
    snapshot: dict[str, object] = {}
    if not os.path.lexists(root):
        return snapshot
    for current, directories, files in os.walk(root):
        base = Path(current)
        for name in directories:
            path = base / name
            relative = str(path.relative_to(root))
            snapshot[relative] = (
                f"symlink:{os.readlink(path)}" if path.is_symlink() else "dir"
            )
        for name in files:
            path = base / name
            relative = str(path.relative_to(root))
            snapshot[relative] = (
                f"symlink:{os.readlink(path)}"
                if path.is_symlink()
                else path.read_bytes()
            )
    return snapshot


@dataclass
class RenameProbe:
    """包住 `safe_fs.rename_entry_no_follow`，记录每次提交的源与终名。"""

    delegate: object
    fail_at: int | None = None
    calls: list[tuple[Path, Path]] = field(default_factory=list)
    devices: list[tuple[int, int]] = field(default_factory=list)

    def __call__(self, parent, name, dest_parent, dest_name, **kwargs):
        source = Path(parent) / name
        self.calls.append((source, Path(dest_parent) / dest_name))
        self.devices.append((os.stat(source).st_dev, os.stat(Path(dest_parent)).st_dev))
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            from yd_producer.store.safe_fs import SafeFilesystemError

            raise SafeFilesystemError(
                f"injected rename failure: {Path(dest_parent) / dest_name}", kind="io"
            )
        return self.delegate(parent, name, dest_parent, dest_name, **kwargs)

    @property
    def count(self) -> int:
        return len(self.calls)


REACH_COUNT = 3


@dataclass
class Env:
    """一次编排所需的全部现场对象。"""

    config: Config
    local: LocalConfig
    yd_root: Path
    scratch_root: Path
    package: SyntheticBaselinePackage


def make_env(
    tmp_path: Path,
    *,
    variants: dict[str, str] | None = None,
    reach_count: int = REACH_COUNT,
    grid_ids: dict[str, str] | None = None,
    river_count: int = 3,
) -> Env:
    """建一份齐备现场：真 TOML -> 真装载器 -> 真 `Config`/`LocalConfig`。"""
    config_path = write_config(
        tmp_path, variants=variants, reach_count=reach_count, grid_ids=grid_ids
    )
    local_path = write_local(tmp_path)
    config = load_config(config_path)
    local = load_local(local_path, config)
    yd_root = Path(local.yd_root)
    scratch_root = Path(local.scratch_root)
    yd_root.mkdir(parents=True, exist_ok=True)
    scratch_root.mkdir(parents=True, exist_ok=True)
    done_dir = yd_root / "output" / "2025010100" / "gfs"
    done_dir.mkdir(parents=True)
    (done_dir / "DONE").write_bytes(b"")
    (done_dir / "yd.rivqdown.dat").write_bytes(b"pre-existing product bytes\n")
    package = write_baseline_package(tmp_path / "baseline", river_count=river_count)
    return Env(
        config=config,
        local=local,
        yd_root=yd_root,
        scratch_root=scratch_root,
        package=package,
    )


def make_builder(env: Env, scripts: dict[str, VariantScript] | None = None):
    return RecordingBuilder(
        env.package, river_count=env.config.reach_count, scripts=scripts
    )


def run(env: Env, builder):
    return run_prepare(
        local=env.local,
        config=env.config,
        baseline_root=env.package.root,
        builder=builder,
    )


def assert_untouched(env: Env, before: dict, builder=None) -> None:
    """`YD_ROOT` 全树逐字节回到执行前，且 scratch 下无任何残留。"""
    assert tree_snapshot(env.yd_root) == before
    assert tree_snapshot(env.scratch_root) == {}
    if builder is not None:
        assert builder.count == 0


GFS = "gfs"
IFS = "ifs"
GFS_GRID = "fixture-grid-gfs"
IFS_GRID = "fixture-grid-ifs"
ENVELOPE_KEYS = (
    "schema_version",
    "source_id",
    "project_name",
    "model_id",
    "basin_id",
    "basin_version_id",
    "river_network_version_id",
    "direct_grid_forcing_contract",
    "sp_att_asset_name",
    "file_checksums",
)
CONTRACT_KEYS = (
    "forcing_mapping_mode",
    "binding_uri",
    "binding_checksum",
    "model_input_package_id",
    "sp_att_path",
    "sp_att_checksum",
    "applicable_source_ids",
    "grid_id",
    "grid_signature",
    "station_bindings",
)
FIXED_NAMES = (
    *NATIVE_VARIANT_FILES,
    VARIANT_BINDING_NAME,
    VARIANT_HANDOFF_NAME,
)


def handoff_module():
    return importlib.import_module("yd_producer.prepare_handoff")


def handoff_error():
    return handoff_module().PreparedVariantHandoffError


def load_handoff(
    root: Path,
    *,
    source_id: str = GFS,
    project_name: str = SYNTHETIC_PROJECT_NAME,
    grid_id: str = GFS_GRID,
    max_manifest_bytes: int = 65_536,
    max_asset_bytes: int = 65_536,
):
    return handoff_module().load_prepared_variant_handoff(
        variant_root=root,
        source_id=source_id,
        project_name=project_name,
        grid_id=grid_id,
        max_manifest_bytes=max_manifest_bytes,
        max_asset_bytes=max_asset_bytes,
    )


def refuse_handoff(root: Path, **kwargs):
    with pytest.raises(handoff_error()) as captured:
        load_handoff(root, **kwargs)
    return captured.value


def variant_fixture(tmp_path: Path, **kwargs) -> Path:
    kwargs.setdefault("source_id", GFS)
    kwargs.setdefault("grid_id", GFS_GRID)
    return write_prepared_variant(tmp_path / "variant", **kwargs)


def envelope_fixture(**overrides) -> dict:
    binding = binding_bytes(grid_id=GFS_GRID, source_id=GFS)
    sp_att = sp_att_bytes(source_id=GFS)
    payload = handoff_payload(
        source_id=GFS,
        project_name=SYNTHETIC_PROJECT_NAME,
        grid_id=GFS_GRID,
        binding=binding,
        sp_att=sp_att,
        checksums={
            "yd.cfg.ic": b"calibrated-state\n",
            "yd.cfg.para": BASELINE_HYDRO_PARAM_BYTES,
        },
    )
    payload.update(overrides)
    return payload


def inject_entry_fault(monkeypatch, root, name, kind, original, outside):
    victim = root / name
    real_open, real_read = os.open, os.read
    device_fds = set()

    def no_device_read(fd, count):
        assert fd not in device_fds, (
            "nonregular descriptor must be rejected before read"
        )
        return real_read(fd, count)

    monkeypatch.setattr(os, "read", no_device_read)
    fired = []
    if kind in {"missing", "link", "directory", "fifo"}:
        victim.unlink()
        if kind == "link":
            victim.symlink_to(outside)
        elif kind == "directory":
            victim.mkdir()
        elif kind == "fifo":
            os.mkfifo(victim)
    else:

        def failing_open(path, flags, *args, **kwargs):
            if path == name and not fired:
                fired.append(path)
                if kind == "device":
                    fd = real_open(os.devnull, os.O_RDONLY)
                    device_fds.add(fd)
                    return fd
                if kind == "open-swap":
                    replacement = root.parent / "replacement"
                    replacement.write_bytes(original)
                    os.replace(replacement, victim)
                else:
                    raise OSError(getattr(errno, kind), kind)
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", failing_open)
    return fired


def inject_read_fault(monkeypatch, name, error_number):
    real_open, real_read, real_close = os.open, os.read, os.close
    target_fds, fired = set(), []

    def opening(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if path == name:
            target_fds.add(fd)
        return fd

    def reading(fd, count):
        if fd in target_fds:
            fired.append(fd)
            raise OSError(error_number, "read-phase fault")
        return real_read(fd, count)

    def closing(fd):
        target_fds.discard(fd)
        return real_close(fd)

    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(os, "read", reading)
    monkeypatch.setattr(os, "close", closing)
    return fired


def inject_mutable_parser(monkeypatch, module):
    parser = module.parse_direct_grid_forcing_contract
    owned = {}

    def mutable_parser(payload, *, source_id):
        contract = parser(payload, source_id=source_id)
        properties = {}
        station = replace(contract.stations[0], properties=properties)
        stations = [station]
        sources = list(contract.applicable_source_ids)
        owned.update(
            properties=properties, stations=stations, sources=sources, station=station
        )
        return replace(contract, stations=stations, applicable_source_ids=sources)

    monkeypatch.setattr(module, "parse_direct_grid_forcing_contract", mutable_parser)
    return owned


def inject_staging_drift(monkeypatch, field):
    real_write = safe_fs.write_bytes_no_follow_exclusive
    fired = []

    def copy_write(path, content, **kwargs):
        result = real_write(path, content, **kwargs)
        path = Path(path)
        if path.name == "yd.cfg.para" and path.parent.name == "ifs":
            fired.append(path)
            manifest_path = path.parent / VARIANT_HANDOFF_NAME
            payload = json.loads(manifest_path.read_bytes())
            contract = payload["direct_grid_forcing_contract"]
            if field in {"binding", "sp_att"}:
                name = VARIANT_BINDING_NAME if field == "binding" else "yd.sp.att"
                content = b"changed but checksum-valid"
                (path.parent / name).write_bytes(content)
                checksum = sha256_literal(content)
                contract[field + "_checksum"] = checksum
                payload["file_checksums"][name] = checksum
            elif field == "noncanonical":
                manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
                return result
            elif field == "missing":
                (path.parent / "yd.sp.att").unlink()
            elif field == "extra":
                (path.parent / "foreign").write_bytes(b"unexpected")
            else:
                payload[field] += "_next"
                if field == "model_id":
                    contract["binding_uri"] = (
                        f"models/{payload[field]}/direct-grid/binding.json"
                    )
            manifest_path.write_bytes(canonical_json_bytes(payload))
        return result

    monkeypatch.setattr(safe_fs, "write_bytes_no_follow_exclusive", copy_write)
    return fired


def alternate_project_builder(env, monkeypatch):
    recording = make_builder(env)
    names = {"gfs": "actual_gfs.cfg.ic", "ifs": "actual_ifs.cfg.ic"}
    states = {}
    real_loader = prepare_module.load_prepared_variant_handoff
    real_read = safe_fs.read_bytes_limited_no_follow

    def builder(request):
        recording(request)
        states[request.source_id] = request.variant_root / names[request.source_id]
        path = request.variant_root / VARIANT_HANDOFF_NAME
        payload = json.loads(path.read_bytes())
        project = names[request.source_id].removesuffix(".cfg.ic")
        payload["project_name"] = project
        payload["direct_grid_forcing_contract"]["sp_att_path"] = (
            f"input/{project}.sp.att"
        )
        path.write_bytes(canonical_json_bytes(payload))

    monkeypatch.setattr(
        prepare_module, "calibrated_state_path", lambda root: states[root.name]
    )

    def loader(**kwargs):
        value = real_loader(**kwargs)
        root = kwargs["variant_root"]
        if root == states[kwargs["source_id"]].parent:
            (root / VARIANT_CALIBRATED_STATE_NAME).rename(states[kwargs["source_id"]])
        return value

    def reading(path, **kwargs):
        content = real_read(path, **kwargs)
        if path in states.values():
            path.rename(path.parent / VARIANT_CALIBRATED_STATE_NAME)
        return content

    monkeypatch.setattr(prepare_module, "load_prepared_variant_handoff", loader)
    monkeypatch.setattr(safe_fs, "read_bytes_limited_no_follow", reading)
    return builder, names


def inject_early_root_drift(monkeypatch, module, root):
    real_read = module.read_bytes_limited_no_follow
    state = {"flipped": False}

    def flipping(path, *, max_bytes, containment_root=None):
        content = real_read(
            path, max_bytes=max_bytes, containment_root=containment_root
        )
        if not state["flipped"] and Path(path).name == VARIANT_HANDOFF_NAME:
            state["flipped"] = True
            displaced = root.parent / "displaced"
            root.rename(displaced)
            replacement = write_prepared_variant(
                root,
                source_id=GFS,
                grid_id=GFS_GRID,
                extra={"drift.txt": b"x\n"},
                omit=(variant_asset_name(GFS),),
            )
            assert replacement == root
        return content

    monkeypatch.setattr(module, "read_bytes_limited_no_follow", flipping)


HANDOFF_FIELDS = [
    "source_id",
    "project_name",
    "model_id",
    "basin_id",
    "basin_version_id",
    "river_network_version_id",
    "contract",
    "sp_att_asset_name",
    "binding_content",
    "sp_att_content",
]
LOADER_FIELDS = [
    "variant_root",
    "source_id",
    "project_name",
    "grid_id",
    "max_manifest_bytes",
    "max_asset_bytes",
]


def inject_copy_growth(monkeypatch, env, recording, name):
    manifest_cap = 8192
    limits = {VARIANT_HANDOFF_NAME: manifest_cap}
    limit = limits.get(name, 1024)
    monkeypatch.setattr(
        prepare_module, "MAX_PREPARED_VARIANT_MANIFEST_BYTES", manifest_cap
    )
    monkeypatch.setattr(prepare_module, "MAX_PREPARED_VARIANT_ASSET_BYTES", 1024)
    evidence = {"limit": limit, "reads": [], "writes": []}
    real_mkdir, real_open, real_read, real_close = os.mkdir, os.open, os.read, os.close
    real_write = safe_fs.write_bytes_no_follow_exclusive
    armed, fds = [], set()

    def mkdir(path, *args, **kwargs):
        if str(path).startswith(".yd-prepare-staging") and not armed:
            armed.append(path)
            (recording.requests[0].variant_root / name).write_bytes(b"x" * (limit + 1))
        return real_mkdir(path, *args, **kwargs)

    def opening(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if armed and path == name and not flags & os.O_WRONLY:
            fds.add(fd)
        return fd

    def reading(fd, count):
        if fd in fds:
            evidence["reads"].append(count)
            assert count == limit + 1, "copy must use bounded max+1 read"
        return real_read(fd, count)

    def closing(fd):
        fds.discard(fd)
        return real_close(fd)

    def writing(path, content, **kwargs):
        if Path(path).name == name:
            evidence["writes"].append(path)
        return real_write(path, content, **kwargs)

    for key, value in (
        ("mkdir", mkdir),
        ("open", opening),
        ("read", reading),
        ("close", closing),
    ):
        monkeypatch.setattr(os, key, value)
    monkeypatch.setattr(safe_fs, "write_bytes_no_follow_exclusive", writing)
    return evidence


def inject_state_point_of_use(monkeypatch, leg, outside):
    real_loader = prepare_module.load_prepared_variant_handoff
    real_open, real_read, real_close = os.open, os.read, os.close
    fds, fired = set(), []

    def loaded(**kwargs):
        value = real_loader(**kwargs)
        if kwargs["source_id"] == "gfs":
            victim = kwargs["variant_root"] / "yd.cfg.ic"
            fired.append(victim)
            if leg == "symlink":
                victim.unlink()
                victim.symlink_to(outside)
        return value

    def opening(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if fired and path == "yd.cfg.ic":
            fds.add(fd)
        return fd

    def reading(fd, count):
        if fd in fds and leg == "EIO":
            raise OSError(errno.EIO, "state point-of-use read")
        return real_read(fd, count)

    def closing(fd):
        fds.discard(fd)
        return real_close(fd)

    monkeypatch.setattr(prepare_module, "load_prepared_variant_handoff", loaded)
    for name, function in (("open", opening), ("read", reading), ("close", closing)):
        monkeypatch.setattr(os, name, function)
    return fired


PREPARE_PARENT_ENTRIES = [
    "input",
    "input/models",
    "input/models/yd_gfs",
    "input/models/yd_ifs",
    "input/viewer",
    "input/viewer/rivers.geojson",
    "input/viewer/boundary.geojson",
]
