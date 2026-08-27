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

### Requirement: 重戳到目标 cycle
重戳 MUST 只改写状态时间头为目标 cycle T 对应值，数据区 MUST 保持不变。

#### Scenario: 重戳保数据
- **WHEN** 将率定末态重戳到指定 T
- **THEN** header 时间对应 T，数据区与原文件一致

### Requirement: 负残差处理
负残差处理 MUST 沿用 NWM 已验证纯函数语义：负残差归零，并执行对应的域均修正阈值检查；超阈值 MUST 报错。

#### Scenario: 负残差归零
- **WHEN** 输入含少量负残差的状态
- **THEN** 输出负值归零，域均修正在阈值内，处理成功

#### Scenario: 域均修正超阈值
- **WHEN** 输入负残差导致域均修正超过阈值的状态
- **THEN** 处理报错，不产出修正后状态
