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

#### Scenario: path identity 与 lead window 在 request boundary fail closed
- **WHEN** 公开 seam 收到 `model_id="."`、public/repository `basin_version_id="."`，或 `max_lead_hours` 为 string/bool/float/negative；另有一个已 ready 的合法 sibling tuple
- **THEN** public-only malformed value 在零 repository call 前失败，repository-return path identity 在 `get_forcing_version`/failure-status write/cleanup 前失败，且 sibling 的 record/package/domain/sidecar/handoff/cycle-ready bytes 全部不变
- **THEN** `max_lead_hours=0`、普通 nonnegative int 与超出可用产品 lead 的任意合法大整数继续按现有可用 lead 截取，不得引入额外上限

#### Scenario: repository 返回 contract 必须重建完整 station/cell 结构语义
- **WHEN** 注入式 repository 直接构造 frozen `DirectGridForcingContract`，绕过 file parser，并分别给出 duplicate/blank station or cell、bool/zero/gapped index、unsafe/casefold-colliding filename、station/grid mismatch、duplicate cell、non-finite/out-of-range coordinate 或 non-Mapping properties
- **THEN** parser 与 producer 共用同一个 semantic validator；producer 在 existing lookup、mapping/package write 与 readiness mutation 前稳定拒绝，且不得把 unsafe filename静默替换成 synthetic filename后继续
- **THEN** source-less parser 仍可保留 pin-compatible multi-source shape，但任何进入指定 source 的生产调用都必须要求 exact current-source singleton

#### Scenario: request preservation 不得掩盖 authoritative drift
- **WHEN** 合法 tuple 已 ready 后，当前 catalog、binding/`.sp.att` 或 canonical NetCDF/grid authority 发生 identity/checksum/content drift
- **THEN** producer 在 existing lookup 后证明旧 ready 已 stale 并撤销其 final evidence；不得为了满足 malformed-request preservation 而返回旧 `already_done`

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
快照 file backend 要求 NWM 结构的 registry/model manifest 时，组装层 MUST 依据调用方显式提供的本轮 WorkIdentity、direct-grid contract 与已验证 binding/`.sp.att` bytes，在本轮 work 内的隔离 shadow object-store staging 中以最终相对 key 构造并由真实 `FileForcingRepository` 读回，再把 staged model 子树以同一 work/filesystem 的一次 no-follow rename 提交到 `<work>/object-store/models/<model_id>/`；MUST NOT 从变体 basename、`yd.binding` 文本、环境变量、数据库或外部 registry 服务猜测身份/contract。生成的 registry/model manifest MUST 可由 `FileForcingRepository` 原样消费，且 contract 的 binding/`.sp.att` checksum、source/project/model/basin identity 与本轮 work 必须一致。项目 MUST NOT 维护跨轮动态 registry；整棵 work 的成功/失败清理仍由既有 publish/cleanup owner 负责，组装层不得另建跨 work 删除协议。

#### Scenario: 临时 registry 生命周期
- **WHEN** 对显式 WorkIdentity、source-specific contract 与 checksum-correct assets 生成临时 file backend
- **THEN** registry、manifest、binding、`.sp.att` 与 station index 只落在本轮 `<work>/object-store/models/<model_id>/`，`FileForcingRepository` 能读回同一 model/source/contract；既有 work 删除后无任何 registry 残留或 work 外副本

#### Scenario: 临时 registry 原子提交与身份拒绝
- **WHEN** final model root/staging 已存在，或 binding/`.sp.att` checksum、contract URI、source/cycle/model/basin/project identity 任一不一致
- **THEN** 生成器在 final model root 提交前稳定失败，只清理本次 staging，不覆盖既有条目、不写 work 外路径，也不修改输入 assets

#### Scenario: finalize handoff 的直接 JSON 契约仍可证明
- **WHEN** 由该临时 file backend 完成一轮 direct-grid forcing finalize
- **THEN** `forcing_domain_handoff.json` 与 `forcing_domain_package.json` 均落在本轮 object-store，handoff 的 `payloads.station_timeseries.time_lattice` 保留逐时段 `native_resolution`；本项目不恢复 NWM 2777 行 parser 来循环证明这些 JSON

### Requirement: SHUD 输入组装与固定参数
组装器 MUST 在 work 内由模型变体、checksum 绑定的 forcing package 与本轮 warm-start 状态组装完整 SHUD 运行目录。运行目录终名固定为 `<work>/model` 并经同父目录 staging 一次 rename 提交；运行目录的初始条件 MUST 为 `states/<source>/<T>.cfg.ic` 的原始 bytes，状态必须是 no-follow 普通文件、可按原生分段格式解析且绝对时间头对应 T；该状态 MUST 覆盖模型变体自带率定末态，MUST NOT 被重戳、修正或回退到变体初态。variant 发现 MUST 严格局限于显式根并以 descriptor-bound streaming 复制普通文件，不得跟随 symlink/读取特殊文件，也不得在无模型包合同依据时另设 entry/depth 业务上限。forcing package 只能从 checksum 验证过的 package manifest 的 SHUD role 成员组装：index 在运行目录改名为 `<project_name>.tsd.forc`，station CSV basename 原样保留；debug/payload/handoff/domain-package 产物不得进入模型输入目录。

组装 MUST 在 `<project_name>.para` 上固定覆盖 `START=0`、`END=7`、`DT_QR_DOWN=60`、`Update_IC_STEP=720`、`BINARY_OUTPUT=1`、`ASCII_OUTPUT=0`。参数 writer 只认 `{{KEY}}`、`${KEY}`、`KEY = value` 三种已登记形态：每键零命中则追加，恰一命中则替换，多命中则拒绝；同一行可各含一个不同 key 的 placeholder，后处理的 key 不得恢复前一 key 的旧 placeholder；六项之外的 bytes 保持不变。00Z 与 12Z MUST 使用同一套参数 bytes。

#### Scenario: 参数三形态覆盖与 cycle 无关
- **WHEN** 对含 placeholder、shell-style placeholder、assignment 与缺失键的合成 `.para` 分别以 00Z/12Z 执行组装
- **THEN** 运行目录六项参数各恰一处且为固定值，两种 cycle 的参数 bytes 相同，未命中的键按原行尾风格追加，其余输入 bytes 不变

#### Scenario: warm-start 状态覆盖变体初态
- **WHEN** 以内容可区分、header 对应 T 的状态 fixture 与自带不同率定 `cfg.ic` 的合成变体执行组装
- **THEN** `<work>/model/<project_name>.cfg.ic` bytes 逐字等于 T 状态、不等于变体率定末态；状态/变体/forcing 三个源保持逐字不变

#### Scenario: 状态身份与类型 fail closed
- **WHEN** state path 不等于 `<states_root>/<source>/<T>.cfg.ic`，或其 leaf/ancestor 是 symlink、FIFO/目录、不可解析 cfg.ic、相对 720 minute header、其它 cycle 的绝对 header
- **THEN** 组装在 final `model` 提交前稳定失败，不重戳、不取旧状态、不产生运行目录终名

#### Scenario: forcing manifest 与角色 fail closed
- **WHEN** `ForcingProductionResult` 与 package manifest 的 checksum/source/cycle/model/package URI 不一致，或 SHUD index 为零/多份、CSV role/URI/checksum/filename set 与 index 不一致
- **THEN** 组装在读入不受信 bytes 或提交 final `model` 前拒绝，不回退 debug index、不扫描未声明文件、不留下运行目录终名

#### Scenario: 组装失败保持三源且无终名
- **WHEN** 复制变体、状态、forcing 或改写参数的任一步骤失败，或 staging 清理本身失败
- **THEN** final `<work>/model` 不存在，variant/state/forcing package 的全树 bytes/类型快照不变；只允许本次 staging 作为可由整棵 work 清理 owner 回收的残留并把清理失败附到原异常

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
