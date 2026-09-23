## MODIFIED Requirements

### Requirement: 配置只来自环境变量
viewer 后端 MUST 只从 `YD_VIEWER_INPUT_DIR`、`YD_VIEWER_OUTPUT_DIR`、`YD_VIEWER_STATIC_DIR` 三个环境变量取路径；MUST NOT 读取配置文件、数据库或 `YD_ROOT` 整体路径；任一变量缺失、不是目录或不可读时 MUST 启动失败并在错误中给出变量名与路径。`YD_VIEWER_STATIC_DIR` 在容器内由 Dockerfile `ENV` 固定为前端构建物目录，MUST NOT 列入运维可覆盖的 env 清单（`env.example`/compose `env_file`）。此外 MUST 读取两个可选变量：`YD_TIANDITU_KEY`（未设或为空视为未设置，反代路由不注册）与 `YD_BASEMAP_CACHE_DIR`（缺省 `/cache`；启动时不校验，首次写入时按需创建），二者缺失 MUST NOT 影响启动或其余端点。

#### Scenario: 缺少输出目录变量
- **WHEN** 未设置 `YD_VIEWER_OUTPUT_DIR` 即创建应用
- **THEN** 创建失败，错误文本包含 `YD_VIEWER_OUTPUT_DIR`

#### Scenario: 输入目录不可读
- **WHEN** `YD_VIEWER_INPUT_DIR` 指向权限为 0o000 的目录
- **THEN** 创建失败，错误文本包含该路径

#### Scenario: 静态目录不在可覆盖清单
- **WHEN** 读取 `viewer/env.example` 与 `viewer/compose.example.yml`
- **THEN** 二者都不含 `YD_VIEWER_STATIC_DIR`

#### Scenario: 可选变量缺失不影响启动
- **WHEN** 未设置 `YD_TIANDITU_KEY` 与 `YD_BASEMAP_CACHE_DIR` 即创建应用
- **THEN** 创建成功，`/api/health` 200，`tianditu_key` 为 None，缓存目录缺省 `/cache`
