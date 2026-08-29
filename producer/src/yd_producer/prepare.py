"""`prepare` 编排：从外部基线包生成两个 direct-grid 变体与两份 viewer GeoJSON。

权威：compute-loop §6.1、products-contract §2/§6、spec `prepare-variants`、
tasks.md「Issue #20 fixture（任务 10.3）」。

**契约标注约定**（本模块与 `cli` 通用）：凡以散文声明的行为选择（带 `MUST` / `MUST NOT` /
`刻意` / `钉死` / `不做…兜底`）都就地标注它的判别性证据——`（pinned: <test id>）` 指出把该
选择改回去时会变红的用例；确实无法判别的标 `（等价变异，不可判别：<理由>）`；本阶段不声明
的标 `（归 M4/<issue>，本阶段不声明）`。审查因此是一次 grep，而不是一次全套变异扫描。

**总不变量（全有或全无）**：本模块对 `YD_ROOT` 的效果要么是「四个终名（两个变体目录 +
两份 GeoJSON）全部由本次运行新建」，要么是「`YD_ROOT` 回到执行前的条目集合，既有内容
逐字节不变」。任何既有条目 MUST NOT 被覆盖或删除；无论成败，scratch 工作目录与
`YD_ROOT` 内 staging 位置都在返回前删除（pinned:
test_success_commits_exactly_four_targets_and_leaves_no_residue、
test_first_commit_failure_leaves_no_new_entries、
test_late_commit_failure_rolls_back_already_committed_targets）。唯一已接受的残留是四个
终名 rename 之间进程被 SIGKILL（或 NFS `ESTALE`）的窗口——POSIX 没有跨目录事务，无法
消解；提交顺序钉死为「两变体 → rivers → boundary」（pinned:
test_every_commit_renames_within_yd_root_on_one_device）是 best-effort 的排序偏好，
**不是**对 viewer 的就绪保证（products-contract §2/§6 没有为 `input/viewer/` 定义就绪
标记，本模块也不发明一个；就绪标记与崩溃后的人工恢复程序路由为 follow-up issue #78——
非行为声明，归 #78，本阶段不声明）。

**清理/回滚不变量（I1）**：任何一步清理或回滚 MUST NOT 取消其余步骤，也 MUST NOT 替换、
掩盖或降级正在传播的异常，更 MUST NOT 把一次已完成的提交报成失败（pinned:
test_one_failing_rollback_step_does_not_cancel_the_others、
test_scratch_cleanup_failure_does_not_gate_the_staging_cleanup）。故清理不是裸序列：
每步各自独立执行（`_run_cleanup_steps`），失败被**收集**——失败路径上作为 `add_note`
附到原始异常上（`BuilderUnavailableError` 因此仍是 `BuilderUnavailableError`，退出码 `3`
不被降级成 `1`；pinned: test_builder_unavailable_survives_a_cleanup_failure），成功路径上
作为 `PrepareReport.cleanup_warnings` 返回（已提交就是已提交，清理残留不改变这个事实；
pinned: test_success_survives_a_staging_cleanup_failure）。两条证据面都由 `cli` 渲染到
stderr 且都不改退出码（spec `cli-config`「prepare 的清理告警与残留证据 MUST 到达运维」；
pinned: test_cleanup_failure_text_reaches_stderr_on_the_failure_path、
test_cleanup_note_reaches_stderr_on_the_exit_one_path、
test_success_path_cleanup_warnings_reach_stderr_without_changing_the_exit_code）。

**本次条目登记不变量（I2）**：`YD_ROOT` 内每一个本次运行创建的条目，MUST 在它可能落盘
**之前**就被登记为本次条目（`_ensure_directory` 先 `created.extend` 再建目录——
`safe_fs.ensure_directory_no_follow` 逐层创建且没有 unwind，登记在后会漏掉半条链；
pinned: test_mid_chain_directory_creation_failure_leaves_no_new_entries）；
回滚只删本次创建的条目，且**父目录一律非递归**（`_remove_created_directory` 走 `rmdir`
语义；pinned: test_rollback_never_recursively_deletes_a_shared_parent）：单次运行下这些
目录在回滚时可证为空，行为不变；而并发写入者落进来的内容会让 `rmdir` 响亮地失败，而不是
被静默递归删掉。

**同一路径拼写不变量（I3）**：拒绝覆盖守卫 `os.path.lexists` 看的路径、`geometry` 写的
路径、`safe_fs` 操作的路径 MUST 是同一个文件系统对象。`safe_fs._expand_path` 会
`expanduser()` 而另外两者不会，故 `yd_root = "~/yd"` 会让守卫看 `./~/yd` 而删除落在真实
`$HOME/yd`。闸门在 `run_prepare` 入口（步骤 1 之前）：`local.yd_root` 与
`local.scratch_root` MUST 是绝对路径（这一条同时拒掉 `~` 与任何相对拼写）且 MUST 是**已
存在**的目录（`safe_fs.verify_directory_no_follow` 顺带拒掉 symlink 组件）。pinned:
test_non_absolute_run_roots_are_refused_before_any_builder_call、
test_tilde_run_root_never_touches_the_real_home、
test_missing_run_roots_are_refused_before_any_builder_call、
test_symlinked_run_root_is_refused。装载器那边不加校验：`specs/cli-config/spec.md` 把它
钉死为只做存在性与类型检查（该句是对装载层规范的转述，不是本模块的行为选择）。

**为什么不是「scratch 目录直接 rename 到 `YD_ROOT`」**：生产上 `yd_root` 在 NFS
（`/ghdc/data/yd`，agent-ops §4.1）而 `scratch_root` 在本地盘（`/scratch/.../yd-loop/`，
agent-ops §4.2）——两棵真不同文件系统的树，而 `safe_fs.rename_entry_no_follow` 明写
`EXDEV` 是硬错误、**刻意没有** fallback copy 路径（`store/safe_fs.py:630-631`）。直接
rename 会在本地测试（两根同在 `tmp_path`）全绿而在现场必然失败。故搬运分两段：scratch
-> `YD_ROOT` 内本次专属 staging（按发布权限**新建**条目，不继承计算节点 uid/gid/mode，
agent-ops §10），再由 staging 同盘 rename 到终名。与控制器发布面同构（agent-ops §8.4）。
pinned: test_commit_survives_a_filesystem_that_refuses_cross_device_rename、
test_every_commit_renames_within_yd_root_on_one_device、
test_published_entries_do_not_inherit_scratch_modes。

**异常契约**：本模块对外只有 `PrepareError` 及其子类 `BuilderUnavailableError`。三处
外来异常一律包装并保留 `__cause__`——`state.cfg_ic.parse` 的 `ValueError`、
`geometry.*` 的 `GeometryError`、`store.safe_fs.*` 的 `SafeFilesystemError`。第三处最易
漏：`SafeFilesystemError` 是 **`RuntimeError` 子类而非 `OSError`**
（`store/safe_fs.py:11`），`except OSError` 兜不住它（pinned:
test_unparsable_calibrated_state_refuses_commit、test_geometry_failure_rolls_back_validated_variants、
test_missing_run_roots_are_refused_before_any_builder_call）。注入 builder 抛出的任何异常
同样包装（`BuilderUnavailableError` 除外——它必须原样上浮，`cli` 靠它区分退出码 `3`；
pinned: test_prepare_rejection_and_unimplemented_binding_use_different_exit_codes）。

**文件系统原语**：一律复用 `store.safe_fs`，本模块不另写一套。**恰有两处豁免**，两处的
理由同源——`safe_fs` 的公共面确无对应原语，而扩它属 #24/#25 发布面的归属：

1. `_copy_tree_publish`（无 copy 原语）——树复制只落在本模块内，且仍由 `safe_fs` 的
   no-follow 原语逐条构成；
2. `_remove_created_directory`（无非递归删目录原语）——父目录的撤回 MUST 是 `rmdir`
   语义而非 `rmtree`（见下 I2；pinned:
   test_rollback_never_recursively_deletes_a_shared_parent），目录本身仍经
   `safe_fs.open_directory_no_follow` 逐层 no-follow 打开，`os.rmdir` 只在那个 fd 上按
   条目名执行。

「恰有两处豁免」这一条本身是代码组织约束、不是行为选择（等价变异，不可判别：多写一处
豁免不改变任何可观测行为）。

**合成约定**：基线包内部布局（`BASELINE_*`）与变体内文件名（`VARIANT_*`）是本 issue 的
fixture 定义的合成约定，以模块常量暴露给 11.1 消费；真实外部基线模型包的现场布局与读取
归 M4（tasks.md 组 10）——现场布局归 M4，本阶段不声明。
"""

from __future__ import annotations

import os
import stat
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
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
#: pinned: test_builder_called_once_per_source_with_distinct_inputs、
#: test_every_commit_renames_within_yd_root_on_one_device
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
#: pinned: test_scratch_residue_refuses_commit、test_missing_variant_entry_refuses_commit
VARIANT_REQUIRED_ENTRIES = frozenset(
    {VARIANT_CALIBRATED_STATE_NAME, VARIANT_HYDRO_PARAM_NAME, VARIANT_BINDING_NAME}
)

#: products-contract §2 的字面量落点（非配置驱动）
VIEWER_GEOJSON_NAMES = {
    "rivers": "rivers.geojson",
    "boundary": "boundary.geojson",
}
_VIEWER_RELATIVE_DIR = Path("input") / "viewer"

#: 变体终名 MUST NOT 落入的 `YD_ROOT` 相对子树（词法闸门，见 `variant_targets`）：
#: `input/viewer/` 是 products-contract §2 钉死的「恰两个文件」目录，`output/` 是同一类
#: 的 viewer 读取面（§2/§7）。变体终名落进去只是普通配置笔误，但既有的两两互异 /
#: 互为祖先闸门都拦不住它——变体目录是两个 GeoJSON 的**兄弟**。
#: pinned: test_variant_target_on_the_viewer_read_surface_is_refused、
#: test_output_subtree_variant_target_is_refused
_VARIANT_FORBIDDEN_RELATIVE_DIRS = (
    _VIEWER_RELATIVE_DIR,
    Path("output"),
)

#: 本次运行专属 staging 在 `YD_ROOT` 下的目录名前缀（**不**落在 `input/viewer/` 之内：
#: products-contract §2 只允许该目录存在两个文件，把 staging 建在里面等于让 viewer 看见
#: 中间态）。pinned: test_every_commit_renames_within_yd_root_on_one_device、
#: test_success_survives_a_staging_cleanup_failure（把前缀挪进 `input/viewer/` 两条都变红）
_STAGING_PREFIX = ".yd-prepare-staging"
_SCRATCH_PREFIX = "prepare"

#: 发布归属：真实 builder 绑定所需的 NWM 侧 driver 归此阶段（见 `default_builder`）。
#: 消息必须**指名**归属（不是自指地断言常量出现过）；pinned:
#: test_production_binding_names_its_owner_with_a_literal（断言字面量 "归属 M4"）
BUILDER_OWNER = "M4（node-22 真计算，docs/design.md §10）"


class PrepareError(Exception):
    """`prepare` 编排的公开异常**基类**；`cli.main` 捕获后走退出码 `1`。"""


class BuilderUnavailableError(PrepareError):
    """生产 builder 绑定尚未可用；`cli.main` 先于基类捕获它并走退出码 `3`。

    与基类**不得合并**：把"配置/产物不合法"（改配置能修）与"这条路还没通"（等 M4）
    报成同一个码，运维无从判断该做哪一件（pinned:
    test_prepare_rejection_and_unimplemented_binding_use_different_exit_codes、
    test_builder_unavailable_is_a_prepare_error_subclass）。
    """


@dataclass(frozen=True, kw_only=True)
class VariantBuildRequest:
    """一次 builder 调用的全部入参。

    字段形态是**消费上游契约、不重新协商**：`source_id` 与 `grid_id` 逐字对应 pin
    `NWM@8ae9b8f2 workers/mapping_builder/cli.py:601-602` 的 `build_direct_grid_variant`
    同名关键字参数。`source_id` 取值走 `raw.source_identity.normalize_source_id` 的
    `"gfs"`/`"ifs"`；`grid_id` 取自 `config.nwm_canonical_grid_id`（pinned:
    test_grid_ids_follow_config_not_a_hardcoded_literal、
    test_builder_called_once_per_source_with_distinct_inputs）。「字段名逐字对应 pin」这一
    半是对上游取证的转述（等价变异，不可判别：本仓运行时 MUST NOT import NWM，无从对拍）。
    """

    source_id: str
    grid_id: str
    baseline_root: Path
    variant_root: Path


@dataclass(frozen=True, kw_only=True)
class PrepareReport:
    """一次成功编排的产出终名。全部路径都已提交到 `YD_ROOT`。

    `cleanup_warnings` 是**成功之后**的清理失败（scratch 工作目录或 `YD_ROOT` 内 staging
    没删掉）。它 MUST NOT 被升格成异常：四个终名都已提交，把它抛出去会让运维看到退出码
    `1`，而重跑又被拒绝覆盖守卫挡住（`prepare` 无 `--force`），一次成功的运行就此变成
    死局。故这里是返回值上的证据面，不是失败信号；空元组表示清理干净（pinned:
    test_success_survives_a_staging_cleanup_failure、
    test_success_reports_no_cleanup_warnings_when_cleanup_is_clean）。
    """

    variants: Mapping[str, Path]
    rivers_geojson: Path
    boundary_geojson: Path
    cleanup_warnings: tuple[str, ...] = field(default=())


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
    """两份 viewer GeoJSON 的终名：products-contract §2 的字面量落点，非配置驱动。

    pinned: test_viewer_targets_are_the_contract_literals。
    """
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

    `..` 的判据是**任一 `os.pardir` 组件**，而非"规范化后是否逃出 `yd_root`"：
    `specs/prepare-variants/spec.md`「拒绝覆盖已有产物」写的是「绝对路径或含 `..` 的路径
    MUST 拒绝执行」，而只查规范化结果会放行 `input/../input/models/yd_gfs` 这类词法上
    含 `..`、折叠后又落回根内的值。`variants.*` 没有任何需要 `..` 的正当理由，收紧到
    组件级判据让规范文本与实现同时为真，且仍是纯词法、仍在任何写入之前。

    pinned: test_absolute_variant_path_is_refused、test_escaping_variant_path_is_refused、
    test_any_pardir_component_in_a_variant_path_is_refused（第三条含"折叠后落回根内"那一
    例，把判据换成规范化后判定即变红）。
    """
    if not value:
        raise PrepareError(f"配置项 `{field}` 不得为空")
    candidate = Path(value)
    if candidate.is_absolute():
        raise PrepareError(
            f"配置项 `{field}` 必须是相对 `yd_root` 的路径，不得为绝对路径：{value}"
        )
    if os.pardir in candidate.parts:
        raise PrepareError(
            f"配置项 `{field}` 不得含 `..` 组件（规范化后是否仍落在 `yd_root` 内都一样"
            f"拒绝）：{value}"
        )
    normalized = os.path.normpath(value)
    parts = Path(normalized).parts
    if normalized == os.curdir or not parts:
        raise PrepareError(f"配置项 `{field}` 不得指向 `yd_root` 自身：{value}")
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
    2. **变体终名 MUST NOT 落在 `input/viewer/` 或 `output/` 之内**（含等于该目录本身）。
       这两棵子树是 viewer 的读取面（products-contract §2/§7），而 §2 明写
       `input/viewer/` 恰只有两个文件。`variants.gfs = "input/viewer/yd_gfs"` 是两个
       GeoJSON 终名的**兄弟**：两两互异过、互为祖先过、两个 `lexists` 也过，于是变体的
       `yd.cfg.ic`/`yd.para`/`yd.binding` 直接落到 viewer 的读取面上；
    3. **四个终名两两互异、且任一 MUST NOT 是另一终名的祖先**。把 `variants.gfs` 与
       `variants.ifs` 抄成同一值是普通的配置笔误，而装载器只校验存在性与类型、不拦；
       两个 `lexists` 守卫也全过（两者都不存在），于是 `gfs` 提交成功、第二次 rename 撞
       `ENOTEMPTY`，`YD_ROOT` 停在"只有一个变体"的半提交态——直接违反总不变量。互为祖先
       同理：两个 `lexists` 与产物校验都发现不了，而提交后 `ifs` 变体会躺在已提交的
       `gfs` 变体**目录内部**。

    pinned：单一来源 test_variant_targets_is_the_single_source_of_final_names、
    test_overwrite_guard_follows_config_not_the_default_literal、
    test_commit_lands_where_config_points_not_at_the_default_literal；闸门 2
    test_variant_target_on_the_viewer_read_surface_is_refused、
    test_output_subtree_variant_target_is_refused；闸门 3
    test_identical_variant_paths_are_refused_before_any_write、
    test_nested_variant_paths_are_refused_before_any_write、
    test_variant_ancestor_of_viewer_directory_is_refused。
    """
    yd_root = Path(local.yd_root)
    targets = {
        source: _resolve_variant_relative(
            f"variants.{source}", getattr(config.variants, source), yd_root
        )
        for source in SOURCE_IDS
    }

    for source, path in targets.items():
        for relative in _VARIANT_FORBIDDEN_RELATIVE_DIRS:
            forbidden = yd_root / relative
            if path == forbidden or _is_ancestor(forbidden, path):
                raise PrepareError(
                    f"配置项 `variants.{source}` 不得落在 `{relative.as_posix()}` 之内"
                    f"（那是 viewer 的读取面，products-contract §2/§7）：{path}"
                )

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
    而真因是"这条路还没通"）。pinned:
    test_production_builder_binding_fails_before_any_subprocess（`subprocess.run`/`Popen`
    与 `nwm.invoke_mapping_builder` 三处探针的调用列表必须为空）、
    test_production_binding_names_its_owner_with_a_literal。
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


def _ensure_directory(
    path: Path, created: list[Path], *, lower_bound: Path | None = None
) -> Path:
    """创建目录并把**本次新建**的每一层登记进 `created`（供提交失败时回滚）。

    先按存在性逐层向上探一遍再交给 `safe_fs.ensure_directory_no_follow`：后者一次建齐
    全部缺失层但不告诉调用方建了哪几层，而总不变量要求提交失败时 `YD_ROOT` 回到执行前的
    条目集合——「为提交而新建的父目录」属本次条目，必须能被指名删除。

    **登记先于创建**（I2）：`ensure_directory_no_follow` 逐层 `os.mkdir` 且没有 unwind，
    链中途失败（`EDQUOT`/`EACCES`/`ENOSPC`）会留下已建好的前几层。登记在创建之后，那几层
    就永远不在 `created` 里，回滚够不着它们——`YD_ROOT` 回不到执行前的条目集合。故先
    `created.extend` 再建：多登记一个"其实没建成"的条目是无害的（回滚对不存在的条目是
    无操作），少登记一个已建成的条目则直接破坏总不变量（pinned:
    test_mid_chain_directory_creation_failure_leaves_no_new_entries）。

    `lower_bound` 给向上探测**封底**：没有下界时，`yd_root` 打错一个字（或 NFS 瞬时未
    挂载）会让循环一路探到 `/`，把整条不存在的祖先链当成"本次新建"，随后
    `ensure_directory_no_follow` 真把整个影子根造出来并**返回成功**，产物提交进一棵
    viewer 永远读不到的树（agent-ops §4.1）。下界不存在即硬失败，不代造（pinned:
    test_probe_loop_refuses_to_rebuild_a_vanished_run_root）。
    """
    missing: list[Path] = []
    probe = path
    while not os.path.lexists(probe):
        missing.append(probe)
        if lower_bound is not None and probe == lower_bound:
            raise PrepareError(
                f"运行根在本次运行途中消失，拒绝重建：{lower_bound}（目标 {path}）"
            )
        if probe.parent == probe:
            break
        probe = probe.parent
    created.extend(reversed(missing))
    return _wrap_fs(
        lambda: safe_fs.ensure_directory_no_follow(path), f"创建目录失败：{path}"
    )


def _wrap_fs(action, message: str):
    """执行一次 `safe_fs` 调用，把 `SafeFilesystemError` 包装成 `PrepareError`。

    `SafeFilesystemError` 是 `RuntimeError` 子类而非 `OSError`（`safe_fs.py:11`），
    `except OSError` 兜不住它；`cli.main` 只捕 `ConfigError` 与本模块的 `PrepareError`，
    逃逸即打 traceback 而非干净退出。`OSError` 一并收下：`safe_fs` 的少数路径
    （`FileNotFoundError`、`FileExistsError`）刻意原样上抛（pinned:
    test_first_commit_failure_leaves_no_new_entries 钉 `SafeFilesystemError` 那一支、
    test_missing_run_roots_are_refused_before_any_builder_call 钉 `OSError` 那一支——删掉
    `except OSError` 后者变红）。
    """
    try:
        return action()
    except safe_fs.SafeFilesystemError as exc:
        raise PrepareError(f"{message}（{exc}）") from exc
    except OSError as exc:
        raise PrepareError(f"{message}（{exc}）") from exc


def _remove_tree(path: Path) -> None:
    """递归删除本次运行创建的 `YD_ROOT` 侧条目（staging、已提交的本次终名）；缺失即无操作。

    只用于**本次新建**的条目——它们在执行前都不存在（四个终名由 `lexists` 守卫证实），
    故树内不可能有既有内容被误删。这里刻意保留 `rmtree_no_follow` 的**拒绝 symlink**
    策略：`YD_ROOT` 内的这些树由本模块逐条新建，出现 symlink 是**篡改证据**，不该被
    默默清掉。builder 产出的 scratch 树是相反的情形，见 `_remove_scratch_tree`（两条策略
    的分工 pinned: test_builder_symlink_residue_is_refused_and_scratch_is_fully_removed）。

    失败抛 `PrepareError`，由 `_run_cleanup_steps` 收集——MUST NOT 直接从清理位置逃逸
    （那会取消其余清理步骤、并替换掉正在传播的原始异常，见模块头 I1；pinned:
    test_one_failing_rollback_step_does_not_cancel_the_others）。
    """
    _wrap_fs(
        lambda: safe_fs.rmtree_no_follow(path, missing_ok=True),
        f"清理失败：{path}",
    )


def _remove_scratch_tree(path: Path) -> None:
    """递归删除 scratch 工作目录；缺失即无操作。**允许** symlink 条目。

    走 `safe_fs.remove_tree_allow_symlinks` 而非 `rmtree_no_follow`：该目录的内容由
    **builder** 写入，按构造是不可信的（`run_prepare` 的主 seam 就是一个可编排 builder），
    builder 在 `variant_root` 里留一条 symlink 会让 `rmtree_no_follow` 拒绝整棵树
    （`safe_fs.py` 的 `Refusing to remove symlink tree entry`），于是 scratch 树被永久搁浅，
    而"变体含未预期条目"这条真因还被清理错误盖住。该原语的 docstring 把自己的适用范围
    钉死为正是这一类"内容按构造不可信的残留树"，且从不跟随 symlink（unlink 链接本身）。
    pinned: test_builder_symlink_residue_is_refused_and_scratch_is_fully_removed（改用
    `rmtree_no_follow` 即变红：scratch 树被搁浅）。
    """
    _wrap_fs(
        lambda: safe_fs.remove_tree_allow_symlinks(
            path.parent, path.name, missing_ok=True
        ),
        f"清理失败：{path}",
    )


def _remove_created_directory(path: Path) -> None:
    """**非递归**撤回本次为提交而新建的一层目录；缺失或父目录已消失即无操作。

    刻意不是 `rmtree`（I2）：`created` 里的父目录（`input/`、`input/models/`）是与别的
    写入者共享的命名空间。递归删除会连带删掉另一个写入者在本次运行期间落进来的内容——
    `prepare` 全程不持锁，四个 `lexists` 守卫到提交循环之间的窗口有整个 builder 运行时长。
    单次运行下这些目录在回滚时可证为空（本次产物已先被撤回），故 `rmdir` 语义与递归删除
    行为完全一致；一旦不空，`ENOTEMPTY` 是一次**响亮的失败**（收进清理失败附到原始异常
    上），而不是一次静默的数据删除（pinned:
    test_rollback_never_recursively_deletes_a_shared_parent）。

    「父目录已消失即无操作」那一支（`except FileNotFoundError: return`）**未钉**，归
    round-2 记录项：`safe_fs.open_directory_no_follow` 的 docstring 并不承诺缺失父目录一定
    以裸 `FileNotFoundError` 形态出现，在该形态被上游钉死之前，为它写用例等于把当前实现
    细节当契约。本阶段不声明。

    `os.rmdir` 走父目录 fd，父目录本身用 `safe_fs.open_directory_no_follow` 打开——路径
    逐层 no-follow，与本模块其余文件系统操作同纪律。
    """
    try:
        parent_fd = safe_fs.open_directory_no_follow(path.parent)
    except FileNotFoundError:
        return
    except (safe_fs.SafeFilesystemError, OSError) as exc:
        raise PrepareError(f"清理失败：{path}（{exc}）") from exc
    try:
        os.rmdir(path.name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PrepareError(
            f"清理失败：{path} 不是本次运行独占的空目录，拒绝递归删除（{exc}）"
        ) from exc
    finally:
        os.close(parent_fd)


def _run_cleanup_steps(steps: Iterable[Callable[[], None]]) -> list[str]:
    """逐步执行清理，**每一步彼此独立**，返回失败描述列表（I1）。

    裸序列的清理有两个致命形态：第一步抛出会取消后面所有步骤（已提交的终名回滚不掉、
    本次新建的父目录被搁浅），而抛出的清理异常还会替换掉正在传播的原始异常
    （`BuilderUnavailableError` 被降级成 `PrepareError`，`cli` 的退出码 `3` 变成 `1`）。

    收 `Exception` 而非只收 `PrepareError`：清理原语内部任何未被翻译的 `OSError`
    （`os.close`/`os.rmdir`）同样会取消后续步骤，那正是本函数要消除的形态。
    `BaseException`（`KeyboardInterrupt`/`SystemExit`）不收——那是进程要停，属模块头
    已声明的"被杀窗口"，不该被清理循环吞掉。

    三条边界各自钉死（round-2 cand-r2-B1）：每步互不取消 pinned:
    test_one_failing_rollback_step_does_not_cancel_the_others；不收 `BaseException`
    pinned: test_keyboard_interrupt_in_a_cleanup_step_is_not_swallowed；收的是 `Exception`
    而非 `PrepareError` pinned:
    test_untranslated_oserror_in_a_cleanup_step_does_not_cancel_the_rest。
    """
    failures: list[str] = []
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - 见 docstring：每步互不取消
            failures.append(str(exc))
    return failures


def _copy_tree_publish(
    source: Path, destination: Path, created: list[Path], *, lower_bound: Path
) -> None:
    """把 `source` 树按**发布权限新建条目**的方式复制到 `destination`。

    刻意不是 `shutil.copytree(copy2)`/`cp -a`：那会把计算节点的 uid/gid/mode 原样带进
    NFS（agent-ops §10「复制 scratch 文件不用 `cp -a` 把计算节点 uid/gid/模式带入 NFS；
    由控制器按发布权限创建」）。这里每个条目都是**新建**的：目录走
    `safe_fs.ensure_directory_no_follow`（显式 0o755），普通文件走
    `safe_fs.write_bytes_no_follow_exclusive`（新建，落地权限由本进程 umask 决定，不读
    源 mode）。属主/权限的进一步收紧归 #24/#25 的发布面。pinned:
    test_published_entries_do_not_inherit_scratch_modes。

    非普通文件（symlink/FIFO/设备）一律拒绝：`safe_fs.stat_no_follow` 对 symlink 直接
    抛（pinned: test_builder_symlink_residue_is_refused_and_scratch_is_fully_removed），
    其余类型在此点名——FIFO/socket/设备那一支**未钉**：只有当 builder 恰以三个必需条目名
    之一产出这类文件时才可达，round-1 已裁定在本阶段真实输入域之外（归 M4，本阶段不
    声明）。
    """
    _ensure_directory(destination, created, lower_bound=lower_bound)
    names = _wrap_fs(
        lambda: safe_fs.list_directory_no_follow(source), f"读取目录失败：{source}"
    )
    for name in sorted(names):
        child = source / name
        info = _wrap_fs(
            lambda child=child: safe_fs.stat_no_follow(child), f"读取条目失败：{child}"
        )
        if stat.S_ISDIR(info.st_mode):
            _copy_tree_publish(
                child, destination / name, created, lower_bound=lower_bound
            )
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
    """逐变体的提交前校验；任一条不成立即 `PrepareError`，一个变体都不提交。

    「一个都不提交」pinned: test_partial_success_commits_nothing（`ifs` 失败时 `gfs` 也不
    落地）、test_reach_count_mismatch_refuses_commit、test_missing_variant_root_refuses_commit。
    """
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
        # pinned: test_builder_symlink_residue_is_refused_and_scratch_is_fully_removed
        # （残留条目不带 `.tmp` 后缀，把判据窄化成按后缀挑剔即变红）、
        # test_scratch_residue_refuses_commit、test_missing_variant_entry_refuses_commit
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
        # pinned: test_unparsable_calibrated_state_refuses_commit
        raise PrepareError(
            f"{source} 变体的率定末态不可解析：{state_path}（{exc}）"
        ) from exc

    if document.river is None:
        # `CfgIcDocument.river` 是 `Section | None`。把 `None` 当成"0 条 reach"会在
        # `reach_count == 0` 的配置下静默通过——缺 river 段是结构性缺陷，不是一个数量。
        # pinned: test_missing_river_section_is_not_treated_as_zero_reaches
        # （`reach_count == 0` 的判别性用例）、test_missing_river_section_refuses_commit
        raise PrepareError(
            f"{source} 变体的率定末态没有 river 段（不是 0 条河段，是缺段）：{state_path}"
        )
    if document.river.row_count != config.reach_count:
        raise PrepareError(
            f"{source} 变体的 reach 数与 `reach_count` 不符："
            f"期望 {config.reach_count}，实际 {document.river.row_count}（{state_path}）"
        )


# --- 编排 --------------------------------------------------------------------


def _verify_root(field_name: str, value: Path | str) -> Path:
    """运行根的入口闸门（I3）：MUST 为绝对路径、MUST 为已存在的目录。

    绝对性这一条同时关掉两个洞：`~/yd` 这类拼写会让守卫的 `os.path.lexists` 与
    `geometry` 看字面量 `~`，而 `safe_fs` 的每个原语都先 `expanduser()`——同一个配置值
    在三个消费者眼里是两个不同的文件系统对象，回滚会去删真实 `$HOME/yd` 里的既有内容；
    普通相对路径同理（`safe_fs` 拿 `Path.cwd()` 锚定，另外两者不锚）。

    "已存在"这一条关掉影子根：`yd_root` 打错一个字（或 NFS 未挂载）时，若默许创建，
    整棵运行根会被凭空造出来、运行**返回成功**、产物躺在 viewer 永远读不到的地方
    （agent-ops §4.1/§4.2）。`safe_fs.verify_directory_no_follow` 逐层 no-follow 打开，
    顺带拒掉任何 symlink 组件，并返回它自己解析后的路径——本模块之后一律用这个返回值，
    保证三个消费者拿的是同一个拼写。

    校验落在这里而不是装载器：`specs/cli-config/spec.md` 把 `local.toml` 的装载钉死为
    只做存在性与类型检查，往 `config.py` 里加文件系统探测会越过那条规范。

    pinned: test_non_absolute_run_roots_are_refused_before_any_builder_call、
    test_tilde_run_root_never_touches_the_real_home（真 `$HOME` 不被触碰）、
    test_missing_run_roots_are_refused_before_any_builder_call、
    test_symlinked_run_root_is_refused。
    """
    path = Path(value)
    if not path.is_absolute():
        raise PrepareError(
            f"配置项 `{field_name}` 必须是绝对路径（`~` 与相对路径一律拒绝——"
            f"守卫、`geometry` 与 `safe_fs` 对它们的展开方式不同）：{value}"
        )
    return _wrap_fs(
        lambda: safe_fs.verify_directory_no_follow(path),
        f"配置项 `{field_name}` 必须是已存在的目录，prepare 不代建运行根：{path}",
    )


def _refuse_existing_targets(
    labelled_targets: Sequence[tuple[str, Path]], *, phase: str
) -> None:
    """四个终名的拒绝覆盖探测；任一 `lexists` 即 `PrepareError`。

    探两次（步骤 1 与提交循环之前）是刻意的 TOCTOU **窄化**（不是消解）：步骤 1 的探测
    与提交之间隔着两次 builder 调用的全部时长，而 `safe_fs.rename_entry_no_follow` 发的
    是裸 `renameat`（没有 `RENAME_NOREPLACE`），期间落到终名上的既有文件会被**静默替换**
    且运行报成功。第二次探测把窗口从"整个构建时长"压到"探测到 rename 之间的微秒级"。
    POSIX 没有可移植的 `RENAME_NOREPLACE`，剩下的窗口不可消解，本模块也不假装消解。

    pinned: test_target_appearing_after_the_first_guard_is_refused_before_commit（删掉第二
    次探测即变红）、test_existing_variant_directory_is_refused、
    test_existing_viewer_geojson_is_refused_byte_for_byte。
    """
    for label, target in labelled_targets:
        if os.path.lexists(target):
            raise PrepareError(
                f"终名已存在（{phase}），拒绝执行：{target}（{label}）；"
                "prepare 不覆盖既有产物，也不提供 --force（compute-loop §6.1）"
            )


def run_prepare(
    *,
    local: LocalConfig,
    config: Config,
    baseline_root: Path | str,
    builder: Builder = default_builder,
) -> PrepareReport:
    """执行一次 `prepare` 编排，严格按 fixture 钉死的八步顺序。

    0. **运行根预检**：`local.yd_root` 与 `local.scratch_root` MUST 是绝对路径且是已存在
       的目录（见 `_verify_root`；I3）；
    1. **拒绝覆盖**：四个终名任一 `lexists` 即 `PrepareError`；此时 MUST NOT 创建
       scratch、MUST NOT 调 builder（`prepare` 不幂等、无 `--force`，compute-loop §6.1；
       pinned: test_existing_variant_directory_is_refused、
       test_existing_viewer_geojson_is_refused_byte_for_byte——两者都断言 builder 零调用）；
    2. 在 `local.scratch_root` 下建本次运行专属工作目录（名字含 pid + 随机 token，
       避免并发/重跑互相覆写；pinned:
       test_two_runs_get_distinct_scratch_and_staging_names——常量 token 变异即变红）；
    3. 对 `("gfs", "ifs")` 各建一个此前不存在的 `variant_root`，各调 `builder` 一次；
    4. 逐变体产物校验（目录存在、条目集合精确、率定末态可解析、river 段存在且行数等于
       `config.reach_count`）；
    5. 把校验通过的两棵变体树按发布权限复制进 `YD_ROOT` 内本次专属 staging；
    6. `geometry.write_viewer_geojson` 直接写进该 staging（**不经 scratch**，唯一落点）；
    7. 四个终名**再探一次**拒绝覆盖（TOCTOU 窄化，见 `_refuse_existing_targets`），随后
       逐个同盘 rename 提交，顺序「两变体 → rivers → boundary」；
    8. 无论成败删除 `YD_ROOT` 内 staging 与 scratch 工作目录；提交阶段失败时**同时**回滚
       本次已提交的终名与本次为提交新建的父目录，使 `YD_ROOT` 回到执行前的条目集合。

    步骤 8 不用 `finally`（I1）。`finally` 里抛出的清理失败会在**成功路径**上抢在
    `return PrepareReport(...)` 之前逃逸，把一次四个终名全部提交完成的运行报成失败，而
    重跑又被拒绝覆盖守卫挡住；在**失败路径**上它则替换掉正在传播的原始异常，
    `BuilderUnavailableError` 被降级成 `PrepareError`，`cli` 的退出码 `3` 变成 `1`。故
    分成 `except` / `else` 两条显式路径：清理步骤各自独立执行、失败被收集，失败路径上
    以 `add_note` 附到原始异常（`raise` 裸重抛，异常对象与 traceback 都不动），成功路径上
    进 `PrepareReport.cleanup_warnings`（pinned:
    test_success_survives_a_staging_cleanup_failure 钉成功路径不被清理失败翻成失败、
    test_builder_unavailable_survives_a_cleanup_failure 钉失败路径异常类不被降级、
    test_keyboard_interrupt_from_the_builder_still_rolls_back 钉这里收的是 `BaseException`
    而非 `Exception`）。清理顺序也钉死为「先 `YD_ROOT` 内 staging、后本地 scratch」：前者
    承载不变量（留在 `YD_ROOT` 里就是 viewer 能看见的中间态），后者只是一次性本地垃圾，
    不该反过来卡住前者（等价变异，不可判别：两步互不取消，交换次序不改变任何可观测结果；
    round-2 已裁定，另见 test_scratch_cleanup_failure_does_not_gate_the_staging_cleanup 钉
    住"不互相卡住"这一半）。
    """
    # 步骤 0：运行根预检（在任何路径拼接、任何写入、任何 builder 调用之前）。
    yd_root = _verify_root("yd_root", local.yd_root)
    scratch_root = _verify_root("scratch_root", local.scratch_root)
    baseline = Path(baseline_root)

    # 步骤 1：相对性/互异性闸门（在任何写入之前）与拒绝覆盖检查。
    variants = variant_targets(local, config)
    viewer = viewer_targets(local)
    labelled_targets = list(variants.items()) + list(viewer.items())
    _refuse_existing_targets(labelled_targets, phase="执行前")

    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    work_dir = scratch_root / f"{_SCRATCH_PREFIX}-{token}"
    staging_root = yd_root / f"{_STAGING_PREFIX}-{token}"
    created: list[Path] = []
    committed: list[Path] = []

    try:
        # 步骤 2–3：scratch 工作目录 + 逐 source 调 builder。
        _ensure_directory(work_dir, [], lower_bound=scratch_root)
        requests: dict[str, VariantBuildRequest] = {}
        for source in SOURCE_IDS:
            variant_root = work_dir / source
            if os.path.lexists(variant_root):  # pragma: no cover - token 保证不可达
                raise PrepareError(f"scratch 变体目录已存在：{variant_root}")
            _ensure_directory(variant_root, [], lower_bound=scratch_root)
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
                # 等价变异，不可判别：删掉本分支后紧邻的 `except PrepareError: raise`
                # 照样把子类原样上浮，可观测行为完全相同（本分支只是把意图写明）。
                # "不重新包装"这条性质本身由
                # test_prepare_rejection_and_unimplemented_binding_use_different_exit_codes
                # 与 test_production_builder_binding_fails_before_any_subprocess 钉住。
                raise
            except PrepareError:
                raise
            except Exception as exc:
                raise PrepareError(f"builder 构建 {source} 变体失败：{exc}") from exc

        # 步骤 4：逐变体产物校验（两个都过才进入搬运）。
        for source in SOURCE_IDS:
            _validate_variant(source, requests[source].variant_root, config)

        # 步骤 5：scratch -> `YD_ROOT` 内 staging（按发布权限新建条目）。
        _ensure_directory(staging_root, created, lower_bound=yd_root)
        models_staging = staging_root / "models"
        viewer_staging = staging_root / "viewer"
        for source in SOURCE_IDS:
            # staging 内按 source 命名而非按终名叶名：两个终名允许同叶名不同父目录
            # （`a/yd` 与 `b/yd`），照终名命名会在 staging 里撞成一个。
            # pinned: test_each_final_name_receives_its_own_source_content
            # （内容按终名对应；两次 staging 复制的源对调即变红）
            _copy_tree_publish(
                requests[source].variant_root,
                models_staging / source,
                created,
                lower_bound=yd_root,
            )

        # 步骤 6：GeoJSON 直接落 staging（唯一落点，不经 scratch）。
        # pinned: test_geojson_feature_counts_match_the_baseline、
        # test_geometry_failure_rolls_back_validated_variants（`GeometryError` 必须被包装
        # 成 `PrepareError` 且两个已校验变体一个都不提交）
        _ensure_directory(viewer_staging, created, lower_bound=yd_root)
        try:
            write_viewer_geojson(
                rivers_shp=baseline_rivers_shp(baseline),
                domain_shp=baseline_domain_shp(baseline),
                out_dir=viewer_staging,
            )
        except GeometryError as exc:
            raise PrepareError(f"viewer GeoJSON 生成失败：{exc}") from exc

        # 步骤 7：提交前把四个终名再探一次（TOCTOU 窄化），随后逐个同盘 rename 提交，
        # 顺序「两变体 → rivers → boundary」。
        # pinned: test_target_appearing_after_the_first_guard_is_refused_before_commit
        # （复探）、test_every_commit_renames_within_yd_root_on_one_device（顺序与同盘）
        _refuse_existing_targets(labelled_targets, phase="提交前复探")
        plan: list[tuple[Path, str, Path]] = [
            (models_staging, source, variants[source]) for source in SOURCE_IDS
        ]
        plan += [
            (viewer_staging, VIEWER_GEOJSON_NAMES[key], viewer[key])
            for key in ("rivers", "boundary")
        ]
        for source_parent, source_name, target in plan:
            _ensure_directory(target.parent, created, lower_bound=yd_root)
            _wrap_fs(
                lambda source_parent=source_parent, source_name=source_name, target=target: (
                    safe_fs.rename_entry_no_follow(
                        source_parent, source_name, target.parent, target.name
                    )
                ),
                f"提交失败：{source_parent / source_name} -> {target}",
            )
            committed.append(target)
    except BaseException as exc:
        # 步骤 8（失败侧）：把本次新建的条目全部撤回，使 `YD_ROOT` 的条目集合与执行前
        # 相同。顺序自内向外：已提交终名 -> staging -> 本次新建的父目录（逆序）->
        # scratch 工作目录。已提交终名在执行前由 `lexists` 守卫证实不存在，故删除的只
        # 可能是本次产物；父目录一律非递归（见 `_remove_created_directory`）。
        #
        # 每一步互不取消，失败**收集**而非抛出：抛出会取消其余撤回步骤，并把原始异常
        # （可能是 `BuilderUnavailableError`）替换成一条清理错误。失败以 `add_note` 附
        # 在原始异常上——`raise` 是裸重抛，异常类型、`__cause__` 与 traceback 都不动。
        # pinned: test_late_commit_failure_rolls_back_already_committed_targets（撤回顺序
        # 与完整性）、test_one_failing_rollback_step_does_not_cancel_the_others（互不取消）、
        # test_builder_unavailable_survives_a_cleanup_failure（异常类不被替换）、
        # test_keyboard_interrupt_from_the_builder_still_rolls_back（这里收的是
        # `BaseException`；改成 `Exception` 即变红）
        failures = _run_cleanup_steps(
            [partial(_remove_tree, target) for target in reversed(committed)]
            + [partial(_remove_tree, staging_root)]
            + [
                partial(_remove_created_directory, directory)
                for directory in reversed(created)
            ]
            + [partial(_remove_scratch_tree, work_dir)]
        )
        for failure in failures:
            exc.add_note(f"回滚/清理未完成：{failure}")
        raise
    else:
        # 步骤 8（成功侧）：只清 staging 与 scratch。失败不升格为异常——四个终名都已
        # 提交，报成失败会让重跑撞上拒绝覆盖守卫（无 `--force`），把一次成功变成死局。
        # pinned: test_success_survives_a_staging_cleanup_failure、
        # test_success_reports_no_cleanup_warnings_when_cleanup_is_clean、
        # test_scratch_cleanup_failure_does_not_gate_the_staging_cleanup
        warnings = _run_cleanup_steps(
            [
                partial(_remove_tree, staging_root),
                partial(_remove_scratch_tree, work_dir),
            ]
        )

    return PrepareReport(
        variants=dict(variants),
        rivers_geojson=viewer["rivers"],
        boundary_geojson=viewer["boundary"],
        cleanup_warnings=tuple(warnings),
    )
