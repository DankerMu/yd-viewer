# viewer-frontend Specification

## Purpose
TBD - created by archiving change m3-viewer. Update Purpose after archive.

## Requirements

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

### Requirement: 交互与曲线窗
hover 河段 MUST 高亮，点击 MUST 选中并打开可拖拽曲线窗；曲线窗 MUST 只有起报 cycle 下拉（来自 `GET /api/cycles`，默认为地图当前 cycle），MUST 在同一坐标轴显示 `series` 中每个可用 source 的 168 点曲线，x 轴为 `UTC(cycle)+lead` 的北京时间；切换下拉 MUST 只重取曲线，MUST NOT 改变地图着色或 cycle。曲线窗外观 MUST 同 NWM `M11RiverForecastPanel`：头部为图标、标题「河段 <reach_id>」、副标题「河段 q_down 流量预报 · <实际可用源以 + 连接>」与关闭按钮；其下为「起报」行（下拉项为北京时间）与源图例行（GFS `#22d3ee`、IFS `#34d399`，缺源项置灰划线）；图表 MUST 支持滚轮缩放时间轴（ECharts inside dataZoom，`filterMode: 'none'`，不随鼠标移动平移），图表内不再另画 ECharts 图例。

#### Scenario: cycles 到下拉项
- **WHEN** cycles 为 `2026082712`、`2026082700`
- **THEN** 下拉项文案为北京时间 `2026-08-27 20:00` 与 `2026-08-27 08:00`，顺序不变

#### Scenario: lead 到时间轴
- **WHEN** cycle `2026082700`、lead 5
- **THEN** x 轴标签为北京时间 `2026-08-27 13:00`

#### Scenario: 缺测流量保持空隙
- **WHEN** API返回保留168位置且含null的source曲线
- **THEN** 客户端类型接受null；null处不补零、不跨缺口连线，tooltip不把null显示为0，其他正常点和source保留；地图null河段继续显示缺失色

#### Scenario: 副标题按可用源
- **WHEN** 曲线响应 `series` 只含非空的 `gfs`
- **THEN** 副标题为「河段 q_down 流量预报 · GFS」，源图例行中 IFS 置灰划线；两源都在时为「· GFS+IFS」

#### Scenario: 滚轮缩放
- **WHEN** 构造曲线图表配置
- **THEN** 恰有一个 `type: 'inside'` 的 dataZoom，`filterMode` 为 `none`、`moveOnMouseMove` 为 false，且配置不含 ECharts `legend`

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

### Requirement: 复制来源登记与规模
从 NWM 复制的组件 MUST 在 `viewer/frontend/SNAPSHOT.md` 登记来源 commit、文件清单与逐文件删减；MUST NOT 复制 store、路由、OpenAPI client、代站弹窗、降水叠加、RBAC；任何源文件 MUST ≤ 1000 行且 MUST NOT 新增源文件 large-file-guard 豁免。唯一允许新增的豁免是生成文件 `viewer/frontend/pnpm-lock.yaml`；MUST 保留该 lockfile 供 frozen install 使用，不改变全局行数阈值。

#### Scenario: 登记存在
- **WHEN** 读取 `SNAPSHOT.md`
- **THEN** 含来源 commit SHA、每个复制文件一行说明，以及对上述六类禁复内容的逐文件删减记录

#### Scenario: 生成 lockfile 的单路径豁免
- **WHEN** `viewer/frontend/pnpm-lock.yaml` 超过 1000 行而前端源文件均未超限
- **THEN** guard 仅对该生成文件路径豁免，frozen install 仍通过；超过 1000 行的前端源文件仍被拒绝

### Requirement: 构建门禁
`corepack pnpm install --frozen-lockfile`、`tsc --noEmit`、`vitest run`、`pnpm build` MUST 全部通过；vitest MUST 覆盖色带映射、相对 URL 拼接、北京时间格式化、`basemaps.json` 解析、cycles → 下拉项、boundary 包围盒六个纯函数模块。

#### Scenario: 门禁命令
- **WHEN** 在 `viewer/frontend` 依次执行上述四条命令
- **THEN** 全部退出码为 0

### Requirement: 本地开发终端挂断清理
README一键全栈命令 MUST 将 SIGHUP 与 SIGTERM 接入同一清理路径；实际测试终端挂断后 MUST 停止本次记录的后台进程组并删除本次创建的临时目录，MUST NOT 删除其他文件或终止不属于本命令的进程。Ctrl-C、SIGTERM、任一子进程退出和固定端口冲突的既有行为 MUST 保持；不承诺 SIGKILL 后清理。

#### Scenario: 终端挂断
- **WHEN** 在独占测试PTY或tmux pane从viewer原样启动README命令，两服务就绪后关闭该测试终端
- **THEN** 本次两子进程组退出，8000/5173释放，打印的临时目录删除；仅向内层Python发信号不能代替此终端关闭验证

#### Scenario: 直接及重复挂断信号
- **WHEN** 另一次原样启动README命令后，向实际Python launcher发送SIGHUP，并在清理等待阶段重复发送SIGHUP
- **THEN** 复用同一清理路径，本次两子进程组退出、端口释放、临时目录删除；重复信号不能中断清理，此验证与终端关闭验证均须通过
