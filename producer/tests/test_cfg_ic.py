"""`yd_producer.state.cfg_ic` 的行为测试。

oracle 纪律：结构索引期望值一律来自 `cfg_ic_fixtures` 的**构造记录**（生成器发行时登记的
行号与角色），不得由解析器回读；两种 mesh 规模各跑一遍，防止把段区间写成常量而恒真。

判别力纪律：`render(parse(b)) == b` 对逐字模型是平凡真，单靠它证明不了任何东西。真正的
承重条是（1）脏输入矩阵——canonical 化的 writer 只在这里变红；（2）结构索引与逐行角色
oracle——段归属偏移一行、preamble 计入 river 只在这里变红。

包络纪律：合成生成器的发射包络 MUST 覆盖解析器接受域，否则包络外的正确行为分支没有任何
用例把守。已实测过的缺口在此各有专用用例：Tab 分隔（真实生产文件的分隔符）、文件首部空行
（钉死「header 行 = 首个非空行」）、`lake_count=0`（lake 段存在但为空）、river 列头拼写
`Index River_Stage`（**真实 `.cfg.ic.update` 的拼写**）、lake 列头拼写 `Index Lake_Stage`、
段内数据行之间的空行（`data_line_indices` 不连续、`span` 宽于行数）。
"""

from __future__ import annotations

import ast
import inspect
import math
import os
import pathlib
import stat

import pytest
from cfg_ic_fixtures import (
    LAKE_COLUMN_HEADER,
    LAKE_COLUMN_HEADER_TOKENS,
    LAKE_STAGE_COLUMN_HEADER_TOKENS,
    MESH_COLUMN_HEADER,
    RIVER_COLUMN_HEADER,
    RIVER_STAGE_COLUMN_HEADER_TOKENS,
    build_cfg_ic,
    build_compat_layout,
)

from yd_producer.state import cfg_ic

MESH_SIZES = (3, 7)

#: 从 NWM pin 移植的辅助全集：每一个都必须自带溯源注释。
PORTED_HELPERS = (
    "_read_bytes_limited",
    "_header_counts",
    "_numeric_row",
    "_looks_like_column_header",
    "_section_from_column_header",
    "_native_lake_section_preamble",
    "_as_float",
)


def _roles(doc: cfg_ic.CfgIcDocument) -> tuple[str, ...]:
    return tuple(role.value for role in doc.roles)


def _function_source_segments(source: str) -> dict[str, str]:
    """按 `ast` 的函数边界切出每个顶层函数**自己的**源码段（含其内部注释）。"""
    tree = ast.parse(source)
    return {
        node.name: ast.get_source_segment(source, node) or ""
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


# --- roundtrip：干净输入 ---


@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_mesh_river_roundtrip_is_byte_identical(mesh_count: int) -> None:
    built = build_cfg_ic(mesh_count=mesh_count, river_count=mesh_count - 1)
    doc = cfg_ic.parse(built.payload)
    assert cfg_ic.render(doc) == built.payload


@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_mesh_river_section_index_matches_construction(mesh_count: int) -> None:
    river_count = mesh_count - 1
    built = build_cfg_ic(mesh_count=mesh_count, river_count=river_count)
    doc = cfg_ic.parse(built.payload)

    assert doc.header_index == built.header_index == 0
    assert doc.declared_mesh_count == mesh_count
    assert doc.mesh.column_header_index == built.mesh_column_header_index
    assert doc.mesh.data_line_indices == built.mesh_data_indices
    assert doc.mesh.row_count == mesh_count
    assert doc.mesh.span == (
        built.mesh_data_indices[0],
        built.mesh_data_indices[-1] + 1,
    )
    assert doc.river is not None
    assert doc.river.column_header_index == built.river_column_header_index
    assert doc.river.data_line_indices == built.river_data_indices
    assert doc.river.row_count == river_count
    # lake 缺席必须与「lake 段存在但为空」可区分（#9 的结构检查依赖这一点）。
    assert doc.lake is None
    assert doc.lake_preamble_index is None
    assert doc.declared_lake_count is None
    assert _roles(doc) == built.roles


@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_mesh_river_lake_roundtrip_and_index(mesh_count: int) -> None:
    river_count = mesh_count - 1
    lake_count = 2
    built = build_cfg_ic(
        mesh_count=mesh_count, river_count=river_count, lake_count=lake_count
    )
    doc = cfg_ic.parse(built.payload)

    assert cfg_ic.render(doc) == built.payload
    assert doc.mesh.data_line_indices == built.mesh_data_indices
    assert doc.river is not None
    assert doc.river.data_line_indices == built.river_data_indices
    assert doc.lake is not None
    assert doc.lake.data_line_indices == built.lake_data_indices
    assert doc.lake.row_count == lake_count
    assert doc.declared_lake_count == lake_count
    # preamble 是段元数据，不是 river 状态行：既不落在 river 区间内，也不落在 lake 区间内。
    assert doc.lake_preamble_index == built.lake_preamble_index
    assert doc.lake_preamble_index not in doc.river.data_line_indices
    assert doc.lake_preamble_index not in doc.lake.data_line_indices
    assert doc.river.row_count == river_count
    assert _roles(doc) == built.roles
    assert _roles(doc)[built.lake_preamble_index] == "lake_preamble"


def test_river_row_count_is_not_constrained_by_header_second_token() -> None:
    """native header 第二个 token 是 mesh 状态列数，不是 river 元素数。"""
    built = build_cfg_ic(mesh_count=4, river_count=9, mesh_state_columns=6)
    doc = cfg_ic.parse(built.payload)
    assert doc.river is not None
    assert doc.river.row_count == 9
    assert doc.declared_mesh_count == 4
    assert doc.lines[0].split()[1] == "6"


# --- roundtrip：脏输入矩阵（判别力承重条） ---


DIRTY_CASES = {
    "crlf": {"eol": "\r\n"},
    "tabs": {"delimiter": "\t"},
    "tabs_crlf_trailing_spaces": {
        "delimiter": "\t",
        "eol": "\r\n",
        "trailing_spaces": True,
    },
    "leading_blank_lines": {"leading_blank_lines": 2},
    "trailing_spaces": {"trailing_spaces": True},
    "blank_lines": {"blank_lines": True},
    "mixed_notation": {"mixed_notation": True},
    "no_trailing_newline": {"trailing_newline": False},
}


@pytest.mark.parametrize("name", sorted(DIRTY_CASES))
@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_dirty_inputs_roundtrip_byte_identical(name: str, mesh_count: int) -> None:
    built = build_cfg_ic(
        mesh_count=mesh_count,
        river_count=2,
        lake_count=1,
        **DIRTY_CASES[name],
    )
    doc = cfg_ic.parse(built.payload)
    assert cfg_ic.render(doc) == built.payload


def test_mixed_notation_tokens_survive_verbatim() -> None:
    """记法必须逐字存活：`0.100000` / `1e-3` / `-0.0` / `2.5E+01` 一个都不许被规范化。"""
    built = build_cfg_ic(mesh_count=3, river_count=2, lake_count=1, mixed_notation=True)
    text = built.payload.decode("utf-8")
    for token in ("0.100000", "1e-3", "-0.0", "2.5E+01"):
        assert token in text
    rendered = cfg_ic.render(cfg_ic.parse(built.payload)).decode("utf-8")
    for token in ("0.100000", "1e-3", "-0.0", "2.5E+01"):
        assert token in rendered
    assert rendered == text


@pytest.mark.parametrize("delimiter", [" ", "\t"])
@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_combined_dirty_input_keeps_full_section_index(
    mesh_count: int, delimiter: str
) -> None:
    """脏输入不得降级为「只保字节、不分段」：叠加脏例必须跑完整段索引 oracle。"""
    built = build_cfg_ic(
        mesh_count=mesh_count,
        river_count=3,
        lake_count=2,
        delimiter=delimiter,
        eol="\r\n",
        trailing_spaces=True,
        blank_lines=True,
    )
    # 发射包络自检：分隔符轴不得是哑参数（否则 Tab 分支的用例全是空转）。
    assert (b"\t" in built.payload) is (delimiter == "\t")
    doc = cfg_ic.parse(built.payload)

    assert cfg_ic.render(doc) == built.payload
    assert doc.header_index == built.header_index
    assert doc.mesh.column_header_index == built.mesh_column_header_index
    assert doc.mesh.data_line_indices == built.mesh_data_indices
    assert doc.river is not None
    assert doc.river.column_header_index == built.river_column_header_index
    assert doc.river.data_line_indices == built.river_data_indices
    assert doc.lake is not None
    assert doc.lake.column_header_index == built.lake_column_header_index
    assert doc.lake.data_line_indices == built.lake_data_indices
    assert doc.lake_preamble_index == built.lake_preamble_index
    assert _roles(doc) == built.roles
    # 空行确实存在且被单独归属，没有被塞进任何段。
    assert "blank" in _roles(doc)
    blank_indices = {i for i, role in enumerate(_roles(doc)) if role == "blank"}
    assert blank_indices
    assert blank_indices.isdisjoint(doc.mesh.data_line_indices)
    assert blank_indices.isdisjoint(doc.river.data_line_indices)
    assert blank_indices.isdisjoint(doc.lake.data_line_indices)


# --- 发射包络：解析器接受什么，生成器就必须能发什么 ---


@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_tab_delimited_native_layout_roundtrips_and_indexes(mesh_count: int) -> None:
    """真实 native `cfg.ic` 是 Tab 分隔（NWM pin 的 `_write_native_ic` 逐字为证）。

    空格分隔的合成文件对「render 把 `\\t` 归一为单个空格」的实现全绿，而那种实现会逐字节
    损坏每一个生产文件。故 Tab 轴必须既跑字节等价、也跑完整段索引 oracle。
    """
    river_count = mesh_count - 1
    built = build_cfg_ic(
        mesh_count=mesh_count,
        river_count=river_count,
        lake_count=2,
        delimiter="\t",
    )
    # 载荷里真的有 Tab，且没有被生成器悄悄换成空格。
    assert b"\t" in built.payload
    assert b"Index\tLakeStage" in built.payload
    assert built.payload.startswith(f"{mesh_count}\t6\t".encode())

    doc = cfg_ic.parse(built.payload)

    rendered = cfg_ic.render(doc)
    assert rendered == built.payload
    assert rendered.count(b"\t") == built.payload.count(b"\t")
    assert doc.header_index == built.header_index
    assert doc.mesh.column_header_index == built.mesh_column_header_index
    assert doc.mesh.data_line_indices == built.mesh_data_indices
    assert doc.river is not None
    assert doc.river.data_line_indices == built.river_data_indices
    assert doc.lake is not None
    assert doc.lake.data_line_indices == built.lake_data_indices
    assert doc.lake_preamble_index == built.lake_preamble_index
    assert _roles(doc) == built.roles


@pytest.mark.parametrize("mesh_count", MESH_SIZES)
@pytest.mark.parametrize(
    ("lake_header_tokens", "case"),
    [
        (LAKE_COLUMN_HEADER_TOKENS, "qhh"),
        (LAKE_STAGE_COLUMN_HEADER_TOKENS, "underscored-lake"),
    ],
)
def test_underscored_stage_column_headers_roundtrip_and_index(
    mesh_count: int, lake_header_tokens: tuple[str, ...], case: str
) -> None:
    """`Index\\tRiver_Stage` 是真实 `.cfg.ic.update` 的 river 列头拼写。

    NWM pin 的 QHH 布局 fixture（`tests/test_state_qc.py` :96/:154/:187）与 checkpoint
    断言（`tests/test_shud_runtime.py` :518/:549）都写这个拼写；只有 pin 的**合成** writer
    `_write_native_ic` :611 用 `Index\\tStage`。生成器此前只会发合成拼写，于是「解析器认
    `river_stage`」这一支无人把守：删掉该 token 后整套用例仍全绿，而真实生产文件会以
    `non-numeric IC data row: 'Index\\tRiver_Stage'` 直接解析失败。
    `Lake_Stage` 同属一类（解析器接受、pin 无实例），在同一条轴上一并覆盖。
    """
    river_count = mesh_count - 1
    built = build_cfg_ic(
        mesh_count=mesh_count,
        river_count=river_count,
        lake_count=2,
        delimiter="\t",
        river_header_tokens=RIVER_STAGE_COLUMN_HEADER_TOKENS,
        lake_header_tokens=lake_header_tokens,
    )
    assert b"Index\tRiver_Stage" in built.payload
    assert ("\t".join(lake_header_tokens)).encode() in built.payload
    assert b"Index\tStage\n" not in built.payload

    doc = cfg_ic.parse(built.payload)

    assert cfg_ic.render(doc) == built.payload
    assert doc.header_index == built.header_index
    assert doc.mesh.column_header_index == built.mesh_column_header_index
    assert doc.mesh.data_line_indices == built.mesh_data_indices
    assert doc.river is not None
    assert doc.river.column_header_index == built.river_column_header_index
    assert doc.river.data_line_indices == built.river_data_indices
    assert doc.river.row_count == river_count
    assert doc.lake is not None
    assert doc.lake.column_header_index == built.lake_column_header_index
    assert doc.lake.data_line_indices == built.lake_data_indices
    assert doc.lake.row_count == 2
    assert doc.declared_lake_count == 2
    assert doc.lake_preamble_index == built.lake_preamble_index
    assert _roles(doc) == built.roles


@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_intra_section_blank_lines_keep_data_indices_non_contiguous(
    mesh_count: int,
) -> None:
    """段内数据行之间可以夹空行：`data_line_indices` 因此不是连续区间。

    `Section` 的文档承诺这一点，但生成器此前只在段与段之间插空行，于是「`span` 由首尾行号
    定」这一支无人把守——把 `span` 写成 `(first, first + len(indices))` 全绿。
    """
    built = build_cfg_ic(
        mesh_count=mesh_count,
        river_count=3,
        lake_count=2,
        intra_section_blank_lines=True,
    )
    # 空行确实插进去了（旗标没有静默失效）：三段的首两条数据行号都不相邻。
    for indices in (
        built.mesh_data_indices,
        built.river_data_indices,
        built.lake_data_indices,
    ):
        assert indices[1] - indices[0] == 2

    doc = cfg_ic.parse(built.payload)

    assert cfg_ic.render(doc) == built.payload
    assert _roles(doc) == built.roles
    sections = ((doc.mesh, built.mesh_data_indices),)
    assert doc.river is not None
    assert doc.lake is not None
    sections += (
        (doc.river, built.river_data_indices),
        (doc.lake, built.lake_data_indices),
    )
    for section, expected in sections:
        assert section.data_line_indices == expected
        assert section.row_count == len(expected)
        # span 含首尾，且因段内空行而**宽于**数据行数——这正是把 span 写成
        # `(first, first + row_count)` 时唯一会变红的地方。
        assert section.span == (expected[0], expected[-1] + 1)
        assert section.span[1] - section.span[0] == len(expected) + 1
    blank_indices = {i for i, role in enumerate(_roles(doc)) if role == "blank"}
    assert blank_indices
    for _, expected in sections:
        assert blank_indices.intersection(range(expected[0], expected[-1] + 1))


@pytest.mark.parametrize("leading", [1, 2])
def test_header_is_the_first_non_blank_line(leading: int) -> None:
    """「header 行 = 首个非空行」：文件首部的空行不得被当成 header。

    `leading == 2` 时第二条是纯空白行（`"   "`），一并覆盖 whitespace-only 首行。
    """
    built = build_cfg_ic(
        mesh_count=3,
        river_count=2,
        lake_count=1,
        leading_blank_lines=leading,
    )
    assert built.header_index == leading
    assert built.lines[0].strip() == ""

    doc = cfg_ic.parse(built.payload)

    assert doc.header_index == leading
    assert doc.roles[0] is cfg_ic.LineRole.BLANK
    assert all(role is cfg_ic.LineRole.BLANK for role in doc.roles[:leading])
    assert doc.roles[leading] is cfg_ic.LineRole.HEADER
    assert doc.declared_mesh_count == 3
    assert doc.mesh.data_line_indices == built.mesh_data_indices
    assert _roles(doc) == built.roles
    assert cfg_ic.render(doc) == built.payload


def test_empty_lake_section_is_distinguishable_from_absent_lake() -> None:
    """`lake_count=0`：lake 段存在但为空，在接受域内（pin 只拒 `lake_count < 0`）。

    #9 的结构检查依赖「lake 缺席（`doc.lake is None`）」与「lake 段空」可区分。
    river_count 必须 >= 1：preamble 识别只在 river 分支里做，没有 river 段时 `0 2` 行会
    被当成 mesh 数据行（多余 mesh 行 -> 报错），那是另一条语义。
    """
    built = build_cfg_ic(mesh_count=3, river_count=2, lake_count=0)
    assert built.lake_data_indices == ()
    assert built.lake_preamble_index is not None

    doc = cfg_ic.parse(built.payload)

    assert doc.lake is not None
    assert doc.lake.row_count == 0
    assert doc.lake.rows == ()
    assert doc.lake.span is None
    assert doc.lake.data_line_indices == ()
    assert doc.lake.column_header_index == built.lake_column_header_index
    assert doc.declared_lake_count == 0
    assert doc.lake_preamble_index == built.lake_preamble_index
    assert _roles(doc) == built.roles
    assert cfg_ic.render(doc) == built.payload

    # 与「lake 段整体缺席」对照：那时 lake 相关字段全为 None。
    absent = cfg_ic.parse(build_cfg_ic(mesh_count=3, river_count=2).payload)
    assert absent.lake is None
    assert absent.declared_lake_count is None
    assert absent.lake_preamble_index is None


# --- 全覆盖划分 ---


@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_every_line_has_exactly_one_role(mesh_count: int) -> None:
    built = build_cfg_ic(
        mesh_count=mesh_count,
        river_count=2,
        lake_count=1,
        blank_lines=True,
        trailing_spaces=True,
    )
    doc = cfg_ic.parse(built.payload)
    assert len(doc.roles) == len(doc.lines)

    owned: list[int] = [doc.header_index]
    for section in (doc.mesh, doc.river, doc.lake):
        assert section is not None
        owned.append(section.column_header_index)
        owned.extend(section.data_line_indices)
    assert doc.lake_preamble_index is not None
    owned.append(doc.lake_preamble_index)
    owned.extend(i for i, role in enumerate(doc.roles) if role is cfg_ic.LineRole.BLANK)

    assert sorted(owned) == list(range(len(doc.lines)))
    assert len(set(owned)) == len(owned)


# --- 数值视图（供 #9 的只读派生） ---


def test_numeric_view_is_derived_not_the_render_source() -> None:
    """逐值断言（不是只断形状）+ 真的调一次 `render`。

    期望值手算自 `cfg_ic_fixtures` 的记法池 `("0.100000", "1e-3", "-0.0", "2.5E+01",
    "0.000000")`：mesh 每行 = 元素号 + 5 个循环取值，river 每行 = 元素号 + 1 个取值
    （river 首行取池中第 15 个 == 第 0 个）。只断 `len()`/`isinstance(float)` 的版本对
    「三处 `append` 全换成全零元组」的实现全绿，而 #9 的负残差处理正消费这个视图。
    """
    built = build_cfg_ic(mesh_count=3, river_count=2, mixed_notation=True)
    doc = cfg_ic.parse(built.payload)

    assert doc.mesh.rows == (
        (1.0, 0.1, 0.001, -0.0, 25.0, 0.0),
        (2.0, 0.1, 0.001, -0.0, 25.0, 0.0),
        (3.0, 0.1, 0.001, -0.0, 25.0, 0.0),
    )
    assert doc.river is not None
    assert doc.river.rows == ((1.0, 0.1), (2.0, 0.001))
    # `-0.0 == 0.0` 为真：元组相等断不出负零，符号位另断。
    assert all(math.copysign(1.0, row[3]) == -1.0 for row in doc.mesh.rows)
    assert all(isinstance(value, float) for row in doc.mesh.rows for value in row)

    # 数值视图是派生物，不是回写来源：原始记法在 render 后仍逐字存活。
    rendered = cfg_ic.render(doc)
    assert rendered == built.payload
    text = rendered.decode("utf-8")
    assert "2.5E+01" in text and "1e-3" in text and "-0.0" in text
    assert "25.0 " not in text


# --- fail-closed：解析级 ---


def test_missing_path_raises_value_error(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse(tmp_path / "absent.cfg.ic")
    assert not isinstance(excinfo.value, OSError)


def test_directory_path_raises_value_error(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse(tmp_path)
    assert not isinstance(excinfo.value, OSError)


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root 无视文件权限位"
)
def test_unreadable_path_raises_value_error(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "locked.cfg.ic"
    build_cfg_ic(mesh_count=3, river_count=2).write(target)
    target.chmod(0o000)
    try:
        with pytest.raises(ValueError) as excinfo:
            cfg_ic.parse(target)
    finally:
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert not isinstance(excinfo.value, OSError)


def test_empty_file_raises_value_error(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "empty.cfg.ic"
    target.write_bytes(b"")
    with pytest.raises(ValueError, match="empty IC file"):
        cfg_ic.parse(target)


def test_whitespace_only_file_raises_value_error() -> None:
    with pytest.raises(ValueError, match="empty IC file"):
        cfg_ic.parse(b"\n   \n\r\n")


def test_non_utf8_bytes_raise_value_error_not_unicode_decode_error(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "binary.cfg.ic"
    target.write_bytes(b"\xff\xfe\x00\x01\x02\x03\xff\xfe")
    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse(target)
    # UnicodeDecodeError 本身是 ValueError 子类：不显式排除的话，缺了封装也照样绿。
    assert not isinstance(excinfo.value, UnicodeDecodeError)
    assert "UTF-8" in str(excinfo.value)


def test_unreadable_header_raises_value_error() -> None:
    payload = b"not a header\n" + build_cfg_ic(mesh_count=3, river_count=2).payload
    with pytest.raises(ValueError, match="unreadable IC header"):
        cfg_ic.parse(payload)


def test_non_numeric_data_row_raises_value_error() -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2)
    text = built.payload.decode("utf-8")
    corrupted = text.replace(built.lines[built.mesh_data_indices[1]], "1 x y z w v\n")
    with pytest.raises(ValueError, match="non-numeric IC data row"):
        cfg_ic.parse(corrupted.encode("utf-8"))


def test_truncated_mesh_body_raises_value_error() -> None:
    built = build_cfg_ic(mesh_count=5, river_count=2)
    kept = [
        line
        for index, line in enumerate(built.lines)
        if index not in set(built.mesh_data_indices[-2:])
    ]
    with pytest.raises(ValueError, match="truncated sectioned IC body"):
        cfg_ic.parse("".join(kept).encode("utf-8"))


def test_surplus_mesh_row_raises_instead_of_being_dropped() -> None:
    """刻意偏离 NWM pin：pin 静默丢弃超出声明数的 mesh 行，格式保真根必须报错。"""
    built = build_cfg_ic(mesh_count=3, river_count=2)
    lines = list(built.lines)
    lines.insert(built.mesh_data_indices[-1] + 1, "99 1.0 1.0 1.0 1.0 1.0\n")
    with pytest.raises(ValueError, match="surplus sectioned IC mesh row"):
        cfg_ic.parse("".join(lines).encode("utf-8"))


def test_data_row_before_any_column_header_raises() -> None:
    """刻意偏离 NWM pin：pin 让分段列头之前的数值行静默落空。"""
    built = build_cfg_ic(mesh_count=3, river_count=2)
    lines = list(built.lines)
    lines.insert(1, "42 1.0 1.0 1.0 1.0 1.0\n")
    with pytest.raises(ValueError, match="precedes any section column header"):
        cfg_ic.parse("".join(lines).encode("utf-8"))


def test_truncated_lake_body_contradicts_preamble() -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2, lake_count=3, lake_body_rows=1)
    with pytest.raises(ValueError, match="truncated sectioned IC lake body"):
        cfg_ic.parse(built.payload)


def test_compat_counts_layout_is_rejected() -> None:
    """不静默支持两种布局：无分段列头的计数式兼容布局必须 fail-closed。"""
    payload = build_compat_layout(mesh_count=3, river_count=2)
    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse(payload)
    assert "原生分段" in str(excinfo.value)


def test_failure_returns_no_partial_document() -> None:
    built = build_cfg_ic(mesh_count=4, river_count=2)
    kept = [
        line
        for index, line in enumerate(built.lines)
        if index != built.mesh_data_indices[-1]
    ]
    result = None
    try:
        result = cfg_ic.parse("".join(kept).encode("utf-8"))
    except ValueError:
        pass
    assert result is None


# --- 字节上界 ---


def test_payload_exactly_at_injected_bound_parses(tmp_path: pathlib.Path) -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2)
    target = built.write(tmp_path / "bound.cfg.ic")
    size = len(built.payload)
    doc = cfg_ic.parse(target, max_bytes=size)
    assert cfg_ic.render(doc) == built.payload


def test_payload_one_byte_over_injected_bound_raises(tmp_path: pathlib.Path) -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2)
    target = built.write(tmp_path / "bound.cfg.ic")
    size = len(built.payload)
    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse(target, max_bytes=size - 1)
    assert "exceeds size limit" in str(excinfo.value)
    assert str(size - 1) in str(excinfo.value)


def test_bound_is_enforced_before_unbounded_read(tmp_path: pathlib.Path) -> None:
    """有界读：超限文件最多只读进 max_bytes + 1 字节，不整份 slurp 进内存。"""
    built = build_cfg_ic(mesh_count=200, river_count=50)
    target = built.write(tmp_path / "big.cfg.ic")
    assert len(built.payload) > 5000

    data = cfg_ic._read_bytes_limited(target, max_bytes=64)
    assert len(data) == 65

    with pytest.raises(ValueError, match="exceeds size limit"):
        cfg_ic.parse(target, max_bytes=64)


@pytest.mark.parametrize("max_bytes", [-1, -2, -100])
def test_negative_bound_is_rejected_before_any_read(
    tmp_path: pathlib.Path, max_bytes: int
) -> None:
    """`max_bytes` 为负 -> 抛 `ValueError`，且在任何读取**之前**。

    没有前置校验时 `handle.read(max_bytes + 1)` 在 `max_bytes == -2` 会退化成
    `read(-1)`，把整个文件读进内存，随后 `len(data) > max_bytes` 照样抛错——那次无界读
    长得和一次正常拒绝一模一样。顺序证明：对一个**根本不存在**的路径传负上界，若校验在读
    之后，拿到的会是「无法读取」的封装错误而不是这条。
    """
    built = build_cfg_ic(mesh_count=3, river_count=2)
    target = built.write(tmp_path / "bound.cfg.ic")

    with pytest.raises(ValueError, match="max_bytes must be non-negative"):
        cfg_ic.parse(target, max_bytes=max_bytes)

    absent = tmp_path / "absent.cfg.ic"
    with pytest.raises(ValueError, match="max_bytes must be non-negative") as excinfo:
        cfg_ic.parse(absent, max_bytes=max_bytes)
    assert "无法读取" not in str(excinfo.value)

    # bytes 入口同样先验后用（同一条前置校验，不分来源）。
    with pytest.raises(ValueError, match="max_bytes must be non-negative"):
        cfg_ic.parse(built.payload, max_bytes=max_bytes)


def test_zero_bound_is_a_valid_bound_and_rejects_via_the_normal_path(
    tmp_path: pathlib.Path,
) -> None:
    """上界 0 是合法输入（非负），走的是正常的超限拒绝路径。"""
    built = build_cfg_ic(mesh_count=3, river_count=2)
    target = built.write(tmp_path / "bound.cfg.ic")
    with pytest.raises(ValueError, match="exceeds size limit of 0 bytes"):
        cfg_ic.parse(target, max_bytes=0)


def test_default_bound_matches_the_nwm_pin_constant() -> None:
    assert cfg_ic.MAX_STATE_IC_BYTES == 64 * 1024 * 1024
    default = inspect.signature(cfg_ic.parse).parameters["max_bytes"].default
    assert default == cfg_ic.MAX_STATE_IC_BYTES


# --- 溯源与隔离 ---


def test_module_carries_nwm_provenance_and_stays_db_free() -> None:
    source = pathlib.Path(cfg_ic.__file__).read_text(encoding="utf-8")
    assert "NWM@8ae9b8f2 packages/common/state_qc.py" in source
    for forbidden in (
        "import packages",
        "from packages",
        "psycopg",
        "sqlalchemy",
        "DATABASE_URL",
        "sacct",
        "sbatch",
    ):
        assert forbidden not in source
    # 移植辅助逐函数带溯源头。窗口 MUST 按**函数边界**取（`ast` 的源码段），不能用定长
    # 切片：定长窗口会越进下一个函数，于是一个辅助可以被**邻居的**溯源注释满足，删掉它
    # 自己那行注释也照样绿。
    segments = _function_source_segments(source)
    for helper in PORTED_HELPERS:
        assert helper in segments, helper
        assert "NWM@8ae9b8f2 packages/common/state_qc.py" in segments[helper], helper


def test_provenance_windows_do_not_leak_into_neighbour_functions() -> None:
    """取窗自身的守卫：每个辅助的窗口里恰好只有自己那一条溯源标记。"""
    source = pathlib.Path(cfg_ic.__file__).read_text(encoding="utf-8")
    segments = _function_source_segments(source)
    for helper in PORTED_HELPERS:
        assert (
            segments[helper].count("NWM@8ae9b8f2 packages/common/state_qc.py") == 1
        ), helper


def test_module_documents_the_deliberate_deviations() -> None:
    source = pathlib.Path(cfg_ic.__file__).read_text(encoding="utf-8")
    head = source[: source.index('"""', 3) + 3]
    assert "刻意偏离" in head
    assert "OSError" in head and "ValueError" in head
    assert "mesh" in head
    # 偏离清单自称是全集，所以每一条 pin 无对应物、且可被输入触发的 fail-closed 都必须在
    # 清单里点名（`parse` 末尾的 unassigned 自检对任何输入都不可达，是不变量断言，不计入）。
    assert "六条" in head
    assert "mesh 列头" in head
    assert "max_bytes" in head
    assert "计数式兼容布局" in head
    for ordinal in ("\n1. ", "\n2. ", "\n3. ", "\n4. ", "\n5. ", "\n6. "):
        assert head.count(ordinal) == 1, ordinal
    assert "\n7. " not in head


# --- 移植辅助的判定语义（与 pin 逐字一致） ---


def test_column_header_detection_matches_pin_semantics() -> None:
    assert cfg_ic._looks_like_column_header(MESH_COLUMN_HEADER)
    assert cfg_ic._looks_like_column_header(RIVER_COLUMN_HEADER)
    assert cfg_ic._looks_like_column_header(LAKE_COLUMN_HEADER)
    assert not cfg_ic._looks_like_column_header("1 0.1 0.2 0.3 0.4 0.5")
    assert not cfg_ic._looks_like_column_header("")
    assert not cfg_ic._looks_like_column_header("Index")


def test_section_from_column_header_disambiguates_stage_sections() -> None:
    assert (
        cfg_ic._section_from_column_header(MESH_COLUMN_HEADER, stage_section_count=0)
        == "mesh"
    )
    assert (
        cfg_ic._section_from_column_header(RIVER_COLUMN_HEADER, stage_section_count=0)
        == "river"
    )
    assert (
        cfg_ic._section_from_column_header(RIVER_COLUMN_HEADER, stage_section_count=1)
        == "lake"
    )
    assert (
        cfg_ic._section_from_column_header(LAKE_COLUMN_HEADER, stage_section_count=1)
        == "lake"
    )


def test_lake_preamble_requires_an_immediately_following_lake_header() -> None:
    assert (
        cfg_ic._native_lake_section_preamble(
            "1 2", next_line=LAKE_COLUMN_HEADER, stage_section_count=1
        )
        == 1
    )
    # 后继列头开启的不是 lake 段（首个 Stage 段即 river）时，不得判为段元数据。
    assert (
        cfg_ic._native_lake_section_preamble(
            "1 2", next_line=RIVER_COLUMN_HEADER, stage_section_count=0
        )
        is None
    )
    # 后继行不是列头（普通 river 数据行）时同样不是段元数据。
    assert (
        cfg_ic._native_lake_section_preamble(
            "1 2", next_line="2 0.350000", stage_section_count=1
        )
        is None
    )
    assert (
        cfg_ic._native_lake_section_preamble(
            "1 2", next_line=None, stage_section_count=1
        )
        is None
    )
    assert (
        cfg_ic._native_lake_section_preamble(
            "1 2 3", next_line=LAKE_COLUMN_HEADER, stage_section_count=1
        )
        is None
    )
    assert (
        cfg_ic._native_lake_section_preamble(
            "1 0", next_line=LAKE_COLUMN_HEADER, stage_section_count=1
        )
        is None
    )
