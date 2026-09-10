"""`yd_producer.rawcopy` 测试共用的合成 fixture。

从原 `test_rawcopy.py` 内联合成 fixture 块整段迁出（至 `expect_kind`）。
"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from yd_producer.config import (
    CanonicalGridConfig,
    Config,
    CycleConfig,
    RawConfig,
    RawSourceConfig,
    SlurmSchema,
    VariantsConfig,
)
from yd_producer.rawcopy import stage_raw
from yd_producer.rawscan import judge

# --- 内联合成 fixture --------------------------------------------------------

CYCLE = datetime(2026, 3, 4, 0, tzinfo=UTC)
CYCLE_DIR = "2026030400"
CYCLE_ISO = "2026-03-04T00:00:00+00:00"

# 入参 source → raw 目录段/存储身份。**逐字写死**，MUST NOT 从被测模块 import
# `SOURCE_DIR_NAMES`。事实来源：NWM@8ae9b8f2 packages/common/source_identity.py:5-9
# 的 `_STORAGE_SOURCE_IDS = {"GFS": "gfs", "ERA5": "ERA5", "IFS": "IFS"}`。
DIR_SEGMENTS = {"ifs": "IFS", "gfs": "gfs"}

GFS_BUNDLE = "gfs.t{cycle_hour}z.pgrb2.0p25.f{lead}.bundle.grib2"
IFS_BUNDLE = "ifs.t{cycle_hour}z.f{lead}.bundle.grib2"
GFS_SECOND_BUNDLE = "gfs.t{cycle_hour}z.pgrb2.0p25.f{lead}.sfc.grib2"

LEADS = (0, 3, 6)
GFS_VARIABLES = ("tmp2m", "apcp", "rh2m", "dswrf")
IFS_VARIABLES = ("2t", "tp")

# 合成的 GRIB short name 表（只为行使承接，不代表 pin 取值）。
SHORT_NAMES = {
    "tmp2m": "2t",
    "apcp": "tp",
    "rh2m": "r2",
    "dswrf": "dswrf",
    "2t": "2t",
    "tp": "tp",
    # 源侧**多出**的那个变量（见 `SOURCE_EXTRA_VARIABLE`），本轮 config 不请求它。
    "sfcwind": "10si",
}

MANIFEST_NAME = "raw-manifest.json"
SOURCE_MANIFEST_NAME = "manifest.json"

# 两条**诊断消息**的片段。准入地板与各消费点自己的闸门给出的 kind 相同
# （都是 `source-manifest`），于是「这条失败由哪一级诊断」只能判在消息上：闸门给具名
# 诊断，地板给泛化兜底。逐字写死，MUST NOT 从被测模块 import。
STRUCTURE_MESSAGE = "结构不合 NWM DownloadManifest 形态"
FLOOR_MESSAGE = "准入期出现未预期的异常"

# entry metadata 的六个承接键（NWM@8ae9b8f2 gfs_adapter.py:623-634）。
CARRIED_KEYS = (
    "cycle_time",
    "valid_time",
    "bundle",
    "grib_short_name",
    "cfgrib_filter_by_keys",
    "logical_remote_url",
)


def make_source(
    *,
    lead_hours=LEADS,
    variables=GFS_VARIABLES,
    bundles=(GFS_BUNDLE,),
    f000_special=False,
) -> RawSourceConfig:
    return RawSourceConfig(
        lead_hours=tuple(lead_hours),
        variables=tuple(variables),
        bundles=tuple(bundles),
        f000_special=f000_special,
    )


def make_config(
    *,
    ifs: RawSourceConfig | None = None,
    gfs: RawSourceConfig | None = None,
) -> Config:
    return Config(
        forecast_days=7,
        output_interval_minutes=60,
        checkpoint_hours=(12,),
        reach_count=3988,
        nwm_mapping_builder_module="workers.mapping_builder.cli",
        # issue #20 新增的必需字段；rawcopy 不读它，但 `Config` 零默认值，缺它即构造失败。
        nwm_canonical_grid_id=CanonicalGridConfig(
            gfs="fixture-grid-gfs", ifs="fixture-grid-ifs"
        ),
        cycle=CycleConfig(hours=(0, 12)),
        variants=VariantsConfig(gfs="input/models/yd_gfs", ifs="input/models/yd_ifs"),
        raw=RawConfig(
            ifs=ifs
            if ifs is not None
            else make_source(variables=IFS_VARIABLES, bundles=(IFS_BUNDLE,)),
            gfs=gfs if gfs is not None else make_source(),
        ),
        slurm=SlurmSchema(required_fields=("partition", "account")),
    )


def bundle_name(source: str, lead: int) -> str:
    """独立字面 oracle：简单 token 仍须生成 pin 的两位/三位终名。"""
    pattern = GFS_BUNDLE if source == "gfs" else IFS_BUNDLE
    return pattern.replace("{cycle_hour}", f"{CYCLE.hour:02d}").replace(
        "{lead}", f"{lead:03d}"
    )


def cycle_dir(raw_root: Path, source: str) -> Path:
    return raw_root / DIR_SEGMENTS[source] / CYCLE_DIR


def local_key(source: str, lead: int) -> str:
    """pin 的 object-store key 形态：`raw/{source_id}/{compact_cycle}/{bundle}`
    （NWM@8ae9b8f2 gfs_adapter.py:615）。字面构造，不调用被测模块。
    """
    return f"raw/{DIR_SEGMENTS[source]}/{CYCLE_DIR}/{bundle_name(source, lead)}"


# 源 manifest 里 `local_key` 的**故意发散**前缀。yd 产出的 key 形态与 pin 逐字相同，
# 于是在一份合规的源 manifest 上「自己算」与「照抄源」两种实现取值恰好重合——用重合
# 的取值喂输入，断言就无法区分二者，成为自证式 fixture。源 manifest 是外部 JSON、
# 没有任何东西强制这种重合，故这里把输入侧偏移一个前缀：照抄源的实现会产出
# `nwm-bucket/raw/...`（`resolve_path` 后指向不存在的路径），自己算的实现不受影响。
SOURCE_LOCAL_KEY_PREFIX = "nwm-bucket/"

# 同一手法的其余四处**故意发散**。源 manifest 是不受信的外部 JSON，下列取值与 yd
# 自算值之间没有任何强制相等关系；写成相等就等于用「期望输出」去喂输入，
# 「承接」与「自算」两种实现在断言下不可区分（round-2 verifier 实测 P1/P2/P3/N1/
# N2/N3 六条变异体在 747 条全套件下全部存活，根因就是这种重合）。逐条：
#
# - `SOURCE_ID_PREFIX`：manifest 级 `source_id` MUST 由 yd 自算（存储身份逐源非
#   对称），故源侧带镜像前缀；照抄源的实现会产出 `mirror-gfs`。
# - `SOURCE_MANIFEST_CYCLE_ISO`：manifest 级 `cycle_time` MUST 自算（= 形参
#   `cycle`）。实现不交叉核对源 manifest 的 cycle，故这里放另一个 cycle 的值。
# - `SOURCE_TIME_SUFFIX`：**entry 级** `cycle_time`/`valid_time` 反过来 MUST 逐字
#   承接。`Z` 与 `+00:00` 是同一时刻的两种合法 ISO-8601 写法，pin 侧写哪种不受本仓
#   约束；自算的实现走 `datetime.isoformat()` 恒产出 `+00:00`，于是可判别。
# - `SOURCE_EXTRA_HOURS`：manifest 级四键 MUST 自算 = 本轮 lead 全集；源侧下载器
#   按它自己的 requested 集合落盘，yd 只要求「源覆盖本轮 lead」。
# - `SOURCE_REMOTE_HOST`：`remote_url` 取源 entry 的**同名字段**，MUST NOT 取承接
#   metadata 里的 `logical_remote_url`；pin 上两者是不同的 URL（镜像 vs 逻辑源）。
SOURCE_ID_PREFIX = "mirror-"
SOURCE_MANIFEST_CYCLE_ISO = "2026-03-03T12:00:00+00:00"
SOURCE_TIME_SUFFIX = "Z"
SOURCE_EXTRA_HOURS = (9, 12)
SOURCE_REMOTE_HOST = "https://mirror.invalid/"
LOGICAL_REMOTE_HOST = "https://example.invalid/"

# --- round-4 的**穷尽偏移清扫**（上面六条是 round 1/2 逐条找出来的实例；这里按谓词
# 补齐余下的每一条）。谓词（round-3 batch-C verifier 写下、此前从未被作为一次清扫
# 兑现）：**凡测试对产出 `raw-manifest.json` 断言的每一个值，源侧对应值 MUST 被偏移
# 使「承接自源」与「由 yd 自算」发散，且每处发散各由一个变红的变异体证明。**
# 两处**故意排除**（这是该类的真实边界，不是漏项）：`forecast_hour` 与 `variable`
# 是 (lead, variable) 的**查找键**本身，偏移它们只会让查找落空、判别的是查找而不是
# 实现，故两端必须重合。
#
# - `SOURCE_CHECKSUM`/`SOURCE_SIZE_BYTES`：产出侧三键一律 `None`（tasks.md:691 的
#   同一条 MUST 管三个字段）。round 2 只偏移了第三个 `manifest_uri`，前两个源侧同为
#   `None`，于是「写 None」与「照抄源」取值重合，断言退化成恒真式。
# - `SOURCE_UNCARRIED_METADATA_KEY`：entry metadata「含且**仅含**」的仅含半边——源侧
#   metadata 恰好只有承接键时，白名单实现与整份照抄逐字节相同。
# - `SOURCE_BUNDLE_TOKEN`/`SOURCE_CFGRIB_TOKEN`：`bundle` 与 `cfgrib_filter_by_keys`
#   的源侧取值原先可由 config 变量表 / `grib_short_name` **推导**出来，于是「承接」
#   与「按已知形状重建」不可区分。各塞一个不可推导的分量。
# - `SOURCE_EXTRA_VARIABLE` 与源侧多出的 lead：entry **集合与条数**由本轮 verdict
#   决定，而不是由源 manifest 的 entry 列表决定；源侧原先恰好只有本轮请求的那些
#   entry，于是「按 verdict 扇出」与「把源 entry 列表照搬」条数相同。
# - `SOURCE_ENTRY_ORDER_REVERSED`：entry **顺序**是 lead 升序 × variables 声明序，
#   源侧原先恰好同序，于是「照抄源顺序」不可判别。
# - `SOURCE_SHORT_NAME_TOKEN`（round-4 复审补）：`grib_short_name` 的源侧取值原先就是
#   标准别名（`tmp2m -> "2t"`），而那正是一个「按变量名查标准别名表自算」的实现会写
#   的值；两侧重合，`CARRIED_KEYS` 循环退化成恒真式（实测：把 fixture 的别名表改成
#   恒等映射后全套件仍全绿）。源侧加一个不可由任何别名表推导的后缀。
# - `SOURCE_IDX_TOKEN`（round-4 复审补）：复数 `idx_selectors` 此前只被断言过**键集**，
#   而键集可由 config 的变量表推导；把每个变量的 selector 整份伪造成空 Mapping（一个
#   字节都不承接）的实现全套件全绿。源侧每个 selector 里加一个不可推导的分量，并在
#   清扫里断言**取值**逐字承接。
SOURCE_CHECKSUM = (
    "sha256:0000000000000000000000000000000000000000000000000000000000c0ffee"
)
SOURCE_SIZE_BYTES = 424242
SOURCE_UNCARRIED_METADATA_KEY = "download_attempt"
SOURCE_BUNDLE_TOKEN = "pin-build-8ae9b8f2"
SOURCE_CFGRIB_TOKEN = "pin-filter-8ae9b8f2"
SOURCE_EXTRA_VARIABLE = "sfcwind"
SOURCE_ENTRY_ORDER_REVERSED = True
SOURCE_SHORT_NAME_TOKEN = "@pin-8ae9b8f2"
SOURCE_IDX_TOKEN_KEY = "idx_source_token"
SOURCE_IDX_TOKEN = "pin-idx-8ae9b8f2"


def source_iso(moment: datetime) -> str:
    """源 manifest 侧的 ISO 写法：`Z` 结尾，与 `datetime.isoformat()` 逐字不同。"""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S") + SOURCE_TIME_SUFFIX


def selector_for(variable: str, lead: int) -> dict[str, Any]:
    """合成的 `IdxSelection.as_metadata()` 四键（NWM@8ae9b8f2 :248-258）。

    另带一个**不可推导**的分量 `SOURCE_IDX_TOKEN_KEY`：没有它，「逐字承接复数键」与
    「按 config 的变量表现造一份 selector」在断言下同值。
    """
    return {
        "step_range": f"{max(lead - 3, 0)}-{lead}",
        "accumulation_type": "interval_bucket",
        "idx_record_number": 7 + lead,
        "selector_warning": None,
        SOURCE_IDX_TOKEN_KEY: SOURCE_IDX_TOKEN,
    }


def entry_payload(source: str, lead: int, variable: str) -> dict[str, Any]:
    # 源侧 short name 带一个不可由任何别名表推导的后缀：见 `SOURCE_SHORT_NAME_TOKEN`。
    short_name = SHORT_NAMES[variable] + SOURCE_SHORT_NAME_TOKEN
    segment = f"{DIR_SEGMENTS[source]}/{CYCLE_DIR}/"
    remote = SOURCE_REMOTE_HOST + segment
    logical = LOGICAL_REMOTE_HOST + segment
    return {
        "remote_url": remote + bundle_name(source, lead),
        "local_key": SOURCE_LOCAL_KEY_PREFIX + local_key(source, lead),
        "variable": variable,
        "forecast_hour": lead,
        # 源侧**非 None**：产出侧这两键 MUST 落 `None`（tasks.md:691）。源侧同为
        # `None` 时「写 None」与「照抄源」不可区分。
        "expected_checksum": SOURCE_CHECKSUM,
        "expected_size_bytes": SOURCE_SIZE_BYTES,
        "metadata": {
            "cycle_time": source_iso(CYCLE),
            "valid_time": source_iso(CYCLE + timedelta(hours=lead)),
            "bundle": {
                "layout": "per_forecast_hour",
                "variables": list(GFS_VARIABLES if source == "gfs" else IFS_VARIABLES),
                "physical_file_count": 1,
                # 不可由 config / 变量表推导的分量：没有它，「逐条承接」与「按已知
                # 三键重建」在断言下同值。
                "build_id": SOURCE_BUNDLE_TOKEN,
            },
            "grib_short_name": short_name,
            "cfgrib_filter_by_keys": {
                "shortName": short_name,
                # 同上：没有它，「承接」与「由 `grib_short_name` 现造一个单键
                # Mapping」同值。
                "filterToken": SOURCE_CFGRIB_TOKEN,
            },
            "logical_remote_url": logical + bundle_name(source, lead),
            # **非承接键**：产出侧 metadata「含且仅含」六键 + 两个 idx 键，源侧多出
            # 这一个键才让「仅含」半边有判别力（整份 `dict(metadata)` 会带上它）。
            SOURCE_UNCARRIED_METADATA_KEY: 3,
        },
    }


def source_manifest_payload(
    source: str,
    *,
    leads=LEADS,
    variables=None,
    with_idx: bool | None = None,
    with_requested: bool = True,
    declared_hours=None,
) -> dict[str, Any]:
    """合成 NWM 落盘的源 `manifest.json`（DownloadManifest.as_dict 同形）。

    `with_idx` 默认按源形态取：GFS 带 `idx_selectors`（云镜像下载路径注入），IFS 不带
    （pin 侧 IFS 全文无 idx 键）。

    单数 `idx_selector` **只在 `len(variables) == 1` 时**才另写：§3.1
    （`nwm-snapshot-inventory.md:113`）逐字记「L1071-1072 只有在 `len(variables) == 1`
    时才另写单数键」。原先在 4 变量 bundle 上每条 entry 都写单数键，既与本文件自己
    转录的 pin 事实相悖，又让「单数键由 yd 按变量从复数键取」与「照抄源侧单数键」
    两种实现不可区分（round-2 verifier 的 P1 变异体因此存活）。
    """
    variables = variables or (GFS_VARIABLES if source == "gfs" else IFS_VARIABLES)
    if with_idx is None:
        with_idx = source == "gfs"
    entries = []

    def emit(lead: int, vars_at_lead: tuple[str, ...]) -> None:
        # 每组自带一份 `selectors`：源侧多出的变量 MUST NOT 混进本轮请求变量那组的
        # 复数键，否则 `set(metadata["idx_selectors"]) == set(GFS_VARIABLES)` 会因为
        # 输入被污染而变红——那是「因错误的理由变红」，不是判别力。
        selectors = {var: selector_for(var, lead) for var in vars_at_lead}
        for variable in vars_at_lead:
            payload = entry_payload(source, lead, variable)
            if with_idx:
                payload["metadata"]["idx_selectors"] = selectors
                if len(vars_at_lead) == 1:
                    payload["metadata"]["idx_selector"] = selectors[variable]
            entries.append(payload)

    for lead in leads:
        emit(lead, tuple(variables))
        # 源侧多出一个本轮 config 未请求的变量：产出侧的 entry 集合由 verdict 决定。
        emit(lead, (SOURCE_EXTRA_VARIABLE,))
    span = list(declared_hours) if declared_hours else [*leads, *SOURCE_EXTRA_HOURS]
    for extra_lead in span:
        # 源侧多出本轮 lead 之外的 entry：源只需**覆盖**本轮 lead，不需相等。
        if extra_lead not in leads:
            emit(extra_lead, tuple(variables))
    if SOURCE_ENTRY_ORDER_REVERSED:
        # 源侧 entry 顺序与产出侧（lead 升序 × variables 声明序）**相反**：同序时
        # 「照抄源顺序」与「按 verdict 定序」不可区分。
        entries.reverse()
    metadata: dict[str, Any] = {
        "first_forecast_hour": min(span),
        "last_forecast_hour": max(span),
        "forecast_hours": list(span),
    }
    if with_requested:
        metadata["requested_forecast_hours"] = list(span)
    return {
        "source_id": SOURCE_ID_PREFIX + DIR_SEGMENTS[source],
        "cycle_time": SOURCE_MANIFEST_CYCLE_ISO,
        "manifest_uri": f"s3://nwm/raw/{DIR_SEGMENTS[source]}/{CYCLE_DIR}/manifest.json",
        "metadata": metadata,
        "entries": entries,
    }


def source_entry(payload: dict[str, Any], lead: int, variable: str) -> dict[str, Any]:
    """按 (lead, variable) 取源 manifest 的那条 entry。

    MUST NOT 用 `payload["entries"][0]`：源侧 entry 的**顺序与集合**都被刻意偏移过
    （见 `SOURCE_ENTRY_ORDER_REVERSED` / `SOURCE_EXTRA_VARIABLE`），下标会取到一条
    本轮根本不消费的 entry，用例就会因为「改了没人读的那条」而静默失去判别力。
    """
    matches = [
        entry
        for entry in payload["entries"]
        if entry["forecast_hour"] == lead and entry["variable"] == variable
    ]
    assert len(matches) == 1, (
        f"(lead={lead}, variable={variable!r}) 命中 {len(matches)} 条"
    )
    return matches[0]


def bundle_bytes(lead: int) -> bytes:
    # 非 UTF-8 前导字节：真实 bundle 是 GRIB2 二进制。
    return b"GRIB\xff\x00lead-%03d" % lead


def build_tree(
    tmp_path: Path,
    source: str = "gfs",
    *,
    leads=LEADS,
    manifest: dict[str, Any] | None = None,
    write_manifest: bool = True,
) -> tuple[Path, Path]:
    """铺一棵 raw fixture 树与一个空 work 根，返回 `(raw_root, work_dir)`。"""
    raw_root = tmp_path / "nwm-raw"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    base = cycle_dir(raw_root, source)
    base.mkdir(parents=True, exist_ok=True)
    for lead in leads:
        (base / bundle_name(source, lead)).write_bytes(bundle_bytes(lead))
    if write_manifest:
        payload = manifest if manifest is not None else source_manifest_payload(source)
        (base / SOURCE_MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")
    return raw_root, work_dir


def write_source_manifest(
    raw_root: Path, source: str, payload: dict[str, Any] | str
) -> None:
    target = cycle_dir(raw_root, source) / SOURCE_MANIFEST_NAME
    if isinstance(payload, str):
        target.write_text(payload, encoding="utf-8")
    else:
        target.write_text(json.dumps(payload), encoding="utf-8")


def snapshot(root: Path) -> dict[str, tuple[int, int, int, int]]:
    """递归快照：路径 -> `lstat` 元组 (size, mtime_ns, ino, mode)。

    零写入取证用它整棵比对，MUST NOT 只断言 `raw-manifest.json` 不存在——只查一个
    文件抓不到「建了目录」「落了半套副本」。
    """
    out: dict[str, tuple[int, int, int, int]] = {}
    for path in sorted(root.rglob("*")):
        status = os.lstat(path)
        out[str(path.relative_to(root))] = (
            status.st_size,
            status.st_mtime_ns,
            status.st_ino,
            status.st_mode,
        )
    return out


def content_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def staged(
    raw_root: Path,
    work_dir: Path,
    source: str = "gfs",
    config: Config | None = None,
    *,
    raw_root_arg=None,
):
    config = config or make_config()
    verdict = judge(raw_root, source, CYCLE, config)
    return stage_raw(
        verdict,
        raw_root if raw_root_arg is None else raw_root_arg,
        work_dir,
        source,
        CYCLE,
        config,
    )


def expect_kind(excinfo, kind: str) -> None:
    assert excinfo.value.kind == kind, f"实际 kind={excinfo.value.kind!r}"
