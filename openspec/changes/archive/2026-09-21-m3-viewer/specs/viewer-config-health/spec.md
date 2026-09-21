## ADDED Requirements

### Requirement: 配置只来自环境变量
viewer 后端 MUST 只从 `YD_VIEWER_INPUT_DIR`、`YD_VIEWER_OUTPUT_DIR`、`YD_VIEWER_STATIC_DIR` 三个环境变量取路径；MUST NOT 读取配置文件、数据库或 `YD_ROOT` 整体路径；任一变量缺失、不是目录或不可读时 MUST 启动失败并在错误中给出变量名与路径。`YD_VIEWER_STATIC_DIR` 在容器内由 Dockerfile `ENV` 固定为前端构建物目录，MUST NOT 列入运维可覆盖的 env 清单（`env.example`/compose `env_file`）。

#### Scenario: 缺少输出目录变量
- **WHEN** 未设置 `YD_VIEWER_OUTPUT_DIR` 即创建应用
- **THEN** 创建失败，错误文本包含 `YD_VIEWER_OUTPUT_DIR`

#### Scenario: 输入目录不可读
- **WHEN** `YD_VIEWER_INPUT_DIR` 指向权限为 0o000 的目录
- **THEN** 创建失败，错误文本包含该路径

#### Scenario: 静态目录不在可覆盖清单
- **WHEN** 读取 `viewer/env.example` 与 `viewer/compose.example.yml`
- **THEN** 二者都不含 `YD_VIEWER_STATIC_DIR`

### Requirement: 启动几何自检
应用创建时 MUST 读取 `input/viewer/rivers.geojson` 与 `boundary.geojson` 各一次并校验：`rivers` 顶层 `type` 为 `FeatureCollection`，每个 Feature 的 `properties.reach_id` 存在、为整数（非 bool、非浮点、非字符串）且全体无重复；`boundary` 顶层为单个 `Feature`（不是 FeatureCollection），`geometry.type` 为 `Polygon` 或 `MultiPolygon`。任一不满足 MUST 启动失败并指明文件与原因。校验通过后 `reach_id` 集合成为本进程的河段权威集合。

#### Scenario: 几何完整（Polygon）
- **WHEN** `rivers` 含 5 个 `reach_id` 为 1..5 的 Feature，`boundary` 为 `Polygon` Feature
- **THEN** 应用创建成功，权威集合为 {1,2,3,4,5}

#### Scenario: boundary 为 MultiPolygon 也接受
- **WHEN** `boundary.geometry.type` 为 `MultiPolygon`
- **THEN** 应用创建成功

#### Scenario: rivers 顶层不是 FeatureCollection
- **WHEN** `rivers.geojson` 顶层是单个 `Feature`
- **THEN** 创建失败，错误文本包含 `rivers.geojson` 与 `FeatureCollection`

#### Scenario: reach_id 缺失或非整数
- **WHEN** 某 Feature 无 `reach_id`，或其值为 `1.0`、`"1"`、`true`
- **THEN** 创建失败，错误文本包含 `reach_id` 与该 Feature 序号

#### Scenario: reach_id 重复
- **WHEN** 两个 Feature 的 `reach_id` 相同
- **THEN** 创建失败，错误文本包含 `rivers.geojson` 与该重复值

#### Scenario: boundary 含多个 Feature
- **WHEN** `boundary.geojson` 是含 2 个 Feature 的 FeatureCollection
- **THEN** 创建失败，错误文本包含 `boundary.geojson`

#### Scenario: boundary 几何类型错误
- **WHEN** `boundary.geometry.type` 为 `LineString`
- **THEN** 创建失败，错误文本包含 `LineString`

#### Scenario: boundary 缺失
- **WHEN** `boundary.geojson` 不存在
- **THEN** 创建失败，错误文本包含 `boundary.geojson`

#### Scenario: rivers 缺失或不是 JSON
- **WHEN** `rivers.geojson` 不存在，或其内容不是合法 JSON
- **THEN** 创建失败，错误文本包含 `rivers.geojson`

#### Scenario: 请求期不重读几何
- **WHEN** 应用创建后删除 `rivers.geojson` 再请求 `/api/cycles`
- **THEN** 请求正常返回

### Requirement: health 端点
`GET /api/health` MUST 在 `output/` 可枚举时返回 `200 {"status":"ok","latest_cycle":<catalog 锚 cycle 或 null>}`；`output/` 不可枚举时 MUST 返回 503。`latest_cycle` MUST 取自 viewer-catalog 的同一枚举函数，MUST NOT 另写扫描逻辑。响应 MUST NOT 包含内部路径或运行状态。

#### Scenario: 空输出目录
- **WHEN** `output/` 存在但无任何 `DONE`
- **THEN** 返回 200，`latest_cycle` 为 `null`

#### Scenario: 有可用 cycle
- **WHEN** catalog 锚 cycle 为 `2026082712`
- **THEN** 返回 200，`latest_cycle` 为 `"2026082712"`

#### Scenario: 输出目录被移除
- **WHEN** 启动后 `output/` 被删除
- **THEN** 返回 503
