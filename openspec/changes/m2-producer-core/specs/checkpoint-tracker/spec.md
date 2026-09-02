# checkpoint-tracker

来源：compute-loop-design §9.2–9.3、§4.2（快照清单含 tracker）、agent-ops §8.3。

## ADDED Requirements

### Requirement: 运行期 T+12 捕获
tracker MUST 在 SHUD 运行期间轮询 `<project>.cfg.ic.update` 的 header 时间，命中 relative 720 分钟时复制为独立 checkpoint 文件，并确认副本可按原生分段格式读取；SHUD 继续运行不受影响。捕获产物的时间头保持原样（相对分钟），发布前的绝对定戳属 run-controller 发布路径（compute-loop §9.2）。**绝对（epoch）分钟形式的 header 不在 tracker 的接受域内**：运行中的 SHUD 写进 `cfg.ic.update` 的是相对分钟，epoch 形式只出现在 warm-start 初态与重戳后的正式状态上，其判定与产生归 state-tools 重戳与发布路径。tracker 见到 epoch 形式的 header MUST 判为未命中（fail closed，如实进入漏采路径），MUST NOT 为兼容它而引入 `start_time` 与绝对时间换算。

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
7 天运行成功但 T+12 未捕获时，补跑 MUST 使用同一 cycle T 初态与同一份 forcing，将 `END` 缩短为 0.5 天、`Update_IC_STEP=720`，在全新的专用输出目录执行一次同步 job-local 调用。只有精确 `<project>.cfg.ic.update` 经 relative-720 header、原生分段结构与 checksum 校验后才可成为 T+12 checkpoint；补跑参数文件随后 MUST 恢复主跑原 bytes，初态与 forcing 前后 MUST 不变。任一调用、校验、输入对账或恢复失败 MUST 判整轮失败，不写状态与 `DONE`。

#### Scenario: 补跑成功
- **WHEN** 主跑漏采后以注入的假 SHUD 调用完成 0.5 天补跑
- **THEN** 调用时参数为 END=0.5、Update_IC_STEP=720，runner 只见同一初态/forcing 与全新专用输出目录，校验后的末态被采纳为 T+12 checkpoint，主跑参数原 bytes 已恢复

#### Scenario: 补跑失败传导整轮失败
- **WHEN** 补跑 runner 抛错、返回非零、未写末态、写出错误时刻/截断末态、静态输入漂移或参数恢复失败
- **THEN** tracker 不新增捕获 authority，本 source/cycle 判失败，无状态推进、无 `DONE`

### Requirement: attempt-local authority 与残留隔离
每棵 scratch work MUST 只服务一个 Slurm attempt；捕获 authority MUST 是同一 tracker 实例的记录及其 checksum，MUST NOT 由规范文件名存在、目录扫描或上一次 attempt 的 recovery 产物推导。新 tracker/recovery 见到同名 checkpoint 或既有 recovery root 时 MUST 保留残留并 fail closed，MUST NOT 覆盖、删除或采纳。tracker 在 O_EXCL 创建 canonical 后也 MUST NOT 按 pathname 删除校验/回读失败的条目：创建成功不能证明当前 pathname 未被竞争者替换，失败条目 MUST 作为未验证残留保留且不得记为 authority，最终随整棵失败 work 由 controller 回收。产品补跑目标 MUST 恰为 12 小时；其它目标（含把 720 分钟误写为 720 小时）MUST 在任何 runner 调用和文件写入前拒绝。

#### Scenario: 旧规范文件不是 checkpoint authority
- **WHEN** 新 tracker 的 `state_checkpoints/` 已有一个 header/body 均合法的规范文件，但实例内无对应捕获记录
- **THEN** 实时观测不覆盖或删除该文件，补跑不采纳它，并以未验证残留失败

#### Scenario: O_EXCL 后 canonical 回读不一致
- **WHEN** 捕获或补跑安装的 O_EXCL 写返回后、canonical 回读前，同名 entry 的 bytes 被改成另一份不同内容的合法或损坏状态，或回读本身失败
- **THEN** tracker 不采纳、不按 pathname 删除该 entry，保留未验证残留并报告未捕获或整轮失败

#### Scenario: 不可达目标小时早拒绝
- **WHEN** tracker 目标为 `[720]` 或任何不等于 `[12]` 的集合
- **THEN** 补跑在 runner 调用数为 0、参数和输入 bytes 不变时抛出领域错误

### Requirement: job-local 执行归属
tracker 与漏采补跑 MUST 在该 source/cycle 的同一个 Slurm 作业内完成（job-local，compute-loop §9.2）；控制器只观察作业结束后有无有效 checkpoint，MUST NOT 在登录节点侧轮询 `cfg.ic.update`，补跑 MUST NOT 触发第二次作业提交。

#### Scenario: 补跑不增加提交计数
- **WHEN** 主跑漏采触发作业内补跑并成功
- **THEN** 控制器侧该 source/cycle 的 executor 提交计数仍为 1

### Requirement: 快照可追溯
tracker 与补跑逻辑 MUST 快照并适配 NWM 已验证实现；模块头部 MUST 记录来源 `NWM@8ae9b8f2` 与原仓相对路径，纳入溯源头部检查；若勘察确认 NWM 侧该模块与 job/scheduler 强耦合无法快照，MUST 在 change design 的 Decisions 中显式记录偏离及理由。

#### Scenario: tracker 模块溯源
- **WHEN** 对 tracker 相关快照模块运行溯源检查测试
- **THEN** 模块头部含 `NWM@8ae9b8f2` 与原路径注释（或 design 中存在显式偏离记录）
