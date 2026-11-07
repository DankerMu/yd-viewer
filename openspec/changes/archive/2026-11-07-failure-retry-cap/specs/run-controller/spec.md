## MODIFIED Requirements

### Requirement: 失败处理
作业在 `run_sources` 的当前控制器实例中明确返回 `FAILED`/`TIMEOUT` 时 MUST 不写 `DONE`、不推进状态链；双源组合器 MUST 按「双源独立追赶组合公共契约」调用 MUST 注入的本源失败收尾 provider。该 provider 对同一 job 恰执行一次 `sacct -j <job_id> -X -n -P --format=ExitCode` 并返回非空退出码字符串；轮询通道不得取 `ExitCode`，不得从 `JobState` 猜测，`JobRecord` 七字段不变。组合器 MUST 将该字符串作为 `FailureInputs.exit_code` 传给 `finalize_failed_job`，随后把完整 stdout/stderr、命令、job ID、起止时间与退出码合成本次尝试记录，MUST 追加到 `logs/<source>/<T>.log`（已有普通文件时 no-follow、有界读出旧内容原样写入临时文件再接本次记录并原子 rename，旧内容逐字节保留；旧内容不以换行结尾时先补一个换行，使本次 header 单独成行），日志原子提交成功后才删除整个精确 scratch work。失败收尾完成后，下次 run 从干净 work 对该 cycle 重试；但控制器在 `rawscan` 之前 MUST no-follow、有界读取该日志并计数 `schema == "yd-failure-log-v1"` 的 header 行（按字节切行，无法解码或解析的行不是 header，不令文件不可判定），计数 ≥ 3 或日志不可判定时 MUST 以 `retry_exhausted` 停本源，零 staging、零提交，兄弟源不受影响；人工把该日志移出 `logs/<source>/` 后恢复。失败次数 MUST 只以该日志内的尝试记录表达，MUST NOT 维护 `status.json`、独立计数文件或退避。一个源的失败或失败收尾错误 MUST NOT 取消另一源已经启动的作业；双源控制器在两源都结束后才返回或抛出错误。直接六参数 `run_once` 的兼容行为不在此自动收尾要求内：它仍返回 `JOB_FAILED` 并保留 work。

#### Scenario: 失败轮产物
- **WHEN** fake executor 返回失败
- **THEN** 该 source/cycle 无 `DONE`、状态链未动、存在唯一含该轮 job ID 的合并日志、work 目录不存在

#### Scenario: 同一 cycle 第三次失败后停源
- **WHEN** `logs/ifs/<T>.log` 已含 3 条 `yd-failure-log-v1` header，ifs 的待跑 cycle 为 T，且 raw 齐全
- **THEN** ifs 返回 `STOPPED`，停因为 `retry_exhausted`，零 staging、零 sbatch，detail 含日志绝对路径与计数；gfs 照常推进

#### Scenario: 失败日志追加保真
- **WHEN** `logs/ifs/<T>.log` 已含 1 条记录，T 再次失败并完成失败收尾
- **THEN** 新日志的前缀与旧文件逐字节相同，header 计数为 2，work 目录不存在

#### Scenario: 失败日志不可判定时不 fail-open
- **WHEN** `logs/ifs/<T>.log` 是 symlink、目录或超出字节上界
- **THEN** ifs 返回 `STOPPED`/`retry_exhausted`，detail 写明不可判定的原因，零 sbatch

#### Scenario: 正文非 UTF-8 的失败日志照常计数
- **WHEN** `logs/ifs/<T>.log` 是一条记录、stdout 含非 UTF-8 字节且不以换行结尾，T 再次失败并完成失败收尾
- **THEN** 收尾前计数为 1 且控制器照常提交；收尾后旧文件仍是逐字节前缀、计数为 2
