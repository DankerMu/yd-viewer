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

### Requirement: 天地图瓦片反代
应用在设置了 `YD_TIANDITU_KEY` 时 MUST 提供 `GET /api/basemap/tianditu/{layer}/{z}/{x}/{y}`：`layer` MUST 属于 `vec/cva/img/cia/ter/cta`，`0 ≤ z ≤ 18`，`0 ≤ x, y < 2^z`，否则 4xx；上游为 `https://t{n}.tianditu.gov.cn/DataServer?T=<layer>_w&x=<x>&y=<y>&l=<z>&tk=<key>`（`n` 由瓦片坐标确定），请求 MUST 使用固定浏览器 User-Agent、MUST NOT 转发访客 Referer/Cookie、超时 10 s、只用标准库 `urllib`。上游 200 且正文以 PNG/JPEG 签名开头时 MUST 先以 tmp + `os.replace` 写入 `YD_BASEMAP_CACHE_DIR/<layer>/<z>/<x>/<y>` 再响应；命中或首次成功 MUST 带 `Cache-Control: public, max-age=604800`、`X-Tile-Cache: hit|miss` 与按签名判定的 `image/png`/`image/jpeg`。上游非 200、超时、错误或非图片正文 MUST 不落盘并回 502（429 回 503），且 `Cache-Control: no-store`；某层上游 429 后 60 s 内该层的未命中请求 MUST 直接 503 `no-store` 且不请求上游（缓存命中仍正常返回），其它层不受影响。key MUST NOT 出现在日志或响应正文。未设置 `YD_TIANDITU_KEY` 时该路径 MUST 保持 404（非 `index.html`）。

#### Scenario: 首次未命中后命中
- **WHEN** 上游对 `vec/1/0/0` 返回 200 PNG，连续请求两次
- **THEN** 第一次上游被调用一次、响应 `X-Tile-Cache: miss` 且缓存文件存在；第二次上游未被调用、响应 `X-Tile-Cache: hit`，两次 Content-Type 均为 `image/png`

#### Scenario: 上游失败不缓存
- **WHEN** 上游返回 500，或返回 200 但正文为 HTML，或超时
- **THEN** 响应 502 且 `Cache-Control: no-store`，缓存目录中无对应文件

#### Scenario: 限流冷却按层隔离
- **WHEN** `vec` 上游返回 429，随后 60 s 内再次请求 `vec` 与首次请求 `img`
- **THEN** 第一次 `vec` 503 `no-store`；第二次 `vec` 503 且上游未被调用；`img` 正常请求上游

#### Scenario: 参数校验
- **WHEN** 请求 `layer=foo`、`z=19` 或 `x=2^z`
- **THEN** 4xx，不请求上游

#### Scenario: 无 key 时不存在
- **WHEN** 未设置 `YD_TIANDITU_KEY` 并请求 `/api/basemap/tianditu/vec/1/0/0`
- **THEN** 404 且 Content-Type 不是 `text/html`

