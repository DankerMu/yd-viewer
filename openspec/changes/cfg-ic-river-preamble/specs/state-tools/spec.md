## MODIFIED Requirements

### Requirement: cfg.ic 原生分段解析
解析器 MUST 按原生分段格式处理 `cfg.ic`：至少包含 mesh 状态段与 river `Stage` 段，可能含 lake 段；MUST NOT 按"单一 6 列表"读取；解析后 MUST 能无损回写。river 段与 lake 段各以一行 `<count> <state-cols>` 前导开头（原生 SHUD 段元数据）：解析器 MUST 把紧邻对应段列头之前、由两个整数（count ≥ 0，cols > 0）组成的该行归为段前导而非状态数据行，MUST NOT 把它计入任何段的数据行，且 MUST 以声明的 count 校验该段实际行数；mesh 段已满后出现的其它数值行仍 MUST 被拒绝。

#### Scenario: mesh+river 双段 roundtrip
- **WHEN** 解析含 mesh 与 river 段的合成 `cfg.ic` 并回写
- **THEN** 回写文件与原文件字节等价

#### Scenario: 含 lake 段 roundtrip
- **WHEN** 解析含 mesh/river/lake 三段的合成 `cfg.ic` 并回写
- **THEN** 回写文件与原文件字节等价

#### Scenario: 含 river 段前导的原生布局
- **WHEN** 解析 header 声明 mesh=N、mesh 段 N 行之后紧接 `M 2` 与 `Index Stage` 列头、再接 M 行 river 数据的 `cfg.ic`
- **THEN** 解析成功，`M 2` 行归为 river 前导且不在 mesh/river 任一段的数据行内，river 段恰 M 行，回写字节等价

#### Scenario: river 行数与前导声明不符被拒
- **WHEN** river 前导声明 M 行而实际 river 数据行数不为 M
- **THEN** 解析失败并报出实际与声明行数

#### Scenario: mesh 已满后的非前导数值行仍被拒
- **WHEN** mesh 段已满后出现三 token 数值行，或两整数行之后不是 river 列头
- **THEN** 解析失败并指明多余 mesh 行
