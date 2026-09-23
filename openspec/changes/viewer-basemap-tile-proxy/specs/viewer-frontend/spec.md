## MODIFIED Requirements

### Requirement: 底图切换
页面启动 MUST fetch `./basemaps.json`；右上按钮 MUST 只列出 JSON 中存在的键（`vector`/`satellite`/`terrain`），每个底图为 `tiles` 栅格层加可选 `annotation` 栅格层；每条瓦片 URL MUST 先解析为绝对 URL 再交给 MapLibre：绝对 URL 原样保留；相对路径以页面目录（页面 URL 至最后一个 `/`）作字符串前缀拼接，拼接步骤 MUST NOT 把瓦片模板交给 `URL` 解析（会把 `{z}/{x}/{y}` 百分号编码，MapLibre 无法替换；页面目录本身仍可用 `new URL(".", document.baseURI)` 求得）；文件缺失（404）、内容为 `{}` 或三键全缺时 MUST 使用无瓦片的空样式且页面其余功能不受影响。

#### Scenario: 只配了两种底图
- **WHEN** `basemaps.json` 只含 `vector` 与 `satellite`
- **THEN** 按钮只有两项，默认选 `vector`

#### Scenario: 无底图配置（404）
- **WHEN** `basemaps.json` 返回 404
- **THEN** 地图以空样式渲染，河网与曲线窗仍可用

#### Scenario: 空对象
- **WHEN** `basemaps.json` 返回 200 与 `{}`
- **THEN** 解析结果为零底图、空样式，无按钮

#### Scenario: 相对瓦片路径
- **WHEN** 页面位于 `https://h/yd/` 且 `vector.tiles` 为 `["api/basemap/tianditu/vec/{z}/{x}/{y}"]`
- **THEN** 样式中的 tiles 恰为 `["https://h/yd/api/basemap/tianditu/vec/{z}/{x}/{y}"]`（花括号原样，非 `%7B`）；绝对 URL 条目字节不变
