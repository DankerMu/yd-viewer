"""`yd_producer.geometry` 的行为测试。

oracle 纪律：期望坐标一律由 `geometry_fixtures` 用 pyproj **正向**投影生成的
lon/lat 锚点提供，测试断言被测工具能把 Albers 米制坐标还原回锚点；不得手写
期望坐标（手写常数等于把工具自身当 oracle）。
"""

from __future__ import annotations

import shutil

import pytest
from geometry_fixtures import (
    METRIC_GUARD,
    AlbersParams,
    SyntheticBaseline,
    sidecar,
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
    assert str(victim) in str(excinfo.value)
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


def test_read_shapefile_accepts_uppercase_shp_suffix(
    baseline: SyntheticBaseline, tmp_path
) -> None:
    upper_dir = tmp_path / "upper"
    upper_dir.mkdir()
    upper_shp = upper_dir / "RIVERS.SHP"
    shutil.copyfile(baseline.rivers_shp, upper_shp)
    for suffix in (".shx", ".dbf", ".prj"):
        shutil.copyfile(
            sidecar(baseline.rivers_shp, suffix), upper_dir / f"RIVERS{suffix}"
        )
    _, features = read_shapefile(upper_shp)
    assert [record["Index"] for record, _ in features] == list(baseline.river_indices)


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
