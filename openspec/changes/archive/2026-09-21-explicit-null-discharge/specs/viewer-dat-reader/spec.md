## MODIFIED Requirements

### Requirement: 单位换算与取值
数据层结果 MUST 提供按 lead 取整行（去掉第 0 列，按权威集合升序排列）与按 `reach_id` 取整列（168 值）；有限值 MUST 已从 m³/day 换算为 m³/s（除以 86400）；河段值 NaN、+Inf、-Inf MUST 在访问器中显式逐点转换为 None，不拒绝 source、不改变位置或长度。分钟列仍遵循严格数据层校验。

#### Scenario: 取 lead 0 行
- **WHEN** lead 0 行 reach_id=1 原值为 86400
- **THEN** 取行结果中 reach_id=1 的值为 1.0

#### Scenario: 取 reach 列
- **WHEN** 请求 reach_id=3 的列
- **THEN** 返回 168 个 float 或 None；有限值第 k 个等于数据区第 k 行该列原值 / 86400，非有限河段值为 None

#### Scenario: 列编号非顺序
- **WHEN** 列编号为 `[3,1,2]` 且权威集合为 {1,2,3}
- **THEN** 取行结果按 reach_id 1,2,3 顺序返回对应列的值（不按文件列位置）


#### Scenario: 非有限流量逐点缺测
- **WHEN** 分钟合法，河段值混合有限值、NaN、+Inf、-Inf
- **THEN** row/column 保留顺序和长度，仅非有限流量转 None，正常值单位换算不变
