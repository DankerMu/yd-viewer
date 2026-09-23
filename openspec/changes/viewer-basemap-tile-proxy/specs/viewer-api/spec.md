## ADDED Requirements

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
