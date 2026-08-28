"""基线 GIS 几何工具：`.prj` 装载、到 EPSG:4326 的重投影、最小只读 shapefile 读取。

设计纪律：

* **fail closed**：`.prj` 缺失/为空/编码非 UTF-8/不可解析，或 `.shp`/`.shx`/`.dbf`
  缺失/损坏，一律抛 `GeometryError`；不回退任何默认 CRS，不返回半成品几何。
* **失败归属**：每条 `GeometryError` 的消息点名该失败**责任范围内**的文件路径——
  责任可判到单个文件时只点名那一个，不可判时点名**全部候选**（宁可多报一个路径，
  也不冤枉一个完好文件，更不能漏掉真正损坏的那个）；与文件无关的失败（CRS 不可
  转换、几何重投影失败）不点名任何文件，改为点名出错的 CRS / 几何类型。
* **单一公开异常**：本模块对外只有 `GeometryError`；pyproj 的 `CRSError`、pyshp 的
  `ShapefileException`、以及 `UnicodeDecodeError` 等原生异常一律转换，不外泄。
* **路径绑定**：`read_shapefile` 打开的每个文件都属于调用方点名的那一组 shapefile。
  旁文件只替换调用方路径的**最后一个** `.shp` 后缀得到（`yd.riv.shp` -> `yd.riv.shx`，
  而不是 `yd.shx`）；调用方给的 `.shp` 自身只做校验，绝不二次推导。后缀必须是**小写**
  `.shp`：早先的旁文件推导一律拼小写后缀，若同时接受 `RIVERS.SHP`，在大小写敏感
  文件系统（CI ubuntu、node-22）上就会去找根本不存在的 `RIVERS.shx`；同目录若并存
  一组小写 `rivers.*`，还会退化成跨组配对（几何来自 `RIVERS.SHP`、属性/CRS 来自
  `rivers.dbf`/`rivers.prj`）。大小写不敏感文件系统上构造不出这种误配（同名仅大小写
  不同的两组文件无法共存），但拒绝发生在**字符串**层、任何 `is_file()` 之前，因此
  非小写后缀一律 fail closed 由调用方改名，行为与文件系统大小写敏感性无关。
* **轴序**：到 EPSG:4326 的 transformer 必须 `always_xy=True`，输出为 (lon, lat)。
  pyproj 默认按 CRS 权威轴序返回 (lat, lon)，而 GeoJSON 要求 (lon, lat)。

依赖面刻意最小（pyshp + pyproj + shapely），不引入 GDAL/geopandas/Fiona
（design.md D5、products-contract §6）。
"""

from __future__ import annotations

import json
import os
import struct
import uuid
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import mapping
from shapely.geometry import shape as shapely_shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import orient, unary_union
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

    只做「几何 + DBF 记录 -> 内存对象」，不解释字段语义：`reach_id` 等字段语义、
    以及「要素数是否符合业务预期」属 prepare-variants 的 GeoJSON 生成任务。
    这里唯一的数量检查是**组完整性**守卫——几何数与属性记录数必须一一对应，
    否则这一组文件互不匹配，配对结果无意义，必须 fail closed（见下方注释）。

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
    # 几何与属性表分开打开，使 .dbf 损坏时的报错不会误伤 .shp/.shx。
    # 几何读取同时消费 .shp 负载与 .shx 索引：结构先验放行后仍失败的输入
    # （如记录区被改写但大小/对齐/声明长度俱合法的 .shx），无法把责任判给其中
    # 某一个文件，故消息同时点名两者——宁可多报一个路径，也不冤枉一个完好文件。
    try:
        with shapefile.Reader(shp=str(shp_file), shx=str(shx_file)) as reader:
            shapes = reader.shapes()
    except Exception as exc:
        raise GeometryError(
            f"shapefile 几何读取失败（.shp 负载或 .shx 索引损坏）: {shp_file} 或 {shx_file}"
        ) from exc
    try:
        with shapefile.Reader(dbf=str(dbf_file)) as reader:
            records = [record.as_dict() for record in reader.records()]
    except Exception as exc:
        raise GeometryError(f"shapefile 属性表读取失败: {dbf_file}") from exc

    # 组完整性守卫。几何数由 .shp 负载与 .shx 索引共同决定（pyshp 按索引记录条数取
    # 几何），记录数由 .dbf 头部决定；三者任一被改坏都可能只表现为数量不等而各自读取
    # 无误——例如 .dbf 头部记录数被改写、.shx 被截断后又把头部声明长度改自洽、或是一份
    # 陈旧/错配的兄弟 .dbf。责任无法判给其中某一个，故与上面的几何读取失败同规则：
    # 点名全部候选，宁可多报一个路径，也不冤枉一个完好文件、更不漏掉真正损坏的那个。
    # 该分支不可删：删掉后末尾的 zip(..., strict=True) 会抛裸 ValueError，冲破本模块
    # 「单一公开异常」契约。两个数量本身保留在消息里，是最直接的诊断线索。
    if len(shapes) != len(records):
        raise GeometryError(
            f"shapefile 几何数({len(shapes)})与属性记录数({len(records)})不一致，"
            f"该组文件互不匹配: {shp_file} 或 {shx_file} 或 {dbf_file}"
        )

    # 与几何读取失败同规则地点名 `.shp` 与 `.shx` 两者，不点名 `.dbf`。
    # 这里手上只有一个 `Shape` 对象，判不出它来自哪个文件的损坏：合法的 ESRI NULL
    # 记录（shapeType 0，`.shp` 负载一侧）与「`.shx` 记录长度被改坏后在完好 `.shp`
    # 字节上错位读出的 NULL」到达此处完全同形——pyshp 在 shapes() 路径上只按 `.shx`
    # 的长度字段顺序推进（偏移字段不读），长度改大若干字就会让后续记录落到零字节上，
    # 而大小/对齐/头部声明长度俱不变，结构先验与数量守卫都判不出来。责任不可判即
    # 点名全部候选。`.dbf` 不在候选内：它已被独立打开读出记录且数量守卫已放行，
    # 对几何解码不负任何责任，点名它就是冤枉一个已知完好的文件。
    try:
        geometries = [shapely_shape(shp.__geo_interface__) for shp in shapes]
    except Exception as exc:
        raise GeometryError(
            f"shapefile 几何不可解析（.shp 负载或 .shx 索引损坏）: "
            f"{shp_file} 或 {shx_file}"
        ) from exc

    return crs, list(zip(records, geometries, strict=True))


#: viewer 只消费 `reach_id`（products-contract §6），其余 DBF 字段一律不透传
REACH_ID_FIELD = "Index"


def _geojson_text(obj: dict, describe: str) -> str:
    """序列化并同时充当有限性守卫，非有限坐标 -> `GeometryError` 点名 `describe`。

    `json.dumps` 默认把 `inf`/`nan` 写成裸 `Infinity`/`NaN`：RFC 8259 不承认它们，
    Python 的 `json.loads` 非标准地接受，浏览器的 `JSON.parse` 拒绝——一个投影域外
    顶点会让 viewer 整层图空白。`allow_nan=False` 让它在**落盘前**就地抛错。

    非有限坐标不是假想输入：`reproject_geometry` 走 pyproj 默认的 `errcheck=False`，
    投影反算有效域之外的顶点被静默映射为 `inf` 而不抛异常（pyproj 3.7.2 实测）。
    """
    try:
        return json.dumps(obj, ensure_ascii=False, allow_nan=False)
    except ValueError as exc:
        raise GeometryError(f"GeoJSON 含非有限坐标: {describe}") from exc


def _coerce_reach_id(value: object, shp_file: Path, position: int) -> int:
    """DBF `Index` 值 -> `reach_id`，只接受 `int`。

    pyshp 自己会把格式良好的 `N` 型字段解析成 `int`；到这里还是 `str` 说明 pyshp
    解析失败（字段是 `C` 型文本或数字位被写坏），是 `float` 说明字段带小数位——
    两者都是「基线与契约不符」，fail closed。刻意不做 `int(value)` 兜底：`int(1.9)`
    会把一个错的 `reach_id` 静默截断成一个**存在但错误**的河段编号。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise GeometryError(
            f"shapefile 的 {REACH_ID_FIELD} 字段值不是整数({value!r})，"
            f"第 {position} 条记录: {shp_file}"
        )
    return value


def _reach_ids(records: list[dict], shp_file: Path) -> list[int]:
    """按 DBF 记录顺序取出 `reach_id`，缺失/非整数/重复一律 fail closed。

    重复必须拒绝：viewer 按 `reach_id` 定位 DAT 的流量列，两条河段同号会让整列
    数据静默错位到错误的河段上——画得出来、但画的是别的河的流量。
    """
    reach_ids: list[int] = []
    first_position: dict[int, int] = {}
    for position, record in enumerate(records, start=1):
        if REACH_ID_FIELD not in record:
            raise GeometryError(
                f"shapefile 属性表缺少 {REACH_ID_FIELD} 字段，"
                f"第 {position} 条记录: {shp_file}"
            )
        reach_id = _coerce_reach_id(record[REACH_ID_FIELD], shp_file, position)
        if reach_id in first_position:
            raise GeometryError(
                f"shapefile 的 {REACH_ID_FIELD} 值 {reach_id} 重复"
                f"（第 {first_position[reach_id]} 条与第 {position} 条记录）: {shp_file}"
            )
        first_position[reach_id] = position
        reach_ids.append(reach_id)
    return reach_ids


def build_rivers_geojson(shp_path: str | Path) -> dict:
    """基线河网 shapefile -> EPSG:4326 的 GeoJSON `FeatureCollection`。

    要素数与顺序严格等同 DBF 记录（确定性输出）；`properties` **只含** `reach_id`，
    取自 DBF `Index`。0 要素的合法图层返回 0 要素的 `FeatureCollection`，不报错——
    「要素数是否符合 `reach_count`」属 prepare 编排（任务 10.3），不在这里发明约束。
    """
    shp_file = Path(shp_path)
    crs, features = read_shapefile(shp_file)
    reach_ids = _reach_ids([record for record, _ in features], shp_file)
    transformer = to_wgs84_transformer(crs)

    geojson_features = []
    for reach_id, (_, geom) in zip(reach_ids, features, strict=True):
        feature = {
            "type": "Feature",
            "properties": {"reach_id": reach_id},
            "geometry": mapping(reproject_geometry(geom, transformer)),
        }
        # 逐要素守卫，使非有限坐标的失败归属点到具体 reach_id 而不是整份文档。
        _geojson_text(feature, f"reach_id={reach_id} 的河段要素: {shp_file}")
        geojson_features.append(feature)
    return {"type": "FeatureCollection", "features": geojson_features}


def build_boundary_geojson(shp_path: str | Path) -> dict:
    """domain 单元面图层 -> 恰含 1 个合并边界要素的 `FeatureCollection`。

    合并顺序钉死为「先在基线源投影 CRS 内 `unary_union`，再对合并结果整体重投影」：
    源 CRS 是等积 Albers，共享边在该平面内严格重合；若先转经纬度再合并，共享边在角度
    空间不再逐点重合，会在溶解处留下缝隙与线状伪影。

    `properties` 为空对象——边界没有 `reach_id` 语义，viewer 只把它当流域轮廓画。
    """
    shp_file = Path(shp_path)
    crs, features = read_shapefile(shp_file)
    if not features:
        raise GeometryError(f"domain 图层没有任何单元，无法合并出边界: {shp_file}")

    # GEOS 会对自相交/拓扑非法的环抛 `shapely.errors.GEOSException`
    # （`TopologyException: side location conflict ...`）。`read_shapefile` 上游不做
    # OGC 有效性检查，这类图层经公共 API 完全可达，不转换就会让一个不含任何路径的
    # 原生异常逃出本模块的「单一公开异常」契约。
    try:
        merged = unary_union([geom for _, geom in features])
    except Exception as exc:
        raise GeometryError(
            f"domain 单元合并失败（几何自相交或拓扑非法）: {shp_file}"
        ) from exc
    if merged.geom_type not in ("Polygon", "MultiPolygon"):
        raise GeometryError(
            f"domain 单元合并结果不是面几何({merged.geom_type}): {shp_file}"
        )
    reprojected = reproject_geometry(merged, to_wgs84_transformer(crs))
    # 环向矫正前先做有限性守卫：`orient` 靠环的有符号面积定向，坐标含 inf 时面积为
    # nan，比较结果无意义且不报错。
    describe = f"合并边界要素: {shp_file}"
    _geojson_text(mapping(reprojected), describe)
    # RFC 7946 要求外环逆时针、内环顺时针；shapefile 约定与之相反，且 `unary_union`
    # 实测输出为顺时针外环——这一步是必须显式做的转换，不是 no-op。
    feature = {
        "type": "Feature",
        "properties": {},
        "geometry": mapping(orient(reprojected, sign=1.0)),
    }
    _geojson_text(feature, describe)
    return {"type": "FeatureCollection", "features": [feature]}


def _remove_paths(paths: list[Path]) -> list[Path]:
    """删除给定路径，返回**未能删除**的那些。

    只接受**本次调用自己写出的**路径：临时文件，以及已 `os.replace` 到终名的本次产物。
    终名上若原本存在调用方的同名文件，它在 `os.replace` 时就已被本次产物取代，这里
    删掉的是本次产物、不做恢复（覆盖语义见 `write_viewer_geojson` 的文档）。

    返回残留而不是吞掉删除异常，是为了让「清理也失败」这条罕见路径同样可被点名，
    而不是退化成 best-effort。
    """
    residue: list[Path] = []
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            residue.append(path)
    return residue


def write_viewer_geojson(
    *, rivers_shp: str | Path, domain_shp: str | Path, out_dir: str | Path
) -> tuple[Path, Path]:
    """把两份 viewer GeoJSON 写入 `out_dir`，返回 `(rivers 路径, boundary 路径)`。

    **无部分产物**，三段都要成立（下游 prepare 会把 scratch 目录整体提交，半份或
    截断的产物会被当成完整产物发布出去）：

    * 构建段：两份文档全部构建并序列化成字符串**成功之后**才开始落盘；
    * 落盘段：每份先写到 `out_dir` 内**本次调用专属**的临时名，两份都写完之后才
      `os.replace` 到终名。写入**中途**失败（ENOSPC/EFBIG 等）只会截断临时文件，
      终名上永远不会出现半份内容——这正是「记账后删」的朴素回滚做不到的：
      `write_text` 以 `w` 模式先截断再写，中途失败留下的是一个已存在的坏文件。
    * 收尾段：任何一步失败都删掉本次创建的临时文件与已提升到终名的文件，使 `out_dir`
      内不留本次的任何产物；删除自身失败时把残留路径写进消息一并报出，原始异常仍挂在
      `__cause__` 上——不掩盖，也不留「可能残留」的模糊语义。

    覆盖语义（诚实声明，不做做不到的承诺）：终名上若已存在调用方的同名文件，`os.replace`
    会原子地替换它，且**不会**在后续回滚中被恢复——回滚只保证「本次产物不残留」，不保证
    「调用方原文件不丢」。拒绝覆盖检查属 prepare 编排（任务 10.3），本函数不做。

    `out_dir` 由调用方给出并按需创建；本函数不解析 `YD_ROOT`、不做拒绝覆盖检查、
    不做提交与 scratch 清理——均属 prepare 编排（任务 10.3）。
    """
    rivers_text = _geojson_text(build_rivers_geojson(rivers_shp), str(rivers_shp))
    boundary_text = _geojson_text(build_boundary_geojson(domain_shp), str(domain_shp))

    target = Path(out_dir)
    rivers_out = target / "rivers.geojson"
    boundary_out = target / "boundary.geojson"
    # 本次调用专属后缀：同一 `out_dir` 上的并发/重复调用不会互相覆盖临时文件。
    token = f".{os.getpid()}-{uuid.uuid4().hex}.tmp"
    plan = [(rivers_out, rivers_text), (boundary_out, boundary_text)]
    temps = [path.with_name(path.name + token) for path, _ in plan]
    promoted: list[Path] = []
    try:
        target.mkdir(parents=True, exist_ok=True)
        for (_, text), tmp in zip(plan, temps, strict=True):
            tmp.write_text(text, encoding="utf-8")
        for (path, _), tmp in zip(plan, temps, strict=True):
            os.replace(tmp, path)
            promoted.append(path)
    except OSError as exc:
        residue = _remove_paths(promoted + temps)
        if residue:
            raise GeometryError(
                f"viewer GeoJSON 写出失败且清理未能删除本次产物"
                f"({'、'.join(str(path) for path in residue)}): {target}"
            ) from exc
        raise GeometryError(
            f"viewer GeoJSON 写出失败，out_dir 内未留下本次的任何文件: {target}"
        ) from exc

    stray = _remove_paths(temps)
    if stray:
        raise GeometryError(
            f"viewer GeoJSON 已写出但临时文件未能清理"
            f"({'、'.join(str(path) for path in stray)}): {target}"
        )
    return rivers_out, boundary_out
