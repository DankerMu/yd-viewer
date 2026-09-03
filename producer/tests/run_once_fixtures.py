"""Issue #26 run_once 端到端 fixture：独立目录字面量/构造器、terminal-hook 包装、
进程内 AttemptDriver，与 real registry/forcing/assemble/tracker/publish 合成链。

本模块**不含任何 `test_*` 函数**。独立性纪律：
- 路径、cycle、reach、job 名等期望值全部在本地字面登记，MUST NOT 从被测模块
  `yd_producer.controller` 导入（只有 driver 的 `PreparedAttempt`/`AttemptProducts`
  等**被测公共类型**例外——它们就是 fixture 要证明的对象）。
- raw 树/源 manifest/变体/率定态/checkpoint 一律由本模块的构造器合成，不借用被测
  实现的回读值；DAT 字节用 `dat_fixtures`（该模块保证不 import `yd_producer`）。
- terminal hook 先消费本轮 `raw_manifest_path` 的每条 `local_key`（同一
  `LocalObjectStore`），再行使真实 `stage_work_registry -> FileForcingRepository ->
  ForcingProducer -> assemble`；只验证 same-root 接线，不重验 raw->canonical 数值
  （数值正确性显式归 M4，见 tasks.md「M2 fake 端到端 oracle」）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from assembly_fixtures import (
    BINDING,
    PARAMETER_TEMPLATE,
    SP_ATT,
    canonical_netcdf,
    file_repository_contract,
)
from cfg_ic_fixtures import build_cfg_ic
from dat_fixtures import build_dat_bytes

from yd_producer import publish
from yd_producer.assemble import (
    RunDirectory,
    WorkIdentity,
    assemble,
    stage_work_registry,
)
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
from yd_producer.executor import FakeJobExecutor, JobRecord, JobState, StepClock
from yd_producer.forcing import ForcingProducer, ForcingProducerConfig
from yd_producer.forcing.file_store import FileForcingRepository
from yd_producer.store.object_store import LocalObjectStore, sha256_bytes
from yd_producer.tracker import CheckpointTracker, ensure_twelve_hour_checkpoint

# --- 独立字面量 --------------------------------------------------------------

#: T：2026-08-26 12Z（与 publish 套件的锚点一致以便手算复用）。
CYCLE = datetime(2026, 8, 26, 12, tzinfo=UTC)
#: T+12（checkpoint 的正式落点）。
NEXT_CYCLE = datetime(2026, 8, 27, 0, tzinfo=UTC)
#: 手算绝对分钟（20691 天 * 1440 = 29795040，2026-08-26 00Z；+720 => 29795760）。
ABSOLUTE_MINUTE = 29795760
#: tracker 捕获时 checkpoint 的**相对**分钟（T+12 = 720）。
RELATIVE_MINUTE = "720.000000"

#: 合成 reach 数（小规模：布局判定与规模无关）。
REACH_COUNT = 8
#: `config.forecast_days * 24`。
EXPECTED_ROWS = 168
#: 变体/项目名：`<project>.cfg.ic` / `<project>.para` / `<project>.tsd.forc`。
PROJECT = "yd"
#: 提交作业名：`yd-<source>-<cycle>`（tasks.md ownership 6 的字面形态）。
JOB_NAME = "yd-gfs-2026082612"
#: IFS 同轮的作业名字面形态。
IFS_JOB_NAME = "yd-ifs-2026082612"

#: 各 run_once 测试共享的确定性时钟（T0 起、每步 10s；单元面在 executor 测试）。
T0 = CYCLE.replace(hour=0, minute=0, second=0)


def step_clock() -> StepClock:
    """run_once 注入用的确定性时钟：不读挂钟，杜绝跨文件重复定义。

    各测试文件原先各自定义 `StepClockImpl`/`_clock`；现统一收进 fixture（fixture 是
    测试支撑模块，不构成对 `yd_producer` 私有面的引用）。
    """
    return StepClock(start=T0, step=timedelta(seconds=10))


#: source gfs 的 raw 规则（合成；与 `cli_fixtures` 无关，本 fixture 自行登记）。
GFS_LEADS = (0, 3)
GFS_VARIABLES = ("tmp2m",)
GFS_BUNDLE = "gfs.t{cycle_hour:02d}z.pgrb2.0p25.f{lead:03d}.bundle.grib2"
#: source ifs 的 raw 规则。
IFS_LEADS = (0, 3)
IFS_VARIABLES = ("2t",)
IFS_BUNDLE = "ifs.t{cycle_hour:02d}z.f{lead:03d}.bundle.grib2"

#: raw 目录段（`rawscan.SOURCE_DIR_NAMES` 的独立转录）。
DIR_SEGMENTS = {"ifs": "IFS", "gfs": "gfs"}

#: 源 manifest 的 entry metadata 六键（独立登记）。
CARRIED_KEYS = (
    "cycle_time",
    "valid_time",
    "bundle",
    "grib_short_name",
    "cfgrib_filter_by_keys",
    "logical_remote_url",
)

#: forcing 依赖的 canonical 变量（按 source 登记，独立于 `ForcingProducer` 常量）。
CANONICAL_VARIABLES = {
    "gfs": (
        "prcp_rate_or_amount",
        "air_temperature_2m",
        "relative_humidity_2m",
        "wind_u_10m",
        "wind_v_10m",
        "pressure_surface",
        "shortwave_down",
    ),
    "ifs": (
        "prcp_rate_or_amount",
        "air_temperature_2m",
        "relative_humidity_2m",
        "wind_u_10m",
        "wind_v_10m",
        "surface_pressure",
        "shortwave_down",
    ),
}

#: slurm 键集（config/local 必须完全一致；partition 必须非空串）。
#: partition 用**非默认**值（`gpu-1`）：任何把报告里硬编码成 `"cpu"` 的实现都会当场红。
SLURM_FIELDS = ("partition", "account")
SLURM_RESOURCES = {"partition": "gpu-1", "account": "yd-forecast"}


# --- 构造器 ------------------------------------------------------------------


def cycle_text(cycle: datetime = CYCLE) -> str:
    return cycle.strftime("%Y%m%d%H")


def make_config(*, source: str = "gfs") -> Config:
    """一份能过 preflight 与 rawscan/rawcopy 的合成 `Config`。"""
    return Config(
        forecast_days=7,
        output_interval_minutes=60,
        checkpoint_hours=(12,),
        reach_count=REACH_COUNT,
        nwm_mapping_builder_module="workers.mapping_builder.cli",
        nwm_canonical_grid_id=CanonicalGridConfig(
            gfs="fixture-grid-gfs", ifs="fixture-grid-ifs"
        ),
        cycle=CycleConfig(hours=(0, 12)),
        variants=VariantsConfig(gfs="input/models/yd_gfs", ifs="input/models/yd_ifs"),
        raw=RawConfig(
            ifs=RawSourceConfig(
                lead_hours=IFS_LEADS,
                variables=IFS_VARIABLES,
                bundles=(IFS_BUNDLE,),
                f000_special=False,
            ),
            gfs=RawSourceConfig(
                lead_hours=GFS_LEADS,
                variables=GFS_VARIABLES,
                bundles=(GFS_BUNDLE,),
                f000_special=False,
            ),
        ),
        slurm=SlurmSchema(required_fields=SLURM_FIELDS),
    )


def make_local(tmp_path: Path | str, *, config: Config) -> LocalConfig:
    root = Path(tmp_path).resolve()
    return LocalConfig(
        yd_root=str(root / "yd"),
        scratch_root=str(root / "scratch"),
        shud_binary=str(root / "bin" / "shud"),
        nwm=NwmLocal(
            raw_root=str(root / "nwm" / "raw"),
            checkout_root=str(root / "nwm" / "checkout"),
            python=str(root / "nwm" / ".venv" / "bin" / "python"),
        ),
        slurm=dict(SLURM_RESOURCES),
        cron=CronLocal(
            lock_path=str(root / "run" / "yd-producer.lock"),
            log_dir=str(root / "log"),
        ),
    )


def write_config_local(
    tmp_path: Path, *, source: str = "gfs"
) -> tuple[Config, LocalConfig]:
    config = make_config(source=source)
    local = make_local(tmp_path, config=config)
    Path(local.yd_root).mkdir(parents=True, exist_ok=True)
    Path(local.scratch_root).mkdir(parents=True, exist_ok=True)
    return config, local


def variant_dir(local: LocalConfig, source: str = "gfs") -> Path:
    return (
        Path(local.yd_root)
        / "input"
        / "models"
        / ("yd_gfs" if source == "gfs" else "yd_ifs")
    )


def write_variant(local: LocalConfig, *, source: str = "gfs") -> Path:
    """写 `yd.cfg.ic`（合法原生格式）+ `yd.para` + `yd.binding`。"""
    root = variant_dir(local, source)
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{PROJECT}.cfg.ic").write_bytes(
        build_cfg_ic(
            mesh_count=2,
            river_count=REACH_COUNT,
            minute=f"{ABSOLUTE_MINUTE}.000000",
        ).payload
    )
    (root / f"{PROJECT}.para").write_bytes(PARAMETER_TEMPLATE)
    (root / "yd.binding").write_bytes(BINDING)
    return root


def write_state(local: LocalConfig, *, source: str = "gfs") -> Path:
    """`states/<source>/<T>.cfg.ic`：绝对时间头对应 T。"""
    path = Path(local.yd_root) / "states" / source / f"{cycle_text()}.cfg.ic"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        build_cfg_ic(
            mesh_count=2,
            river_count=REACH_COUNT,
            minute=f"{ABSOLUTE_MINUTE}.000000",
        ).payload
    )
    return path


def raw_root(local: LocalConfig) -> Path:
    return Path(local.nwm.raw_root)


def write_raw_cycle(
    local: LocalConfig, *, source: str = "gfs", cycle: datetime = CYCLE
) -> Path:
    """写 `raw/<SEG>/<cycle>/` 的 bundle 与源 manifest.json（rawcopy 可承接形态）。"""
    segment = DIR_SEGMENTS[source]
    base = raw_root(local) / segment / cycle_text(cycle)
    base.mkdir(parents=True, exist_ok=True)
    pattern = GFS_BUNDLE if source == "gfs" else IFS_BUNDLE
    variables = GFS_VARIABLES if source == "gfs" else IFS_VARIABLES
    leads = GFS_LEADS if source == "gfs" else IFS_LEADS
    for lead in leads:
        name = pattern.format(cycle_hour=cycle.hour, lead=lead)
        (base / name).write_bytes(b"GRIB\xff\x00lead-%03d" % lead)
    manifest = source_manifest(
        source=source, cycle=cycle, leads=leads, variables=variables
    )
    (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return base


def source_manifest(
    *, source: str, cycle: datetime, leads, variables
) -> dict[str, object]:
    """合成 NWM 落盘的源 manifest（DownloadManifest.as_dict 同形，六键承接）。"""
    segment = DIR_SEGMENTS[source]
    pattern = GFS_BUNDLE if source == "gfs" else IFS_BUNDLE
    entries = []
    for lead in leads:
        name = pattern.format(cycle_hour=cycle.hour, lead=lead)
        cycle_iso = cycle.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        valid_iso = (cycle + timedelta(hours=lead)).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        remote = f"https://mirror.invalid/{segment}/{cycle_text(cycle)}/{name}"
        for variable in variables:
            entries.append(
                {
                    "remote_url": remote,
                    "local_key": f"mirror/raw/{segment}/{cycle_text(cycle)}/{name}",
                    "variable": variable,
                    "forecast_hour": lead,
                    "expected_checksum": None,
                    "expected_size_bytes": None,
                    "metadata": {
                        "cycle_time": cycle_iso,
                        "valid_time": valid_iso,
                        "bundle": {
                            "layout": "per_forecast_hour",
                            "variables": list(variables),
                        },
                        "grib_short_name": variable.split("_")[0],
                        "cfgrib_filter_by_keys": {"shortName": variable},
                        "logical_remote_url": remote,
                    },
                }
            )
    return {
        "source_id": f"mirror-{segment}",
        "cycle_time": cycle.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
        "manifest_uri": f"s3://nwm/raw/{segment}/{cycle_text(cycle)}/manifest.json",
        "metadata": {
            "first_forecast_hour": leads[0],
            "last_forecast_hour": leads[-1],
            "forecast_hours": list(leads),
            "requested_forecast_hours": list(leads),
        },
        "entries": entries,
    }


def checkpoint_payload(*, minute: str = RELATIVE_MINUTE) -> bytes:
    """tracker 捕获/补跑 candidate 的合法原生状态：relative-720 header + river 满行。"""
    return build_cfg_ic(mesh_count=3, river_count=REACH_COUNT, minute=minute).payload


def run_identity(source: str = "gfs", cycle: datetime = CYCLE) -> WorkIdentity:
    """driver / hook 共用的 WorkIdentity（同 assembly_fixtures 的合成身份 + project=yd）。"""
    return WorkIdentity(
        source_id=source,
        cycle_time=cycle,
        model_id="demo_model",
        basin_id="basin_a",
        basin_version_id="basin_v1",
        river_network_version_id="rivnet_v1",
        project_name=PROJECT,
    )


def canonical_grid(value: WorkIdentity) -> dict[str, object]:
    """合成 direct-grid grid.json（source-specific grid_id）。"""
    return {
        "cells": [
            {"id": "cell-one", "lon": 1.0, "lat": 2.0},
            {"id": "cell-two", "lon": 6.0, "lat": 7.0},
        ]
    }


def write_canonical_catalog(store: LocalObjectStore, value: WorkIdentity) -> None:
    """合成 canonical catalog/NetCDF：显式 synthetic，不加 raw->canonical 数值断言。

    与 `assembly_fixtures.write_file_repository_canonical_catalog` 同形，但按 source
    选用正确的压力变量名（gfs -> `pressure_surface`，ifs -> `surface_pressure`），
    使 GFS/IFS 两源都可用真实 `FileForcingRepository`/`ForcingProducer` 跑通。
    """
    source = value.source_id
    grid_key = f"canonical/{source}/grid/{source}_grid/grid.json"
    store.write_bytes_atomic(
        grid_key, json.dumps(canonical_grid(value), separators=(",", ":")).encode()
    )
    pressure_variable = "surface_pressure" if source == "ifs" else "pressure_surface"
    products: list[dict[str, object]] = []
    for variable, (unit, number) in (
        ("prcp_rate_or_amount", ("mm/day", 1.0)),
        ("air_temperature_2m", ("degC", 10.0)),
        ("relative_humidity_2m", ("0-1", 0.5)),
        ("wind_u_10m", ("m/s", 3.0)),
        ("wind_v_10m", ("m/s", 4.0)),
        (pressure_variable, ("Pa", 101000.0)),
        ("shortwave_down", ("W/m2", 250.0)),
    ):
        # 每个变量在两个 lead 都产出（`_missing_product_details` 取全部变量 valid_time
        # 的并集；GFS 的 gap allowance 只对 gfs 生效，IFS 要求每变量覆盖每个并集时刻）。
        for lead in (0, 3):
            identifier = f"{source}_{value.cycle_time:%Y%m%d%H}_{variable}_f{lead:03d}"
            key = (
                f"canonical/{source}/{value.cycle_time:%Y%m%d%H}/"
                f"{variable}/{identifier}.nc"
            )
            content = canonical_netcdf(variable, value, unit, number, lead)
            store.write_bytes_atomic(key, content)
            products.append(
                {
                    "canonical_product_id": identifier,
                    "source_id": source,
                    "source_version": f"{value.cycle_time:%Y%m%d%H}",
                    "cycle_time": value.cycle_time.isoformat().replace("+00:00", "Z"),
                    "valid_time": (value.cycle_time + timedelta(hours=lead))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "lead_time_hours": lead,
                    "variable": variable,
                    "unit": unit,
                    "grid_id": f"{source}_grid",
                    "grid_definition_uri": grid_key,
                    "native_time_resolution": "3h",
                    "native_spatial_resolution": "1deg",
                    "object_uri": key,
                    "checksum": sha256_bytes(content),
                    "quality_flag": "ok",
                    "lineage_json": {},
                }
            )
    catalog = {
        "schema_version": "nhms.canonical.product_catalog.v1",
        "source_id": source,
        "cycle_time": value.cycle_time.isoformat().replace("+00:00", "Z"),
        "products": products,
    }
    store.write_bytes_atomic(
        f"canonical/{source}/{value.cycle_time:%Y%m%d%H}/_catalog/catalog.json",
        json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode(),
    )


# --- terminal hook + driver ---------------------------------------------------


@dataclass
class HookState:
    """hook 与 driver 之间的进程内交接槽（M2 注入；不构成跨进程 receipt）。"""

    run_directory: RunDirectory | None = None
    tracker: CheckpointTracker | None = None


class InProcessDriver:
    """进程内 AttemptDriver：只交显式 identity/command/DAT，collect 只返还已存在产物。

    `collect` 绝不创建文件——若 hook 未在 SUCCEEDED 跃迁内完成真链，这里如实失败。
    """

    def __init__(self, state: HookState) -> None:
        self._state = state
        self._request = None
        self._log_path = None

    def prepare(self, *, request):
        self._request = request
        scratch_dat = request.work_dir / "output" / "yd.rivqdown.dat"
        self._log_path = request.work_dir / "job.log"
        return __import__(
            "yd_producer.controller",
            fromlist=["PreparedAttempt"],
        ).PreparedAttempt(
            identity=run_identity(request.source, request.cycle),
            command=(request.shud_binary, "--cycle", cycle_text(request.cycle)),
            scratch_dat=scratch_dat,
        )

    def collect(self, *, attempt, terminal_record):
        if self._state.run_directory is None or self._state.tracker is None:
            raise RuntimeError(
                "terminal hook 未执行：attempt 产物不存在，collect 拒绝伪造"
            )
        return __import__(
            "yd_producer.controller", fromlist=["AttemptProducts"]
        ).AttemptProducts(
            job_id=terminal_record.job_id,
            run_directory=self._state.run_directory,
            tracker=self._state.tracker,
            scratch_dat=attempt.scratch_dat,
            merged_log=self._log_path,
        )


def consume_raw_manifest(*, request) -> None:
    """terminal hook 的先验：逐字读本轮 raw manifest 并逐条回读其 `local_key`。

    走**同一** `LocalObjectStore(request.object_store_root)`：manifest 与全部 raw
    副本必须在该 root 下真实可读（若 controller 误传 attempt-work 根，consumer 找不到
    raw 即红）。任何一条缺失/不可读都让 hook（进而本轮）失败——这是「fake hook 必须
    真正消费 manifest」的独立判别器。
    """
    store = LocalObjectStore(request.object_store_root)
    manifest_path = Path(request.raw_manifest_path)
    if manifest_path.parent != request.object_store_root:
        raise RuntimeError(
            f"raw manifest {manifest_path} 不在 object-store 根 "
            f"{request.object_store_root}"
        )
    # manifest 本身不是 object key（raw-manifest.json 在 store 根），走普通有界读；
    # 其声明的每条 entry.local_key 才是 object key，经同一 store 的 resolve_path 回读。
    from yd_producer.store.safe_fs import read_bytes_limited_no_follow

    payload = json.loads(
        read_bytes_limited_no_follow(manifest_path, max_bytes=16 * 1024 * 1024)
    )
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("raw manifest 无 entries")
    for entry in entries:
        key = entry.get("local_key")
        if not isinstance(key, str) or not key.startswith("raw/"):
            raise RuntimeError(f"raw manifest entry 的 local_key 非法：{key!r}")
        content = store.read_bytes_limited(key, max_bytes=16 * 1024 * 1024)
        if not content:
            raise RuntimeError(f"staged raw 副本为空：{key}")


def make_terminal_hook(request, state: HookState, *, recovery: bool = False):
    """构造只在 fake 首次 SUCCEEDED 跃迁内执行一次的 terminal hook。

    序列：先 `consume_raw_manifest`（同根凭证），再写合成 canonical catalog，然后
    `stage_work_registry -> FileForcingRepository -> ForcingProducer -> assemble`，
    驱动同一 `CheckpointTracker`（`recovery=False` 主跑捕获 720；`recovery=True`
    以 #17 recovery seam 在同一 hook 内补跑），最后写 v2 DAT 与合并日志。

    `recovery=False`：主跑捕获 720（直接写 `.cfg.ic.update` 后 `capture_available`）；
    `recovery=True`：跳过主跑捕获，hook 内以 #17 recovery seam 补跑。
    """

    def hook() -> None:
        identity = run_identity(request.source, request.cycle)
        consume_raw_manifest(request=request)
        store = LocalObjectStore(request.object_store_root)
        write_canonical_catalog(store, identity)
        registry = stage_work_registry(
            work_root=request.work_root,
            identity=identity,
            contract=file_repository_contract(identity),
            binding_content=BINDING,
            sp_att_content=SP_ATT,
            max_asset_bytes=4096,
        )
        repository = FileForcingRepository(store, registry.registry_manifest)
        producer = ForcingProducer(
            config=ForcingProducerConfig(
                workspace_root=registry.work_dir,
                object_store_root=registry.object_store_root,
                object_store_prefix="",
            ),
            repository=repository,
            object_store=store,
        )
        forcing = producer.produce(
            source_id=identity.source_id,
            cycle_time=identity.cycle_time,
            model_id=identity.model_id,
            basin_id=identity.basin_id,
            basin_version_id=identity.basin_version_id,
            river_network_version_id=identity.river_network_version_id,
        )
        states_root = request.state_path.parent.parent
        run_directory = assemble(
            registry=registry,
            variant_dir=request.variant_dir,
            forcing=forcing,
            states_root=str(states_root),
            state_path=str(request.state_path),
        )
        tracker = CheckpointTracker(
            run_dir=run_directory.path,
            project_name=identity.project_name,
            checkpoint_hours=(12,),
        )
        if recovery:
            payload = checkpoint_payload()

            def recovery_runner(*, run_directory, output_dir):
                (output_dir / f"{identity.project_name}.cfg.ic.update").write_bytes(
                    payload
                )
                return 0

            ensure_twelve_hour_checkpoint(
                tracker=tracker,
                run_directory=run_directory,
                runner=recovery_runner,
            )
        else:
            update = run_directory.path / f"{identity.project_name}.cfg.ic.update"
            update.write_bytes(checkpoint_payload())
            tracker.capture_available()
            if tracker.missing_hours():
                raise RuntimeError(
                    f"主跑捕获未命中 720：missing={tracker.missing_hours()}"
                )
        dat = request.work_dir / "output" / "yd.rivqdown.dat"
        dat.parent.mkdir(parents=True, exist_ok=True)
        dat.write_bytes(
            build_dat_bytes(
                nc=request.reach_count,
                rows=request.forecast_days * 24,
            )
        )
        (request.work_dir / "job.log").write_bytes(
            f"job stdout for {cycle_text(request.cycle)}\n".encode()
        )
        state.run_directory = run_directory
        state.tracker = tracker

    return hook


class HookedExecutor:
    """包住真实 `FakeJobExecutor` 的窄 wrapper（tasks.md「M2 fake 端到端 oracle」）。

    只在底层 `poll` **首次**从非终态跃迁为 `SUCCEEDED` 时调用一次 terminal hook；
    其余 submit/poll/records/submissions/inflight/max_inflight 完全委派给 fake，
    不修改 `executor.py` 协议或 fake 行为。
    """

    def __init__(self, executor: FakeJobExecutor, hook) -> None:
        self._executor = executor
        self._hook = hook
        self._fired = False
        self._previous: JobRecord | None = None
        self._job_name: str | None = None

    def submit(self, spec):
        record = self._executor.submit(spec)
        self._previous = record
        self._job_name = spec.name
        return record

    def poll(self, job_id: str) -> JobRecord:
        record = self._executor.poll(job_id)
        if (
            not self._fired
            and self._previous is not None
            and not self._previous.state.is_terminal
            and record.state is JobState.SUCCEEDED
        ):
            self._fired = True
            self._hook(job_id=job_id)
        self._previous = record
        return record

    @property
    def submissions(self):
        return self._executor.submissions

    @property
    def max_inflight(self):
        return self._executor.max_inflight

    def inflight(self):
        return self._executor.inflight()


# --- publish 三态注入 ---------------------------------------------------------


def make_publish_inputs(
    *, local: LocalConfig, config: Config, source: str = "gfs"
) -> publish.PublishInputs:
    """success 链的 `PublishInputs` 正例（供 #94 四轴测试复用或偏移）。"""
    work_root = Path(local.scratch_root).resolve() / "work"
    work_dir = work_root / source / cycle_text()
    return publish.PublishInputs(
        yd_root=local.yd_root,
        source=source,
        cycle=CYCLE,
        scratch_dat=work_dir / "output" / "yd.rivqdown.dat",
        scratch_checkpoint=(
            work_dir / "model" / "state_checkpoints" / f"{PROJECT}.f012.cfg.ic.update"
        ),
        merged_log=work_dir / "job.log",
        work_dir=work_dir,
        work_root=work_root,
        expected_rows=EXPECTED_ROWS,
        reach_count=REACH_COUNT,
        variant_reach_count=REACH_COUNT,
    )
