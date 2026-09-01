# yd 循环预报设计方案（node-22 独立计算环）

状态：方案已定稿，尚未开始实现
日期：2026-08-27

## 1. 目标与边界

在 node-22 上为 yd 流域运行独立的 IFS/GFS 双源 SHUD 循环预报，并把正式产物写入 NFS，供 node-27 的 yd-viewer 只读消费。

本期边界：

- 只复用 NWM 已下载的 raw GRIB，且对 raw 永远只读；
- yd 自己完成 raw 完整性扫描、canonical、direct-grid forcing、SHUD、状态接力和发布；
- 无数据库、无 registry 服务、无 NWM scheduler/orchestrator；
- 一个 Python CLI + cron + `flock` + Slurm；
- 计算中间物全部位于共享 `/scratch`，NFS 只保存长期数据；
- IFS/GFS 独立成环，一源故障不阻塞另一源；
- 客户侧 producer 迁移形态不在本期范围，当前只固化 `YD_ROOT` 文件边界。

正式展示契约见 [products-contract.md](products-contract.md)。节点操作纪律见 [agent-ops.md](agent-ops.md)。

## 2. 已拍板决策

| 分支 | 结论 |
|---|---|
| 来源 | IFS + GFS，各自独立状态链和产物 |
| cycle | 仅 UTC 00/12，间隔 12 小时 |
| 预报长度 | 7 天 |
| 水文输出 | `DT_QR_DOWN=60`，168 个逐小时平均流量段 |
| warm start | cycle T 只接受 `states/<source>/<T>.cfg.ic`；缺失即停该源 |
| 下一状态 | 7 天单跑中捕获 T+12；漏采后确定性补跑 12 小时 |
| 调度 | cron 非阻塞 `flock`；控制器持锁至本次全部作业收尾 |
| 并发 | 同源串行；IFS/GFS 最多各一个 Slurm 作业并行 |
| 完成真相 | `output/<T>/<source>/DONE` |
| 失败 | 本次该源停；另一源继续；下次 cron 从干净 work 重试一次 |
| 初始建链 | 显式 `init`；`run` 永不自动 bootstrap |
| forcing 映射 | source-specific direct-grid；固定生成 `yd_gfs`、`yd_ifs` 两个变体 |
| NWM 依赖 | NFS raw 只读 + `prepare` 一次性 builder；日常代码为本仓独立快照 |

不实现：旧状态降级、跨轮重戳、冷启动、degraded、状态 registry、血缘 JSON、失败计数、指数退避、下载兜底、常驻服务、自写 watchdog 或自动 `scancel`。

## 3. 物理拓扑与目录

### 3.1 可见性

- node-22 登录节点与 Slurm 计算节点共同可见 `/scratch`；
- Slurm 计算节点看不到 yd NFS；
- NFS 在 node-22 挂为 `/ghdc/data/yd`，node-27 挂为 `/home/ghdc/yd`；
- 因此只有 node-22 控制器负责 NFS ↔ scratch 搬运和正式发布。

不得把 NFS 当作 Slurm 作业目录，也不得让计算作业直接发布展示产物。

### 3.2 NFS 长期根

```text
<YD_ROOT>/                         # node-22: /ghdc/data/yd
  input/
    models/
      yd_gfs/
      yd_ifs/
    viewer/
      rivers.geojson
      boundary.geojson
  states/
    gfs/<cycle>.cfg.ic
    ifs/<cycle>.cfg.ic
  output/<cycle>/<source>/
    yd.rivqdown.dat
    DONE
  logs/<source>/<cycle>.log
```

### 3.3 scratch

现场 `local.toml` 指定 `scratch_root`，规划示例：

```text
/scratch/frd_muziyao/yd-loop/
  work/<source>/<cycle>/
    raw/                         # 本轮从 NWM NFS 只读复制的临时副本
    raw-manifest.json
    object-store/
      canonical/
      forcing/
    model/
    output/
    state-checkpoints/
    job.log
```

每轮 work 目录是一次性隔离单元。成功发布后删除；失败先回收一份日志，再删除。下次运行从零组装，不复用失败残留。

## 4. 与 NWM 的关系

### 4.1 raw 所有权

NWM downloader 在 node-27 将 raw 写入共享 NFS。node-22 的当前权威视图是：

```text
/ghdc/data/nwm/object-store/raw
```

node-27 对应视图是 `/home/ghdc/nwm/object-store/raw`。`/scratch/frd_muziyao/nhms-prod/object-store` 是 NWM 调度器私有根，不是 raw 来源。

这是 NWM 资产：

- node-22 yd 控制器只做完整性扫描和读取；
- Slurm 计算节点看不到 NFS，因此控制器只把本轮 manifest 引用的 raw 文件复制到 yd 自己的 scratch work；
- 临时副本只供本轮 canonical/forcing 使用，收尾时随 work 删除；
- 不复制到 `YD_ROOT`，也不长期缓存；
- 不修改、不移动、不重命名、不删除 NWM 原件；
- yd 清理代码不得跨入 NWM NFS raw 根；
- NWM 下载停摆时 yd 同步停更，不实现第二套下载器。

raw 根和精确 source 路径由 `local.toml` 指定，代码不写死账户路径。部署前必须以 `frd_muziyao` 身份确认可遍历和读取；权限不足时 fail closed，不修改 NWM 目录权限。

### 4.2 独立代码快照

从 NWM 固定 commit 精简复制并由本仓独立维护：

- DB-free canonical converter；
- file-backend direct-grid forcing producer；
- object-store/path 基础函数和 direct-grid 契约；
- IFS/GFS source 与 raw manifest 数据结构；
- `cfg.ic` 原生分段解析、重戳、负残差处理和结构检查；
- T+12 checkpoint tracker 与漏采补跑；
- 上述能力的最小测试。

每个快照模块记录 NWM 来源 commit。不得复制或运行时依赖：

- PostgreSQL repository、迁移和数据库模型；
- scheduler/orchestrator、候选、reservation、file journal；
- model registry 生命周期、state index/copyback；
- NWM downloader、ingest、output parser 入库链；
- NWM display API、MVT 和前端 store。

日常 `run` 使用 yd 自己由 `uv` 建立的环境，不 import NWM checkout。只有一次性 `prepare` 的 mapping-builder 通过 NWM 当前活动解释器调用，具体纪律见 agent ops。

## 5. 配置

版本化 `config.toml` 保存业务规则：

- cycle 固定 00/12；
- IFS/GFS 0–168h raw 完整性规则、变量和 bundle 文件模式；
- 两个模型变体相对路径（相对 `yd_root`，不得为绝对路径）；
- 每个 source 的 NWM canonical grid 标识（`prepare` 传给 mapping-builder 的 `grid_id`）；它随 NWM 快照固定、不随现场变化，与 `nwm_mapping_builder_module` 同属版本化快照事实，故与后者一同落 `config.toml` 而非 `local.toml`；
- `forecast_days=7`；
- `output_interval_minutes=60`；
- `checkpoint_hours=[12]`；
- Slurm 资源配置字段结构。

不入库的 `local.toml` 只保存现场值：

- `yd_root`；
- `scratch_root`；
- NWM raw 根和 NWM checkout/解释器（仅 prepare）；
- SHUD 二进制；
- Slurm partition、account、CPU、内存和 walltime；
- cron lock 与日志位置。

项目不维护动态 registry。复制来的 file backend 如要求 NWM 结构的 registry/model manifest，控制器根据 TOML 在本轮 work 内临时生成，用完随 work 删除。

## 6. CLI

一个 Python CLI 提供三个显式入口：

```text
yd-producer prepare --baseline <基线模型包路径>
  一次性生成 direct-grid 模型变体和 viewer GeoJSON

yd-producer init
  只在系统历史上第一次建立两条状态链

yd-producer run
  日常发现、追赶、提交、发布和清理
```

### 6.1 `prepare`

输入是外部受控、Git ignored 的 yd 基线模型包，其路径经 `prepare --baseline` 在调用时传入，**不进入 `config.toml` 也不进入 `local.toml`**：`prepare` 是一次性、需当前任务明确授权的人工操作（agent-ops §8.1），把只被它消费一次的路径做成常驻必需字段，等于要求 `init`/`run` 也填一个它们从不读的现场值。流程：

1. 检查本次将要写的**全部四个终名**——两个变体目录（路径取自 `config.toml` 的 `variants.gfs`/`variants.ifs`，相对 `yd_root`）与两份 viewer GeoJSON `input/viewer/rivers.geojson`、`input/viewer/boundary.geojson`——均不存在；任一存在即拒绝，不提供覆盖参数。被检查的路径与提交时实际写入的路径必须同源计算；
2. 在 scratch 中通过薄外壳调用 NWM mapping-builder；
3. 按 GFS、IFS 各自 canonical grid 生成两份 binding、重写后的 `sp.att` 和 forcing station 索引；
4. 生成完整运行变体 `yd_gfs`、`yd_ifs`；两者水文参数和率定状态来自同一基线，但网格 binding 不共用；
5. 从基线 GIS 生成 EPSG:4326 的 `rivers.geojson` 与 `boundary.geojson`；
6. 把校验通过的产物搬运到 `YD_ROOT` 之内的本次专属 staging 位置（按发布权限新建条目，不把计算节点的 uid/gid/mode 带进 NFS），再逐个 rename 到四个终名——rename 的源与终名必须同一文件系统，故不能直接把 scratch 目录 rename 过去（scratch 在计算节点本地盘、`YD_ROOT` 在 NFS）；
7. 删除 scratch 中间物与该 staging 位置（无论成败）。

运行根只保留两个运行变体，不长期保留基线包。基线模型包的现场路径和归档方式由实施方管理，不进入 Git；`--baseline` 是必需参数，代码不内置任何默认路径。本项目不额外维护人工填写的模型包总 checksum。

本期 M1–M5 固定同一套基线模型、SHUD 二进制和河网。模型或 SHUD 升级是新的契约变更：禁止原地覆盖现有变体和状态；必须在新的干净 staging 根重新 `prepare`、`init`、真跑和 viewer 验证，再单独设计切换。当前 CLI 不提供在线升级状态机或 `--force`。

### 6.2 `init`

`init` 是唯一 bootstrap 入口：

1. 若 `states` 下已有任一状态，或 `output` 下已有任一 `DONE`，直接拒绝；
2. 以执行时刻往前 7 天为扫描窗；
3. 对每个 source 找到窗内最早的完整 00Z/12Z raw cycle；
4. 从两个变体内的同源率定末态复制首态，重戳到各自首轮 T；
5. 写为 `states/<source>/<T>.cfg.ic`；
6. 不运行 SHUD、不写 `DONE`。

任一 source 在扫描窗内没有完整 cycle 时，`init` 整体拒绝且不写任何状态。拒绝理由必须区分「raw 缺文件」与「raw 文件存在但不可读」——生产 raw 根是 NFS 上由 NWM 以另一 uid 写入的目录树，权限失败会让 `judge` 判该 cycle 不完整，此时提示「等待 raw 补齐后重跑」是把权限故障伪装成缺数据，运维会对着已在盘上的数据永远重跑；存在不可读文件时必须点明这一点。仅在确为缺文件时才提示等待 raw 补齐后重跑 `init`。不提供单源建链或事后补链入口——`init` 只在系统历史上第一次执行。

率定末态在变体内的定位判据：变体目录（`config.toml` 的 `variants.<source>`，相对 `YD_ROOT`）**顶层**恰好一个 `.cfg.ic` 普通文件即该源率定末态；「相对 `YD_ROOT`」是一道必须由 `init` 自己强制的闸门而非描述——取值为绝对路径或含 `..` 分量时 `init` 整体拒绝且不写任何状态，不得拼接后读取（否则链起点取自 `YD_ROOT` 之外）；该闸门与 `prepare` 侧的同名判据必须是同一份实现；命中数不为 1、目录不存在或不可枚举时 `init` 整体拒绝且不写任何状态。不按项目名猜文件名（项目名与变体目录名的等同关系无权威来源，猜错的失败模式与「prepare 未跑」不可区分），不递归搜索（运行期 `cfg.ic.update` 等衍生物会污染候选集）。该判据同时约束 `prepare` 的提交形态：变体顶层必须且只能带一份率定末态。

`init` 的判定与落盘严格两阶段：所有拒绝守卫、率定末态解析与两源首轮 T 的确定全部完成之前不发生任何写入；写入阶段以 `O_EXCL` 落终名，拒绝覆盖已存在文件。写入阶段中途失败时 `init` 不回滚——已落盘的首态保留在盘上（`init` 不持有 `states/` 的删除权），但必须以非零退出码报告**全部**已落盘的 source。收尾话术随终态分支，判据是「`written` 非空**或**盘上真的留下了半写目标」，不是单看 `written`。写入失败分两类：其一，目标被排他创建后 `os.write` 中途因 I/O 或配额失败，盘上留下 header 合法、body 截断的文件——该目标既不算已落盘、也不会被删除，`init` 必须单独点名它可能已被部分写入、重跑前须一并人工确认；其二，失败发生在目标被创建之前（首个目录创建即被权限拒绝、`O_EXCL` 的 `open` 本身被拒、父目录分量走查失败等），盘上零残留。**这两类不可由 `SafeFilesystemError.kind` 区分**——`kind == "io"` 同时覆盖 open 期失败与写中途失败，故 `init` 必须在捕获异常后用 no-follow 的 `lstat` 直接探测目标：条目存在则按第一类点名可能半写；`FileNotFoundError` 则按零残留处理；探测本身失败则 fail closed 到「可能半写」但话术须留有余地。收尾话术因此是**三路**而非两路：有 source 落盘或探测到半写目标时提示「根已非全新，重跑前需人工清理 `states/`」；零落盘、零残留且**写入路径上不存在持久外来条目**时报「零写入，根仍是全新根」并把根因放在首位，不得宣称需要清理；零落盘、零残留但**写入路径上被一个持久外来条目挡住**时，必须点名该条目的**实际路径**并要求重跑前先确认并移除它——此时既不能说「根仍是全新根」（重跑必然以同样理由再次失败），也不能说「可能已被部分写入」（该条目不是本次写入产生）。**第二路与第三路的判据是「阻塞物是否为持久外来条目」，不是「哪条腿抛的异常」、也不是「条目是否恰好落在终名 target 上」**：外来条目既可能占住终名 target（`O_EXCL` 撞 `EEXIST`），也可能占住其**父目录分量**（`states/<source>` 是 symlink、FIFO 或普通文件，使目录创建拿到 `NotADirectoryError`/`ELOOP`）——两种载体的运维后果完全相同（重跑逐字节复现同一失败，必须先移除该条目），故 MUST 走同一路话术，且话术点名的 MUST 是被占住的那个路径本身而非终名 target。与之相对，权限类失败（如 `states/` 置 `0o500`/`0o600` 使父目录 open 拿 `EACCES`）盘上没有外来条目，`chmod` 后直接重跑即可成功，仍走第二路。

两源首轮可因 raw 到达情况不同而不同；从首轮开始各自演进。率定末态约对应 2025-01，直接重戳到首轮意味着初期存在状态收敛偏差；这是已接受的首启代价，不把它伪装成 degraded，也不在 viewer 展示内部状态。

`run` 发现状态目录缺失时只报错，绝不自动执行 init。已有持续产物接管时不调用 init：下一轮由现有状态文件名确定。

## 7. raw 完整性与 forcing

### 7.1 自行扫描

本仓按 NWM adapter 的当前事实固化两份 source 规则：

- 仅接受 00Z、12Z；
- 预报 lead 覆盖 0–168h；
- IFS/GFS 各自的变量、bundle 名和 f000 特例；
- 所有预期文件存在且可读才视为完整。

不靠目录稳定时间、末 lead 文件或动态推断判断完整。

### 7.2 临时 raw manifest

完整后，控制器把 manifest 声明的本轮 raw 文件复制到 `work/raw/`，并在 work 内生成 NWM-compatible `raw-manifest.json`。manifest 包含 converter 所需的 source、cycle、forecast hours、变量与 GRIB filter 信息，entry 路径只引用 `work/raw/` 临时副本。控制器复制前后均不修改 NWM NFS 原件。

### 7.3 DB-free 日常链

单个 source/cycle 的 Slurm 作业在 scratch 内顺序执行：

```text
临时 raw manifest
  → canonical NetCDF + catalog
  → source-specific direct-grid forcing 包
  → 组装 SHUD 输入
  → 7 天 SHUD
```

canonical、forcing 和临时 manifest 都是本轮工件，不写 NFS，也不跨轮复用。direct-grid forcing 将 canonical 格点直接作为 SHUD forcing 站点，binding 权重为 1；不走旧的 105 站 IDW。

IFS/GFS forcing 原生 3 小时并不限制水文输出为 3 小时：SHUD 求解按自身步长推进，forcing 在相邻时刻间保持当前值，`DT_QR_DOWN=60` 独立输出逐小时平均流量。

## 8. 严格 warm start

状态命名唯一：

```text
states/<source>/<cycle>.cfg.ic
```

cycle T 的规则：

- 只读取精确的 `<T>.cfg.ic`；
- 缺失、不可读或时间不对应 T 时停止该 source；
- 不取更旧状态，不跨轮重戳，不冷启动；
- IFS/GFS 永不互借状态；
- 成功后只保留下一待跑状态及其前一份状态。

`cfg.ic` 是原生分段格式，不得按“单一 6 列表”处理：至少包含 mesh 状态段与 river `Stage` 段，可能还有 lake 段。重戳和检查复用精简后的 NWM `state_qc` 解析语义。

负残差处理沿用 NWM 已验证的纯函数：负残差归零，并保留对应的域均修正阈值检查；不引入状态 registry 或血缘 JSON。

## 9. 单轮 SHUD 与 T+12 状态

### 9.1 固定参数

每个 source/cycle 只先跑一次 7 天 SHUD：

```text
START = 0
END = 7
DT_QR_DOWN = 60
Update_IC_STEP = 720
BINARY_OUTPUT = 1
ASCII_OUTPUT = 0
```

00Z、12Z 都是 `START=0`，因为 direct-grid forcing 的 `Time_Day=0` 锚在 cycle 时刻。

### 9.2 checkpoint 捕获

SHUD 会反复覆盖同一个 `<project>.cfg.ic.update`：当模型时间为 720、1440、… 分钟时依次更新。因此 T+12 文件不能在 7 天运行结束后再取。

作业内保留 NWM 的最小 job-local tracker：

1. 启动 SHUD；
2. 轮询 `cfg.ic.update` 的 header 时间；
3. 命中 relative 720 分钟时，复制到独立 checkpoint 文件（`cfg.ic.update` 的 header 是模型相对分钟，tracker 只认这一种形式；epoch 形式的 header 属重戳后的正式状态，见下段，tracker 对其判未命中而非兼容）；
4. 确认复制完成并可按原生分段格式读取；
5. SHUD 继续跑到 7 天。

`cfg.ic.update` 的 header 时间是模型相对分钟；正式 `states/<source>/<T+12>.cfg.ic` 的时间头必须对应绝对 T+12，与 `init` 写出的首态同一语义（§8 的"时间不对应 T 即停"以绝对时间判定）。因此控制器在发布前把捕获的 checkpoint 重戳到绝对 T+12。这是本轮产物的定戳，不属于 §2 禁止的"跨轮重戳"（后者指拿旧 cycle 状态改戳冒充新 cycle）。

### 9.3 漏采补跑

如果 7 天运行成功但 tracker 未得到 T+12：

1. 使用同一个 cycle T 初态与同一份 forcing；
2. 把 `END` 缩短为 0.5 天，`Update_IC_STEP=720`；
3. 在同一个 Slurm 作业内确定性补跑一次 12 小时，不产生第二次提交；
4. 只从本次补跑的全新专用输出目录读取精确 `<project>.cfg.ic.update`，经 relative-720 header、原生分段结构与 checksum 校验后才把末态采纳为 T+12 checkpoint；
5. 参数文件在补跑调用结束后恢复为主跑的原始 bytes，同一初态与 forcing 在补跑前后 checksum 不变；
6. 补跑仍失败则整轮失败，不写状态和 `DONE`。

一次 scratch work 只服务一次 Slurm attempt：tracker 的已捕获 authority 是该 attempt 内的内存记录与其 checksum，不能靠 `state_checkpoints/` 下某个规范文件名存在来恢复。重排队、进程重启或下次 cron 重试必须删除并重新组装整棵 work；若新实例在旧 work 中看到同名 checkpoint 或 recovery 目录，只能把它当作未验证残留，保留证据并 fail closed，既不能采纳，也不能覆盖/删除。配置的产品目标必须恰为 `checkpoint_hours=[12]`；`[720]` 这类小时/分钟混淆在启动补跑前响亮失败。

这保留 NWM 的可靠性，但不复制其外层 watcher 服务、恢复状态机、checkpoint manifest 或 registry。publisher 继续由调用方显式交入已经验证的 checkpoint 路径，不扫描 recovery 目录。

## 10. 控制器、Slurm 与积压

cron 每小时调用 `yd-producer run` 的非阻塞 `flock` 包装：

- 前一实例仍持锁时，本 tick 直接跳过，不排队；
- 锁覆盖发现、提交、等待、发布和清理的完整生命周期；
- 手工补跑也必须走同一个锁入口，不能绕过互斥。

一次 run 先为每个 source 确定严格前沿：

1. 若该源没有任何 `DONE`，全新链只允许存在 init 写入的最早状态，该文件名就是待跑 T；
2. 否则取该源最新 `DONE` cycle D，待跑 T 固定为 D+12h；
3. 必须存在 `states/<source>/<T>.cfg.ic`，否则停止该源；
4. 若无 `DONE(T)` 却存在比 T 更晚的状态或 T 目录半成品，它们是上次发布中断的未提交残留：保留 T 状态，删除残留后重跑 T；
5. 扫描 T 的 raw；未完整则该源暂不提交；
6. 为每源最多组装一个 work 并提交一个 Slurm 作业；
7. IFS/GFS 可并行，控制器等待两者；
8. 成功源发布后以前沿规则立即推进到下一个 cycle，直到追到最新完整 raw；
9. 某源作业失败后，本次停止该源，另一源继续追赶；
10. 下次 cron 对失败 cycle 从干净 work 重试一次。

raw 一次补齐多轮时按时序全补；中间永久缺轮时停在缺口，运维人员补齐原始资料后自动继续。不自动跳过 cycle。

Slurm 的 partition/account/资源/walltime 来自 `local.toml`。不为尚未出现的卡死增加 CLI watchdog；人工取消时只能按本次 receipt 记录的 yd job ID 操作，不得模糊匹配或取消 NWM 作业。

## 11. 发布、崩溃恢复与幂等

### 11.1 成功条件

作业退出成功后，控制器确认本轮至少具备：

- v2 `yd.rivqdown.dat`，168 行、3988 个河段；
- T+12 原生 `cfg.ic`；
- 本轮合并 stdout/stderr 可供失败时回收。

这是 producer 写 `DONE` 前的自身契约检查；viewer 信任 `DONE`，不实现第二套修复协议。

### 11.2 NFS 提交顺序

运行 T 时，旧的 `states/<source>/<T>.cfg.ic` 保留到最后：

1. 从 scratch 复制 DAT 到 NFS source 目录的临时文件；
2. 在 NFS 内原子 rename 为 `yd.rivqdown.dat`；
3. 复制 T+12 状态到 NFS 临时文件；
4. 原子 rename 为 `states/<source>/<T+12>.cfg.ic`；
5. 最后原子创建 `output/<T>/<source>/DONE`；
6. `DONE` 成功后才删除比 T 更旧的状态；最终保留 T 与 T+12；
7. 删除 scratch work。

多个文件无法同时原子提交，因此用“旧状态保留 + DONE 最后写”恢复：若步骤 1–4 间宕机且无 `DONE`，下次删除该 source/cycle 的半成品，仍用 T 状态整轮重跑。不得先写 `DONE` 再提交状态。

### 11.3 失败

- 不写 `DONE`；
- 不推进状态链；
- 把完整 stdout/stderr、命令、开始/结束时间和退出码合成一份 `logs/<source>/<T>.log`；
- 删除整个 scratch work；
- 下次 cron 干净重跑。

不维护失败次数、退避或 `status.json`。

## 12. 保留与清理

| 对象 | 规则 |
|---|---|
| `output/<T>/<source>/{yd.rivqdown.dat,DONE}` | 保留最新成功 cycle 往前 14 天 |
| `states/<source>/*.cfg.ic` | 每源保留下一待跑状态及其前一份 |
| `logs/<source>/<T>.log` | 仅失败轮；与 output 的 14 天窗口一起清理 |
| scratch `work/<source>/<T>` | 每轮成功或失败收尾后删除 |
| NWM NFS raw 原件 | yd 永不清理 |
| scratch raw 副本/canonical/forcing/raw-manifest | 本轮临时工件，随 work 删除 |

清理只允许作用于经 `realpath` 确认位于 yd 自己根目录下的对象；不得跟随路径进入 NWM raw 根。

## 13. 验证计划

### 13.1 本地

| 项 | 验证 |
|---|---|
| raw 扫描 | IFS/GFS 完整、缺文件、GFS f000 特例和临时 manifest |
| DB-free 链 | 合成 raw/canonical fixture 跑到 direct-grid forcing 包 |
| prepare | 两个 source-specific 变体、拒绝覆盖、两个 GeoJSON |
| state | 原生 mesh/river/lake 分段解析、T 重戳、负残差处理 |
| tracker | T+12 正常捕获、快速覆盖漏采、12h 补跑成功/失败 |
| 控制器 | 同源顺序、双源并行、raw 缺口、单源失败、flock 幂等 |
| 发布 | 无 DONE 崩溃恢复、DONE 最后写、状态只保留两份 |

### 13.2 node-22 真运行

至少选择一个 00Z 和一个 12Z，IFS/GFS 均覆盖：

1. raw 扫描只读且未改变 NWM 文件；
2. direct-grid forcing 的首行 `Time_Day=0` 对应 cycle；
3. `START=0`，12Z 没有 12 小时偏移；
4. DAT 为 v2、168 行、分钟列 `0..10020`、3988 河段；
5. T+12 checkpoint 被捕获或由补跑确定生成；
6. 下一轮精确消费该状态；
7. 单源失败时另一源继续；
8. NFS 只在控制器收尾阶段出现正式文件，`DONE` 最后写；
9. scratch work 最终清理，失败只留一份日志。

### 13.3 node-27 闭环

node-27 viewer 必须直接读取上述真实产物完成地图与曲线 receipt；不以 NWM 自身数据库或线上水文产物作为 yd 正确性的 oracle。

## 14. 尚待现场确定

- node-22 本仓 checkout 与 `local.toml` 的实际位置；
- Slurm partition、account、CPU、内存、walltime；
- SHUD 可执行文件路径；
- 首次 prepare 所用外部基线模型包路径；
- cron 的最终分钟点。

这些值只能在部署时实测填写，不能写死进业务代码。
