# 水文预报系统（yd-viewer）设计方案

状态：方案（已与需求方对齐结论，未开始实现）
日期：2026-08-26

## 1. 背景与边界

yd 流域由外部计算团队（zhaochen 方）用 SHUD 完成计算，服务对象是地方客户。
本项目交付一个**只消费 SHUD 计算产物的前端展示服务**：

- 最终部署在**客户自己的服务器**上，我们交付后**无法登录运维**；
- 同款实例旁路部署在 node-27 作为唯一实跑验证 oracle；
- 与 NWM 现有服务**零耦合**：不动 27 的 `/`、`/ops`、PG、ingest 任何一处。

## 2. 已拍板决策（grill 收敛结论）

| 分支 | 结论 |
|---|---|
| 更新形态 | 滚动更新；时次下拉可选，窗口 = 最新时次往前 **7 天** |
| 架构 | **无库直读**：无 PG/ingest，直接读 SHUD 原生二进制产物 |
| 产物来源 | 计算随迁客户服务器本地落盘，无跨机传输链路 |
| 接口契约 | 直读 SHUD 原生输出 + 完成标记（见 §4 产物契约） |
| 27 角色 | 同款实例读 NFS `/home/ghdc/yd`，作为交付验证 oracle |
| 暴露方式 | 客户侧 IP+端口直访；27 侧暂挂 `test.nwm.ac.cn/yd` |
| 功能范围 | **仅核心**：河网地图 + 河段点击流量过程线 + 时次下拉；无气象代站图层、无面雨量、无单元变量染色 |
| 代码组织 | 独立仓库（本仓库），与 NWM 主仓分离；前端组件按需一次性拷贝，不做共享包 |
| 访问控制 | 查看器不带登录，交给客户网络层 |
| 系统名称 | **水文预报系统** |

## 3. 关键事实基线（已实测/已查证）

- **产物是二进制**：yd 的 `yd.cfg.para` 为 `BINARY_OUTPUT=1, ASCII_OUTPUT=0`——
  NWM 仓的文本版 rivqdown 解析器不适用，须按 rSHUD `readout()` 的二进制格式新写解析。
- **二进制格式**（rSHUD ground truth）：整文件 little-endian float64 序列。
  v2：前 128 double（1024 字节）ASCII 文本头，`raw[128]`=起始日期 YYYYMMDD，
  `raw[129]`=列数 nc，`raw[130:130+nc]`=列编号表；v1：`raw[0]`=nc，`raw[1]`=起始日期。
  数据每行 nc+1 个 double，第 0 列为相对起始日的分钟数。模型边写边读时尾部可能有残行，须截断到整行。
- **文件可能很大**：当前 yd 参数 `END=9132`（天）、`DT_QR_DOWN=1440`（日输出），
  单个 rivqdown.dat 可达 ~290 MB → 读取端必须 **memmap 按列抽取**，禁止整文件进内存。
- **河网几何**：`input/yd/gis/river.shp` 共 3988 段，dbf `Index`(1..3988) 与 rivqdown
  列编号一一对应；投影为自定义 Albers（WKT 在 `.prj`），须重投影 EPSG:4326。
  `domain.shp` 7891 个三角单元，并集外边界作流域边界；yd 流域面积中心约 (103.2°E, 36.5°N)。
- **流量单位**：rivqdown 为 m³/day，展示转 m³/s（÷86400）。
- **27 底图现状**：NWM 前端用天地图 WMTS（带 key）。yd-viewer 底图 URL 走配置项，
  27 部署时配天地图；客户内网未确认能否联网 → **默认按完全离线设计**（见 §7）。

## 4. 产物目录契约（对计算方，另见 products-contract.md）

```
<products_root>/                     # 客户机自定；27 侧为 /home/ghdc/yd（22 视图 /ghdc/data/yd）
  input/yd/gis/{river,domain,seg}.*  # 流域几何（随 basin 包，已存在）
  output/<YYYYMMDDHH>/               # 每轮预报一个目录，命名按北京时间
    yd.rivqdown.dat                  # SHUD 原生二进制河道流量（唯一必需产物）
    DONE                             # 计算完成后最后写入；无此标记不展示
```

待计算方确认的契约条款：目录命名与 .dat 内部时间的基准（北京时间 vs UTC）、
DONE 最后写入、SHUD 版本升级/格式变化提前通知、旧时次清理归计算方。

## 5. 服务架构（无状态，只读）

单容器 = FastAPI 后端 + 构建后的前端静态文件。**无数据库、无磁盘缓存、无写路径**，
产物目录以只读方式挂载；进程内缓存（几何 GeoJSON、.dat 头部/时间轴，按 mtime 失效）。

API（全部相对路径，供根路径与反代子路径两种部署形态共用）：

| 端点 | 说明 |
|---|---|
| `GET api/meta` | 标题、底图 URL、边界 bbox、窗口天数 |
| `GET api/geometry/rivers` | 河网 GeoJSON（含 index/down/length/width） |
| `GET api/geometry/boundary` | 流域边界 GeoJSON |
| `GET api/cycles` | 7 天窗口内时次列表（倒序） |
| `GET api/cycles/{id}/reaches/{rid}/discharge` | 单河段流量序列（m³/s） |

守护性行为：产物列数 ≠ 河网段数时返回 409 并明说"产物与几何来自不同流域构建"；
时次滑出窗口返回 404；解析失败返回 502，不画错数据。

**7 天窗口语义**：以**最新时次**为基准往前 7 天（而非墙钟）——计算中断时页面
退化为展示最后一批时次，而不是空白。若需严格"自然日过去 7 天"可一行改回。

## 6. 前端（全新轻量，不移植 m11）

Vite + React + TypeScript + MapLibre GL + ECharts。m11 组件与流域/代站领域模型
深度耦合（已勘察确认），移植成本高于新写；仅借鉴其曲线窗交互与配色。

- 布局：顶栏（系统名"水文预报系统" + 时次下拉）+ 全屏地图 + 浮动曲线面板；
- 地图：底图（配置的栅格瓦片 URL，可为空 → 素底 + 流域边界）+ 河网线图层（宽度按 zoom/河宽），
  点击河段高亮并拉取该时次流量序列；
- 曲线：ECharts 时间轴折线，标题"河段 #id"，单位 m³/s；
- 构建以相对路径为 base（`./`），fetch 一律相对 URL —— `/yd/` 子路径与根路径同一份产物。

## 7. 部署形态

- **交付包（离线优先）**：`docker build`（多阶段：node 构建前端 → python slim 运行）→
  `docker save` 镜像 tar + compose 文件 + `.env` 模板 + 一页部署说明 + 产物契约，打成带版本号的 tar 包。
  客户侧 `docker load` + `docker compose up -d`，产物目录只读挂载。升级 = 换包重启。
- **27 staging**：同一镜像跑独立端口，`YDV_PRODUCTS_ROOT=/home/ghdc/yd`，
  nginx 增加一条 `location /yd/ { proxy_pass http://127.0.0.1:<port>/; }`（剥前缀），
  `nginx -t` 后 reload（不 restart）——这是"零影响 nwm 服务"的执行要点。
- 配置项：`YDV_PRODUCTS_ROOT`（必填）、`YDV_TILE_URL`、`YDV_WINDOW_DAYS=7`、
  `YDV_TITLE=水文预报系统`、`YDV_DONE_MARKER=DONE`、`YDV_PORT`。

## 8. 验证计划

| 项 | 手段 |
|---|---|
| .dat 解析（v1/v2/残行/非连续列表） | pytest：按格式规格合成二进制 fixture |
| 7 天窗口、DONE 门控、时次排序 | pytest：临时产物树 |
| 几何转换（真实 shapefile → 4326） | pytest：坐标落在 (102–105°E, 34–38°N) |
| 流量单位换算、404/409/502 分支 | pytest：TestClient |
| 前端 | tsc + build 门禁；交互走 27 实机浏览器验证 |
| 端到端 | 27 部署后 live receipt：`/yd/` 加载几何、时次列表、点击出曲线 |

注：zhaochen 尚未产出滚动预报产物（当前是 25 年率定长跑），27 端到端在其首轮
按契约落盘后才能闭环；在此之前用合成产物在 27 做同构验证。

## 9. 里程碑

1. **M1 后端**：解析器 + 几何 + API + pytest 全绿（本地）
2. **M2 前端**：核心三件套 + 构建门禁（本地）
3. **M3 打包**：Dockerfile + 离线 bundle 脚本 + 部署文档
4. **M4 27 staging**：部署 + nginx `/yd/` + live receipt（合成产物）
5. **M5 契约闭环**：计算方确认契约并首轮落盘 → 真产物 receipt → 出客户交付包

## 10. 开放项

1. 客户服务器能否联外网（底图/镜像拉取）——待问客户；默认离线设计。
2. 产物契约条款（§4）待与计算方过一遍；27 侧还需他们把每轮产物按契约落到
   `/ghdc/data/yd/output/`（现在其作业在 `/scratch/zhaochen/...`）。
3. 27 上 yd-viewer 的端口号与天地图 key 的配置来源（部署时定）。

## 11. 风险

- SHUD 版本差异导致 v1/v2 头部不同 → 解析器双版本自适应 + 契约要求变更通知。
- 客户服务器无 docker / 架构非 x86 → 交付前向客户确认运行环境（开放项 1 一并问）。
- 产物在 NFS 上 memmap 首次按列读取有冷读延迟（27 侧）→ 可接受；客户侧为本地盘无此问题。
