"""`yd_producer.rawcopy.stage_raw` 只读复制与临时 raw manifest 测试（任务 3.2）。

全部用例使用**内联合成配置值与合成源 manifest**：仓库刻意不提供版本化 `config.toml`
生产实例（归 issue #29），下方的变量名、bundle 模式与 manifest 取值只用于行使 staging
规则，不代表生产取值。

期望值一律在用例内**字面构造**：目录段、`local_key` 形态、承接键名、manifest 级四键
都不从被测模块 import——两侧共用一个字面量会让断言随实现同步漂移，退化成恒真式。
事实来源是 NWM@8ae9b8f2 与勘察清单 §3.1（`SOURCE_DIR_NAMES` 的逐源非对称、
`raw/{source_id}/{compact_cycle}/{bundle}` 的 key 形态、entry metadata 六键、
`idx_selector(s)` 的单复数分工、manifest 级四键）。
"""

import ast
import builtins
import errno
import inspect
import json
import os
import stat
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from yd_producer import rawcopy as rawcopy_module
from yd_producer.config import (
    Config,
    ConfigError,
    CycleConfig,
    RawConfig,
    RawSourceConfig,
    SlurmSchema,
    VariantsConfig,
)
from yd_producer.raw.manifest import DownloadManifest
from yd_producer.rawcopy import RawStagingError, stage_raw
from yd_producer.rawscan import judge
from yd_producer.store.object_store import LocalObjectStore

# --- 内联合成 fixture --------------------------------------------------------

CYCLE = datetime(2026, 3, 4, 0, tzinfo=UTC)
CYCLE_DIR = "2026030400"
CYCLE_ISO = "2026-03-04T00:00:00+00:00"

# 入参 source → raw 目录段/存储身份。**逐字写死**，MUST NOT 从被测模块 import
# `SOURCE_DIR_NAMES`。事实来源：NWM@8ae9b8f2 packages/common/source_identity.py:5-9
# 的 `_STORAGE_SOURCE_IDS = {"GFS": "gfs", "ERA5": "ERA5", "IFS": "IFS"}`。
DIR_SEGMENTS = {"ifs": "IFS", "gfs": "gfs"}

GFS_BUNDLE = "gfs.t{cycle_hour:02d}z.pgrb2.0p25.f{lead:03d}.bundle.grib2"
IFS_BUNDLE = "ifs.t{cycle_hour:02d}z.f{lead:03d}.bundle.grib2"
GFS_SECOND_BUNDLE = "gfs.t{cycle_hour:02d}z.pgrb2.0p25.f{lead:03d}.sfc.grib2"

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
    pattern = GFS_BUNDLE if source == "gfs" else IFS_BUNDLE
    return pattern.format(cycle_hour=CYCLE.hour, lead=lead)


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


# --- Row 1：正向产出（副本齐全 + 三元组集合相等 + 路径落在 work/raw/ 之下）----


def test_full_cycle_copies_files_and_manifest_triples_match(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    result = staged(raw_root, work_dir)

    expected_copies = tuple(
        work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", lead)
        for lead in LEADS
    )
    assert result.copied_files == expected_copies
    for lead, path in zip(LEADS, expected_copies, strict=True):
        assert path.read_bytes() == bundle_bytes(lead)
    assert result.manifest_path == work_dir / MANIFEST_NAME

    # 三元组完整性：集合**相等**（不是包含），两个方向都由本断言承担。
    expected_pairs = {(lead, var) for lead in LEADS for var in GFS_VARIABLES}
    assert {(e.forecast_hour, e.variable) for e in result.entries} == expected_pairs
    # 同一 (lead, bundle) 的全部变量 entry 共享同一个 local_key，且该 key 由 yd
    # **自己算**：源 manifest 的同名字段带 `nwm-bucket/` 前缀，照抄源就会取到它。
    for lead in LEADS:
        keys = {e.local_key for e in result.entries if e.forecast_hour == lead}
        assert keys == {local_key("gfs", lead)}
        assert keys != {entry_payload("gfs", lead, "tmp2m")["local_key"]}
    # entry 顺序：lead 升序 × variables 声明序。
    assert [(e.forecast_hour, e.variable) for e in result.entries] == [
        (lead, var) for lead in LEADS for var in GFS_VARIABLES
    ]

    # `local_key` 是 object-store key，**每一条**经 resolve_path 都落在 work/raw/ 之下
    # 且指向已存在的副本。在发散前缀的 fixture 下这条断言才有判别力：照抄源 key 的
    # 实现会解析到 `<work>/nwm-bucket/raw/...`，既不在 work/raw 之下也不存在。
    store = LocalObjectStore(root=work_dir)
    assert len(result.entries) == len(LEADS) * len(GFS_VARIABLES)
    for entry in result.entries:
        resolved = store.resolve_path(entry.local_key)
        assert resolved.is_file()
        assert (work_dir / "raw") in resolved.parents


def test_entries_missing_one_pair_would_break_set_equality(tmp_path: Path) -> None:
    """三元组集合相等的**两个方向**各自可判：少一条与多一条都不等于期望集。"""
    raw_root, work_dir = build_tree(tmp_path)
    result = staged(raw_root, work_dir)
    pairs = [(e.forecast_hour, e.variable) for e in result.entries]
    expected_pairs = {(lead, var) for lead in LEADS for var in GFS_VARIABLES}
    assert set(pairs[1:]) != expected_pairs  # 漏一条即不等
    assert {*pairs, (99, "apcp")} != expected_pairs  # 多一条即不等
    assert len(pairs) == len(set(pairs)) == len(expected_pairs)


# --- Row：产出 manifest 的正向 schema 断言 -----------------------------------


def test_manifest_json_matches_the_producer_consumer_contract(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    result = staged(raw_root, work_dir)
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    roundtrip = DownloadManifest.from_dict(payload)
    assert roundtrip.source_id == "gfs"  # 存储身份逐源非对称（GFS 小写）
    assert roundtrip.cycle_time == CYCLE
    assert payload["manifest_uri"] is None
    assert payload["metadata"] == {
        "first_forecast_hour": 0,
        "last_forecast_hour": 6,
        "requested_forecast_hours": [0, 3, 6],
        "forecast_hours": [0, 3, 6],
    }
    assert len(payload["entries"]) == len(LEADS) * len(GFS_VARIABLES)
    for entry in payload["entries"]:
        lead = entry["forecast_hour"]
        variable = entry["variable"]
        assert entry["local_key"] == local_key("gfs", lead)
        assert entry["expected_checksum"] is None
        assert entry["expected_size_bytes"] is None
        assert entry["remote_url"] == entry_payload("gfs", lead, variable)["remote_url"]
        metadata = entry["metadata"]
        assert set(metadata) == {*CARRIED_KEYS, "idx_selector", "idx_selectors"}
        for key in CARRIED_KEYS:
            assert (
                metadata[key] == entry_payload("gfs", lead, variable)["metadata"][key]
            )
        # 单数键是**该变量**的 Mapping，不是整个复数 Mapping。
        assert metadata["idx_selector"] == selector_for(variable, lead)
        assert set(metadata["idx_selectors"]) == set(GFS_VARIABLES)


def test_ifs_source_stages_without_any_idx_key(tmp_path: Path) -> None:
    """IFS 作用域证据：无 apcp、无 idx_selectors -> 正常产出，两个 idx 键**缺席**。"""
    raw_root, work_dir = build_tree(tmp_path, "ifs")
    result = staged(raw_root, work_dir, "ifs")
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert payload["source_id"] == "IFS"  # 存储身份逐源非对称（IFS 大写）
    assert {(e.forecast_hour, e.variable) for e in result.entries} == {
        (lead, var) for lead in LEADS for var in IFS_VARIABLES
    }
    for entry in payload["entries"]:
        assert set(entry["metadata"]) == set(CARRIED_KEYS)
    assert result.copied_files == tuple(
        work_dir / "raw" / "IFS" / CYCLE_DIR / bundle_name("ifs", lead)
        for lead in LEADS
    )


def test_source_without_requested_forecast_hours_still_stages(tmp_path: Path) -> None:
    """源侧只强制 `forecast_hours`；`requested_forecast_hours` 缺席（IFS 的 pin 形态）
    MUST NOT 失败，yd 自己写该键 = 本轮 lead 全集。
    """
    raw_root, work_dir = build_tree(
        tmp_path,
        "gfs",
        manifest=source_manifest_payload("gfs", with_requested=False),
    )
    result = staged(raw_root, work_dir)
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["requested_forecast_hours"] == [0, 3, 6]
    assert payload["metadata"]["forecast_hours"] == [0, 3, 6]


def test_gfs_f000_special_trims_variables_but_keeps_the_file(tmp_path: Path) -> None:
    config = make_config(gfs=make_source(f000_special=True))
    raw_root, work_dir = build_tree(tmp_path)
    result = staged(raw_root, work_dir, "gfs", config)
    lead0 = {e.variable for e in result.entries if e.forecast_hour == 0}
    assert lead0 == {"tmp2m", "rh2m"}  # apcp/dswrf 在 f000 无定义
    assert {e.variable for e in result.entries if e.forecast_hour == 3} == set(
        GFS_VARIABLES
    )
    # f000 只削变量集，不削文件集。
    assert work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 0) in set(
        result.copied_files
    )
    assert len(result.copied_files) == len(LEADS)


# --- Row：incomplete verdict 零写入 -------------------------------------------


def test_incomplete_verdict_refuses_with_zero_writes(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    (cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)).unlink()
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    assert verdict.complete is False
    before = snapshot(work_dir)
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "incomplete-verdict")
    assert snapshot(work_dir) == before == {}


# --- Row：R4B2 三条 fail-closed ----------------------------------------------


def _manifest_without_selector_key(key: str, *, variable: str = "apcp"):
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        if entry["variable"] == variable:
            entry["metadata"]["idx_selectors"][variable].pop(key, None)
    return payload


def test_missing_apcp_accumulation_type_fails_closed(tmp_path: Path) -> None:
    payload = _manifest_without_selector_key("accumulation_type")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    before = snapshot(work_dir)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "accumulation-metadata")
    assert snapshot(work_dir) == before == {}


def test_out_of_domain_accumulation_type_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        if entry["variable"] == "apcp":
            entry["metadata"]["idx_selectors"]["apcp"]["accumulation_type"] = "unknown"
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    before = snapshot(work_dir)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "accumulation-metadata")
    assert snapshot(work_dir) == before == {}


def test_interval_bucket_without_step_range_fails_closed(tmp_path: Path) -> None:
    payload = _manifest_without_selector_key("step_range")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "accumulation-metadata")
    assert snapshot(work_dir) == {}


def test_accumulation_aliases_are_accepted(tmp_path: Path) -> None:
    """别名 `accumulation_policy`/`stepRange` 同样可满足 R4B2（域检查覆盖别名）。"""
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        selector = entry["metadata"]["idx_selectors"][entry["variable"]]
        selector["accumulation_policy"] = selector.pop("accumulation_type")
        selector["stepRange"] = selector.pop("step_range")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    assert len(result.entries) == len(LEADS) * len(GFS_VARIABLES)


def test_cumulative_since_cycle_without_step_range_is_accepted(tmp_path: Path) -> None:
    """只有 `interval_bucket` 才要求区间范围。"""
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        selector = entry["metadata"]["idx_selectors"][entry["variable"]]
        selector["accumulation_type"] = "cumulative_since_cycle"
        selector.pop("step_range")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    assert len(result.entries) == len(LEADS) * len(GFS_VARIABLES)


# --- Row：源 manifest 相关 fail-closed ---------------------------------------


def test_missing_manifest_level_forecast_hours_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    payload["metadata"].pop("forecast_hours")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_non_list_forecast_hours_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    # 取一个**逐字符都可转成 lead** 的字符串：若类型闸门退化成「可迭代即可」，
    # 该值会一路走到成功产出，本用例才是 list 类型闸门自己的判别器。
    payload["metadata"]["forecast_hours"] = "036"
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")


def test_forecast_hours_not_covering_a_lead_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs", declared_hours=(0, 3))
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_superset_forecast_hours_is_accepted(tmp_path: Path) -> None:
    """反向不要求：源可以比 yd 要的多。"""
    payload = source_manifest_payload("gfs", declared_hours=(0, 3, 6, 9))
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    written = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert written["metadata"]["forecast_hours"] == [0, 3, 6]


def test_absent_source_manifest_fails_closed(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path, write_manifest=False)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_unparsable_source_manifest_fails_closed(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    write_source_manifest(raw_root, "gfs", "{not json")
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")


def test_source_entries_not_covering_a_variable_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    payload["entries"] = [e for e in payload["entries"] if e["variable"] != "rh2m"]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_missing_carried_metadata_key_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        entry["metadata"].pop("cfgrib_filter_by_keys")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")


# --- Row：承接来的 entry 时间与 (cycle, lead) 槽位的一致性 --------------------
#
# 承接是逐字的，但一条「时间属于另一轮」的源 entry 若被照单全收，产出的
# `raw-manifest.json` 会在 manifest 级声明一个 cycle（yd 自算）、在每条 entry 上声明
# 另一个，且全部 lead 共用同一个 `valid_time`——一份自相矛盾的落盘产物，而这份矛盾
# yd 从自己的入参就能判出来。下面四条按**分量**取判别器：`cycle_time` 分量、
# `valid_time` 分量、解析腿各一条，外加一条证明比较取在**时刻**而不是文本上的绿用例。


def _retime_entries(
    payload: dict[str, Any],
    *,
    cycle_time=None,
    valid_time=None,
) -> None:
    """把每条源 entry 的两个时间键改写成给定值（可调用则按 lead 取值）。"""
    for entry in payload["entries"]:
        lead = entry["forecast_hour"]
        if cycle_time is not None:
            entry["metadata"]["cycle_time"] = (
                cycle_time(lead) if callable(cycle_time) else cycle_time
            )
        if valid_time is not None:
            entry["metadata"]["valid_time"] = (
                valid_time(lead) if callable(valid_time) else valid_time
            )


def test_entries_labelled_with_another_cycle_are_refused(tmp_path: Path) -> None:
    """整份源 manifest 的 entry 时间属于另一轮 -> 拒绝，零写入。

    这是 round-4 的复现输入：staging 原本**成功**，产出 manifest 的 manifest 级
    `cycle_time` 是本轮、每条 entry 是 2019 年那轮，且三个 lead 的 `valid_time` 相同。
    """
    payload = source_manifest_payload("gfs")
    other_cycle = datetime(2019, 7, 4, 18, tzinfo=UTC)
    _retime_entries(
        payload,
        cycle_time=source_iso(other_cycle),
        valid_time=source_iso(other_cycle + timedelta(hours=3)),
    )
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_entry_cycle_time_alone_off_by_one_cycle_is_refused(tmp_path: Path) -> None:
    """`cycle_time` **分量**的判别器：只有它错、`valid_time` 全部正确。"""
    payload = source_manifest_payload("gfs")
    _retime_entries(payload, cycle_time=source_iso(CYCLE - timedelta(hours=6)))
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert "cycle_time" in str(excinfo.value)
    assert snapshot(work_dir) == {}


def test_entry_valid_time_alone_not_matching_the_lead_is_refused(
    tmp_path: Path,
) -> None:
    """`valid_time` **分量**的判别器：`cycle_time` 全部正确，`valid_time` 恒等于
    cycle（即 round-4 复现里「所有 lead 同一个 valid_time」那半边）。

    lead 0 上它恰好是对的，判别力来自 lead 3/6——故本用例同时证明该分量是**逐 lead**
    判的，不是只看一条。
    """
    payload = source_manifest_payload("gfs")
    _retime_entries(payload, valid_time=source_iso(CYCLE))
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert "valid_time" in str(excinfo.value)
    assert snapshot(work_dir) == {}


@pytest.mark.parametrize(
    ("bad_value", "leg"),
    [
        # `json.load` 会产出的非字符串形态：`parse_cycle_time` 在 `.strip()` 上抛
        # `AttributeError`。
        (20260304, "AttributeError"),
        # 字符串但不是 ISO-8601：在 `fromisoformat` 上抛 `ValueError`。
        ("not-a-time", "ValueError"),
    ],
)
def test_unparseable_entry_time_is_refused_by_the_entry_time_gate(
    tmp_path: Path, bad_value: Any, leg: str
) -> None:
    """解析腿的判别器：不可解析的时间由**本闸门**以具名消息拒，不是掉进准入地板。

    地板对二者给出同一个 kind，故判别器取在**消息**上：闸门说「不是可解析的时刻」，
    地板说「准入期出现未预期的异常」。去掉解析腿就只剩后者。
    `except (AttributeError, ValueError)` 是复合闸门，故**两个分量各一行参数**：
    只收窄到其中一个，另一行就会红。
    """
    payload = source_manifest_payload("gfs")
    _retime_entries(payload, cycle_time=bad_value)
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert "不是可解析的时刻" in str(excinfo.value), leg
    assert "准入期出现未预期的异常" not in str(excinfo.value)
    assert snapshot(work_dir) == {}


def test_entry_times_written_in_another_offset_stage_normally(tmp_path: Path) -> None:
    """一致性核对取在**时刻**上，不是文本上：源侧用 `-05:00` 写同一批时刻 ->
    正常产出，且落盘值仍是源侧那串**原文本**（承接是逐字的，核对不改写）。

    这是「按字符串比 `cycle.isoformat()`」这种退化实现的判别器：那样写会把一份
    完全正确的源 manifest 拒掉。
    """

    def offset_iso(moment: datetime) -> str:
        return moment.astimezone(timezone(timedelta(hours=-5))).isoformat()

    payload = source_manifest_payload("gfs")
    _retime_entries(
        payload,
        cycle_time=offset_iso(CYCLE),
        valid_time=lambda lead: offset_iso(CYCLE + timedelta(hours=lead)),
    )
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    produced = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert produced["cycle_time"] == CYCLE_ISO  # manifest 级仍由 yd 自算
    for entry in produced["entries"]:
        metadata = entry["metadata"]
        assert metadata["cycle_time"] == offset_iso(CYCLE)
        assert metadata["cycle_time"].endswith("-05:00")
        assert metadata["valid_time"] == offset_iso(
            CYCLE + timedelta(hours=entry["forecast_hour"])
        )


# --- Row：verdict-mismatch 与其相对路径对照 ----------------------------------


def test_raw_root_from_another_call_site_is_rejected(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    other_root = tmp_path / "other-raw"
    other_base = cycle_dir(other_root, "gfs")
    other_base.mkdir(parents=True)
    for lead in LEADS:
        (other_base / bundle_name("gfs", lead)).write_bytes(bundle_bytes(lead))
    write_source_manifest(other_root, "gfs", source_manifest_payload("gfs"))
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, other_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "verdict-mismatch")
    assert snapshot(work_dir) == {}


def test_relative_raw_root_from_the_same_call_site_stages_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """containment 检查 MUST NOT 误拒合法的相对 `raw_root` 调用（同法提升的判别器）。"""
    _raw_root, work_dir = build_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    config = make_config()
    verdict = judge("nwm-raw", "gfs", CYCLE, config)
    result = stage_raw(verdict, "nwm-raw", work_dir, "gfs", CYCLE, config)
    assert len(result.copied_files) == len(LEADS)
    assert result.manifest_path.is_file()


# --- Row：单 bundle 约束 ------------------------------------------------------


def test_two_bundle_layout_is_refused_with_zero_writes(tmp_path: Path) -> None:
    config = make_config(gfs=make_source(bundles=(GFS_BUNDLE, GFS_SECOND_BUNDLE)))
    raw_root, work_dir = build_tree(tmp_path)
    base = cycle_dir(raw_root, "gfs")
    for lead in LEADS:
        (base / GFS_SECOND_BUNDLE.format(cycle_hour=0, lead=lead)).write_bytes(
            bundle_bytes(lead)
        )
    verdict = judge(raw_root, "gfs", CYCLE, config)
    assert verdict.complete is True
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "unsupported-layout")
    assert snapshot(work_dir) == {}


# --- Row：源侧 symlink（叶子与祖先段）----------------------------------------


def test_symlinked_bundle_is_refused_although_judge_says_complete(
    tmp_path: Path,
) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    base = cycle_dir(raw_root, "gfs")
    target = base / bundle_name("gfs", 3)
    real = base / "real-f003.grib2"
    target.rename(real)
    target.symlink_to(real)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    # 3.1/3.2 的有意不对称：judge 走 `is_file()`（跟随 symlink）判完整……
    assert verdict.complete is True
    # ……而 staging 拒绝为该链背书。
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "source-symlink")
    assert snapshot(work_dir) == {}


def test_symlinked_cycle_directory_segment_is_refused(tmp_path: Path) -> None:
    raw_root = tmp_path / "nwm-raw"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True)
    real_cycle = raw_root / "gfs" / "real-2026030400"
    real_cycle.mkdir(parents=True)
    for lead in LEADS:
        (real_cycle / bundle_name("gfs", lead)).write_bytes(bundle_bytes(lead))
    (real_cycle / SOURCE_MANIFEST_NAME).write_text(
        json.dumps(source_manifest_payload("gfs")), encoding="utf-8"
    )
    (raw_root / "gfs" / CYCLE_DIR).symlink_to(real_cycle, target_is_directory=True)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    assert verdict.complete is True
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "source-symlink")
    assert snapshot(work_dir) == {}


# --- Row：目标已存在 ---------------------------------------------------------


def test_existing_target_file_is_never_overwritten(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    target = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"pre-existing")
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "target-exists")
    assert target.read_bytes() == b"pre-existing"
    assert not (work_dir / MANIFEST_NAME).exists()
    assert sorted(p.name for p in target.parent.iterdir()) == [target.name]


def test_existing_raw_manifest_is_never_overwritten(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    (work_dir / MANIFEST_NAME).write_text("stale", encoding="utf-8")
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "target-exists")
    assert (work_dir / MANIFEST_NAME).read_text(encoding="utf-8") == "stale"
    assert not (work_dir / "raw").exists()


# --- Row：复制期失败的两条清理路径 -------------------------------------------


def test_source_mutated_during_copy_leaves_no_partial_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 k 个源文件在复制窗口内被替换 -> `source-mutated`，work 侧不留半套。

    注入点选在 `os.lstat`：当第 k 个副本已经落盘（即该文件的复制已开始）时，把源
    文件真实改掉——复制后的那次 `lstat` 于是自然拿到不同的元组。这不绕过被测闸门，
    被测的是「前后元组比对」本身。
    """
    raw_root, work_dir = build_tree(tmp_path)
    victim = cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)
    victim_copy = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)
    real_lstat = os.lstat
    state = {"done": False}

    def hooked_lstat(path, *args, **kwargs):
        if (
            not state["done"]
            and str(path) == str(victim)
            and os.path.exists(victim_copy)
        ):
            state["done"] = True
            # 等长替换：size 不变，只有 mtime_ns 变——这是「只比对内容/大小不算」
            # 的判别器（`_identity` 若退化成只比 size，本用例即无法变红）。
            victim.write_bytes(b"GRIB\xff\x00lead-XXX")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", hooked_lstat)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    expect_kind(excinfo, "source-mutated")
    assert state["done"] is True
    assert snapshot(work_dir) == {}


def test_copy_failure_leaves_no_partial_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 k 个文件的目标不可写（ENOSPC/权限）-> `copy-failed`，work 侧不留半套。"""
    raw_root, work_dir = build_tree(tmp_path)
    doomed = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 6)
    real_open = os.open

    def hooked_open(path, flags, *args, **kwargs):
        if str(path) == str(doomed):
            raise OSError(errno.ENOSPC, "No space left on device", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", hooked_open)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    expect_kind(excinfo, "copy-failed")
    assert snapshot(work_dir) == {}


def test_unwritable_work_directory_reports_copy_failed(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    mode = work_dir.stat().st_mode
    os.chmod(work_dir, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(RawStagingError) as excinfo:
            staged(raw_root, work_dir)
        expect_kind(excinfo, "copy-failed")
    finally:
        os.chmod(work_dir, mode)
    assert snapshot(work_dir) == {}


# --- Row：不变的兄弟面（源树与 YD_ROOT 模拟根）-------------------------------


def test_source_tree_is_byte_and_metadata_identical_after_staging(
    tmp_path: Path,
) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    before = snapshot(raw_root)
    before_content = content_snapshot(raw_root)
    staged(raw_root, work_dir)
    assert snapshot(raw_root) == before
    assert content_snapshot(raw_root) == before_content


def test_yd_root_mock_is_untouched_and_holds_no_raw_copy(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    yd_root = tmp_path / "yd-root"
    (yd_root / "output").mkdir(parents=True)
    (yd_root / "output" / "keep.txt").write_text("published", encoding="utf-8")
    before = snapshot(yd_root)
    staged(raw_root, work_dir)
    assert snapshot(yd_root) == before
    assert list(yd_root.rglob("raw")) == []
    assert list(yd_root.rglob("*.grib2")) == []


# --- kind 词表与异常面 -------------------------------------------------------


def test_no_bare_stdlib_exception_escapes_for_each_failure_shape(
    tmp_path: Path,
) -> None:
    """九项 kind 之外，`stage_raw` MUST NOT 外泄裸 OSError/KeyError/JSONDecodeError。"""
    raw_root, work_dir = build_tree(tmp_path)
    write_source_manifest(raw_root, "gfs", "[]")
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert not isinstance(excinfo.value, OSError | KeyError | json.JSONDecodeError)


@pytest.mark.parametrize(
    ("source", "cycle"),
    [
        ("GFS", CYCLE),
        ("era5", CYCLE),
        ("gfs", datetime(2026, 3, 4, 0)),  # noqa: DTZ001 naive 是被测输入
        # 整点闸门是 (minute, second, microsecond) 三元组：逐分量各一行，一个只动
        # minute 的输入不足以为三分量的闸门背书。
        ("gfs", datetime(2026, 3, 4, 0, 30, tzinfo=UTC)),
        ("gfs", datetime(2026, 3, 4, 0, 0, 30, tzinfo=UTC)),
        ("gfs", datetime(2026, 3, 4, 0, 0, 0, 30, tzinfo=UTC)),
        # tz 闸门是 `utcoffset() != timedelta(0)`：naive（上面那行，utcoffset 为
        # None）与「aware 但非 UTC」是两条不同的腿。
        ("gfs", datetime(2026, 3, 4, 0, tzinfo=timezone(timedelta(hours=8)))),
    ],
)
def test_malformed_call_parameters_raise_config_error(
    tmp_path: Path, source: str, cycle: datetime
) -> None:
    """形参写错归 `ConfigError`（「配置写错了」），不占九项 kind 的名额。"""
    raw_root, work_dir = build_tree(tmp_path)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, work_dir, source, cycle, config)
    assert snapshot(work_dir) == {}


# --- 闸门审计补洞：值传播闸门与承接分支的判别器 ------------------------------


def test_non_numeric_forecast_hours_entry_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    payload["metadata"]["forecast_hours"] = [0, 3, "six"]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_non_integer_typed_forecast_hours_entry_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    payload["metadata"]["forecast_hours"] = [0, 3, None]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")


def test_source_manifest_without_entries_key_fails_closed(tmp_path: Path) -> None:
    """`DownloadManifest.from_dict` 的 `KeyError` MUST 收敛成 `source-manifest`。

    同时是形态闸门 `if not isinstance(entries, list): return` 这条腿的判别器：该腿的
    语义是「结构面不归本闸门，交给下一步的结构闸门」。判别器取在**消息**上——kind
    两边相同（地板兜底也给 `source-manifest`），去掉该腿后 `enumerate(None)` 的
    `TypeError` 会掉进准入地板，消息随之从具名的结构诊断退化成泛化的兜底诊断。
    """
    payload = source_manifest_payload("gfs")
    payload.pop("entries")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert STRUCTURE_MESSAGE in str(excinfo.value)
    assert FLOOR_MESSAGE not in str(excinfo.value)


def test_apcp_without_any_selector_mapping_fails_closed(tmp_path: Path) -> None:
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        if entry["variable"] == "apcp":
            entry["metadata"]["idx_selectors"].pop("apcp", None)
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "accumulation-metadata")
    assert snapshot(work_dir) == {}


def test_variable_absent_from_idx_selectors_omits_the_singular_key(
    tmp_path: Path,
) -> None:
    """非 apcp 变量在复数键里缺席 -> 单数键**缺席**，MUST NOT 写空 Mapping。"""
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        entry["metadata"]["idx_selectors"] = {
            k: v for k, v in entry["metadata"]["idx_selectors"].items() if k != "rh2m"
        }
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    written = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    for entry in written["entries"]:
        if entry["variable"] == "rh2m":
            assert "idx_selector" not in entry["metadata"]
            assert set(entry["metadata"]) == {*CARRIED_KEYS, "idx_selectors"}
        else:
            assert entry["metadata"]["idx_selector"] == selector_for(
                entry["variable"], entry["forecast_hour"]
            )


def _handmade_verdict(raw_root: Path, source: str, leads, variables):
    from yd_producer.rawscan import ScanVerdict

    files = tuple(
        cycle_dir(raw_root, source) / bundle_name(source, lead) for lead in leads
    )
    return ScanVerdict(
        complete=True,
        expected_files=files,
        missing_files=(),
        unreadable_files=(),
        expected_variables={lead: tuple(variables) for lead in leads},
    )


def test_verdict_missing_a_lead_variable_set_is_rejected(tmp_path: Path) -> None:
    raw_root, work_dir = build_tree(tmp_path)
    verdict = _handmade_verdict(raw_root, "gfs", LEADS, GFS_VARIABLES)
    broken = type(verdict)(
        complete=True,
        expected_files=verdict.expected_files,
        missing_files=(),
        unreadable_files=(),
        expected_variables={0: GFS_VARIABLES, 6: GFS_VARIABLES},
    )
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(broken, raw_root, work_dir, "gfs", CYCLE, make_config())
    expect_kind(excinfo, "verdict-mismatch")
    assert snapshot(work_dir) == {}


def test_bundle_pattern_validation_is_reused_from_rawscan(tmp_path: Path) -> None:
    """渲染面复用的判别器：模式非法时 staging 走 `rawscan` 的 `ConfigError`。"""
    raw_root, work_dir = build_tree(tmp_path)
    config = make_config(gfs=make_source(bundles=("no-lead-field.grib2",)))
    verdict = _handmade_verdict(raw_root, "gfs", LEADS, GFS_VARIABLES)
    with pytest.raises(ConfigError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    assert excinfo.value.path == "raw.gfs.bundles"
    assert snapshot(work_dir) == {}


# --- Row：复制期的**任何**异常都清理，且不外泄九项词表之外 --------------------


def test_bare_exception_inside_the_write_block_still_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写入块里抛出的非 `RawStagingError` 也必须触发回滚并收敛成九项之一。

    只接 `RawStagingError` 的清理触发器窄于它要维护的不变量：写入块里任何别的异常
    （裸 `ValueError`、`UnicodeEncodeError`、被替换的原语抛出的任意异常）都会绕过
    `written.rollback()` **并**逃出闭合词表。注入点选在第三个目标的 `os.open`，此时
    前两份副本与两级目录都已落盘，故存活的残留是可见的。
    """
    raw_root, work_dir = build_tree(tmp_path)
    doomed = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 6)
    real_open = os.open
    landed: list[str] = []

    def hooked_open(path, flags, *args, **kwargs):
        if str(path) == str(doomed):
            # 刻意不是 OSError：`_copy_one` 的 except OSError 腿接不到它。
            raise RuntimeError("注入的非 OSError 故障")
        fd = real_open(path, flags, *args, **kwargs)
        landed.append(str(path))
        return fd

    monkeypatch.setattr(os, "open", hooked_open)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    # 注入点确实在「已有副本落盘之后」触发，残留是可构造的。
    assert len([p for p in landed if p.startswith(str(work_dir))]) == 2
    expect_kind(excinfo, "copy-failed")
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert snapshot(work_dir) == {}


def test_keyboard_interrupt_mid_copy_still_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`KeyboardInterrupt` 照样清理，但**不**被改写成 `RawStagingError`。

    「不留半套副本」与异常类型无关，故 `BaseException` 也要触发回滚；而把 Ctrl-C
    改写成一次 staging 失败会让操作者看到一个假的 `copy-failed`，故这一支原样外抛
    ——它是本函数唯一有意保留的、九项词表之外的出口。
    """
    raw_root, work_dir = build_tree(tmp_path)
    doomed = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 6)
    real_open = os.open

    def hooked_open(path, flags, *args, **kwargs):
        if str(path) == str(doomed):
            raise KeyboardInterrupt
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", hooked_open)
    with pytest.raises(KeyboardInterrupt):
        staged(raw_root, work_dir)
    monkeypatch.undo()
    assert snapshot(work_dir) == {}


def test_non_utf8_encodable_carried_value_is_refused_before_any_write(
    tmp_path: Path,
) -> None:
    """承接来的值不可 UTF-8 编码 -> 准入期 `source-manifest`，零写入。

    源 manifest 是外部 JSON：`json.load` 接受转义的孤代理 `\\ud800` 并还原成真正的
    孤代理 str，`json.dumps(ensure_ascii=False)` 也照样吐出它，直到写 UTF-8 流才抛
    `UnicodeEncodeError`。若序列化留在复制之后，结果是「三份副本 + 一个 0 字节
    raw-manifest.json」——半套产物，且下一次重试会被 `lexists` 预检卡死。
    """
    payload = source_manifest_payload("gfs")
    source_entry(payload, LEADS[0], GFS_VARIABLES[0])["metadata"]["grib_short_name"] = (
        "2t\ud800"
    )
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    on_disk = (cycle_dir(raw_root, "gfs") / SOURCE_MANIFEST_NAME).read_text(
        encoding="utf-8"
    )
    # 源文件本身是纯 ASCII（孤代理以 6 字符转义存在），不依赖任何非法落盘字节。
    assert on_disk.isascii() and "\\ud800" in on_disk
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert not isinstance(excinfo.value, ValueError)
    assert snapshot(work_dir) == {}


def test_mkdir_failing_midway_leaves_no_directories_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`mkdir(parents=True)` 建到一半失败 -> 已建的祖先段也必须被回滚掉。

    注入方式是**数成功的创建次数**：`Path.mkdir(parents=True)` 先试叶子、拿
    `FileNotFoundError` 再回溯建父目录，用「第 n 次调用即失败」会打在还没创建任何
    目录的探测腿上，抓不到本用例要抓的中途失败。
    """
    raw_root, work_dir = build_tree(tmp_path)
    real_mkdir = os.mkdir
    created: list[str] = []

    def hooked_mkdir(path, *args, **kwargs):
        if len(created) >= 2:
            raise OSError(errno.EDQUOT, "Disk quota exceeded", str(path))
        real_mkdir(path, *args, **kwargs)
        created.append(str(path))

    monkeypatch.setattr(os, "mkdir", hooked_mkdir)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    # 前提取证：确实先成功建了两级目录，才轮到第三级失败。
    assert created == [
        str(work_dir / "raw"),
        str(work_dir / "raw" / "gfs"),
    ]
    expect_kind(excinfo, "copy-failed")
    assert snapshot(work_dir) == {}


# --- Row：raw_root 与 work_dir 必须不相互包含 --------------------------------


def test_work_dir_under_raw_root_is_a_config_error(tmp_path: Path) -> None:
    """work 落在 raw 树内 -> `ConfigError`，raw 树逐字节不变。

    否则副本、目录与失败回滚的 unlink/rmdir 全都发生在 NWM raw 树里
    （`docs/compute-loop-design.md` §4.1 的硬约束）。归 `ConfigError`（「调用写错了」）
    而不是第十项 kind：九项词表由 fixture 钉死。
    """
    raw_root, _work_dir = build_tree(tmp_path)
    before = snapshot(raw_root)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, raw_root / "yd-work", "gfs", CYCLE, config)
    assert snapshot(raw_root) == before


def test_raw_root_under_work_dir_is_a_config_error(tmp_path: Path) -> None:
    """反向包含（raw 在 work 之下）同样拒绝——两个析取分支各自可判。"""
    raw_root, _work_dir = build_tree(tmp_path)
    before = snapshot(raw_root)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, tmp_path, "gfs", CYCLE, config)
    assert snapshot(raw_root) == before


def test_disjoint_sibling_roots_still_stage_normally(tmp_path: Path) -> None:
    """不相交的兄弟目录 MUST NOT 被上面那道闸门误拒（前缀相同也不算包含）。"""
    raw_root, _work_dir = build_tree(tmp_path)
    sibling = tmp_path / "nwm-raw-work"  # 与 raw_root 同前缀但不在其下
    sibling.mkdir()
    result = staged(raw_root, sibling)
    assert len(result.copied_files) == len(LEADS)


# --- Row：verdict 多出一个 lead（集合相等的另一个方向）----------------------


def test_verdict_with_an_extra_lead_is_rejected(tmp_path: Path) -> None:
    """`expected_variables` 多出一个 lead 也是不同源。

    少一个 lead 的方向由 `test_verdict_missing_a_lead_variable_set_is_rejected` 钉住；
    本用例钉另一个方向：多出的 lead 会让产出 manifest 的
    `forecast_hours == requested_forecast_hours == sorted(expected_variables)` 三键
    相等（tasks.md:677/:708）静默失真——`forecast_hours` 由重构的 lead 推导，比
    verdict 声明的少。
    """
    raw_root, work_dir = build_tree(tmp_path)
    verdict = _handmade_verdict(raw_root, "gfs", LEADS, GFS_VARIABLES)
    broken = type(verdict)(
        complete=True,
        expected_files=verdict.expected_files,
        missing_files=(),
        unreadable_files=(),
        expected_variables={
            **{lead: GFS_VARIABLES for lead in LEADS},
            99: GFS_VARIABLES,
        },
    )
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(broken, raw_root, work_dir, "gfs", CYCLE, make_config())
    expect_kind(excinfo, "verdict-mismatch")
    assert snapshot(work_dir) == {}


@pytest.mark.parametrize(
    "bad_value",
    [
        None,  # 不可迭代：留着会漏一个裸 TypeError
        5,  # 同上
        "tmp2m",  # 可迭代但更糟：逐字符扇出，静默产出变量名全错的 manifest
    ],
)
def test_verdict_lead_with_a_malformed_variable_set_is_rejected(
    tmp_path: Path, bad_value: Any
) -> None:
    """键集相等但值的形态不对：集合相等判不了值，仍需逐个判值面形态。"""
    raw_root, work_dir = build_tree(tmp_path)
    verdict = _handmade_verdict(raw_root, "gfs", LEADS, GFS_VARIABLES)
    broken = type(verdict)(
        complete=True,
        expected_files=verdict.expected_files,
        missing_files=(),
        unreadable_files=(),
        expected_variables={0: GFS_VARIABLES, 3: bad_value, 6: GFS_VARIABLES},
    )
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(broken, raw_root, work_dir, "gfs", CYCLE, make_config())
    expect_kind(excinfo, "verdict-mismatch")
    assert snapshot(work_dir) == {}


# --- Row：`_identity` 四元组的逐分量判别器 -----------------------------------


def _mutate_source_during_copy(
    monkeypatch: pytest.MonkeyPatch, victim: Path, victim_copy: Path, mutate
) -> dict[str, bool]:
    """在第 k 份副本已落盘、复制后那次 `lstat` 之前改动源文件。

    注入点选在 `os.lstat`：被测的是「复制前后元组比对」本身，不绕过任何闸门。
    """
    real_lstat = os.lstat
    state = {"done": False}

    def hooked_lstat(path, *args, **kwargs):
        if (
            not state["done"]
            and str(path) == str(victim)
            and os.path.exists(victim_copy)
        ):
            state["done"] = True
            mutate(real_lstat(victim))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", hooked_lstat)
    return state


def _expect_source_mutated(raw_root: Path, work_dir: Path, state: dict[str, bool]):
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    assert state["done"] is True
    expect_kind(excinfo, "source-mutated")
    assert snapshot(work_dir) == {}


def test_source_replaced_by_an_equal_stat_file_is_caught_by_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整体替换：size/mtime_ns/mode 全部还原，只有 `st_ino` 变——`ino` 分量的判别器。

    这正是 `_identity` docstring 自称能抓的「同内容不同 inode 的整体替换」；没有本
    用例时把 `st_ino` 从元组里删掉，整套仍然全绿。
    """
    raw_root, work_dir = build_tree(tmp_path)
    victim = cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)
    victim_copy = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)
    replacement = tmp_path / "replacement.grib2"

    def swap(before: os.stat_result) -> None:
        replacement.write_bytes(victim.read_bytes())  # 等长、等内容
        os.replace(replacement, victim)  # 换 inode
        os.chmod(victim, stat.S_IMODE(before.st_mode))
        os.utime(victim, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(victim)
        # 前提取证：确实只有 ino 变了，别的分量都还原了。
        assert after.st_ino != before.st_ino
        assert (after.st_size, after.st_mtime_ns, after.st_mode) == (
            before.st_size,
            before.st_mtime_ns,
            before.st_mode,
        )

    state = _mutate_source_during_copy(monkeypatch, victim, victim_copy, swap)
    _expect_source_mutated(raw_root, work_dir, state)


def test_source_chmodded_during_copy_is_caught_by_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只改权限位：size/mtime_ns/ino 全不变——`mode` 分量的判别器。"""
    raw_root, work_dir = build_tree(tmp_path)
    victim = cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)
    victim_copy = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)

    def chmod(before: os.stat_result) -> None:
        os.chmod(victim, stat.S_IMODE(before.st_mode) ^ stat.S_IWUSR)
        after = os.stat(victim)
        assert after.st_mode != before.st_mode
        assert (after.st_size, after.st_mtime_ns, after.st_ino) == (
            before.st_size,
            before.st_mtime_ns,
            before.st_ino,
        )

    state = _mutate_source_during_copy(monkeypatch, victim, victim_copy, chmod)
    _expect_source_mutated(raw_root, work_dir, state)


def test_source_appended_during_copy_is_caught_by_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只改长度：mtime_ns 还原、ino/mode 不变——`size` 分量的判别器。"""
    raw_root, work_dir = build_tree(tmp_path)
    victim = cycle_dir(raw_root, "gfs") / bundle_name("gfs", 3)
    victim_copy = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 3)

    def append(before: os.stat_result) -> None:
        with open(victim, "ab") as handle:
            handle.write(b"\x00")
        os.utime(victim, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(victim)
        assert after.st_size != before.st_size
        assert (after.st_mtime_ns, after.st_ino, after.st_mode) == (
            before.st_mtime_ns,
            before.st_ino,
            before.st_mode,
        )

    state = _mutate_source_during_copy(monkeypatch, victim, victim_copy, append)
    _expect_source_mutated(raw_root, work_dir, state)


# --- Row：forecast_hours 逐项类型闸门的逐分量判别器 --------------------------


def test_bool_forecast_hours_entry_fails_closed(tmp_path: Path) -> None:
    """`True` 是 `int` 的子类：`isinstance(value, bool)` 这条析取分支的判别器。

    取值必须是**超集** `[0, 3, 6, True]`：`True` 会静默变成 `1`，若写成
    `[0, 3, True]` 则覆盖检查会先因缺 lead 6 而拒绝，本闸门照样无判别力。
    """
    payload = source_manifest_payload("gfs")
    payload["metadata"]["forecast_hours"] = [0, 3, 6, True]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_numeric_string_forecast_hours_are_accepted(tmp_path: Path) -> None:
    """`int | str` 联合的 `str` 分量：数字字符串是**合法**取值，MUST NOT 被拒。"""
    payload = source_manifest_payload("gfs")
    payload["metadata"]["forecast_hours"] = [0, 3, "6"]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    result = staged(raw_root, work_dir)
    assert len(result.copied_files) == len(LEADS)


# --- Row：源 manifest 结构异常的逐分支收敛（多类型 except 元组的逐分量判别器）--


def test_non_utf8_source_manifest_bytes_fail_closed(tmp_path: Path) -> None:
    """源 manifest 不是合法 UTF-8 -> `UnicodeDecodeError` 收敛成 `source-manifest`。

    字节要挑**任何编码下都非法**的：以 `\xff\xfe` 开头会被 `json.detect_encoding`
    当成 UTF-16 BOM 解码成功，于是走的是 `JSONDecodeError` 腿而不是本用例要钉的
    解码腿（实测：那种输入下「只留 JSONDecodeError」的变异体存活）。
    """
    raw_root, work_dir = build_tree(tmp_path)
    (cycle_dir(raw_root, "gfs") / SOURCE_MANIFEST_NAME).write_bytes(
        b'{"source_id": "\xff\xfe\xfdgfs"}'
    )
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


@pytest.mark.parametrize(
    ("key", "value", "leaked"),
    [
        # `DownloadManifest.from_dict` 对同一份坏 manifest 会抛四种不同的裸异常，
        # 每种是 except 元组里的一个**独立分量**：少写一个就有一种直接外泄。
        ("cycle_time", "nope", ValueError),  # datetime.fromisoformat
        ("entries", 5, TypeError),  # 不可迭代
        ("cycle_time", 5, AttributeError),  # int 没有 .strip
    ],
)
def test_structurally_broken_source_manifest_never_leaks_a_bare_exception(
    tmp_path: Path, key: str, value: Any, leaked: type[Exception]
) -> None:
    payload = source_manifest_payload("gfs")
    payload[key] = value
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    # 前提取证：这份输入确实会让底层原语抛出被点名的那个裸异常。
    with pytest.raises(leaked):
        DownloadManifest.from_dict(dict(payload))
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert not isinstance(excinfo.value, leaked)
    assert snapshot(work_dir) == {}


# --- Row：回滚自身失败（`rollback` 保证不抛 + 失败带进外抛异常）---------------


def _copy_failure_with_broken_rollback(
    monkeypatch: pytest.MonkeyPatch, work_dir: Path, failure: BaseException
):
    """让第三份副本的创建失败，并让**回滚原语**抛一个非 `OSError`。

    两个注入合在一起才是本类的判别器：只让复制失败，回滚会成功、什么也测不到；
    只让回滚失败，没有触发回滚的失败路径。
    """
    doomed = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 6)
    real_open = os.open

    def hooked_open(path, flags, *args, **kwargs):
        if str(path) == str(doomed):
            raise failure
        return real_open(path, flags, *args, **kwargs)

    def hooked_unlink(path, *args, **kwargs):
        # 刻意不是 OSError：`rollback` 原先只吞 `OSError`，这条会**替换**正在外抛的
        # `RawStagingError` 并逃出九项闭合词表（round-2 verifier 在 NUL 路径上实测过
        # 同一机制，`os.rmdir` 抛裸 `ValueError`）。
        raise ValueError(f"注入的非 OSError 清理故障：{path}")

    monkeypatch.setattr(os, "open", hooked_open)
    monkeypatch.setattr(os, "unlink", hooked_unlink)


def test_rollback_failure_is_reported_and_never_replaces_the_staging_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回滚原语抛非 `OSError` 时：外抛的仍是 `RawStagingError`，且残留有信号。

    `rollback` 在三个 handler 里都跑在「已有异常正在外抛」的上下文里，它自己抛出的
    异常会**替换**那个异常——于是一个纯入参就能让裸 `ValueError` 逃出 `stage_raw`。
    收口点只能在 `rollback` 内部（handler 加 `except` 拦不住它自己）。配套的另一半
    是**不静默**：清理失败必须进入外抛的异常，否则「不留任何部分产物」这条无条件
    不变量失守时无任何信号，而残留会让下一次重试被 `target-exists` 楔死。
    """
    raw_root, work_dir = build_tree(tmp_path)
    _copy_failure_with_broken_rollback(
        monkeypatch,
        work_dir,
        OSError(errno.ENOSPC, "No space left on device"),
    )
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    expect_kind(excinfo, "copy-failed")
    assert not isinstance(excinfo.value, ValueError)
    notes = "".join(getattr(excinfo.value, "__notes__", []))
    assert "清理" in notes and "残留" in notes
    # 信号必须与事实一致：这两份副本确实还在。
    survivors = sorted(p.name for p in (work_dir / "raw" / "gfs" / CYCLE_DIR).iterdir())
    assert survivors == [bundle_name("gfs", lead) for lead in (0, 3)]
    for name in survivors:
        assert name in notes


def test_tier2_message_stops_claiming_cleanup_when_rollback_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非 `RawStagingError` 腿的消息 MUST NOT 在清理失败时仍宣称「已清理」。

    这是本轮唯一被实测出会说假话的路径（tier-1 的消息不含清理声明）：残留 2 份副本
    的同时，异常消息逐字写着「已清理本轮 work 侧写入」。
    """
    raw_root, work_dir = build_tree(tmp_path)
    _copy_failure_with_broken_rollback(
        monkeypatch, work_dir, RuntimeError("注入的非 OSError 故障")
    )
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    expect_kind(excinfo, "copy-failed")
    message = str(excinfo.value)
    assert "已清理本轮 work 侧写入" not in message
    assert "残留" in message
    assert snapshot(work_dir) != {}


def test_successful_rollback_still_reports_a_clean_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向判别器：回滚成功时消息仍宣称「已清理」，且不挂任何残留 note。

    没有这条，「一律不说已清理」的实现也能让上面那条变绿。
    """
    raw_root, work_dir = build_tree(tmp_path)
    doomed = work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", 6)
    real_open = os.open

    def hooked_open(path, flags, *args, **kwargs):
        if str(path) == str(doomed):
            raise RuntimeError("注入的非 OSError 故障")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", hooked_open)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    assert "已清理本轮 work 侧写入" in str(excinfo.value)
    assert getattr(excinfo.value, "__notes__", []) == []
    assert snapshot(work_dir) == {}


def test_null_byte_work_dir_is_refused_without_leaking_a_bare_value_error(
    tmp_path: Path,
) -> None:
    """NUL 字节的 `work_dir`：纯入参、无注入，原先让裸 `ValueError` 逃出 `stage_raw`。

    链条是 `Path.exists()`/`os.path.lexists` 都自吞 `ValueError` 返 `False`，于是整条
    NUL 祖先链被登记进账本，`mkdir` 抛裸 `ValueError` -> tier-2 -> `rollback` 的
    `os.rmdir` 再抛裸 `ValueError` 把它顶掉。现在在归一闸门上以 `ConfigError`（形参
    写错）短路，零写入。
    """
    raw_root, work_dir = build_tree(tmp_path)
    before = snapshot(work_dir)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError) as excinfo:
        stage_raw(verdict, raw_root, f"{work_dir}/w\x00x", "gfs", CYCLE, config)
    assert not isinstance(excinfo.value, ValueError)
    assert snapshot(work_dir) == before


# --- Row：外部 JSON 的值形态（不可哈希值 MUST NOT 漏裸 TypeError）------------


@pytest.mark.parametrize("bad_value", [["interval_bucket"], {"a": 1}])
def test_unhashable_accumulation_type_fails_closed(
    tmp_path: Path, bad_value: Any
) -> None:
    """`"accumulation_type": ["interval_bucket"]` 是合法 JSON，反序列化成不可哈希值。

    闸门写成 `x not in frozenset(...)` 时求哈希抛裸 `TypeError`，而 `_build_entries`
    整段在 `stage_raw` 的 try 块**之前**，三层 handler 一条也接不到。§3.1 对该字段
    无任何类型约束，「pin 不会写 list」是对生成器的观察、不是对落盘 JSON 的保证。
    """
    payload = source_manifest_payload("gfs")
    for entry in payload["entries"]:
        if entry["variable"] == "apcp":
            entry["metadata"]["idx_selectors"]["apcp"]["accumulation_type"] = bad_value
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    # 前提取证：这份取值确实不可哈希（求哈希即裸 TypeError）。
    with pytest.raises(TypeError):
        hash(bad_value)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "accumulation-metadata")
    assert not isinstance(excinfo.value, TypeError)
    assert snapshot(work_dir) == {}


def test_unhashable_entry_variable_fails_closed(tmp_path: Path) -> None:
    """同类的另一个出口：`ManifestEntry.from_dict` 不强制 `variable` 的类型，

    一个 `"variable": ["apcp"]` 会在按 (lead, variable) 建索引时让 `dict` 求哈希抛裸
    `TypeError`——同样在 try 块之前。
    """
    payload = source_manifest_payload("gfs")
    source_entry(payload, LEADS[0], GFS_VARIABLES[0])["variable"] = ["apcp"]
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    # 前提取证：坏值确实穿过了 `from_dict`（它只强制 forecast_hour/metadata）。
    assert ["apcp"] in [
        entry.variable for entry in DownloadManifest.from_dict(dict(payload)).entries
    ]
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert not isinstance(excinfo.value, TypeError)
    assert snapshot(work_dir) == {}


# --- Row：containment 的三种别名与一条合法路径 -------------------------------


def _case_insensitive(tmp_path: Path) -> bool:
    probe = tmp_path / "case-probe"
    probe.mkdir()
    return (tmp_path / "CASE-PROBE").is_dir()


def test_case_aliased_work_dir_inside_raw_root_is_refused(tmp_path: Path) -> None:
    """大小写别名：`<b>/NWM-RAW/work` 与 `<b>/nwm-raw` 词法不相交、物理同一棵树。

    `resolve()` 关不掉这条腿——CPython 的 posix 实现折叠 symlink 与 `..`，但**保留
    调用方给的非链组件大小写**。判据必须落到 inode 身份上。
    """
    raw_root, _work_dir = build_tree(tmp_path)
    if not _case_insensitive(tmp_path):
        pytest.skip("大小写敏感的卷上不存在该别名")
    alias_work = tmp_path / "NWM-RAW" / "work"
    # 前提取证：别名确实指向 raw_root 那个 inode，且两条路径词法不相交。
    assert os.path.samestat(os.stat(tmp_path / "NWM-RAW"), os.stat(raw_root))
    assert not alias_work.resolve().is_relative_to(raw_root.resolve())
    before = snapshot(raw_root)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, alias_work, "gfs", CYCLE, config)
    assert snapshot(raw_root) == before


def test_symlinked_work_dir_pointing_into_raw_root_is_refused(tmp_path: Path) -> None:
    """`work_dir` **自身**是一条指进 raw 树的链（#71 的目标侧逐段检查按设计跳过根，
    故那条工具在这里无效）。
    """
    raw_root, _work_dir = build_tree(tmp_path)
    real = raw_root / "work-real"
    real.mkdir()
    link = tmp_path / "worklink"
    link.symlink_to(real, target_is_directory=True)
    before = snapshot(raw_root)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, link, "gfs", CYCLE, config)
    assert snapshot(raw_root) == before


def test_dotdot_aliased_work_dir_inside_raw_root_is_refused(tmp_path: Path) -> None:
    """`..` 段：`<b>/side/../nwm-raw/work` 词法上不以 `<b>/nwm-raw` 为前缀。"""
    raw_root, _work_dir = build_tree(tmp_path)
    (tmp_path / "side").mkdir()
    alias_work = tmp_path / "side" / ".." / "nwm-raw" / "work"
    before = snapshot(raw_root)
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    with pytest.raises(ConfigError):
        stage_raw(verdict, raw_root, alias_work, "gfs", CYCLE, config)
    assert snapshot(raw_root) == before


def test_work_dir_reached_through_dotdot_outside_raw_root_stages_normally(
    tmp_path: Path,
) -> None:
    """合法路径 MUST NOT 被误拒：`<raw_root>/../work` 解析后是 raw 树的**兄弟**。

    这是该闸门唯一的无误拒判别器——既有三条用例（`raw_root/yd-work`、`tmp_path`、
    同前缀兄弟 `nwm-raw-work`）全部词法可判，在纯词法闸门下也照样绿。
    """
    raw_root, work_dir = build_tree(tmp_path)
    through_dotdot = raw_root / ".." / "work"
    # 前提取证：这条路径词法上以 raw_root 为前缀，解析后却在 raw 树之外。
    assert Path(through_dotdot).is_relative_to(raw_root)
    assert not through_dotdot.resolve().is_relative_to(raw_root.resolve())
    result = staged(raw_root, through_dotdot)
    assert len(result.copied_files) == len(LEADS)
    assert (work_dir / MANIFEST_NAME).is_file()


# --- Row：lead_hours 的排序 / verdict 变量集的 list 分量 ----------------------


def test_unsorted_lead_hours_stage_normally(tmp_path: Path) -> None:
    """`lead_hours=(6, 0, 3)` 是合法配置（装载器不排序也不要求有序）。

    `_reconstruct_sources` 的 `sorted(lead_hours)` 是活闸门而不是展示用排序：去掉它
    时该合法调用会被 `verdict-mismatch` 误拒（`judge` 侧的 `_expected_leads` 排序）。
    """
    config = make_config(gfs=make_source(lead_hours=(6, 0, 3)))
    raw_root, work_dir = build_tree(tmp_path)
    result = staged(raw_root, work_dir, "gfs", config)
    assert result.copied_files == tuple(
        work_dir / "raw" / "gfs" / CYCLE_DIR / bundle_name("gfs", lead)
        for lead in LEADS
    )
    written = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert written["metadata"]["forecast_hours"] == [0, 3, 6]


def test_verdict_lead_variable_set_as_a_list_stages_normally(tmp_path: Path) -> None:
    """值形态闸门的 `list` 分量：`isinstance(variables, tuple | list)` 的右半。

    该分量声明「`list` 值集是合法输入」，但既有三个坏输入（`None`/`5`/`"tmp2m"`）对
    两个分量咬合相同，把闸门收窄成只认 `tuple` 的变异体照样全绿。
    """
    raw_root, work_dir = build_tree(tmp_path)
    verdict = _handmade_verdict(raw_root, "gfs", LEADS, GFS_VARIABLES)
    as_list = type(verdict)(
        complete=True,
        expected_files=verdict.expected_files,
        missing_files=(),
        unreadable_files=(),
        expected_variables={lead: list(GFS_VARIABLES) for lead in LEADS},
    )
    result = stage_raw(as_list, raw_root, work_dir, "gfs", CYCLE, make_config())
    assert {(e.forecast_hour, e.variable) for e in result.entries} == {
        (lead, var) for lead in LEADS for var in GFS_VARIABLES
    }


# --- Row：symlink 拒绝**先于**读源 manifest（顺序的判别器）-------------------


def test_symlinked_cycle_directory_is_refused_before_the_manifest_is_read(
    tmp_path: Path,
) -> None:
    """链目标里放一份畸形 `manifest.json`：两种顺序按 `kind` 分开。

    先读 manifest 的实现会以 `source-manifest` 失败（并且已经穿过那条 spec 说
    「不跟随」的链）；正确顺序以 `source-symlink` 失败。既有的链 cycle 目录用例背后
    是一份**合法** manifest，两种顺序同样报 `source-symlink`，判别不了顺序。
    """
    raw_root = tmp_path / "nwm-raw"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True)
    real_cycle = raw_root / "gfs" / "real-2026030400"
    real_cycle.mkdir(parents=True)
    for lead in LEADS:
        (real_cycle / bundle_name("gfs", lead)).write_bytes(bundle_bytes(lead))
    (real_cycle / SOURCE_MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    (raw_root / "gfs" / CYCLE_DIR).symlink_to(real_cycle, target_is_directory=True)
    # 前提取证：链后面那份 manifest 确实不可解析（先读就必然是 source-manifest）。
    with pytest.raises(json.JSONDecodeError):
        json.loads((real_cycle / SOURCE_MANIFEST_NAME).read_text(encoding="utf-8"))
    config = make_config()
    verdict = judge(raw_root, "gfs", CYCLE, config)
    assert verdict.complete is True
    with pytest.raises(RawStagingError) as excinfo:
        stage_raw(verdict, raw_root, work_dir, "gfs", CYCLE, config)
    expect_kind(excinfo, "source-symlink")
    assert snapshot(work_dir) == {}


# --- Row：manifest 级四键由 yd 自算（first/last 两端都发散）------------------


def test_manifest_level_hour_keys_are_self_computed_not_copied(tmp_path: Path) -> None:
    """源侧小时表在**两端**都比本轮 lead 宽：first 与 last 各自可判。

    默认 fixture 只让 last 发散（源侧 `[0,3,6,9,12]` vs 本轮 `[0,3,6]`），照抄源侧
    `first_forecast_hour` 的实现在那份输入上取值恰好重合。这里把本轮 lead 收成
    `(3, 6)`、源侧仍从 0 起，四键全部发散。
    """
    leads = (3, 6)
    config = make_config(gfs=make_source(lead_hours=leads))
    payload = source_manifest_payload("gfs", leads=leads, declared_hours=(0, 3, 6, 9))
    raw_root, work_dir = build_tree(tmp_path, leads=leads, manifest=payload)
    result = staged(raw_root, work_dir, "gfs", config)
    written = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert written["metadata"] == {
        "first_forecast_hour": 3,
        "last_forecast_hour": 6,
        "requested_forecast_hours": [3, 6],
        "forecast_hours": [3, 6],
    }
    # 前提取证：源侧四键与上面每一项都不同（否则本用例判别不了「照抄」）。
    assert payload["metadata"] == {
        "first_forecast_hour": 0,
        "last_forecast_hour": 9,
        "forecast_hours": [0, 3, 6, 9],
        "requested_forecast_hours": [0, 3, 6, 9],
    }


# --- Row：准入期收口（floor）的结构探针 --------------------------------------
#
# 本节不是「给 `OverflowError` 和 `RecursionError` 各补一个用例」。三轮修复都按
# **异常类型**枚举消费点，每轮都修掉被点名的实例、每轮又留下同类新实例（round 1 的
# `UnicodeEncodeError`、round 2 的裸 `ValueError`/`TypeError`、round 3 的
# `OverflowError`/`RecursionError`）。判据改取**位置**：准入段整体在一个收口块内，
# 于是「非词表异常从准入期逃逸」这件事在结构上不可能，而不是恰好不存在。
#
# 两条断言分工：
# - `test_admission_phase_is_structurally_enclosed_by_one_floor`：AST 机检
#   `stage_raw` 的**第一条语句**就是收口 `try`、其后紧跟写入期起点，故不存在任何一条
#   落在收口之外的准入语句。新增的准入语句只能落在块内——这是闭合的来源。
# - `test_admission_call_boundary_contains_injected_non_vocabulary_exception`：对
#   AST **枚举出来**的每一个准入期调用点注入一个非词表异常，断言收口生效且零写入。
#   参数集由源码派生，不是手写清单：将来在准入段新写一个调用点会**自动**入表。


class _ProbeEscape(Exception):
    """注入用的异常：直接继承 `Exception`，**不是**被测模块任何一条 `except` 元组
    成员（`OSError`/`ValueError`/`TypeError`/`KeyError`/`AttributeError`/
    `JSONDecodeError`/`UnicodeDecodeError`）的子类。

    这一点是判别力的全部来源：若注入 `ValueError`，`_load_source_manifest` 自己的
    handler 就会吃掉它，探针在**删掉收口块**的情况下照样通过，从而不是判别器。
    """


def _stage_raw_body() -> list[ast.stmt]:
    tree = ast.parse(inspect.getsource(rawcopy_module))
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "stage_raw"
    )
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body = body[1:]  # docstring
    return body


def _admission_try() -> ast.Try | None:
    """`stage_raw` 的收口块。**不在这里断言**：收口不存在时若模块级代码抛异常，整个
    文件会在 collect 期就报错，其余用例连红都变不出来（红证会被淹掉）。结构本身由
    `test_admission_phase_is_structurally_enclosed_by_one_floor` 断言。
    """
    node = _stage_raw_body()[0]
    return node if isinstance(node, ast.Try) else None


def _dotted(func: ast.expr) -> str | None:
    """`os.path.lexists` -> `"os.path.lexists"`；`work_real.is_relative_to` -> 同形。

    根不是 Name（例如 `"、".join(...)`）时返回该调用目标的源码文本。
    """
    parts: list[str] = []
    node: ast.expr = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ast.unparse(func)


def _admission_call_targets() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """AST 遍历（**不是 grep**）准入段的全部调用点，按可注入性分两桶。

    可注入 = 点号链的根是被测模块的**模块级名字**（含被模块全局遮蔽的 builtin），
    因此可以在模块命名空间上替换。另一桶是对**局部对象**取方法（`work_real
    .is_relative_to(...)`、`cycle.astimezone(...)`、`"、".join(...)`）：它们没有模块级
    的注入点，但同样**词法落在收口块内**，由上一条结构断言覆盖。
    """
    patchable: set[str] = set()
    local_methods: set[str] = set()
    node = _admission_try()
    for stmt in node.body if node is not None else []:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            root = name.split(".")[0]
            if root.isidentifier() and (
                root in vars(rawcopy_module) or hasattr(builtins, root)
            ):
                patchable.add(name)
            else:
                local_methods.add(name)
    return tuple(sorted(patchable)), tuple(sorted(local_methods))


ADMISSION_INJECTION_TARGETS, ADMISSION_LOCAL_METHOD_CALLS = _admission_call_targets()

# 注入后**不会被调用**的准入期调用点，逐条附理由。它不是豁免清单：探针会断言实际
# 未命中的集合与本表**恰好相等**，于是将来某个点位从「happy path 会走到」变成
# 「走不到」（或反之）都会打红，必须显式改这里而不能悄悄漏掉。
ADMISSION_UNREACHED_ON_HAPPY_PATH = {
    # 只在失败分支被构造。且它同时是收口器**自己**用来落地的类型，把它换成注入器
    # 等于连收口器一起换掉——那不是对准入段的有效注入，而是把收口器本身拆了。
    "RawStagingError": "只在失败分支构造；且收口器自身依赖它",
    "ConfigError": "只在 containment 失败分支构造",
}

# 被**非调用**方式消费的准入期名字：替换它们不会走到 `raiser.__call__`，但同样把一个
# 非词表异常送进准入段。逐条附机制，并在探针里单独走一条断言分支——不这样分，
# 「注入确实生效了」这条前提就会在这些点位上悄悄失效。
ADMISSION_NONCALL_CONSUMPTION = {
    "str": "同时被 `isinstance(variable, str)` 当作类型实参；替换后由 isinstance 抛 "
    "`TypeError`，仍必须被收口",
}


class _AttrShim:
    """把 `mod.attr` 换成别的对象，其余属性照转的薄壳（用于 `os.path.lexists`
    这类多级点号链：不去动真正的 `os`，只在被测模块的命名空间里换一层）。"""

    def __init__(self, target: Any, attr: str, value: Any) -> None:
        self.__dict__["_target"] = target
        self.__dict__["_attr"] = attr
        self.__dict__["_value"] = value

    def __getattr__(self, name: str) -> Any:
        if name == self.__dict__["_attr"]:
            return self.__dict__["_value"]
        return getattr(self.__dict__["_target"], name)


def _install(monkeypatch: pytest.MonkeyPatch, dotted: str, replacement: Any) -> None:
    root, *rest = dotted.split(".")
    if not rest:
        monkeypatch.setattr(rawcopy_module, root, replacement, raising=False)
        return
    current = getattr(rawcopy_module, root)
    chain = [current]
    for part in rest[:-1]:
        current = getattr(current, part)
        chain.append(current)
    value: Any = replacement
    for part, holder in zip(reversed(rest), reversed(chain), strict=True):
        value = _AttrShim(holder, part, value)
    monkeypatch.setattr(rawcopy_module, root, value, raising=False)


def test_admission_phase_is_structurally_enclosed_by_one_floor() -> None:
    """`stage_raw` 的**整个**准入段落在唯一一个收口 `try` 内，无第二块、无块外语句。

    判据取在**范围**上而不是槽位上。只钉 `body[0]` 是 `try`、`body[1]` 是写入期起点
    是不够的：一条准入语句被移到 `body[2]`（仍在任何写入之前）就同时逃出地板**和**
    AST 派生的探针参数集（实测 21 -> 20），违反 MUST 而全套件全绿。故这里钉两件事：
    函数体顶层的**完整形状**（多出任何一条语句即红），以及地板 `try` 体的**首尾**
    语句就是 fixture 具名的准入段两端点（任一端被移出即红）。

    MUST NOT 改成钉 `len(ADMISSION_INJECTION_TARGETS)`：计数会在每次**合法**新增调用
    点时变红，且它对「语句被移出地板」与「语句被删掉」不可区分——那正是本仓复盘所
    批判的枚举式判别器。

    **两条如实登记的残留**（本断言覆盖不到，不粉饰）：
    - 准入语句被移进**写入段**的 `try`：那里有另一套 handler，是另一个问题；
    - 准入工作被抽成函数、由写入段调用：任何 AST 用例都测不出来。
    """
    body = _stage_raw_body()
    # 顶层形状：地板 try / 写入期起点 / 写入段 try / return，恰好四条。
    assert [type(stmt).__name__ for stmt in body] == [
        "Try",
        "Assign",
        "Try",
        "Return",
    ], [type(stmt).__name__ for stmt in body]
    node = body[0]
    assert isinstance(node, ast.Try), (
        "函数体第一条语句必须是收口 try（否则块前有裸语句）"
    )
    assert ADMISSION_INJECTION_TARGETS, "探针参数集为空：枚举没取到准入段"
    following = body[1]
    assert isinstance(following, ast.Assign), "收口块之后必须紧接写入期起点"
    assert [t.id for t in following.targets if isinstance(t, ast.Name)] == ["written"]
    # 地板 try 体的首尾 = 准入段的两个端点（fixture 逐字：「形参守卫直到
    # `target-exists` 预检」）。首端是 `verdict.complete` 检查，尾端是
    # `os.path.lexists` 的 target-exists 预检。
    first = ast.unparse(node.body[0])
    last = ast.unparse(node.body[-1])
    assert "verdict.complete" in first, first
    assert "os.path.lexists" in last and "target-exists" in last, last

    kinds = [ast.unparse(handler.type) for handler in node.handlers]
    assert kinds == ["(ConfigError, RawStagingError)", "Exception"], kinds
    # 第一层只做原样外抛（保 kind/`__cause__`/`is` 身份）。
    assert [type(s).__name__ for s in node.handlers[0].body] == ["Raise"]
    # 第二层把非词表异常收敛成 `RawStagingError`，且**不吞 BaseException**：
    # `KeyboardInterrupt`/`SystemExit` MUST 照常传播。
    assert "BaseException" not in kinds
    source = ast.unparse(node.handlers[1])
    assert "RawStagingError" in source and "ADMISSION_FALLBACK_KIND" in source
    assert "from exc" in source  # `__cause__` 保留
    # 收口器自身不得抛：kind 取自闭合词表，消息拼装走不抛的 `_safe_repr`。
    assert rawcopy_module.ADMISSION_FALLBACK_KIND in rawcopy_module.ERROR_KINDS
    assert "_safe_repr" in source and "!r}" not in source


def test_admission_local_method_calls_are_inside_the_floor() -> None:
    """无模块级注入点的那一桶：登记其存在，并声明它们由词法包含覆盖。"""
    assert ADMISSION_LOCAL_METHOD_CALLS  # 该桶非空，别把它当成「不存在」
    for name in ADMISSION_LOCAL_METHOD_CALLS:
        # 全部是对局部对象/字面量取方法；没有一个是模块级名字或 builtin
        # （否则它应当落在可注入桶里，而不是靠词法包含兜底）。
        root = name.split(".")[0]
        assert not hasattr(rawcopy_module, root)
        assert not (root.isidentifier() and hasattr(builtins, root))


@pytest.mark.parametrize("dotted", ADMISSION_INJECTION_TARGETS)
def test_admission_call_boundary_contains_injected_non_vocabulary_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dotted: str
) -> None:
    """对准入段每个调用点注入一个非词表异常，断言收口生效且零写入。

    参数集由 AST 派生，故这不是「加两个用例」：准入段将来新写的调用点会自动入表。
    """
    raw_root, work_dir = build_tree(tmp_path)
    calls: list[str] = []

    def raiser(*args: Any, **kwargs: Any) -> Any:
        calls.append(dotted)
        raise _ProbeEscape(f"注入到 {dotted}")

    _install(monkeypatch, dotted, raiser)
    try:
        staged(raw_root, work_dir)
    except (RawStagingError, ConfigError) as exc:
        if dotted in ADMISSION_NONCALL_CONSUMPTION:
            assert not calls, f"{dotted} 已按调用点生效，登记表该行已陈旧"
        else:
            assert calls, f"{dotted} 未被调用，但 staging 失败了"
            assert isinstance(exc.__cause__, _ProbeEscape) or isinstance(
                exc.__context__, _ProbeEscape
            ), "注入的异常必须留在 `__cause__`/`__context__` 里，不得被抹掉"
        if isinstance(exc, RawStagingError):
            assert exc.kind in rawcopy_module.ERROR_KINDS
        # 零写入取证 MUST 落在**收口成功**这条分支上——19 个注入点实际走的就是它。
        # （原先这两行写在下面 `raise` 之后，是不可达死码：探针于是对 governing
        # invariant 第三合取项「不留任何部分产物」零判别力。）
        monkeypatch.undo()
        assert snapshot(work_dir) == {}, "准入期失败 MUST 零写入"
    except BaseException as exc:  # 探针要看的就是逃逸
        raise AssertionError(
            f"{dotted} 处注入的 {type(exc).__name__} 逃出了 "
            "{ConfigError, RawStagingError} 词表"
        ) from exc
    else:
        monkeypatch.undo()
        assert not calls, f"{dotted} 被调用了却没有失败，注入无效"
        assert dotted in ADMISSION_UNREACHED_ON_HAPPY_PATH, (
            f"{dotted} 在正向路径上未被调用，且不在 "
            "ADMISSION_UNREACHED_ON_HAPPY_PATH 登记表内"
        )


def test_admission_unreached_ledger_has_no_stale_rows() -> None:
    """登记表不得有陈旧行：每一行都必须仍是准入段的一个调用点。"""
    assert set(ADMISSION_UNREACHED_ON_HAPPY_PATH) <= set(ADMISSION_INJECTION_TARGETS)
    assert set(ADMISSION_NONCALL_CONSUMPTION) <= set(ADMISSION_INJECTION_TARGETS)
    assert not set(ADMISSION_NONCALL_CONSUMPTION) & set(
        ADMISSION_UNREACHED_ON_HAPPY_PATH
    )


def _json_depth_that_overflows(cap: int = 200_000) -> int | None:
    """找出让 `json.loads` 抛 `RecursionError` 的最小可测嵌套深度；找不到返回 `None`。

    深度写死是不可移植的：CPython 3.12 上 6 万层必炸，3.14 上同一份文本解析通过
    （json 的 C 扫描器不再按 Python 递归上限计数）。用例的取证对象是**收口**而不是
    解析器的实现细节，故这里自标定；标定不到就跳过并说明。
    """
    depth = 2000
    while depth <= cap:
        try:
            json.loads("[" * depth + "]" * depth)
        except RecursionError:
            return depth
        depth *= 4
    return None


@pytest.mark.parametrize(
    ("shape", "kind"),
    [
        ("overflow", "source-manifest"),
        ("recursion", "source-manifest"),
    ],
)
def test_round3_named_escapes_are_now_contained(
    tmp_path: Path, shape: str, kind: str
) -> None:
    """round-3 verifier 实测逃逸的两条具名形态，作为收口的端到端回归。

    它们**不是**本类的闭合证据（闭合由上面的结构断言与参数化探针承担），只是把
    verifier 的两条复现钉住，防止将来的重构把收口挪走而探针恰好都走别的分支。
    """
    if shape == "overflow":
        payload = source_manifest_payload("gfs")
        # `int(1e400)` -> `OverflowError`（不是 `ValueError`，不在任何 except 元组里）。
        # 形态闸门先接住它并给出更准的消息；无论走闸门还是走地板，都必须落进词表。
        raw_text = json.dumps(payload).replace(
            '"forecast_hour": 0', '"forecast_hour": 1e400', 1
        )
    else:
        # 6 万层嵌套放在一份**其余部分完全合规**的 manifest 的附加键里：`json.load`
        # 抛 `RecursionError`（`RuntimeError` 子类），同样不在任何 except 元组里。
        depth = _json_depth_that_overflows()
        if depth is None:
            pytest.skip(
                "本解释器的 JSON 解析器在可测深度内不抛 `RecursionError`"
                "（CPython 3.14 起 json 的 C 扫描器不再按 Python 递归上限计数）；"
                "该形态的收口由上方参数化逃逸探针无条件覆盖，CI 钉 3.12 会实跑本行"
            )
        payload = source_manifest_payload("gfs")
        payload["metadata"]["deep"] = "<PLACEHOLDER>"
        # 文本拼装，**不在用例里 `json.loads` 整份**：那会先把测试进程自己撞到上限。
        raw_text = json.dumps(payload).replace(
            '"<PLACEHOLDER>"', "[" * depth + "]" * depth, 1
        )
    raw_root, work_dir = build_tree(tmp_path)
    write_source_manifest(raw_root, "gfs", raw_text)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, kind)
    assert snapshot(work_dir) == {}


# --- Row：源 entry 的索引键完整性（两条**独立**缺陷，各自可触发）--------------
#
# round-3 verifier 实测证明二者可分离，只补一条会直接重演类闭合失败：
# - 形态闸门关不掉「纯整数重复键」（不涉及任何归一）；
# - injectivity 守卫关不掉「唯一一条 `3.9` entry」（不涉及任何重复）。
# 这是本模块唯一一条会**成功返回 + 输出静默错误**的路径：影子 entry 顶掉真实
# (lead, variable) 的 `remote_url` 与六键，副本是真的、清单的来源声明是假的。


def _shadow_entry(payload: dict[str, Any], lead: int, variable: str, **overrides: Any):
    """复制一条真实 entry 做影子，并**保留 `idx_selectors`**。

    verifier 记录的构造陷阱：影子 entry 若少了 `metadata["idx_selectors"]`，会被更早
    的 `_check_accumulation` 以 `accumulation-metadata` 吸收，用例于是「因为错误的
    理由」变绿，看起来像已经防御住了。
    """
    original = source_entry(payload, lead, variable)
    shadow = json.loads(json.dumps(original))
    assert IDX_SELECTORS_TEST_KEY in shadow["metadata"]
    shadow["remote_url"] = "https://attacker.invalid/bogus.grib2"
    shadow["metadata"]["grib_short_name"] = "ATTACKER"
    shadow.update(overrides)
    payload["entries"].append(shadow)
    return shadow


IDX_SELECTORS_TEST_KEY = "idx_selectors"


@pytest.mark.parametrize(
    ("bad_value", "why"),
    [
        (3.9, "浮点被 `int()` 截断成 3"),
        (3.0, "整值浮点同样不是源里写的那个值"),
        ("3", "字符串被 `int()` 解析成 3"),
        (True, "`bool` 是 `int` 子类，`int(True) == 1`"),
    ],
)
def test_lossy_forecast_hour_shape_is_refused_before_any_write(
    tmp_path: Path, bad_value: Any, why: str
) -> None:
    """`forecast_hour` 的**形态闸门**：`int()` 有损归一的取值一律拒。

    单独可触发：本用例的坏 entry 是该 (lead, variable) 的**唯一**一条，不涉及任何
    重复键，故 injectivity 守卫关不掉它。
    """
    payload = source_manifest_payload("gfs")
    entry = source_entry(payload, 3, "apcp")
    entry["forecast_hour"] = bad_value
    # 前提取证：pin 侧的 `int()` 确实会把它静默归一成另一个合法 lead。
    assert int(bad_value) in (1, 3) and int(bad_value) is not bad_value
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert "forecast_hour" in str(excinfo.value)
    assert snapshot(work_dir) == {}


# 形态闸门 `if not isinstance(entry, Mapping) or "forecast_hour" not in entry` 是一条
# **复合闸门**，两个分量各自的语义都是「这条 entry 不归本闸门管，交给结构闸门给出具名
# 诊断」。两个分量各配一条判别器，判据取在消息上（kind 两侧同为 `source-manifest`）。


def test_non_mapping_source_entry_is_diagnosed_by_the_structure_gate(
    tmp_path: Path,
) -> None:
    """第一分量 `not isinstance(entry, Mapping)`：非容器 entry MUST 走结构闸门。

    去掉该分量后 `"forecast_hour" not in 42` 抛 `TypeError`（`int` 不可迭代），落进
    准入地板，诊断退化成泛化兜底。用 `42` 而不是字符串：字符串上 `in` 是**合法**的
    子串判定、不抛，那样的输入对本分量没有判别力。
    """
    payload = source_manifest_payload("gfs")
    payload["entries"].append(42)
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert STRUCTURE_MESSAGE in str(excinfo.value)
    assert FLOOR_MESSAGE not in str(excinfo.value)
    assert snapshot(work_dir) == {}


def test_source_entry_without_forecast_hour_is_diagnosed_by_the_structure_gate(
    tmp_path: Path,
) -> None:
    """第二分量 `"forecast_hour" not in entry`：缺该键的 entry MUST 走结构闸门。

    去掉该分量后 `entry["forecast_hour"]` 抛 `KeyError`，同样落进准入地板。
    """
    payload = source_manifest_payload("gfs")
    orphan = json.loads(json.dumps(source_entry(payload, 3, "apcp")))
    orphan.pop("forecast_hour")
    payload["entries"].append(orphan)
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert STRUCTURE_MESSAGE in str(excinfo.value)
    assert FLOOR_MESSAGE not in str(excinfo.value)
    assert snapshot(work_dir) == {}


def test_safe_repr_falls_back_again_when_even_the_type_name_raises() -> None:
    """`_safe_repr` 的**内层**兜底：连 `type(value).__name__` 都抛时仍不得抛。

    外层兜底自己会取类型名去拼消息，于是一个 `__name__` 抛异常的元类能让「保证不抛」
    的收口器与 `rollback` 在它们**自己的**兜底逻辑里失守。这条腿此前无判别器（删掉
    内层 `except` 全套件不变红）。
    """

    class ExplodingName(type):
        @property
        def __name__(cls) -> str:  # 元类上的 property，故形参是类而非实例
            raise RuntimeError("类型名也取不到")

    class Pathological(metaclass=ExplodingName):
        def __repr__(self) -> str:
            raise RuntimeError("repr 自身抛异常")

    value = Pathological()
    # 前提取证：两层都真的会抛，本用例不是在测一个不会触发的分支。
    with pytest.raises(RuntimeError):
        repr(value)
    with pytest.raises(RuntimeError):
        _ = type(value).__name__
    # 抛出即失败，但**不让病态对象进异常链**：它会连 pytest 自己的报告拼装一起炸
    # （实测：内层兜底缺失时整轮 pytest 以 INTERNALERROR 收场，看不到红条）。
    try:
        rendered = rawcopy_module._safe_repr(value)
    except BaseException as exc:  # noqa: BLE001 —— 判的就是「它抛了没有」
        rendered = f"<_safe_repr 抛了 {type(exc).__name__}>"
    assert rendered == "<无法取 repr 的对象>"

    # 对照：只有 `__repr__` 抛的普通对象走**外层**兜底，两条腿不是同一条。
    class OnlyReprRaises:
        def __repr__(self) -> str:
            raise RuntimeError("只有 repr 抛")

    assert rawcopy_module._safe_repr(OnlyReprRaises()) == (
        "<OnlyReprRaises 对象，repr() 自身抛异常>"
    )


def test_fractional_shadow_entry_cannot_hijack_a_real_slot(tmp_path: Path) -> None:
    """影子 entry（`forecast_hour: 3.9` + 完整 `idx_selectors`）MUST NOT 被接受。

    **本用例是两条闸门的联合端到端回归，不是任一条的判别器**，这一点如实写明：该影子
    同时是「`int()` 有损归一」与「(3, "apcp") 重复键」两种违规，于是只去形态闸门时由
    injectivity 守卫接住、只去 injectivity 守卫时由形态闸门接住，**只有两条同时去掉
    才变红**（round-4 实测：G1 不红、G2 不红、G1+G2 红）。原 docstring 声称「没有形态
    闸门时它会占住槽位且 staging 正常成功」，在本 head 上不成立。
    单条闸门各自的判别器另有其人，不在此重复构造：形态闸门是
    `test_lossy_forecast_hour_shape_is_refused_before_any_write[3.9]`（坏 entry 是该
    (lead, variable) 的唯一一条，不涉重复），injectivity 守卫是
    `test_duplicate_source_entry_key_is_refused`（重复键是纯整数，不涉归一）。
    它在这里的价值是那条**联合**性质：「一条源侧影子 entry 无法劫持一个真实槽位」。
    `idx_selectors` 必须带上——否则它会被 `_check_accumulation` 提前吸收成
    `accumulation-metadata`，用例白绿。
    """
    payload = source_manifest_payload("gfs")
    shadow = _shadow_entry(payload, 3, "apcp", forecast_hour=3.9)
    assert int(shadow["forecast_hour"]) == 3  # 前提取证：确实指向真实槽位
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


def test_duplicate_source_entry_key_is_refused(tmp_path: Path) -> None:
    """`_index_source_entries` 的 **injectivity 守卫**：重复键一律拒，不后写覆盖。

    单独可触发：本用例的重复键是**纯整数**的，不涉及任何 `int()` 归一，故形态闸门
    关不掉它。两条断言合起来是「两条缺陷可分离」的回归形式。
    """
    payload = source_manifest_payload("gfs")
    shadow = _shadow_entry(payload, 3, "apcp")
    assert shadow["forecast_hour"] == 3 and isinstance(shadow["forecast_hour"], int)
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert "唯一" in str(excinfo.value)
    assert snapshot(work_dir) == {}


def test_duplicate_key_on_a_lead_this_round_does_not_request_is_still_refused(
    tmp_path: Path,
) -> None:
    """重复键的判定面是**整份源 manifest**，不是本轮消费到的那几条。

    索引是 (lead, variable) -> entry 的函数；源侧不满足单射时本模块无从判断该取哪
    一条，MUST NOT 因为「这条这轮用不到」就放行——下一轮 config 换个 lead 就命中。
    """
    payload = source_manifest_payload("gfs")
    _shadow_entry(payload, SOURCE_EXTRA_HOURS[0], "apcp")
    raw_root, work_dir = build_tree(tmp_path, manifest=payload)
    with pytest.raises(RawStagingError) as excinfo:
        staged(raw_root, work_dir)
    expect_kind(excinfo, "source-manifest")
    assert snapshot(work_dir) == {}


# --- Row：containment 的 inode 身份判据，`inner` **自身**分量 ------------------


def test_contains_by_identity_catches_inner_being_a_link_to_outer(
    tmp_path: Path,
) -> None:
    """`inner` 自身就是指向 `outer` 的链时，只有「自身分量」这一条腿咬得住。

    这是**无条件**判别器：`os.stat` 跟随 symlink，与卷的大小写敏感性无关，故它在
    ubuntu-latest（ext4，大小写敏感）与 darwin/APFS 上同样有判别力——而 seam 级的
    大小写别名用例在大小写敏感卷上必然自跳过（见本文件 `_case_insensitive`）。
    """
    outer = tmp_path / "nwm-raw"
    outer.mkdir()
    inner = tmp_path / "link-to-raw"
    inner.symlink_to(outer, target_is_directory=True)
    # 前提取证：`inner` 在**词法**上不在 `outer` 之下，故 `is_relative_to` 这类纯
    # 字符串前缀判据抓不到它。（原先这里比的是一个从未创建的 `elsewhere` 目录，对
    # 任意路径恒为 False——是恒真式，不是前提。）
    assert not inner.is_relative_to(outer)
    assert os.path.samestat(os.stat(inner), os.stat(outer))
    assert rawcopy_module._contains_by_identity(outer, inner) is True
    # 反向判别器：祖先段里没有 `outer` 时必须为 False（否则上一条恒真）。
    assert rawcopy_module._contains_by_identity(outer, tmp_path) is False


# --- Row：tier-3（`BaseException`）的残留信号 --------------------------------


def test_keyboard_interrupt_with_a_failing_rollback_carries_the_residue_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tier-3 的 `if failures: exc.add_note(...)` 的判别器。

    既有的唯一一条 `KeyboardInterrupt` 用例里回滚是**成功**的，于是 `failures` 恒空、
    该分支从不进入，删掉整段 `add_note` 全套件不变红（round-3 verifier 变异体 E4
    存活）。判别器必须**同时**注入两处：中断复制 + 让回滚原语失败。
    """
    raw_root, work_dir = build_tree(tmp_path)
    _copy_failure_with_broken_rollback(monkeypatch, work_dir, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt) as excinfo:
        staged(raw_root, work_dir)
    monkeypatch.undo()
    # 类型 MUST NOT 被改写成 `RawStagingError`：Ctrl-C 不是一次 staging 失败。
    assert not isinstance(excinfo.value, RawStagingError)
    notes = "".join(getattr(excinfo.value, "__notes__", []))
    assert "清理" in notes and "残留" in notes
    survivors = sorted(p.name for p in (work_dir / "raw" / "gfs" / CYCLE_DIR).iterdir())
    assert survivors == [bundle_name("gfs", lead) for lead in (0, 3)]
    for name in survivors:
        assert name in notes


# --- Row：`rollback`「保证不抛」在 repr 抛异常时也成立 ------------------------


def test_rollback_does_not_raise_when_the_exception_repr_itself_raises() -> None:
    """`{exc!r}` 在 handler 内部求值，`repr` 自身抛异常就会击穿「保证不抛」。

    这是 `rollback` 的兜底逻辑**低一层**的洞：三个 handler 都跑在「已有异常正在外抛」
    的上下文里，`rollback` 抛出的任何异常都会替换那个异常并逃出九项词表。
    """

    class NastyError(OSError):
        def __repr__(self) -> str:
            raise RuntimeError("repr 自身炸了")

    written = rawcopy_module._Written()
    written.files.append(Path("/nonexistent/probe-file"))

    def exploding_unlink(path: Any) -> None:
        raise NastyError(errno.EIO, "注入的清理故障")

    real_unlink = os.unlink
    # 直接替换而不是 monkeypatch：`rollback` 读的是 `os.unlink` 这个全局绑定。
    os.unlink = exploding_unlink
    try:
        failures = written.rollback()
    finally:
        os.unlink = real_unlink
    assert len(failures) == 1
    assert "probe-file" in failures[0]
    assert "repr" in failures[0]  # 兜底文案，而不是抛出


# --- Row：oracle 类的**闭合谓词**，按谓词清扫而不是逐条打补丁 -----------------
#
# 谓词（round-3 batch-C verifier 写下）：**凡测试对产出 `raw-manifest.json` 断言的每
# 一个值，源侧对应值 MUST 被偏移使「承接自源」与「由 yd 自算」发散。** round 1 找到
# 1 条腿、round 2 找到 6 条、round 3 又找到 2 条——一个每次独立清扫都还能吐出新实例
# 的类是被搜刮空了，不是闭合了。本用例把谓词本身写成断言：它对**产出 manifest 的
# 全部断言面**逐项核对源侧取值确实不同，于是将来任何一次 fixture 漂移让两侧重新
# 重合，都会在这里变红，而不是靠下一位 reviewer 去猜还有没有第 N+1 条腿。


def test_every_asserted_manifest_value_diverges_from_its_source_side_value(
    tmp_path: Path,
) -> None:
    # 本轮 lead 取 `(3, 6)` 而源侧小时表从 0 起到 9 止：四个小时键在**两端**都发散。
    # 默认 fixture 只让 last 一端发散，`first_forecast_hour` 两侧恰好都是 0——那正是
    # 本谓词要抓的重合形态，故清扫必须在两端都发散的输入上做。
    leads = (3, 6)
    config = make_config(gfs=make_source(lead_hours=leads))
    payload = source_manifest_payload("gfs", leads=leads, declared_hours=(0, 3, 6, 9))
    raw_root, work_dir = build_tree(tmp_path, leads=leads, manifest=payload)
    result = staged(raw_root, work_dir, "gfs", config)
    produced = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    # --- manifest 级 ---
    assert produced["source_id"] != payload["source_id"]
    assert produced["cycle_time"] != payload["cycle_time"]
    assert produced["manifest_uri"] != payload["manifest_uri"]
    for key in (
        "first_forecast_hour",
        "last_forecast_hour",
        "requested_forecast_hours",
        "forecast_hours",
    ):
        assert produced["metadata"][key] != payload["metadata"][key], key
    # 「仅含」半边：源侧 manifest 级 metadata 与产出侧**整体**不等（上面四键逐条不等
    # 已蕴含），此处显式记明它由 `==` 全等断言承担，不另设偏移键。
    assert produced["metadata"] != payload["metadata"]

    # --- entry 集合与顺序 ---
    assert len(produced["entries"]) != len(payload["entries"])
    produced_pairs = [(e["forecast_hour"], e["variable"]) for e in produced["entries"]]
    source_pairs = [(e["forecast_hour"], e["variable"]) for e in payload["entries"]]
    assert set(produced_pairs) != set(source_pairs)  # 集合由 verdict 定，不是照搬
    common = [pair for pair in source_pairs if pair in set(produced_pairs)]
    assert common != produced_pairs  # 顺序由 verdict 定，不是照搬源侧顺序

    # --- entry 级 ---
    for entry in produced["entries"]:
        lead, variable = entry["forecast_hour"], entry["variable"]
        origin = source_entry(payload, lead, variable)
        assert entry["local_key"] != origin["local_key"]
        assert entry["expected_checksum"] != origin["expected_checksum"]
        assert entry["expected_size_bytes"] != origin["expected_size_bytes"]
        metadata, source_metadata = entry["metadata"], origin["metadata"]
        # 「仅含」半边：源侧带一个非承接键，整份照抄会带上它。
        assert SOURCE_UNCARRIED_METADATA_KEY in source_metadata
        assert SOURCE_UNCARRIED_METADATA_KEY not in metadata
        # entry 级 `cycle_time`/`valid_time` 反过来 MUST 逐字承接：可判别性来自
        # 「自算的实现会写 `+00:00`」，故断言的是**与自算写法**不同。
        for key in ("cycle_time", "valid_time"):
            assert metadata[key] == source_metadata[key]
            assert metadata[key].endswith(SOURCE_TIME_SUFFIX)
            assert not metadata[key].endswith("+00:00")
        # `bundle`/`cfgrib_filter_by_keys` 各带一个不可由 config / `grib_short_name`
        # 推导的分量，于是「承接」与「按已知形状重建」可判别。
        assert metadata["bundle"]["build_id"] == SOURCE_BUNDLE_TOKEN
        assert metadata["cfgrib_filter_by_keys"]["filterToken"] == SOURCE_CFGRIB_TOKEN
        # `grib_short_name`：逐字承接。源侧带不可推导后缀，故与「按变量名查标准别名表
        # 自算」发散——没有这个偏移，源侧就是标准别名本身，两种实现同值。
        assert metadata["grib_short_name"] == source_metadata["grib_short_name"]
        assert metadata["grib_short_name"].endswith(SOURCE_SHORT_NAME_TOKEN)
        assert metadata["grib_short_name"] != variable
        # 复数 `idx_selectors` 的**取值**（不只是键集）逐字承接：键集可由 config 的
        # 变量表推导，只断言键集时「整份伪造复数键」的实现无法判别。
        assert metadata["idx_selectors"] == source_metadata["idx_selectors"]
        assert metadata["idx_selectors"]
        for name, selector in metadata["idx_selectors"].items():
            assert selector[SOURCE_IDX_TOKEN_KEY] == SOURCE_IDX_TOKEN, name
        # `remote_url` MUST 取源 entry 的同名字段，MUST NOT 取 `logical_remote_url`。
        assert entry["remote_url"] == origin["remote_url"]
        assert entry["remote_url"] != source_metadata["logical_remote_url"]
        # 单数 idx 键由 yd 按变量从复数键取；源侧多变量 bundle 上根本没有单数键。
        assert "idx_selector" not in source_metadata
        assert metadata["idx_selector"] == source_metadata["idx_selectors"][variable]

    # --- 两处**故意排除**：查找键本身。偏移它们会让查找落空，判别的是查找而不是
    # 实现，故两端必须重合。这是本类的真实边界，不是漏项。
    for entry in produced["entries"]:
        origin = source_entry(payload, entry["forecast_hour"], entry["variable"])
        assert entry["forecast_hour"] == origin["forecast_hour"]
        assert entry["variable"] == origin["variable"]
