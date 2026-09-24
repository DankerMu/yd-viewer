## MODIFIED Requirements

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
