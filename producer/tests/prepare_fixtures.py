"""`prepare` 编排用的合成基线包、**记录型可编排假 builder** 与全树快照工具。

独立性纪律（与 `geometry_fixtures` / `cfg_ic_fixtures` 同源）：

* 本模块 MUST NOT 从 `yd_producer.prepare` import 任何**行为**——只 import 它公开的
  布局常量（`VARIANT_*`），因为那正是被测契约里"变体该长什么样"的单一权威；假 builder
  若自己再写一套文件名，测的就成了"两套常量是否一致"。判定逻辑（条目集合校验、reach
  数校验、提交/清理）一概不复用。
* 合成率定末态 `cfg.ic` 一律经 `cfg_ic_fixtures.build_cfg_ic` 的原生分段生成器产出，
  **不在本 issue 手写第二套格式**；reach 数的期望值由生成器实际写入的 river 行数给定
  （`RecordingBuilder.written_river_rows`），不手写常量。
* 合成基线 GIS 一律经 `geometry_fixtures.write_synthetic_baseline` 产出（10.1/10.2 已
  钉死的锚点纪律）。

"无新写入"的断言一律用 `tree_snapshot`：**相对路径 + 文件字节**的全树快照。单点探测
（"某个特定文件不存在"）对"写到别处去了"的实现恒真，没有判别力。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from cfg_ic_fixtures import build_cfg_ic
from geometry_fixtures import SyntheticBaseline, write_synthetic_baseline

from yd_producer.prepare import (
    VARIANT_BINDING_NAME,
    VARIANT_CALIBRATED_STATE_NAME,
    VARIANT_HYDRO_PARAM_NAME,
    VariantBuildRequest,
)

#: 基线包内的水文参数文件名。假 builder 把它**原样复制**进每个变体，使"两变体水文参数
#: 同源一致"这条断言有一个独立于被测实现的来源。
BASELINE_HYDRO_PARAM_NAME = "yd.para"

#: 合成基线包内的水文参数字节（内容无语义，只作同源判据）。
BASELINE_HYDRO_PARAM_BYTES = b"# synthetic hydrologic parameters\nKsatH 1.0e-4\n"

#: 合成率定末态的 mesh 段行数（与 reach 数无关，只为让文档结构合法）。
SYNTHETIC_MESH_COUNT = 2


def binding_bytes(*, grid_id: str, source_id: str) -> bytes:
    """假 builder 写进 `yd.binding` 的字节：把 `grid_id`/`source_id` **写进内容**。

    这是"变体内容 ↔ 终名"绑定的唯一 oracle，故 fixture 与断言共用本函数而不是各写一遍
    格式：断言那边只要写出「`yd_gfs` 里该有 `source_id=gfs`」，把两次 staging 复制源对调
    的实现就必红。只断言"两个 binding 互不相等"在对调下恒真——它是一条永真式。
    """
    return f"grid_id={grid_id}\nsource_id={source_id}\n".encode()


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
    """在 `directory` 下生成一份合成基线包。

    布局对齐 `prepare` 的合成约定：`<root>/gis/rivers.shp` 与 `<root>/gis/domain.shp`
    （真实外部基线包的现场布局归 M4）。另放一份水文参数文件供假 builder 复制。
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    gis = write_synthetic_baseline(
        root / "gis",
        river_count=river_count,
        unit_count=unit_count,
        rivers_stem="rivers",
        domain_stem="domain",
    )
    hydro_param = root / BASELINE_HYDRO_PARAM_NAME
    hydro_param.write_bytes(BASELINE_HYDRO_PARAM_BYTES)
    return SyntheticBaselinePackage(
        root=root,
        gis=gis,
        hydro_param=hydro_param,
        river_feature_count=river_count,
    )


@dataclass
class VariantScript:
    """单个 source 的产出剧本；默认值即"完全合法的变体"。"""

    #: 率定末态 river 段的行数；`None` 表示取 builder 的 `river_count`
    river_count: int | None = None
    #: `False` 表示率定末态**没有 river 段**（`build_cfg_ic(river_count=0)`）
    include_river_section: bool = True
    #: 额外留在 `variant_root` 内的条目（如 `"yd.binding.tmp"`）
    extra_entries: tuple[str, ...] = ()
    #: 额外留在 `variant_root` 内的 **symlink** 条目（名 -> 目标字符串）。builder 的产出
    #: 按构造不可信，symlink 是编排清理面必须能吃下的形态之一
    symlink_entries: tuple[tuple[str, str], ...] = ()
    #: 不写出的条目（用于"变体缺必需文件"）
    omit_entries: tuple[str, ...] = ()
    #: `True` 表示 builder 返回但**根本没建**（这里是：删掉编排预建的）`variant_root`
    remove_variant_root: bool = False
    #: `True` 表示率定末态写成不可解析的字节（非 UTF-8）
    corrupt_state: bool = False
    #: 非 `None` 时 builder 抛出该异常
    raises: BaseException | None = None


class RecordingBuilder:
    """记录型 + 可编排的假 builder。

    记录每次调用的 `VariantBuildRequest`（含调用当时 `variant_root` 是否已存在、是否为
    空），并按 `scripts` 给定的剧本在 `variant_root` 内写出合成变体。
    """

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
        #: 调用当时 `variant_root` 是否已存在且为空目录
        self.variant_root_was_empty_dir: list[bool] = []
        #: 逐 source 实际写入率定末态的 river 数据行数（reach 期望值的唯一来源）
        self.written_river_rows: dict[str, int] = {}

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

        payload = {
            # 水文参数从**同一份基线**原样复制：两变体字节必然一致（同源）。
            VARIANT_HYDRO_PARAM_NAME: self._package.hydro_param.read_bytes(),
            # binding 逐 source 由 `grid_id`/`source_id` 决定：两变体字节必然不同（不
            # 共用），且内容可反查出它属于哪个 source（见 `binding_bytes`）。
            VARIANT_BINDING_NAME: binding_bytes(
                grid_id=request.grid_id, source_id=request.source_id
            ),
            VARIANT_CALIBRATED_STATE_NAME: (
                b"\xff\xfe truncated-not-utf8"
                if script.corrupt_state
                else document.payload
            ),
        }
        for name, content in payload.items():
            if name in script.omit_entries:
                continue
            (root / name).write_bytes(content)
        for name in script.extra_entries:
            (root / name).write_bytes(b"residue\n")
        for name, link_target in script.symlink_entries:
            (root / name).symlink_to(link_target)


def tree_snapshot(root: Path) -> dict[str, object]:
    """`root` 的全树快照：相对路径 -> 目录标记 / 文件字节 / symlink 目标。

    键含**每一个**条目（目录也在内），故"多留了一个空的 staging 目录"这类残留同样会
    让比对变红——只比文件的快照对空目录残留恒真。不跟随 symlink。
    """
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
    """包住 `safe_fs.rename_entry_no_follow`，记录每次提交的源与终名。

    存在的理由是两条**判别性**证据只能在这条边界上取：(i) 每次 rename 的源与终名
    `st_dev` 相等且源在 `yd_root` 之内——把 staging 放回 scratch 的实现在生产上必然
    `EXDEV`，本地两根同盘时不会自己暴露；(ii) 提交中途失败的回滚（`fail_at` 令第 N 次
    rename 抛 `SafeFilesystemError`，模拟 NFS 的 `ENOSPC`/`ESTALE`）。
    """

    delegate: object
    fail_at: int | None = None
    calls: list[tuple[Path, Path]] = field(default_factory=list)
    #: 每次 rename **调用当时**源与终名父目录的 `st_dev`（rename 之后源已不存在，事后
    #: 再 stat 只会拿到 `FileNotFoundError`，故必须在这里取）。
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
