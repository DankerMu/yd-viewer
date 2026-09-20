## ADDED Requirements

### Requirement: 结构层：有界读头与行数推算
读取器 MUST 提供只读文件头的入口：有界读前 `1024 + 8*(2+nc)` 字节（1024 字节文本头、little-endian float64 的 `st`、`nc`、`nc` 个列编号），并用 `stat` 得到的文件大小推算数据区行数 `k = (size - 1024 - 8*(2+nc)) / (8*(nc+1))`。MUST 校验：`nc` 为正整数；`size` 恰能整除（否则视为截断）；`k` 恰为 168；列编号集合等于权威集合。任一不满足 MUST 抛出带路径与具体原因的 `DatError`。读取器 MUST 只用标准库（`array`/`struct`），MUST NOT 引入 numpy。`st` 日期头 MUST 只解析不用于任何时间计算（契约 §5.2）。

#### Scenario: 合法头
- **WHEN** 文件为 1024 字节头 + `st` + `nc=5` + 列编号 `[1,2,3,4,5]` + 168 行且权威集合为 {1..5}
- **THEN** 结构层通过，得到 `nc=5`、列编号 `[1,2,3,4,5]`、行数 168，且读取字节数为 `1024+8*7`

#### Scenario: 行数不足
- **WHEN** 数据区只有 167 行
- **THEN** 抛出 `DatError`，原因含 `167` 与 `168`

#### Scenario: 列编号与几何不符
- **WHEN** 列编号为 `[1,2,3,4,6]` 而权威集合为 {1..5}
- **THEN** 抛出 `DatError`，原因含缺失的 `5` 与多余的 `6`

#### Scenario: 文件截断
- **WHEN** 文件大小减去头部后不能被 `8*(nc+1)` 整除
- **THEN** 抛出 `DatError`

#### Scenario: st 头不参与时间
- **WHEN** 合成文件的 `st` 被写成与 cycle 无关的日期
- **THEN** 结构层与数据层均通过，任何返回值不含由 `st` 推出的时间

### Requirement: 数据层：整读与分钟列校验
数据入口 MUST 先执行结构层校验，再把整个文件读入内存并解析数据区；MUST 校验第 0 列逐值等于 `i*60`（`i=0..167`），期望值由 products-contract §5.2 常量（`START=0`、`END=7 天`、`DT_QR_DOWN=60 分钟`）推导，MUST NOT 与测试写出侧共用同一算术表达式。不满足 MUST 抛出 `DatError` 且 MUST NOT 返回部分结果。

#### Scenario: 合法文件
- **WHEN** 第 0 列为 `0,60,…,10020`
- **THEN** 数据层通过，得到 168 行

#### Scenario: 分钟列整体偏移
- **WHEN** 第 0 列为 `60,120,…,10080`
- **THEN** 抛出 `DatError`，原因指明第 0 行期望 `0`

#### Scenario: 分钟列含非有限值
- **WHEN** 第 5 行第 0 列为 NaN
- **THEN** 抛出 `DatError`，原因含行号 5

### Requirement: 单位换算与取值
数据层结果 MUST 提供按 lead 取整行（去掉第 0 列，按权威集合升序排列）与按 `reach_id` 取整列（168 值）；返回值 MUST 已从 m³/day 换算为 m³/s（除以 86400）。

#### Scenario: 取 lead 0 行
- **WHEN** lead 0 行 reach_id=1 原值为 86400
- **THEN** 取行结果中 reach_id=1 的值为 1.0

#### Scenario: 取 reach 列
- **WHEN** 请求 reach_id=3 的列
- **THEN** 返回 168 个 float，第 k 个等于数据区第 k 行该列原值 / 86400

#### Scenario: 列编号非顺序
- **WHEN** 列编号为 `[3,1,2]` 且权威集合为 {1,2,3}
- **THEN** 取行结果按 reach_id 1,2,3 顺序返回对应列的值（不按文件列位置）
