# checkpoint-tracker

来源：compute-loop-design §9.2–9.3。

## ADDED Requirements

### Requirement: 运行期 T+12 捕获
tracker MUST 在 SHUD 运行期间轮询 `<project>.cfg.ic.update` 的 header 时间，命中 relative 720 分钟（或等价的 T+12 绝对分钟）时复制为独立 checkpoint 文件，并确认副本可按原生分段格式读取；SHUD 继续运行不受影响。

#### Scenario: 正常捕获
- **WHEN** 模拟的 `cfg.ic.update` 覆写序列包含 header=720 分钟的版本
- **THEN** tracker 产出独立 checkpoint 文件，且该文件通过分段格式读取校验

#### Scenario: 捕获副本校验失败不算成功
- **WHEN** 命中 720 分钟但复制得到的文件无法按分段格式读取
- **THEN** tracker 判定本次捕获失败，不报告成功

### Requirement: 快速覆盖漏采如实报告
`cfg.ic.update` 被快速覆写导致 720 分钟版本从未被观测到时，tracker MUST 如实报告未捕获，MUST NOT 以更晚时刻的版本冒充 T+12。

#### Scenario: 覆写跳过 720
- **WHEN** 模拟覆写序列从 head<720 直接跳到 head>720
- **THEN** tracker 报告 T+12 未捕获

### Requirement: 确定性补跑
7 天运行成功但 T+12 未捕获时，补跑 MUST 使用同一 cycle T 初态与同一份 forcing，将 `END` 缩短为 0.5 天、`Update_IC_STEP=720`，取末态作为 T+12 checkpoint；补跑仍失败 MUST 判整轮失败，不写状态与 `DONE`。

#### Scenario: 补跑成功
- **WHEN** 主跑漏采后以假执行器完成 0.5 天补跑
- **THEN** 补跑运行目录参数为 END=0.5、Update_IC_STEP=720，末态被采纳为 T+12 checkpoint

#### Scenario: 补跑失败传导整轮失败
- **WHEN** 补跑作业以失败结束
- **THEN** 本 source/cycle 判失败，无状态推进、无 `DONE`
