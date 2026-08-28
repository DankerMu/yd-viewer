# forcing-chain

来源：compute-loop-design §4.2、§5（registry 临时生成）、§7.3、§8（warm-start 初态）、§9.1；design.md（本 change）D2/D5。

## ADDED Requirements

### Requirement: DB-free canonical 转换（NWM 快照）
canonical converter MUST 以本轮临时 raw manifest 与 `work/raw/` 副本为输入，在 work 的 object-store 内生成 canonical NetCDF 与 catalog；MUST NOT 依赖 PostgreSQL、NWM registry 服务或 NWM checkout import。

#### Scenario: 合成 raw 到 canonical
- **WHEN** 对合成 raw fixture 与对应 manifest 运行 converter
- **THEN** work object-store 内生成 canonical NetCDF 与 catalog，且过程无任何数据库连接

### Requirement: source-specific direct-grid forcing
forcing 生产 MUST 将 canonical 格点直接作为 SHUD forcing 站点（binding 权重恒为 1，不走 105 站 IDW）；生成的 forcing 首行 `Time_Day=0` MUST 锚定 cycle 时刻；IFS 与 GFS 使用各自 canonical grid 的 binding。

#### Scenario: 合成 canonical 到 forcing 包
- **WHEN** 对合成 canonical fixture 运行 direct-grid forcing 生产
- **THEN** 生成 forcing 包，站点集合等于格点集合，binding 权重全为 1

#### Scenario: 时间零点锚定 cycle
- **WHEN** 检查生成的 forcing 首行
- **THEN** `Time_Day=0` 对应 cycle 时刻（无 12Z 偏移）

### Requirement: work 内临时 registry
快照 file backend 要求 NWM 结构的 registry/model manifest 时，控制器 MUST 依据 TOML 配置在本轮 work 内临时生成，并随 work 删除；项目 MUST NOT 维护跨轮动态 registry。

#### Scenario: 临时 registry 生命周期
- **WHEN** forcing 链需要 registry 结构并完成本轮处理
- **THEN** registry 文件只存在于 work 内，work 清理后不留任何 registry 残留

### Requirement: SHUD 输入组装与固定参数
组装器 MUST 在 work 内由模型变体、forcing 包与本轮 warm-start 状态组装完整 SHUD 运行目录：运行目录的初始条件 MUST 为 `states/<source>/<T>.cfg.ic` 的内容，MUST 覆盖模型变体自带的率定末态 `cfg.ic`；MUST NOT 以变体自带初态运行日常 cycle。组装 MUST 固定覆盖 `START=0`、`END=7`、`DT_QR_DOWN=60`、`Update_IC_STEP=720`、`BINARY_OUTPUT=1`、`ASCII_OUTPUT=0`；00Z 与 12Z MUST 使用同一套参数。

#### Scenario: 参数覆盖
- **WHEN** 对合成变体与 forcing 包执行组装（cycle 分别为 00Z 与 12Z）
- **THEN** 运行目录的 SHUD 参数文件中六项参数为固定值，两种 cycle 无差异

#### Scenario: warm-start 状态覆盖变体初态
- **WHEN** 以内容可区分的 T 状态 fixture 与自带不同内容 `cfg.ic` 的合成变体执行组装
- **THEN** 运行目录的初始条件文件字节等于 T 状态，不等于变体率定末态

### Requirement: 快照模块可追溯
每个从 NWM 复制的模块 MUST 在文件头部记录来源 `NWM@8ae9b8f2` 与原仓相对路径；快照 MUST NOT 包含 DB/scheduler 分支代码。

#### Scenario: 溯源头部检查
- **WHEN** 对 `yd_producer` 内标记为快照的模块运行溯源检查测试
- **THEN** 每个快照文件的头部（前若干行）内存在一条 **`#` 注释行**含 `NWM@8ae9b8f2` 与该文件的原路径；写在 docstring 或字符串里不算数

#### Scenario: 未登记快照文件的反向守卫
- **WHEN** `producer/` 内出现**任意位置**带 `NWM@` 溯源注释、但不在快照勘察清单路径表内的文件
- **THEN** 溯源检查测试失败，指出该文件路径

正反两向 MUST 共用同一条「什么算溯源注释」的谓词（注释形式）；行预算只作用于正向，反向不设行预算。

#### Scenario: 快照 DB-free 隔离
- **WHEN** 对已落地的快照模块目录运行禁区检查
- **THEN** 无任何 `psycopg`、`DATABASE_URL`、scheduler/registry import 与环境变量读取
