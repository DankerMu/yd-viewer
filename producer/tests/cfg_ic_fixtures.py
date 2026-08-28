"""合成 `cfg.ic` 生成器（程序化，零二进制入库）。

独立性硬约束：本模块 MUST NOT 从 `yd_producer.state` import 任何东西。结构索引期望值由
**构造过程**记录（生成器知道自己发了哪几行、每行是什么），而不是由被测解析器回读——否则
段归属断言退化为「解析器与自己一致」的永真式。

覆盖的「脏」维度：CRLF 行尾、行尾多余空格、段间空行（含全空白行）、数值记法混合
（`0.100000` / `1e-3` / `-0.0` / `2.5E+01`）、文件无末尾换行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# 与 SHUD 原生分段布局一致的列头文本。
MESH_COLUMN_HEADER = "Index Canopy Snow Surface Unsat GW"
RIVER_COLUMN_HEADER = "Index Stage"
LAKE_COLUMN_HEADER = "Index LakeStage"

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
    trailing_spaces: str
    texts: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)

    def emit(self, text: str, role: str) -> int:
        self.texts.append(text + self.trailing_spaces)
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
    trailing_newline: bool = True,
    trailing_spaces: bool = False,
    blank_lines: bool = False,
    mixed_notation: bool = False,
    minute: str = DEFAULT_MINUTE,
    mesh_state_columns: int = MESH_STATE_COLUMNS,
    lake_body_rows: int | None = None,
) -> SyntheticCfgIc:
    """生成一份原生分段 `cfg.ic`。

    `lake_count is None` 表示不含 lake 段；`lake_body_rows` 用于制造「preamble 声明数与实际
    lake 行数不符」的截断体（默认等于 `lake_count`）。
    """
    emitter = _Emitter(eol=eol, trailing_spaces="  " if trailing_spaces else "")
    counter = 0

    header_index = emitter.emit(f"{mesh_count} {mesh_state_columns} {minute}", "header")
    if blank_lines:
        emitter.blank()

    mesh_header_index = emitter.emit(MESH_COLUMN_HEADER, "column_header")
    mesh_data_indices: list[int] = []
    for element in range(1, mesh_count + 1):
        values = []
        for _ in range(mesh_state_columns - 1):
            values.append(_value(counter, mixed=mixed_notation))
            counter += 1
        mesh_data_indices.append(emitter.emit(f"{element} " + " ".join(values), "data"))

    river_header_index: int | None = None
    river_data_indices: list[int] = []
    if river_count > 0:
        if blank_lines:
            emitter.blank(whitespace=True)
        river_header_index = emitter.emit(RIVER_COLUMN_HEADER, "column_header")
        for element in range(1, river_count + 1):
            stage = _value(counter, mixed=mixed_notation)
            counter += 1
            river_data_indices.append(emitter.emit(f"{element} {stage}", "data"))

    lake_preamble_index: int | None = None
    lake_header_index: int | None = None
    lake_data_indices: list[int] = []
    if lake_count is not None:
        if blank_lines:
            emitter.blank()
        lake_preamble_index = emitter.emit(
            f"{lake_count} {LAKE_STATE_COLUMNS}", "lake_preamble"
        )
        lake_header_index = emitter.emit(LAKE_COLUMN_HEADER, "column_header")
        body_rows = lake_count if lake_body_rows is None else lake_body_rows
        for element in range(1, body_rows + 1):
            stage = _value(counter, mixed=mixed_notation)
            counter += 1
            lake_data_indices.append(emitter.emit(f"{element} {stage}", "data"))

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
