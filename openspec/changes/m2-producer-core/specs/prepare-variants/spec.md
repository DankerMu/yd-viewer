# prepare-variants

来源：compute-loop-design §6.1、design.md §3.2、products-contract §6。

## ADDED Requirements

### Requirement: 拒绝覆盖已有产物
`prepare` 在任何 scratch 写入与任何 builder 调用之前 MUST 检查它将要写的**全部四个**终名——两个变体目录与两份 viewer GeoJSON——任一已存在即拒绝执行，且 MUST NOT 提供覆盖参数。被检查的路径 MUST 与实际提交时写入的路径由**同一个函数**计算，二者不得分叉；变体相对路径取自 `config.toml` 的 `variants.gfs`/`variants.ifs`，其值 MUST 为相对路径，绝对路径或含 `..` 的路径 MUST 拒绝执行。四个终名 MUST 两两不同，且任一终名 MUST NOT 是另一终名的祖先；变体终名 MUST NOT 落在 `input/viewer/` 或 `output/` 之内（二者是 viewer 的读取面，products-contract §2）。不满足即在任何写入与任何 builder 调用之前拒绝执行。四个终名 MUST 在提交循环之前再复探一次。

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
- **THEN** 两个目录互不相同、**调用当时为空且由本次运行新建**（编排在调用 builder 前建目录，故「调用前不存在」只能指「不是上一次运行的遗留」），且均位于 `local.toml` 的 `scratch_root` 之下

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

#### Scenario: 变体终名落在 viewer 读取面即拒绝
- **WHEN** `config.toml` 的 `variants.gfs` 落在 `input/viewer/` 或 `output/` 之内
- **THEN** prepare 在任何写入与任何 builder 调用之前即拒绝，builder 调用次数为 0

#### Scenario: 守卫后出现的终名在提交前被复探拦下
- **WHEN** 某个终名在守卫通过之后、提交之前才出现
- **THEN** prepare 拒绝提交，该已存在文件的字节不变

#### Scenario: 提交中途失败不留新条目
- **WHEN** 提交阶段的首次 rename 失败
- **THEN** prepare 报错退出，`YD_ROOT` 内不留本次新建的任何条目（含为提交而新建的父目录与 staging 位置），scratch 工作目录已删除

### Requirement: 运行根 preflight 与清理容错
`prepare` MUST 在任何检查与写入之前校验 `local.toml` 的 `yd_root` 与 `scratch_root` 均为**绝对路径**且已存在为目录；不满足即拒绝执行。清理与回滚 MUST 逐步独立执行：任一步失败 MUST NOT 取消其余步骤，MUST NOT 替换或掩盖在途异常，也 MUST NOT 把已完成提交的运行报成失败。清理循环 MUST 只收集 `Exception`：`KeyboardInterrupt`/`SystemExit` 表示进程要停，MUST NOT 被清理循环吞掉；反之清理原语内部任何**未被翻译的** `OSError`（`os.close`/`os.rmdir`）MUST 被收集而非放行，否则它会取消其余步骤。回滚边界 MUST 捕获 `BaseException`：`KeyboardInterrupt` 落在 builder 或提交途中时 MUST 照常回滚后再原样抛出。

#### Scenario: 运行根非绝对路径即拒绝
- **WHEN** `local.toml` 的 `yd_root` 取 `~/yd` 或任何相对路径
- **THEN** prepare 在任何 builder 调用与任何写入之前拒绝，家目录与工作目录下均无新条目

#### Scenario: 运行根不存在即拒绝
- **WHEN** `local.toml` 的 `yd_root` 指向不存在的路径
- **THEN** prepare 拒绝执行，MUST NOT 凭空建出该路径，builder 调用次数为 0

#### Scenario: 单步清理失败不取消其余清理
- **WHEN** 提交失败后的回滚中有一步删除失败
- **THEN** 其余回滚步骤照常执行，调用方看到的仍是**原始**失败原因

#### Scenario: 提交成功后清理失败仍报成功
- **WHEN** 四个终名全部提交成功，随后 staging 清理失败
- **THEN** prepare 仍返回成功，清理失败作为告警随报告返回

#### Scenario: 清理步骤内的 KeyboardInterrupt 不被吞掉
- **WHEN** 回滚的最后一个清理步骤抛出 `KeyboardInterrupt`
- **THEN** 该 `KeyboardInterrupt` 原样上抛，MUST NOT 被收集成告警、MUST NOT 被在途异常取代

#### Scenario: 清理步骤内未翻译的 OSError 不取消其余步骤
- **WHEN** 某个清理步骤内部的 `os.close` 抛出裸 `OSError`
- **THEN** 其余清理步骤照常执行，调用方看到的仍是原始 `PrepareError`，该 `OSError` 作为告警随附

#### Scenario: builder 途中的 KeyboardInterrupt 仍触发回滚
- **WHEN** builder 调用中抛出 `KeyboardInterrupt`
- **THEN** 回滚照常完成（`YD_ROOT` 条目集合回到执行前、scratch 已清），随后该 `KeyboardInterrupt` 原样上抛

### Requirement: 生成两个 source-specific 变体
`prepare` MUST 经薄外壳按 source 各调用一次 mapping-builder，按 GFS、IFS 各自 canonical grid 生成两份 binding、重写后的 `sp.att` 与 forcing station 索引，产出完整运行变体 `yd_gfs`、`yd_ifs`；两者水文参数与率定状态来自同一基线，网格 binding MUST NOT 共用；变体 reach 数 MUST 等于 `config.toml` 的 `reach_count`，不一致时 MUST 拒绝提交。

#### Scenario: 编排按源各调用一次 builder
- **WHEN** 对合成基线包运行 prepare 编排（记录型假 builder 注入）
- **THEN** builder 恰被调用两次，两次入参的 `source_id` 与 `grid_id` 不同，两次输出分别落入 `yd_gfs` 与 `yd_ifs`，两变体的水文参数文件同源一致

#### Scenario: 真实 builder 绑定 fail-closed
- **WHEN** 不注入假 builder、走生产 builder 绑定执行 prepare 编排
- **THEN** 在发起任何子进程之前即停并**指名归属**（承接它的任务号，或在无编号任务时指名承接阶段），`YD_ROOT` 无任何写入，scratch 工作目录已删除；经 CLI 执行时退出码为 `3`（分阶段未实现），与其它 prepare 拒绝（退出码 `1`）可区分，且该退出码 MUST NOT 因清理失败而降级

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
