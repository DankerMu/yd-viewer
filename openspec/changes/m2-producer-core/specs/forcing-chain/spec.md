# forcing-chain

来源：compute-loop-design §4.2、§5（registry 临时生成）、§7.3、§8（warm-start 初态）、§9.1；design.md（本 change）D2/D5。

## ADDED Requirements

### Requirement: DB-free canonical 转换（NWM 快照）
canonical converter MUST 以本轮临时 raw manifest 与 `work/raw/` 副本为输入，在 work 的 object-store 内生成 canonical NetCDF 与 catalog；MUST NOT 依赖 PostgreSQL、NWM registry 服务或 NWM checkout import。

#### Scenario: 合成 raw 到 canonical
- **WHEN** 对合成 raw fixture 与对应 manifest 运行 converter
- **THEN** work object-store 内生成 canonical NetCDF 与 catalog，且过程无任何数据库连接

#### Scenario: 真实 GRIB 读路径被覆盖
- **WHEN** 对**合成 GRIB2 样本**（非 NetCDF 替身）与携带 `metadata.grib_short_name` 的 manifest entry 运行 converter
- **THEN** converter 经 cfgrib 后端读取该样本并产出 canonical NetCDF 与 catalog；**MUST NOT** 静默回退到 netcdf4 后端——回退是生产路径未被覆盖的假绿形态，测试 MUST 能把回退判红

#### Scenario: 运行期无出站连接
- **WHEN** 在拦截出站 socket 连接的闸门下执行完整的 manifest 转换（读 raw → 转换 → 写产物 → 写 catalog）
- **THEN** 闸门一次都不被触发；converter 的构造签名内**不存在** repository 形参，模块内**不存在** `CanonicalRepository` 协议

### Requirement: source-specific direct-grid forcing
forcing 生产 MUST 将 direct-grid binding 声明的 canonical `grid_cell_id` 直接作为 SHUD forcing 站点（每个站点/变量恰一条 mapping，权重恒为 1，不走 105 站 IDW）；输出站点集合 MUST 与 binding 的 grid-cell 集合一一对应，未被 binding 引用的 canonical 额外格点不得成为站点。生成的 forcing 首行 `Time_Day=0` MUST 锚定显式传入的 cycle 时刻；IFS 与 GFS MUST 使用各自 canonical grid 的 binding，不得跨 source 复用。

#### Scenario: 合成 canonical 到 forcing 包
- **WHEN** 对包含两个 bound grid cells 与一个 unbound extra cell 的合成 canonical fixture 运行 direct-grid forcing 生产，其中两站风分量分别为 `(u,v)=(3,4)` 与 `(6,8)`
- **THEN** 生成的 forcing 包恰有两个站点，站点值逐项等于各自绑定 canonical cell 的值，每个站点/变量只有一条 `method="direct_grid"`、`weight=1.0` mapping，且不读取或输出额外格点
- **THEN** 每份 station CSV 第 1 行为 `<row-count>\t6\t<start-date>\t<end-date>`，第 2 行逐字为 `Time_Day\tPrecip\tTemp\tRH\tWind\tRN`，第 3 行是首个数据行且两站该行 Wind 分别为手算值 `5` 与 `10`，Press 不进入 SHUD CSV

#### Scenario: source-specific binding 隔离
- **WHEN** 以同一 cycle 分别对 grid id/cell id 可区分的 GFS 与 IFS 合成 canonical 和 binding 运行 forcing 生产
- **THEN** 两个 forcing 包各自只包含本 source binding 的站点、grid-cell 值与 lineage，任一 source 的 binding 都不被另一 source 复用

#### Scenario: repository 返回值不得绕过 source 隔离
- **WHEN** 注入式 repository 返回一份由 source-less parser 或 direct constructor 产生、同时声明 GFS/IFS 的 contract
- **THEN** `ForcingProducer` 在 repository 返回边界再次验证当前 source 单例并在任何 mapping/package 写入前拒绝；该约束不得只由 file parser 保证

#### Scenario: 时间零点锚定 cycle
- **WHEN** 分别以 UTC 00Z 与 12Z cycle 运行 forcing 生产，并检查每份 SHUD station CSV 的首个数据行
- **THEN** 首行 `Time_Day=0` 对应显式传入的 cycle 时刻，12Z 不增加 0.5 天偏移

#### Scenario: 缺 cycle 行时拒绝重锚
- **WHEN** canonical 输入的最早可产出 valid time 晚于显式 cycle
- **THEN** forcing 生产 fail closed、不得把该 valid time 重标为 `Time_Day=0`，且不得留下 ready forcing package/version

#### Scenario: 绑定格点缺失或身份不匹配
- **WHEN** binding 引用 canonical 中不存在的 `grid_cell_id`，或 binding 的 source/grid identity 与 canonical 不一致
- **THEN** forcing 生产在 ready 输出前稳定失败，不回退 IDW，也不留下 ready forcing package/version

#### Scenario: 非法 cycle 不得碰撞合法 ready 状态
- **WHEN** 已存在合法 12Z ready 后，以 06Z、12:30、非零秒或非零微秒调用公开 forcing seam
- **THEN** 请求在任何 repository lookup/write/cleanup 前稳定失败，既不得生成非法 cycle 产物，也不得改变原 12Z version、sidecar、handoff 或 cycle-ready 证据

#### Scenario: catalog row 与 canonical 对象身份联合校验
- **WHEN** catalog row 的 `object_uri`/checksum 指向另一 source、另一 cycle、另一 variable 或与 row 身份不一致的 NetCDF，或 dataset 的 data variable、`cycle_time`、`valid_time`、`lead_time_hours`、`unit`、`grid_id` 任一不一致
- **THEN** forcing 生产在读取值与写 ready 前 fail closed；object key MUST 逐字对应 row 的 source/cycle/variable/canonical product id

#### Scenario: catalog row 时间与 product id 必须独立自洽
- **WHEN** checksum 正确的 catalog row 与 NetCDF 被成对修改，使二者彼此相等但 `valid_time - cycle_time != lead_time_hours`，或 `canonical_product_id` 不等于 `<normalized-source>_<YYYYMMDDHH>_<variable>_f<lead:03d>`
- **THEN** catalog constructor 在构造 `CanonicalProduct`、lead 过滤与 NetCDF 读取前 fail closed；row/NetCDF 的 pairwise agreement 不构成 identity proof

#### Scenario: canonical NetCDF 累计读取有界
- **WHEN** catalog 指向大于 536870912 bytes 的 regular/sparse canonical NetCDF
- **THEN** descriptor-bound gateway 在 xarray 打开与完整 checksum 扫描前按同一 fd 的大小/累计字节上限稳定拒绝、关闭 fd，且不留下 ready 输出

#### Scenario: 输出配置漂移不得复用 ready
- **WHEN** 同一 source/cycle/model 已 ready 后，`rn_shortwave_factor` 或其它影响 package bytes/shape/path/选择策略的 forcing config 发生变化但 `producer_version` 不变
- **THEN** stable output-config identity 不匹配，producer 重算或 fail closed，不得返回旧 `already_done`

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
- **THEN** 每个快照文件的头部（前若干行）内存在一条**独立的 `#` 注释行，其注释内容恰为** `NWM@8ae9b8f2 <原路径>`（允许缩进与行尾空白，不允许路径之后还有其它内容）；写在 docstring 或字符串里不算数。`<原路径>` MUST 是一条**纯仓库相对路径**——与清单 §1 `NWM 原路径` 列同形，只含路径字符，**不带 `:<行号>` 后缀、不带括注或任何说明文字**；紧贴路径粘上的尾随内容（无空格分隔）同样属于「路径之后还有其它内容」，不构成溯源头部

#### Scenario: 未登记快照文件的反向守卫
- **WHEN** `producer/` 内出现**任意位置**带上述溯源头部形式注释行、但不在快照勘察清单路径表内的文件
- **THEN** 溯源检查测试失败，指出该文件路径

#### Scenario: 行内引用不触发反向守卫
- **WHEN** 某个文件在注释里**顺带引用** NWM 的某处，形如 `# NWM@8ae9b8f2 \`x/y.py\` 的某某字段：……`（路径后以空格分隔仍有叙述文字），或 `# NWM@8ae9b8f2 x/y.py:43（逐字移植）`（行号与括注紧贴路径）
- **THEN** 反向守卫 MUST NOT 因此判它为未登记快照文件——二者都是**行级引用**，不是溯源头部

正反两向 MUST 共用同一条「什么算溯源注释」的谓词（整行即溯源头部形式）；行预算只作用于正向，反向不设行预算。

该谓词是**登记守卫**而非抄袭检测器：它识别的是已声明的头部形式，不是任意的 NWM 提及。**已声明的残留**：真实拷贝若**只以行级引用或 docstring 形式**标注溯源（`路径:行号（说明）`、或把溯源写在模块 docstring 里），反向守卫看不见它。已知实例：`producer/src/yd_producer/state/cfg_ic.py`（issue #8 落地）派生自 pin 的 `packages/common/state_qc.py`，其 10 处 `#` 标记全为 `路径:行号（说明）` 形式（实测 `grep -cE '^[[:space:]]*#.*NWM@'`；先前写 11 是把 docstring 的两行误计在内）、模块级溯源写在 docstring 第 3 行，故不被反向守卫捕获；其登记不一致已另立 issue 归 #8 处置，不在 issue #5 范围内——该行**自相矛盾**：结构列（目标路径 `state/state_qc.py`、`落地状态` `待落地`、`剥离点` `无`）说未落地，而同一行 `备注` 自己写着「落地状态：部分（格式层）」已落在 `cfg_ic.py`、余量归 issue #9。守卫只读结构列（见 §1 序言「`落地状态` 是守卫的期望落地集来源」），散文对它不可见。此残留与「同一 commit 内既降级又删文件」并列记为已知非目标：反向守卫是**登记守卫**，识别的是已声明的头部形式，不是任意 NWM 提及的抄袭检测器。此形式约束由 issue #5 的 round-4 集成红驱动——先前的「任意位置的 `NWM@` 注释」代理量被实测证伪：master 上 `producer/src/yd_producer/config.py` 的一处行内引用被误判为未登记快照文件，从而给所有后续 PR 强加一条它们无法满足的义务（该文件在清单 §1 内既无 `NWM 原路径` 也无 `剥离点`）。

#### Scenario: 快照 DB-free 隔离
- **WHEN** 对已落地的快照模块目录运行禁区检查
- **THEN** 无任何数据库驱动/`DATABASE_URL`、scheduler 或 registry 包 import、journal/reservation import 与环境变量读取；检查 MUST 基于 import/调用结构，不得因普通标识符或错误消息含 `scheduler`/`registry` 单词而误报

#### Scenario: work-local manifest adapter 不等于 registry 服务
- **WHEN** forcing file backend 以显式构造参数读取本轮 work 内的 model manifest 索引
- **THEN** 该纯文件 adapter 被允许，且不得从环境变量、NWM scheduler 路径、数据库或跨轮动态 registry 发现 manifest
