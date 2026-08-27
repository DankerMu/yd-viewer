"""`yd_producer.geometry` 的行为测试。

oracle 纪律：期望坐标一律由 `geometry_fixtures` 用 pyproj **正向**投影生成的
lon/lat 锚点提供，测试断言被测工具能把 Albers 米制坐标还原回锚点；不得手写
期望坐标（手写常数等于把工具自身当 oracle）。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import struct
import subprocess
import sys

import pytest
from geometry_fixtures import (
    METRIC_GUARD,
    AlbersParams,
    SyntheticBaseline,
    sidecar,
    write_empty_layer,
    write_layer_with_null_shape,
    write_synthetic_baseline,
)
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from yd_producer.geometry import (
    GeometryError,
    load_prj_crs,
    read_shapefile,
    reproject_geometry,
    to_wgs84_transformer,
)

TOLERANCE_DEG = 1e-6


@pytest.fixture
def baseline(tmp_path) -> SyntheticBaseline:
    return write_synthetic_baseline(tmp_path / "baseline", river_count=3, unit_count=2)


def _parts(geom: BaseGeometry) -> list[list[tuple[float, float]]]:
    """折线几何 -> part 列表；面几何 -> 环列表（0 为外环）。"""
    if geom.geom_type == "LineString":
        return [list(geom.coords)]
    if geom.geom_type == "MultiLineString":
        return [list(part.coords) for part in geom.geoms]
    if geom.geom_type == "Polygon":
        return [list(geom.exterior.coords)] + [
            list(ring.coords) for ring in geom.interiors
        ]
    raise AssertionError(f"未预期的几何类型: {geom.geom_type}")


def _assert_matches_anchors(
    actual: list[tuple[float, float]],
    expected: tuple[tuple[float, float], ...],
) -> None:
    """**严格按序**逐点比对。

    折线的顶点顺序即河段的流向语义（10.2 的 `rivers.geojson` 原样继承），
    不存在「顺序可归一化」的豁免；排序后比对会把顺序反转这类错误变成永真式。
    """
    assert len(actual) == len(expected)
    for (lon, lat), (exp_lon, exp_lat) in zip(actual, expected, strict=True):
        assert lon == pytest.approx(exp_lon, abs=TOLERANCE_DEG)
        assert lat == pytest.approx(exp_lat, abs=TOLERANCE_DEG)


def _open_ring(ring: list[tuple[float, float]] | tuple[tuple[float, float], ...]):
    """校验环闭合并去掉重复的收尾点。"""
    assert len(ring) >= 4, f"环至少 4 点（含闭合点），得到 {len(ring)}"
    assert ring[0][0] == pytest.approx(ring[-1][0], abs=TOLERANCE_DEG)
    assert ring[0][1] == pytest.approx(ring[-1][1], abs=TOLERANCE_DEG)
    return list(ring[:-1])


def _assert_ring_matches_anchors(
    actual: list[tuple[float, float]],
    expected: tuple[tuple[float, float], ...],
) -> None:
    """面的环比对：只容忍**整体**起点旋转与环向反转，其余逐点严格对齐。

    起点旋转/环向归一化是 pyshp/shapely 环表示的既有偏差（PR 已声明），
    与坐标值无关；任何平移/缩放/单点漂移都无法被某个旋转或反向掩盖。
    """
    assert len(actual) == len(expected)
    opened = _open_ring(actual)
    target = _open_ring(expected)
    for candidate in (opened, list(reversed(opened))):
        for offset in range(len(candidate)):
            rotated = candidate[offset:] + candidate[:offset]
            if all(
                abs(lon - exp_lon) <= TOLERANCE_DEG
                and abs(lat - exp_lat) <= TOLERANCE_DEG
                for (lon, lat), (exp_lon, exp_lat) in zip(rotated, target, strict=True)
            ):
                return
    raise AssertionError(
        f"环与锚点在任何起点旋转/环向下都不匹配（容差 {TOLERANCE_DEG} 度）：\n"
        f"actual={opened}\nexpected={target}"
    )


def _read_reprojected(shp_path) -> list[tuple[dict, BaseGeometry]]:
    crs, features = read_shapefile(shp_path)
    transformer = to_wgs84_transformer(crs)
    return [
        (record, reproject_geometry(geom, transformer)) for record, geom in features
    ]


# --- .prj 装载 ------------------------------------------------------------


def test_load_prj_crs_matches_generator_albers_params(
    baseline: SyntheticBaseline,
) -> None:
    crs = load_prj_crs(baseline.rivers_prj)
    operation = crs.coordinate_operation
    assert operation is not None
    assert operation.method_name == "Albers Equal Area"
    values = {param.name: param.value for param in operation.params}
    albers = baseline.albers
    assert values["Latitude of 1st standard parallel"] == pytest.approx(albers.lat_1)
    assert values["Latitude of 2nd standard parallel"] == pytest.approx(albers.lat_2)
    assert values["Longitude of false origin"] == pytest.approx(albers.lon_0)
    assert values["Latitude of false origin"] == pytest.approx(albers.lat_0)
    assert values["Easting at false origin"] == pytest.approx(albers.x_0)
    assert values["Northing at false origin"] == pytest.approx(albers.y_0)


def test_load_prj_crs_honours_custom_albers_parameters(tmp_path) -> None:
    custom = AlbersParams(lat_1=27.5, lat_2=45.5, lon_0=110.0, x_0=1000.0, y_0=2000.0)
    other = write_synthetic_baseline(tmp_path / "custom", albers=custom)
    values = {
        param.name: param.value
        for param in load_prj_crs(other.rivers_prj).coordinate_operation.params
    }
    assert values["Latitude of 1st standard parallel"] == pytest.approx(27.5)
    assert values["Longitude of false origin"] == pytest.approx(110.0)
    assert values["Easting at false origin"] == pytest.approx(1000.0)


def test_load_prj_crs_missing_file_raises(tmp_path) -> None:
    missing = tmp_path / "absent.prj"
    with pytest.raises(GeometryError) as excinfo:
        load_prj_crs(missing)
    assert str(missing) in str(excinfo.value)


def test_load_prj_crs_empty_file_raises(baseline: SyntheticBaseline) -> None:
    baseline.rivers_prj.write_text("   \n", encoding="utf-8")
    with pytest.raises(GeometryError) as excinfo:
        load_prj_crs(baseline.rivers_prj)
    assert str(baseline.rivers_prj) in str(excinfo.value)
    # 诊断必须是「空文件」而不是「WKT 不可解析」：空 `.prj` 的现场处置是补投影
    # 定义，与内容写坏了不是一回事；去掉空值分支后 pyproj 会把它报成解析失败。
    assert "为空" in str(excinfo.value)


def test_load_prj_crs_garbage_wkt_raises(baseline: SyntheticBaseline) -> None:
    baseline.rivers_prj.write_text("PROJCS[not a wkt at all {{{", encoding="utf-8")
    with pytest.raises(GeometryError) as excinfo:
        load_prj_crs(baseline.rivers_prj)
    assert str(baseline.rivers_prj) in str(excinfo.value)
    # 不得外泄 pyproj 原生异常类型
    assert type(excinfo.value) is GeometryError


# --- 重投影：往返 oracle / 轴序 / 合法域 -----------------------------------


def test_river_roundtrip_restores_anchors(baseline: SyntheticBaseline) -> None:
    features = _read_reprojected(baseline.rivers_shp)
    assert len(features) == len(baseline.river_anchors)
    for (_, geom), expected_parts in zip(features, baseline.river_anchors, strict=True):
        actual_parts = _parts(geom)
        assert len(actual_parts) == len(expected_parts)
        for actual, expected in zip(actual_parts, expected_parts, strict=True):
            _assert_matches_anchors(actual, expected)


@pytest.mark.parametrize("unit_count", [1, 2, 3])
def test_domain_roundtrip_restores_anchors(tmp_path, unit_count: int) -> None:
    """每个面、每个环（含**外环**）都与锚点对齐——含无洞面。

    只断言计数/合法域/`is_valid` 会放过整体平移：+0.05 度的外环漂移或
    +5 度的无洞面平移都仍是合法且有效的面。
    """
    other = write_synthetic_baseline(
        tmp_path / f"domain-{unit_count}", unit_count=unit_count
    )
    features = _read_reprojected(other.domain_shp)
    assert len(features) == len(other.domain_anchors) == unit_count
    hole_free = 0
    for (_, geom), expected_rings in zip(features, other.domain_anchors, strict=True):
        actual_rings = _parts(geom)
        assert len(actual_rings) == len(expected_rings)
        if len(expected_rings) == 1:
            hole_free += 1
        for actual, expected in zip(actual_rings, expected_rings, strict=True):
            _assert_ring_matches_anchors(actual, expected)
    assert hole_free == unit_count - 1


def test_reprojected_coordinates_within_lonlat_domain(
    baseline: SyntheticBaseline,
) -> None:
    for shp in (baseline.rivers_shp, baseline.domain_shp):
        for _, geom in _read_reprojected(shp):
            for part in _parts(geom):
                for lon, lat in part:
                    assert -180.0 <= lon <= 180.0
                    assert -90.0 <= lat <= 90.0


def test_transformer_uses_lon_lat_axis_order(baseline: SyntheticBaseline) -> None:
    """钉死 `always_xy=True`：第一分量是经度、第二分量是纬度。

    锚点的经度（~103）与纬度（~33）差异显著，lon/lat 互换会使纬度越界。
    """
    crs, features = read_shapefile(baseline.rivers_shp)
    transformer = to_wgs84_transformer(crs)
    first_geom = features[0][1]
    first_anchor = baseline.river_anchors[0][0][0]
    x, y = _parts(first_geom)[0][0]
    lon, lat = transformer.transform(x, y)
    assert lon == pytest.approx(first_anchor[0], abs=TOLERANCE_DEG)
    assert lat == pytest.approx(first_anchor[1], abs=TOLERANCE_DEG)
    assert abs(lon - lat) > 10.0


def test_source_coordinates_are_metric_and_actually_transformed(
    baseline: SyntheticBaseline,
) -> None:
    """重投影前坐标为米制（|coord| > 180），重投影后落经纬度域——证明确实转换。"""
    crs, features = read_shapefile(baseline.rivers_shp)
    transformer = to_wgs84_transformer(crs)
    for _, geom in features:
        for part in _parts(geom):
            for x, y in part:
                assert abs(x) > METRIC_GUARD
                assert abs(y) > METRIC_GUARD
        for part in _parts(reproject_geometry(geom, transformer)):
            for lon, lat in part:
                assert abs(lon) <= 180.0
                assert abs(lat) <= 90.0


# --- 结构保持 -------------------------------------------------------------


def test_geometry_structure_preserved(baseline: SyntheticBaseline) -> None:
    crs, rivers = read_shapefile(baseline.rivers_shp)
    transformer = to_wgs84_transformer(crs)
    assert len(rivers) == len(baseline.river_anchors)
    for _, geom in rivers:
        projected = reproject_geometry(geom, transformer)
        assert projected.geom_type == geom.geom_type
        assert [len(part) for part in _parts(projected)] == [
            len(part) for part in _parts(geom)
        ]

    domain_crs, units = read_shapefile(baseline.domain_shp)
    domain_transformer = to_wgs84_transformer(domain_crs)
    assert len(units) == len(baseline.domain_anchors)
    for _, geom in units:
        projected = reproject_geometry(geom, domain_transformer)
        assert projected.geom_type == "Polygon"
        assert len(projected.exterior.coords) == len(geom.exterior.coords)


def test_multipart_and_interior_ring_structure_preserved(
    baseline: SyntheticBaseline,
) -> None:
    rivers = _read_reprojected(baseline.rivers_shp)
    multipart_expected = baseline.river_anchors[-1]
    assert len(multipart_expected) > 1, "基线必须含多部件折线"
    multipart_actual = _parts(rivers[-1][1])
    assert rivers[-1][1].geom_type == "MultiLineString"
    assert len(multipart_actual) == len(multipart_expected)
    assert [len(p) for p in multipart_actual] == [len(p) for p in multipart_expected]

    units = _read_reprojected(baseline.domain_shp)
    holed_expected = baseline.domain_anchors[0]
    assert len(holed_expected) > 1, "基线必须含带洞面"
    holed_actual = units[0][1]
    assert len(holed_actual.interiors) == len(holed_expected) - 1
    assert [len(ring.coords) for ring in holed_actual.interiors] == [
        len(ring) for ring in holed_expected[1:]
    ]
    assert holed_actual.is_valid
    for ring in holed_actual.interiors:
        assert Polygon(holed_actual.exterior).contains(Polygon(ring))


def test_interior_ring_and_non_first_part_coordinates_are_reprojected(
    baseline: SyntheticBaseline,
) -> None:
    """只处理外环/首部件的实现会在此留下未转换的米制坐标而失败。"""
    rivers = _read_reprojected(baseline.rivers_shp)
    multipart_geom = rivers[-1][1]
    non_first_actual = _parts(multipart_geom)[1:]
    non_first_expected = baseline.river_anchors[-1][1:]
    assert non_first_expected
    for actual, expected in zip(non_first_actual, non_first_expected, strict=True):
        _assert_matches_anchors(actual, expected)

    units = _read_reprojected(baseline.domain_shp)
    interior_actual = [list(ring.coords) for ring in units[0][1].interiors]
    interior_expected = baseline.domain_anchors[0][1:]
    assert interior_expected
    for actual, expected in zip(interior_actual, interior_expected, strict=True):
        _assert_ring_matches_anchors(actual, expected)
        for lon, lat in actual:
            assert -180.0 <= lon <= 180.0
            assert -90.0 <= lat <= 90.0


# --- shapefile 读取与失败契约 ----------------------------------------------


def test_read_shapefile_returns_crs_and_index_records(
    baseline: SyntheticBaseline,
) -> None:
    crs, features = read_shapefile(baseline.rivers_shp)
    assert crs == load_prj_crs(baseline.rivers_prj)
    assert [record["Index"] for record, _ in features] == list(baseline.river_indices)


@pytest.mark.parametrize("suffix", [".shp", ".shx", ".dbf"])
def test_read_shapefile_missing_sidecar_raises(
    baseline: SyntheticBaseline, suffix: str
) -> None:
    victim = baseline.rivers_shp.with_suffix(suffix)
    victim.unlink()
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    message = str(excinfo.value)
    assert str(victim) in message
    # 诊断必须是「缺文件」，且**只**点名缺的那个：没有这道先验，缺 `.shp` 会掉进
    # 几何读取失败并连带点名字节完好的 `.shx`（冤枉完好文件），缺 `.shx`/`.dbf`
    # 也会被报成内容损坏，把「补文件」误导成「修文件」。
    assert "缺少必需文件" in message
    for other in (".shp", ".shx", ".dbf"):
        if other != suffix:
            assert str(sidecar(baseline.rivers_shp, other)) not in message
    assert type(excinfo.value) is GeometryError


def test_read_shapefile_missing_prj_raises(baseline: SyntheticBaseline) -> None:
    baseline.rivers_prj.unlink()
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    assert str(baseline.rivers_prj) in str(excinfo.value)


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("truncated", b"\x00\x00\x27\x0a" + b"\x00" * 20),
        ("garbage", b"NOT A SHAPEFILE" * 40),
    ],
)
@pytest.mark.filterwarnings("ignore:Declared file size")
def test_read_shapefile_corrupt_shp_raises(
    baseline: SyntheticBaseline, label: str, payload: bytes
) -> None:
    baseline.rivers_shp.write_bytes(payload)
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    assert str(baseline.rivers_shp) in str(excinfo.value)
    assert type(excinfo.value) is GeometryError


def test_read_shapefile_corrupt_dbf_raises(baseline: SyntheticBaseline) -> None:
    baseline.rivers_shp.with_suffix(".dbf").write_bytes(b"garbage dbf payload" * 10)
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    assert str(baseline.rivers_shp.with_suffix(".dbf")) in str(excinfo.value)


# --- 路径绑定：带点基名 / 兄弟组 / 后缀校验 --------------------------------


@pytest.fixture
def dotted(tmp_path) -> SyntheticBaseline:
    """同目录下的兄弟组：`yd.riv.*`（折线）与 `yd.*`（面）。

    真实基线用 `yd.riv.shp` / `yd.dm.shp` 这类带点基名很常见；旁文件若用
    「剥掉全部后缀取基名」推导，`yd.riv.shp` 会被解析到 `yd.*` 上。
    """
    baseline = write_synthetic_baseline(
        tmp_path / "dotted",
        river_count=3,
        unit_count=2,
        rivers_stem="yd.riv",
        domain_stem="yd",
    )
    assert baseline.rivers_shp.name == "yd.riv.shp"
    assert baseline.domain_shp.name == "yd.shp"
    return baseline


def test_read_shapefile_dotted_basename_reads_named_group(
    dotted: SyntheticBaseline,
) -> None:
    crs, features = read_shapefile(dotted.rivers_shp)
    assert crs == load_prj_crs(dotted.rivers_prj)
    assert len(features) == len(dotted.river_anchors)
    assert [record["Index"] for record, _ in features] == list(dotted.river_indices)
    for _, geom in features:
        assert geom.geom_type in {"LineString", "MultiLineString"}


def test_read_shapefile_dotted_basename_ignores_sibling_group(
    dotted: SyntheticBaseline,
) -> None:
    """兄弟组 `yd.*` 存在时，`yd.riv.shp` 绝不能读成 `yd.*`（静默错图层）。"""
    _, siblings = read_shapefile(dotted.domain_shp)
    assert len(siblings) != len(dotted.river_anchors), "兄弟组要素数须可判别"

    _, features = read_shapefile(dotted.rivers_shp)
    assert len(features) == len(dotted.river_anchors)
    assert [record["Index"] for record, _ in features] == list(dotted.river_indices)
    assert all(geom.geom_type != "Polygon" for _, geom in features)


def test_read_shapefile_dotted_basename_uses_its_own_prj(
    dotted: SyntheticBaseline,
) -> None:
    """兄弟组 `.prj` 参数不同时，读出的 CRS 必须来自被点名那一组。"""
    divergent = AlbersParams(lon_0=110.0)
    assert divergent.lon_0 != dotted.albers.lon_0
    dotted.domain_prj.write_text(divergent.to_esri_wkt(), encoding="utf-8")

    crs, _ = read_shapefile(dotted.rivers_shp)
    values = {param.name: param.value for param in crs.coordinate_operation.params}
    assert values["Longitude of false origin"] == pytest.approx(dotted.albers.lon_0)


def test_read_shapefile_dotted_basename_missing_sidecar_names_own_group(
    dotted: SyntheticBaseline,
) -> None:
    victim = dotted.rivers_shp.with_name("yd.riv.dbf")
    victim.unlink()
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(dotted.rivers_shp)
    assert str(victim) in str(excinfo.value)
    assert "yd.dbf" not in str(excinfo.value)


@pytest.mark.parametrize("suffix", [".prj", ".dbf", ".txt", ""])
def test_read_shapefile_rejects_non_shp_path(
    baseline: SyntheticBaseline, suffix: str
) -> None:
    victim = baseline.rivers_shp.with_name(f"rivers{suffix}")
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(victim)
    assert str(victim) in str(excinfo.value)
    assert type(excinfo.value) is GeometryError


@pytest.mark.parametrize("name", ["RIVERS.SHP", "rivers.Shp"])
def test_read_shapefile_rejects_non_lowercase_shp_suffix(
    baseline: SyntheticBaseline, tmp_path, name: str
) -> None:
    """完整的**全大写** shapefile 组也必须被拒，且理由是后缀规则而非缺文件。

    旁文件按小写后缀推导，接受 `RIVERS.SHP` 在大小写敏感文件系统（CI ubuntu、
    node-22）上会去找不存在的 `RIVERS.shx`；同目录并存一组小写 `rivers.*` 时更会
    退化成跨组配对。大小写不敏感文件系统上构造不出这种误配（同名仅大小写不同的
    两组文件无法共存），但拒绝发生在**字符串**层、任何 `is_file()` 之前，因此本
    用例在 APFS 与 ext4 上行为一致。
    """
    upper_dir = tmp_path / "upper"
    upper_dir.mkdir()
    upper_shp = upper_dir / name
    stem = upper_shp.name[: -len(upper_shp.suffix)]
    upper_case = upper_shp.suffix.isupper()
    shutil.copyfile(baseline.rivers_shp, upper_shp)
    for suffix in (".shx", ".dbf", ".prj"):
        cased = suffix.upper() if upper_case else suffix
        shutil.copyfile(
            sidecar(baseline.rivers_shp, suffix), upper_dir / f"{stem}{cased}"
        )
        assert (upper_dir / f"{stem}{cased}").is_file()

    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(upper_shp)
    assert str(upper_shp) in str(excinfo.value)
    assert "小写" in str(excinfo.value)
    assert type(excinfo.value) is GeometryError


# --- 失败归属：损坏的 .shx 必须点名 .shx ------------------------------------


@pytest.mark.parametrize(
    ("label", "payload"),
    [("garbage", b"garbage index payload" * 7), ("truncated", None)],
)
def test_read_shapefile_corrupt_shx_names_the_shx(
    baseline: SyntheticBaseline, label: str, payload: bytes | None
) -> None:
    shx = sidecar(baseline.rivers_shp, ".shx")
    shx.write_bytes(shx.read_bytes()[:50] if payload is None else payload)
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    assert str(shx) in str(excinfo.value)
    assert str(baseline.rivers_shp) not in str(excinfo.value)
    assert type(excinfo.value) is GeometryError


@pytest.mark.parametrize("extra_bytes", [1, 7])
def test_read_shapefile_shx_with_partial_record_names_the_shx(
    baseline: SyntheticBaseline, extra_bytes: int
) -> None:
    """尾部多出不足一条 8 字节记录的碎片：索引记录区长度非法，必须点名 `.shx`。"""
    shx = sidecar(baseline.rivers_shp, ".shx")
    shx.write_bytes(shx.read_bytes() + b"\x00" * extra_bytes)
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    assert str(shx) in str(excinfo.value)
    assert str(baseline.rivers_shp) not in str(excinfo.value)


def test_read_shapefile_shx_declared_length_mismatch_names_the_shx(
    baseline: SyntheticBaseline,
) -> None:
    """记录区长度合法、但头部声明长度与实际不符（丢掉整条记录）也要点名 `.shx`。"""
    shx = sidecar(baseline.rivers_shp, ".shx")
    shx.write_bytes(shx.read_bytes()[:-8])
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    assert str(shx) in str(excinfo.value)
    assert str(baseline.rivers_shp) not in str(excinfo.value)


def test_read_shapefile_shx_shorter_than_header_names_the_shx(
    baseline: SyntheticBaseline,
) -> None:
    """只有「短于 100 字节头」这一条能判的 `.shx`：92 字节且声明长度自洽。

    92 字节时 `(92 - 100) % 8 == 0`（Python 取模为非负），把头部 24-28 字节改写成
    大端 46（=92/2）后声明长度也与实际一致——另外两条结构校验都放行，唯有最小长度
    这一条能判。删掉最小长度分支，本用例就会掉进 pyshp 的几何读取失败，报错不再
    单独点名 `.shx`。
    """
    shx = sidecar(baseline.rivers_shp, ".shx")
    raw = shx.read_bytes()[:92]
    shx.write_bytes(raw[:24] + struct.pack(">i", len(raw) // 2) + raw[28:])
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    assert str(shx) in str(excinfo.value)
    assert str(baseline.rivers_shp) not in str(excinfo.value)
    assert type(excinfo.value) is GeometryError


def test_read_shapefile_shx_record_area_misaligned_names_the_shx(
    baseline: SyntheticBaseline,
) -> None:
    """只有「记录区非 8 字节整数倍」这一条能判的 `.shx`：尾部多 4 字节且声明长度自洽。

    合法 `.shx` 追加 4 字节后长度 128，把头部声明字数同步改写成 64 使声明长度与实际
    一致，文件也远长于 100 字节头——唯有对齐这一条能判。删掉该分支，这份结构损坏的
    `.shx` 会被静默接受（pyshp 按前 N 条记录照读），与 fail closed 契约直接冲突。
    """
    shx = sidecar(baseline.rivers_shp, ".shx")
    raw = shx.read_bytes() + b"\x00" * 4
    assert (len(raw) - 100) % 8 == 4
    shx.write_bytes(raw[:24] + struct.pack(">i", len(raw) // 2) + raw[28:])
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    assert str(shx) in str(excinfo.value)
    assert str(baseline.rivers_shp) not in str(excinfo.value)
    assert type(excinfo.value) is GeometryError


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("all_ones", lambda raw: raw[:100] + b"\xff" * (len(raw) - 100)),
        (
            "repeat_record_0",
            lambda raw: raw[:100] + raw[100:108] * ((len(raw) - 100) // 8),
        ),
    ],
)
def test_read_shapefile_structurally_valid_but_corrupt_shx_names_both_files(
    baseline: SyntheticBaseline, label: str, mutate
) -> None:
    """记录区被改写但大小/对齐/声明长度俱合法：结构先验放行，报错必须同时点名两者。

    这类输入的责任无法判给单个文件——失败可能来自 `.shp` 负载，也可能来自 `.shx`
    索引。单点名 `.shp` 就会冤枉一个字节完好的文件（Round 1 D1 的误归属类），单点名
    `.shx` 则会漏掉真正损坏的 `.shp`；契约取「两个都报」。
    """
    shx = sidecar(baseline.rivers_shp, ".shx")
    raw = shx.read_bytes()
    mutated = mutate(raw)
    assert len(mutated) == len(raw)
    assert mutated[:100] == raw[:100]
    shx.write_bytes(mutated)
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    assert str(shx) in str(excinfo.value)
    assert str(baseline.rivers_shp) in str(excinfo.value)
    assert type(excinfo.value) is GeometryError


#: 在子解释器里跑的探针：脚本自身以 `assert False` 开头，若 `-O` 未生效会立刻炸掉，
#: 保证本用例不会因为「断言其实还在跑」而假绿。argv[1]=tests 目录，argv[2]=工作目录。
_DASH_O_PROBE = """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from geometry_fixtures import sidecar, write_synthetic_baseline
from yd_producer.geometry import GeometryError, read_shapefile

assert False, "-O 未生效：assert 仍在执行"

base = write_synthetic_baseline(Path(sys.argv[2]) / "baseline", river_count=3, unit_count=2)
shx = sidecar(base.rivers_shp, ".shx")
shx.write_bytes(shx.read_bytes()[:50])
try:
    read_shapefile(base.rivers_shp)
except GeometryError as exc:
    message = str(exc)
else:
    raise SystemExit("未抛 GeometryError")
if str(shx) not in message:
    raise SystemExit("报错未点名 .shx: " + message)
if str(base.rivers_shp) in message:
    raise SystemExit("报错误伤完好的 .shp: " + message)
"""


def test_shx_attribution_survives_python_dash_o(tmp_path) -> None:
    """`python -O` 下（pyshp 内部 assert 被剥除）截断 `.shx` 仍须点名 `.shx`。

    先前实现依赖 pyshp 的 `assert len(offsets_) == self.numShapes`；`-O` 一开，截断
    索引被读成 0 条记录，报错退化成「几何数(0)与属性记录数(3)不一致」并点名**完好的**
    `.shp`——归属错误。结构先验不含任何 `assert`，故与 `-O` 无关。
    """
    tests_dir = str(pathlib.Path(__file__).parent)
    completed = subprocess.run(
        [sys.executable, "-O", "-c", _DASH_O_PROBE, tests_dir, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_read_shapefile_valid_shx_passes_structure_check(tmp_path) -> None:
    """无误报：生成器写出的合法 `.shx`（含 0 要素图层）必须原样通过结构先验。"""
    baseline = write_synthetic_baseline(tmp_path / "sizes", river_count=2, unit_count=1)
    for shp in (baseline.rivers_shp, baseline.domain_shp):
        crs, features = read_shapefile(shp)
        assert crs is not None
        assert len(features) > 0

    empty = write_empty_layer(tmp_path / "empty.shp", baseline.prj_wkt)
    crs, features = read_shapefile(empty)
    assert features == []


# --- .prj 编码：非 UTF-8 fail closed / BOM 前缀可读 --------------------------


@pytest.mark.parametrize("encoding", ["cp1252", "gbk"])
def test_load_prj_crs_non_utf8_encoding_raises(
    baseline: SyntheticBaseline, encoding: str
) -> None:
    """内容合法但编码非 UTF-8：必须转成 `GeometryError`。

    `UnicodeDecodeError` 派生自 `ValueError`（非 `OSError`），不转换就会外泄成
    10.2/10.3 的 `except GeometryError` 接不住的原生异常，且消息不含路径。
    """
    localized = "黄河流域" if encoding == "gbk" else "Rhône"
    baseline.rivers_prj.write_bytes(
        baseline.prj_wkt.replace("unknown", localized, 1).encode(encoding)
    )
    with pytest.raises(GeometryError) as excinfo:
        load_prj_crs(baseline.rivers_prj)
    assert str(baseline.rivers_prj) in str(excinfo.value)
    assert type(excinfo.value) is GeometryError

    with pytest.raises(GeometryError) as read_excinfo:
        read_shapefile(baseline.rivers_shp)
    assert str(baseline.rivers_prj) in str(read_excinfo.value)
    assert type(read_excinfo.value) is GeometryError


def test_load_prj_crs_accepts_utf8_bom(baseline: SyntheticBaseline) -> None:
    """ArcGIS 常写出带 BOM 的 `.prj`：必须正常装载，且参数与生成器一致。"""
    baseline.rivers_prj.write_bytes(b"\xef\xbb\xbf" + baseline.prj_wkt.encode("utf-8"))
    crs = load_prj_crs(baseline.rivers_prj)
    operation = crs.coordinate_operation
    assert operation is not None
    assert operation.method_name == "Albers Equal Area"
    values = {param.name: param.value for param in operation.params}
    albers = baseline.albers
    assert values["Latitude of 1st standard parallel"] == pytest.approx(albers.lat_1)
    assert values["Latitude of 2nd standard parallel"] == pytest.approx(albers.lat_2)
    assert values["Longitude of false origin"] == pytest.approx(albers.lon_0)
    assert crs == load_prj_crs(baseline.domain_prj)

    read_crs, features = read_shapefile(baseline.rivers_shp)
    assert read_crs == crs
    assert len(features) == len(baseline.river_anchors)


# --- 失败归属：组完整性（几何数 vs 记录数）必须点名整组候选 -----------------


def _assert_names_whole_group(message: str, baseline: SyntheticBaseline) -> None:
    """数量不一致的责任无法判给单个文件，消息必须点名 `.shp`/`.shx`/`.dbf` 三者。"""
    for suffix in (".shp", ".shx", ".dbf"):
        path = sidecar(baseline.rivers_shp, suffix)
        assert str(path) in message, f"消息漏掉 {suffix}: {message}"


def test_read_shapefile_dbf_header_count_mismatch_names_whole_group(
    baseline: SyntheticBaseline,
) -> None:
    """`.dbf` 头部记录数被改写：`.shp`/`.shx` 字节完好，但三者责任不可判。

    单点名 `.shp` 就会冤枉两个完好文件并漏掉真正损坏的 `.dbf`（Round 3 R3-F1 的
    误归属类）。该分支同时是「删不得」的：没有它，末尾 `zip(strict=True)` 会抛裸
    `ValueError`，冲破单一公开异常契约——故本用例也钉住异常类型。
    """
    dbf = sidecar(baseline.rivers_shp, ".dbf")
    raw = bytearray(dbf.read_bytes())
    raw[4:8] = struct.pack("<I", 0)
    dbf.write_bytes(bytes(raw))

    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    message = str(excinfo.value)
    assert f"({len(baseline.river_anchors)})" in message
    assert "(0)" in message
    _assert_names_whole_group(message, baseline)
    assert type(excinfo.value) is GeometryError


def test_read_shapefile_doctored_shx_count_mismatch_names_whole_group(
    baseline: SyntheticBaseline,
) -> None:
    """`.shx` 丢掉一条记录、头部声明长度又被改自洽：`.shp`/`.dbf` 字节完好。

    结构先验的三条（最小长度 / 8 字节对齐 / 声明长度）全部放行，pyshp 也照读出
    N-1 条几何而不报错，失败只表现为数量不等——责任落在 `.shx` 上，若这条消息只
    点名 `.shp` 与 `.dbf`，就恰好漏掉了唯一损坏的那个文件。故点名整组。
    """
    shx = sidecar(baseline.rivers_shp, ".shx")
    raw = shx.read_bytes()[:-8]
    shx.write_bytes(raw[:24] + struct.pack(">i", len(raw) // 2) + raw[28:])

    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(baseline.rivers_shp)
    message = str(excinfo.value)
    assert f"({len(baseline.river_anchors) - 1})" in message
    assert f"({len(baseline.river_anchors)})" in message
    _assert_names_whole_group(message, baseline)
    assert type(excinfo.value) is GeometryError


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root 无视文件权限位，构造不出不可读的 .shx",
)
def test_read_shapefile_unreadable_shx_names_the_shx(
    baseline: SyntheticBaseline,
) -> None:
    """`.shx` 存在但不可读（权限位）：`stat` 成功、`open` 抛 `PermissionError`。

    不转换就会外泄成调用方 `except GeometryError` 接不住的 `OSError`，且消息不含
    路径；责任明确在 `.shx`，不得点名完好的 `.shp`。
    """
    shx = sidecar(baseline.rivers_shp, ".shx")
    shx.chmod(0o000)
    try:
        with pytest.raises(GeometryError) as excinfo:
            read_shapefile(baseline.rivers_shp)
    finally:
        shx.chmod(0o644)
    assert str(shx) in str(excinfo.value)
    assert str(baseline.rivers_shp) not in str(excinfo.value)
    assert type(excinfo.value) is GeometryError


# --- 与文件无关的失败：点名 CRS / 几何类型 ----------------------------------


#: 工程坐标系（LOCAL_CS）：`.prj` 本身完全合法可解析，但与 WGS84 之间不存在
#: 任何大地基准关系，pyproj 构造 transformer 时抛 `ProjError`。厂区/局部坐标
#: 的现场资料里确实会出现这种 `.prj`。
_ENGINEERING_PRJ_WKT = (
    'LOCAL_CS["Arbitrary",LOCAL_DATUM["Unknown",0],'
    'UNIT["metre",1],AXIS["X",EAST],AXIS["Y",NORTH]]'
)


def test_to_wgs84_transformer_rejects_untransformable_crs(
    baseline: SyntheticBaseline,
) -> None:
    """`.prj` 可解析但不可转到 WGS84：`ProjError` 必须转成 `GeometryError`。

    责任范围内没有文件——`.prj` 读取与解析都成功了，出错的是 CRS 与 WGS84 的关系，
    故消息点名该 CRS 而不点名任何路径。
    """
    baseline.rivers_prj.write_text(_ENGINEERING_PRJ_WKT, encoding="utf-8")
    crs = load_prj_crs(baseline.rivers_prj)
    assert crs.type_name == "Engineering CRS"

    with pytest.raises(GeometryError) as excinfo:
        to_wgs84_transformer(crs)
    assert "EPSG:4326" in str(excinfo.value)
    assert type(excinfo.value) is GeometryError


class _RaisingTransformer:
    """`transform` 必然抛异常的 transformer 替身。

    `reproject_geometry` 的 transformer 是**调用方传入的公开参数**，调用方可以给
    任意 `Transformer`（含 `Transformer.from_pipeline` 构造的）；已实测 pyproj 的
    正常构造路径在空几何、三维、超界坐标、`GeometryCollection` 下都不抛异常，找不到
    天然的失败输入，故在这个公开接缝上注入一个会抛的 transformer，钉住「原生异常
    不外泄」这条契约。
    """

    def transform(self, *args: object, **kwargs: object) -> tuple[float, ...]:
        raise RuntimeError("proj pipeline exploded")


def test_reproject_geometry_converts_transformer_failure() -> None:
    """重投影过程中的原生异常必须转成 `GeometryError`，消息点名几何类型。"""
    geom = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)])
    with pytest.raises(GeometryError) as excinfo:
        reproject_geometry(geom, _RaisingTransformer())
    assert geom.geom_type in str(excinfo.value)
    assert type(excinfo.value) is GeometryError


def test_read_shapefile_null_geometry_names_only_the_shp(
    baseline: SyntheticBaseline, tmp_path
) -> None:
    """NULL 几何（shapeType 0）无法转成 GeoJSON：责任只在 `.shp` 负载。

    `.shx` 索引与 `.dbf` 记录都与该记录一一对应且字节完好，数量也相等——三条结构
    校验、两次读取、组完整性守卫全部放行，唯有几何解析能判。消息不得点名这两个
    完好的旁文件。
    """
    layer = write_layer_with_null_shape(
        tmp_path / "nulls" / "nulls.shp", baseline.prj_wkt
    )
    with pytest.raises(GeometryError) as excinfo:
        read_shapefile(layer)
    message = str(excinfo.value)
    assert str(layer) in message
    assert str(sidecar(layer, ".shx")) not in message
    assert str(sidecar(layer, ".dbf")) not in message
    assert type(excinfo.value) is GeometryError
