# state-tools

来源：compute-loop-design §8、products-contract（状态不属展示契约）、agent-ops §11.2。

## ADDED Requirements

### Requirement: cfg.ic 原生分段解析
解析器 MUST 按原生分段格式处理 `cfg.ic`：至少包含 mesh 状态段与 river `Stage` 段，可能含 lake 段；MUST NOT 按"单一 6 列表"读取；解析后 MUST 能无损回写。

#### Scenario: mesh+river 双段 roundtrip
- **WHEN** 解析含 mesh 与 river 段的合成 `cfg.ic` 并回写
- **THEN** 回写文件与原文件字节等价

#### Scenario: 含 lake 段 roundtrip
- **WHEN** 解析含 mesh/river/lake 三段的合成 `cfg.ic` 并回写
- **THEN** 回写文件与原文件字节等价

### Requirement: 结构检查
结构检查 MUST 拒绝缺段、段内行数与 header 不符或数值区损坏的状态文件，并报出具体缺陷。

#### Scenario: 缺 river 段被拒
- **WHEN** 检查缺少 river `Stage` 段的文件
- **THEN** 检查失败并指明缺失段

#### Scenario: 非有限状态值被拒
- **WHEN** 检查任一状态列含 `nan` / `inf` / `-inf` 的文件
- **THEN** 检查失败并指明该行该列非有限；非有限判定 MUST 先于负值判定

#### Scenario: river 行数与权威计数不符被拒
- **WHEN** 以权威 river 元素数检查 river 段行数不符的文件
- **THEN** 检查失败并报出实际与期望行数

### Requirement: 重戳到目标 cycle
重戳 MUST 只改写状态时间头为目标 cycle 对应的绝对值，数据区 MUST 保持不变。同一重戳函数服务 init 首态与发布前 T+12 checkpoint 定戳两条路径（compute-loop §9.2）。

#### Scenario: 重戳保数据
- **WHEN** 将率定末态重戳到指定 T
- **THEN** header 时间对应 T，数据区与原文件一致

### Requirement: state 输入边界保持单一异常契约
state 文档 API MUST 保留合法输入的既有行为，并对本 Requirement 点名的畸形实参统一抛 `ValueError`；失败时 MUST NOT 返回部分文档或修改源文档。时间归一 MUST 与宿主时区无关。

#### Scenario: offset 未知的 aware datetime 被确定性拒绝
- **WHEN** `_ensure_utc` 或重戳入口收到 `tzinfo` 非空、但 `utcoffset()` 返回 `None` 的 datetime，并分别在 UTC、EST、Asia/Shanghai 宿主时区执行
- **THEN** 三种环境均抛 `ValueError`，MUST NOT 把该 wall clock 当宿主本地时间重释

#### Scenario: 既有 datetime 语义保持
- **WHEN** 重戳入口收到 naive datetime 或带合法非 UTC offset 的 aware datetime
- **THEN** naive 值仍按 UTC 解释，aware 值仍转换为同一 UTC 时刻

#### Scenario: 畸形或溢出的重戳 target 使用 ValueError
- **WHEN** target 为 `None`、`date`、float、其 timezone 返回非 `timedelta` offset，或为 UTC 转换时溢出的 `datetime.max @ -14:00` / `datetime.min @ +14:00`
- **THEN** 重戳抛 `ValueError`，不返回文档，源文档字节不变

#### Scenario: 行替换实参在文档边界预检
- **WHEN** `with_replaced_lines` 收到非 Mapping 实参、值为 `None`/bytes/int，或替换文本含孤立 Unicode surrogate
- **THEN** 该入口立即抛 `ValueError`，不返回文档，源文档字节不变

#### Scenario: bytes-like cfg.ic 内容有明确解析语义
- **WHEN** `cfg_ic.parse` 分别收到内容相同的 bytes、bytearray 与 memoryview
- **THEN** 三者产出字节等价的文档；可变 bytes-like 输入在解析前快照，且超出 `max_bytes` 时在制造不可变副本前抛 `ValueError`

### Requirement: 负残差处理
负残差处理 MUST 以 NWM 已验证纯函数语义为兼容基线：负残差归零，并执行对应的域均修正阈值检查；超阈值 MUST 报错。非有限值 MUST 在任何归零投影之前被拒。yd MAY 在本仓修复 `state/cfg_ic.py` 的快照缺陷，不要求它与 pin 逐字或 AST 等价；每一处偏离 MUST 同时在模块头说明，并在 `nwm-snapshot-inventory.md` 对应行的「剥离点」列登记一句“问题 + 修法”。

#### Scenario: 已登记的 cfg.ic 缺陷修复
- **WHEN** `state/cfg_ic.py` 相对 `NWM@8ae9b8f2` 修复一处缺陷
- **THEN** 差异审计不要求逐字或 AST 等价，但清单对应行的「剥离点」必须能逐处解释问题与修法，且模块头同步说明

#### Scenario: 负残差归零
- **WHEN** 输入含少量负残差的状态
- **THEN** 输出负值归零，域均修正在阈值内，处理成功

#### Scenario: 非有限值在归零前被拒
- **WHEN** 输入任一状态列含 `nan` / `inf` / `-inf` 的状态
- **THEN** 处理报错，不产出修正后状态，且不得先行归零

#### Scenario: 域均修正超阈值
- **WHEN** 输入负残差导致域均修正超过阈值的状态
- **THEN** 处理报错，不产出修正后状态
