# 水文预报系统（yd-viewer）设计方案

状态：方案已定稿，尚未开始实现
日期：2026-08-27

## 1. 当前目标与边界

本期交付并验收一条独立的 yd 真数据闭环：

```text
NWM 已下载 raw GRIB（只读）
  → node-22 yd producer（IFS/GFS 双源 SHUD）
  → NFS YD_ROOT
  → node-27 yd-viewer
  → https://test.nwm.ac.cn/yd/
```

本期完成标准是 **node-22 真计算 → NFS → node-27 真展示**，不是客户侧部署：

- node-22 运行本仓 producer，产出 yd 自己的 IFS/GFS 预报；
- node-27 运行本仓 viewer，直接读取同一份 NFS 产物；
- NWM 仅提供只读 raw 数据、一次性 direct-grid builder，以及本仓精简快照代码的来源；
- yd 日常运行不依赖 NWM 数据库、scheduler、ingest、display API 或前端运行时；
- 客户侧 producer 的下载、调度和计算形态待客户环境明确后另行设计；当前只保证 producer 与 viewer 通过 `YD_ROOT` 文件边界解耦。

## 2. 已拍板决策

| 分支 | 结论 |
|---|---|
| 当前验收 | node-22 双源真计算，node-27 真产物展示 |
| 更新节律 | IFS/GFS 各自独立；UTC 00/12 两轮，严格按时序推进 |
| 预报长度 | 7 天；水文流量每小时输出，168 行 |
| viewer 架构 | 单容器 FastAPI + 构建后 React；无数据库、无写路径 |
| 数据接口 | `YD_ROOT` 下的 GeoJSON、SHUD v2 二进制和 `DONE` |
| 单源行为 | 任一来源 `DONE` 即发布 cycle；曲线按实际可用源显示一条或两条 |
| 地图默认帧 | 最新任一源完成 cycle；GFS 优先，否则 IFS；取 lead 0 |
| 历史窗口 | viewer 列最新成功 cycle 往前 7 天；producer 保留 14 天 |
| 前端复用 | 从 NWM 当前 m11 页面复制最小纯 UI/纯函数快照，独立维护 |
| 底图 | node-27 部署时注入天地图配置；矢量/卫星/地形切换；禁止硬编码 key |
| 访问控制 | viewer 不带登录，由部署网络边界负责 |
| 客户迁移 | 本期不承诺未知客户调度环境；仅固化可搬迁的文件边界 |

明确不做：

- PostgreSQL、Redis、ingest、消息队列、MVT 服务；
- `meta.json`、`status.json`、degraded 状态和运维页面；
- 气象代站、面雨量、单元变量等扩展图层；
- viewer 内的 shapefile/GDAL 运行时转换；
- NWM 登录、RBAC、全局 store、多流域和 `/ops` 代码；
- SHUD v1 二进制兼容和残行修复。

## 3. 系统结构

### 3.1 `YD_ROOT`

同一份 NFS 在两台主机上的路径不同：

- node-22：`/ghdc/data/yd`
- node-27：`/home/ghdc/yd`

逻辑布局：

```text
<YD_ROOT>/
  input/
    models/
      yd_gfs/                    # GFS direct-grid SHUD 变体
      yd_ifs/                    # IFS direct-grid SHUD 变体
    viewer/
      rivers.geojson             # EPSG:4326，含 reach_id
      boundary.geojson           # EPSG:4326 流域边界
  states/
    gfs/<cycle>.cfg.ic
    ifs/<cycle>.cfg.ic
  output/
    <YYYYMMDDHH>/
      gfs/
        yd.rivqdown.dat
        DONE
      ifs/
        yd.rivqdown.dat
        DONE
  logs/
    gfs/<cycle>.log
    ifs/<cycle>.log
```

viewer 容器只读挂载 `input/viewer` 与 `output`，看不到模型、状态和计算日志。

### 3.2 几何

一次性 `prepare` 从外部提供的 yd 模型包生成：

- `river.shp` 的 3988 条河段转为 `rivers.geojson`；
- DBF `Index` 作为 `reach_id`，当前为 1..3988，与 rivqdown 列编号对应；
- `domain.shp` 的 7891 个单元合并为 `boundary.geojson`；
- 自定义 Albers 投影按 `.prj` 重投影到 EPSG:4326。

几何固定放在 `YD_ROOT/input/viewer`。viewer 启动和请求期间不加载 shapefile，也不携带 GDAL/Fiona。

## 4. SHUD 产物语义

### 4.1 二进制格式

viewer 只支持本项目当前 SHUD 版本写出的 v2：

- 1024 字节文本头；
- 随后的 little-endian float64：起始日期、列数、列编号表；
- 数据区每行 `nc + 1` 个 float64，第 0 列为模型相对分钟，其后为河段值；
- 当前 `nc = 3988`，列编号与 GeoJSON `reach_id` 使用同一套 SHUD 编号。

格式权威仍是 rSHUD `readout()`，但绝对时间不使用其“日期头 + 分钟”的 00Z 假设。

### 4.2 时间

producer 固定覆盖 SHUD 参数：

```text
START = 0
END = 7
DT_QR_DOWN = 60
```

00Z 与 12Z 都使用 `START=0`。direct-grid forcing 的首行 `Time_Day=0` 即 cycle 时刻。

绝对时间唯一解释为：

```text
UTC cycle_id + DAT 第 0 列分钟
```

因此 12Z 不会因 v2 日期头只有自然日而静默提前 12 小时。

7 天、60 分钟输出得到：

- 168 行；
- 分钟列 `0, 60, 120, …, 10020`；
- lead 标签 `0h, 1h, …, 167h`；
- 每行代表标签之后一小时区间的平均河道流量，例如 lead 0 表示 `[cycle, cycle+1h)`。

这是 SHUD `PrintData` 的累计并按输出间隔平均行为，不是 168 个瞬时状态点。

viewer 结构层要求列编号无重复且集合等于几何权威集合；重复列不是取首列/末列的问题，而是在 catalog 枚举期作为不合规 DAT 排除。分钟轴测试采用独立 golden 证据，须杀死 validator 单独或与 synthetic writer 同时偏移 +60 的变异；等价公式改写不属于必须杀死的变异（#246 用户裁决）。

### 4.3 单位

`yd.rivqdown.dat` 中流量单位为 m³/day。后端统一除以 86400，API 和页面均使用 m³/s。普通页面文案只显示“流量 (m³/s)”；逐小时平均口径由本节和产物契约明确。

## 5. 产物发布与窗口

每个 source/cycle 正式目录只有：

```text
output/<cycle>/<source>/
  yd.rivqdown.dat
  DONE
```

规则：

1. producer 先完成 DAT 和下一轮状态的提交，最后创建空文件 `DONE`；
2. viewer 只枚举有 `DONE` 的 source 目录；
3. cycle 下任一 source 有 `DONE` 即完成发布；viewer 是否列出该 source 另须通过消费侧结构校验（不改变完成语义）；
4. IFS/GFS 互不阻塞；后完成的来源自然补成第二条曲线；
5. viewer 的 7 天窗口以最新成功 cycle 为锚，而不是墙钟；计算停更后仍展示最后一批数据；
6. producer 保留最新成功 cycle 往前 14 天，清理窗口外 source 目录。

完整条款见 [products-contract.md](products-contract.md)。

## 6. viewer 后端

单容器内的 FastAPI 同时服务业务 API、预转换 GeoJSON 和构建后的前端。无数据库、无磁盘缓存；「无写路径」指无业务写 API、不写 `YD_ROOT` 只读挂载。容器 entrypoint 在镜像内可写静态目录生成 `basemaps.json` 是唯一运行时配置写入，不是业务产物。

后端只从 `YD_VIEWER_INPUT_DIR`、`YD_VIEWER_OUTPUT_DIR`、`YD_VIEWER_STATIC_DIR` 三个 env 取目录，不读配置文件、数据库或整个 `YD_ROOT`；任一缺失、非目录或不可读即启动失败，错误带变量名及路径（缺失时说明未设置）。几何启动自检及 DAT 两层消费校验遵循 [products-contract.md](products-contract.md) §5.2/§6。容器内静态目录由镜像固定，不接受运维覆盖，详见 [agent-ops.md](agent-ops.md) §9.2。

### 6.1 API

所有前端请求使用相对路径；反代 `/yd/` 剥前缀后与根路径部署共用同一构建物。

| 端点 | 说明 |
|---|---|
| `GET /api/cycles` | 同一 catalog 枚举：最新结构层可用 cycle 往前 7 天（含边界），cycle 倒序、source 按 gfs/ifs；空态 `200 []` |
| `GET /api/map/latest` | 按 catalog 顺序尝试候选：最新 cycle 内 GFS 优先、其次 IFS，再更早 cycle；数据层（结构/分钟）失败 WARNING（路径与原因）后继续，首个成功者取 lead 0；`values` 为 m³/s 或 `null`。无候选或全部数据层失败 404。河段缺测不排除 source、不回落、不 404 |
| `GET /api/cycles/{cycle}/reaches/{reach_id}` | 对指定 cycle 每个 catalog 可用 source 做数据层读取，成功者入 series，各 168 个 m³/s 或 `null`；数据层失败 WARNING 并省略，series 为空 404。河段缺测不省略 source、不 404 |
| `GET /api/health` | output 可枚举时 `200 {"status":"ok","latest_cycle":"YYYYMMDDHH"或null}`；latest_cycle 来自同一 catalog 枚举，不另写扫描，不返回内部路径或运行状态 |
| `GET /api/basemap/tianditu/{layer}/{z}/{x}/{y}` | 同源天地图瓦片反代（用户裁决 2026-09-22，参照 NWM `apps/api/routes/basemap.py`）：`layer` ∈ `vec/cva/img/cia/ter/cta`，`0 ≤ z ≤ 18`，`x`/`y` 为该 z 下合法整数，否则 4xx；服务端持 `YD_TIANDITU_KEY`，上游 `https://t{0..7}.tianditu.gov.cn/DataServer?T=<layer>_w&x&y&l&tk`（按瓦片坐标选子域），固定浏览器 UA、不转发访客 Referer/Cookie，超时 10 s；命中先写 `YD_BASEMAP_CACHE_DIR/<layer>/<z>/<x>/<y>`（tmp + `os.replace`）再回 `Cache-Control: public, max-age=604800` 与 `X-Tile-Cache: hit|miss`，正文须以 PNG/JPEG 签名开头才算命中；上游失败（非 200、超时、非图片）永不缓存，回 502/503 且 `Cache-Control: no-store`；某层上游 429 后该层冷却 60 s 内直接 503（不打上游，不影响其它层）；未设 `YD_TIANDITU_KEY` 时该路由 404。不使用第三方 HTTP 客户端（标准库 `urllib` + 线程池），不做清理/配额统计 |

`output/` 不可枚举（含启动后删除、权限不可读）时以上四端点均返回 503。cycle 不在可用列表内为 404；格式不匹配 `^\d{10}$`、小时不为 00/12 或 reach_id 不在权威集合内为 4xx，不能 5xx。错误沿用 FastAPI 默认 `{"detail": ...}`，不自定义错误模型/异常处理器。

`/geometry/rivers.geojson`、`/geometry/boundary.geojson` 直接提供 `YD_VIEWER_INPUT_DIR` 同名文件（不复制、不改写，字节一致），不包装成 geometry API。`YD_VIEWER_STATIC_DIR` 以 `/` 挂载构建后 SPA，根路径提供 `index.html`；`/api/*` 与 `/geometry/*` 优先于静态挂载，未知 `/api/*` 返回非 HTML 的 404。单页无路由，不做 history fallback。

`/api/cycles` 示例：

```json
[
  {"cycle": "2026082700", "sources": ["gfs", "ifs"]},
  {"cycle": "2026082612", "sources": ["gfs"]}
]
```

`/api/map/latest` 示例：

```json
{
  "cycle": "2026082700",
  "source": "gfs",
  "valid_time": "2026-08-27T00:00:00Z",
  "values": [12.3, null, 0.4]
}
```

`values` 为按权威 `reach_id` 升序排列的 m³/s 或 JSON `null` 数组，不按 DAT 文件列位置；`valid_time` 是 `UTC(cycle)` 的带 `Z` ISO 8601 时间，12Z 示例为 `2026-08-27T12:00:00Z`。OpenAPI/响应类型必须声明该可空性，不得发出 `NaN`/`Infinity` token，也不得依赖框架默认把非有限浮点变成 `null`。

数据层失败（结构或分钟列，含非有限分钟）仍 WARNING 并回落/省略；河段 NaN/+Inf/-Inf 不是失败。优先 source 即使整行/整列全 `null` 也保留并返回 200，不回落到下一 source、不省略、不 404。例如最新 cycle 的 GFS 含缺测而 IFS 全有限时，map 仍选 GFS 且对应点为 `null`；曲线同时保留两源，缺测位置仍占 168 点。

河段曲线形状为 `{"cycle":"2026082700","reach_id":1,"lead_hours":[0,…,167],"series":{"gfs":[168个m³/s或null],"ifs":[168个m³/s或null]}}`（此处省略号仅说明形状）。缺源（数据层失败）省略键，不让前端发两次请求再合并。前端按 `UTC(cycle)+lead` 计算横轴，不从日期头推时刻。

## 7. 前端

技术栈：Vite 6 + React 18.3 + TypeScript 5.9 + MapLibre 4.7 + ECharts 6 + echarts-for-react 3 + Tailwind（版本随 NWM），使用 `corepack pnpm`（pnpm 10.11）。保留 NWM 组件的 Tailwind class 子集，不加插件或主题；不引入全局 store、路由或 react-query，状态使用 `useState`。

交互以 NWM 当前实际挂载的源页面 `OverviewPage` 为准；可复制组件仍沿用源码中的 `M11*` 命名：

- 全屏地图，加载 `./geometry/rivers.geojson` 与 `./geometry/boundary.geojson`；
- 河网按 `/api/map/latest` 的 `values` 与升序 `reach_id` 对应着色；前端类型将 `values`/`series` 声明为可空；`null` 使用下方缺失色，不因缺测改选其他 source；
- 右上只显示配置中存在的矢量/卫星/地形底图按钮；
- 右下流量 colorbar 与单位 `m³/s`；
- 地图缩放控件和比例尺，初始视野 fit 到 boundary 包围盒；无飞行或记忆视野等额外相机逻辑；
- hover 河段高亮，点击选中并打开可拖拽曲线窗；
- 曲线窗只有起报 cycle 下拉（来自 cycles，默认地图当前 cycle），series 的可用源各 168 点同轴显示；TypeScript 接受 `null`；缺测点不补零、不跨缺口连线，tooltip 不得把 `null` 显示为 0；
- 切换历史 cycle 只重取曲线，不改变地图着色或地图 cycle；
- 页头显示系统标题「永登流域水文模拟系统」（用户裁决 2026-09-22）、map/latest 的起报时间（标「起报」「北京时间」）与「流量 (m³/s)」，不显示停更原因、source 失败或内部计算状态；无可用 cycle 显示「暂无数据」；文档 `<title>` 同为该标题。

全部页面时间按 `Asia/Shanghai` 显示：cycle `2026082712` → `2026-08-27 20:00`；`2026082700` lead 5 → `2026-08-27 13:00`；下拉保持 API cycle 顺序。API 绝对时间仍为 UTC `Z`。

固定 5 档色带使用 ≥ 阈值，图例与地图使用同一分档：

| 流量 m³/s | 颜色 | 图例标签 |
|---|---|---|
| `<1` | `#7FB8DC` | `<1` |
| `1 ≤ v < 10` | `#4292C6` | `1–10` |
| `10 ≤ v < 100` | `#2171B5` | `10–100` |
| `100 ≤ v < 1000` | `#08519C` | `100–1000` |
| `≥1000` | `#CB181D` | `≥1000` |

`null`（含 API 缺测流量）用 `#94ADC7`，不是第六个数值档；色带不可配置。

从 NWM 复制并精简：

- `M11DraggableCurveWindow`；
- `ForecastChart` 与 ECharts tree-shaking 配置；
- 底图切换器和 MapLibre 样式生成；
- 河段 hover/selected 高亮；
- discharge 色带和图例；
- 起报下拉的纯 UI 外壳。

不复制 NWM 的 OpenAPI client、store、路由、登录/RBAC、MVT、代站弹窗、降水叠加、多流域、监控和运维链接。来源为 NWM `4f8d98263` 对应快照；在 `viewer/frontend/SNAPSHOT.md` 登记完整来源 commit、复制文件清单和逐文件删减（包括上述禁复内容），之后独立维护。任何源文件 ≤1000 行，不新增源文件 large-file-guard 豁免；唯一允许新增的豁免是生成文件 `viewer/frontend/pnpm-lock.yaml`；色带/图例只取必要片段。

前端构建 `base: './'`；API、几何及 `basemaps.json` 请求均为相对路径，构建物无以 `/` 开头的绝对资源引用。`https://h/yd/` 下 cycles 请求为 `https://h/yd/api/cycles`，同一构建物兼容根路径与剥前缀部署。
天地图 key 只在运行时经 env 注入：构建物不得含 `tianditu.gov.cn` 或 `tk=`；现役 NWM 天地图 key 可复用（用户裁决 2026-09-22：key 已绑定域名白名单），但只从 node-27 私有 env 复制到 yd 私有 env，不入 Git、不进日志/receipt。运行时 entrypoint 生成静态 `basemaps.json`：设置了 `YD_TIANDITU_KEY` 时（反代模式，用户裁决 2026-09-22）六条 URL 固定为相对路径 `api/basemap/tianditu/<layer>/{z}/{x}/{y}`（vector=vec/cva、satellite=img/cia、terrain=ter/cta），忽略六个 `YD_BASEMAP_*_URL`；未设置时沿用六个 env：`YD_BASEMAP_VECTOR_URL`、`YD_BASEMAP_SATELLITE_URL`、`YD_BASEMAP_TERRAIN_URL` 与各自 `YD_BASEMAP_*_ANNOTATION_URL`。形状为 `{"vector":{"tiles":[url],"annotation":[url]或null},...}`；缺底图 URL 则键缺席，注记可选，URL 原样写入、不进日志。前端把 `basemaps.json` 中的每条瓦片 URL 先以页面目录解析为绝对 URL（同 API 的 `resolveUrl`）再交给 MapLibre，绝对 URL 原样保留。
天地图 key 只在运行时经 env 注入：构建物不得含 `tianditu.gov.cn` 或 `tk=`；现役 NWM 天地图 key 可复用（用户裁决 2026-09-22：key 已绑定域名白名单），但只从 node-27 私有 env 复制到 yd 私有 env，不入 Git、不进日志/receipt。运行时 entrypoint 从六个 env 生成静态 `basemaps.json`：`YD_BASEMAP_VECTOR_URL`、`YD_BASEMAP_SATELLITE_URL`、`YD_BASEMAP_TERRAIN_URL` 与各自 `YD_BASEMAP_*_ANNOTATION_URL`。形状为 `{"vector":{"tiles":[url],"annotation":[url]或null},...}`；缺底图 URL 则键缺席，注记可选，URL 原样写入、不进日志。

页面启动 fetch `./basemaps.json`，只列出存在的 `vector`/`satellite`/`terrain`，按该顺序默认选首项；每种底图由 tiles 栅格层与可选 annotation 栅格层组成。404、`{}` 或三键全缺均用无瓦片空样式、无切换按钮，河网与曲线仍可用；无需重建前端。不增加 `/api/config`。

## 8. node-27 部署

- 单独镜像、单独 compose project、独立回环端口；不得占用 NWM display API 的 `:8080`；
- host 只读挂载 `/home/ghdc/yd/input/viewer` 和 `/home/ghdc/yd/output`；
- Nginx 只增加 `/yd/` location，`nginx -t` 成功后 reload，禁止 restart；
- 不修改 NWM 的 `/`、`/ops`、PG、ingest、autopipe、display API 或前端；
- node-27 是当前阶段唯一浏览器 live receipt oracle。

具体登录、发布、权限与验证纪律见 [agent-ops.md](agent-ops.md)。

## 9. 验证

### 9.1 本地

| 项 | 验证 |
|---|---|
| v2 DAT 解析 | 合成 168 行、3988 列 fixture；校验分钟列、单位换算和按列读取 |
| 目录契约 | 临时树覆盖无 DONE、单源、双源、7 天窗口和排序 |
| 几何 | 真实外部 fixture 预转换后落在 yd 合理经纬度范围，reach_id 与河网一致 |
| viewer 启动自检 | 合成几何覆盖缺失/坏 JSON、顶层类型、reach_id 缺失/类型/重复、boundary Polygon/MultiPolygon；请求期不重读几何 |
| API | FastAPI 测试覆盖 cycles、latest map、单/双源曲线、health |
| 前端 | `corepack pnpm install --frozen-lockfile`、`corepack pnpm typecheck`（`tsc --noEmit`）、`corepack pnpm test`（`vitest run`）、`corepack pnpm build`；纯函数覆盖色带、相对 URL、北京时间、basemaps 解析、cycles 下拉、boundary 包围盒；不做 DOM/视觉回归 |
| 容器打包 | 仓库根 `docker build -f viewer/Dockerfile .`；非 root shell 单测覆盖 basemaps 生成与 URL 不进日志；不冒充 M5 现场 health receipt |

CI 新增 `viewer-frontend` job，在 `viewer/frontend` 按上述顺序安装、typecheck、test、build；现有 producer、viewer-backend、openspec job 不变。

### 9.2 node-22 真产物

每源至少实跑 2 个连续 cycle，IFS/GFS 都覆盖 00Z 与 12Z：

- `START=0`、`DT_QR_DOWN=60`；
- DAT 恰有 168 行，分钟列 `0..10020`；
- 3988 个河段；
- T+12 状态可供下一轮精确接续：第二轮 receipt 引用第一轮写出的 `<T+12>.cfg.ic`；
- IFS/GFS 独立推进；
- NWM raw 未被 yd 修改；
- 单源失败不影响另一源完成：只观察不诱发，未发生时 receipt 写「未行使」。

### 9.3 node-27 live receipt

- `/yd/` 与 `/api/health` 可达；
- 最新地图着色、colorbar 和单位一致；
- GFS 优先，无 GFS 时自动显示 IFS；
- 河段曲线为 168 点，单源/双源均正确；
- 历史起报只影响曲线窗；
- 三种底图切换正常；
- NWM 原有 `/`、`/ops` 与 display API 不受影响。

本地绿不能替代 node-22 或 node-27 receipt。

## 10. 里程碑

阶段验证遵循 [agent-ops.md](agent-ops.md) §11.1 的 oracle 路由；产物语义以 [products-contract.md](products-contract.md) 为准；本地绿不能替代 node-22/node-27 receipt。

### M1 文档与契约

本方案、[compute-loop-design.md](compute-loop-design.md)、[products-contract.md](products-contract.md)、[agent-ops.md](agent-ops.md) 四份文档定稿且互相一致。

oracle：文档一致性检查（agent-ops §11.1）。

### M2 producer 基础

node-22 producer 的以下本地可验证代码：

- `prepare`/`init`/`run` 三入口 CLI 与 `config.toml`/`local.toml` 装载（[compute-loop-design.md](compute-loop-design.md) §5–6）；`prepare` 薄外壳只用 NWM 活动解释器、缺失即 fail closed（agent-ops §7.2）；
- NWM 快照模块及其最小测试：DB-free canonical converter、direct-grid forcing、object-store/path 基础函数、raw manifest 结构，记录来源 commit（同 §4.2）；
- `cfg.ic` 原生分段解析、重戳、负残差处理与结构检查（同 §8）；
- T+12 tracker 与 12 小时漏采补跑（同 §9）；
- IFS/GFS raw 完整性扫描与临时 manifest（同 §7）；
- 控制器：前沿推进、flock、Slurm 提交封装、NFS 提交顺序与崩溃恢复、保留与清理，以及 `yd-producer run` 对 `controller.run_sources` 的生产接线（逐源 Slurm executor、attempt driver、poll wait、独立 `sacct ExitCode` provider；同 §10–12）。

阶段门禁：本地测试全绿（[compute-loop-design.md](compute-loop-design.md) §13.1）。其中 direct-grid、forcing、SHUD、T+12、Slurm 按 agent-ops §11.1 的最终 oracle 在 M4 的 node-22 真运行；M2 通过不构成对这些能力的验证。

### M3 viewer

- v2 DAT 解析与 m³/day → m³/s 换算（本方案 §4）；
- `DONE` 目录契约枚举与 7 天窗口（§5、[products-contract.md](products-contract.md)）；
- 四个 API：cycles、map/latest、reach 曲线、health（§6）；几何作为同源静态文件提供；
- m11 最小 UI 快照：曲线窗、底图切换、色带、hover/selected，记录来源 commit（§7）；
- 前端构建门禁（§9.1）。

阶段门禁：本地测试与构建（§9.1）。M3 不依赖 node-22 真产物，可与 M2 并行推进；按 agent-ops §12 末句，没有 node-22 真产物时可用合成数据开发 viewer，但不得计作 M4/M5 完成。地图与曲线的最终 oracle 在 M5。

### M4 node-22 真计算

完成：2026-09-22（node-22 receipt `m4-stage5-rerun-20260921.md`；登记见 agent-ops §15.4）。

- 现场填写 `local.toml`（[compute-loop-design.md](compute-loop-design.md) §5、§14）；
- 经授权执行一次性 `prepare` 与 `init`：二者改变长期状态，须现场 receipt，不得由 cron 调用（agent-ops §8.1）；
- 每源至少实跑 2 个连续 cycle，IFS/GFS 都覆盖 00Z 与 12Z，全部满足 §9.2 与 [compute-loop-design.md](compute-loop-design.md) §13.2；T+12 精确接续以第二轮消费第一轮写出的状态文件为证；
- 单源失败隔离与 Slurm requeue/PREEMPTED 只观察不诱发，未发生时 receipt 写「未行使」，不写「已验证」；
- 按 [products-contract.md](products-contract.md) §8 与 agent-ops §10 设置发布目录权限，并在 M4 出口以 node-27 `nwm` 身份实读发布目录；
- 安装 cron 接管日常 `run`（锁由 CLI 自持，cron 行不套外层 flock），最终分钟点现场确定（agent-ops §8.2、§15、compute-loop §14）。

oracle：node-22 真运行 receipt（agent-ops §11.2）。

### M5 node-27 真闭环

完成：2026-09-22（`https://test.nwm.ac.cn/yd/`，node-27 receipt `/home/nwm/yd-viewer/receipts/m5-node27-receipt-20260922.md`；登记见 agent-ops §16.3；矢量底图受共用天地图 key 限流，已登记为已知偏差）。

- 以 node-27 `nwm` 身份实际读取验证同一 NFS（agent-ops §10、§12 步骤 4）；
- 现场确认独立端口并注入天地图等 env 配置（§11、agent-ops §9.2）；
- 构建/加载 yd 镜像并以只读挂载旁路启动；部署为对外动作，须明确授权（agent-ops §9.2）；
- 回环 health 通过后，经授权修改 Nginx `/yd/` location，`nginx -t` 后 reload（agent-ops §9.3）；
- 浏览器 receipt 全部满足 §9.3，并复核 NWM `/`、`/ops` 与 display API 不受影响。

oracle：node-27 live receipt（agent-ops §11.3）。

### 依赖与边界

- M4 依赖 M2：`yd-producer run` 的生产业务体（含 worker/receipt 适配）必须已实现并通过本地测试；此前禁止手工拼出等价生产流程（agent-ops §8.1）。M4 只负责真实 node-22 验证与 cron 安装，不接管 CLI 实现；
- M5 依赖 M3、M4，顺序遵循 agent-ops §12 标准发布顺序，每步留 receipt；
- 本期 M1–M5 固定同一套基线模型、SHUD 二进制和河网；升级须走干净 staging 根（[compute-loop-design.md](compute-loop-design.md) §6.1、[products-contract.md](products-contract.md) §9）；
- 客户交付包和客户侧 producer 迁移不属于本期 M1–M5。

## 11. 尚待现场确定

这些值不得在代码中猜测：

- node-27 viewer 独立端口：`127.0.0.1:8082`（用户裁决 2026-09-22，见 agent-ops §16）；
- node-27 有效天地图配置：复用 NWM 现役天地图 key（用户裁决 2026-09-22，见 §7 与 agent-ops §9.2）；
- `/yd/` 归属（用户裁决 2026-09-22）：主线 viewer 接管 `test.nwm.ac.cn/yd/`，应急副本继续持有 `nwm.ac.cn/yd/`（agent-ops §14.6）；何时以主线替换 `nwm.ac.cn/yd/` 另行裁决；
- Slurm partition、account、CPU、内存和 walltime；
- 外部基线模型包在首次 `prepare` 时的现场路径；
- 客户服务器的计算、下载和调度形态。
