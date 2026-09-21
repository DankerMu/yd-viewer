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
import dataclasses
import inspect
import math
import os
import pathlib
import stat

import pytest
import source_probe
from cfg_ic_fixtures import (
    LAKE_COLUMN_HEADER,
    LAKE_COLUMN_HEADER_TOKENS,
    LAKE_STAGE_COLUMN_HEADER_TOKENS,
    MESH_COLUMN_HEADER,
    RIVER_COLUMN_HEADER,
    RIVER_STAGE_COLUMN_HEADER_TOKENS,
    build_cfg_ic,
    build_cfg_ic_rows,
    build_compat_layout,
    mesh_row,
    with_bom,
)

from yd_producer.state import cfg_ic

MESH_SIZES = (3, 7)


@pytest.fixture
def tmp_path(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path.resolve()


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
    # river 段前导（真实 `yd.cfg.ic` 的布局，#305）：单独一条、叠加空行、与 lake 前导共存。
    "river_preamble": {"river_preamble": True, "lake_count": None},
    "river_preamble_blank_lines": {
        "river_preamble": True,
        "blank_lines": True,
        "lake_count": None,
    },
    "river_preamble_lake": {"river_preamble": True},
}

#: 脏矩阵的公共规模参数；个别脏例（如无 lake 的 river 前导布局）按名覆盖它们。
DIRTY_DEFAULTS = {"river_count": 2, "lake_count": 1}


@pytest.mark.parametrize("name", sorted(DIRTY_CASES))
@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_dirty_inputs_roundtrip_byte_identical(name: str, mesh_count: int) -> None:
    built = build_cfg_ic(
        mesh_count=mesh_count,
        **{**DIRTY_DEFAULTS, **DIRTY_CASES[name]},  # type: ignore[arg-type]
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


@pytest.mark.parametrize("river_preamble", [False, True])
@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_every_line_has_exactly_one_role(mesh_count: int, river_preamble: bool) -> None:
    built = build_cfg_ic(
        mesh_count=mesh_count,
        river_count=2,
        lake_count=1,
        blank_lines=True,
        trailing_spaces=True,
        river_preamble=river_preamble,
    )
    doc = cfg_ic.parse(built.payload)
    assert len(doc.roles) == len(doc.lines)

    owned: list[int] = [doc.header_index]
    for section in (doc.mesh, doc.river, doc.lake):
        assert section is not None
        owned.append(section.column_header_index)
        owned.extend(section.data_line_indices)
    assert (doc.river_preamble_index is not None) is river_preamble
    if doc.river_preamble_index is not None:
        owned.append(doc.river_preamble_index)
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


def test_symlink_leaf_path_raises_value_error(tmp_path: pathlib.Path) -> None:
    root = tmp_path.resolve()
    built = build_cfg_ic(mesh_count=3, river_count=2)
    real = built.write(root / "real.cfg.ic")
    link = root / "link.cfg.ic"
    link.symlink_to(real)
    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse(link)
    assert not isinstance(excinfo.value, OSError)
    assert os.readlink(link) == str(real)
    assert real.read_bytes() == built.payload


def test_symlink_ancestor_path_raises_value_error(tmp_path: pathlib.Path) -> None:
    root = tmp_path.resolve()
    built = build_cfg_ic(mesh_count=3, river_count=2)
    real_dir = root / "real"
    real_dir.mkdir()
    real = built.write(real_dir / "state.cfg.ic")
    alias = root / "alias"
    alias.symlink_to(real_dir, target_is_directory=True)
    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse(alias / "state.cfg.ic")
    assert not isinstance(excinfo.value, OSError)
    assert real.read_bytes() == built.payload


def test_bytes_like_input_still_parses_without_filesystem() -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2)
    doc = cfg_ic.parse(bytearray(built.payload))
    assert cfg_ic.render(doc) == built.payload


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


#: `parse` 体内每一条 `raise` 的分类表。总数 MUST 与 `ast` 数出来的 `raise` 节点数闭合，
#: 故往 `parse` 里加一条 fail-closed 而不更新模块头清单会**立刻变红**。
#:
#: 这张表取代了原来的自指写法（#54 评论 2）：旧版只断 docstring 写着「六条」、从代码零导出，
#: 于是 docstring 写「三条」时它绿（第 4 条偏离当时确实存在）、写「五条」时它仍绿（第 6 条
#: 仍在清单外）——对「偏离清单漏登记」这一类恒绿，而那正是它本该守住的东西。
PARSE_RAISE_CLASSIFICATION = {
    # 模块头逐条登记的、对 pin 的刻意偏离（无 pin 对应物的 fail-closed）。
    "deliberate_deviations": 9,
    # 有 pin 对应物：超限 / 非 UTF-8 / 空文件 / 不可读 header / 非数值数据行 /
    # 截断 body / 截断 lake body。
    "pin_counterparts": 7,
    # `pragma: no cover` 的全覆盖划分自检：对任何输入都不可达，是不变量断言。
    "unreachable_invariant": 1,
}

#: 文档改写 API 的拒绝路径（模块头「模型扩展」一节登记，不计入上面的九条偏离）。
DOCUMENT_API_RAISE_COUNTS = {
    # roles/lines 长度、header_index、段列头行号、段数据行号、river preamble 行号、
    # lake preamble 行号。
    "CfgIcDocument.__post_init__": 6,
    # 行号非 int、行号越界、替换值含断行字符、被替换的数据行重算不出数值。
    "CfgIcDocument.with_replaced_lines": 4,
}


def test_module_documents_the_deliberate_deviations() -> None:
    source = pathlib.Path(cfg_ic.__file__).read_text(encoding="utf-8")
    head = source[: source.index('"""', 3) + 3]
    assert "刻意偏离" in head
    assert "OSError" in head and "ValueError" in head
    assert "mesh" in head
    # 偏离清单自称是全集，所以每一条 pin 无对应物、且可被输入触发的 fail-closed 都必须在
    # 清单里点名（`parse` 末尾的 unassigned 自检对任何输入都不可达，是不变量断言，不计入）。
    assert "mesh 列头" in head
    assert "max_bytes" in head
    assert "计数式兼容布局" in head
    assert "段重入" in head or "分段列头第二次出现" in head
    assert "BOM" in head
    # 声明的条数由 docstring **解析**得出（不是 `"八条" in head` 那种子串断言——实测该写法
    # 对「八条改回六条」的变异体存活，因为后文「故不计入上面的八条偏离」也含「八条」），
    # 并与代码侧的分类表闭合；分类表又与 `ast` 计数闭合（见下一条）。
    declared = source_probe.declared_deviation_count(head)
    assert declared == PARSE_RAISE_CLASSIFICATION["deliberate_deviations"] == 9
    for ordinal in range(1, declared + 1):
        assert head.count(f"\n{ordinal}. ") == 1, ordinal
    assert f"\n{declared + 1}. " not in head


def test_deviation_list_is_closed_against_the_actual_raise_count() -> None:
    """穷尽性的机械闭合：`parse` 体内 `raise` 总数 == 分类表之和。

    往 `parse` 里加一条 `raise ValueError` 而不回来更新模块头清单与分类表，此条变红。
    """
    source = source_probe.read_source(cfg_ic.__file__)

    assert source_probe.count_raises(source, "parse") == sum(
        PARSE_RAISE_CLASSIFICATION.values()
    )
    for name, expected in DOCUMENT_API_RAISE_COUNTS.items():
        assert source_probe.count_raises(source, name) == expected, name


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


# --- #54 第 3/4/5 条：段重入守卫、BOM 感知诊断、文档构造期不变量 ---


def test_duplicate_section_column_header_is_refused() -> None:
    """#54 第 3 条推荐 (a)：river 段之后再次出现 mesh 列头即 `ValueError`。

    pin 只把 `section` 重新置位，于是 `mesh.span == (2, 7)` 会把 river 的列头与数据行
    整段吞进区间内，而 `Section.span` 的契约是「段内可能夹杂空行」。
    """
    payload = (
        b"2 6 27000000.000000\n"
        b"Index Canopy Snow Surface Unsat GW\n"
        b"1 0.1 0.2 0.3 0.4 0.5\n"
        b"Index Stage\n"
        b"1 0.5\n"
        b"Index Canopy Snow Surface Unsat GW\n"
        b"2 0.1 0.2 0.3 0.4 0.5\n"
    )

    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse(payload)

    message = str(excinfo.value)
    assert "duplicate sectioned IC column header" in message
    assert "'mesh'" in message


def test_legal_three_section_layout_is_unaffected_by_the_re_entry_guard() -> None:
    """守卫不得误伤合法三段文件（mesh / river / lake 各恰一次）。"""
    for mesh_count in MESH_SIZES:
        synthetic = build_cfg_ic(mesh_count=mesh_count, river_count=4, lake_count=2)
        doc = cfg_ic.parse(synthetic.payload)
        assert cfg_ic.render(doc) == synthetic.payload
        assert doc.mesh.column_header_index == synthetic.mesh_column_header_index
        assert doc.river is not None and doc.lake is not None


def test_utf8_bom_is_diagnosed_as_a_bom_not_as_a_truncated_body() -> None:
    """#54 第 4 条：BOM 会把运维支到「文件被截断」的错误方向。"""
    payload = with_bom(build_cfg_ic(mesh_count=3, river_count=4).payload)

    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse(payload)

    message = str(excinfo.value)
    assert "UTF-8 BOM" in message
    assert "truncated sectioned IC body" not in message


def test_utf8_bom_on_the_silently_misparsed_coincidence_is_also_refused() -> None:
    """#54 实测的静默误解析巧合：mesh 行数恰等于列数（6）时 BOM 文件会**通过**。"""
    payload = with_bom(
        build_cfg_ic_rows(
            mesh_rows=[mesh_row(index) for index in range(1, 7)],
            header_tokens=("6", "6", "0", "0.0"),
        ).payload
    )

    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse(payload)

    assert "UTF-8 BOM" in str(excinfo.value)


def test_mesh_column_header_guard_is_exercised() -> None:
    """#54 评论 1：该守卫此前无任何用例（`if False:` 变异下全套 339 条全绿）。"""
    payload = b"0 6 27000000.000000\nIndex Stage\n1 0.100000\n"

    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse(payload)

    assert str(excinfo.value) == "sectioned IC body has no mesh column header"


def _valid_doc() -> cfg_ic.CfgIcDocument:
    return cfg_ic.parse(build_cfg_ic(mesh_count=3, river_count=4).payload)


def test_document_post_init_rejects_a_roles_lines_length_mismatch() -> None:
    doc = _valid_doc()

    with pytest.raises(ValueError) as excinfo:
        dataclasses.replace(doc, roles=doc.roles[:-1])

    assert "roles/lines length mismatch" in str(excinfo.value)


def test_document_post_init_rejects_an_out_of_range_header_index() -> None:
    doc = _valid_doc()

    with pytest.raises(ValueError) as excinfo:
        dataclasses.replace(doc, header_index=len(doc.lines))

    assert "header_index" in str(excinfo.value)


def test_document_post_init_rejects_out_of_range_section_line_numbers() -> None:
    doc = _valid_doc()

    with pytest.raises(ValueError) as excinfo:
        dataclasses.replace(
            doc,
            mesh=dataclasses.replace(doc.mesh, column_header_index=len(doc.lines)),
        )
    assert "column_header_index" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        dataclasses.replace(
            doc,
            mesh=dataclasses.replace(doc.mesh, data_line_indices=(len(doc.lines),)),
        )
    assert "data line index" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        dataclasses.replace(doc, river_preamble_index=len(doc.lines))
    assert "river_preamble_index" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        dataclasses.replace(doc, lake_preamble_index=len(doc.lines))
    assert "lake_preamble_index" in str(excinfo.value)


def test_naked_dataclasses_replace_with_a_different_line_count_fails_immediately() -> (
    None
):
    """#54 第 5 条实测的「静默产出看起来正常的 bytes」路径在此变红。"""
    doc = _valid_doc()

    with pytest.raises(ValueError):
        dataclasses.replace(doc, lines=(*doc.lines, "9 0.1 0.2 0.3 0.4 0.5\n"))


def test_with_replaced_lines_keeps_the_original_line_ending() -> None:
    synthetic = build_cfg_ic(mesh_count=3, river_count=4, eol="\r\n")
    doc = cfg_ic.parse(synthetic.payload)
    index = synthetic.mesh_data_indices[0]

    replaced = doc.with_replaced_lines({index: "1 0 0 0 0 0"})

    assert replaced.lines[index] == "1 0 0 0 0 0\r\n"
    assert replaced.mesh.rows[0] == (1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    for other in range(len(doc.lines)):
        if other != index:
            assert replaced.lines[other] == doc.lines[other]


def test_with_replaced_lines_keeps_a_missing_trailing_newline_missing() -> None:
    synthetic = build_cfg_ic(mesh_count=3, river_count=4, trailing_newline=False)
    doc = cfg_ic.parse(synthetic.payload)
    last = len(doc.lines) - 1

    replaced = doc.with_replaced_lines({last: "4 0"})

    assert replaced.lines[last] == "4 0"
    assert not cfg_ic.render(replaced).endswith(b"\n")


@pytest.mark.parametrize("char", ["\n", "\r", "\x85", " "])
def test_with_replaced_lines_refuses_a_replacement_that_would_change_the_line_count(
    char: str,
) -> None:
    doc = _valid_doc()

    with pytest.raises(ValueError) as excinfo:
        doc.with_replaced_lines({doc.mesh.data_line_indices[0]: f"1 0{char}2 0"})

    assert "must not contain a line break" in str(excinfo.value)


@pytest.mark.parametrize("index", [-1, 10_000])
def test_with_replaced_lines_refuses_an_out_of_range_index(index: int) -> None:
    doc = _valid_doc()

    with pytest.raises(ValueError) as excinfo:
        doc.with_replaced_lines({index: "1 0"})

    assert "out of range" in str(excinfo.value)


def test_with_replaced_lines_refuses_a_non_integer_index() -> None:
    doc = _valid_doc()

    with pytest.raises(ValueError) as excinfo:
        doc.with_replaced_lines({"2": "1 0"})  # type: ignore[dict-item]

    assert "must be int" in str(excinfo.value)


def test_with_replaced_lines_refuses_a_data_row_replacement_that_is_not_numeric() -> (
    None
):
    """`Section.rows` 是 `lines` 的派生视图，重算不出数值即 fail-closed。"""
    doc = _valid_doc()

    with pytest.raises(ValueError) as excinfo:
        doc.with_replaced_lines({doc.mesh.data_line_indices[0]: "Index Canopy"})

    assert "is not a numeric row" in str(excinfo.value)


# --- #305：river 段前导行 `<river-count> <state-cols>` ---

#: 现场 `yd.cfg.ic` 的逐行布局（`/ghdc/data/yd/input/yd/yd.cfg.ic`，只读核对得到；真实文件
#: 不入库，此处按同构缩小到 mesh=3 / river=2）：header 三 token 且 `6` 两侧带空格、Tab 分隔、
#: mesh 列头、3 行 mesh、`2<Tab>2` river 前导、`Index<Tab>Stage`、2 行 river、**无 lake 段**。
FIELD_LAYOUT_PAYLOAD = (
    b"3\t 6 \t13150080.000000\n"
    b"Index\tCanopy\tSnow\tSurface\tUnsat\tGW\n"
    b"1\t0.100000\t0.200000\t0.300000\t0.400000\t0.500000\n"
    b"2\t0.110000\t0.210000\t0.310000\t0.410000\t0.510000\n"
    b"3\t0.120000\t0.220000\t0.320000\t0.420000\t0.520000\n"
    b"2\t2\n"
    b"Index\tStage\n"
    b"1\t1.500000\n"
    b"2\t1.600000\n"
)
#: 由**构造**登记的行号（不是解析器回读）。
FIELD_LAYOUT_ROLES = (
    "header",
    "column_header",
    "data",
    "data",
    "data",
    "river_preamble",
    "column_header",
    "data",
    "data",
)


def test_field_layout_with_a_river_preamble_parses_and_roundtrips() -> None:
    """现场布局：mesh 已满后的 `2\t2` 是 river 段前导，不是多余 mesh 行（#305）。

    改动前此输入以 `surplus sectioned IC mesh row` 被拒，M4 的 prepare / init / tracker
    三条路径全部撞上。
    """
    doc = cfg_ic.parse(FIELD_LAYOUT_PAYLOAD)

    assert cfg_ic.render(doc) == FIELD_LAYOUT_PAYLOAD
    assert _roles(doc) == FIELD_LAYOUT_ROLES
    assert doc.header_index == 0
    assert doc.declared_mesh_count == 3
    assert doc.mesh.column_header_index == 1
    assert doc.mesh.data_line_indices == (2, 3, 4)
    assert doc.mesh.row_count == 3
    assert doc.river_preamble_index == 5
    assert doc.declared_river_count == 2
    assert doc.river is not None
    assert doc.river.column_header_index == 6
    assert doc.river.data_line_indices == (7, 8)
    assert doc.river.row_count == 2
    assert doc.river.rows == ((1.0, 1.5), (2.0, 1.6))
    # 前导是段元数据：既不进 mesh 也不进 river 的数据行。
    assert doc.river_preamble_index not in doc.mesh.data_line_indices
    assert doc.river_preamble_index not in doc.river.data_line_indices
    # 现场文件没有 lake 段。
    assert doc.lake is None
    assert doc.lake_preamble_index is None
    assert doc.declared_lake_count is None


@pytest.mark.parametrize("mesh_count", MESH_SIZES)
@pytest.mark.parametrize("delimiter", [" ", "\t"])
def test_river_preamble_layout_indexes_match_construction(
    mesh_count: int, delimiter: str
) -> None:
    built = build_cfg_ic(
        mesh_count=mesh_count,
        river_count=3,
        delimiter=delimiter,
        river_preamble=True,
    )
    assert built.river_preamble_index is not None

    doc = cfg_ic.parse(built.payload)

    assert cfg_ic.render(doc) == built.payload
    assert _roles(doc) == built.roles
    assert _roles(doc)[built.river_preamble_index] == "river_preamble"
    assert doc.river_preamble_index == built.river_preamble_index
    assert doc.declared_river_count == 3
    assert doc.mesh.data_line_indices == built.mesh_data_indices
    assert doc.mesh.row_count == mesh_count
    assert doc.river is not None
    assert doc.river.data_line_indices == built.river_data_indices
    assert doc.river.row_count == 3
    assert doc.lake is None


@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_river_and_lake_preambles_each_land_in_their_own_role(mesh_count: int) -> None:
    """两个段前导同时在场时各归各位，两个 count 都被校验。"""
    built = build_cfg_ic(
        mesh_count=mesh_count,
        river_count=3,
        lake_count=2,
        delimiter="\t",
        river_preamble=True,
    )
    assert built.river_preamble_index is not None
    assert built.lake_preamble_index is not None
    assert built.river_preamble_index < built.lake_preamble_index

    doc = cfg_ic.parse(built.payload)

    assert cfg_ic.render(doc) == built.payload
    assert _roles(doc) == built.roles
    assert _roles(doc)[built.river_preamble_index] == "river_preamble"
    assert _roles(doc)[built.lake_preamble_index] == "lake_preamble"
    assert doc.river_preamble_index == built.river_preamble_index
    assert doc.lake_preamble_index == built.lake_preamble_index
    assert doc.declared_river_count == 3
    assert doc.declared_lake_count == 2
    assert doc.river is not None and doc.lake is not None
    assert doc.river.data_line_indices == built.river_data_indices
    assert doc.lake.data_line_indices == built.lake_data_indices
    for index in (doc.river_preamble_index, doc.lake_preamble_index):
        assert index not in doc.mesh.data_line_indices
        assert index not in doc.river.data_line_indices
        assert index not in doc.lake.data_line_indices


def test_river_body_shorter_than_the_preamble_count_is_refused() -> None:
    built = build_cfg_ic(mesh_count=3, river_count=4, river_preamble=True)
    kept = [
        line
        for index, line in enumerate(built.lines)
        if index != built.river_data_indices[-1]
    ]

    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse("".join(kept).encode("utf-8"))

    message = str(excinfo.value)
    assert "truncated sectioned IC river body" in message
    # 实际与声明两个数字都必须报出来（spec Scenario）。
    assert "river=3" in message and "river=4" in message


def test_river_body_longer_than_the_preamble_count_is_refused() -> None:
    built = build_cfg_ic(mesh_count=3, river_count=2, river_preamble=True)
    lines = list(built.lines)
    lines.insert(built.river_data_indices[-1] + 1, "9 1.000000\n")

    with pytest.raises(ValueError) as excinfo:
        cfg_ic.parse("".join(lines).encode("utf-8"))

    message = str(excinfo.value)
    assert "truncated sectioned IC river body" in message
    assert "river=3" in message and "river=2" in message


def test_three_token_numeric_row_after_a_full_mesh_is_still_surplus() -> None:
    """偏离 1 的实质不变：只有**两整数 + 紧邻 river 列头**才是前导。"""
    built = build_cfg_ic(mesh_count=3, river_count=2, river_preamble=True)
    preamble_index = built.river_preamble_index
    assert preamble_index is not None
    lines = list(built.lines)
    lines.insert(preamble_index, "2 2 1\n")

    with pytest.raises(ValueError, match="surplus sectioned IC mesh row"):
        cfg_ic.parse("".join(lines).encode("utf-8"))


def test_two_integer_row_not_followed_by_a_river_header_is_still_surplus() -> None:
    """两整数行但下一有效行不是 river 列头（这里是另一条两整数行）→ 维持拒绝。"""
    built = build_cfg_ic(mesh_count=3, river_count=2, river_preamble=True)
    preamble_index = built.river_preamble_index
    assert preamble_index is not None
    lines = list(built.lines)
    lines.insert(preamble_index, "2 2\n")

    with pytest.raises(ValueError, match="surplus sectioned IC mesh row"):
        cfg_ic.parse("".join(lines).encode("utf-8"))


def test_two_integer_row_followed_by_a_lake_header_is_not_a_river_preamble() -> None:
    """后继列头开启的是 lake 段时不得判为 river 前导（无 river 段即无处可归）。"""
    payload = (
        b"2 6 27000000.000000\n"
        b"Index Canopy Snow Surface Unsat GW\n"
        b"1 0.1 0.2 0.3 0.4 0.5\n"
        b"2 0.1 0.2 0.3 0.4 0.5\n"
        b"1 2\n"
        b"Index LakeStage\n"
        b"1 0.5\n"
    )

    with pytest.raises(ValueError, match="surplus sectioned IC mesh row"):
        cfg_ic.parse(payload)


def test_river_preamble_requires_an_immediately_following_river_header() -> None:
    assert (
        cfg_ic._native_river_section_preamble(
            "2 2", next_line=RIVER_COLUMN_HEADER, stage_section_count=0
        )
        == 2
    )
    # 声明 0 行是合法的（与 lake 前导同构：只拒负数）。
    assert (
        cfg_ic._native_river_section_preamble(
            "0 2", next_line=RIVER_COLUMN_HEADER, stage_section_count=0
        )
        == 0
    )
    # 后继列头开启的不是 river 段。
    assert (
        cfg_ic._native_river_section_preamble(
            "2 2", next_line=LAKE_COLUMN_HEADER, stage_section_count=0
        )
        is None
    )
    assert (
        cfg_ic._native_river_section_preamble(
            "2 2", next_line=RIVER_COLUMN_HEADER, stage_section_count=1
        )
        is None
    )
    # 后继行不是列头 / 没有后继行。
    assert (
        cfg_ic._native_river_section_preamble(
            "2 2", next_line="2 0.350000", stage_section_count=0
        )
        is None
    )
    assert (
        cfg_ic._native_river_section_preamble(
            "2 2", next_line=None, stage_section_count=0
        )
        is None
    )
    # token 数不是 2 / 列数非正 / count 为负。
    assert (
        cfg_ic._native_river_section_preamble(
            "2 2 1", next_line=RIVER_COLUMN_HEADER, stage_section_count=0
        )
        is None
    )
    assert (
        cfg_ic._native_river_section_preamble(
            "2 0", next_line=RIVER_COLUMN_HEADER, stage_section_count=0
        )
        is None
    )
    assert (
        cfg_ic._native_river_section_preamble(
            "-1 2", next_line=RIVER_COLUMN_HEADER, stage_section_count=0
        )
        is None
    )
    # 非整数字面形态（pin 的 lake 判据同样只认纯整数）。
    assert (
        cfg_ic._native_river_section_preamble(
            "2.0 2", next_line=RIVER_COLUMN_HEADER, stage_section_count=0
        )
        is None
    )


def test_river_preamble_helper_claims_no_pin_provenance() -> None:
    """pin 从不识别 river 前导：本函数是本仓自有实现，不得贴逐字移植标签。"""
    source = pathlib.Path(cfg_ic.__file__).read_text(encoding="utf-8")
    segments = _function_source_segments(source)
    assert "_native_river_section_preamble" in segments
    assert (
        segments["_native_river_section_preamble"].count(
            "NWM@8ae9b8f2 packages/common/state_qc.py"
        )
        == 0
    )
    assert "_native_river_section_preamble" not in PORTED_HELPERS
    # lake 版本本体不受影响：仍带且只带一条溯源标签。
    assert (
        segments["_native_lake_section_preamble"].count(
            "NWM@8ae9b8f2 packages/common/state_qc.py"
        )
        == 1
    )


def test_with_replaced_lines_treats_the_river_preamble_like_the_lake_preamble() -> None:
    """前导行不在任何段的 `data_line_indices` 内：替换它不被拒，`rows` 不受影响。"""
    built = build_cfg_ic(mesh_count=3, river_count=2, lake_count=1, river_preamble=True)
    doc = cfg_ic.parse(built.payload)
    assert doc.river_preamble_index is not None
    assert doc.lake_preamble_index is not None
    assert doc.river is not None and doc.lake is not None
    for index in (doc.river_preamble_index, doc.lake_preamble_index):
        assert index not in doc.mesh.data_line_indices
        assert index not in doc.river.data_line_indices
        assert index not in doc.lake.data_line_indices

    replaced = doc.with_replaced_lines(
        {doc.river_preamble_index: "20 2", doc.lake_preamble_index: "10 2"}
    )

    assert replaced.mesh.rows == doc.mesh.rows
    assert replaced.river is not None and replaced.lake is not None
    assert replaced.river.rows == doc.river.rows
    assert replaced.lake.rows == doc.lake.rows
    assert replaced.river.data_line_indices == doc.river.data_line_indices
    assert replaced.lake.data_line_indices == doc.lake.data_line_indices
    assert replaced.mesh.data_line_indices == doc.mesh.data_line_indices
    assert replaced.roles == doc.roles
    assert replaced.lines[doc.river_preamble_index] == "20 2\n"
    assert replaced.lines[doc.lake_preamble_index] == "10 2\n"
    assert doc.lines[doc.river_preamble_index] == "2 2\n"
