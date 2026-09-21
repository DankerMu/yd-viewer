## MODIFIED Requirements

### Requirement: GET /api/map/latest
MUST 按 catalog 顺序取候选：最新 cycle 内 `gfs` 优先、其次 `ifs`，再更早 cycle；对候选执行 viewer-dat-reader 数据层读取，失败即 WARNING 并尝试下一候选；返回首个成功者的 `{"cycle","source","valid_time","values"}`，`valid_time` 为 `UTC(cycle)` 的 ISO 8601 带 `Z` 字符串，`values` 为 lead 0 行按 `reach_id` 升序的 m³/s 或 null 数组。无候选或全部失败 MUST 返回 404。

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

#### Scenario: 非有限流量不排除源
- **WHEN** 结构和分钟合法，优先 source 含 NaN/+Inf/-Inf（含整源全部缺测）
- **THEN** 该 source 保留，响应200；对应点显式为JSON null，不回落、不省略、不因缺测返回404；API schema允许这些null，其余排序/单位/168点不变

### Requirement: GET /api/cycles/{cycle}/reaches/{reach_id}
MUST 返回 `{"cycle","reach_id","lead_hours":[0..167],"series":{...}}`：对该 cycle 每个 catalog 可用 source 执行数据层读取，成功者进入 `series`（168 个 m³/s 或 null），失败者 WARNING 并省略；`series` 为空 MUST 404。`cycle` 不在可用列表内 MUST 404；`cycle` 不匹配 `^\d{10}$` 或小时 ∉ {00,12}、或 `reach_id` 不在权威集合内 MUST 返回 4xx（非 5xx）。

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

#### Scenario: 非有限流量不排除源
- **WHEN** 结构和分钟合法，优先 source 含 NaN/+Inf/-Inf（含整源全部缺测）
- **THEN** 该 source 保留，响应200；对应点显式为JSON null，不回落、不省略、不因缺测返回404；API schema允许这些null，其余排序/单位/168点不变
