## MODIFIED Requirements

### Requirement: 地图着色与色带
地图 MUST 加载 `./geometry/rivers.geojson` 与 `./geometry/boundary.geojson`，按 `GET /api/map/latest` 的 `values` 以 `reach_id` 对应着色。色带 MUST 用 ≥ 阈值判定：`v ≥ 1000 → #CB181D`、`100 ≤ v < 1000 → #08519C`、`10 ≤ v < 100 → #2171B5`、`1 ≤ v < 10 → #4292C6`、`v < 1 → #7FB8DC`、`null → #94ADC7`。右下 MUST 显示 NWM `M11FloatingLegend` 外观的「径流量图例」卡片：标题带图层图标，依次列同一分档的 5 行（标签 `<1 m³/s`、`1–10 m³/s`、`10–100 m³/s`、`100–1000 m³/s`、`≥1000 m³/s`），末行为「无径流数据」（`#94ADC7`）；「无径流数据」只是缺失色说明，不是第六个数值档；分档 MUST NOT 改为 NWM 的 6 档。

#### Scenario: 分档映射含边界
- **WHEN** 值依次为 0.5、1、9.99、10、99.9、100、500、999.9、1000、5000、null
- **THEN** 颜色依次为 `#7FB8DC`、`#4292C6`、`#4292C6`、`#2171B5`、`#2171B5`、`#08519C`、`#08519C`、`#08519C`、`#CB181D`、`#CB181D`、`#94ADC7`

#### Scenario: 径流量图例行
- **WHEN** 渲染图例卡片
- **THEN** 标记含标题「径流量图例」，按序含 `<1 m³/s`、`1–10 m³/s`、`10–100 m³/s`、`100–1000 m³/s`、`≥1000 m³/s`，其后为「无径流数据」且色块为 `#94ADC7`

### Requirement: 地图基础交互
页面 MUST 纵向由页头横栏与其下全幅地图区组成，地图区底色 `#d7e7ef`，地图、浮层与曲线窗都位于地图区内；地图 MUST 提供带指南针（`visualizePitch`）的缩放控件与比例尺；有底图时版权归属 MUST 为展开式并含「© 天地图」，无底图时 MUST NOT 出现「© 天地图」；初始视野 MUST fit 到 `boundary.geojson` 的包围盒；MUST NOT 有其它相机逻辑（无飞行、无记忆视野）。

#### Scenario: 初始视野
- **WHEN** boundary 包围盒为 `[[100,30],[101,31]]`
- **THEN** 初始视野 fit 到该包围盒（由纯函数从 GeoJSON 计算包围盒并断言）

#### Scenario: 底图版权归属
- **WHEN** `basemaps.json` 含 `vector`（带 annotation）
- **THEN** 生成样式中该底图的每个栅格源 `attribution` 为 `© 天地图`；空样式不含任何栅格源

### Requirement: 底图切换
页面启动 MUST fetch `./basemaps.json`；右上按钮 MUST 只列出 JSON 中存在的键（`vector`/`satellite`/`terrain`），外观同 NWM `M11FloatingBasemapSwitcher`（矢量/卫星/地形各带图标，选中项 `primary-600` 底色）；每个底图为 `tiles` 栅格层加可选 `annotation` 栅格层；每条瓦片 URL MUST 先解析为绝对 URL 再交给 MapLibre：绝对 URL 原样保留；相对路径以页面目录（页面 URL 至最后一个 `/`）作字符串前缀拼接，拼接步骤 MUST NOT 把瓦片模板交给 `URL` 解析（会把 `{z}/{x}/{y}` 百分号编码，MapLibre 无法替换；页面目录本身仍可用 `new URL(".", document.baseURI)` 求得）；文件缺失（404）、内容为 `{}` 或三键全缺时 MUST 使用无瓦片的空样式且页面其余功能不受影响。

#### Scenario: 只配了两种底图
- **WHEN** `basemaps.json` 只含 `vector` 与 `satellite`
- **THEN** 按钮只有两项，默认选 `vector`，每个按钮带图标，选中项为 `primary-600` 底色

#### Scenario: 无底图配置（404）
- **WHEN** `basemaps.json` 返回 404
- **THEN** 地图以空样式渲染，河网与曲线窗仍可用

#### Scenario: 空对象
- **WHEN** `basemaps.json` 返回 200 与 `{}`
- **THEN** 解析结果为零底图、空样式，无按钮

#### Scenario: 相对瓦片路径
- **WHEN** 页面位于 `https://h/yd/` 且 `vector.tiles` 为 `["api/basemap/tianditu/vec/{z}/{x}/{y}"]`
- **THEN** 样式中的 tiles 恰为 `["https://h/yd/api/basemap/tianditu/vec/{z}/{x}/{y}"]`（花括号原样，非 `%7B`）；绝对 URL 条目字节不变

### Requirement: 页头
页头 MUST 为 NWM `SiteHeader` 同款横栏（高 84 px、`primary-900→800→700` 横向渐变）：左侧徽标、系统标题「永登流域水文模拟系统」与英文副标题「Yongdeng Basin Hydrological Modeling」，右侧合作单位 logo 条（`lg` 及以上宽度显示）；横栏 MUST NOT 含起报时间或其它状态；文档 `<title>` MUST 为同一标题。左上图层卡片 MUST 只有「水文」组的一项「流量 · q_down / m³/s」，恒为选中态且不是按钮，MUST NOT 含「气象」组或禁用占位；卡片底部 MUST 显示最新可用 cycle 的起报时间（北京时间，标「起报」与「北京时间」），无可用 cycle 时显示「暂无数据」；MUST NOT 显示停更原因、source 失败或任何内部计算状态。

#### Scenario: 页头时间
- **WHEN** `map/latest` 的 cycle 为 `2026082712`
- **THEN** 图层卡片含「起报 2026-08-27 20:00 北京时间」与「q_down / m³/s」

#### Scenario: 页头标题
- **WHEN** 页头横栏渲染
- **THEN** 标记为 `<header>`，含「永登流域水文模拟系统」「Yongdeng Basin Hydrological Modeling」、徽标与合作单位两张图，且不含「起报」

#### Scenario: 无可用 cycle
- **WHEN** 图层卡片以 `cycle = null` 渲染
- **THEN** 卡片含「暂无数据」，不含按钮、「气象」或「代站」
