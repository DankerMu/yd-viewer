# prepare-variants

来源：compute-loop-design §6.1、design.md §3.2、products-contract §6。

## ADDED Requirements

### Requirement: 拒绝覆盖已有变体
`prepare` 开始前 MUST 检查 `YD_ROOT/input/models/yd_gfs` 与 `yd_ifs`；任一存在即拒绝执行，且 MUST NOT 提供覆盖参数。

#### Scenario: 变体已存在即拒绝
- **WHEN** 模拟 `YD_ROOT` 中已存在 `yd_gfs` 目录时执行 prepare 编排
- **THEN** 拒绝退出，`YD_ROOT` 与 scratch 均无新写入

### Requirement: 生成两个 source-specific 变体
`prepare` MUST 经薄外壳按 source 各调用一次 mapping-builder，按 GFS、IFS 各自 canonical grid 生成两份 binding、重写后的 `sp.att` 与 forcing station 索引，产出完整运行变体 `yd_gfs`、`yd_ifs`；两者水文参数与率定状态来自同一基线，网格 binding MUST NOT 共用；变体 reach 数 MUST 等于 `config.toml` 的 `reach_count`，不一致时 MUST 拒绝提交。

#### Scenario: 编排按源各调用一次 builder
- **WHEN** 对合成基线包运行 prepare 编排（记录型假 builder 注入）
- **THEN** builder 恰被调用两次，两次入参的 source 与 canonical grid 不同，两次输出分别落入 `yd_gfs` 与 `yd_ifs`，两变体的水文参数文件同源一致

#### Scenario: reach 数不符拒绝提交
- **WHEN** 假 builder 产出的变体 reach 数不等于 `reach_count`
- **THEN** prepare 拒绝提交，`YD_ROOT` 无新写入

### Requirement: viewer GeoJSON 生成
`prepare` MUST 从基线 GIS 生成 EPSG:4326 的 `rivers.geojson` 与 `boundary.geojson`，落点固定为 `YD_ROOT/input/viewer/rivers.geojson` 与 `YD_ROOT/input/viewer/boundary.geojson`（products-contract §2）：河段要素带 SHUD `Index` 作为 `reach_id` 且数量与基线河网一致；boundary 为单元合并边界；坐标 MUST 按基线 `.prj` 自定义 Albers 投影重投影。

#### Scenario: 河网属性与数量
- **WHEN** 对含 N 条河段的合成基线 GIS 运行几何生成
- **THEN** `rivers.geojson` 含 N 个要素，每个带与 DBF `Index` 对应的 `reach_id`，坐标为经纬度

#### Scenario: 边界合并
- **WHEN** 对合成 domain 单元运行几何生成
- **THEN** `boundary.geojson` 为合并后的边界要素，坐标为经纬度

### Requirement: 提交后清理 scratch
变体与 GeoJSON 提交到 `YD_ROOT` 后，`prepare` MUST 删除 scratch 中间物；运行根 MUST NOT 长期保留基线包。

#### Scenario: 中间物不残留
- **WHEN** prepare 编排成功完成
- **THEN** 模拟 `YD_ROOT` 的 `input/models/` 含两变体、`input/viewer/` 下精确存在 `rivers.geojson` 与 `boundary.geojson`，scratch 工作目录已删除
