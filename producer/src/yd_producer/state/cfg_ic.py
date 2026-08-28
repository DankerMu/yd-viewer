"""SHUD `cfg.ic` 原生分段格式的**字节保真**解析与回写（任务 4.1）。

溯源：`NWM@8ae9b8f2 packages/common/state_qc.py`。分段识别的判定语义整体移植自该 pin，
逐函数带 `NWM@8ae9b8f2 packages/common/state_qc.py` 注释。

**为什么不是「快照 + 补一个 writer」**：pin 的 `_parse_ic_file`(:424) 没有 writer，且解析
三重有损——`line.strip()` 丢首尾空白、空行被丢弃、token 经 `float()` 丢原始记法
（`0.100000` / `1e-3` / `-0.0` 一旦转成 float 就无法复原原写法）。而 spec state-tools 要求
「解析后 MUST 能无损回写」且验收是**字节等价**，下游重戳又要求「数据区 MUST 保持不变」。
`cfg.ic` 的产出方是 SHUD 求解器与率定末态，格式不由本项目控制，故本模块的数据模型**逐行
保真**：`CfgIcDocument.lines` 是原始行（含行尾符）的逐字副本，`render` 只把它们拼接回
bytes，**绝不由数值重新格式化**。数值视图 `Section.rows` 是只读派生，不参与回写。

行归属是**全覆盖划分**：每一行恰好一个 `LineRole`，无「未归属」行。

对 pin 的**刻意偏离**（八条，此处即全集）。此清单已把 `parse` 里**全部** `raise ValueError`
逐条对 pin 的 `_parse_sectioned_rows` / `_parse_ic_file` 核对过：其余 `raise` 均有 pin 对应物
（超限、非 UTF-8、空文件、不可读 header、非数值数据行、截断 body、截断 lake body）；唯一既无
pin 对应物又未列入的 `raise` 是 `parse` 末尾的 unassigned 全覆盖划分自检——它对**任何**输入
都不可达（`pragma: no cover`），是内部不变量断言而非 fail-closed，故不计入偏离。
清单的穷尽性由 `test_cfg_ic.py` 的 `ast` 计数测试机械闭合（`parse` 体内 `raise` 总数 ==
偏离数 + 有 pin 对应物数 + 不可达自检数），docstring 里的条数写错即变红。

1. **mesh 段超出 header 声明 `mesh_count` 的多余数据行抛 `ValueError`**。pin 的
   `_parse_sectioned_rows`(:531-534，其中 `:532-533` 是 `if len(mesh_rows) < mesh_count:`
   与 `mesh_rows.append(row)` 这一对) 静默丢弃多余 mesh 行；格式保真根不得静默丢状态行。
2. **任何分段列头之前出现的数值行抛 `ValueError`**。pin 的分段走查在 `section is None`
   时让该行穿过所有分支被静默丢弃；同 1 的理由，且全覆盖划分不允许存在无归属的行。
3. **文件不存在/是目录/不可读的 `OSError` 统一封装为 `ValueError`**。pin 的
   `_read_bytes_limited`(:563-571) 直接抛 `OSError`，由调用方 `except (OSError, ValueError)`
   兜住；本模块收敛为单一异常类型，调用方无需知道两种。仓库级错误封装另属结构检查层。
4. **分段体内没有 mesh 列头时抛 `ValueError`**。pin 无此检查（它只按 `section` 归行，
   mesh 段缺席就返回空 mesh 列表）；本模块的 `Section.column_header_index` 是非可选字段，
   没有 mesh 列头就构造不出 `CfgIcDocument.mesh`，故 fail-closed。可达面很窄：只有
   `declared_mesh_count == 0` 且首个列头不是 mesh 段时才走到这里（否则先被偏离 1/2 或
   「截断 body」拦下）。
5. **`max_bytes` 为负时在任何读取之前抛 `ValueError`**。pin 的读取路径是
   `handle.read(max_bytes + 1)`：`max_bytes == -2` 会退化为 `read(-1)`，把**整个文件**
   读进内存，随后 `len(data) > max_bytes` 照样抛错——于是这次无界读长得和一次正常拒绝
   一模一样，OOM 保护静默失效。`max_bytes` 在本模块是公开可注入参数，故先验后读。
6. **body 内不含任何分段列头（计数式兼容布局）时抛 `ValueError`**。pin 的
   `_parse_sectioned_rows`(:508-509) 在这种输入上返回 `None`，其调用方 `_parse_ic_file`
   (:463-493) 随即按 header 的 `(mesh, river, lake)` 计数切分数据行并**成功**返回；本模块
   只支持原生分段布局，在此 fail-closed，是无 pin 对应物的拒绝。理由：格式保真回写要求
   逐段行归属可判定，而计数式布局没有列头可锚定段边界，靠 header 计数切分一旦与真实 body
   不符就会把状态行错归到别的段——这正是「绝不静默丢/错置状态行」的同一条根。取舍与未决点
   见 issue #8 fixture。
7. **同名分段列头第二次出现时抛 `ValueError`（段重入守卫）**。pin 的分段走查只把
   `section` 变量重新置位、数据行继续往同一个 list 累加，于是 river 段之后再出现一次 mesh
   列头会让 `mesh.span` 把 river 的列头与数据行整段吞进区间内（issue #54 第 3 条实测：
   `mesh span (2, 7)` 覆盖 idx 3 的 river 列头与 idx 4 的 river 数据行）。`Section.span`
   的契约是「段内可能夹杂空行」，而不是「夹着另一个段」；#9 是 `span` 的第一个消费方，故按
   #54 推荐 (a) 在此 fail-closed。
8. **首行以 U+FEFF（UTF-8 BOM）起头时抛 `ValueError` 并点名 BOM**。pin 无 BOM 面。
   `str.strip()` **不**剥 U+FEFF，于是 header 首 token 被 `_as_float` 判为 None、被
   `_header_counts` 的推导式静默丢掉，计数 token 整体左移一位：`declared_mesh_count` 拿到
   的是 mesh **列数**而不是行数（issue #54 第 4 条实测：BOM + `3 6 0 0.0` + 3 行 mesh 报
   `truncated sectioned IC body: have mesh=3; header declares mesh=6`，把运维支到「文件被
   截断」的错误方向；mesh 行数恰等于列数时更会**静默误解析**通过）。故在解码后、任何分段
   判定之前显式拒绝并直说 BOM。

对 pin 的**模型扩展**（非偏离，pin 无对应面，故不计入上面的八条）：

- 空行单独归 `LineRole.BLANK`。pin 在分段前先丢空行，本模块必须保留它们才能字节等价，又
  不能把它们计入任何段的数据行（会污染 #9 继承的段行数与行区间），故显式成为第五类归属。
  **检测路径仍按 pin 归一化**（先 `strip()`、跳过空行再判定），保真只作用于回写侧。
- `CfgIcDocument.__post_init__` 的构造期不变量校验与 `CfgIcDocument.with_replaced_lines`
  的行替换 API（issue #54 第 5 条）。pin 没有文档模型，故这两处的 `raise ValueError`
  既无 pin 对应物、也不是对 pin 判定语义的偏离，而是本模块自有 API 的前置条件。它们是
  **文档改写的唯一合法入口**：裸 `dataclasses.replace(doc, lines=...)` 一旦改变行数就会
  让 `roles` / `header_index` / 各段行号全部过期，而 `render` 照样返回看起来正常的 bytes
  （#54 实测）；`__post_init__` 把这条静默路径变红。`with_replaced_lines` 同时重算被替换
  数据行的 `Section.rows`——`rows` 是 `lines` 的派生视图，滞留旧值会造出「文本已归零、
  `rows` 仍是负数」的错位文档。上面的 `ast` 计数测试把这两个函数的 `raise` 单独计一类，
  漏登记同样变红。

本模块 stdlib-only：零 NWM 运行时 import、零数据库/scheduler 依赖，不写任何文件
（`render` 返回 bytes，落盘归调用方）。**只支持原生分段布局**（见上偏离 6）。
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "MAX_STATE_IC_BYTES",
    "CfgIcDocument",
    "LineRole",
    "Section",
    "parse",
    "render",
]

#: UTF-8 BOM 的解码形态。`str.strip()` 不剥它，故必须显式判（见模块头偏离 8）。
_UTF8_BOM = "\ufeff"

#: `str.splitlines` 会在这些字符上断行——它们全部**不是** C/Fortran 行读者的换行。
#: `with_replaced_lines` 用它判「替换值是否会改变行数」，比只判 `"\n"` 严（U+0085 走的
#: 正是这条：issue #54 第 2 条实测它能把一条物理行断成两条逻辑行）。
_LINE_BREAK_CHARS = (
    "\n",
    "\r",
    "\v",
    "\f",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    "\u2028",
    "\u2029",
)

# NWM@8ae9b8f2 packages/common/state_qc.py:43（逐字移植）
# Upper bound (bytes) on a SHUD ``.cfg.ic`` state file the QC parser will read into
# memory. Real per-basin restart states are far smaller; a file above this bound is
# treated as a QC failure (corrupt / wrong artifact) rather than read unboundedly into
# memory (OOM protection). 64 MiB matches the limited-read ceiling used elsewhere.
MAX_STATE_IC_BYTES = 64 * 1024 * 1024


class LineRole(enum.Enum):
    """一行在 `cfg.ic` 里的归属。文档模型对每一行恰好赋一个值（全覆盖划分）。"""

    BLANK = "blank"
    HEADER = "header"
    COLUMN_HEADER = "column_header"
    DATA = "data"
    LAKE_PREAMBLE = "lake_preamble"


@dataclass(frozen=True)
class Section:
    """一个分段（mesh / river / lake）在文档里的位置与只读数值视图。

    `data_line_indices` 是该段数据行在 `CfgIcDocument.lines` 里的**精确**行号（段内可能
    夹杂空行，故不等于连续区间）；`span` 是含首尾的行区间 `[start, end)`，仅当段有数据行
    时有意义。`rows` 是数值派生视图，**不是**回写来源。
    """

    name: str
    column_header_index: int
    data_line_indices: tuple[int, ...]
    rows: tuple[tuple[float, ...], ...]

    @property
    def row_count(self) -> int:
        return len(self.data_line_indices)

    @property
    def span(self) -> tuple[int, int] | None:
        if not self.data_line_indices:
            return None
        return (self.data_line_indices[0], self.data_line_indices[-1] + 1)


@dataclass(frozen=True)
class CfgIcDocument:
    """`cfg.ic` 的格式保真文档模型。

    `lines` 是原始行的逐字副本，**含各自的行尾符**（`str.splitlines(keepends=True)`），
    因此 CRLF/LF 混排与「文件无末尾换行」都被原样保留，`render` 拼接即字节还原。
    """

    lines: tuple[str, ...]
    roles: tuple[LineRole, ...]
    header_index: int
    mesh: Section
    river: Section | None
    lake: Section | None
    lake_preamble_index: int | None
    declared_mesh_count: int
    declared_lake_count: int | None

    def __post_init__(self) -> None:
        """构造期不变量校验（issue #54 第 5 条）。

        `parse` 自己不可能产出违反项，但 `dataclasses.replace(doc, lines=...)` 可以：一旦
        行数变化，`roles` / `header_index` / 各段行号全部过期，而 `render` 照样返回
        **看起来正常**的 bytes。此处把那条静默路径变红。写面请改用
        `with_replaced_lines`，它行数恒定故所有派生行号继续有效。
        """
        line_count = len(self.lines)
        if len(self.roles) != line_count:
            raise ValueError(
                "CfgIcDocument roles/lines length mismatch: "
                f"len(roles)={len(self.roles)} != len(lines)={line_count}"
            )
        if not 0 <= self.header_index < line_count:
            raise ValueError(
                f"CfgIcDocument header_index {self.header_index} out of range "
                f"[0, {line_count})"
            )
        for section in (self.mesh, self.river, self.lake):
            if section is None:
                continue
            if not 0 <= section.column_header_index < line_count:
                raise ValueError(
                    f"CfgIcDocument {section.name} column_header_index "
                    f"{section.column_header_index} out of range [0, {line_count})"
                )
            for index in section.data_line_indices:
                if not 0 <= index < line_count:
                    raise ValueError(
                        f"CfgIcDocument {section.name} data line index {index} "
                        f"out of range [0, {line_count})"
                    )
        if self.lake_preamble_index is not None and not (
            0 <= self.lake_preamble_index < line_count
        ):
            raise ValueError(
                f"CfgIcDocument lake_preamble_index {self.lake_preamble_index} "
                f"out of range [0, {line_count})"
            )

    def with_replaced_lines(self, replacements: Mapping[int, str]) -> CfgIcDocument:
        """按行号替换行文本，返回新文档。**行数恒定**，故派生行号继续有效。

        `replacements` 的值是**不含行尾符**的行体；每行的原行尾符由本 API 原样贴回
        （原行无行尾符——文件末行无换行——则替换后也无）。这是 4.3 重戳与 4.4 负残差
        改写文档的**唯一**入口：未列入 `replacements` 的行逐字节保持原样，故「只有被
        刻意改动的那几行重新序列化」是结构性保证，而不是实现自觉。

        被替换的**数据行**的数值视图 `Section.rows` 随之重算——`rows` 是 `lines` 的派生
        视图，让它滞留旧值会造出「文本已归零、`rows` 仍是负数」的错位文档（负残差归零的
        幂等性正是在此被证伪的）。重算不出数值行（替换值非数值）即 `ValueError`。

        越界行号、或替换值内含任何会被 `str.splitlines` 断行的字符（即会改变行数）一律
        抛 `ValueError`。
        """
        line_count = len(self.lines)
        new_lines = list(self.lines)
        for index, text in replacements.items():
            if not isinstance(index, int) or isinstance(index, bool):
                # TRY004 豁免的理由：本模块族的约定是「结构性/语义性拒绝一律 `ValueError`」（#8 确立），
                # 调用方无需分辨两种异常类型；此处刻意不抛 `TypeError`。
                raise ValueError(  # noqa: TRY004
                    f"line index must be int, got {index!r}"
                )
            if not 0 <= index < line_count:
                raise ValueError(f"line index {index} out of range [0, {line_count})")
            for char in _LINE_BREAK_CHARS:
                if char in text:
                    raise ValueError(
                        "replacement line text must not contain a line break "
                        f"({char!r} found at line {index}); "
                        "with_replaced_lines keeps the line count constant"
                    )
            original = self.lines[index]
            body = original.splitlines()[0] if original.splitlines() else ""
            new_lines[index] = text + original[len(body) :]

        replaced = set(replacements)

        def _refreshed(section: Section | None) -> Section | None:
            if section is None or replaced.isdisjoint(section.data_line_indices):
                return section
            rows: list[tuple[float, ...]] = []
            for index in section.data_line_indices:
                row = _numeric_row(new_lines[index])
                if row is None:
                    raise ValueError(
                        f"replacement for {section.name} data line {index} is not a "
                        f"numeric row: {new_lines[index]!r}"
                    )
                rows.append(tuple(row))
            return dataclasses.replace(section, rows=tuple(rows))

        return dataclasses.replace(
            self,
            lines=tuple(new_lines),
            mesh=_refreshed(self.mesh),
            river=_refreshed(self.river),
            lake=_refreshed(self.lake),
        )


def parse(
    source: Path | str | bytes,
    *,
    max_bytes: int = MAX_STATE_IC_BYTES,
) -> CfgIcDocument:
    """解析原生分段 `cfg.ic`，返回逐行保真的文档模型。

    `source` 为 `Path`/`str` 时按**文件路径**读入（有界读，最多 `max_bytes + 1` 字节）；
    为 `bytes` 时按**文件内容**直接解析。任何结构性不可用一律抛 `ValueError`（不外泄
    `OSError` / `UnicodeDecodeError`），且失败时不返回部分文档。

    `max_bytes` 为负时**在任何读取之前**抛 `ValueError`（见模块头偏离 5）。
    """
    if max_bytes < 0:
        raise ValueError(f"max_bytes must be non-negative, got {max_bytes}")
    if isinstance(source, bytes):
        data = source
    else:
        path = Path(source)
        # NWM@8ae9b8f2 packages/common/state_qc.py:431-435（调用点注释逐字保留）
        # Bounded read (OOM protection): read at most one byte past the limit so an
        # oversized file is detected without being slurped whole into memory. The path
        # here is a trusted local IC file (the snapshot layer stages it before calling),
        # so a plain bounded read is used rather than the no-follow safe-fs reader
        # (which would reject legitimate symlinked temp dirs such as macOS /tmp).
        try:
            data = _read_bytes_limited(path, max_bytes=max_bytes)
        except OSError as error:
            # 刻意偏离 pin：OSError 统一封装为 ValueError（见模块头偏离 3）。
            raise ValueError(f"无法读取 cfg.ic：{path}（{error}）") from error
    if len(data) > max_bytes:
        raise ValueError(f"IC file exceeds size limit of {max_bytes} bytes")
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"IC file is not valid UTF-8: {error}") from error
    if raw.startswith(_UTF8_BOM):
        # 刻意偏离 pin：pin 无 BOM 面，BOM 会被误诊成 truncated（见模块头偏离 8）。
        raise ValueError(
            "IC file starts with a UTF-8 BOM (U+FEFF): "
            "文件带 UTF-8 BOM，请以无 BOM 的 UTF-8 重新导出 cfg.ic。"
            "BOM 会让 header 的首个计数 token 无法解析、计数整体左移一位，"
            "从而把根因误诊成文件被截断"
        )

    lines = tuple(raw.splitlines(keepends=True))
    roles: list[LineRole | None] = [None] * len(lines)
    # 检测路径按 pin 归一化：先 strip、丢空行，再做 header / 分段判定。
    significant: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped:
            significant.append((index, stripped))
        else:
            roles[index] = LineRole.BLANK
    if not significant:
        raise ValueError("empty IC file")

    header_index, header_text = significant[0]
    roles[header_index] = LineRole.HEADER
    counts = _header_counts(header_text.split())
    if counts is None:
        raise ValueError(f"unreadable IC header: {header_text!r}")
    # native header 的第二个数值 token 是 mesh 状态**列数**，不是 river 元素数；lake 槽恒为
    # 0（pin :496-506 与 :601-606 逐字为证）。故只取 mesh_count，river/lake 计数不由 header 定。
    declared_mesh_count = counts[0]

    body = significant[1:]
    if not any(_looks_like_column_header(text) for _, text in body):
        # 刻意偏离 pin：pin 在此返回 None 并成功走计数式兼容布局（见模块头偏离 6）。
        raise ValueError(
            "cfg.ic 不是原生分段布局：未发现任何分段列头"
            "（如 `Index Canopy Snow Surface Unsat GW` / `Index Stage`）；"
            "本模块只接受原生分段格式，不回退到计数式兼容布局"
        )

    mesh_indices: list[int] = []
    river_indices: list[int] = []
    lake_indices: list[int] = []
    mesh_rows: list[tuple[float, ...]] = []
    river_rows: list[tuple[float, ...]] = []
    lake_rows: list[tuple[float, ...]] = []
    column_header_indices: dict[str, int] = {}
    lake_preamble_index: int | None = None
    declared_lake_count: int | None = None
    section: str | None = None
    stage_section_count = 0

    for position, (line_index, text) in enumerate(body):
        if _looks_like_column_header(text):
            section = _section_from_column_header(
                text, stage_section_count=stage_section_count
            )
            if section in column_header_indices:
                # 刻意偏离 pin：pin 只把 `section` 重新置位、数据行继续往同一个 list 累加，
                # 于是 `Section.span` 会吞进另一个段的列头与数据行（见模块头偏离 7）。
                raise ValueError(
                    f"duplicate sectioned IC column header for section {section!r}: "
                    f"first at line {column_header_indices[section]}, "
                    f"again at line {line_index} ({text!r})"
                )
            if section in {"river", "lake"}:
                stage_section_count += 1
            roles[line_index] = LineRole.COLUMN_HEADER
            column_header_indices[section] = line_index
            continue

        row = _numeric_row(text)
        if row is None:
            raise ValueError(f"non-numeric IC data row: {text!r}")

        if section is None:
            # 刻意偏离 pin：pin 让 section=None 的数值行穿过所有分支被静默丢弃。
            raise ValueError(
                f"IC data row precedes any section column header: {text!r}"
            )
        if section == "mesh":
            if len(mesh_rows) >= declared_mesh_count:
                # 刻意偏离 pin：pin 静默丢弃超出声明数的 mesh 行（见模块头偏离 1）。
                raise ValueError(
                    "surplus sectioned IC mesh row: "
                    f"header declares mesh={declared_mesh_count}; extra row {text!r}"
                )
            mesh_rows.append(tuple(row))
            mesh_indices.append(line_index)
            roles[line_index] = LineRole.DATA
            continue
        if section == "river":
            # NWM@8ae9b8f2 packages/common/state_qc.py:536-539（逐字保留的语义说明）
            # Native SHUD inserts ``<lake-count> <lake-state-columns>`` between
            # the final river row and the lake column header.  It is section
            # metadata, not an additional river state.  QHH is the first
            # production basin with this layout (``1 2`` + ``Index LakeStage``).
            next_text = body[position + 1][1] if position + 1 < len(body) else None
            preamble = _native_lake_section_preamble(
                text,
                next_line=next_text,
                stage_section_count=stage_section_count,
            )
            if preamble is not None:
                declared_lake_count = preamble
                lake_preamble_index = line_index
                roles[line_index] = LineRole.LAKE_PREAMBLE
                continue
            river_rows.append(tuple(row))
            river_indices.append(line_index)
            roles[line_index] = LineRole.DATA
            continue
        lake_rows.append(tuple(row))
        lake_indices.append(line_index)
        roles[line_index] = LineRole.DATA

    if len(mesh_rows) != declared_mesh_count:
        raise ValueError(
            "truncated sectioned IC body: "
            f"have mesh={len(mesh_rows)}; header declares mesh={declared_mesh_count}"
        )
    if declared_lake_count is not None and len(lake_rows) != declared_lake_count:
        raise ValueError(
            "truncated sectioned IC lake body: "
            f"have lake={len(lake_rows)}; section declares lake={declared_lake_count}"
        )
    if "mesh" not in column_header_indices:
        # 刻意偏离 pin：pin 无此检查（见模块头偏离 4）。`Section.column_header_index`
        # 非可选，没有 mesh 列头就构造不出 `doc.mesh`。
        raise ValueError("sectioned IC body has no mesh column header")

    unassigned = [index for index, role in enumerate(roles) if role is None]
    if unassigned:  # pragma: no cover - 全覆盖划分的自检，正常路径不可达
        raise ValueError(f"unassigned IC lines at indices {unassigned}")
    assigned_roles: tuple[LineRole, ...] = tuple(
        role for role in roles if role is not None
    )

    return CfgIcDocument(
        lines=lines,
        roles=assigned_roles,
        header_index=header_index,
        mesh=Section(
            name="mesh",
            column_header_index=column_header_indices["mesh"],
            data_line_indices=tuple(mesh_indices),
            rows=tuple(mesh_rows),
        ),
        river=(
            Section(
                name="river",
                column_header_index=column_header_indices["river"],
                data_line_indices=tuple(river_indices),
                rows=tuple(river_rows),
            )
            if "river" in column_header_indices
            else None
        ),
        lake=(
            Section(
                name="lake",
                column_header_index=column_header_indices["lake"],
                data_line_indices=tuple(lake_indices),
                rows=tuple(lake_rows),
            )
            if "lake" in column_header_indices
            else None
        ),
        lake_preamble_index=lake_preamble_index,
        declared_mesh_count=declared_mesh_count,
        declared_lake_count=declared_lake_count,
    )


def render(doc: CfgIcDocument) -> bytes:
    """由**逐字行**还原文件内容。

    MUST NOT 由 `Section.rows` 重新格式化数值——那会在 `0.100000` / `1e-3` / `-0.0` 这类
    记法上丢字节，而在「干净」输入上恒绿、看不出来。
    """
    return "".join(doc.lines).encode("utf-8")


# --- 以下为 NWM pin 移植的分段识别辅助（判定语义逐字一致） ---


def _read_bytes_limited(path: Path, *, max_bytes: int) -> bytes:
    """Read at most ``max_bytes + 1`` bytes from a trusted local IC file.

    Reading one byte past the limit lets the caller detect (and reject) an oversized
    file without ever materialising more than ``max_bytes + 1`` bytes in memory.
    """
    # NWM@8ae9b8f2 packages/common/state_qc.py:563-571（逐字移植；OSError 由调用点统一封装）
    with open(path, "rb") as handle:
        return handle.read(max_bytes + 1)


def _header_counts(header: Sequence[str]) -> tuple[int, int, int] | None:
    """Extract compatibility (mesh, river, lake) counts from header tokens.

    Compatibility IC headers lead with integer element counts and end with a
    minute-time token. The minute-time may itself be integer-valued (e.g.
    ``27000000.000000``), so it cannot be distinguished from a count by
    integer-ness alone. We therefore take the LAST numeric token as the minute-time
    and the integer-valued tokens BEFORE it as the (mesh, river, lake) counts. lake
    defaults to 0 when absent. For native sectioned files, only the first returned
    value is an element count; the section-aware parser deliberately ignores the
    remaining compatibility values.
    """
    # NWM@8ae9b8f2 packages/common/state_qc.py:574-606（逐字移植）
    numeric = [
        value for value in (_as_float(token) for token in header) if value is not None
    ]
    if len(numeric) < 2:
        # Need at least one count token plus the trailing minute-time.
        return None
    # Drop the trailing minute-time; the remaining tokens are the integer counts.
    count_values = numeric[:-1]
    ints: list[int] = []
    for value in count_values:
        if not float(value).is_integer():
            # A fractional token among the counts marks an earlier minute-time /
            # malformed header; counts must precede it.
            break
        ints.append(int(value))
        if len(ints) == 3:
            break
    if not ints:
        return None
    mesh = ints[0]
    river = ints[1] if len(ints) > 1 else 0
    lake = ints[2] if len(ints) > 2 else 0
    return mesh, river, lake


def _numeric_row(line: str) -> list[float] | None:
    """内部分类器：整行 token 全可解析为 float 时返回数值行，否则 None。"""
    # NWM@8ae9b8f2 packages/common/state_qc.py:730-739（逐字移植）
    tokens = line.split()
    row: list[float] = []
    for token in tokens:
        value = _as_float(token)
        if value is None:
            return None
        row.append(value)
    return row or None


def _looks_like_column_header(line: str) -> bool:
    """判定一行是否为分段列头。"""
    # NWM@8ae9b8f2 packages/common/state_qc.py:741-748（逐字移植）
    tokens = [token.strip().lower() for token in line.split()]
    if not tokens:
        return False
    return tokens[0] in {"index", "id"} and any(
        token
        in {
            "canopy",
            "snow",
            "surface",
            "unsat",
            "gw",
            "stage",
            "river_stage",
            "lake_stage",
            "lakestage",
        }
        for token in tokens[1:]
    )


def _section_from_column_header(line: str, *, stage_section_count: int) -> str:
    """由列头文本判定其开启的段名。"""
    # NWM@8ae9b8f2 packages/common/state_qc.py:751-759（逐字移植）
    tokens = {token.strip().lower() for token in line.split()}
    if {"canopy", "snow", "surface", "unsat", "gw"} & tokens:
        return "mesh"
    if "lake_stage" in tokens or "lakestage" in tokens:
        return "lake"
    if "stage" in tokens or "river_stage" in tokens:
        return "river" if stage_section_count == 0 else "lake"
    return "mesh"


def _native_lake_section_preamble(
    line: str,
    *,
    next_line: str | None,
    stage_section_count: int,
) -> int | None:
    """Return the declared lake count for a native SHUD lake preamble.

    The two metadata tokens are emitted as plain integers.  Requiring both the
    integer lexical form and an immediately following lake column header avoids
    confusing the last ``<river-id> <stage>`` row with section metadata.
    """
    # NWM@8ae9b8f2 packages/common/state_qc.py:762-785（逐字移植）
    if next_line is None or not _looks_like_column_header(next_line):
        return None
    if (
        _section_from_column_header(next_line, stage_section_count=stage_section_count)
        != "lake"
    ):
        return None
    tokens = line.split()
    if len(tokens) != 2:
        return None
    try:
        lake_count, state_column_count = (int(token) for token in tokens)
    except ValueError:
        return None
    if lake_count < 0 or state_column_count <= 0:
        return None
    return lake_count


def _as_float(token: Any) -> float | None:
    # NWM@8ae9b8f2 packages/common/state_qc.py:878-882（逐字移植）
    try:
        return float(token)
    except (TypeError, ValueError):
        return None
