# prepare-variants

来源：compute-loop-design §6.1、design.md §3.2、products-contract §6。

## ADDED Requirements

### Requirement: 拒绝覆盖已有产物
`prepare` 在任何 scratch 写入与任何 builder 调用之前 MUST 检查它将要写的**全部四个**终名——两个变体目录与两份 viewer GeoJSON——任一已存在即拒绝执行，且 MUST NOT 提供覆盖参数。被检查的路径 MUST 与实际提交时写入的路径由**同一个函数**计算，二者不得分叉；变体相对路径取自 `config.toml` 的 `variants.gfs`/`variants.ifs`，其值 MUST 为相对路径，绝对路径或含 `..` 的路径 MUST 拒绝执行。四个终名 MUST 两两不同，且任一终名 MUST NOT 是另一终名的祖先；不满足即在任何写入与任何 builder 调用之前拒绝执行。

#### Scenario: 变体已存在即拒绝
- **WHEN** 模拟 `YD_ROOT` 中已存在 `yd_gfs` 目录时执行 prepare 编排
- **THEN** 拒绝退出，`YD_ROOT` 与 scratch 均无新写入，注入的假 builder 调用次数为 0

#### Scenario: viewer GeoJSON 已存在即拒绝且原文件字节不变
- **WHEN** 模拟 `YD_ROOT` 的 `input/viewer/rivers.geojson` 已存在且内容已知时执行 prepare 编排
- **THEN** 拒绝退出，该文件字节与执行前完全一致，`input/models/` 无新目录，假 builder 调用次数为 0

#### Scenario: 变体相对路径为绝对路径即拒绝
- **WHEN** `config.toml` 的 `variants.gfs` 取绝对路径
- **THEN** prepare 拒绝退出，`YD_ROOT` 与该绝对路径下均无写入

### Requirement: 每次 builder 调用独占全新 scratch 目录
`prepare` MUST 为每次 builder 调用分配一个**全新、此前不存在**的 scratch 目录，并把它作为该次调用的 `variant_root` 传入；提交前 MUST 校验该目录的内容恰为预期产物集合，出现任何未预期条目（含 `.tmp` 残留）即拒绝提交。

#### Scenario: scratch 残留即拒绝提交
- **WHEN** 假 builder 在其 `variant_root` 内额外留下一个 `.tmp` 文件
- **THEN** prepare 拒绝提交，`YD_ROOT/input/models/` 下无任何变体目录

#### Scenario: scratch 目录必须是新建的
- **WHEN** prepare 编排为两次 builder 调用分配 scratch 目录
- **THEN** 两个目录互不相同、调用前均不存在，且均位于 `local.toml` 的 `scratch_root` 之下

### Requirement: 提交经 YD_ROOT 内 staging 后原地 rename
每个终名的最后一步 MUST 是一次**同文件系统内**的 rename，其源 MUST 位于 `YD_ROOT` 之内的本次专属 staging 位置；该 staging 位置 MUST NOT 落在 `input/viewer/` 之内（products-contract §2 只允许该目录存在两个文件），且无论成败 MUST 在返回前删除。`scratch_root` 与 `yd_root` 在生产上是两棵不同文件系统的树（agent-ops §4.1/§4.2），故 `prepare` MUST NOT 直接把 scratch 内的目录 rename 到 `YD_ROOT`；从 scratch 到 `YD_ROOT` staging 的搬运 MUST 按发布权限新建条目，MUST NOT 把计算节点的 uid/gid/mode 原样带入（agent-ops §10）。

#### Scenario: 提交源与终名同文件系统
- **WHEN** prepare 编排提交任一终名
- **THEN** 该次 rename 的源位于 `YD_ROOT` 之内，与终名同一 `st_dev`

#### Scenario: 四个终名 MUST 互不相同
- **WHEN** `config.toml` 的 `variants.gfs` 与 `variants.ifs` 取同一值
- **THEN** prepare 在任何写入与任何 builder 调用之前即拒绝，`YD_ROOT` 与执行前逐字节相同

#### Scenario: 终名互为祖先即拒绝
- **WHEN** `config.toml` 的 `variants.ifs` 落在 `variants.gfs` 之下
- **THEN** prepare 在任何写入与任何 builder 调用之前即拒绝，`YD_ROOT` 与执行前逐字节相同

#### Scenario: 提交中途失败不留新条目
- **WHEN** 提交阶段的首次 rename 失败
- **THEN** prepare 报错退出，`YD_ROOT` 内不留本次新建的任何条目（含为提交而新建的父目录与 staging 位置），scratch 工作目录已删除

### Requirement: 生成两个 source-specific 变体
`prepare` MUST 经薄外壳按 source 各调用一次 mapping-builder，按 GFS、IFS 各自 canonical grid 生成两份 binding、重写后的 `sp.att` 与 forcing station 索引，产出完整运行变体 `yd_gfs`、`yd_ifs`；两者水文参数与率定状态来自同一基线，网格 binding MUST NOT 共用；变体 reach 数 MUST 等于 `config.toml` 的 `reach_count`，不一致时 MUST 拒绝提交。

#### Scenario: 编排按源各调用一次 builder
- **WHEN** 对合成基线包运行 prepare 编排（记录型假 builder 注入）
- **THEN** builder 恰被调用两次，两次入参的 `source_id` 与 `grid_id` 不同，两次输出分别落入 `yd_gfs` 与 `yd_ifs`，两变体的水文参数文件同源一致

#### Scenario: 真实 builder 绑定 fail-closed
- **WHEN** 不注入假 builder、走生产 builder 绑定执行 prepare 编排
- **THEN** 在发起任何子进程之前即停并指名归属任务号，`YD_ROOT` 无任何写入，scratch 工作目录已删除；经 CLI 执行时退出码为 `3`（分阶段未实现），与其它 prepare 拒绝（退出码 `1`）可区分

#### Scenario: reach 数不符拒绝提交
- **WHEN** 假 builder 产出的变体 reach 数不等于 `reach_count`
- **THEN** prepare 拒绝提交，`YD_ROOT` 无新写入

#### Scenario: 变体率定末态缺 river 段即拒绝
- **WHEN** 假 builder 产出的变体率定末态 `cfg.ic` 没有 river 段
- **THEN** prepare 拒绝提交（MUST NOT 判定为 0 条 reach），`YD_ROOT` 无新写入

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

#### Scenario: 失败路径同样清理 scratch
- **WHEN** prepare 编排因 reach 数不符而拒绝提交
- **THEN** scratch 工作目录已删除，`YD_ROOT` 无新写入
