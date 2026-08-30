"""`yd_producer.init.bootstrap` 首态建链测试（tasks.md 任务 11.1、issue #21）。

治理不变量（fixture 的 Invariant Matrix）：`init` 要么让 `YD_ROOT` 从「全新根」转到
「每个 source 恰有一份重戳到其首轮 T 的首态」，要么**一个字节都不写**；除阶段 B 内的写入
失败外没有第三种终态，且任何情况下 `output/` 与已有 `states/` 内容都不被修改或删除。

期望值口径（fixture 的 Review focus 逐字）：

- header 的分钟 token 期望值一律由**锚点常量**给出（`EPOCH_MINUTES_*`，由
  `(T - 1970-01-01T00:00Z) / 60` 手算并在常量注释里写明推导），**MUST NOT** 在用例里再调
  一次 `restamp_to_absolute_time`——被测函数自判是永真式。
- 落盘字节的期望值由**构造侧**给出：把合成率定末态里的 `DEFAULT_MINUTE` 文本换成期望的
  分钟文本，逐字节相等即同时钉死「minute token 被重写」与「其余字节逐字不变」。
- 配置取值刻意**非默认**（`cycle.hours` 与变体目录名各有一条专门的回归行）：硬编码
  `[0, 12]` 或硬编码 `input/models/yd_<source>` 的实现在那两行必红。
- 所有交给 `safe_fs` 的路径都由 `tmp_path.resolve()` 派生：macOS 的 `/var` 是 symlink，
  `safe_fs._anchor_for` 逐段拒 symlink，未 resolve 即得与实现无关的假红。

`DIR_SEGMENTS` 与 cycle 目录格式在本 fixtures 模块内**逐字写死**，MUST NOT 从被测模块 import：两侧
共用一个字面量会让大小写/格式断言变成恒真式。
"""

from __future__ import annotations

import contextlib
import os
import stat
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cfg_ic_fixtures import (
    DEFAULT_MINUTE,
    build_cfg_ic,
    build_cfg_ic_rows,
    mesh_row,
    river_row,
)

from yd_producer import init
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
from yd_producer.init import bootstrap

# --- 锚点 --------------------------------------------------------------------

#: 注入的执行时刻。扫描窗因此恒为 [2026-08-20T12:00Z, 2026-08-27T12:00Z]（双端闭）。
NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)
WINDOW_START = datetime(2026, 8, 20, 12, tzinfo=UTC)

#: 各锚点时刻的 epoch 分钟。推导：`(T - 1970-01-01T00:00Z) / 60`。
#: 2026-08-20T12Z 距 epoch 20685.5 天 = 20685.5 * 1440 = 29787120 分钟；此后每 12 小时
#: 加 720 分钟，故下面每个常量都可由上一个 +720*k 手工核对。
EPOCH_MINUTES_20_12Z = 29787120  # 2026-08-20 12Z（窗下端点）
EPOCH_MINUTES_21_00Z = 29787840  # +720*1
EPOCH_MINUTES_21_12Z = 29788560  # +720*2
EPOCH_MINUTES_22_00Z = 29789280  # +720*3
EPOCH_MINUTES_24_12Z = 29792880  # +720*8
EPOCH_MINUTES_25_00Z = 29793600  # +720*10
EPOCH_MINUTES_26_12Z = 29795760  # +720*13
EPOCH_MINUTES_27_00Z = 29796480  # +720*14
EPOCH_MINUTES_27_12Z = 29797200  # +720*15（== NOW，钉死扫描窗**上**端点闭合）

#: off-grid 的执行时刻：既不是 00Z 也不是 12Z，故同一天的 12Z 候选**被枚举到**却严格晚于
#: `now`。窗因此为 [2026-08-20T06:00Z, 2026-08-27T06:00Z]。用它才能把「日期网格上界」与
#: 「`cycle <= now` 比较」两条约束分开钉死（`NOW + 12h` 落在从不被枚举的日期上）。
NOW_OFF_GRID = datetime(2026, 8, 27, 6, tzinfo=UTC)

CYCLE_DIR_FORMAT = "%Y%m%d%H"  # 逐字写死，见模块头
STATE_SUFFIX = ".cfg.ic"

#: 入参 source → raw 目录段（NWM@8ae9b8f2 packages/common/source_identity.py:5-9）。
DIR_SEGMENTS = {"ifs": "IFS", "gfs": "gfs"}
#: 阶段 B 的写入序，逐字写死（fixture 裁决 5）。
WRITE_ORDER = ("ifs", "gfs")

IFS_BUNDLES = ("ifs.t{cycle_hour:02d}z.f{lead:03d}.bundle.grib2",)
GFS_BUNDLES = ("gfs.t{cycle_hour:02d}z.pgrb2.0p25.f{lead:03d}.bundle.grib2",)
BUNDLES = {"ifs": IFS_BUNDLES, "gfs": GFS_BUNDLES}
LEADS = (0, 3)

DEFAULT_VARIANTS = {"ifs": "input/models/yd_ifs", "gfs": "input/models/yd_gfs"}


# --- 合成配置 ----------------------------------------------------------------


def make_config(
    *,
    cycle_hours: tuple[int, ...] = (0, 12),
    variants: dict[str, str] | None = None,
    ifs_bundles: tuple[str, ...] = IFS_BUNDLES,
    gfs_bundles: tuple[str, ...] = GFS_BUNDLES,
) -> Config:
    names = dict(DEFAULT_VARIANTS if variants is None else variants)
    return Config(
        forecast_days=7,
        output_interval_minutes=60,
        checkpoint_hours=(12,),
        reach_count=3988,
        nwm_mapping_builder_module="workers.mapping_builder.cli",
        # issue #20 新增的必需字段；init 不读它，但 `Config` 零默认值，缺它即构造失败。
        nwm_canonical_grid_id=CanonicalGridConfig(
            gfs="fixture-grid-gfs", ifs="fixture-grid-ifs"
        ),
        cycle=CycleConfig(hours=tuple(cycle_hours)),
        variants=VariantsConfig(gfs=names["gfs"], ifs=names["ifs"]),
        raw=RawConfig(
            ifs=RawSourceConfig(
                lead_hours=LEADS,
                variables=("t2m", "tp"),
                bundles=tuple(ifs_bundles),
                f000_special=False,
            ),
            gfs=RawSourceConfig(
                lead_hours=LEADS,
                variables=("tmp2m", "apcp"),
                bundles=tuple(gfs_bundles),
                f000_special=True,
            ),
        ),
        slurm=SlurmSchema(required_fields=("partition", "account")),
    )


def make_local(yd_root: Path, raw_root: Path) -> LocalConfig:
    base = yd_root.parent
    return LocalConfig(
        yd_root=str(yd_root),
        scratch_root=str(base / "scratch"),
        shud_binary=str(base / "bin" / "shud"),
        nwm=NwmLocal(
            raw_root=str(raw_root),
            checkout_root=str(base / "nwm" / "checkout"),
            python=str(base / "nwm" / ".venv" / "bin" / "python"),
        ),
        slurm={"partition": "cpu", "account": "yd"},
        cron=CronLocal(lock_path=str(base / "run" / "lock"), log_dir=str(base / "log")),
    )


# --- 合成目录树 --------------------------------------------------------------


def default_payload() -> bytes:
    """默认率定末态：3 token 原生 header（`<mesh> <mesh-state-columns> <minute>`）。"""
    return build_cfg_ic(mesh_count=2, river_count=2, delimiter="\t").payload


def compat_four_token_payload() -> bytes:
    """4 token 兼容 header（`<mesh> <river> <lake> <minute>`）+ 非空 lake 段。"""
    return build_cfg_ic_rows(
        mesh_rows=[mesh_row(1), mesh_row(2)],
        river_rows=[river_row(1)],
        lake_rows=[river_row(1)],
        header_tokens=("2", "1", "1", DEFAULT_MINUTE),
        delimiter="\t",
    ).payload


def large_payload(mesh_count: int = 200) -> bytes:
    """一份**大于 `RLIMIT_FSIZE` 测试上限**的率定末态，用于构造写循环中途的真实 I/O 失败。"""
    return build_cfg_ic(
        mesh_count=mesh_count, river_count=mesh_count, delimiter="\t"
    ).payload


def two_token_payload() -> bytes:
    """只有 2 个数值 token 的 header（issue #1197 形状）：`restamp` 的 shape 门必拒。"""
    return build_cfg_ic_rows(
        mesh_rows=[mesh_row(1), mesh_row(2)],
        river_rows=[river_row(1)],
        header_tokens=("2", "6"),
        delimiter="\t",
    ).payload


def expected_bytes(payload: bytes, epoch_minutes: int) -> bytes:
    """构造侧的期望产物：只把 `DEFAULT_MINUTE` 换成 `%.6f` 的目标分钟，其余逐字节不动。"""
    assert payload.count(DEFAULT_MINUTE.encode()) == 1
    return payload.replace(
        DEFAULT_MINUTE.encode(), f"{float(epoch_minutes):.6f}".encode()
    )


class Tree:
    """一棵合成 `YD_ROOT` + NWM raw 树。所有路径都从 `tmp_path.resolve()` 派生。"""

    def __init__(
        self,
        tmp_path: Path,
        *,
        config: Config | None = None,
        payloads: dict[str, bytes] | None = None,
        calibration_names: dict[str, str] | None = None,
        make_states: bool = True,
        make_output: bool = True,
    ) -> None:
        root = tmp_path.resolve()
        self.root = root
        self.yd_root = root / "yd"
        self.raw_root = root / "raw"
        self.config = make_config() if config is None else config
        self.local = make_local(self.yd_root, self.raw_root)
        self.states = self.yd_root / "states"
        self.output = self.yd_root / "output"
        if make_states:
            self.states.mkdir(parents=True)
        if make_output:
            self.output.mkdir(parents=True)
        self.raw_root.mkdir(parents=True)
        self.payloads = {
            source: (payloads or {}).get(source, default_payload())
            for source in WRITE_ORDER
        }
        names = calibration_names or {}
        self.calibration: dict[str, Path] = {}
        for source in WRITE_ORDER:
            variant_dir = self.yd_root / getattr(self.config.variants, source)
            variant_dir.mkdir(parents=True, exist_ok=True)
            name = names.get(source, f"yd_{source}{STATE_SUFFIX}")
            path = variant_dir / name
            path.write_bytes(self.payloads[source])
            self.calibration[source] = path

    def variant_dir(self, source: str) -> Path:
        return self.yd_root / getattr(self.config.variants, source)

    def cycle_dir(self, source: str, cycle: datetime) -> Path:
        return self.raw_root / DIR_SEGMENTS[source] / cycle.strftime(CYCLE_DIR_FORMAT)

    def write_cycle(self, source: str, cycle: datetime, *, complete: bool = True):
        """铺一轮 raw。`complete=False` 时故意少铺最后一个预期文件。"""
        base = self.cycle_dir(source, cycle)
        base.mkdir(parents=True, exist_ok=True)
        names = [
            pattern.format(cycle_hour=cycle.hour, lead=lead)
            for lead in LEADS
            for pattern in BUNDLES[source]
        ]
        if not complete:
            names = names[:-1]
        for name in names:
            # 非 UTF-8 前导字节：真实 bundle 是 GRIB2 二进制。
            (base / name).write_bytes(b"GRIB\xff\x00stub")
        return base

    def state_path(self, source: str, cycle: datetime) -> Path:
        return self.states / source / (cycle.strftime(CYCLE_DIR_FORMAT) + STATE_SUFFIX)

    def run(self, *, now: datetime = NOW) -> init.InitReport:
        return bootstrap(local=self.local, config=self.config, now=now)


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """整棵树的字节 + mtime_ns 快照（不存在的根返回空）。"""
    result: dict[str, tuple[bytes, int]] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_file():
            info = path.stat()
            result[key] = (path.read_bytes(), info.st_mtime_ns)
        else:
            result[key] = (b"<dir>", 0)
    return result


def all_files(root: Path) -> list[Path]:
    return [path for path in sorted(root.rglob("*")) if path.is_file()]


@contextlib.contextmanager
def unreadable(path: Path) -> Iterator[None]:
    """把 `path` 临时置为 `chmod 0o000`，退出时**一定**恢复（否则 tmp 清理会踩到）。"""
    original = stat.S_IMODE(path.stat().st_mode)
    path.chmod(0o000)
    try:
        yield
    finally:
        path.chmod(original)


@contextlib.contextmanager
def stat_hostile(path: Path) -> Iterator[None]:
    """把 `path` 临时置为 `0o444`：**可列目录**、但子项 `lstat`/`stat` 抛 `EACCES`。

    与 `unreadable`（`0o000`，连 `listdir` 都不行）刻意分层：`0o000` 只行使
    `_entry_names`，本 helper 才行使 `_entry_kind` / `_is_directory` 这一层。darwin 与
    Linux 上「目录有 r 无 x」即此语义（读得到名字，解析不了名字）。
    """
    original = stat.S_IMODE(path.stat().st_mode)
    path.chmod(0o444)
    try:
        yield
    finally:
        path.chmod(original)


def skip_if_root() -> None:
    if os.geteuid() == 0:
        pytest.skip("root 无视 mode 位，`chmod 0o000` 仍可枚举，本用例无判别力")


def assert_zero_write(tree: Tree, before_states, before_output) -> None:
    assert snapshot(tree.states) == before_states
    assert snapshot(tree.output) == before_output
    assert all_files(tree.states) == []
