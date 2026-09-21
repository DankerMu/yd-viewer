## MODIFIED Requirements

### Requirement: 相对路径部署
前端构建 MUST 使用 `base: './'`，所有 API、几何与 `basemaps.json` 请求 MUST 是相对路径；同一构建物 MUST 在根路径与经 `/yd/` 剥前缀反代两种部署下均可工作。构建产物 MUST NOT 含任何天地图 key 或以 `/` 开头的绝对资源引用。

#### Scenario: 相对 URL 拼接
- **WHEN** 页面 URL 为 `https://h/yd/` 且请求 cycles
- **THEN** 拼出的请求 URL 为 `https://h/yd/api/cycles`

#### Scenario: 构建物不含 key
- **WHEN** 对 `dist/` 全文搜索 `tianditu.gov.cn` 与 `tk=`
- **THEN** 零命中

#### Scenario: 显式文档URL
- **WHEN** helper接收 `https://h/yd/index.html` 或 `https://h/yd/viewer` 作为pageUrl并解析相对API/geometry/basemaps路径
- **THEN** MUST遵循WHATWG document-relative语义解析到 `/yd/` 下的兄弟路径，不将文档当目录；目录base由尾斜杠表达，根路径和 `/yd/` 目录输入行为不变
