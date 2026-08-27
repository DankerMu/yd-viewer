"""基线 GIS 几何工具：`.prj` 装载、到 EPSG:4326 的重投影、最小只读 shapefile 读取。

设计纪律：

* **fail closed**：`.prj` 缺失/为空/编码非 UTF-8/不可解析，或 `.shp`/`.shx`/`.dbf`
  缺失/损坏，一律抛 `GeometryError` 且消息含**真正出错**的那个文件路径；不回退任何
  默认 CRS，不返回半成品几何。
* **单一公开异常**：本模块对外只有 `GeometryError`；pyproj 的 `CRSError`、pyshp 的
  `ShapefileException`、以及 `UnicodeDecodeError` 等原生异常一律转换，不外泄。
* **路径绑定**：`read_shapefile` 打开的每个文件都属于调用方点名的那一组 shapefile。
  旁文件只替换调用方路径的**最后一个** `.shp` 后缀得到（`yd.riv.shp` -> `yd.riv.shx`，
  而不是 `yd.shx`）；调用方给的 `.shp` 自身只做校验，绝不二次推导。后缀必须是**小写**
  `.shp`：旁文件按小写后缀推导，若同时接受 `RIVERS.SHP` 就会在大小写敏感文件系统上
  去找根本不存在的 `RIVERS.shx`，在大小写不敏感文件系统上则可能读到另一组同名小写
  文件（几何来自一组、属性/CRS 来自另一组）。故非小写后缀一律 fail closed，
  由调用方改名，行为与文件系统大小写敏感性无关。
* **轴序**：到 EPSG:4326 的 transformer 必须 `always_xy=True`，输出为 (lon, lat)。
  pyproj 默认按 CRS 权威轴序返回 (lat, lon)，而 GeoJSON 要求 (lon, lat)。

依赖面刻意最小（pyshp + pyproj + shapely），不引入 GDAL/geopandas/Fiona
（design.md D5、products-contract §6）。
"""

from __future__ import annotations

import struct
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import shape as shapely_shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

#: viewer 契约要求的输出坐标系（products-contract §6）
WGS84 = "EPSG:4326"

#: 一个 shapefile 组必需的三个文件
REQUIRED_SUFFIXES = (".shp", ".shx", ".dbf")

Feature = tuple[dict, BaseGeometry]


class GeometryError(Exception):
    """几何工具的唯一公开异常类型。"""


def _sidecar(shp_path: Path, suffix: str) -> Path:
    """把 `shp_path` 的**最后一个**后缀换成 `suffix`，得到同组旁文件路径。

    不能用 `path.with_suffix("")` 取「基名」再 `with_suffix(suffix)`：对
    `yd.riv.shp` 这类带点基名，前者得到 `yd.riv`、后者再把 `.riv` 也换掉，
    最终指向另一组 shapefile（`yd.*`）——静默读到错误图层。
    """
    return shp_path.with_name(shp_path.name[: -len(shp_path.suffix)] + suffix)


#: `.shx` 固定 100 字节头；其后是定长 8 字节索引记录（offset + length，各 4 字节）
_SHX_HEADER_BYTES = 100
_SHX_RECORD_BYTES = 8


def _check_shx_structure(shx_file: Path) -> None:
    """结构化先验 `.shx`，使索引损坏/截断的报错点名 `.shx` 而不是完好的 `.shp`。

    只用 stdlib 读头部，不碰 pyshp 内部属性：早先的实现靠 `Reader.shx_reader.offsets`
    触发 pyshp 内部的 `assert len(offsets_) == self.numShapes`，在 `python -O` 下断言
    被剥除，截断的 `.shx` 会一路读成 0 条记录，最终报成「几何数与属性记录数不一致」并
    点名**完好的** `.shp`——归属错误。

    校验三条（ESRI Shapefile 白皮书）：文件不短于 100 字节头；头后剩余字节是 8 的整数倍；
    头部 24-28 字节的大端 int32「文件长度（16-bit 字数）」与实际大小一致。
    """
    try:
        actual_size = shx_file.stat().st_size
        with shx_file.open("rb") as handle:
            header = handle.read(28)
    except OSError as exc:
        raise GeometryError(f"shapefile 索引读取失败: {shx_file}") from exc

    if actual_size < _SHX_HEADER_BYTES or len(header) < 28:
        raise GeometryError(
            f"shapefile 索引文件短于 {_SHX_HEADER_BYTES} 字节头({actual_size}): {shx_file}"
        )
    if (actual_size - _SHX_HEADER_BYTES) % _SHX_RECORD_BYTES:
        raise GeometryError(
            f"shapefile 索引记录区({actual_size - _SHX_HEADER_BYTES} 字节)"
            f"不是 {_SHX_RECORD_BYTES} 字节的整数倍: {shx_file}"
        )
    declared_size = struct.unpack(">i", header[24:28])[0] * 2
    if declared_size != actual_size:
        raise GeometryError(
            f"shapefile 索引头声明长度({declared_size} 字节)与实际大小"
            f"({actual_size} 字节)不一致: {shx_file}"
        )


def load_prj_crs(prj_path: str | Path) -> CRS:
    """读取 shapefile 的 `.prj` 旁文件 WKT 并构造 `pyproj.CRS`。

    文件缺失、为空、非 UTF-8 编码、WKT 不可解析一律抛 `GeometryError`
    （消息含路径），不回退到任何默认 CRS——现场投影参数不得在代码中猜测。

    以 `utf-8-sig` 解码：ArcGIS 写出的 `.prj` 常带 UTF-8 BOM，纯 `utf-8` 会把
    BOM 留在 WKT 头部使解析失败；而 cp1252/GBK 等非 UTF-8 编码仍会解码失败并
    转成 `GeometryError`（`UnicodeDecodeError` 是 `ValueError`，不被 `OSError`
    捕获，不转换就会外泄成调用方接不住的原生异常）。
    """
    path = Path(prj_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise GeometryError(f"无法读取投影旁文件: {path}") from exc
    try:
        wkt = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GeometryError(f"投影旁文件不是 UTF-8 编码: {path}") from exc
    if not wkt.strip():
        raise GeometryError(f"投影旁文件为空: {path}")
    try:
        return CRS.from_wkt(wkt)
    except Exception as exc:
        raise GeometryError(f"投影旁文件 WKT 不可解析: {path}") from exc


def to_wgs84_transformer(src_crs: CRS) -> Transformer:
    """构造 `src_crs` -> EPSG:4326 的 transformer，输出坐标为 (lon, lat)。

    `always_xy=True` 是硬要求：缺失时 pyproj 会按 EPSG:4326 的权威轴序返回
    (lat, lon)，与 GeoJSON 相反且不会报错，属静默错误。
    """
    try:
        return Transformer.from_crs(src_crs, WGS84, always_xy=True)
    except Exception as exc:
        raise GeometryError(f"无法构造到 {WGS84} 的坐标转换: {src_crs}") from exc


def reproject_geometry(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    """把 shapely 几何整体重投影，几何类型与部件/环结构保持不变。

    `shapely.ops.transform` 对 Multi* 的每个部件与面的每个内环逐一施加变换，
    不存在「只转外环/首部件」的分支。
    """
    try:
        return shapely_transform(transformer.transform, geom)
    except Exception as exc:
        raise GeometryError(f"几何重投影失败: {geom.geom_type}") from exc


def read_shapefile(shp_path: str | Path) -> tuple[CRS, list[Feature]]:
    """读取一组 shapefile，返回 `(crs, [(record_dict, geometry), ...])`。

    只做「几何 + DBF 记录 -> 内存对象」，不解释字段语义、不做数量校验
    （`reach_id` 语义与要素一致性属 prepare-variants 的 GeoJSON 生成任务）。

    `shp_path` 必须以**小写** `.shp` 结尾，否则抛 `GeometryError` 并点名该路径
    （见模块文档 路径绑定）；`.shx`/`.dbf`/`.prj` 只替换该最后一个后缀得到，
    调用方给的 `.shp` 自身只做存在性校验，不二次推导。
    """
    shp_file = Path(shp_path)
    if shp_file.suffix != ".shp":
        if shp_file.suffix.lower() == ".shp":
            raise GeometryError(f"shapefile 路径后缀必须是小写 .shp: {shp_file}")
        raise GeometryError(f"shapefile 路径必须以 .shp 结尾: {shp_file}")

    shx_file = _sidecar(shp_file, ".shx")
    dbf_file = _sidecar(shp_file, ".dbf")
    prj_file = _sidecar(shp_file, ".prj")
    group = {".shp": shp_file, ".shx": shx_file, ".dbf": dbf_file}
    for suffix in REQUIRED_SUFFIXES:
        if not group[suffix].is_file():
            raise GeometryError(f"shapefile 缺少必需文件: {group[suffix]}")

    crs = load_prj_crs(prj_file)

    _check_shx_structure(shx_file)
    # 几何与属性表分开打开，使损坏时的报错能精确指向出错的那个文件
    try:
        with shapefile.Reader(shp=str(shp_file), shx=str(shx_file)) as reader:
            shapes = reader.shapes()
    except Exception as exc:
        raise GeometryError(f"shapefile 几何读取失败: {shp_file}") from exc
    try:
        with shapefile.Reader(dbf=str(dbf_file)) as reader:
            records = [record.as_dict() for record in reader.records()]
    except Exception as exc:
        raise GeometryError(f"shapefile 属性表读取失败: {dbf_file}") from exc

    if len(shapes) != len(records):
        raise GeometryError(
            f"shapefile 几何数({len(shapes)})与属性记录数({len(records)})不一致: "
            f"{shp_file}"
        )

    try:
        geometries = [shapely_shape(shp.__geo_interface__) for shp in shapes]
    except Exception as exc:
        raise GeometryError(f"shapefile 几何不可解析: {shp_file}") from exc

    return crs, list(zip(records, geometries, strict=True))
