## MODIFIED Requirements

### Requirement: viewer GeoJSON 生成
`prepare` MUST 从基线 GIS 生成 EPSG:4326 的 `rivers.geojson` 与 `boundary.geojson`，落点固定为 `YD_ROOT/input/viewer/rivers.geojson` 与 `YD_ROOT/input/viewer/boundary.geojson`（products-contract §2）：河段要素带 SHUD `Index` 作为 `reach_id` 且数量与基线河网一致；rivers 顶层 MUST 为 FeatureCollection；boundary 顶层 MUST 为单个 Feature（properties 为空对象），其 geometry 为单元合并后的 Polygon 或 MultiPolygon，MUST NOT 包装为 FeatureCollection；坐标 MUST 按基线 `.prj` 自定义 Albers 投影重投影。

#### Scenario: 河网属性与数量
- **WHEN** 对含 N 条河段的合成基线 GIS 运行几何生成
- **THEN** `rivers.geojson` 含 N 个要素，每个带与 DBF `Index` 对应的 `reach_id`，坐标为经纬度

#### Scenario: 边界合并
- **WHEN** 对合成 domain 单元运行几何生成
- **THEN** `boundary.geojson` 顶层为单个 Feature，geometry 为合并后的 Polygon 或 MultiPolygon，坐标为经纬度；`rivers.geojson` 仍为 FeatureCollection

