## ADDED Requirements

### Requirement: 主跑与补跑共用有界日志消费
生产 worker 的 recovery runner MUST 复用 `_run_shud_live` 的 `_LOG_CHUNK` 分块读取、逐块 root-aware `_append_job_log` 及进程退出后读至 EOF 的路径，MUST NOT 使用 `communicate()` 或积累与总输出同大的父进程 payload。`job.log` MUST 按主跑 merged stdout/stderr、再补跑 merged stdout/stderr 的顺序保存全部原始 bytes；字节数、checksum、receipt 与 collect 结果 MUST 不变。主跑 tracker 捕获行为 MUST 保持，补跑 MUST NOT 借共享读取触发主跑 tracker 捕获；补跑 return code、argv/cwd/env、参数恢复与 T+12 校验 MUST 保持既有语义。不新增截断、保留、超时、重试或平台内存上限。

#### Scenario: 大输出补跑保持完整且分块
- **WHEN** synthetic SHUD 每次有序输出 520000 字节的合并 stdout/stderr，IFS 主跑后补跑
- **THEN** job.log 恰为 1040000 字节且 byte order/sha256 与发出内容一致，receipt checksum 匹配、collect 接受已验证 T+12；可观察的补跑 append 多于一块且每块至多 _LOG_CHUNK，总和恰为补跑输出字节数

#### Scenario: 主跑及退出尾部日志保持
- **WHEN** GFS 主跑一次发出 520000 字节（含退出前尾部输出）且无需补跑
- **THEN** 完整 job.log 恰为发出流、长度 520000、checksum 一致，主跑 tracker/receipt/collect 保持原有结果

#### Scenario: 补跑非零退出不产生成功收据
- **WHEN** 同步 recovery 子进程返回非零
- **THEN** 继续以现有 recovery 失败语义停止，不新增 T+12 authority 或成功 receipt，不改变日志 bytes 策略
