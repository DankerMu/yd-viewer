# init-bootstrap

来源：compute-loop-design §6.2；design.md（本 change）D7。

## ADDED Requirements

### Requirement: 只在全新根执行
`init` 发现 `states/` 下已有任一状态文件、或 `output/` 下已有任一 `DONE` 时，MUST 直接拒绝执行。

#### Scenario: 已有状态即拒绝
- **WHEN** 模拟根中存在 `states/gfs/2026082700.cfg.ic` 时执行 init
- **THEN** 拒绝退出，`states/` 与 `output/` 无任何变化

#### Scenario: 已有 DONE 即拒绝
- **WHEN** 模拟根中存在任一 `output/<cycle>/<source>/DONE` 时执行 init
- **THEN** 拒绝退出，无任何写入

### Requirement: 扫描窗内确定各源首轮
`init` MUST 以执行时刻往前 7 天为扫描窗，对每个 source 找到窗内最早的完整 00Z/12Z raw cycle 作为该源首轮 T；任一 source 窗内无完整 cycle 时 MUST 整体拒绝且不写任何状态（fail closed，等待 raw 补齐后重跑；compute-loop §6.2）。

#### Scenario: 双源各自首轮
- **WHEN** raw fixture 中 GFS 与 IFS 窗内最早完整 cycle 不同
- **THEN** 两源分别以各自最早完整 cycle 为首轮 T

#### Scenario: 单源窗内无完整 raw 即整体拒绝
- **WHEN** 窗内 IFS 无任何完整 cycle 而 GFS 有
- **THEN** init 拒绝退出，`states/` 下无任何文件

### Requirement: 首态生成
`init` MUST 从两个变体内各自同源率定末态复制首态，重戳到该源首轮 T，写为 `states/<source>/<T>.cfg.ic`；MUST NOT 运行 SHUD，MUST NOT 写任何 `DONE`。

#### Scenario: 首态写入
- **WHEN** 对合成变体与完整 raw fixture 执行 init
- **THEN** 每个建链 source 得到一个重戳到其首轮 T 的状态文件，`output/` 下无 `DONE`
