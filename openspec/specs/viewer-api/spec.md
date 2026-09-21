# viewer-api Specification

## Purpose
TBD - created by archiving change m3-viewer. Update Purpose after archive.
## Requirements
### Requirement: GET /api/cycles
MUST 返回 viewer-catalog 结果的 JSON 数组，元素形如 `{"cycle":"YYYYMMDDHH","sources":["gfs","ifs"]}`；无可用 cycle 时 MUST 返回 `200 []`。

#### Scenario: 双源与单源混合
- **WHEN** `2026082700` 双源可用、`2026082612` 仅 gfs
- **THEN** 返回 `[{"cycle":"2026082700","sources":["gfs","ifs"]},{"cycle":"2026082612","sources":["gfs"]}]`

#### Scenario: 空态
- **WHEN** 无任何可用 cycle
- **THEN** 返回 200 与 `[]`

### Requirement: GET /api/map/latest
MUST 按 catalog 顺序取候选：最新 cycle 内 `gfs` 优先、其次 `ifs`，再更早 cycle；对候选执行 viewer-dat-reader 数据层读取，失败即 WARNING 并尝试下一候选；返回首个成功者的 `{"cycle","source","valid_time","values"}`，`valid_time` 为 `UTC(cycle)` 的 ISO 8601 带 `Z` 字符串，`values` 为 lead 0 行按 `reach_id` 升序的 m³/s 数组。无候选或全部失败 MUST 返回 404。

#### Scenario: GFS 优先
- **WHEN** 最新 cycle 双源可用
- **THEN** `source` 为 `gfs`

#### Scenario: 无 GFS 回落 IFS
- **WHEN** 最新 cycle 只有 `ifs` 可用
- **THEN** `source` 为 `ifs`

#### Scenario: 分钟列坏则回落
- **WHEN** 最新 cycle `gfs` 的 DAT 第 0 列整体偏移、`ifs` 合规
- **THEN** `source` 为 `ifs`，日志出现含 `gfs` 路径的 WARNING

#### Scenario: 12Z 的 valid_time
- **WHEN** 最新 cycle 为 `2026082712`
- **THEN** `valid_time` 为 `2026-08-27T12:00:00Z`

#### Scenario: 无可用 cycle
- **WHEN** 无任何 `DONE`
- **THEN** 返回 404 且体含 `detail`

### Requirement: GET /api/cycles/{cycle}/reaches/{reach_id}
MUST 返回 `{"cycle","reach_id","lead_hours":[0..167],"series":{...}}`：对该 cycle 每个 catalog 可用 source 执行数据层读取，成功者进入 `series`（168 个 m³/s），失败者 WARNING 并省略；`series` 为空 MUST 404。`cycle` 不在可用列表内 MUST 404；`cycle` 不匹配 `^\d{10}$` 或小时 ∉ {00,12}、或 `reach_id` 不在权威集合内 MUST 返回 4xx（非 5xx）。

#### Scenario: 双源曲线
- **WHEN** 请求可用双源 cycle 的 reach 1
- **THEN** `series` 含 `gfs` 与 `ifs` 两键，各 168 值，`lead_hours` 长度 168 且首尾为 0 与 167

#### Scenario: 单源曲线
- **WHEN** 该 cycle 只有 `ifs` 可用
- **THEN** `series` 只含 `ifs`

#### Scenario: 一源分钟列坏
- **WHEN** 双源 cycle 中 `gfs` 第 0 列偏移
- **THEN** `series` 只含 `ifs`，日志含 `gfs` 路径 WARNING

#### Scenario: reach 越界
- **WHEN** `reach_id` 为 99999
- **THEN** 返回 4xx，响应体含 `detail`

#### Scenario: cycle 不存在
- **WHEN** `cycle` 为合法格式但无可用 source
- **THEN** 返回 404

### Requirement: 错误与不可用
`output/` 不可枚举时四个端点（cycles、map/latest、曲线、health）MUST 返回 503；错误响应体 MUST 使用 FastAPI 默认的 `{"detail": ...}` 形状，MUST NOT 自定义错误模型或异常处理器。

#### Scenario: 输出目录不可读
- **WHEN** `output/` 权限为 0o000
- **THEN** `/api/cycles`、`/api/map/latest`、`/api/cycles/2026082700/reaches/1`、`/api/health` 均返回 503 且体含 `detail`

### Requirement: 静态几何与 SPA 托管
应用 MUST 在 `/geometry/rivers.geojson` 与 `/geometry/boundary.geojson` 直接提供 `YD_VIEWER_INPUT_DIR` 内的同名文件（不复制、不改写）；MUST 把 `YD_VIEWER_STATIC_DIR` 挂在 `/` 提供构建后前端；`/api/*` 与 `/geometry/*` MUST 先于静态挂载匹配；未知 `/api/*` 路径 MUST 返回 404 而不是 `index.html`。

#### Scenario: 几何可取
- **WHEN** 请求 `/geometry/rivers.geojson`
- **THEN** 返回 200，体与磁盘文件字节一致

#### Scenario: 未知 API 不落 SPA
- **WHEN** 请求 `/api/nope`
- **THEN** 返回 404 且 Content-Type 不是 `text/html`

#### Scenario: 根路径返回前端
- **WHEN** 静态目录含 `index.html` 并请求 `/`
- **THEN** 返回 200 与该文件内容

