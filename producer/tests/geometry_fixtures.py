"""合成 shapefile 基线生成器（程序化，零二进制入库）。

为什么不提交 `.shp/.shx/.dbf/.prj` 二进制：仓库根 `.gitignore` 的 `fixtures/`
无前导斜杠，会匹配任意层级的 `fixtures/` 目录，`producer/tests/fixtures/` 会被
静默忽略（本地绿、CI 红）。程序化生成从根上绕开该陷阱，同时让 Albers 参数、
河段数、单元数成为测试可参数化的入参。

独立性硬约束：本模块 MUST NOT 从 `yd_producer.geometry` import 任何 CRS /
transformer 构造函数或重投影函数。生成器自行用 `.prj` 的 WKT 构造正向
`pyproj.Transformer`；否则正向与反向共用同一条构造路径，一个漏掉
`always_xy=True` 的错误会在往返中自相抵消，把轴序断言变成永真式。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer

# 经纬度锚点（lon, lat）序列；part / ring 为其列表
Anchors = tuple[tuple[float, float], ...]

#: 默认 DBF 索引字段名与字段定义（#18 起的既有行为，故障图层以关键字参数覆盖）
DEFAULT_INDEX_FIELD = "Index"
DEFAULT_INDEX_FIELD_SPEC = ("N", 10, 0)

#: 生成器保证：所有写入 shapefile 的 Albers 坐标绝对值均超过该阈值，
#: 使「米制坐标被当作经纬度」的实现错误必然可判。
METRIC_GUARD = 180.0


def sidecar(shp_path: Path, suffix: str) -> Path:
    """把 `shp_path` 的最后一个后缀换成 `suffix`（`yd.riv.shp` -> `yd.riv.prj`）。

    刻意不用 `with_suffix("")` + `with_suffix(suffix)`：带点基名会被多剥一层后缀，
    指向另一组 shapefile。本函数与 `yd_producer.geometry` 的同名逻辑各自实现，
    保持生成器对被测模块的独立性。
    """
    return shp_path.with_name(shp_path.name[: -len(shp_path.suffix)] + suffix)


@dataclass(frozen=True)
class AlbersParams:
    """合成基线使用的自定义 Albers Equal Area 参数（非 EPSG 编码投影）。"""

    lat_1: float = 25.0
    lat_2: float = 47.0
    lat_0: float = 0.0
    lon_0: float = 105.0
    x_0: float = 0.0
    y_0: float = 0.0

    def to_proj_dict(self) -> dict[str, object]:
        return {
            "proj": "aea",
            "lat_1": self.lat_1,
            "lat_2": self.lat_2,
            "lat_0": self.lat_0,
            "lon_0": self.lon_0,
            "x_0": self.x_0,
            "y_0": self.y_0,
            "datum": "WGS84",
            "units": "m",
        }

    def to_crs(self) -> CRS:
        return CRS.from_dict(self.to_proj_dict())

    def to_esri_wkt(self) -> str:
        """写入 `.prj` 的 WKT（ESRI 方言，与真实 shapefile 旁文件一致）。"""
        return self.to_crs().to_wkt("WKT1_ESRI")


@dataclass(frozen=True)
class SyntheticBaseline:
    """一份合成基线 GIS 包及其经纬度 oracle。"""

    directory: Path
    rivers_shp: Path
    domain_shp: Path
    albers: AlbersParams
    prj_wkt: str
    #: river 要素 -> part -> 顶点（lon, lat）
    river_anchors: tuple[tuple[Anchors, ...], ...]
    river_indices: tuple[int, ...]
    #: domain 要素 -> ring（0 为外环，其余为内环）-> 顶点（lon, lat）
    domain_anchors: tuple[tuple[Anchors, ...], ...]
    domain_indices: tuple[int, ...]

    @property
    def rivers_prj(self) -> Path:
        return sidecar(self.rivers_shp, ".prj")

    @property
    def domain_prj(self) -> Path:
        return sidecar(self.domain_shp, ".prj")


def _forward_transformer(albers: AlbersParams) -> Transformer:
    """经纬度 -> 自定义 Albers 的正向投影（生成器自建，独立于被测模块）。"""
    return Transformer.from_crs("EPSG:4326", albers.to_crs(), always_xy=True)


def project_anchors(
    anchors: Anchors, albers: AlbersParams
) -> list[tuple[float, float]]:
    """把 (lon, lat) 锚点正向投影到自定义 Albers，返回 (x, y) 米制坐标。"""
    transformer = _forward_transformer(albers)
    return [transformer.transform(lon, lat) for lon, lat in anchors]


def _river_anchors(count: int, albers: AlbersParams) -> tuple[tuple[Anchors, ...], ...]:
    """N 条河段折线；最后一条为多部件（multi-part）折线。

    经度一律取在中央经线以西 2 度以外，避免中央经线附近 easting≈0
    破坏 METRIC_GUARD 保证。
    """
    if count < 2:
        raise ValueError(f"river_count 至少为 2（需覆盖多部件折线），得到 {count}")
    rivers: list[tuple[Anchors, ...]] = []
    for i in range(count):
        lon = albers.lon_0 - 2.0 - 0.4 * i
        lat = 33.0 + 0.2 * i
        first: Anchors = tuple((lon + 0.1 * j, lat + 0.15 * j) for j in range(3 + i))
        if i == count - 1:
            second: Anchors = tuple(
                (lon - 0.3 + 0.1 * j, lat + 1.0 + 0.2 * j) for j in range(4)
            )
            rivers.append((first, second))
        else:
            rivers.append((first,))
    return tuple(rivers)


def _ring(
    lon_min: float, lat_min: float, lon_max: float, lat_max: float, *, hole: bool
) -> Anchors:
    """外环取顺时针、内环取逆时针（shapefile 环向约定）。"""
    corners = [
        (lon_min, lat_min),
        (lon_min, lat_max),
        (lon_max, lat_max),
        (lon_max, lat_min),
        (lon_min, lat_min),
    ]
    if hole:
        corners.reverse()
    return tuple(corners)


def _domain_anchors(
    count: int, albers: AlbersParams, *, adjacent: bool = False
) -> tuple[tuple[Anchors, ...], ...]:
    """N 个 domain 单元面；第一个带一个 interior ring（洞）。

    默认布局（`adjacent=False`）里单元沿经度方向**互不相接**（间距 1.2 度 > 边长
    1.0 度），合并结果为 MultiPolygon。

    `adjacent=True` 时改为沿**经度方向**首尾共边（第 i 个单元的东边界即第 i+1 个
    单元的西边界），使合并边界由构造已知：外环顶点集 = 两端外角 ∪ 共享边端点。
    相邻方向必须是经度：共享边端点落在纬线弧上，在 Albers 平面内与两侧外角不共线，
    因而必然是 union 输出的顶点；若沿纬度方向堆叠，共享边端点落在 Albers 平面内为
    直线的经线上，该 oracle 就变成「GEOS 是否保留共线节点」的赌注。

    共享边的两个端点在相邻两个 shell 里是**同一对 (lon, lat) 元组**，正向投影后
    浮点数逐位相同，共享边在源 CRS 平面内严格重合，不会留下 sliver。
    """
    if count < 1:
        raise ValueError(f"unit_count 至少为 1，得到 {count}")
    units: list[tuple[Anchors, ...]] = []
    for i in range(count):
        if adjacent:
            lon_min = albers.lon_0 - 6.0 + 1.0 * i
        else:
            lon_min = albers.lon_0 - 5.0 - 1.2 * i
        lat_min = 35.0
        shell = _ring(lon_min, lat_min, lon_min + 1.0, lat_min + 1.0, hole=False)
        if i == 0:
            hole = _ring(
                lon_min + 0.3, lat_min + 0.3, lon_min + 0.6, lat_min + 0.6, hole=True
            )
            units.append((shell, hole))
        else:
            units.append((shell,))
    return tuple(units)


def _assert_metric_guard(parts: list[list[tuple[float, float]]], label: str) -> None:
    for part in parts:
        for x, y in part:
            if abs(x) <= METRIC_GUARD or abs(y) <= METRIC_GUARD:
                raise ValueError(
                    f"{label} 的 Albers 坐标 ({x}, {y}) 未超过 {METRIC_GUARD}，"
                    "无法证伪『米制坐标被当作经纬度』的实现"
                )


def _write_prj(shp_path: Path, wkt: str) -> None:
    sidecar(shp_path, ".prj").write_text(wkt, encoding="utf-8")


def _write_layer(
    shp_path: Path,
    shape_type: int,
    features: tuple[tuple[Anchors, ...], ...],
    albers: AlbersParams,
    wkt: str,
    *,
    index_field: str = DEFAULT_INDEX_FIELD,
    index_field_spec: tuple[str, int, int] = DEFAULT_INDEX_FIELD_SPEC,
    index_values: Sequence[object] | None = None,
) -> tuple[object, ...]:
    """写一组图层；三个关键字参数放开 DBF 字段名 / 字段类型 / 显式 index 值序列。

    默认值即 #18 起沿用的行为（字段 `("Index", "N", 10, 0)`、索引 `range(1, N+1)`），
    默认路径下写出的字节与扩展前完全一致；缺字段 / 字段为 C 型文本 / 索引重复等
    故障图层一律经这三个关键字参数构造，不改默认。
    """
    indices: tuple[object, ...]
    if index_values is None:
        indices = tuple(range(1, len(features) + 1))
    else:
        indices = tuple(index_values)
        if len(indices) != len(features):
            raise ValueError(
                f"index_values 长度({len(indices)})与要素数({len(features)})不一致"
            )
    # 显式给出三个目标文件而非「基名」：`Writer(基名)` 对 `yd.riv.shp` 会写成
    # `yd.*`，与 `read_shapefile` 的路径绑定契约相悖，也无法生成带点基名的基线。
    with shapefile.Writer(
        shp=str(shp_path),
        shx=str(sidecar(shp_path, ".shx")),
        dbf=str(sidecar(shp_path, ".dbf")),
        shapeType=shape_type,
    ) as writer:
        if index_field:
            writer.field(index_field, *index_field_spec)
        else:
            # 无 Index 字段的图层：pyshp 不允许零字段的 DBF，故写一个无关字段。
            writer.field("Name", "C", 20)
        for index, feature in zip(indices, features, strict=True):
            projected = [project_anchors(part, albers) for part in feature]
            _assert_metric_guard(projected, f"{shp_path.name} Index={index}")
            if shape_type == shapefile.POLYLINE:
                writer.line(projected)
            else:
                writer.poly(projected)
            writer.record(index)
    _write_prj(shp_path, wkt)
    return indices


def write_empty_layer(shp_path: Path, wkt: str) -> Path:
    """写一个 0 要素的合法折线图层（含 `.shx`/`.dbf`/`.prj`），供无误报回归使用。

    0 要素时 `.shx` 只有 100 字节头、记录区为空，是结构先验的边界情形。
    """
    shp_path = Path(shp_path)
    shp_path.parent.mkdir(parents=True, exist_ok=True)
    with shapefile.Writer(
        shp=str(shp_path),
        shx=str(sidecar(shp_path, ".shx")),
        dbf=str(sidecar(shp_path, ".dbf")),
        shapeType=shapefile.POLYLINE,
    ) as writer:
        writer.field("Index", "N", 10, 0)
    _write_prj(shp_path, wkt)
    return shp_path


def write_synthetic_baseline(
    directory: Path,
    *,
    river_count: int = 3,
    unit_count: int = 2,
    albers: AlbersParams | None = None,
    rivers_stem: str = "rivers",
    domain_stem: str = "domain",
    adjacent_units: bool = False,
) -> SyntheticBaseline:
    """在 `directory` 下生成 `rivers` / `domain` 两组 shapefile 及其 `.prj`。

    坐标 oracle 为 lon/lat 锚点：先用 pyproj 正向投影写入文件，测试再断言
    被测工具能把它们还原回锚点——期望坐标不得手写。

    `rivers_stem`/`domain_stem` 允许生成带点基名（如 `yd.riv`）与同目录同前缀的
    兄弟组（`yd.riv.*` 与 `yd.*`），供路径绑定回归使用。

    `adjacent_units=True` 把 domain 单元改为沿经度方向共边（见 `_domain_anchors`），
    供合并边界的构造已知 oracle 使用；默认布局互不相接，保持 #18 的既有行为。
    """
    albers = albers or AlbersParams()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    wkt = albers.to_esri_wkt()

    rivers_shp = directory / f"{rivers_stem}.shp"
    domain_shp = directory / f"{domain_stem}.shp"
    river_anchors = _river_anchors(river_count, albers)
    domain_anchors = _domain_anchors(unit_count, albers, adjacent=adjacent_units)
    river_indices = _write_layer(
        rivers_shp, shapefile.POLYLINE, river_anchors, albers, wkt
    )
    domain_indices = _write_layer(
        domain_shp, shapefile.POLYGON, domain_anchors, albers, wkt
    )

    return SyntheticBaseline(
        directory=directory,
        rivers_shp=rivers_shp,
        domain_shp=domain_shp,
        albers=albers,
        prj_wkt=wkt,
        river_anchors=river_anchors,
        river_indices=river_indices,
        domain_anchors=domain_anchors,
        domain_indices=domain_indices,
    )


def write_layer_with_null_shape(shp_path: Path, wkt: str) -> Path:
    """写一个含 **NULL 几何**（shapeType 0）的合法折线图层。

    NULL 是 ESRI 规范允许的记录类型（属性行无几何时 ArcGIS 就这么写），`.shx`
    索引与 `.dbf` 记录都与之一一对应、字节完好；损坏只在 `.shp` 负载一侧——
    GeoJSON 无法表示 NULL，几何解析必然失败。供「几何不可解析」的归属回归使用。
    """
    shp_path = Path(shp_path)
    shp_path.parent.mkdir(parents=True, exist_ok=True)
    with shapefile.Writer(
        shp=str(shp_path),
        shx=str(sidecar(shp_path, ".shx")),
        dbf=str(sidecar(shp_path, ".dbf")),
        shapeType=shapefile.POLYLINE,
    ) as writer:
        writer.field("Index", "N", 10, 0)
        writer.line([[(1.0e6, 2.0e6), (1.1e6, 2.1e6)]])
        writer.record(1)
        writer.null()
        writer.record(2)
    _write_prj(shp_path, wkt)
    return shp_path


def shared_edge_anchors(
    baseline: SyntheticBaseline,
) -> tuple[tuple[float, float], ...]:
    """相邻布局下两个单元共享边的两个端点（lon, lat），由构造给出。

    只对 `adjacent_units=True` 且 `unit_count == 2` 的基线有意义：共享边是第一个
    单元外环的东边界，即 `(lon_max, lat_min)` 与 `(lon_max, lat_max)`。
    """
    shells = [unit[0] for unit in baseline.domain_anchors]
    if len(shells) != 2:
        raise ValueError(f"共享边 oracle 只支持 2 个单元，得到 {len(shells)}")
    left, right = shells
    left_lon_max = max(lon for lon, _ in left)
    right_lon_min = min(lon for lon, _ in right)
    if left_lon_max != right_lon_min:
        raise ValueError("两个单元并不共边，请用 adjacent_units=True 生成基线")
    lats = sorted({lat for lon, lat in left if lon == left_lon_max})
    return tuple((left_lon_max, lat) for lat in lats)


def write_rivers_layer(
    shp_path: Path,
    *,
    river_count: int = 2,
    albers: AlbersParams | None = None,
    index_field: str = DEFAULT_INDEX_FIELD,
    index_field_spec: tuple[str, int, int] = DEFAULT_INDEX_FIELD_SPEC,
    index_values: Sequence[object] | None = None,
) -> tuple[Path, tuple[object, ...]]:
    """单独写一组河网图层，DBF 字段名/字段类型/index 值序列可控。

    供「缺 `Index` 字段」「`Index` 重复」「`Index` 为 C 型文本」三种故障基线使用：
    几何与 `.prj` 一律走与合法基线相同的锚点正向投影路径，只有 DBF 一侧被做坏，
    使失败必然归因于属性面而不是几何面。`index_field=""` 表示不写 `Index` 字段。
    """
    albers = albers or AlbersParams()
    shp_path = Path(shp_path)
    shp_path.parent.mkdir(parents=True, exist_ok=True)
    wkt = albers.to_esri_wkt()
    anchors = _river_anchors(river_count, albers)
    indices = _write_layer(
        shp_path,
        shapefile.POLYLINE,
        anchors,
        albers,
        wkt,
        index_field=index_field,
        index_field_spec=index_field_spec,
        index_values=index_values,
    )
    return shp_path, indices


#: 越域图层用的源 CRS 原始米制坐标：投影反算有效域之外，重投影后为非有限值
OUT_OF_DOMAIN_XY = (1.0e12, 1.0e12)


def write_out_of_domain_layer(
    shp_path: Path,
    *,
    polygon: bool = False,
    albers: AlbersParams | None = None,
    index: int = 7,
) -> Path:
    """写一个含**投影域外顶点**的图层，重投影后坐标为非有限值。

    这是锚点纪律的**唯一豁免**：合法 lon/lat 锚点正向投影只会得到有限 Albers 坐标，
    非有限回归无法经锚点路径构造，故这里直写源 CRS 的原始米制坐标并绕过
    `METRIC_GUARD`（`1e12` 量级取自 issue #19 评论的实测复现）。本图层只验错误路径
    与失败归属，不做坐标往返断言；要素带一个已知 `Index`，供归属断言点名。
    """
    albers = albers or AlbersParams()
    shp_path = Path(shp_path)
    shp_path.parent.mkdir(parents=True, exist_ok=True)
    inside = (0.0, 3.15e6)
    outside = OUT_OF_DOMAIN_XY
    with shapefile.Writer(
        shp=str(shp_path),
        shx=str(sidecar(shp_path, ".shx")),
        dbf=str(sidecar(shp_path, ".dbf")),
        shapeType=shapefile.POLYGON if polygon else shapefile.POLYLINE,
    ) as writer:
        writer.field(DEFAULT_INDEX_FIELD, *DEFAULT_INDEX_FIELD_SPEC)
        if polygon:
            writer.poly(
                [
                    [
                        inside,
                        (inside[0], outside[1]),
                        outside,
                        (outside[0], inside[1]),
                        inside,
                    ]
                ]
            )
        else:
            writer.line([[inside, outside]])
        writer.record(index)
    _write_prj(shp_path, albers.to_esri_wkt())
    return shp_path


def write_bowtie_domain_layer(
    shp_path: Path, *, albers: AlbersParams | None = None
) -> Path:
    """写一个含**自相交（bowtie）环**的 domain 图层，外加一个不相接的合法方形。

    与 `write_out_of_domain_layer` 同类豁免：直写源 CRS 的原始米制坐标、绕过锚点
    正向投影与 `METRIC_GUARD`——bowtie 的病态在于环自身的拓扑，不在坐标值，经锚点
    路径构造不出来。两个要素缺一不可：GEOS 对**单个** bowtie 面通常能自行修复，
    只有与至少一个其它单元一起 `unary_union` 时才抛 `TopologyException`。
    本图层只验错误路径与失败归属，不做坐标往返断言。
    """
    albers = albers or AlbersParams()
    shp_path = Path(shp_path)
    shp_path.parent.mkdir(parents=True, exist_ok=True)
    bowtie = [
        (0.0, 3.0e6),
        (1.0e5, 3.1e6),
        (1.0e5, 3.0e6),
        (0.0, 3.1e6),
        (0.0, 3.0e6),
    ]
    square = [
        (5.0e5, 3.0e6),
        (5.0e5, 3.1e6),
        (6.0e5, 3.1e6),
        (6.0e5, 3.0e6),
        (5.0e5, 3.0e6),
    ]
    with shapefile.Writer(
        shp=str(shp_path),
        shx=str(sidecar(shp_path, ".shx")),
        dbf=str(sidecar(shp_path, ".dbf")),
        shapeType=shapefile.POLYGON,
    ) as writer:
        writer.field(DEFAULT_INDEX_FIELD, *DEFAULT_INDEX_FIELD_SPEC)
        for index, ring in enumerate([bowtie, square], start=1):
            writer.poly([ring])
            writer.record(index)
    _write_prj(shp_path, albers.to_esri_wkt())
    return shp_path


def river_anchors(
    count: int, albers: AlbersParams | None = None
) -> tuple[tuple[Anchors, ...], ...]:
    """`write_rivers_layer(river_count=count)` 写进图层的那组 lon/lat 锚点。

    `write_rivers_layer` 只返回路径与 index 序列；需要坐标 oracle 的用例（例如
    「`Index` 非升序时 `reach_id` 仍与要素逐条对应」）用本函数取同一组锚点，
    避免测试去 import 生成器的私有实现。
    """
    return _river_anchors(count, albers or AlbersParams())
