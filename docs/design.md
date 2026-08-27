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
3. cycle 下任一 source 有 `DONE`，该 cycle 即可选择；
4. IFS/GFS 互不阻塞；后完成的来源自然补成第二条曲线；
5. viewer 的 7 天窗口以最新成功 cycle 为锚，而不是墙钟；计算停更后仍展示最后一批数据；
6. producer 保留最新成功 cycle 往前 14 天，清理窗口外 source 目录。

完整条款见 [products-contract.md](products-contract.md)。

## 6. viewer 后端

单容器内的 FastAPI 同时服务业务 API、预转换 GeoJSON 和构建后的前端。容器无数据库、无磁盘缓存、无写路径。

### 6.1 API

所有前端请求使用相对路径；反代 `/yd/` 剥前缀后与根路径部署共用同一构建物。

| 端点 | 说明 |
|---|---|
| `GET /api/cycles` | 最新成功 cycle 往前 7 天，倒序返回 cycle 及实际可用 source |
| `GET /api/map/latest` | 选择最新一个任一来源 `DONE` 的 cycle；该 cycle 内 GFS 优先、否则 IFS；取 lead 0，返回 3988 个 m³/s 值 |
| `GET /api/cycles/{cycle}/reaches/{reach_id}` | 一次返回该河段该 cycle 的所有可用 GFS/IFS 曲线，各 168 点 |
| `GET /api/health` | 容器健康检查；确认服务运行且只读数据挂载可访问 |

几何作为同源静态文件提供，不再包装成 geometry API。

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
  "values": [12.3, 9.8, 0.4]
}
```

河段曲线一次返回可用双源，缺源时省略该 source，不让前端发两次请求再合并。

## 7. 前端

技术栈：Vite + React + TypeScript + MapLibre GL + ECharts，使用 `corepack pnpm`。

交互以 NWM 当前实际挂载的源页面 `OverviewPage` 为准；可复制组件仍沿用源码中的 `M11*` 命名：

- 全屏地图；
- 河网按 `/api/map/latest` 的流量着色；
- 右上矢量/卫星/地形底图按钮；
- 右下 m11 流量 colorbar；
- 地图缩放和比例尺；
- 点击河段高亮并打开可拖拽曲线窗；
- 曲线窗内只有起报时次下拉，GFS/IFS 同轴显示；
- 曲线窗切换历史 cycle 不改变地图，地图始终保持最新总览；
- 页面显示最新数据时间，不显示停更原因或内部计算状态。

从 NWM 复制并精简：

- `M11DraggableCurveWindow`；
- `ForecastChart` 与 ECharts tree-shaking 配置；
- 底图切换器和 MapLibre 样式生成；
- 河段 hover/selected 高亮；
- discharge 色带和图例；
- 起报下拉的纯 UI 外壳。

不复制 NWM 的 OpenAPI client、Zustand store、登录/RBAC、MVT、代站、多流域、监控和运维链接。复制代码记录来源 commit，之后由本仓独立维护。

NWM 源码中的旧天地图 key 不得复制。node-27 通过运行时配置注入有效的底图 URL 模板；客户侧未来可替换为内网瓦片或空底图，无需重建前端。

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
| API | FastAPI 测试覆盖 cycles、latest map、单/双源曲线、health |
| 前端 | TypeScript、构建和组件测试；base 与 fetch 均为相对路径 |

### 9.2 node-22 真产物

至少实跑一个 00Z 和一个 12Z：

- `START=0`、`DT_QR_DOWN=60`；
- DAT 恰有 168 行，分钟列 `0..10020`；
- 3988 个河段；
- T+12 状态可供下一轮精确接续；
- IFS/GFS 独立推进；
- NWM raw 未被 yd 修改；
- 单源失败不影响另一源完成。

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

1. **M1 文档与契约**：本方案、计算环、产物契约、agent ops 四份文档一致。
2. **M2 producer 基础**：prepare/init/run CLI、DB-free canonical/direct-grid、状态工具和本地测试。
3. **M3 viewer**：v2 解析、四个 API、m11 最小 UI 快照、前端构建门禁。
4. **M4 node-22 真计算**：00Z/12Z、IFS/GFS、T+12 接力和真实 DAT receipt。
5. **M5 node-27 真闭环**：旁路部署、Nginx `/yd/`、真实地图与曲线 receipt。

客户交付包和客户侧 producer 迁移不属于本期 M1–M5。

## 11. 尚待现场确定

这些值不得在代码中猜测：

- node-27 viewer 独立端口；
- node-27 有效天地图配置；
- Slurm partition、account、CPU、内存和 walltime；
- 外部基线模型包在首次 `prepare` 时的现场路径；
- 客户服务器的计算、下载和调度形态。
