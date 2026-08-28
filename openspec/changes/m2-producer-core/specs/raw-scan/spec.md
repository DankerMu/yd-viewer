# raw-scan

来源：compute-loop-design §7.1–7.2、§4.1、agent-ops §4.3。

## ADDED Requirements

### Requirement: 基于显式规则的完整性判定
扫描器 MUST 按 `config.toml` 固化的 source 规则判定一个 source/cycle 的 raw 是否完整：仅接受 00Z/12Z、预报 lead 覆盖 0–168h、IFS/GFS 各自变量与 bundle 文件模式、GFS f000 特例；所有预期文件存在且可读才判完整。MUST NOT 以目录稳定时间、末 lead 文件存在或其它动态推断替代逐文件检查。

#### Scenario: 全部预期文件存在
- **WHEN** 目录 fixture 含某 source/cycle 的全部预期 raw 文件
- **THEN** 判定完整并返回预期文件清单

#### Scenario: 缺失单个文件
- **WHEN** fixture 缺少一个中间 lead 的文件
- **THEN** 判定不完整并列出缺失文件

#### Scenario: 仅有末 lead 文件不算完整
- **WHEN** fixture 只存在最末 lead 的文件
- **THEN** 判定不完整（不以末 lead 推断整轮就绪）

#### Scenario: GFS f000 特例
- **WHEN** 扫描的 source 按 config 声明 f000 特例（`raw.<source>.f000_special = true`）且 lead 0 在该源预期 lead 全集内
- **THEN** lead 0 的文件仍属预期文件集，但其预期变量集排除该时刻无定义的累积/平均量；不因这些变量缺席而判不完整，也不因特例而放行缺失的 lead 0 文件

#### Scenario: 非 00/12 cycle 被拒绝
- **WHEN** 请求扫描 06Z cycle
- **THEN** 拒绝并报错，不进行文件检查

### Requirement: raw 只读与临时副本
完整判定后，控制器 MUST 把清单内文件复制到本轮 `work/raw/`；复制前后 NWM 原件的内容与元数据 MUST 保持不变；副本 MUST NOT 写入 `YD_ROOT` 或跨轮保留。

#### Scenario: 复制不改动源
- **WHEN** 将 fixture raw 根中的文件复制到 work
- **THEN** 源文件内容与 mtime 不变，work/raw/ 出现同内容副本

#### Scenario: 副本不落 NFS
- **WHEN** 复制完成
- **THEN** `YD_ROOT` 模拟根内不出现任何 raw 副本

#### Scenario: 判定不完整时零写入
- **WHEN** 以 `complete=False` 的判定结果请求复制
- **THEN** 拒绝并报错，work 根内不出现任何新增路径（含目录与半个 manifest）

#### Scenario: 复制中途源被改动
- **WHEN** 某个源文件在复制窗口内被替换，复制前后的 `lstat` 元组不一致
- **THEN** 报错停止，work 内不留半套副本

#### Scenario: 不覆盖已存在的目标
- **WHEN** 目标副本路径上已存在同名文件
- **THEN** 报错停止，不覆盖该文件

### Requirement: 本轮临时 raw manifest
扫描器 MUST 在 work 内生成 NWM-compatible `raw-manifest.json`，包含 converter 所需的 source、cycle、forecast hours、变量与 GRIB filter 信息；entry 路径 MUST 只引用 `work/raw/` 临时副本。entry MUST 逐变量扇出，同一 bundle 的各变量 entry 共享同一路径键；manifest 声明的 `(lead, variable)` 集合 MUST 与判定结果的预期变量集**相等**。

#### Scenario: manifest 结构与路径
- **WHEN** 对完整 cycle 生成 manifest
- **THEN** JSON 含 source/cycle/forecast hours/变量/filter 字段，且所有 entry 路径位于 `work/raw/` 之下

#### Scenario: 逐变量扇出与预期集相等
- **WHEN** 对完整 cycle 生成 manifest
- **THEN** entry 的 `(lead, variable)` 集合与判定结果的预期变量集相等，且同一 lead 的各变量 entry 共享同一路径键

### Requirement: manifest 语义键承接与 fail-closed
manifest 的 entry 级语义键（GRIB filter、累积语义）MUST 逐条承接自 NWM 源 manifest，MUST NOT 在本仓发明或以默认值补齐。降水累积语义缺失或越域时 MUST 报错停止；manifest 级 forecast hours 键 MUST 显式写出，MUST NOT 依赖消费端由实际 entry 反推。

#### Scenario: 源 manifest 不可用
- **WHEN** 源 manifest 缺失、不可解析，或其 entry 无法覆盖本轮预期的 `(lead, variable)` 全集
- **THEN** 报错停止，不以空值或推导补齐

#### Scenario: 降水累积语义缺失
- **WHEN** 某条降水变量 entry 的累积类型缺失
- **THEN** 报错停止，MUST NOT 静默默认为「自起报累积」

#### Scenario: 累积类型取值越域
- **WHEN** 累积类型取声明取值域之外的值
- **THEN** 报错停止

#### Scenario: 区间累积缺少区间范围
- **WHEN** 累积类型为区间桶但区间范围键缺失
- **THEN** 报错停止

#### Scenario: manifest 级 forecast hours 缺失
- **WHEN** 源 manifest 缺少 manifest 级 forecast hours 键
- **THEN** 报错停止，不落到消费端「由实际 entry 反推应有小时表」的自证式回退
