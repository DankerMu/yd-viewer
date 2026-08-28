"""`prepare` 编排：从外部基线包生成两个 direct-grid 变体与两份 viewer GeoJSON。

权威：compute-loop §6.1、products-contract §2/§6、spec `prepare-variants`、
tasks.md「Issue #20 fixture（任务 10.3）」。

**总不变量（全有或全无）**：本模块对 `YD_ROOT` 的效果要么是「四个终名（两个变体目录 +
两份 GeoJSON）全部由本次运行新建」，要么是「`YD_ROOT` 回到执行前的条目集合，既有内容
逐字节不变」。任何既有条目 MUST NOT 被覆盖或删除；无论成败，scratch 工作目录与
`YD_ROOT` 内 staging 位置都在返回前删除。唯一已接受的残留是四个终名 rename 之间进程被
SIGKILL（或 NFS `ESTALE`）的窗口——POSIX 没有跨目录事务，无法消解；提交顺序钉死为
「两变体 → rivers → boundary」是 best-effort 的排序偏好，**不是**对 viewer 的就绪保证
（products-contract §2/§6 没有为 `input/viewer/` 定义就绪标记，本模块也不发明一个；
就绪标记与崩溃后的人工恢复程序路由为 follow-up issue #78）。

**为什么不是「scratch 目录直接 rename 到 `YD_ROOT`」**：生产上 `yd_root` 在 NFS
（`/ghdc/data/yd`，agent-ops §4.1）而 `scratch_root` 在本地盘（`/scratch/.../yd-loop/`，
agent-ops §4.2）——两棵真不同文件系统的树，而 `safe_fs.rename_entry_no_follow` 明写
`EXDEV` 是硬错误、**刻意没有** fallback copy 路径（`store/safe_fs.py:630-631`）。直接
rename 会在本地测试（两根同在 `tmp_path`）全绿而在现场必然失败。故搬运分两段：scratch
-> `YD_ROOT` 内本次专属 staging（按发布权限**新建**条目，不继承计算节点 uid/gid/mode，
agent-ops §10），再由 staging 同盘 rename 到终名。与控制器发布面同构（agent-ops §8.4）。

**异常契约**：本模块对外只有 `PrepareError` 及其子类 `BuilderUnavailableError`。三处
外来异常一律包装并保留 `__cause__`——`state.cfg_ic.parse` 的 `ValueError`、
`geometry.*` 的 `GeometryError`、`store.safe_fs.*` 的 `SafeFilesystemError`。第三处最易
漏：`SafeFilesystemError` 是 **`RuntimeError` 子类而非 `OSError`**
（`store/safe_fs.py:11`），`except OSError` 兜不住它。注入 builder 抛出的任何异常同样
包装（`BuilderUnavailableError` 除外——它必须原样上浮，`cli` 靠它区分退出码 `3`）。

**文件系统原语**：一律复用 `store.safe_fs`，本模块不另写一套。**唯一豁免**是
`_copy_tree_publish`——`safe_fs` 的公共面确无 copy 原语，而扩它属 #24/#25 发布面的归属，
故树复制只落在本模块内，且仍由 `safe_fs` 的 no-follow 原语逐条构成。

**合成约定**：基线包内部布局（`BASELINE_*`）与变体内文件名（`VARIANT_*`）是本 issue 的
fixture 定义的合成约定，以模块常量暴露给 11.1 消费；真实外部基线模型包的现场布局与读取
归 M4（tasks.md 组 10）。
"""

from __future__ import annotations

import os
import stat
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from yd_producer.config import Config, LocalConfig
from yd_producer.geometry import GeometryError, write_viewer_geojson
from yd_producer.raw.source_identity import normalize_source_id
from yd_producer.state import cfg_ic
from yd_producer.store import safe_fs

__all__ = [
    "BASELINE_DOMAIN_SHP_NAME",
    "BASELINE_GIS_DIRNAME",
    "BASELINE_RIVERS_SHP_NAME",
    "SOURCE_IDS",
    "VARIANT_BINDING_NAME",
    "VARIANT_CALIBRATED_STATE_NAME",
    "VARIANT_HYDRO_PARAM_NAME",
    "VARIANT_REQUIRED_ENTRIES",
    "VIEWER_GEOJSON_NAMES",
    "BuilderUnavailableError",
    "PrepareError",
    "PrepareReport",
    "VariantBuildRequest",
    "baseline_domain_shp",
    "baseline_rivers_shp",
    "calibrated_state_path",
    "default_builder",
    "run_prepare",
    "variant_targets",
    "viewer_targets",
]

#: 两个 source 的处理与**提交顺序**（钉死；见模块头的排序偏好说明）。
SOURCE_IDS = ("gfs", "ifs")

#: 基线包内部布局（合成约定，真实布局归 M4）
BASELINE_GIS_DIRNAME = "gis"
BASELINE_RIVERS_SHP_NAME = "rivers.shp"
BASELINE_DOMAIN_SHP_NAME = "domain.shp"

#: 变体内文件名（合成约定，11.1 消费其中的率定末态）
VARIANT_CALIBRATED_STATE_NAME = "yd.cfg.ic"
VARIANT_HYDRO_PARAM_NAME = "yd.para"
VARIANT_BINDING_NAME = "yd.binding"
#: 变体目录**恰**应含有的条目集合：多一条（含 `.tmp` 残留）或少一条都拒绝提交
VARIANT_REQUIRED_ENTRIES = frozenset(
    {VARIANT_CALIBRATED_STATE_NAME, VARIANT_HYDRO_PARAM_NAME, VARIANT_BINDING_NAME}
)

#: products-contract §2 的字面量落点（非配置驱动）
VIEWER_GEOJSON_NAMES = {
    "rivers": "rivers.geojson",
    "boundary": "boundary.geojson",
}
_VIEWER_RELATIVE_DIR = Path("input") / "viewer"

#: 本次运行专属 staging 在 `YD_ROOT` 下的目录名前缀（**不**落在 `input/viewer/` 之内：
#: products-contract §2 只允许该目录存在两个文件，把 staging 建在里面等于让 viewer 看见
#: 中间态）。
_STAGING_PREFIX = ".yd-prepare-staging"
_SCRATCH_PREFIX = "prepare"

#: 发布归属：真实 builder 绑定所需的 NWM 侧 driver 归此阶段（见 `default_builder`）。
BUILDER_OWNER = "M4（node-22 真计算，docs/design.md §10）"


class PrepareError(Exception):
    """`prepare` 编排的公开异常**基类**；`cli.main` 捕获后走退出码 `1`。"""


class BuilderUnavailableError(PrepareError):
    """生产 builder 绑定尚未可用；`cli.main` 先于基类捕获它并走退出码 `3`。

    与基类**不得合并**：把"配置/产物不合法"（改配置能修）与"这条路还没通"（等 M4）
    报成同一个码，运维无从判断该做哪一件。
    """


@dataclass(frozen=True, kw_only=True)
class VariantBuildRequest:
    """一次 builder 调用的全部入参。

    字段形态是**消费上游契约、不重新协商**：`source_id` 与 `grid_id` 逐字对应 pin
    `NWM@8ae9b8f2 workers/mapping_builder/cli.py:601-602` 的 `build_direct_grid_variant`
    同名关键字参数。`source_id` 取值走 `raw.source_identity.normalize_source_id` 的
    `"gfs"`/`"ifs"`；`grid_id` 取自 `config.nwm_canonical_grid_id`。
    """

    source_id: str
    grid_id: str
    baseline_root: Path
    variant_root: Path


@dataclass(frozen=True, kw_only=True)
class PrepareReport:
    """一次成功编排的产出终名。全部路径都已提交到 `YD_ROOT`。"""

    variants: Mapping[str, Path]
    rivers_geojson: Path
    boundary_geojson: Path


Builder = Callable[[VariantBuildRequest], None]


# --- 基线包 / 变体内的合成布局 ----------------------------------------------


def baseline_rivers_shp(baseline_root: Path | str) -> Path:
    """基线 GIS 河网图层路径（合成约定）。"""
    return Path(baseline_root) / BASELINE_GIS_DIRNAME / BASELINE_RIVERS_SHP_NAME


def baseline_domain_shp(baseline_root: Path | str) -> Path:
    """基线 GIS domain 单元图层路径（合成约定）。"""
    return Path(baseline_root) / BASELINE_GIS_DIRNAME / BASELINE_DOMAIN_SHP_NAME


def calibrated_state_path(variant_root: Path | str) -> Path:
    """变体内率定末态 `cfg.ic` 路径（合成约定；11.1 的首态建链从这里读）。"""
    return Path(variant_root) / VARIANT_CALIBRATED_STATE_NAME


# --- 终名（守卫与写入的唯一来源）--------------------------------------------


def viewer_targets(local: LocalConfig) -> dict[str, Path]:
    """两份 viewer GeoJSON 的终名：products-contract §2 的字面量落点，非配置驱动。"""
    root = Path(local.yd_root)
    return {
        key: root / _VIEWER_RELATIVE_DIR / name
        for key, name in VIEWER_GEOJSON_NAMES.items()
    }


def _resolve_variant_relative(field: str, value: str, yd_root: Path) -> Path:
    """把 `variants.<source>` 的相对路径校验并拼成终名；违规即 `PrepareError`。

    相对性是 fail-closed 闸门（compute-loop §5「相对 `yd_root`，不得为绝对路径」）：
    绝对路径会把产物写到运行根之外，`..` 逃逸会让"拒绝覆盖"守卫保护的树和实际写入的树
    不是同一棵。规范化只做**词法**折叠（`os.path.normpath`），不 `resolve()`——后者会
    跟随 symlink 触碰文件系统，而这是一个在任何写入之前运行的纯函数闸门。
    """
    if not value:
        raise PrepareError(f"配置项 `{field}` 不得为空")
    candidate = Path(value)
    if candidate.is_absolute():
        raise PrepareError(
            f"配置项 `{field}` 必须是相对 `yd_root` 的路径，不得为绝对路径：{value}"
        )
    normalized = os.path.normpath(value)
    parts = Path(normalized).parts
    if normalized == os.curdir or not parts:
        raise PrepareError(f"配置项 `{field}` 不得指向 `yd_root` 自身：{value}")
    if parts[0] == os.pardir:
        raise PrepareError(
            f"配置项 `{field}` 规范化后逃出 `yd_root`：{value} -> {normalized}"
        )
    return yd_root / normalized


def _is_ancestor(candidate: Path, other: Path) -> bool:
    """`candidate` 是否为 `other` 的祖先（词法判定，两者同根且均已规范化）。"""
    return candidate != other and candidate.parts == other.parts[: len(candidate.parts)]


def variant_targets(local: LocalConfig, config: Config) -> dict[str, Path]:
    """两个变体目录的终名——**唯一**的变体终名来源。

    拒绝覆盖检查与提交写入 MUST 都走本函数（#32 记录的守卫/写入面分叉在此消除：两处各
    写一遍字面量时，改 `config.variants.*` 只会让其中一处跟着走）。

    本函数同时行使两条 fail-closed 闸门，因为二者都必须在**任何写入与任何 builder 调用
    之前**成立：

    1. **相对性**（见 `_resolve_variant_relative`）；
    2. **四个终名两两互异、且任一 MUST NOT 是另一终名的祖先**。把 `variants.gfs` 与
       `variants.ifs` 抄成同一值是普通的配置笔误，而装载器只校验存在性与类型、不拦；
       两个 `lexists` 守卫也全过（两者都不存在），于是 `gfs` 提交成功、第二次 rename 撞
       `ENOTEMPTY`，`YD_ROOT` 停在"只有一个变体"的半提交态——直接违反总不变量。互为祖先
       同理：两个 `lexists` 与产物校验都发现不了，而提交后 `ifs` 变体会躺在已提交的
       `gfs` 变体**目录内部**。
    """
    yd_root = Path(local.yd_root)
    targets = {
        source: _resolve_variant_relative(
            f"variants.{source}", getattr(config.variants, source), yd_root
        )
        for source in SOURCE_IDS
    }

    labelled = [(f"variants.{source}", path) for source, path in targets.items()]
    labelled += [
        (f"input/viewer/{VIEWER_GEOJSON_NAMES[key]}", path)
        for key, path in viewer_targets(local).items()
    ]
    for index, (left_label, left) in enumerate(labelled):
        for right_label, right in labelled[index + 1 :]:
            if left == right:
                raise PrepareError(
                    f"终名冲突：`{left_label}` 与 `{right_label}` 指向同一路径 {left}；"
                    "四个终名必须两两不同"
                )
            if _is_ancestor(left, right) or _is_ancestor(right, left):
                raise PrepareError(
                    f"终名冲突：`{left_label}`（{left}）与 `{right_label}`（{right}）"
                    "互为祖先；任一终名不得落在另一终名之内"
                )
    return targets


# --- 生产 builder 绑定（fail-closed）----------------------------------------


def default_builder(request: VariantBuildRequest) -> None:
    """生产 builder 绑定：在**发起任何子进程之前**指名归属地失败。

    这是对 pin 的只读取证结论，不是"未做"：

    * `workers/mapping_builder/cli.py` 的 argparse `main` 在 pin 上只解析
      `--package-path` 并输出 resolution JSON，**不驱动 build**（其 docstring 明写
      SUB-5 未落地），故 `-m workers.mapping_builder.cli` 形态不足以产出变体——这正是
      tasks.md 组 8 记入 #32 的待确认项，本 issue 以取证结清；
    * 唯一能建变体的是 `build_direct_grid_variant`，它是 **programmatic-only** 且需调用
      方预先算好约 24 个关键字参数（`grid_snapshot_loader`/`snapshot_cells`/
      `grid_snapshot_reference`/`approvals`/`rollback_target`/`distance_qa`/
      `capacity_report`/`proj_crs_database_version` 等），其中多项来自 NWM grid
      registry；而 yd MUST NOT 在运行时 import NWM（design.md D6 / agent-ops §7.2），
      故真实调用需要 NWM 侧另加 driver。

    因此本绑定 MUST NOT 静默成功、MUST NOT 先起子进程再失败——后者会拿到退出码 0 的
    resolution JSON，随后在 reach 校验处报出一条**归属谎报**的错误（"reach 数不符"，
    而真因是"这条路还没通"）。
    """
    raise BuilderUnavailableError(
        f"生产 mapping-builder 绑定尚未可用，归属 {BUILDER_OWNER}："
        f"NWM@8ae9b8f2 的 `build_direct_grid_variant` 是 programmatic-only、需约 24 个"
        "来自 grid registry 的关键字参数，而 yd 运行时 MUST NOT import NWM"
        "（design.md D6 / agent-ops §7.2），真实调用需 NWM 侧另加 driver；"
        "`-m <nwm_mapping_builder_module>` 形态的 CLI 在该 pin 上只输出 resolution "
        "JSON、不驱动 build。本次请求"
        f"（source_id={request.source_id}、grid_id={request.grid_id}）未发起任何子进程"
    )


# --- 文件系统助手（全部由 safe_fs 原语构成）---------------------------------


def _ensure_directory(path: Path, created: list[Path]) -> Path:
    """创建目录并把**本次新建**的每一层登记进 `created`（供提交失败时回滚）。

    先按存在性逐层向上探一遍再交给 `safe_fs.ensure_directory_no_follow`：后者一次建齐
    全部缺失层但不告诉调用方建了哪几层，而总不变量要求提交失败时 `YD_ROOT` 回到执行前的
    条目集合——「为提交而新建的父目录」属本次条目，必须能被指名删除。
    """
    missing: list[Path] = []
    probe = path
    while not os.path.lexists(probe):
        missing.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent
    target = _wrap_fs(
        lambda: safe_fs.ensure_directory_no_follow(path), f"创建目录失败：{path}"
    )
    created.extend(reversed(missing))
    return target


def _wrap_fs(action, message: str):
    """执行一次 `safe_fs` 调用，把 `SafeFilesystemError` 包装成 `PrepareError`。

    `SafeFilesystemError` 是 `RuntimeError` 子类而非 `OSError`（`safe_fs.py:11`），
    `except OSError` 兜不住它；`cli.main` 只捕 `ConfigError` 与本模块的 `PrepareError`，
    逃逸即打 traceback 而非干净退出。`OSError` 一并收下：`safe_fs` 的少数路径
    （`FileNotFoundError`、`FileExistsError`）刻意原样上抛。
    """
    try:
        return action()
    except safe_fs.SafeFilesystemError as exc:
        raise PrepareError(f"{message}（{exc}）") from exc
    except OSError as exc:
        raise PrepareError(f"{message}（{exc}）") from exc


def _remove_tree(path: Path) -> None:
    """删除本次运行自己创建的目录/文件；缺失即无操作。

    只用于**本次新建**的条目（scratch 工作目录、`YD_ROOT` 内 staging、已提交的本次终名、
    为提交而新建的父目录）——它们在执行前都不存在（四个终名由 `lexists` 守卫证实），
    故树内不可能有既有内容被误删。清理失败不掩盖原始失败：抛 `PrepareError`。
    """
    _wrap_fs(
        lambda: safe_fs.rmtree_no_follow(path, missing_ok=True),
        f"清理失败：{path}",
    )


def _copy_tree_publish(source: Path, destination: Path, created: list[Path]) -> None:
    """把 `source` 树按**发布权限新建条目**的方式复制到 `destination`。

    刻意不是 `shutil.copytree(copy2)`/`cp -a`：那会把计算节点的 uid/gid/mode 原样带进
    NFS（agent-ops §10「复制 scratch 文件不用 `cp -a` 把计算节点 uid/gid/模式带入 NFS；
    由控制器按发布权限创建」）。这里每个条目都是**新建**的：目录走
    `safe_fs.ensure_directory_no_follow`（显式 0o755），普通文件走
    `safe_fs.write_bytes_no_follow_exclusive`（新建，落地权限由本进程 umask 决定，不读
    源 mode）。属主/权限的进一步收紧归 #24/#25 的发布面。

    非普通文件（symlink/FIFO/设备）一律拒绝：`safe_fs.stat_no_follow` 对 symlink 直接
    抛，其余类型在此点名。
    """
    _ensure_directory(destination, created)
    names = _wrap_fs(
        lambda: safe_fs.list_directory_no_follow(source), f"读取目录失败：{source}"
    )
    for name in sorted(names):
        child = source / name
        info = _wrap_fs(
            lambda child=child: safe_fs.stat_no_follow(child), f"读取条目失败：{child}"
        )
        if stat.S_ISDIR(info.st_mode):
            _copy_tree_publish(child, destination / name, created)
        elif stat.S_ISREG(info.st_mode):
            payload = _wrap_fs(
                lambda child=child: safe_fs.read_bytes_no_follow(child),
                f"读取文件失败：{child}",
            )
            _wrap_fs(
                lambda name=name, payload=payload: (
                    safe_fs.write_bytes_no_follow_exclusive(destination / name, payload)
                ),
                f"写入 staging 失败：{destination / name}",
            )
        else:
            raise PrepareError(f"变体内出现非普通文件条目，拒绝发布：{child}")


# --- 产物校验 ----------------------------------------------------------------


def _validate_variant(source: str, variant_root: Path, config: Config) -> None:
    """逐变体的提交前校验；任一条不成立即 `PrepareError`，一个变体都不提交。"""
    if not variant_root.is_dir():
        raise PrepareError(
            f"builder 未在 {source} 的 variant_root 产出目录：{variant_root}"
        )

    names = set(
        _wrap_fs(
            lambda: safe_fs.list_directory_no_follow(variant_root),
            f"读取 {source} 变体目录失败：{variant_root}",
        )
    )
    unexpected = sorted(names - VARIANT_REQUIRED_ENTRIES)
    if unexpected:
        # `.tmp` 残留是最常见的一种，但判据是"恰为预期集合"而非"没有 .tmp"：按后缀
        # 挑剔等于给未来每一种残留形态留一个洞。
        raise PrepareError(
            f"{source} 变体目录含未预期条目，拒绝提交："
            + "、".join(unexpected)
            + f"（{variant_root}）"
        )
    missing = sorted(VARIANT_REQUIRED_ENTRIES - names)
    if missing:
        raise PrepareError(
            f"{source} 变体目录缺少必需条目："
            + "、".join(missing)
            + f"（{variant_root}）"
        )

    state_path = calibrated_state_path(variant_root)
    try:
        document = cfg_ic.parse(state_path)
    except ValueError as exc:
        # `cfg_ic.parse` 把 OSError/UnicodeDecodeError 都收敛成 ValueError，故这一条
        # 兜住全部解析侧失败；MUST NOT 让它逃出本模块。
        raise PrepareError(
            f"{source} 变体的率定末态不可解析：{state_path}（{exc}）"
        ) from exc

    if document.river is None:
        # `CfgIcDocument.river` 是 `Section | None`。把 `None` 当成"0 条 reach"会在
        # `reach_count == 0` 的配置下静默通过——缺 river 段是结构性缺陷，不是一个数量。
        raise PrepareError(
            f"{source} 变体的率定末态没有 river 段（不是 0 条河段，是缺段）：{state_path}"
        )
    if document.river.row_count != config.reach_count:
        raise PrepareError(
            f"{source} 变体的 reach 数与 `reach_count` 不符："
            f"期望 {config.reach_count}，实际 {document.river.row_count}（{state_path}）"
        )


# --- 编排 --------------------------------------------------------------------


def run_prepare(
    *,
    local: LocalConfig,
    config: Config,
    baseline_root: Path | str,
    builder: Builder = default_builder,
) -> PrepareReport:
    """执行一次 `prepare` 编排，严格按 fixture 钉死的八步顺序。

    1. **拒绝覆盖**：四个终名任一 `lexists` 即 `PrepareError`；此时 MUST NOT 创建
       scratch、MUST NOT 调 builder（`prepare` 不幂等、无 `--force`，compute-loop §6.1）；
    2. 在 `local.scratch_root` 下建本次运行专属工作目录（名字含 pid + 随机 token，
       避免并发/重跑互相覆写）；
    3. 对 `("gfs", "ifs")` 各建一个此前不存在的 `variant_root`，各调 `builder` 一次；
    4. 逐变体产物校验（目录存在、条目集合精确、率定末态可解析、river 段存在且行数等于
       `config.reach_count`）；
    5. 把校验通过的两棵变体树按发布权限复制进 `YD_ROOT` 内本次专属 staging；
    6. `geometry.write_viewer_geojson` 直接写进该 staging（**不经 scratch**，唯一落点）；
    7. 四个终名逐个同盘 rename 提交，顺序「两变体 → rivers → boundary」；
    8. 无论成败删除 scratch 工作目录与 staging；提交阶段失败时**同时**回滚本次已提交的
       终名与本次为提交新建的父目录，使 `YD_ROOT` 回到执行前的条目集合。
    """
    yd_root = Path(local.yd_root)
    scratch_root = Path(local.scratch_root)
    baseline = Path(baseline_root)

    # 步骤 1：相对性/互异性闸门（在任何写入之前）与拒绝覆盖检查。
    variants = variant_targets(local, config)
    viewer = viewer_targets(local)
    for label, target in list(variants.items()) + list(viewer.items()):
        if os.path.lexists(target):
            raise PrepareError(
                f"终名已存在，拒绝执行：{target}（{label}）；"
                "prepare 不覆盖既有产物，也不提供 --force（compute-loop §6.1）"
            )

    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    work_dir = scratch_root / f"{_SCRATCH_PREFIX}-{token}"
    staging_root = yd_root / f"{_STAGING_PREFIX}-{token}"
    created: list[Path] = []
    committed: list[Path] = []

    try:
        # 步骤 2–3：scratch 工作目录 + 逐 source 调 builder。
        _ensure_directory(work_dir, [])
        requests: dict[str, VariantBuildRequest] = {}
        for source in SOURCE_IDS:
            variant_root = work_dir / source
            if os.path.lexists(variant_root):  # pragma: no cover - token 保证不可达
                raise PrepareError(f"scratch 变体目录已存在：{variant_root}")
            _ensure_directory(variant_root, [])
            request = VariantBuildRequest(
                source_id=normalize_source_id(source),
                grid_id=getattr(config.nwm_canonical_grid_id, source),
                baseline_root=baseline,
                variant_root=variant_root,
            )
            requests[source] = request
            try:
                builder(request)
            except BuilderUnavailableError:
                # 归属信息就在这条异常里，重新包装会把退出码 3 降级成 1。
                raise
            except PrepareError:
                raise
            except Exception as exc:
                raise PrepareError(f"builder 构建 {source} 变体失败：{exc}") from exc

        # 步骤 4：逐变体产物校验（两个都过才进入搬运）。
        for source in SOURCE_IDS:
            _validate_variant(source, requests[source].variant_root, config)

        # 步骤 5：scratch -> `YD_ROOT` 内 staging（按发布权限新建条目）。
        _ensure_directory(staging_root, created)
        models_staging = staging_root / "models"
        viewer_staging = staging_root / "viewer"
        for source in SOURCE_IDS:
            # staging 内按 source 命名而非按终名叶名：两个终名允许同叶名不同父目录
            # （`a/yd` 与 `b/yd`），照终名命名会在 staging 里撞成一个。
            _copy_tree_publish(
                requests[source].variant_root, models_staging / source, created
            )

        # 步骤 6：GeoJSON 直接落 staging（唯一落点，不经 scratch）。
        _ensure_directory(viewer_staging, created)
        try:
            write_viewer_geojson(
                rivers_shp=baseline_rivers_shp(baseline),
                domain_shp=baseline_domain_shp(baseline),
                out_dir=viewer_staging,
            )
        except GeometryError as exc:
            raise PrepareError(f"viewer GeoJSON 生成失败：{exc}") from exc

        # 步骤 7：四个终名逐个同盘 rename 提交，顺序「两变体 → rivers → boundary」。
        plan: list[tuple[Path, str, Path]] = [
            (models_staging, source, variants[source]) for source in SOURCE_IDS
        ]
        plan += [
            (viewer_staging, VIEWER_GEOJSON_NAMES[key], viewer[key])
            for key in ("rivers", "boundary")
        ]
        for source_parent, source_name, target in plan:
            _ensure_directory(target.parent, created)
            _wrap_fs(
                lambda source_parent=source_parent, source_name=source_name, target=target: (
                    safe_fs.rename_entry_no_follow(
                        source_parent, source_name, target.parent, target.name
                    )
                ),
                f"提交失败：{source_parent / source_name} -> {target}",
            )
            committed.append(target)
    except BaseException:
        # 步骤 8（失败侧）：把本次新建的条目全部撤回，使 `YD_ROOT` 的条目集合与执行前
        # 相同。顺序自内向外：已提交终名 -> staging -> 本次新建的父目录（逆序）。
        # 已提交终名在执行前由 `lexists` 守卫证实不存在，故删除的只可能是本次产物。
        for target in reversed(committed):
            _remove_tree(target)
        _remove_tree(staging_root)
        for directory in reversed(created):
            _remove_tree(directory)
        raise
    finally:
        # 步骤 8（无条件）：scratch 工作目录与 staging 一并删除，成败都清。
        _remove_tree(work_dir)
        _remove_tree(staging_root)

    return PrepareReport(
        variants=dict(variants),
        rivers_geojson=viewer["rivers"],
        boundary_geojson=viewer["boundary"],
    )
