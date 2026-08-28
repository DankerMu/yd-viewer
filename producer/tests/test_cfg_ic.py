"""`yd_producer.state.cfg_ic` 的行为测试。

oracle 纪律：结构索引期望值一律来自 `cfg_ic_fixtures` 的**构造记录**（生成器发行时登记的
行号与角色），不得由解析器回读；两种 mesh 规模各跑一遍，防止把段区间写成常量而恒真。

判别力纪律：`render(parse(b)) == b` 对逐字模型是平凡真，单靠它证明不了任何东西。真正的
承重条是（1）脏输入矩阵——canonical 化的 writer 只在这里变红；（2）结构索引与逐行角色
oracle——段归属偏移一行、preamble 计入 river 只在这里变红。
"""

from __future__ import annotations

import inspect
import os
import pathlib
import stat

import pytest
from cfg_ic_fixtures import (
    LAKE_COLUMN_HEADER,
    MESH_COLUMN_HEADER,
    RIVER_COLUMN_HEADER,
    build_cfg_ic,
    build_compat_layout,
)

from yd_producer.state import cfg_ic

MESH_SIZES = (3, 7)


def _roles(doc: cfg_ic.CfgIcDocument) -> tuple[str, ...]:
    return tuple(role.value for role in doc.roles)


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


@pytest.mark.parametrize("mesh_count", MESH_SIZES)
def test_combined_dirty_input_keeps_full_section_index(mesh_count: int) -> None:
    """脏输入不得降级为「只保字节、不分段」：叠加脏例必须跑完整段索引 oracle。"""
    built = build_cfg_ic(
        mesh_count=mesh_count,
        river_count=3,
        lake_count=2,
        eol="\r\n",
        trailing_spaces=True,
        blank_lines=True,
    )
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
    built = build_cfg_ic(mesh_count=3, river_count=2, mixed_notation=True)
    doc = cfg_ic.parse(built.payload)
    assert len(doc.mesh.rows) == 3
    assert all(len(row) == 6 for row in doc.mesh.rows)
    assert doc.river is not None
    assert len(doc.river.rows) == 2
    assert all(isinstance(value, float) for row in doc.mesh.rows for value in row)


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
    # 移植辅助逐函数带溯源头。
    for helper in (
        "_read_bytes_limited",
        "_header_counts",
        "_numeric_row",
        "_looks_like_column_header",
        "_section_from_column_header",
        "_native_lake_section_preamble",
    ):
        marker = source.index(f"def {helper}(")
        body = source[marker : marker + 1200]
        assert "NWM@8ae9b8f2 packages/common/state_qc.py" in body, helper


def test_module_documents_the_deliberate_deviations() -> None:
    source = pathlib.Path(cfg_ic.__file__).read_text(encoding="utf-8")
    head = source[: source.index('"""', 3) + 3]
    assert "刻意偏离" in head
    assert "OSError" in head and "ValueError" in head
    assert "mesh" in head


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
