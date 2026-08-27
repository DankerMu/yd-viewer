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

from dataclasses import dataclass
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer

# 经纬度锚点（lon, lat）序列；part / ring 为其列表
Anchors = tuple[tuple[float, float], ...]

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
    count: int, albers: AlbersParams
) -> tuple[tuple[Anchors, ...], ...]:
    """N 个 domain 单元面；第一个带一个 interior ring（洞）。"""
    if count < 1:
        raise ValueError(f"unit_count 至少为 1，得到 {count}")
    units: list[tuple[Anchors, ...]] = []
    for i in range(count):
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
) -> tuple[int, ...]:
    indices = tuple(range(1, len(features) + 1))
    # 显式给出三个目标文件而非「基名」：`Writer(基名)` 对 `yd.riv.shp` 会写成
    # `yd.*`，与 `read_shapefile` 的路径绑定契约相悖，也无法生成带点基名的基线。
    with shapefile.Writer(
        shp=str(shp_path),
        shx=str(sidecar(shp_path, ".shx")),
        dbf=str(sidecar(shp_path, ".dbf")),
        shapeType=shape_type,
    ) as writer:
        writer.field("Index", "N", 10, 0)
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


def write_synthetic_baseline(
    directory: Path,
    *,
    river_count: int = 3,
    unit_count: int = 2,
    albers: AlbersParams | None = None,
    rivers_stem: str = "rivers",
    domain_stem: str = "domain",
) -> SyntheticBaseline:
    """在 `directory` 下生成 `rivers` / `domain` 两组 shapefile 及其 `.prj`。

    坐标 oracle 为 lon/lat 锚点：先用 pyproj 正向投影写入文件，测试再断言
    被测工具能把它们还原回锚点——期望坐标不得手写。

    `rivers_stem`/`domain_stem` 允许生成带点基名（如 `yd.riv`）与同目录同前缀的
    兄弟组（`yd.riv.*` 与 `yd.*`），供路径绑定回归使用。
    """
    albers = albers or AlbersParams()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    wkt = albers.to_esri_wkt()

    rivers_shp = directory / f"{rivers_stem}.shp"
    domain_shp = directory / f"{domain_stem}.shp"
    river_anchors = _river_anchors(river_count, albers)
    domain_anchors = _domain_anchors(unit_count, albers)
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
