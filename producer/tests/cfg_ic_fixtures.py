r"""合成 `cfg.ic` 生成器（程序化，零二进制入库）。

独立性硬约束：本模块 MUST NOT 从 `yd_producer.state` import 任何东西。结构索引期望值由
**构造过程**记录（生成器发行时登记的行号与角色），而不是由被测解析器回读——否则段归属
断言退化为「解析器与自己一致」的永真式。

**发射包络 MUST 覆盖解析器接受域**：凡解析器接受的输入形态，本生成器都要能构造，否则那
一支正确行为没有任何用例把守。覆盖的维度：
- 分隔符：空格与 **Tab**（真实 native `cfg.ic` 是 Tab 分隔，见 NWM pin 的
  `tests/test_state_qc.py::_write_native_ic`：`"\t".join(...)` / `Index\tLakeStage`）
- 行尾：LF / CRLF、文件有无末尾换行
- 空白：行尾多余空格、段间空行（含纯空白行）、**文件首部的空行**（钉死「header 行 =
  首个非空行」）、**段内数据行之间的空行**（`Section.data_line_indices` 因此不连续）
- 列头拼写：river 段 `Index Stage` 与 **`Index River_Stage`**、lake 段 `Index LakeStage`
  与 **`Index Lake_Stage`**（四种拼写解析器都接受，见 `_looks_like_column_header`）
- 数值记法混合：`0.100000` / `1e-3` / `-0.0` / `2.5E+01`
- lake 段：缺席 / 非空 / **存在但为空（`lake_count=0`）**
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

# 与 SHUD 原生分段布局一致的列头 token（实际分隔符由 `delimiter` 决定）。
MESH_COLUMN_HEADER_TOKENS = ("Index", "Canopy", "Snow", "Surface", "Unsat", "GW")
RIVER_COLUMN_HEADER_TOKENS = ("Index", "Stage")
LAKE_COLUMN_HEADER_TOKENS = ("Index", "LakeStage")

#: river 段的**生产拼写**：真实 SHUD `.cfg.ic.update` 写的是 `Index\tRiver_Stage`
#: （NWM pin `tests/test_state_qc.py` :96/:154/:187 的 QHH 布局 fixture 与
#: `tests/test_shud_runtime.py` :518/:549 的 checkpoint 断言为证）；pin 里只有**合成**
#: writer `_write_native_ic` :611 用 `Index\tStage`。
RIVER_STAGE_COLUMN_HEADER_TOKENS = ("Index", "River_Stage")
#: lake 段的下划线拼写：解析器接受（`_section_from_column_header` 显式判 `lake_stage`），
#: 但 pin 的 fixture 里没有实例——收进包络是为了「接受什么就发什么」，不宣称它是 QHH 形状。
LAKE_STAGE_COLUMN_HEADER_TOKENS = ("Index", "Lake_Stage")

#: 空格分隔的列头文本（移植辅助的判定语义用例直接消费）。
MESH_COLUMN_HEADER = " ".join(MESH_COLUMN_HEADER_TOKENS)
RIVER_COLUMN_HEADER = " ".join(RIVER_COLUMN_HEADER_TOKENS)
LAKE_COLUMN_HEADER = " ".join(LAKE_COLUMN_HEADER_TOKENS)

#: native header 的第二个数值 token 是 mesh 状态**列数**（不是 river 元素数）。
MESH_STATE_COLUMNS = 6
#: lake preamble 的第二个 token 是 lake 状态列数。
LAKE_STATE_COLUMNS = 2
#: header 末位的绝对分钟时标（本 issue 不解释其时间语义，只保留文本）。
DEFAULT_MINUTE = "27000000.000000"

#: 记法混合池：canonical 化的 writer 在这些 token 上必然丢字节。
_MIXED_NOTATIONS = ("0.100000", "1e-3", "-0.0", "2.5E+01", "0.000000")
_PLAIN_NOTATION = "0.100000"


@dataclass
class SyntheticCfgIc:
    """一份合成 `cfg.ic` 及其**由构造记录**的结构索引期望值。"""

    payload: bytes
    lines: tuple[str, ...]
    roles: tuple[str, ...]
    header_index: int
    mesh_column_header_index: int
    mesh_data_indices: tuple[int, ...]
    river_column_header_index: int | None
    river_data_indices: tuple[int, ...]
    lake_preamble_index: int | None
    lake_column_header_index: int | None
    lake_data_indices: tuple[int, ...]

    def write(self, path: Path) -> Path:
        path.write_bytes(self.payload)
        return path


@dataclass
class _Emitter:
    eol: str
    delimiter: str
    trailing_spaces: str
    texts: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)

    def emit(self, tokens: tuple[str, ...] | list[str], role: str) -> int:
        self.texts.append(self.delimiter.join(tokens) + self.trailing_spaces)
        self.roles.append(role)
        return len(self.texts) - 1

    def blank(self, *, whitespace: bool = False) -> int:
        self.texts.append("   " if whitespace else "")
        self.roles.append("blank")
        return len(self.texts) - 1


def _value(index: int, *, mixed: bool) -> str:
    if not mixed:
        return _PLAIN_NOTATION
    return _MIXED_NOTATIONS[index % len(_MIXED_NOTATIONS)]


def build_cfg_ic(
    *,
    mesh_count: int,
    river_count: int,
    lake_count: int | None = None,
    eol: str = "\n",
    delimiter: str = " ",
    trailing_newline: bool = True,
    trailing_spaces: bool = False,
    blank_lines: bool = False,
    leading_blank_lines: int = 0,
    intra_section_blank_lines: bool = False,
    mixed_notation: bool = False,
    minute: str = DEFAULT_MINUTE,
    mesh_state_columns: int = MESH_STATE_COLUMNS,
    lake_body_rows: int | None = None,
    mesh_header_tokens: tuple[str, ...] = MESH_COLUMN_HEADER_TOKENS,
    river_header_tokens: tuple[str, ...] = RIVER_COLUMN_HEADER_TOKENS,
    lake_header_tokens: tuple[str, ...] = LAKE_COLUMN_HEADER_TOKENS,
) -> SyntheticCfgIc:
    """生成一份原生分段 `cfg.ic`。

    `lake_count is None` 表示不含 lake 段；`lake_count == 0` 表示 lake 段**存在但为空**
    （preamble 声明 0 行 + lake 列头 + 零条数据行），这在解析器接受域内。
    `lake_body_rows` 用于制造「preamble 声明数与实际 lake 行数不符」的截断体（默认等于
    `lake_count`）。`leading_blank_lines` 在 header 之前插空行（偶数位空串、奇数位纯空白），
    用于钉死「header 行 = 首个非空行」。`intra_section_blank_lines` 在每个**至少两行数据**
    的段的首条数据行之后插一条空行，使该段的数据行号不连续。
    `mesh_header_tokens` / `river_header_tokens` / `lake_header_tokens` 选择段列头拼写
    （生产拼写见模块头常量）。`mesh_header_tokens` 允许把 `Unsat` 挪出索引 4——4.4 的投影列
    定位按**列头文本**查找，写死索引 4 的实现只有在列序被打乱时才变红；列序固定的 fixture
    让「按文本定位」与「按固定索引定位」在观测上重合，那条断言即无判别力。
    列数由 `mesh_state_columns` 决定，`mesh_header_tokens` 的 token 数须与之相等。
    """
    if len(mesh_header_tokens) != mesh_state_columns:
        raise AssertionError(
            f"mesh 列头 token 数 {len(mesh_header_tokens)} 与 mesh_state_columns "
            f"{mesh_state_columns} 不符"
        )
    emitter = _Emitter(
        eol=eol,
        delimiter=delimiter,
        trailing_spaces="  " if trailing_spaces else "",
    )
    counter = 0

    for position in range(leading_blank_lines):
        emitter.blank(whitespace=position % 2 == 1)

    header_index = emitter.emit(
        (str(mesh_count), str(mesh_state_columns), minute), "header"
    )
    if blank_lines:
        emitter.blank()

    mesh_header_index = emitter.emit(mesh_header_tokens, "column_header")
    mesh_data_indices: list[int] = []
    for element in range(1, mesh_count + 1):
        values = []
        for _ in range(mesh_state_columns - 1):
            values.append(_value(counter, mixed=mixed_notation))
            counter += 1
        mesh_data_indices.append(emitter.emit([str(element), *values], "data"))
        if intra_section_blank_lines and element == 1 and mesh_count >= 2:
            emitter.blank()

    river_header_index: int | None = None
    river_data_indices: list[int] = []
    if river_count > 0:
        if blank_lines:
            emitter.blank(whitespace=True)
        river_header_index = emitter.emit(river_header_tokens, "column_header")
        for element in range(1, river_count + 1):
            stage = _value(counter, mixed=mixed_notation)
            counter += 1
            river_data_indices.append(emitter.emit([str(element), stage], "data"))
            if intra_section_blank_lines and element == 1 and river_count >= 2:
                emitter.blank(whitespace=True)

    lake_preamble_index: int | None = None
    lake_header_index: int | None = None
    lake_data_indices: list[int] = []
    if lake_count is not None:
        if blank_lines:
            emitter.blank()
        lake_preamble_index = emitter.emit(
            (str(lake_count), str(LAKE_STATE_COLUMNS)), "lake_preamble"
        )
        lake_header_index = emitter.emit(lake_header_tokens, "column_header")
        body_rows = lake_count if lake_body_rows is None else lake_body_rows
        for element in range(1, body_rows + 1):
            stage = _value(counter, mixed=mixed_notation)
            counter += 1
            lake_data_indices.append(emitter.emit([str(element), stage], "data"))
            if intra_section_blank_lines and element == 1 and body_rows >= 2:
                emitter.blank()

    # 末尾空行只在有末尾换行时发；否则最后一行会是空串，行数与 splitlines 不对齐。
    if blank_lines and trailing_newline:
        emitter.blank()

    lines = tuple(text + eol for text in emitter.texts)
    if not trailing_newline:
        lines = lines[:-1] + (emitter.texts[-1],)

    return SyntheticCfgIc(
        payload="".join(lines).encode("utf-8"),
        lines=lines,
        roles=tuple(emitter.roles),
        header_index=header_index,
        mesh_column_header_index=mesh_header_index,
        mesh_data_indices=tuple(mesh_data_indices),
        river_column_header_index=river_header_index,
        river_data_indices=tuple(river_data_indices),
        lake_preamble_index=lake_preamble_index,
        lake_column_header_index=lake_header_index,
        lake_data_indices=tuple(lake_data_indices),
    )


def build_compat_layout(*, mesh_count: int, river_count: int) -> bytes:
    """生成**计数式兼容布局**（无任何分段列头）——本模块必须拒绝的历史格式。"""
    lines = [f"{mesh_count} {river_count} 0 {DEFAULT_MINUTE}"]
    for element in range(1, mesh_count + 1):
        lines.append(f"{element} " + " ".join([_PLAIN_NOTATION] * 5))
    for element in range(1, river_count + 1):
        lines.append(f"{element} {_PLAIN_NOTATION}")
    return ("\n".join(lines) + "\n").encode("utf-8")


#: UTF-8 BOM 的字节形态（#54 第 4 条）。
UTF8_BOM_BYTES = b"\xef\xbb\xbf"
#: U+0085 NEXT LINE：`str.splitlines` 在它上面断行，C/Fortran 行读者不会（#54 第 2 条）。
NEL = "\x85"


def build_cfg_ic_rows(
    *,
    mesh_rows: Sequence[Sequence[str]],
    river_rows: Sequence[Sequence[str]] | None = None,
    lake_rows: Sequence[Sequence[str]] | None = None,
    header_tokens: Sequence[str] | None = None,
    minute: str = DEFAULT_MINUTE,
    mesh_state_columns: int = MESH_STATE_COLUMNS,
    eol: str = "\n",
    delimiter: str = " ",
    header_delimiter: str | None = None,
    data_delimiters: Sequence[str] | None = None,
    trailing_spaces: str = "",
    header_trailing_spaces: str | None = None,
    blank_lines: bool = False,
    trailing_newline: bool = True,
    mesh_header_tokens: tuple[str, ...] = MESH_COLUMN_HEADER_TOKENS,
    river_header_tokens: tuple[str, ...] = RIVER_COLUMN_HEADER_TOKENS,
    lake_header_tokens: tuple[str, ...] = LAKE_COLUMN_HEADER_TOKENS,
) -> SyntheticCfgIc:
    """生成一份原生分段 `cfg.ic`，**每一行的 token 文本由调用方逐个给出**。

    与 `build_cfg_ic` 的分工：`build_cfg_ic` 按规模批量发，用于 roundtrip / 结构索引；
    本函数让调用方钉死每一格的**文本记法**（负值、`nan`/`inf`、边界值、混合记法），是
    4.2 / 4.4 逐值手算证据的构造入口。每行第一个 token 是元素 id。

    `header_tokens` 为 None 时发 native 三 token 头 `<mesh> <mesh-state-columns> <minute>`；
    给了就逐字发（用于两 token 的 #1197 形状、四 token 兼容布局、>=5 token 未知布局）。
    `header_delimiter` / `header_trailing_spaces` 让 header 行的空白布局独立于数据行——
    脏矩阵要的正是「header 多空格 + 行尾空格 + 数据行 Tab 分隔」这种混排。

    `data_delimiters` 让**数据行行内**的分隔逐位不同（按序循环使用，例如
    `("   ", "\\t")` 发出 `1   0.1\t0.2   0.3\t...`）。存在的理由是判别力：数据行若统一用
    单一分隔符，`"\\t".join(body.split())` 这种 canonical 化回写在字节上与就地 splice **完全
    重合**，裁决 2 的「改动行只替换目标 token 的字节」在负残差路径上就没有任何用例能证伪。
    `mesh_header_tokens` 的作用同 `build_cfg_ic`（把 `Unsat` 挪出索引 4）。
    """
    header_delimiter = delimiter if header_delimiter is None else header_delimiter
    header_trailing_spaces = (
        trailing_spaces if header_trailing_spaces is None else header_trailing_spaces
    )
    texts: list[str] = []
    roles: list[str] = []

    def _join(tokens: Sequence[str], sep: str) -> str:
        if data_delimiters is None:
            return sep.join(tokens)
        parts = [tokens[0]] if tokens else []
        for position, token in enumerate(tokens[1:]):
            parts.append(data_delimiters[position % len(data_delimiters)])
            parts.append(token)
        return "".join(parts)

    def emit(tokens: Sequence[str], role: str, *, sep: str, tail: str) -> int:
        text = _join(tokens, sep) if role == "data" else sep.join(tokens)
        texts.append(text + tail)
        roles.append(role)
        return len(texts) - 1

    def blank() -> None:
        texts.append("")
        roles.append("blank")

    if header_tokens is None:
        header_tokens = (str(len(mesh_rows)), str(mesh_state_columns), minute)
    header_index = emit(
        header_tokens, "header", sep=header_delimiter, tail=header_trailing_spaces
    )
    if blank_lines:
        blank()

    mesh_header_index = emit(
        mesh_header_tokens, "column_header", sep=delimiter, tail=trailing_spaces
    )
    mesh_data_indices = [
        emit(row, "data", sep=delimiter, tail=trailing_spaces) for row in mesh_rows
    ]

    river_header_index: int | None = None
    river_data_indices: list[int] = []
    if river_rows is not None:
        if blank_lines:
            blank()
        river_header_index = emit(
            river_header_tokens, "column_header", sep=delimiter, tail=trailing_spaces
        )
        river_data_indices = [
            emit(row, "data", sep=delimiter, tail=trailing_spaces) for row in river_rows
        ]

    lake_preamble_index: int | None = None
    lake_header_index: int | None = None
    lake_data_indices: list[int] = []
    if lake_rows is not None:
        if blank_lines:
            blank()
        lake_preamble_index = emit(
            (str(len(lake_rows)), str(LAKE_STATE_COLUMNS)),
            "lake_preamble",
            sep=delimiter,
            tail=trailing_spaces,
        )
        lake_header_index = emit(
            lake_header_tokens, "column_header", sep=delimiter, tail=trailing_spaces
        )
        lake_data_indices = [
            emit(row, "data", sep=delimiter, tail=trailing_spaces) for row in lake_rows
        ]

    lines = tuple(text + eol for text in texts)
    if not trailing_newline:
        lines = lines[:-1] + (texts[-1],)

    return SyntheticCfgIc(
        payload="".join(lines).encode("utf-8"),
        lines=lines,
        roles=tuple(roles),
        header_index=header_index,
        mesh_column_header_index=mesh_header_index,
        mesh_data_indices=tuple(mesh_data_indices),
        river_column_header_index=river_header_index,
        river_data_indices=tuple(river_data_indices),
        lake_preamble_index=lake_preamble_index,
        lake_column_header_index=lake_header_index,
        lake_data_indices=tuple(lake_data_indices),
    )


#: mesh 段的列名序列（小写），与 `MESH_COLUMN_HEADER_TOKENS` 的位置一一对应（去掉 Index）。
MESH_VALUE_COLUMNS = ("canopy", "snow", "surface", "unsat", "gw")


def mesh_row(element: int, **overrides: str) -> tuple[str, ...]:
    """一条 mesh 数据行的 token 文本：`Index Canopy Snow Surface Unsat GW`。

    `overrides` 按**列名**（小写）覆写单格文本，故用例可以写
    `mesh_row(1, unsat="-1e-6")` 而不必数到第几个位置——列名是 4.4 投影列定位的真相来源。
    """
    values = dict.fromkeys(MESH_VALUE_COLUMNS, _PLAIN_NOTATION)
    unknown = set(overrides) - set(values)
    if unknown:
        raise AssertionError(f"unknown mesh column(s): {sorted(unknown)}")
    values.update(overrides)
    return (str(element), *(values[name] for name in MESH_VALUE_COLUMNS))


def river_row(element: int, stage: str = _PLAIN_NOTATION) -> tuple[str, ...]:
    """一条 river 数据行的 token 文本：`Index Stage`。"""
    return (str(element), stage)


def with_bom(payload: bytes) -> bytes:
    """在文件最前面贴 UTF-8 BOM（#54 第 4 条的复现输入）。"""
    return UTF8_BOM_BYTES + payload


def inject_nel(payload: bytes, *, physical_line: str, replacement: str) -> bytes:
    """把一条**物理行**换成含 U+0085 的等价文本，使 `splitlines` 把它断成两条逻辑行。

    字节 roundtrip 恒为 True（`render` 只拼接原始行），故这条 fail-open 只能由行数门抓。
    """
    text = payload.decode("utf-8")
    if text.count(physical_line) != 1:
        raise AssertionError(f"expected exactly one occurrence of {physical_line!r}")
    return text.replace(physical_line, replacement).encode("utf-8")
